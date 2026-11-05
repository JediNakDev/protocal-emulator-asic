#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""`make demo`: run test/demo_waveform.py on the simulated chip and tell the
story of its run: original capture, Host edit, replay, decoded effect.

make does not reliably fail on a failing cocotb test, so the exit status comes
from the results file and from this run's own report. See docs/demo.md."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
STEPS = ("original", "edit", "replay", "decoded")
FRAME_RULER = "  S01234567P  S01234567P  "  # idle, start, data LSB first, stop


def hex_bytes(data):
    return " ".join(f"{b:02X}" for b in data)


def text(data):
    return '"' + "".join(chr(b) if 32 <= b < 127 else "." for b in data) + '"'


def bit_cells(level, times, first_fall, bit, count=len(FRAME_RULER)):
    """The line once per UART bit, sampled mid-bit: `-` high, `_` low."""
    start = first_fall - 2 * bit
    cells = ""
    for i in range(count):
        t = start + i * bit + bit // 2
        high = level ^ (sum(1 for e in times if e <= t) & 1)
        cells += "-" if high else "_"
    return cells


def verdict(ok):
    return "yes" if ok else "NO"


def print_original(s):
    bit, ns = s["cycles_per_bit"], 1e9 / s["hz"]
    times = s["times"]
    print("\n== 1. Original: UART driven onto a pin, captured with timestamps ==")
    print(f"Driven   pin {s['pin']} (ui[6]), 8N1 at {s['baud']} baud, {bit} cycles per bit: "
          f"{hex_bytes(s['sent'])} {text(s['sent'])}")
    print(f"Captured initial level {s['level']}, {len(times)} edges, STICKY {s['sticky']:#04x}")
    print("  edge  counter (cycles)  to level  after (cycles)")
    for i, t in enumerate(times):
        after = "" if i == 0 else f"{t - times[i - 1]:>14}"
        print(f"  {i:>4}  {t:>16}  {s['level'] ^ (~i & 1):>8}  {after}")
    print(f"Check    every interval equals pin {s['pin']} to the cycle ({ns:.0f} ns): {verdict(s['exact'])}")


def print_edit(s, original):
    bit, times = original["cycles_per_bit"], original["times"]
    faulted = list(original["sent"])
    faulted[s["byte"]] ^= 1 << s["bit"]
    print("\n== 2. Edit: what the Host changed in the recording, and why ==")
    print(f"Why      8N1 has no parity, so a flipped data bit passes framing unnoticed; "
          f"byte {s['byte']} bit {s['bit']} turns {text(original['sent'])} into {text(faulted)}")
    print(f"Change   invert cycles {s['span'][0]} to {s['span'][1]}, that bit's {bit} cycles")
    print(f"Removed  edge at {s['removed'] or 'none'}")
    print(f"Added    edge at {s['added'] or 'none'}")
    print(f"           {FRAME_RULER}")
    print(f"captured   {bit_cells(original['level'], times, times[0], bit)}")
    print(f"edited     {bit_cells(s['level'], s['times'], s['times'][0], bit)}")


def print_replay(s, original):
    bit = original["cycles_per_bit"]
    print("\n== 3. Replay: waveform driven out of the chip ==")
    print(f"Pin {s['pin']} (uo[5]): the untouched recording as a control, then the edited one")
    for name in ("control", "fault"):
        played = s[name]
        start, end = [t / 1000 for t in played["t_ns"]]
        print(f"{name.capitalize():<8} {len(played['words'])} replay words, {len(played['edges'])} edges, "
              f"{start:.1f} to {end:.1f} us in the VCD; "
              f"every interval equals its recording to the cycle: {verdict(played['exact'])}")
    print("Words    fault replay: " + " ".join(f"{w:04X}" for w in s["fault"]["words"]))
    print(f"STICKY   {s['sticky']:#04x}")
    print(f"           {FRAME_RULER}")
    for name in ("control", "fault"):
        edges = s[name]["edges"]
        if edges:
            print(f"{name:<10} {bit_cells(1, edges, edges[0], bit)}")


