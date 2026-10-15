# Design review: native baseline and future programmability

This review answers [issue #14](https://github.com/JediNakDev/protocal-emulator-asic/issues/14).
It checks the architecture against the [non-negotiable native protocol baseline](../AGENTS.md#non-negotiable-protocol-baseline): UART, SPI, I2C, JTAG, SWD, PS/2, CAN, low-speed USB and 10BASE-T Ethernet, plus programs uploaded for protocols nobody planned.
It records the decisions taken, the evidence behind them, and the work that remains.
The baseline is not complete: section 7 lists the protocol and physical evidence still required.

The review was carried out on October 13-14, 2026, on top of `9ae3776`.
Measurements of program size, Host throughput and protocol tolerances come from the RTL and tests of this change; the synthesis estimate is local and unrouted.

## Summary of decisions

| Topic | Decision | Status |
| --- | --- | --- |
| Wait with a deadline | Timed `wait` with X as the count of checks; X is 0 afterwards only on timeout | Implemented ([spec](spec.md#timed-wait)) |
| Record lengths and boundaries | `mov x, isrcnt` reads the ISR count; receivers close messages with count-terminated records | Implemented ([framing](protocols.md#record-framing)) |
| Protocol gaps closed with them | CAN receive resynchronization, USB reply deadline, Ethernet frame boundaries and link pulses, SWD WAIT/FAULT, SPI modes 1-3, I2C reads at 100 and 400 kHz | Implemented with pin-level regressions |
| Engines | Keep two for now; evaluate a third with the 8x4 allocation | Provisional |
| Bit state machine (G2) | Keep one shared instance; it limits concurrency, not individual protocols | Provisional |
| Clock | Keep 50 MHz as the physical target and 40/48/50 MHz program profiles; 60 MHz is the lowest common clock with exact integer bit intervals | Provisional, evaluation recorded |
| Instruction memory | 64 words fit the current demonstrations; measure a complete CAN node before choosing capacity | Open |
| Storage | Choose flip-flop, latch or SRAM from physical builds; no change yet | Open |
| Allocation | Stay at 6x4 until the 8x4 template is available | Provisional |

## 1. Protocol status

Each row lists what is implemented and verified at the top-level pins today, and what the baseline still needs.
"Verified" means RTL simulation against an independent peer; no row has board evidence yet.

| Protocol | Roles implemented | Rates verified | Evidence | Still missing |
| --- | --- | --- | --- | --- |
| UART | 8N1 transmitter and receiver | 16 cycles per bit (3.125 Mbaud at 50 MHz); any rate `f / (8 (CLKDIV + 1))` | `test_uart_tx`, `test_uart_rx` | Standard baud rates checked in absolute time, receiver tolerance to baud offset, other frame formats, break and flow control |
| SPI | Controller, modes 0-3 | SCK 6.25 MHz, and 12.5 MHz in modes 0 and 2 with MISO bypass | `test_spi_controller_mode0`-`mode3`, `test_spi_controller_12mhz_bypass` | Peripheral role; chip select by the program |
| I2C | Controller: write, read, repeated START, stretching | 100 kHz and 400 kHz, UM10204 minimums checked in ns | `test_i2c_controller_100khz`, `_400khz`, `_400khz_no_stretch` | Target role; multi-controller arbitration; bus recovery; SMBus clock-low timeout |
| JTAG | Controller | TCK 6.25 MHz | `test_jtag_idcode_ir_dr_bypass` | Other TCK rates in absolute time; TRST and system reset pins |
| SWD | Host: transactions with on-chip ACK check, line reset, sequences | SWCLK 6.25 MHz | `test_swd_read_write`, `test_swd_bad_request_and_recovery`, `test_swd_wait_and_fault` | ORUNDETECT = 1; dormant-state selection sequences tested; target role |
| PS/2 | Device and host | 10-16.7 kHz clock | `test_ps2_chip_as_keyboard`, `test_ps2_chip_as_host` | Timeouts for a silent peer; real-device evidence |
| CAN 2.0A | Transmitter with arbitration; listen-only receiver with resynchronization | 1 Mbit/s; receive tolerance 2.2% fast to 3.5% slow measured | `test_can_*` | Acknowledging received frames; error frames, counters and bus-off; overload frames; extended identifiers in the Host parser |
| Low-speed USB | Host, bit level, with a 17-bit-time reply deadline | 1.5 Mbit/s at 48 MHz; device clock +/- 1.5% | `test_usb_*` | Keep-alive and bus reset on chip; device role; operation at 50 MHz |
| 10BASE-T | Transmitter with link pulses; receiver with exact framing | 10 Mbit/s at 40 MHz; receive jitter +/- 9 ns | `test_eth_*` | Receive jitter margin per 802.3; link integrity state; collision handling; full duplex at line rate |

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
| B5 | SWD sent the data phase after WAIT or FAULT, so a write's data could read as a request and the target could drive SWDIO against the host | Skip the data phase after any ACK other than OK and keep the command stream aligned | WAIT on a write whose data is a valid request byte, FAULT on a read, then successful retries; no contention | Done: `test_swd_wait_and_fault` |
| B6 | SPI had mode 0 only | Modes 0-3 with SCK idling at CPOL | Each mode against a mode-parametric peripheral | Done: `test_spi_controller_mode0`-`mode3` |
| B7 | I2C had writes only and no speed evidence | Reads with ACK/NACK, repeated START, stretching, at 100 and 400 kHz within UM10204 timing | Write, pointer write plus repeated-START read, NACK; timing checked in ns | Done: `test_i2c_controller_*` |
| B8 | The chip does not acknowledge received CAN frames | A correctly received frame gets a dominant ACK slot from the chip, with no external protocol controller | A node transmitting alone on the bus sees its frame acknowledged by the chip; a frame with a bad CRC is not acknowledged | Open (section 6, step 4) |
| B9 | No CAN error signaling | Error frames on stuff, form, CRC and bit errors; error counters, error-passive and bus-off | A node injecting each error type; recovery after bus-off | Open |
| B10 | No USB keep-alive or bus reset | Keep-alive within each 1 ms while idle; a reset SE0 of at least 10 ms on request | Device model that suspends after 3 ms idle; reset length checked in absolute time | Done: `test_usb_keep_alive`, `test_usb_bus_reset` |
| B11 | PS/2 waits forever for a silent peer | Bounded waits with a reported failure | Host program against an absent device; device program against an inhibiting host | Done: `test_ps2_host_absent_device`, `test_ps2_host_device_stops`, `test_ps2_device_inhibited` |
| B12 | Ethernet receive jitter margin below 802.3 | Decode with the jitter 802.3 allows at the receiver | Jitter sweep to the standard's limit, at all input phases | Open; needs a decoder or clock change (section 5) |
| B13 | Peers run in cycles, so a chip clock change scales both sides | Peers keep their wire rates in absolute time | Each program at its clock profile, checked in ns, and failing when the chip clock is wrong | Done: every protocol test module has a `*_wrong_clock` control |

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
| `can_tx` + `can_rx` | 19 + 21 | 2 | G2 |
| `ps2_host` / `ps2_device` | 25 / 22 | 1 | |
| `i2c` | 24 | 1 | |
| `eth10_tx` + `eth10_rx` | 21 + 7 | 2 | G2, falling-edge sample |
| `usb_ls_host` + `usb_ls_rx` | 18 + 5 | 2 | G2, output pin 12 |
| `uart_tx` + `uart_rx` | 4 + 9 | 2 | |
| `capture` + `replay` | 7 + 3 | 1 each | Edge capture, one flag |
| `spi_cpha0` / `spi_cpha1` | 2 / 3 | 1 | |
| `jtag` | 2 | 1 | |

Every program fits the 64 shared words alone, and SWD with a UART receiver fits (57).
Robust behavior costs words: SWD grew from 13 to 48 to check ACKs on chip, and the CAN receiver from 16 to 21 for resynchronization.
A complete CAN node adds DLC tracking, a CRC verdict, ACK and error signaling to the 40 words of transmit plus receive.
Its final size has not been measured; neither sufficiency nor insufficiency of 64 words is established.
Instruction capacity remains an open evaluation (section 5).

Reproduce every size from the actual uploaded source with `python tools/measure.py`.
The JSON includes the base commit and SHA-256 of the source files, so uncommitted measurements can be distinguished from the base commit.

### Buffers and Host throughput

[Measured through the pins](protocols.md#host-throughput): the receive path sustains one word per 44 cycles (18.2 Mbit/s at 50 MHz) with a poll-then-burst Host at `f_clk / 8`, and an 8-word queue covers 8 production periods minus about 330 cycles of reading.
Ethernet receive loads that path to 73% at 40 MHz and tolerates about 4 µs of Host pause; CAN and USB load it below 10%.
Ethernet full duplex at line rate exceeds the Host port at 40 MHz.
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
| Instruction memory | 64 words | All | Every concurrent combination needs a size check; fuller CAN behavior is unmeasured |
| Clock profile | 1 board clock | Ethernet 40 MHz, USB 48 MHz, CAN 50 MHz | The current Ethernet and USB programs require different clocks |

Concurrent operation of all nine protocols is not a requirement.
Useful targets, to be measured before deciding on engines: a debug probe with a console (SWD or JTAG with UART receive), a sensor bridge (I2C with SPI), a CAN node with a sniffer (CAN with capture), and any single-engine protocol with capture of its own lines.
Only the first two fit two engines today.
The debug probe with UART receive uses 57 words for SWD or 11 for JTAG; I2C with SPI uses 26 or 27 words.
CAN transmit/receive with capture uses 47 words but needs three engines; UART full duplex with capture uses 20 words and also needs three.
These capacity checks do not establish concurrency: pin remapping, combined Host service and independent simultaneous peers must be tested for each target.

### Area and timing cost of this change

Local synthesis with yosys and ABC to the IHP SG13G2 typical liberty (an estimate of the CMOS5L flow, as in [the area log](area-log.md)): 332,357 µm² before and 332,568 µm² after (+211 µm², +0.06%); ABC's unrouted logic delay estimate is 5.84 ns before and 5.81 ns after.
The routed cost must still be measured with a prerelease build (section 6, step 2).

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
| 10BASE-T transmit (50 ns half bits) | 2 cycles | 2.4 (no) | 2.5 (no) | 3 cycles |
| 10BASE-T receive | Current decoder | No | No | New decoder table needed (6 cycles per bit) |
| Low-speed USB (667 ns bits) | 26.7 (no) | 32 cycles | 33.3: 33 is +1.0% | 40 cycles |
| CAN 1 Mbit/s | 40 | 48 | 50 | 60 |
| UART, SPI, I2C, JTAG, SWD, PS/2 | Any, through CLKDIV | Any | Any | Any |

60 MHz is the lowest clock that is an integer multiple of both 20 MHz (Ethernet half-bit rate) and 1.5 MHz (USB bit rate).
Higher multiples also provide exact integer intervals; these are candidate profiles requiring changed programs and decoder tables, not verified common-clock support.
It needs setup closure at 16.7 ns, while the latest routed build misses 20 ns by 4.14 ns at the slow corner.
A fractional (phase-accumulator) tick divider would approximate other rates with one cycle of quantization jitter.
At 50 MHz that is 20 ns, which is a large fraction of an Ethernet 50 ns half bit.
Acceptability for any protocol must be established against its timing limits with absolute-time peers and a phase/jitter sweep; it is not assumed here.

Decision: keep 50 MHz as the physical target and the 40/48/50 MHz program profiles; closing 50 MHz at all corners comes first, and 60 MHz is recorded as the common-clock option for when timing allows.
A fractional divider remains a candidate general timing primitive, to be prototyped and measured before adoption.
No PLL or clock multiplier is selected.

Switching the board clock between configurations preserves individual coverage only.
Safe procedure: disable the engines (`CTRL = 0`), hold `rst_n` low while the board changes the clock, release reset, then reload the program and configuration, since reset returns every register to its default and instruction memory is undefined after reset.

### Engines and the bit state machine

Two engines run every protocol alone, but USB, a CAN node and Ethernet each take both, and the receiving CAN node needs to drive TXD, which belongs to the transmitting engine.
A third engine would allow a CAN node with a separate acknowledging engine, UART full duplex beside another protocol, and capture beside any two-engine protocol.
The area log estimates about 84,000 µm² per engine before placement.
Adding that to the latest routed 436,690 µm² gives about 58% of the placement area before further timing repair and routing costs; this does not establish that it fits or closes timing.
Decision: keep two engines in this change; build a three-engine variant as a measured experiment when the 8x4 template is confirmed.

The single G2 limits combinations, not protocols.
A second instance costs about 45,000 µm² (table and logic) and would allow USB or Ethernet beside a CAN receiver.
Decision: keep one; reconsider together with the engine count.

### Pin access and the CAN acknowledgement

Two ways meet the "no external protocol controller" rule:

| Option | Cost | Benefit |
| --- | --- | --- |
| Two open-drain pins tied together with a pull-up to the transceiver's TXD | One more bidirectional pin, a resistor; no RTL change | Both engines can pull TXD dominant; the receiving engine drives the ACK slot |
| On-chip shared open-drain ownership: a pin pulled low when any engine drives it low | A pin-sharing register and a wired-AND in the pin block | Saves a pin; also serves multi-engine I2C, 1-Wire and other wired-AND buses |

The receiving program must also find the end of the CRC from the DLC and know the CRC verdict before the ACK slot.
The checksum primitive can compute CRC-15 when configured to consume the destuffed bits, but no branch directly tests its value.
A program must read `lfsrl` through `in`, move the result into a scratch register and test it; the read modifies the ISR and must not race a G2 emission or destroy pending payload bits.
DLC tracking, exact final-bit timing, checksum configuration and this read sequence must all be demonstrated before a native ACK is claimed.
Decision: prototype the acknowledging receiver with the two-pin wiring first, measure its program size, then decide whether on-chip pin sharing and more instruction memory are justified.

### Instruction memory and storage

| Option | For | Against |
| --- | --- | --- |
| 64 words of flip-flops (current) | Verified, routed, simple | 69,500 µm² plus read multiplexers in the earlier estimate; complete CAN capacity is unmeasured |
| Latch storage (`PE_LATCH_STORE`) | Lower storage area; current total mapped area is 280,354 µm² | Not yet physically verified on CMOS5L; latch gating, duty cycle and falling-edge paths need checks |
| IHP SRAM macros | Densest for larger sizes | Synchronous read; two engines read every cycle, so a second port or arbitration; macro placement, Metal4 power, precheck |

Decision: none yet.
Build and gate-level-test the latch variant, and compare a larger instruction store with available SRAM candidates; choose from routed timing, area and gate-level results, not estimates.
Doubling instruction words also needs an addressing design: PC, jump targets, wrap bounds, indexed-jump base and Host instruction addresses are currently six bits.
Simply increasing the storage depth would leave half of it unreachable.
Evaluate banking versus wider instruction/address encodings, including relocation, Host loading, fetch snooping and cycle behavior, before selecting either.

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
3. Convert the remaining peers to absolute time (B13) and run each program at its clock profile, so later clock or timing changes are caught.
4. CAN acknowledgement (B8): two-pin wired-AND prototype, DLC tracking and CRC verdict; measure program size.
5. Decide instruction capacity and storage with physical builds of the 64-word latch variant and defined larger-store/SRAM candidates.
6. CAN error signaling (B9), USB keep-alive and reset (B10), PS/2 timeouts (B11).
7. Ethernet receive margin (B12): a decoder table or clock-profile change, measured with a jitter sweep.
8. With the 8x4 template: a three-engine and two-G2 experiment against the concurrency targets.
9. Board workflow and demonstrations (roadmap milestone 3), then freeze (milestone 4).

Run synthesis after each RTL step and a routed prerelease after each step that changes the RTL's structure.

## 7. Remaining acceptance tests and builds

The baseline is complete only when all of these pass:

- Every mandatory protocol has reliable native signaling for its declared roles and rates, with independent top-level peer tests; unfinished behavior remains tracked and cannot remove a mandatory protocol.
- Open rows B8-B13 of section 2 pass their acceptance tests.
- Each program's wire timing is checked in absolute time at its clock profile.
- A routed build of the final source meets setup and hold at every intended corner for every clock profile the programs need (40, 48 and 50 MHz today), with fanout, slew and capacitance violations resolved or explained.
- The submitted storage variant passes its own physical build and gate-level tests.
- Board evidence for at least the base protocols where hardware is available.

## Verification notes

The new instructions are checked three ways: directed tests at the pins (`test_engine.test_timed_wait`, `test_timed_wait_flag_and_reserved`, `test_count_sources`), random programs compared every cycle with the reference model, whose generator now places short counts before timed waits so that they also time out, and every protocol regression above.
Negative controls confirmed that the checks can fail: the SPI peripheral rejects a CPHA 0 program in mode 1, and the I2C target reports Standard-mode violations for the Fast-mode timing.
Two test bugs surfaced and were fixed along the way: the Ethernet driver started TP_IDL 250 ns late, which the old framing hid, and `Chip.start` started a second clock driver whenever a test reset the chip.

## Review completion and follow-up gates

The issue distinguishes completion of this review from completion of the native baseline.
The review records protocol coverage (section 1), blocking requirements and pin tests (section 2), timeout and record interfaces (section 3), resource and Host measurements (section 4), provisional design choices and experiments (section 5), implementation order (section 6), and outstanding acceptance evidence (section 7).
The selected implementation remains two engines, one G2, 64 flip-flop instruction words, 6x4 allocation and separate clock profiles; none is a final capacity or operating guarantee.
Final engine count, common-clock support and storage technology remain gated by fuller CAN programs, simultaneous peer tests and source-linked physical builds.
Do not freeze the architecture or mark the baseline complete while those gates or B8-B13 remain open.
