# Programmable Protocol Emulator ASIC

An open-source chip for hardware debugging, protocol experiments, and reverse engineering, built for [Jane Street's protocol emulator ASIC competition](https://blog.janestreet.com/protocol-emulator-asic-competition/).
Upload programs through the Host port to change protocol behavior after fabrication.
The same RTL runs UART, SPI, I2C, JTAG, SWD, PS/2, CAN, low-speed USB, and 10BASE-T programs in simulation.

Two programmable engines share 64 instruction words and control 13 protocol pins.
General building blocks provide checksums, line decoding, edge timestamps, input filtering, and cycle-exact pin operations.
Capture and replay programs can record a waveform, edit it on the Host, and replay it for fault injection without decoding its protocol.
See [protocol coverage and limits](docs/protocols.md) for the supported roles, timing, buffering requirements, and external hardware.

## Current status

The programmable core, protocol programs, independent peer tests, random reference-model comparisons, and selected formal checks are implemented.
Recorded CMOS5L builds passed hardening, precheck, and functional gate-level simulation.
Those builds met the 50 MHz target at the typical corner but failed setup timing at the slow corner.
The latest RTL and timing configuration need a new routed build before claiming timing closure.
[The area log](docs/area-log.md) ties each measurement to its source state; [the roadmap](docs/roadmap.md) defines the remaining work.

| Setting | Value |
| --- | --- |
| Process | IHP 130nm CMOS5L (`ihp-sg13cmos5l`) |
| Allocation | 6x4 Tiny Tapeout tiles |
| Top module | `tt_um_jedinakdev_protocol_emulator` |
| Clock target | 50 MHz (`CLOCK_PERIOD=20` ns) |
| Submission deadline | January 18, 2027, per the [competition announcement](https://blog.janestreet.com/protocol-emulator-asic-competition/) |
| License | Apache-2.0 |

## Run the checks

On macOS, install [Homebrew](https://brew.sh/) and [uv](https://docs.astral.sh/uv/getting-started/installation/), then run:

```sh
brew install icarus-verilog verilator yosys z3
uv python install 3.11
make setup
make check
```

On Ubuntu, install `iverilog`, `verilator`, `yosys`, `z3`, and `make` with apt, then run the same uv and Make commands.
`make setup` creates `.venv` with a uv-managed Python 3.11 and installs the pinned test dependencies.
The root Makefile adds `.venv/bin` to `PATH`, so activation is optional for root commands.
Formal checks require `yosys-smtbmc` and Z3; CI uses a current OSS CAD Suite and Yices.

| Command | Purpose |
| --- | --- |
| `make check` | Run lint, synthesis checks, formal checks, tooling regressions, and RTL simulation |
| `make test` | Run cocotb tests with Icarus Verilog |
| `make test LATCH=yes` | Run the RTL suite with latch storage |
| `make lint` | Check both storage variants with Verilator; warnings fail |
| `make synth-check` | Check RTL drivers with Yosys before optimization |
| `make formal` | Check fetch consistency, queue occupancy, and safe reset outputs |
| `make test-tooling` | Test the verification commands' failure handling |
| `make model` | Build and test the earlier C architecture model |
| `make clean` | Remove simulation, model, and formal build outputs |

Simulation writes `test/tb.fst` and `test/results.xml`.
Open the waveform with Surfer or GTKWave.
[The testbench guide](test/README.md) explains targeted runs, random seeds, and gate-level simulation.

## Load and run a program

The Host uses an SPI-style mode-0 port that starts in 1-bit mode and can switch to 4-bit transfers.
Hold `HCS_N` high and `HSCK` low while idle.
After reset, read register `0x00` by sending `0x80` and one dummy byte; the returned data byte is `0x50`.
Load instruction words through `IMEM_ADDR` and `IMEM_DATA`, configure the engine and pin ownership, then enable the engine through `CTRL`.
Instruction memory is undefined after reset and must be written before execution.

The [specification](docs/spec.md) defines the register map, instruction encoding, cycle counts, stalls, sampling latency, and reset behavior.
Program comments in [programs/](programs/) describe their required configuration and data packing.
The assembler is a Python library in [tools/pe/asm.py](tools/pe/asm.py); [test/chip.py](test/chip.py) demonstrates loading programs through the top-level Host pins.

Bidirectional pins `P0`-`P7` support push-pull and open-drain drive and are released after reset.
`P8`-`P9` are input-only; `P10`-`P12` are output-only and drive zero after reset.
Provide external pull-ups for lines that must idle high, and use the electrical interfaces listed in [the protocol guide](docs/protocols.md#external-hardware).

## CI and physical builds

Normal pushes run RTL verification, the C model checks, and datasheet generation.
Publishing a GitHub release or prerelease runs all physical CI:

- `gds`: CMOS5L hardening, precheck, functional gate-level tests, and the layout viewer.
- `timing`: setup and hold estimates at three corners after global routing.

Branch pushes, tag pushes, and draft releases do not start these physical workflows.
The timing estimate helps compare changes; final operating limits require detailed-route reports.
[The release guide](docs/releasing.md) describes how to select a source commit, start a build, and review its artifacts.

Enable GitHub Pages with **Source: GitHub Actions** to publish the layout viewer, and allow the release tags in the `github-pages` environment's deployment rules if that environment restricts refs.
The physical workflows use `TinyTapeout/tt-gds-action@ihp-cmos5l` and the CMOS5L PDK.
Local hardening requires Docker, LibreLane, and the corresponding PDK installation; use the [CMOS5L action](https://github.com/TinyTapeout/tt-gds-action/tree/ihp-cmos5l) as the configuration reference.

## Repository guide

| Path | Contents |
| --- | --- |
| [CONTEXT.md](CONTEXT.md) | Project goals, constraints, and terminology |
| [docs/spec.md](docs/spec.md) | Authoritative interface, instruction, and timing definitions |
| [docs/protocols.md](docs/protocols.md) | Protocol demonstrations, limits, and wiring requirements |
| [docs/roadmap.md](docs/roadmap.md) | Completed milestones and remaining acceptance criteria |
| [docs/area-log.md](docs/area-log.md) | Source-linked synthesis and routed measurements |
| [docs/info.md](docs/info.md) | Tiny Tapeout datasheet source |
| [src/](src/) | Top-level wrapper and synthesizable RTL blocks |
| [info.yaml](info.yaml), [src/config.json](src/config.json) | Metadata, pin descriptions, source list, and physical settings |
| [programs/](programs/), [tools/pe/](tools/pe/) | Firmware, assembler, register definitions, decoder tables, and Host helpers |
| [test/](test/README.md), [formal/](formal/README.md) | Pin-level simulations, reference comparisons, and formal properties |
| [model/](model/README.md) | Earlier four-engine C architecture exploration at 40 MHz |

The C model has a different ISA, Host interface, and resource budget from the two-engine RTL.
The current RTL reference model is [tools/pe/model.py](tools/pe/model.py).

When changing RTL, define externally visible behavior first and verify it through the top-level pins.
Register new RTL files in `info.yaml`, `test/Makefile`, and the root Makefile's source list.
Run `make check`, keep the specification and datasheet consistent, and record physical measurements against the exact build commit.
