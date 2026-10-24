# Agent instructions

Read `CONTEXT.md` for project background, `README.md` for setup, and `docs/roadmap.md` before planning milestones.

## Non-negotiable protocol baseline

The goal is to support as many protocols as possible, including new protocols uploaded after fabrication.
Native support for every protocol named in the competition examples is mandatory: UART, SPI, I2C, JTAG, SWD, PS/2, CAN, low-speed USB, and 10BASE-T Ethernet.
This requirement cannot be violated or traded away to simplify timing closure, reduce area, meet a deadline, or optimize UART, SPI, and I2C alone.

Native support means uploaded programs and on-chip hardware perform protocol timing and signaling.
The Host may load programs, configure the chip, and exchange data; external pull-ups, level shifters, transceivers, and other electrical front ends are permitted.
Neither Host emulation nor an external protocol controller substitutes for native support.

Evaluate every architecture, ISA, storage, buffering, pin, and clock decision against the entire baseline and the ability to implement future protocols through general programmable operations.
Reject any decision that prevents native support for a baseline protocol or makes new protocols require RTL changes or another chip build.
Treat current protocol limitations as engineering gaps to resolve, not permission to downgrade a baseline protocol to an optional stretch goal.
Require independent top-level peer tests for every baseline protocol and routed timing evidence for its required operating clock before declaring the baseline complete.

## What we want

- **Programmability:** users can upload new programs after fabrication without changing RTL or rebuilding the chip.
- **Flexibility:** support the entire mandatory protocol baseline on the programmable architecture and provide general pin and timing operations for future protocols.
- **Novelty:** develop a useful, distinctive capability or design/verification approach, supported by a reproducible demonstration and clear evidence of its value.
- **Practicality:** establish reliable native support across the entire baseline, and measure the area and timing cost of architectural growth without sacrificing protocol coverage.
- **Quality:** prefer simplicity, robustness, and maintainability over development shortcuts.

## What to ensure

- Define instruction cycle counts, stalls, sampling latency, reset behavior, and pin drive modes before implementing them.
- Preserve the Tiny Tapeout interface and use synthesizable RTL with explicit widths, safe outputs, and no inferred latches.
- Reproduce bugs through top-level pins before fixing them; retain a regression test.
- Verify protocol behavior with independent cocotb peers, including timing, reset, and boundary cases.
- Run `make check` after RTL or test changes and resolve failures and warnings.
- Run synthesis and physical builds early; support operating limits with area, routing, and setup/hold reports.
- Register new RTL in `info.yaml`, `test/Makefile`, and the root lint command.
- Keep specifications, pin descriptions, and the datasheet consistent with actual behavior and documented limitations.
- Leave generated artifacts and changelogs to their generators.
