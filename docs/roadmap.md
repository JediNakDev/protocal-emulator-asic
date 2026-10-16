# Competition roadmap

Deliver a programmable, verified, routable protocol emulator in this project's current 6x4 allocation on IHP CMOS5L.
The [organizer update and FAQ](competition-update.md) plans a maximum of 8x4 tiles, but says to design to 6x4 until the new template is available and treat 8x4 as an upgrade.
Users must be able to load new programs after fabrication and implement protocols within documented timing and I/O limits.
The [competition announcement](https://blog.janestreet.com/protocol-emulator-asic-competition/) sets the submission deadline at January 18, 2027.
The priorities and acceptance criteria below are this project's working plan.
Every milestone must preserve the [non-negotiable native protocol baseline](../AGENTS.md#non-negotiable-protocol-baseline) and programmability for future protocols.
The organizers' optional stretch goals are mandatory capabilities for this project.

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
The latest measured RTL (`17995b3`) reaches +4.05 ns typical-corner setup slack and -4.14 ns slow-corner setup slack.

[The issue #14 design review](design-review.md) defines the next architecture choices and their evidence gates.
Timed waits, shift-count reads, exact receive records, all four SPI controller modes with chip select and I2C reads are implemented in the current working source.
USB keep-alive and reset, PS/2 deadlines and on-chip CAN acknowledgement are implemented; the CAN node fills all 128 instruction words.
CAN error signaling (B9) needs more instruction memory or denser instructions, and Ethernet receive margin (B12) needs a larger decoder or a faster clock; both wait on the decisions recorded in the design review.
Develop these capabilities alongside physical feasibility; timing closure on the current demonstrations alone does not freeze an adequate architecture.

## 1. Confirm physical closure

Run this work alongside the protocol and storage work in milestone 2.
Select operating clocks that support the entire mandatory protocol baseline.
A lower clock is acceptable only if all baseline protocols remain natively achievable and are reverified at their required operating clocks.
Use the routed results for `17995b3` as the baseline for the next timing changes.
Publish a prerelease after each selected change to run GDS and the timing estimate as described in [the release guide](releasing.md).
Compare the final reports with that baseline, including the area cost and remaining timing and electrical violations.

Complete this milestone when:

- A routed build of the selected source passes hardening, precheck, and gate-level regression.
- Setup and hold reports support the documented operating clock at every intended corner.
- Area, utilization, routing, fanout, slew, and capacitance reports have been reviewed and remaining violations explained or resolved.
- The area log records the source commit, workflow runs, configuration, and final results.

Global-route timing estimates are useful for comparing changes but can be optimistic after detailed routing.
Functional gate-level simulation checks the mapped logic; it does not establish timing closure.
Keep the 50 MHz target separate from any operating limit justified by final reports.

## 2. Resolve protocol and storage decisions

Use [the verified limitations](protocols.md#limits-found-during-verification) to identify and resolve gaps in the mandatory native protocol baseline.
Timed waits and access to an engine's ISR bit count are specified and implemented in the current source.
Use the review's B8-B13 requirements and acceptance tests to address the remaining gaps.
Choose between flip-flop and latch storage using CMOS5L area, timing, and gate-level results.

Complete this milestone when:

- Each proposed hardware change has defined instruction timing, stalls, sampling, reset, and boundary behavior before implementation.
- Pin-level regressions demonstrate its benefit against independent peers.
- Both the specification and protocol guide describe the supported behavior and remaining limits.
- A release build measures its physical cost before the architecture grows further.
- The submitted storage variant has passed its own physical build and gate-level checks.
- Every baseline protocol has independent top-level peer verification, with required operating clocks supported by routed timing evidence.
- Fuller programs and simultaneous peers justify the selected instruction capacity, engine count, decoder count and Host service profiles.

Deliver reliable native support for every baseline protocol alongside the capture/edit/replay demonstration.
Document remaining limitations accurately and track gaps that prevent meeting the baseline as unfinished work.
Choose general programmable operations that also enable future protocols.

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
- The public repository includes architecture and ISA documentation, the assembler and example programs, a verification write-up describing AI use, and passing Tiny Tapeout GDS/precheck evidence.
- The organizers' current rules and submission form have been checked and the submission completed before the deadline.

## Working rules

Run `make check` before pushing RTL or test changes.
Define externally visible behavior and retain top-level regression tests for fixes.
Add new RTL files to `info.yaml`, `test/Makefile`, and the root source list.
Keep measurements tied to exact commits, and use prereleases for intermediate physical builds.
Review physical results before increasing memory, adding engines, or expanding the instruction set.
