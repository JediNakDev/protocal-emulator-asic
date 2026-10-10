# SPDX-License-Identifier: Apache-2.0
"""Regression tests for the local verification commands."""

from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("stale_model", [False, True])
def test_formal_stops_on_synthesis_failure(tmp_path, stale_model):
    formal = tmp_path / "formal"
    formal.mkdir()
    # Exercise the real build commands without solving the whole chip. Older
    # Ubuntu Yosys/Z3 packages take minutes on the chip-wide reset proof.
    (formal / "build_check.v").write_text("""
module build_check_props(input wire clk);
    reg q = 1'b0;
    always @(posedge clk) q <= 1'b0;
    always @(*) assert (q == 1'b0);
endmodule
""")
    command = ["make", "-f", str(ROOT / "Makefile"), "formal", "FORMAL_PROPS=build_check:2"]
    if stale_model:
        built = subprocess.run(command + ["RTL_SOURCES="], cwd=tmp_path,
                               capture_output=True, text=True, timeout=30)
        assert built.returncode == 0, built.stdout + built.stderr
        assert (formal / "build/build_check.smt2").exists()

    failed = subprocess.run(command + ["RTL_SOURCES=missing.v"], cwd=tmp_path,
                            capture_output=True, text=True, timeout=30)
    output = failed.stdout + failed.stderr
    assert failed.returncode != 0, output
    assert "missing.v" in output
    assert "formal: build_check passed" not in output