def print_decoded(s):
    print("\n== 4. Decoded effect: independent UART decoder on the pins ==")
    print(f"Before   pin 8           {hex_bytes(s['before'])}  {text(s['before'])}")
    print(f"Control  pin 10 unedited {hex_bytes(s['control'])}  {text(s['control'])}")
    print(f"After    pin 10 edited   {hex_bytes(s['after'])}  {text(s['after'])}")
    diffs = [(i, a ^ b) for i, (a, b) in enumerate(zip(s["before"], s["after"])) if a != b]
    for i, mask in diffs:
        print(f"Fault    byte {i}: {s['before'][i]:02X} -> {s['after'][i]:02X}, XOR {mask:02X}")
    if not diffs:
        print("Fault    none: the bytes are identical")
    for violation in s["violations"]:
        print(f"Framing  {violation}")
    if diffs and not s["violations"]:
        print("Framing  no violations: start and stop bits stay valid, so a receiver accepts the wrong byte")


def print_report(report):
    steps = report["steps"]
    if "original" in steps:
        print_original(steps["original"])
    if "edit" in steps:
        print_edit(steps["edit"], steps["original"])
    if "replay" in steps:
        print_replay(steps["replay"], steps["original"])
    if "decoded" in steps:
        print_decoded(steps["decoded"])


def results_problems(path):
    """What the cocotb results file says went wrong, if anything."""
    if not path.exists():
        return [f"no results file at {path}"]
    cases = list(ET.parse(path).getroot().iter("testcase"))
    problems = [] if len(cases) == 1 else [f"expected 1 testcase, found {len(cases)}"]
    for case in cases:
        problems += [f"{case.get('name')}: {child.tag}" for child in case
                     if child.tag in ("failure", "error", "skipped")]
    return problems


def report_problems(report, run):
    if report is None:
        return ["the simulation wrote no report"]
    if report["run"] != run:
        return ["the report is from another run"]
    problems = [f"step did not finish: {s}" for s in STEPS if s not in report["steps"]]
    if report["error"]:
        problems.insert(0, report["error"])
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sim-build", default="sim_build/demo", help="build folder, relative to test/")
    parser.add_argument("--out", type=Path, default=ROOT / "output/demo", help="gitignored output folder")
    args = parser.parse_args()

    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    report_file, vcd, log = out / "report.json", out / "waveform.vcd", out / "sim.log"
    results = f"{args.sim_build}/results.xml"
    results_file = ROOT / "test" / results
    # A leftover report or results file must never pass for this run's.
    for stale in (report_file, vcd, log, results_file):
        stale.unlink(missing_ok=True)

    run = uuid.uuid4().hex
    env = dict(os.environ, PE_DEMO_REPORT=str(report_file), PE_DEMO_VCD=str(vcd), PE_DEMO_RUN=run)
    env["PATH"] = f"{ROOT / '.venv/bin'}{os.pathsep}{env['PATH']}"
    command = ["make", "-C", "test", f"SIM_BUILD={args.sim_build}", f"COCOTB_RESULTS_FILE={results}",
               "COCOTB_TEST_MODULES=demo_waveform", "WAVES=0"]  # no test/tb.fst, which `make test` owns

    print("Waveform capture, Host edit and replay: RTL simulation through the chip's pins (50 MHz profile)")
    with open(log, "w") as f:
        done = subprocess.run(command, cwd=ROOT, env=env, stdout=f, stderr=subprocess.STDOUT)

    report = json.loads(report_file.read_text()) if report_file.exists() else None
    if report is not None and report["run"] == run:
        print_report(report)
    problems = report_problems(report, run) + results_problems(results_file)
    if done.returncode:
        problems.append(f"make exited {done.returncode}")
    if problems:
        print(f"\nFAIL: see {log}")
        for problem in problems:
            print(f"  {problem}")
        return 1
    print(f"\nPASS: pin 8 and pin 10 traces in {vcd}; report.json and sim.log beside it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
