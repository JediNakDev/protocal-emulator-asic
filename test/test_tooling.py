# SPDX-License-Identifier: Apache-2.0
"""Regression tests for the local verification commands."""

from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("stale_model", [False, True])
def test_formal_stops_on_synthesis_failure(tmp_path, stale_model):
    formal = tmp_path / "formal"
    formal.mkdir()
    shutil.copy(ROOT / "formal/reset.v", formal)
    command = ["make", "-f", str(ROOT / "Makefile"), "formal", "FORMAL_PROPS=reset:8"]
    if stale_model:
        sources = " ".join(str(p) for p in sorted((ROOT / "src").glob("*.v")))
        built = subprocess.run(command + [f"RTL_SOURCES={sources}"], cwd=tmp_path,
                               capture_output=True, text=True)
        assert built.returncode == 0, built.stdout + built.stderr
        assert (formal / "build/reset.smt2").exists()

    failed = subprocess.run(command + ["RTL_SOURCES=missing.v"], cwd=tmp_path,
                            capture_output=True, text=True)
    output = failed.stdout + failed.stderr
    assert failed.returncode != 0, output
    assert "missing.v" in output
    assert "formal: reset passed" not in output
