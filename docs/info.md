## How it works

This project is a development scaffold for a programmable protocol emulator in Jane Street's ASIC competition.
It targets IHP's 130nm CMOS5L process with a 6x4 tile allocation.
The initial clock target is 50 MHz.

The current RTL is a toolchain smoke test: `uo_out` is the sum of `ui_in` and `uio_in`, modulo 256.
All bidirectional pins are inputs (`uio_oe = 0`), and `uio_out = 0`.
The circuit is combinational and does not use the clock or reset.
The programmable engine and protocol firmware are not implemented yet.

## How to test

Run `make setup` once, then `make check` from the repository root.
The cocotb smoke test drives the external inputs and checks the output sum.
Simulation produces `test/tb.fst` for waveform inspection.

For the current circuit, drive 20 on `ui_in` and 30 on `uio_in`; `uo_out` should read 50.

## External hardware

No protocol peripherals are required for the current smoke test.
External hardware requirements will be documented when the emulator and its pin interface are implemented.
