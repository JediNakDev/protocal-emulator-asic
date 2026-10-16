# Competition update and FAQ

Source: organizer email from Ben and Anish, supplied by the project owner and recorded here on October 13, 2026.
The email's send date was not provided.
This note summarizes that email; it does not independently confirm later changes or template availability.
The organizers planned to update the [competition blog](https://blog.janestreet.com/protocol-emulator-asic-competition/) in the following weeks and add a submission form closer to the deadline.

## Allocation, process, and physical flow

- The planned maximum allocation is 8x4 tiles.
  Tiny Tapeout expected the 8x4 CMOS5L template around mid-October 2026 and planned to email entrants when it became available.
  Keep designing to 6x4 until then and treat 8x4 as an upgrade.
- IHP SRAM macros are permitted, including multiple macros, provided the design fits its tiles and passes Tiny Tapeout precheck.
  [Ken Pettit's SRAM walkthrough](https://kdp1965.github.io/ihp-um-janestreet-prism/ihp_sram_macro.html) uses two macros and covers RTL, LibreLane configuration, Metal4 power, and sign-off.
- Use the Tiny Tapeout GDS flow, since winning designs will use it on the shuttle.
  GitHub Actions have a six-hour limit; if a near-full design exceeds it, run the same flow using the [local hardening guide](https://tinytapeout.com/guides/local-hardening/).
- The CMOS5L installation problem in the Tiny Tapeout GDS action had been fixed a few weeks before the email.
  Affected entrants should rerun with the latest action.

## Pins, electrical interfaces, and clocks

- The interface provides 24 digital pins: eight inputs, eight outputs, and eight bidirectional pins, plus clock and reset.
  There are no analog pads.
  Internal analog or mixed-signal circuits, such as a PLL, are permitted if they pass precheck.
- External components are permitted on a Pmod, including pull-ups, transceivers, and level shifters.
  The demo board uses 3.3 V digital I/O.
  Document every required external component and connection; use the [ETR board and Pmod specifications](https://tinytapeout.com/specs/pcb-etr/) and [recommended pinouts](https://tinytapeout.com/specs/pinouts/) as references.
- At the time of the email, CMOS5L had no returned silicon and no characterized pad numbers.
  Tiny Tapeout was checking whether numbers from the similar SG13G2 process were available.
- The competition sets no clock-frequency target.
  Choose a frequency that closes timing after place-and-route and supports the demonstrated protocols.

## Architecture and protocol expectations

- The chip must be reprogrammable after fabrication to support new protocols.
  A CPU is optional; eFPGA designs, multiple engines sharing a pipeline, and other architectures are allowed.
  Include the software side: an assembler or toolchain, or a bitstream flow, and example programs for supported protocols.
- Start with UART, SPI, and I2C.
  There are no fixed speed requirements; common UART baud rates, SPI modes 0-3, and I2C at 100 and 400 kHz are suggested targets.
  Concurrent protocols and support for both controller and target roles are advantages but are optional.
- Low-speed USB and 10 Mbit Ethernet are stretch goals.
  More on-chip protocol work, such as line coding, CRC, and clock recovery, strengthens those demonstrations.
- Program loading is part of the entrant's design.
  The demo board's microcontroller is the natural Host, but any documented Host is acceptable.
- Flops do not start at zero; use `rst_n` to reach a known state.
  The demo board can trigger reset through a button or software.
  Deselection powers the design down and loses its state, so reload its program after selection.
  See Tiny Tapeout's [FPGA-to-ASIC guidance](https://tinytapeout.com/hdl/fpga_vs_asic/).

## Entry and submission requirements

- Students and professionals can enter individually or in teams of any size.
  Each team submits one entry.
- Receiving the organizer email confirms that sign-up succeeded.
  The sign-up form sends no confirmation email, and no further registration step is required.
  Sign-up exists to receive updates.
- Organizers cannot review individual architectures before the deadline, but entrants may email specific questions.
- Submit a public repository link containing RTL, tests, and a passing GDS/precheck from the Tiny Tapeout flow.
  Include architecture and instruction-set documentation, the assembler or toolchain with example programs, and a verification write-up.
  A video or demo is optional and will be considered if provided.
- Development may remain private until submission.
- AI tools are allowed; describe their use in the write-up.
- A design may also go on another shuttle, provided the competition entry is its own open-source CMOS5L submission by the deadline.

## Judging

There is no fixed weighting.
Flexibility is the main criterion: can the chip be reprogrammed for protocols it was not designed for?
Judges will also consider demonstrated protocols, unique architectural capabilities, and the design and verification approach.
A strong architecture and verification approach can be competitive with fewer protocols.

## Timeline and prizes

| Event | Organizer guidance |
| --- | --- |
| Submission deadline | January 18, 2027 |
| Selection announcement | Aiming for early February 2027 |
| CMOS5L shuttle | March 2027 |
| Selected designs | Around four planned; the number may change |
| Winners' hardware | Fabricated chip mounted on the latest Tiny Tapeout ETR demo board |

After the deadline, small non-functional fixes, such as re-hardening with a newer flow, are allowed.
The organizers will work with winners on those fixes.
The Tiny Tapeout Discord competitions channel is available for teammate searches and discussion.
Questions can go to [asic-competition@janestreet.com](mailto:asic-competition@janestreet.com).

## Implications for this project

Keep the current 6x4 configuration until the 8x4 template is confirmed available and an upgrade is justified by physical measurements.
The project's 50 MHz clock is a design target, not a competition requirement; final operating limits must follow routed timing evidence.
The project's [non-negotiable native protocol baseline](../AGENTS.md#non-negotiable-protocol-baseline) requires all competition examples, including those the organizers describe as stretch goals, and programmability for future protocols.
Choose clocks and architectural changes that preserve this entire baseline, and treat current limitations as engineering gaps to resolve.
Demonstrate capture/edit/replay alongside the mandatory protocol coverage.
Document Host loading, reset, reload after deselection, external wiring, and AI use alongside the final verification evidence.
Recheck the current organizer rules and submission form before submitting.
