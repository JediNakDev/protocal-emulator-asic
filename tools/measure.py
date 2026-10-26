# SPDX-License-Identifier: Apache-2.0
"""Reproduce program sizes and optional cell area estimates using Liberty.

Run from any directory with Python 3.11 or later. Results are JSON on stdout;
Yosys logs go to --output. This does not establish routed timing closure.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

from pe.asm import assemble


ROOT = Path(__file__).resolve().parents[1]


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--liberty", type=Path, help="map both storage variants with Yosys (a CMOS5L liberty)")
    parser.add_argument("--output", type=Path, default=ROOT / "output/measure")
    args = parser.parse_args()
    # Use the physical source manifest rather than a separate RTL file list.
    # info.yaml uses a plain YAML list; avoid an extra dependency for this tool.
    manifest = (ROOT / "info.yaml").read_text()
    block = manifest.split("source_files:", 1)[1].split("\n\n", 1)[0]
    sources = [ROOT / "src" / name for name in re.findall(r'-\s+"([^"\n]+\.v)"', block)]
    if not sources:
        parser.error("info.yaml has no Verilog source_files")
    programs = sorted((ROOT / "programs").glob("*.pasm"))
    inputs = sources + programs + [ROOT / "tools/pe/asm.py", Path(__file__).resolve(),
                                   ROOT / "info.yaml", ROOT / "src/config.json"]
    report = {
        "base_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "source_sha256": {str(p.relative_to(ROOT)): sha256(p) for p in inputs},
        "program_words": {p.stem: len(assemble(p.read_text())) for p in programs},
    }
    if args.liberty:
        liberty = args.liberty.resolve(strict=True)
        args.output.mkdir(parents=True, exist_ok=True)
        report["liberty_sha256"] = sha256(liberty)
        report["liberty"] = str(liberty)
        report["yosys_version"] = subprocess.check_output(["yosys", "-V"], text=True).strip()
        report["synthesis"] = {}
        # The latch store instantiates sg13cmos5l_dlhrq_1 (read_verilog
        # defines SYNTHESIS), so the latch variant needs the CMOS5L library.
        for variant, define in (("flip_flop", "-DPE_FLOP_STORE"), ("latch", "")):
            quoted = " ".join(json.dumps(str(p)) for p in sources)
            lib = json.dumps(str(liberty))
            script = (
                f"read_liberty -lib {lib}; read_verilog {define} {quoted}; "
                "synth -flatten -top tt_um_jedinakdev_protocol_emulator; "
                f"dfflibmap -liberty {lib}; abc -liberty {lib}; opt_clean; stat -liberty {lib}"
            )
            log = args.output / f"{variant}.log"
            with log.open("w") as stream:
                subprocess.run(["yosys", "-T", "-p", script], cwd=ROOT,
                               stdout=stream, stderr=subprocess.STDOUT, check=True)
            match = re.search(r"Chip area for module .*?:\s*([\d.]+)", log.read_text())
            if match is None:
                raise RuntimeError(f"No mapped area in {log}")
            report["synthesis"][variant] = {
                "cell_area_um2": float(match[1]), "command": script, "log": str(log.resolve()),
            }
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
