# Testbench

The tests use [cocotb](https://docs.cocotb.org/en/stable/) and drive only the top-level pins.
`chip.py` is the Host model, `peers.py` holds independent protocol peers, and `tb.v` models pull-ups, open-drain peers and line contention.
Run `make test LATCH=yes` to simulate the latch storage build.

## Random comparison with the reference model

`test_random.py` runs random programs and configurations on the chip and on the reference model in `../tools/pe/model.py`, which is written from `docs/spec.md` and shares no code with the RTL.
It compares every pad on every cycle, then all state the Host can read.
The random setup includes the bit state machine, every pin mode, random board inputs, and instruction memory writes while the engines run.

| Variable | Default | Meaning |
| --- | --- | --- |
| `PE_RANDOM_SEEDS` | 60 (4 at gate level) | Number of seeds |
| `PE_RANDOM_SEED` | 1 | First seed |

A failure names its seed, for example `PE_RANDOM_SEED=43 PE_RANDOM_SEEDS=1`, and the waveform is in `tb.fst`.
Rerun one seed with:

```sh
PE_RANDOM_SEED=43 PE_RANDOM_SEEDS=1 make COCOTB_TEST_MODULES=test_random
```

The CI `test` workflow also runs 200 new seeds on every push, starting at 1000 times the run number.

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
