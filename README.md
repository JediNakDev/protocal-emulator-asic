# Programmable Protocol Emulator ASIC

Starter workspace for [Jane Street's protocol emulator ASIC competition](https://blog.janestreet.com/protocol-emulator-asic-competition/), based on the [Tiny Tapeout CMOS5L Verilog template](https://github.com/TinyTapeout/ttihp-verilog-template/tree/cmos5l).
The current RTL is the template's 8-bit adder, retained as a toolchain smoke test.
The programmable emulator has not been implemented yet.

## Competition configuration

- Process: IHP 130nm CMOS5L (`ihp-sg13cmos5l`).
- Allocation: `6x4` tiles, set in [info.yaml](info.yaml).
- Top module: `tt_um_jedinakdev_protocol_emulator`.
- Initial clock target: 50 MHz, matching the template's 20 ns `CLOCK_PERIOD`.
- Deadline: January 18, 2027, according to the [competition announcement](https://blog.janestreet.com/protocol-emulator-asic-competition/).
- License: Apache-2.0.

The clock frequency is a design target; verify it with post-route timing once the emulator exists.
The competition calls for a reprogrammable engine that can implement new protocols after fabrication.
Start with UART, SPI, and I2C, and prove correctness before expanding the protocol set.

## Local setup on macOS

Install [Homebrew](https://brew.sh/) and [uv](https://docs.astral.sh/uv/getting-started/installation/) if needed, then run:

```sh
brew install icarus-verilog verilator
uv python install 3.11
make setup
make check
```

`make setup` creates `.venv` using a uv-managed Python 3.11 and installs the template's pinned cocotb and pytest dependencies.
The root Makefile adds `.venv/bin` to the command path, so activating the environment is optional.
On Ubuntu, install `iverilog`, `verilator`, and `make` with apt instead of Homebrew, then use the same uv commands.

```sh
make test   # Simulate with Icarus Verilog and cocotb
make lint   # Check RTL with Verilator; warnings fail the command
make check  # Run lint and simulation
make clean  # Remove simulation outputs
```

Simulation saves the waveform to `test/tb.fst` and the JUnit report to `test/results.xml`.
Open the waveform in the Surfer VS Code extension, or a separately installed GTKWave.

## C model of the protocol engine

[model/](model/README.md) contains a cycle-accurate C model of the planned engine and its board: four engines at 40 MHz.
UART, SPI and I2C, plus USB low-speed host and 10BASE-T transmit, run there as engine programs.
They are checked by models of real parts (USB-UART bridge, W25Q128JV flash, ADT7420 sensor, USB keyboard, 10BASE-T receiver) against the pass criteria in [model/CRITERIA.md](model/CRITERIA.md).
It fixes the instruction set, pin and timing rules, and resource budget the RTL must implement.
Run it with `make model`.

## Where to work

- [src/project.v](src/project.v): Tiny Tapeout top-level wrapper and starter RTL.
- [info.yaml](info.yaml): metadata, tile allocation, source list, and pin descriptions.
- [src/config.json](src/config.json): physical implementation settings.
- [test/test.py](test/test.py): cocotb tests driven through the external chip interface.
- [test/tb.v](test/tb.v): simulation wrapper.
- [docs/info.md](docs/info.md): project datasheet source.

When adding RTL files, list each one in `info.yaml` and `test/Makefile`, and include it in the root Makefile's lint command.
Update the pin descriptions and datasheet when the emulator interface is defined.
Replace the author handle in `info.yaml` with your preferred attribution before submission.

## RTL to GDS

The existing `gds` GitHub Actions workflow uses `TinyTapeout/tt-gds-action@ihp-cmos5l` and `pdk: ihp-sg13cmos5l`.
After pushing your changes, it runs hardening, precheck, and gate-level simulation, and uploads the layout and build logs.
Use those results to check mapped area, routing, and timing early in development.
The `test` workflow runs RTL simulation and lint, while `docs` builds the project datasheet.

Enable GitHub Pages with **Source: GitHub Actions** in the repository settings to publish the layout viewer.
See the [Tiny Tapeout Pages instructions](https://tinytapeout.com/faq/#my-github-action-is-failing-on-the-pages-part).

The dev container is configured for the CMOS5L PDK and support-tools branch.
Full local hardening requires a running Docker engine, LibreLane, and the CMOS5L PDK installation used by the [CMOS5L action](https://github.com/TinyTapeout/tt-gds-action/tree/ihp-cmos5l).
The [general local-hardening guide](https://tinytapeout.com/guides/local-hardening/) still shows `ihp-sg13g2` for IHP; use the competition's CMOS5L configuration instead.

## First milestones

Read the [competition roadmap](docs/roadmap.md) for the goal, first UART task, architecture questions, phases, completion criteria, and suggested timeline.

1. Implement a UART transmitter and verify decoded bytes and bit timing through the top-level pins.
2. Make pin operations and waits programmable, with a way to load programs after fabrication.
3. Implement UART, SPI, and I2C as programs and verify them against independent protocol models.
4. Run GDS builds regularly and track area, timing, and routing as the architecture grows.

Fill out the update sign-up form linked in the [competition announcement](https://blog.janestreet.com/protocol-emulator-asic-competition/) to receive deadline and submission updates.
