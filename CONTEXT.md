# Project context

This project is an open-source entry for [Jane Street's protocol emulator ASIC competition](https://blog.janestreet.com/protocol-emulator-asic-competition/).
The chip supports hardware debugging, protocol experiments, and reverse engineering through programs uploaded after fabrication.
Its instruction set and hardware primitives provide general pin and timing operations that new protocols can reuse.

## Goals

- Make protocol behavior programmable without RTL changes or a new chip build.
- Support as many protocols as possible, including future protocols uploaded after fabrication.
- Meet the non-negotiable native protocol baseline defined in [AGENTS.md](AGENTS.md#non-negotiable-protocol-baseline); all competition examples are mandatory project capabilities.
- Demonstrate a distinctive capability with reproducible evidence: cycle-exact waveform capture, Host editing, and replay for fault injection.
- Keep the design routable and verify the area and timing cost of architectural changes.

## Constraints

| Constraint | Current configuration |
| --- | --- |
| Process and flow | IHP 130nm CMOS5L through Tiny Tapeout |
| Allocation | 6x4 tiles in `info.yaml`; planned competition maximum is 8x4 once its template is available |
| Interface | Standard Tiny Tapeout pins with an SPI-style Host port |
| Clock target | Project target of 50 MHz; competition has no fixed target and all-corner setup closure remains open |
| Submission deadline | January 18, 2027, confirmed by the organizer email |

[The organizer update and FAQ](docs/competition-update.md), recorded October 13, 2026, covers allocation, SRAM, I/O, reset and reload, judging, and submission requirements.
Keep designing to 6x4 until the 8x4 template is confirmed available.
Flexibility is the main judging criterion, and the submission write-up must describe AI use.
Recheck the current organizer rules and submission form before submission.
RP2040 PIO and TI PRU are architectural inspiration.

## Implemented design and evidence

The implemented architecture has two engines, shared writable instruction memory, per-engine buffers and checksum units, a shared bit state machine, and edge timestamp capture.
[docs/spec.md](docs/spec.md) defines its behavior.
[docs/protocols.md](docs/protocols.md) records the simulated protocol roles and limitations.

Verification includes top-level cocotb tests with independent protocol peers, random cycle-by-cycle comparisons against a specification-based Python model, and formal checks for selected properties.
Recorded CMOS5L builds passed hardening, precheck, and functional gate-level simulation and met 50 MHz at the typical corner.
The latest measured RTL (`17995b3`) has +4.05 ns typical-corner setup slack and -4.14 ns slow-corner setup slack, so all-corner closure remains open.
Use [docs/area-log.md](docs/area-log.md) to identify which source commit each physical result covers.

Physical CI runs on published releases and prereleases.
[docs/releasing.md](docs/releasing.md) describes the build process; [docs/roadmap.md](docs/roadmap.md) defines the remaining milestones.
The earlier four-engine C model in `model/` is an architecture experiment with a different ISA and is separate from the current RTL reference model.

## Terms

- **Engine:** hardware that executes pin and timing instructions.
- **Program:** instructions uploaded to implement protocol behavior.
- **Host:** the external system that loads programs and exchanges control and data with the chip.
