# Agent instructions

Read `CONTEXT.md` for project background, `README.md` for setup, and `docs/roadmap.md` before planning milestones.

## What we want

- **Programmability:** users can upload new programs after fabrication without changing RTL or rebuilding the chip.
- **Flexibility:** implement UART, SPI, and I2C on the same engine, with general pin and timing operations that can express additional protocols.
- **Novelty:** develop a useful, distinctive capability or design/verification approach, supported by a reproducible demonstration and clear evidence of its value.
- **Practicality:** establish reliable base protocols before stretch goals, and measure the area and timing cost of architectural growth.
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
