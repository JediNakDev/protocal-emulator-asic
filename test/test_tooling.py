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


def test_assembler_checks_page_jumps():
    import sys
    sys.path.insert(0, str(ROOT / "tools"))
    from pe.asm import AsmError, assemble

    span = assemble("""
        set page, far
        jmp !y far
        jmp far
    far:
        set page, 0
        jmp 0
    """)
    # Loaded at 61: `far` is address 64, page address 0, and `set page, far` selects page 1.
    assert span.words(61) == [0xE061, 0x00C0, 0x0000, 0xE060, 0x0000]
    assert span.address("far", 61) == 64 and span.label("far", 61) == 0
    with pytest.raises(AsmError, match="wrong page"):
        assemble("jmp far\njmp 0\nfar: jmp far").words(62)
    with pytest.raises(AsmError, match="runs across a page boundary"):
        assemble("nop\nnop\nnop").words(62)
    with pytest.raises(AsmError, match="wrong page"):
        assemble("set page, far\njmp !y near\nnear: jmp near\nfar: jmp far").words(61)
    with pytest.raises(AsmError, match="does not fit"):
        assemble("nop\n" * 3).words(126)
    # The default wrap, at the last instruction (address 65, page address 1),
    # would also wrap after the `nop` at page address 1 in page 0.
    wrapping = "nop\nset page, far\njmp far\n" + "jmp 0\n" * 61 + "far: set page, 0\njmp 0\n"
    with pytest.raises(AsmError, match="wrap address"):
        assemble(wrapping).words(0)
    assert len(assemble(wrapping.replace("jmp far\n", "jmp far\n.wrap\n", 1)).words(0)) == 66
