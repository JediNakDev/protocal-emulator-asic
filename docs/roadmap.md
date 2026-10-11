# Competition roadmap

Deliver a programmable, verified, routable protocol emulator in the competition's 6x4 allocation on IHP CMOS5L.
Users must be able to load new programs after fabrication and implement protocols within documented timing and I/O limits.
The [competition announcement](https://blog.janestreet.com/protocol-emulator-asic-competition/) sets the submission deadline at January 18, 2027.
The priorities and acceptance criteria below are this project's working plan.

## Completed milestones

| Milestone | Evidence |
| --- | --- |
| CMOS5L project and local verification toolchain | `info.yaml`, `src/config.json`, `make check` |
| Programmable two-engine core, assembler, and Host loader | [Specification](spec.md), `src/`, `tools/pe/asm.py`, `test/chip.py` |
| UART TX/RX, SPI mode-0 controller, and I2C write controller | `programs/`, `test/test_protocols.py` |
| Additional protocol demonstrations | [Coverage and limits](protocols.md), independent cocotb peers |
| Random comparisons with an independent specification-based model | `tools/pe/model.py`, `test/test_random.py` |
| Selected formal checks | [Formal guide](../formal/README.md), fetch, queue occupancy, and reset harnesses |
| Routed CMOS5L builds with precheck and functional gate-level tests | [Area and timing log](area-log.md), source-linked build results |
| Cycle-exact waveform capture, editing, and replay | `programs/capture.pasm`, `programs/replay.pasm`, `test/test_waveform.py` |

The current architecture and instruction semantics are defined in [spec.md](spec.md).
The initial single-engine UART exercise has been superseded by the implemented programmable design.
Recorded builds meet 50 MHz at the typical corner; all-corner setup closure remains open.
Changes after those measurements need a new routed build before their physical results are known.

## 1. Confirm physical closure

This is the next priority.
Publish a prerelease from the current source to run GDS and the timing estimate as described in [the release guide](releasing.md).
Compare the final reports with the recorded builds, including the cost of the fetch, decode, Host command, and setup-margin changes.

Complete this milestone when:

- A routed build of the selected source passes hardening, precheck, and gate-level regression.
- Setup and hold reports support the documented operating clock at every intended corner.
- Area, utilization, routing, fanout, slew, and capacitance reports have been reviewed and remaining violations explained or resolved.
- The area log records the source commit, workflow runs, configuration, and final results.

Global-route timing estimates are useful for comparing changes but can be optimistic after detailed routing.
Functional gate-level simulation checks the mapped logic; it does not establish timing closure.
Keep the 50 MHz target separate from any operating limit justified by final reports.

## 2. Resolve protocol and storage decisions

Use [the verified limitations](protocols.md#limits-found-during-verification) to decide which capabilities are needed for the final submission.
The largest candidates are a wait with timeout and access to an engine's ISR bit count.
Choose between flip-flop and latch storage using CMOS5L area, timing, and gate-level results.

Complete this milestone when:

- Each proposed hardware change has defined instruction timing, stalls, sampling, reset, and boundary behavior before implementation.
- Pin-level regressions demonstrate its benefit against independent peers.
- Both the specification and protocol guide describe the supported behavior and remaining limits.
- A release build measures its physical cost before the architecture grows further.
- The submitted storage variant has passed its own physical build and gate-level checks.

Prioritize reliable UART, SPI, and I2C behavior and the capture/edit/replay demonstration.
Keep USB, Ethernet, and CAN roles scoped to what the tests and electrical interfaces support.

## 3. Make the demonstrations reproducible

Turn the existing tests into a clear path from a clean checkout to a protocol demonstration.
The waveform demo should show the original capture, the Host edit, the replay, and the decoded effect of the injected fault.
Document minimum pulse widths, sustainable capture rates, buffering, loss flags, and replay underrun requirements.

Complete this milestone when:

- A clean checkout reproduces `make check` and the targeted protocol and waveform tests.
- Program upload and configuration examples identify the exact firmware and required pin mapping.
- A board wiring guide lists pull-ups, level shifting, clocks, and protocol front ends.
- Reset, Host stalls, buffer limits, missing peers, and data-loss reporting have regression coverage within the documented scope.
- Random failures retain a seed and waveform for reproduction.

If an FPGA or board is available, exercise the same programs against real devices and document the setup.
Track that as additional hardware evidence alongside simulation and ASIC checks.

## 4. Freeze and submit

Reserve January 4-17, 2027 for final checks, artifact review, documentation, and submission.
Finish architectural work early enough to run a complete physical build after the last RTL or configuration change.

Complete this milestone when:

- The final tag identifies the source used for passing checks and physical artifacts.
- The specification, `info.yaml`, datasheet, protocol guide, and measured operating limits agree.
- Release notes link the GDS, reports, gate-level results, and demonstration instructions.
- Licensing and attribution are complete for the project and incorporated components.
- The organizers' current rules and submission form have been checked and the submission completed before the deadline.

## Working rules

Run `make check` before pushing RTL or test changes.
Define externally visible behavior and retain top-level regression tests for fixes.
Add new RTL files to `info.yaml`, `test/Makefile`, and the root source list.
Keep measurements tied to exact commits, and use prereleases for intermediate physical builds.
Review physical results before increasing memory, adding engines, or expanding the instruction set.
