# Design review: native baseline and future programmability

This review answers [issue #14](https://github.com/JediNakDev/protocal-emulator-asic/issues/14).
It checks the architecture against the [non-negotiable native protocol baseline](../AGENTS.md#non-negotiable-protocol-baseline): UART, SPI, I2C, JTAG, SWD, PS/2, CAN, low-speed USB and 10BASE-T Ethernet, plus programs uploaded for protocols nobody planned.
It records the decisions taken, the evidence behind them, and the work that remains.
The baseline is not complete: section 7 lists the protocol and physical evidence still required.

The review was carried out on October 13-14, 2026, on top of `9ae3776`.
Measurements of program size, Host throughput and protocol tolerances come from the RTL and tests of this change; the synthesis estimate is local and unrouted.

### October 26 follow-up

The original review and its estimates below describe the merged 128-word design; the tables of sections 1, 2 and 4 and the decisions of section 5 are updated to the current source.
The working implementation has 512 instruction words in eight pages of 64, stored in explicitly instantiated latches; a programmable clock-recovery block (G7); a falling-edge output phase for half-cycle transmit timing; and the CMOS5L 8x4 allocation.
The template precondition is satisfied by [tt-support-tools d66cf17](https://github.com/TinyTapeout/tt-support-tools/commit/d66cf179e7bc4d296362ab7e2e3b344dc3c4f665).
Loaders select pages by forcing `set page` before the entry jump; EXECCFG bit 7 is reserved.
The [specification](spec.md) describes the current interface.

- B9 is done: `can_node` (472 words over eight pages) with `can_line` (39) implements ISO 11898-1 error confinement on chip, verified against an independent node model at the pins ([section 2](#2-blocking-limitations)).
- B12 is done in simulation: Ethernet now runs at 50 MHz, and G7 takes the transition nearest its reference for each bit; the 802.3 limit of +/-13.5 ns passes in every jitter model, with about 0.5 ns of margin at the worst case ([protocols](protocols.md#ethernet-receive-margin)).
- B14 is done and kept: the node releases TXD when its queue runs dry, then signals the error.
- Instruction memory exceeds the owner's C3 decision of 256 words: B9 needs 511 words with the line program. Six pages of flip-flops failed clock-tree placement at 75-79% utilization, so the store became latches, which place at 64% with eight pages ([storage](#instruction-memory-and-storage)). The owner should confirm this reversal of C3 and the use of latch cells.
- Routed timing at the slow corner for the current RTL is not yet established; the [area log](area-log.md) records each build.

## Summary of decisions

| Topic | Decision | Status |
| --- | --- | --- |
| Timed wait | Timed `wait` with X as the count of checks; X is 0 afterwards only on timeout | Implemented ([spec](spec.md#timed-wait)) |
| Record lengths and boundaries | `mov x, isrcnt` reads the ISR count; receivers close messages with count-terminated records | Implemented ([framing](protocols.md#record-framing)) |
| Protocol gaps closed with them | CAN receive resynchronization, USB reply deadline, Ethernet frame boundaries and link pulses, SWD WAIT/FAULT, SPI modes 1-3 and chip select, I2C reads at 100 and 400 kHz | Implemented with pin-level regressions |
| Engines | Keep two for now; evaluate a third with the 8x4 allocation | Provisional |
| Bit state machine (G2) | Keep one shared instance; it limits concurrency, not individual protocols | Provisional |
| Clock | Keep 50 MHz as the physical target and 48/50 MHz program profiles (USB at 48 MHz, every other protocol at 50 MHz); 60 MHz is the lowest common clock with exact integer bit intervals | Provisional, evaluation recorded |
| Instruction memory | 512 words in eight pages of 64; a forced `set page` selects each engine's starting page and `set page` lets a program cross pages | Implemented ([spec](spec.md#instruction-memory)); the CAN node with error confinement uses 511 words; provisional until a routed build of this RTL closes timing (section 5) |
| G2 emission target | G2_CTRL bit 5 emits into the engine that does not own G2, so one engine can decode the line while the other runs the protocol | Implemented ([spec](spec.md#bit-state-machine-g2)) |
| Storage | Latches (`sg13cmos5l_dlhrq_1`, instantiated), since six pages of flip-flops failed placement; `PE_FLOP_STORE` keeps the flip-flop variant | Implemented; needs routed timing and gate-level evidence, and the owner's confirmation |
| Allocation | 8x4 | Implemented |

## 1. Protocol status

Each row lists what is implemented and verified at the top-level pins today, and what the baseline still needs.
"Verified" means RTL simulation against an independent peer; no row has board evidence yet.

| Protocol | Roles implemented | Rates verified | Evidence | Still missing |
| --- | --- | --- | --- | --- |
| UART | 8N1 transmitter and receiver | 16 cycles per bit (3.125 Mbaud at 50 MHz); any rate `f / (8 (CLKDIV + 1))` | `test_uart_tx`, `test_uart_rx` | Standard baud rates checked in absolute time, receiver tolerance to baud offset, other frame formats, break and flow control |
| SPI | Controller, modes 0-3, with chip select | SCK 6.25 MHz, and 12.5 MHz in modes 0 and 2 with MISO bypass | `test_spi_controller_mode0`-`mode3`, `test_spi_controller_12mhz_bypass`, `test_spi_host_pause` | Peripheral role; more than one chip select |
| I2C | Controller: write, read, repeated START, stretching | 100 kHz and 400 kHz, UM10204 minimums checked in ns | `test_i2c_controller_100khz`, `_400khz`, `_400khz_no_stretch` | Target role; multi-controller arbitration; bus recovery; SMBus clock-low timeout |
| JTAG | Controller | TCK 6.25 MHz | `test_jtag_idcode_ir_dr_bypass` | Other TCK rates in absolute time; TRST and system reset pins |
| SWD | Host: transactions with on-chip ACK check, line reset, sequences | SWCLK 6.25 MHz | `test_swd_read_write`, `test_swd_bad_request_and_recovery`, `test_swd_wait`, `test_swd_fault_and_abort` | ORUNDETECT = 1; dormant-state selection sequences tested; target role |
| PS/2 | Device and host, with bounded waits | 10-16.7 kHz clock | `test_ps2_*` | Real-device evidence |
| CAN 2.0A and 2.0B | Node with ISO 11898-1 error confinement: transmitter with arbitration, receiver that checks the CRC and acknowledges, error and overload frames, error counters, error passive and bus-off; listen-only 2.0A receiver | 1 Mbit/s; the node acknowledges transmitters 1.5% fast and slow, also after ten bits without an edge; listen-only receive tolerance 2.2% fast to 3.5% slow measured | `test_can_*` | A synchronization jump width limit; real-transceiver evidence |
| Low-speed USB | Host, bit level, with a 17-bit-time reply deadline, keep-alive and bus reset | 1.5 Mbit/s at 48 MHz; device clock +/- 1.5% | `test_usb_*` | Device role; operation at 50 MHz |
| 10BASE-T | Transmitter with link pulses; receiver with exact framing | 10 Mbit/s at 50 MHz; receive jitter +/- 13.5 ns at +/- 200 ppm (802.3) | `test_eth_*`, `tools/eth_margin.py` | Link integrity state; collision handling; full duplex at line rate |

Waveform capture, Host editing and exact replay remain the distinctive capability ([protocols](protocols.md#waveform-capture-and-replay)); this review does not change them.

## 2. Blocking limitations

Each limitation that blocks reliable native support becomes a required behavior and a test at the top-level pins.
"Done" rows have that test in this change.

| ID | Gap | Required behavior | Acceptance test at the pins | Status |
| --- | --- | --- | --- | --- |
| B1 | CAN receive did not resynchronize (worked from -0.2% to +0.4% offset) | Re-time the sample point on recessive-to-dominant edges; receive at the full 1.58% CAN tolerance | 8-byte frames, including ten-bit stretches without an edge, from a transmitter 1.5% fast and 1.5% slow | Done: `test_can_receive_clock_offset`, `_fast` |
| B2 | A missing USB reply stalled the program | Give up 16-18 bit times after the end of SE0, report it, run the next command | No reply; a reply at 16.75 bit times received, at 17.25 reported missing; the next transaction succeeds | Done: `test_usb_missing_reply_is_bounded`, `test_usb_reply_inside_deadline`, `_after_deadline` |
| B3 | Ethernet frame boundaries depended on Host timing | Records keep exact lengths and boundaries whenever the Host reads them | Back-to-back frames at the 9.6 µs minimum gap, with link pulses and a Host pausing between reads | Done: `test_eth_receive_back_to_back` |
| B4 | No Ethernet link pulses | Normal link pulses every 16 +/- 8 ms while idle, 100 ns wide; frames start promptly | Pulse width, interval from idle and after a frame | Done: `test_eth_link_pulses` |
| B5 | SWD sent the data phase after WAIT or FAULT, so a write's data could read as a request and the target could drive SWDIO against the host | Skip the data phase after any ACK other than OK and keep the command stream aligned | A busy AP answers WAIT to a write whose data is a valid request byte, but not to a CTRL/STAT read; an AP error sets STICKYERR, so AP and DP accesses get FAULT until ABORT clears it; then successful retries; no contention | Done: `test_swd_wait`, `test_swd_fault_and_abort` |
| B6 | SPI had mode 0 only, and the Host drove chip select | Modes 0-3 with SCK idling at CPOL; the program drives chip select and holds it across a late Host | Multi-byte transactions, back to back and with a Host pause inside one, against a mode-parametric peripheral that answers only while selected; CS_N setup, hold and high times checked in ns | Done: `test_spi_controller_mode0`-`mode3`, `test_spi_host_pause` |
| B7 | I2C had writes only and no speed evidence | Reads with ACK/NACK, repeated START, stretching, at 100 and 400 kHz within UM10204 timing | Write, pointer write plus repeated-START read, NACK; timing checked in ns | Done: `test_i2c_controller_*` |
| B8 | The chip does not acknowledge received CAN frames | A correctly received frame gets a dominant ACK slot from the chip, with no external protocol controller | A node transmitting alone on the bus sees its frame acknowledged by the chip, with the CRC delimiter, ACK delimiter and end of frame recessive; a frame with a bad CRC is not acknowledged | Done: `test_can_node_acks` (also 1.5% fast and slow), `test_can_node_bad_crc_not_acked` |
| B9 | No CAN error signaling | Error frames on stuff, form, CRC and bit errors; error counters, error-passive and bus-off | A node injecting each error type; recovery after bus-off | Done: `test_can_node_bad_crc_not_acked`, `_error_recovery`, `_receive_error_counts` (rules 1, 2, 6, 8, 11), `_transmit_error_counts` (3, 3b, 7), `_bit_error_in_flag` (4, 5), `_error_passive_suspend` (3a, 9, suspend transmission), `_bus_off` (10, 12), `_overload`, `_start_in_intermission_bit3`, `_bus_integration` ([section 5](#can-node-capacity-and-error-signaling-b9)) |
| B10 | No USB keep-alive or bus reset | Keep-alive within each 1 ms while idle; a reset SE0 of at least 50 ms (a root port, USB 2.0 TDRSTR) on request | Device model that suspends after 3 ms idle; reset length checked in absolute time | Done: `test_usb_keep_alive`, `test_usb_bus_reset` |
| B11 | PS/2 waits forever for a silent peer | Bounded waits with a reported failure | Host program against an absent device; device program against an inhibiting host | Done: `test_ps2_host_absent_device`, `test_ps2_host_device_stops`, `test_ps2_device_inhibited` |
| B12 | Ethernet receive jitter margin below 802.3 | Decode with the jitter 802.3 allows at the receiver | Jitter sweep to the standard's limit, at all input phases | Done in simulation: `test_eth_receive_jitter`, `_extreme_jitter`, `_cable_jitter`, `_long_frame` at the pins, and the `tools/eth_margin.py` sweep ([section 5](#ethernet-receive-jitter-b12)) |
| B13 | Peers run in cycles, so a chip clock change scales both sides | Peers keep their wire rates in absolute time | Each program at its clock profile, checked in ns, and failing when the chip clock is wrong | Done: every protocol test module has a `*_wrong_clock` control, and the listen-only CAN and Ethernet receivers have their own (`test_can_receive_wrong_clock`, `test_eth_receive_wrong_clock`) |
| B14 | A CAN transmit queue that runs dry inside a frame stalls `can_node` with TXD at the last bit's level, which can hold the bus dominant | A transmitter that runs out of queued bits releases TXD and ends the frame, never holding the bus dominant | The Host stops feeding a frame part way; the bus returns to recessive within a bit | Done: `test_can_node_transmit_underrun`; the error-confinement node keeps 4 transmit words (20 bits) beside its counters, so the Host-pause regression is now 14 µs (`test_can_node_transmit_host_pause`) |

## 3. Timeout and record-boundary interfaces

### Timed wait

`wait <pol> <gpio|pin|flag> <n> timeout` makes at most X checks, one per tick, and leaves X at 0 only if it timed out ([specification](spec.md#timed-wait)).

- Encoding: bit 4 of `wait`, which no source used before (pin indices need 4 bits, flags 3).
- Deadline: counted in checks, so in ticks of the engine's clock divider; a satisfied check leaves X = N - k + 1.
- Outcome: `jmp !x` selects the timeout path; X also tells when the condition arrived, which CAN uses to tell "already dominant" from "edge in the window".
- Interactions: the X decrement survives the stall, besides `irq wait`'s flag and side-set; the delay applies once after completion.
- A later timed wait can reuse the remaining X budget, but successful checks and intervening instructions do not decrement it; programs needing a wall-clock deadline across several waits must account for those cycles explicitly.
- Reserved: timed `pattern` waits, since the pattern uses X; they run as `nop`.

Alternatives considered:

| Option | Why not chosen |
| --- | --- |
| A dedicated deadline register per engine | 16 more flops and a load path per engine, and a new way to load it; X already has a decrement path for `jmp x--` |
| On timeout, jump to a vector or skip the next instruction | Needs a second address path in the fetch logic, which is on the critical timing path |
| A loop of `jmp pin` and `jmp x--` | Two cycles per check and a branch penalty in resolution; CAN resynchronization needs one-cycle checks |

Register pressure is the cost: a timed wait uses X.
Every program in this change found X free at the waiting point; the USB host and CAN receiver load it from Y.

### Record lengths and boundaries

`mov <dest>, isrcnt` and `mov <dest>, osrcnt` read the shift counts through `mov` operation 3, which was a duplicate of operation 0.
Receivers close variable-length messages with [count-terminated records](protocols.md#record-framing): 15-bit data words, the partial last word, and a trailer `0x8000 | count` that payload cannot contain.

| Option | Why not chosen |
| --- | --- |
| A 17th "end of record" bit per buffer entry | Widens every buffer entry and the Host port format; the in-band trailer costs 1 bit in 16 |
| Markers and padding (the previous scheme) | Took up to 16 instructions per record; Ethernet had no separator that payload could not contain |
| A length prefix | Unknown until the message ends |

Interactions: the count is read at the start of the cycle, before any G2 emission in that cycle; the programs read it only after their decoder has stopped emitting.

## 4. Measurements

### Program capacity

| Program | Words | Engines | Other shared resources |
| --- | --- | --- | --- |
| `swd` | 48 | 1 | |
| `can_node` + `can_line` | 472 + 39 | 2, across eight pages | G2, CRC, 8 flags, buffer RAM |
| `can_rx` (listen-only) | 21 | 1 | G2 |
| `ps2_host` / `ps2_device` | 42 / 34 | 1 | |
| `i2c` | 24 | 1 | |
| `eth10_tx` + `eth10_rx` | 30 + 7 | 2 | G2, G7, falling-edge sample and output phase |
| `usb_ls_host` + `usb_ls_rx` | 37 + 5 | 2 | G2, output pin 12 |
| `uart_tx` + `uart_rx` | 4 + 9 | 2 | |
| `capture` + `replay` | 7 + 3 | 1 each | Edge capture, one flag |
| `spi_cpha0` / `spi_cpha0_fast` / `spi_cpha1` | 11 each | 1 | |
| `jtag` | 2 | 1 | |

Every program except the CAN node fits one 64-word page alone, and SWD with a UART receiver fits one page together (57).
Robust behavior costs words: SWD grew from 13 to 48 to check ACKs on chip, the CAN receiver from 16 to 21 for resynchronization, and the PS/2 and USB programs by 12 to 19 words each for bounded waits, keep-alive and reset.
The acknowledging CAN node (B8) measured 99 words for the node engine and 29 for the line engine, all 128 words of both pages, against an estimate of 60 plus 25; with error confinement (B9) it measures 447 instructions plus 25 words of alignment padding, and 39 for the line engine.
The estimate left out extended identifiers, remote frames, the stuff bit that may precede the CRC delimiter, the field split that the 16-bit ISR count forces, and the `set page` words that cross pages.

Reproduce every size from the actual uploaded source with `python tools/measure.py`.
The JSON includes the base commit and SHA-256 of the source files, so uncommitted measurements can be distinguished from the base commit.

### Buffers and Host throughput

[Measured through the pins](protocols.md#host-throughput): the receive path sustains one word per 44 cycles (18.2 Mbit/s at 50 MHz) with a poll-then-burst Host at `f_clk / 8`, and an 8-word queue covers 8 production periods minus about 330 cycles of reading.
Ethernet receive loads that path to 59% at 50 MHz and tolerates about 5 µs of Host pause; CAN and USB load it below 10%.
Ethernet full duplex at line rate exceeds the demonstrated duplex rate.
Larger buffers would lengthen tolerable pauses but cannot raise the sustained rate; the sustained rate is limited by `f_clk / 8` on `HSCK` and per-transaction overhead.

`test_host.test_host_duplex_throughput` measures simultaneous polling, receive bursts and transmit bursts, with independent observation of transmitted bytes on the pins.
The normal 4-word TX/4-word RX split sustains a word in each direction every 128 cycles over 300 words (6.25 Mbit/s per direction at 50 MHz), without receive loss or transmit starvation.
A word in each direction every 64 cycles overloads the same Host schedule.
These are demonstrated profiles and a failing control, not a measured maximum or a guarantee for another Host implementation.
Record trailers and protocol command words consume additional capacity.

### Pins

| Protocol | Pins | Kind |
| --- | --- | --- |
| UART | TX, RX | 1 output or open-drain, 1 input |
| SPI | SCK, MOSI, MISO, CS | 3 outputs, 1 input |
| I2C | SDA, SCL | 2 open-drain (bidirectional pins) |
| JTAG | TCK, TMS, TDI, TDO | 3 outputs, 1 input |
| SWD | SWCLK, SWDIO | 1 output, 1 bidirectional |
| PS/2 | CLK, DATA | 2 open-drain |
| CAN | TXD, RXD | 1 output, 1 input |
| Low-speed USB | D+, D-, receive enable | 2 bidirectional, 1 output used internally |
| 10BASE-T | TD+, TD-, RX | 2 outputs, 1 input (comparator) |

13 protocol pins cover every protocol alone and most pairs.
Open-drain and turnaround protocols need the 8 bidirectional pins; input-only pins 8-9 suit receivers.
The USB receive enable wastes output pin 12 because G2 reads only the pin space; an internal engine-to-G2 signal would free it.

### Shared resources and conflicts

| Resource | Count | Users | Conflict |
| --- | --- | --- | --- |
| Engines | 2 | USB, CAN node, Ethernet, UART full duplex use both | Any of those excludes every other protocol |
| G2 | 1 | CAN receive, USB receive, Ethernet receive | Only one of these at a time |
| Instruction memory | 512 words, eight pages of 64 | All | A program over 64 words crosses pages with `set page`; programs sharing a page need a size check per page; the CAN node leaves one word |
| Clock profile | 1 board clock | USB 48 MHz, every other protocol 50 MHz | USB needs its own board clock |

Concurrent operation of all nine protocols is not a requirement.
Useful targets, to be measured before deciding on engines: a debug probe with a console (SWD or JTAG with UART receive), a sensor bridge (I2C with SPI), a CAN node with a sniffer (CAN with capture), and any single-engine protocol with capture of its own lines.
Only the first two fit two engines today.
The debug probe with UART receive uses 57 words for SWD or 11 for JTAG; I2C with SPI uses 35 words.
A CAN node with capture needs three engines and 135 words, more than either resource; UART full duplex with capture uses 20 words but also needs three engines.
These capacity checks do not establish concurrency: pin remapping, combined Host service and independent simultaneous peers must be tested for each target.

### Area and timing cost of the timed wait and count reads

Local synthesis of the timed wait and count reads, before the instruction memory grew, with yosys and ABC to the IHP SG13G2 typical liberty (an estimate of the CMOS5L flow, as in [the area log](area-log.md)): 332,357 µm² before and 332,568 µm² after (+211 µm², +0.06%); ABC's unrouted logic delay estimate is 5.84 ns before and 5.81 ns after.
The 128-word instruction memory adds about 97,000 µm² (+29%) on top of that ([below](#instruction-memory-and-storage)).
The routed cost of both must still be measured with a prerelease build (section 6, step 2).

The October 14 rerun reproduces 332,568.5958 µm² for flip-flop storage and measures 280,354.2840 µm² for latch storage with Yosys 0.69+post (`143eb14f`).
The latch variant saves 15.7% of total mapped cell area; this does not measure routing, clock trees, setup or hold.
The library file's SHA-256 is `7677a8918689f452e80405ad16a83e744709342574f2aedcc507c2758986b396`.
Reproduce with the same library:

```sh
mkdir -p output
.venv/bin/python tools/measure.py --liberty /path/to/sg13g2_stdcell_typ_1p20V_25C.lib > output/measure-report.json
```

The report includes source hashes, library hash, exact Yosys commands and both log paths.
Local detailed routing was unavailable because the Docker daemon was not running; no new physical operating limit is claimed.

## 5. Options and chosen design

### Clocks

| Protocol | 40 MHz | 48 MHz | 50 MHz | 60 MHz |
| --- | --- | --- | --- | --- |
| 10BASE-T transmit (50 ns half bits) | 2 cycles | 2.4 (no) | 2.5: the falling-edge output phase (current) | 3 cycles |
| 10BASE-T receive | G7, 8 samples per bit | No | G7, 10 samples per bit (current) | G7, 12 samples per bit |
| Low-speed USB (667 ns bits) | 26.7 (no) | 32 cycles | 33.3: 33 is +1.0% | 40 cycles |
| CAN 1 Mbit/s | 40 | 48 | 50 | 60 |
| UART, SPI, I2C, JTAG, SWD, PS/2 | Any, through CLKDIV | Any | Any | Any |

60 MHz is the lowest clock that is an integer multiple of both 20 MHz (Ethernet half-bit rate) and 1.5 MHz (USB bit rate).
Higher multiples also provide exact integer intervals; these are candidate profiles requiring changed programs and decoder tables, not verified common-clock support.
It needs setup closure at 16.7 ns, while the latest routed build misses 20 ns by 4.14 ns at the slow corner.
A fractional (phase-accumulator) tick divider would approximate other rates with one cycle of quantization jitter.
At 50 MHz that is 20 ns, which is a large fraction of an Ethernet 50 ns half bit.
Acceptability for any protocol must be established against its timing limits with absolute-time peers and a phase/jitter sweep; it is not assumed here.

Decision: keep 50 MHz as the physical target with 48 MHz (USB) and 50 MHz (everything else) program profiles; closing 50 MHz at all corners comes first, and 60 MHz is recorded as the common-clock option for when timing allows.
Ethernet moved from 40 to 50 MHz for B12: the finer sampling grid gives the receive margin, and the falling-edge output phase (`NEGOUT`) times the transmitter's 2.5-cycle half bits.
A fractional divider remains a candidate general timing primitive, to be prototyped and measured before adoption.
No PLL or clock multiplier is selected.

Switching the board clock between configurations preserves individual coverage only.
Safe procedure: disable the engines (`CTRL = 0`), hold `rst_n` low while the board changes the clock, release reset, then reload the program and configuration, since reset returns every register to its default and instruction memory is undefined after reset.

### Engines and the bit state machine

Two engines run every protocol alone, but USB, the CAN node and Ethernet each take both.
A third engine would allow UART full duplex beside another protocol, capture beside any two-engine protocol, and a CAN node with an engine for error signaling.
The area log estimates about 84,000 µm² per engine before placement.
Adding that to the latest routed 436,690 µm² gives about 58% of the placement area before further timing repair and routing costs; this does not establish that it fits or closes timing.
Decision: keep two engines in this change; build a three-engine variant as a measured experiment when the 8x4 template is confirmed.

The single G2 limits combinations, not protocols.
A second instance costs about 45,000 µm² (table and logic) and would allow USB or Ethernet beside a CAN receiver.
Decision: keep one; reconsider together with the engine count.

### The CAN acknowledgement (B8)

The implemented node needs no shared pin: one engine drives TXD for its own frames and for the acknowledgement, and the other times the bits.

- `can_line` (engine 1, 29 words) hard-synchronizes on the start of frame, resynchronizes on recessive-to-dominant edges other than its own, samples 36 cycles (72%) after the edge, and sets two flags per bit: "sampled" and "the next bit begins".
  It records every raw bus bit for the Host and owns G2.
- G2 (`tools/pe/g2.can_line`) destuffs each sample and emits the data bits into engine 0's ISR, where the checksum unit computes CRC-15.
  Its output 1 flags a stuff error, or a bit error when TXD is dominant and the bus recessive.
- `can_node` (engine 0, 99 words over both pages) drives TXD by side-set: the Host's transmit symbols, arbitration against the sampled bus bit, and the acknowledgement.
  While receiving it follows the fields by the ISR count and runs a handler per field with `mov pc, ~y`, derives the data length from DLC and RTR, reads the CRC residue with `in lfsrh, 16`, waits for the CRC delimiter (a stuff bit may come first) and drives the acknowledgement slot if the residue is 0.

The acknowledgement edge reaches the pad 2 cycles (40 ns) after the node's own transmit edges would.
Wiring two open-drain pins together and an on-chip wired-AND pin mode were considered for this and are not needed; the wired-AND mode remains a candidate for multi-engine I2C or 1-Wire.

### CAN node capacity and error signaling (B9)

B9 adds, beyond the node: error flags (6 dominant bits, or 6 recessive bits when error-passive) and the 8-bit error delimiter after any detected error; form checks of the CRC delimiter, the acknowledgement delimiter and the end of frame; the transmitter's acknowledgement error; transmit and receive error counters with their +1, +8 and -1 rules; error-passive at 128; bus-off above 255, and recovery after 128 occurrences of 11 recessive bits.
A sketch of this on the current instruction set needs roughly 50 to 70 more words, mostly for the counters, whose only arithmetic is a decrement.
No word is free: the node uses all 128.
The transmit underrun guard (B14) needs about 4 more words in the node's bit loop: before the `out pc` that would stall, test the OSR count (`jmp !osre`) and the queue level (`mov x, status`), and end the frame with TXD recessive instead.

| ID | Change | Current | Proposed |
| --- | --- | --- | --- |
| C1 | Counters on the Host | Nothing tracks errors | The node signals errors on chip and reports each in its record; the Host keeps the counters and sets a flag for error-passive or bus-off |
| C2 | Denser instructions | Decrement is the only arithmetic; RAM is indexed by Y; the ISR count stops at 16 | Add-immediate to X and Y, RAM access with an immediate index, a 6-bit ISR count |
| C3 | More instruction memory | 128 words, 2 pages | 256 words, 4 pages; `set page` and EXECCFG take a 2-bit page |
| C4 | Hardware bit timing | `can_line` times bits in software | A programmable bit timer: bit period, sample point, resynchronization window and jump width, with strobes as flags; it frees engine 1 |

Pros:

- C1: no RTL change; the counters are data, not signaling.
- C2: shrinks every protocol's arithmetic and counting, not only CAN; no memory growth.
- C3: programs, encodings and cycle counts stay as they are; the CAN node gets room for B9 and later protocols get room too; the area cost is known from the 64-to-128 step.
- C4: removes the most timing-critical loop from software and serves UART, PS/2 and low-speed USB as well; engine 1 becomes free for error signaling.

Cons:

- Tier 1 (permanent or tapeout risk):
  - C3: about 194,000 µm² more cell area in flip-flops (twice the measured 97,000 µm² per 64 words), which does not fit the 6x4 allocation; it needs the 8x4 template.
  - C4: a new timing block whose behavior every NRZ protocol would depend on; it must be specified, modeled, formally checked and routed before tapeout.
  - C2: new instruction formats change the decoder on the critical path; the latest routed build already misses 20 ns.
- Tier 2 (user-visible limits):
  - C1: error-passive and bus-off take effect only when the Host reacts, so the node may signal active errors too long; that breaks the standard's confinement rules unless the Host is fast and reliable.
  - C1: on its own it still needs about 20 words for error flags and delimiters, which do not exist.
  - C2: saves an estimated 10 to 20 words in the node, which is not enough alone.
  - C4: the CAN node still needs both engines and most of its 99 words.
- Tier 3 (cost and effort):
  - C2, C4: spec, model, assembler, random tests and formal checks change.
  - C3: `set page` and EXECCFG grow by one bit; assembler page checks generalize to four pages.

Recommendation: C3 once the 8x4 template is confirmed, because it keeps every verified program and timing unchanged and also covers growth beyond CAN; add C2 later only if measurements show the density gain is worth the decoder risk.
The area and allocation decision belongs to the project owner; B9 stays open until it is made.

Outcome (October 26): the owner chose C3 with 256 words, but the error-confinement node measured 447 instructions, far beyond the 50 to 70 words sketched above, because it also implements suspend transmission, overload frames, start of frame in intermission bit 3, bus integration and both flag kinds, with counters stored as 255 - C in buffer RAM so that an 8-bit `out ram` and `jmp x--` implement +8 and -1 without an adder.
Memory therefore grew to 512 words, in latches ([storage](#instruction-memory-and-storage)); this exceeds the C3 decision and needs the owner's confirmation.
The node keeps the field handlers at fixed page-0 addresses, uses one page per concern (frame, tail, intermission, flags, delimiters, counting routines, integration) and calls counting routines with a return address in RAM entry 4.
C1 was rejected because it violates confinement timing; C2 and C4 remain options for density and engine count.

### Ethernet receive jitter (B12)

The G2 Manchester decoder samples on both clock edges at 40 MHz (a 12.5 ns grid, using the falling-edge pin 15) and re-times on every transition.
Its theoretical jitter limit is about +/- 9.4 ns per transition, which matches the measured pass at +/- 9 ns and failure at +/- 10 ns; 802.3 asks a 10BASE-T receiver to tolerate +/- 13.5 ns.
A decoder that snaps to each transition cannot do better on this grid; reaching +/- 13.5 ns needs a reference averaged over several transitions, as receiver designs such as National Semiconductor's [AN-0783](https://bitsavers.mirrorservice.org/components/national/_appNotes/AN-0783.pdf) and [US patent 5317215](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/5317215) describe.

| ID | Change | Current | Proposed |
| --- | --- | --- | --- |
| E1 | Larger G2 | 16 states, 64-entry table | 32 states, 128-entry table for an averaging (digital PLL) decoder |
| E2 | Faster clock | 40 MHz Ethernet profile | 60 MHz profile with a new decoder table (8.3 ns grid) |

Pros:

- E1: a general gain: any table-driven decoder gets twice the states.
- E2: no new hardware; finer timing for every protocol.

Cons:

- Tier 1: E1 adds about 512 table flip-flops and a wider state path; E2 needs setup closure at 16.7 ns, while the latest routed build misses 20 ns by 4.14 ns.
- Tier 2: neither is proven to reach +/- 13.5 ns until a decoder is written and swept; with E2 a 60 MHz board clock replaces the 40 MHz one.
- Tier 3: E1 changes the spec, model, table builder and random tests.

Decision (October 24-26): neither E1 nor E2. A general clock-recovery unit (G7, `src/pe_cr.v`) replaced the G2 decoder's transition snapping: a phase accumulator whose phase is corrected, once per bit, by the in-window transition nearest its reference, with a gain that starts high for fast acquisition and settles to 2^-K, and a strobe into G2 at a programmable phase.
Its first version averaged every edge on a 12.5 ns grid at 40 MHz and lost frames when every transition sat at +13.5 or -13.5 ns.
Moving Ethernet to 50 MHz with double sampling (a 10 ns grid) and selecting the nearest transition gives 0 lost frames at the 802.3 limit in every jitter model, losses beginning at +/-14.5 ns ([protocols](protocols.md#ethernet-receive-margin)).
G7 is protocol-independent: it serves any self-clocked line code whose transitions carry the clock.

### Instruction memory and storage

Measured with yosys, mapped to the IHP `sg13g2` typical liberty (cell area before placement, whole chip):

| Store | Flip-flops (`PE_FLOP_STORE`) | Latches (default) |
| --- | --- | --- |
| 64 words | 332,569 µm² | 280,354 µm² |
| 128 words | 429,914 µm² (+29%) | 341,332 µm² |

The routed 64-word flip-flop build occupies 48% of the 6x4 allocation, so 128 flip-flop words project to roughly 62%.

| | Current | Proposed (implemented) |
| --- | --- | --- |
| Capacity | 64 words shared by both engines | 128 words in two pages of 64 |
| Addressing | 6-bit PC, jumps, wrap bounds, jump base and `IMEM_ADDR` | Same 6-bit fields within a page; EXECCFG bit 7 selects the engine's page; `IMEM_ADDR` is 7 bits |
| Storage | Flip-flops | Flip-flops |

Pros:

- The acknowledging CAN node (99 plus 29 words measured) fits, with `set page` letting its node program span both pages.
- Every existing program, encoding and cycle count is unchanged; programs and the assembler stay page-relative.
- Two programs no longer compete for one 64-word budget, which helps every concurrent combination.
- Flip-flops keep the verified, routed and gate-level-tested storage.

Cons:

- Significant: about 97,000 µm² more cell area (+29%), the largest single growth so far; routed utilization and timing must be confirmed by a prerelease build.
- Moderate: jumps between pages cost a `set page` word each, and wrap bounds are page addresses, so a program spanning pages must avoid the wrap address in the other page (the assembler checks both).
- Moderate: the CAN node alone fills the memory, leaving none for CAN error signaling (B9).
- Minor: after changing an engine's page the Host must force a `jmp`, because the instruction register still holds a word from the old page.
- Minor: the latch variant would save about 88,600 µm² at 128 words but remains physically unverified on CMOS5L.

Rejected alternatives: a wider PC and jump encoding changes every instruction format and the assembler for a gain that only a single program over 64 words would need; SRAM macros need a second read port or arbitration that changes fetch timing (below).

Decision: 128 flip-flop words in two pages, implemented in this change because the acknowledging CAN node (B8) cannot fit 64 words.
It rests on the unrouted estimate above, so it stays provisional: the routed prerelease (section 6, step 2) must confirm utilization and timing before the size is frozen, and the capacity question is reopened together with C3 if it does not fit.
Revisit latch storage only with a physical build and gate-level tests.

Outcome (October 26): four pages of flip-flops routed and closed timing at every corner (build 3 in the [area log](area-log.md)), but B9 needs eight pages, and six pages of flip-flops already failed clock-tree placement at 75-79% utilization (builds 4 and 5).
The store is now one latch per bit (`sg13cmos5l_dlhrq_1`, instantiated in synthesis, so none is inferred), about 40% of a flip-flop with its write multiplexer; eight pages place at 64%.
A write is registered, then opens one row during the low phase of the next cycle (spec H2), so a read sees the old word for the first half of that cycle and the new one at the following rising edge, as with flip-flops.
Each row's gate is a gated clock, which CTS buffers as its own clock tree; with double-spaced clock routing on those 3,594 nets global routing did not converge (build 6), so clock nets use default rules (`CTS_APPLY_NDR` none).
With default rules build 7 routed cleanly, but synthesis had merged the clock into the shared row decode, and clock-tree synthesis disconnected three row enables; each row now has an explicit clock-gating cell (`sg13cmos5l_lgcp_1`), so the clock reaches only those cells (build 8).
This decision reverses the "flip-flops until a physical build" rule above and the owner's C3 size; it is provisional until the routed build of this RTL closes setup and hold, including the latch checks, and gate-level tests pass.

The current store supports one Host write and two independent engine fetches, with same-cycle write forwarding.
A synchronous SRAM candidate must preserve one issued instruction per enabled tick, stalled and branch fetch behavior, and live-write visibility; otherwise every protocol's timing changes.
Replicated single-port macros need a coherent write policy; a two-port macro still needs an explicit allocation of the two reads and the Host write.
Also compare a separate decoder table, per-engine queue/RAM ports, macro dimensions, power routing and CMOS5L precheck support.
SRAM area alone does not settle these port and latency questions.

### Allocation

Keep 6x4 until the 8x4 template is confirmed; use 8x4 for measured gains (engines, G2, instruction memory) only.

## 6. Implementation order

1. Done in this change: timed wait, count reads, record framing, and the protocol work of section 2, rows B1-B7, with spec, model, assembler and tests.
2. Publish a prerelease of this source to measure its routed cost against `17995b3`, and continue slow-corner closure at 50 MHz (roadmap milestone 1).
3. Done: every peer keeps absolute time (B13), with a wrong-clock control per protocol.
4. Done: CAN acknowledgement (B8) by a node program pair across both pages, with no pin sharing; measured at 128 words.
5. Done: 128 instruction words in two pages, and G2 emission into either engine; then 256 words in four pages, then 512 words in eight pages of latches.
6. Done: USB keep-alive and reset (B10), PS/2 timeouts (B11), the CAN transmit underrun guard (B14) and CAN error confinement (B9).
7. Done in simulation: Ethernet receive margin (B12), with G7 at 50 MHz and a jitter sweep (`tools/eth_margin.py`).
8. With the 8x4 template: a three-engine and two-G2 experiment against the concurrency targets.
9. Board workflow and demonstrations (roadmap milestone 3), then freeze (milestone 4).

Run synthesis after each RTL step and a routed prerelease after each step that changes the RTL's structure.

## 7. Remaining acceptance tests and builds

The baseline is complete only when all of these pass:

- Every mandatory protocol has reliable native signaling for its declared roles and rates, with independent top-level peer tests; unfinished behavior remains tracked and cannot remove a mandatory protocol.
- Rows B9, B12 and B14 of section 2 keep passing their acceptance tests, as every done row must.
- Each program's wire timing is checked in absolute time at its clock profile.
- A routed build of the final source meets setup and hold at every intended corner for every clock profile the programs need (48 and 50 MHz today), including the latch store's timing checks, with fanout, slew and capacitance violations resolved or explained.
- The submitted storage variant passes its own physical build and gate-level tests.
- Board evidence for at least the base protocols where hardware is available.

## Verification notes

The new instructions are checked three ways: directed tests at the pins (`test_engine.test_timed_wait`, `test_timed_wait_flag_and_reserved`, `test_count_sources`), random programs compared every cycle with the reference model, whose generator now places short counts before timed waits so that they also time out, and every protocol regression above.
Negative controls confirmed that the checks can fail: the SPI peripheral rejects a CPHA 0 program in mode 1, a chip select released while the Host is late and a CS_N high time of one cycle; the CAN peer reports an acknowledgement held into the ACK delimiter; and the I2C target reports Standard-mode violations for the Fast-mode timing.
Two test bugs surfaced and were fixed along the way: the Ethernet driver started TP_IDL 250 ns late, which the old framing hid, and `Chip.start` started a second clock driver whenever a test reset the chip.

## Review completion and follow-up gates

The issue distinguishes completion of this review from completion of the native baseline.
The review records protocol coverage (section 1), blocking requirements and pin tests (section 2), timeout and record interfaces (section 3), resource and Host measurements (section 4), provisional design choices and experiments (section 5), implementation order (section 6), and outstanding acceptance evidence (section 7).
The selected implementation is two engines, one G2, one G7, 512 latch instruction words in eight pages, the 8x4 allocation and separate 48 and 50 MHz clock profiles; none is a final capacity or operating guarantee.
Final engine count, common-clock support and storage technology remain gated by fuller CAN programs, simultaneous peer tests and source-linked physical builds.
Do not freeze the architecture or mark the baseline complete while those gates remain open, in particular routed slow-corner timing of the current RTL and its gate-level tests.
