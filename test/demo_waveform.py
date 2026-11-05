# SPDX-License-Identifier: Apache-2.0
"""The simulation behind `make demo` (tools/demo_waveform.py prints its report):
capture a UART stream through the chip's pins, flip one bit of the recording
on the Host, replay it, and decode the pins with the independent UART peer.

Not part of `make test`. Every step records what the pins and the Host port
showed before its checks run, so a failed run still reports how far it got."""

import json
import os

import cocotb

from chip import regs
from peers import Wave, now_ns, uart_decode, uart_transmit
from pe.proto import waveform
from test_waveform import PIN_IN, PIN_OUT, PROFILE, play, record, setup, to_cycles

BAUD = 250_000                  # the rate of test_edit_and_replay_uart
BIT = round(PROFILE / BAUD)     # cycles per UART bit
DATA = b"ON"
FAULT_BYTE, FAULT_BIT = 1, 3    # "ON" becomes "OF"
LEAD = 200                      # cycles of idle line before each replay
LOSS = regs.STK_CAP_OVR0 | regs.STK_RX_OVF0
UI_PIN_IN, UO_PIN_OUT = 6, 5     # pins 8 and 10 on the top-level ports


def frame_edges(byte):
    frame = [1, 0] + [(byte >> i) & 1 for i in range(8)] + [1]
    return sum(1 for a, b in zip(frame, frame[1:]) if a != b)


class Report:
    """JSON file rewritten after each step, tagged with this run's id."""

    def __init__(self, path, run):
        self.path = path
        self.data = {"run": run, "steps": {}, "error": None}

    def step(self, name, **fields):
        self.data["steps"][name] = fields
        self.save()

    def fail(self, message):
        self.data["error"] = message
        self.save()

    def save(self):
        with open(self.path, "w") as f:
            json.dump(self.data, f, indent=1)


def write_vcd(path, waves):
    """One-bit VCD of {name: Wave}, in absolute simulation time."""
    ids = {name: chr(ord("a") + i) for i, name in enumerate(waves)}
    head = ["$timescale 1ps $end", "$scope module demo $end"]
    head += [f"$var wire 1 {ids[n]} {n} $end" for n in waves]
    head += ["$upscope $end", "$enddefinitions $end", "#0"]
    head += [f"{w.initial}{ids[n]}" for n, w in waves.items()]
    changes = sorted((round(t * 1000), v, ids[n]) for n, w in waves.items()
                     for t, v in zip(w.times, w.levels))
    with open(path, "w") as f:
        f.write("\n".join(head) + "\n")
        for t, v, sig in changes:
            f.write(f"#{t}\n{v}{sig}\n")


async def replay(chip, level, times):
    """Replay a recording. Returns a Wave of pin 10 over the whole replay,
    and what happened on the pin against the recording it played."""
    wave = Wave(chip.dut.uo_out, UO_PIN_OUT)
    words = waveform.to_replay(level, times, lead=LEAD)
    await play(chip, words)
    while (await chip.levels(1))[0]:
        await chip.cycles(20)
    await chip.cycles(2 * BIT)
    wave.stop()
    edges = to_cycles(wave.times)
    return wave, {"words": words, "edges": edges, "t_ns": [wave.start, now_ns()],
                  "exact": waveform.intervals(edges) == waveform.intervals(times)}


async def session(chip, report, pin8, pin10):
    # Step 1: UART onto pin 8, while the Host drains the capture timestamps.
    await chip.enable(1)
    await chip.cycles(20)
    sender = cocotb.start_soon(uart_transmit(chip.set_pin8, BAUD, DATA))
    per_frame = [frame_edges(b) for b in DATA]
    level, times = await record(chip, sum(per_frame))
    await sender
    sticky = await chip.read8(regs.STICKY)
    exact = waveform.intervals(times) == waveform.intervals(to_cycles(pin8.times))
    report.step("original", pin=PIN_IN, baud=BAUD, cycles_per_bit=BIT, sent=list(DATA),
                level=level, times=times, sticky=sticky, exact=exact, hz=PROFILE)
    assert level == 1, "capture did not start on the idle line"
    assert exact, "captured intervals differ from pin 8"

    # Step 2: on the Host, invert one data bit's span of the recording.
    start = times[sum(per_frame[:FAULT_BYTE])] + (1 + FAULT_BIT) * BIT
    span = (start, start + BIT)
    edit_level, edited = waveform.invert_span(level, times, *span)
    report.step("edit", byte=FAULT_BYTE, bit=FAULT_BIT, span=span, level=edit_level, times=edited,
                removed=sorted(set(times) - set(edited)), added=sorted(set(edited) - set(times)))

    # Step 3: the line goes to idle, then the untouched recording plays as a
    # control and the edited one after it.
    await chip.push(1, waveform.to_replay(level, []))
    await chip.enable(3)
    await chip.cycles(LEAD)
    control, played_control = await replay(chip, level, times)
    fault, played_fault = await replay(chip, edit_level, edited)
    sticky = await chip.read8(regs.STICKY)
    report.step("replay", pin=PIN_OUT, control=played_control, fault=played_fault, sticky=sticky)
    assert played_control["exact"], "control replay differs from the recording"
    assert played_fault["exact"], "fault replay differs from the edited recording"
    assert not sticky & (LOSS | regs.STK_TX_OVF1), f"sticky bits {sticky:#04x} report lost data"

    # Step 4: only pin recordings reach the decoder.
    decoded = [uart_decode(w, BAUD, now_ns()) for w in (pin8, control, fault)]
    before, control_bytes, after = [d for d, _ in decoded]
    violations = [v for _, vs in decoded for v in vs]
    expected = bytearray(DATA)
    expected[FAULT_BYTE] ^= 1 << FAULT_BIT
    report.step("decoded", before=before, control=control_bytes, after=after,
                expected=list(expected), violations=violations)
    assert before == list(DATA), "decoder disagrees with the bytes sent"
    assert control_bytes == before, "unedited replay decoded differently"
    assert after != before, "decoder saw no change: the edit injected no fault"
    assert after == list(expected), "decoded fault is not the chosen bit flip"
    assert not violations, violations


@cocotb.test()
async def demo_waveform(dut):
    """Capture, Host edit, replay and decoded effect, through the pins."""
    report = Report(os.environ["PE_DEMO_REPORT"], os.environ["PE_DEMO_RUN"])
    report.save()
    chip = await setup(dut, 1)
    pin8, pin10 = Wave(dut.ui_in, UI_PIN_IN), Wave(dut.uo_out, UO_PIN_OUT)
    try:
        await session(chip, report, pin8, pin10)
    except Exception as e:
        report.fail(f"{type(e).__name__}: {e}")
        raise
    finally:
        pin10.stop()
        pin8.stop()
        write_vcd(os.environ["PE_DEMO_VCD"], {"pin8_ui6_in": pin8, "pin10_uo5_out": pin10})
