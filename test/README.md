# Testbench

The cocotb tests load programs and exchange data through the chip's top-level pins.
`chip.py` implements the Host transactions, `peers.py` and the protocol test files provide independent peers, and `tb.v` models pull-ups, external drivers, and contention.
The suite covers the Host port, engine timing, general primitives, protocol programs, and waveform capture/edit/replay.

## Run RTL simulation

Set up the environment as described in [the root README](../README.md), then run from the repository root:

```sh
make test
make test LATCH=yes
```

Run one test module by overriding the module list:

```sh
make test COCOTB_TEST_MODULES=test_protocols
make test COCOTB_TEST_MODULES=test_waveform
```

The waveform is `test/tb.fst` and the JUnit report is `test/results.xml`.
`make check` also runs lint, synthesis checks, tooling regressions, and [formal checks](../formal/README.md).
When adding RTL, update `PROJECT_SOURCES` here, the root source list, and `info.yaml`.
Keep the top module instantiated in `tb.v` consistent with the metadata.

## Random reference comparisons

`test_random.py` runs random programs and configurations on the chip and on [the independent Python model](../tools/pe/model.py), which is written from [the specification](../docs/spec.md).
It compares every pad on every cycle, then the state exposed through the Host.
Configurations include the bit state machine, pin modes, external inputs, and instruction-memory writes during execution.

| Variable | Default | Meaning |
| --- | --- | --- |
| `PE_RANDOM_SEEDS` | 60 in RTL, 4 at gate level | Number of seeds |
| `PE_RANDOM_SEED` | 1 | First seed |

A failure reports its seed; rerun it from the repository root with:

```sh
PE_RANDOM_SEED=43 PE_RANDOM_SEEDS=1 make test COCOTB_TEST_MODULES=test_random
```

The `test` workflow adds a 200-seed soak on each push, starting at 1000 times its run number.
Its artifacts include the waveform, JUnit reports, and protocol output files.

## Run gate-level simulation

Published releases and prereleases run gate-level tests after GDS hardening; see [the release guide](../docs/releasing.md).
For a local run, harden the project and copy `runs/wokwi/final/nl/tt_um_jedinakdev_protocol_emulator.nl.v` to `test/gate_level_netlist.v`.
Set `PDK_ROOT` to the parent directory of the installed `ihp-sg13cmos5l` PDK, then run from the repository root:

```sh
make -C test clean
make test GATES=yes
```

The Makefile loads the CMOS5L I/O cells, standard cells, and UDP primitives from that PDK.
These tests check functional mapped behavior without delay annotation.
Use routed setup and hold reports to assess timing.

## Inspect waveforms

From the repository root, open the saved waveform with either viewer:

```sh
gtkwave test/tb.fst test/tb.gtkw
surfer test/tb.fst
```

To save VCD instead, change the dump filename in `tb.v` to `tb.vcd` and run `make test FST=`.
