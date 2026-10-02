# Sample testbench for a Tiny Tapeout project

This is a sample testbench for a Tiny Tapeout project. It uses [cocotb](https://docs.cocotb.org/en/stable/) to drive the DUT and check the outputs.
See below to get started or for more information, check the [website](https://tinytapeout.com/hdl/testing/).

## Setting up

1. Edit [Makefile](Makefile) and modify `PROJECT_SOURCES` to point to your Verilog files.
2. Keep the module instantiated in [tb.v](tb.v) consistent with `top_module` in `../info.yaml`.

## How to run

To run the RTL simulation:

```sh
make test # From the repository root, after make setup
```

To run gate-level simulation, first harden your project and copy `runs/wokwi/final/nl/tt_um_jedinakdev_protocol_emulator.nl.v` to `test/gate_level_netlist.v`.
Activate the project environment with `source .venv/bin/activate` from the repository root, set `PDK_ROOT` to the PDK installation directory, and change into `test`.

Then run:

```sh
make -B GATES=yes
```

If you wish to save the waveform in VCD format instead of FST format, edit tb.v to use `$dumpfile("tb.vcd");` and then run:

```sh
make -B FST=
```

This will generate `tb.vcd` instead of `tb.fst`.

## How to view the waveform file

Using GTKWave

```sh
gtkwave tb.fst tb.gtkw
```

Using Surfer

```sh
surfer tb.fst
```
