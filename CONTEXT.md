# Project context

This project is an open-source entry for [Jane Street's protocol emulator ASIC competition](https://blog.janestreet.com/protocol-emulator-asic-competition/).
The chip supports hardware debugging, protocol experiments, and reverse engineering through programs uploaded after fabrication.
Its instruction set and hardware primitives provide general pin and timing operations that new protocols can reuse.

## Goals

- Make protocol behavior programmable without RTL changes or a new chip build.
- Establish UART, SPI, and I2C as reliable base examples, then demonstrate additional protocols within documented limits.
- Demonstrate a distinctive capability with reproducible evidence: cycle-exact waveform capture, Host editing, and replay for fault injection.
- Keep the design routable and verify the area and timing cost of architectural changes.

## Constraints

| Constraint | Current configuration |
| --- | --- |
| Process and flow | IHP 130nm CMOS5L through Tiny Tapeout |
| Allocation | 6x4 tiles in `info.yaml` |
| Interface | Standard Tiny Tapeout pins with an SPI-style Host port |
| Clock target | 50 MHz; all-corner setup closure remains open |
| Submission deadline | January 18, 2027, per the competition announcement |

Recheck the announcement before submission for changes to the allocation, deadline, and submission process.
RP2040 PIO and TI PRU are architectural inspiration.

## Implemented design and evidence

The implemented architecture has two engines, shared writable instruction memory, per-engine buffers and checksum units, a shared bit state machine, and edge timestamp capture.
[docs/spec.md](docs/spec.md) defines its behavior.
[docs/protocols.md](docs/protocols.md) records the simulated protocol roles and limitations.

Verification includes top-level cocotb tests with independent protocol peers, random cycle-by-cycle comparisons against a specification-based Python model, and formal checks for selected properties.
Recorded CMOS5L builds passed hardening, precheck, and functional gate-level simulation and met 50 MHz at the typical corner.
The recorded slow-corner setup results fail the target; the latest RTL and configuration changes still require routed measurements.
Use [docs/area-log.md](docs/area-log.md) to identify which source commit each physical result covers.

Physical CI runs on published releases and prereleases.
[docs/releasing.md](docs/releasing.md) describes the build process; [docs/roadmap.md](docs/roadmap.md) defines the remaining milestones.
The earlier four-engine C model in `model/` is an architecture experiment with a different ISA and is separate from the current RTL reference model.

## Terms

- **Engine:** hardware that executes pin and timing instructions.
- **Program:** instructions uploaded to implement protocol behavior.
- **Host:** the external system that loads programs and exchanges control and data with the chip.
