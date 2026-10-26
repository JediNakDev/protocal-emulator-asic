# Protocol programs

Each protocol runs a program from `programs/`.
Host-side framing and transaction helpers for SPI, I2C, JTAG, SWD, PS/2, CAN, USB and Ethernet live in `tools/pe/proto/`.
Every one is verified in RTL simulation by a cocotb test (`test/test_<protocol>.py`) against an independent peer model that shares no code with the program or the host-side software.
UART, SPI and I2C are verified in `test/test_protocols.py`, which loads the same standalone program files used for upload.

The base protocol sources are `uart_tx.pasm`, `uart_rx.pasm`, `spi_cpha0.pasm`, `spi_cpha0_fast.pasm`, `spi_cpha1.pasm` and `i2c.pasm` in `programs/`.
Their comments describe the pin configuration, data packing and timing.

All results below are from RTL simulation.
They do not cover pad timing, metastability, clock duty cycle or analog front ends.
[The design review](design-review.md) records which roles and speeds are still missing for each protocol of the mandatory baseline.

## Summary

| Protocol | Role | Program words | Hardware used | Clock | Verified |
| --- | --- | --- | --- | --- | --- |
| UART 8N1 | Transmitter, receiver | 4, 9 | Side-set, `jmp pin`, flags | 50 MHz, 16 cycles per bit | Bytes to and from independent UART peers; a low stop bit sets flag 4 and drops the byte |
| SPI | Controller, modes 0-3, with chip select | 11 | 2-pin side-set (SCK, CS_N), pin invert for CPOL 1, autopush | 50 MHz, SCK 6.25 MHz; 12.5 MHz in modes 0 and 2 with MISO bypass | Full-duplex transactions of 1 to 6 bytes, back to back and with a 20 µs Host pause inside one, against a mode-parametric peripheral that answers only while selected; SCK idles at CPOL; CS_N setup, hold and high time and MOSI setup and hold checked in absolute time |
| I2C | Controller | 24 | Open-drain, side-set clock, `wait` for stretching | 50 MHz, 100 kHz and 400 kHz | Writes, reads with repeated START, NACK of a wrong address and of the last read byte, target clock stretching; every UM10204 timing minimum checked in absolute time |
| JTAG | Controller | 2 | Side-set, autopull/autopush | 50 MHz, TCK 6.25 MHz | IDCODE, IR capture, DR read/write, BYPASS against an IEEE 1149.1 TAP model |
| SWD | Host (probe) | 48 | Side-set, SWDIO turnaround, on-chip ACK check | 50 MHz, SWCLK 6.25 MHz | IDCODE read, CTRL/STAT write and read-back, SELECT write; WAIT from a busy AP and FAULT from STICKYERR skip the data phase, and ABORT clears STICKYERR; bad-parity request recovery; no SWDIO contention |
| PS/2 | Device (keyboard) | 34 | Open-drain, `status`, side-set clock, timed wait | 50 MHz | Scan codes to a host model; host command with request-to-send and acknowledge; an inhibited bus defers a frame, and an inhibit mid-frame aborts it with 0xFFFF reported |
| PS/2 | Host | 42 | Open-drain, inhibit, `status`, timed wait | 50 MHz | Device frames; reset command acknowledged, 0xFA and 0xAA received; an absent device or one that stops clocking is reported with 0xFFFF and the lines released |
| CAN 2.0A | Receiver (listen-only) | 21 | G2 destuffer, emit into ISR, timed wait, `isrcnt` | 50 MHz, 1 Mbit/s | Stuff-heavy and 8-byte frames; transmitter 1.5% fast and 1.5% slow, with the longest stretches between resynchronizing edges |
| CAN 2.0A and 2.0B | Node with error confinement: transmitter with arbitration, receiver that checks the CRC and acknowledges | 472 + 39 | G2 destuffer with bit-error check, emitting into the other engine; CRC (G1); field handlers by `mov pc`; `set page` across eight pages; error counters in buffer RAM; timed wait | 50 MHz, 1 Mbit/s | Standard, extended and remote frames from a node alone on the bus acknowledged by the chip, including frames with a stuff bit before the CRC delimiter, from a transmitter exact, 1.5% fast and 1.5% slow, also after ten bits without an edge; frames sent by the chip and acknowledged by a node exact, 1.5% fast and 1.5% slow, and after a 14 µs Host pause; lost and won arbitration; ISO 11898-1 error confinement: active error flags for stuff, form, CRC, ACK and bit errors, passive error flags, REC and TEC by rules 1-12 including exceptions 3a and 3b (rules 4 and 5 with the bus held recessive under the chip's flag), error passive with suspend transmission, bus-off with recovery after 128 occurrences of 11 recessive bits, overload frames for intermission bit 1, end of frame bit 7 and error delimiter bit 8, a start of frame in intermission bit 3, transmit underrun (B14), and bus integration when started inside a frame |
| Low-speed USB | Host | 37 + 5 | G2 clock-recovering NRZI receiver, pattern wait, timed wait, both engines | 48 MHz, 1.5 Mbit/s | GET_DESCRIPTOR (SETUP, DATA0, ACK, IN, DATA1, ACK) with device clocks exact, 1.5% fast and 1.5% slow; a missing reply ends at a 17-bit-time deadline and the next command runs; keep-alives at least every 1 ms while idle; a 50 ms bus reset on request |
| 10BASE-T | Transmitter | 30 | G8 indexed jump, 3-pin side-set, falling-edge output phase (`NEGOUT`) for the half-cycle mid-bit transitions, `status` | 50 MHz, 10 Mbit/s | 89-byte frame, TD- complementary, every half bit 50 ns, TP_IDL, FCS; normal link pulses every 13.1 ms while idle |
| 10BASE-T | Receiver | 7 | G7 clock recovery with double sampling (pin 15, falling edge), G2 strobe and emit into ISR, `isrcnt` | 50 MHz, 10 Mbit/s | The 802.3 receive jitter limit, +/-13.5 ns: uniform, composite (5.5 ns pattern-dependent) and every transition at an extreme, at several input phases and +/-200 ppm; a maximum-length frame; back-to-back frames at the minimum interframe gap and link pulses, separated exactly whenever the Host reads them; [margin](#ethernet-receive-margin) |
| Any (waveform) | Capture and replay | 7 + 3 | Edge capture (G3), flag wait, autopush/autopull | 50 MHz, 1-cycle resolution | Random edges 4 to 100,003 cycles apart, captured and replayed with every interval exact; a UART byte edited on the Host and decoded by an independent receiver |

The CAN node, `can_node` with `can_line`, occupies 511 of 512 instruction words (472 + 39), including 25 words of padding for its fixed field-handler addresses and page boundaries. It uses both engines, so no other program runs beside it; the largest single-page program, SWD, uses 48.
The Host starts it with flags clear and buffer RAM entry 4 at 0 (the node at `bol`, the line at `start`); from then on the chip integrates into the bus and does all signaling, error handling and confinement itself.

## Record framing

Receivers whose messages have no fixed length close each message with a count-terminated record, so the Host finds every boundary in the word stream whenever it reads it.
`can_rx`, `can_line`, `usb_ls_rx` and `eth10_rx` use it, and `tools/pe/proto/records.py` splits the stream.

- The program runs with autopush at 15 and `in` shifting left, so each data word carries 15 bits, the first in bit 14, and bit 15 is always clear.
- At the end of a message the program reads the number of bits in the partial last word with `mov x, isrcnt`, pushes that word (0-14 bits in its low bits, possibly none), and pushes the trailer `0x8000 | count`:

  ```
  mov x, isrcnt
  push
  mov isr, ~null
  in x, 15        ; autopushed at the threshold of 15
  ```

- A program that must never stall on a full receive queue, such as `can_line`, pushes with `push iffull noblock` before each bit instead of autopush, so its last word may hold all 15 bits and its trailer count may be 15.

Payload words never have bit 15 set, so a trailer cannot be mistaken for data, whatever the payload, and the record keeps its exact length in bits.
The framing costs 1 bit in 16 of receive bandwidth and 4 instructions.

## Base protocols

| Program | Configuration | Data |
| --- | --- | --- |
| `uart_tx` | OUTPIN and optional side-set = TX, push-pull, `out` shifting right, CLKDIV sets 8 ticks per bit | One byte per transmit word in bits 7:0 |
| `uart_rx` | INPIN base and jmp pin = RX, `in` shifting right | Received bytes in bits 15:8 |
| `spi_cpha0`, `spi_cpha0_fast` | MOSI = OUTPIN, MISO = INPIN, side-set = SCK and CS_N = SCK + 1, push-pull; autopush at 8, both shifting left; PINCFG invert on SCK for mode 2 | One transmit word per byte in bits 15:8, with bit 7 set when another byte of the same transaction follows (`tools/pe/proto/spi.py`); received bytes in bits 7:0 |
| `spi_cpha1` | As `spi_cpha0`; PINCFG invert on SCK for mode 3 | As `spi_cpha0` |
| `i2c` | SDA = OUTPIN/SETPIN/INPIN base, SCL = SDA + 1 and side-set, both open-drain; CLKDIV 19 for 100 kHz or 4 for 400 kHz at 50 MHz | One transmit word per byte with START, the nine SDA bits and STOP (`tools/pe/proto/i2c.py`); one receive word per byte with the nine sampled bits |

UART bit rates follow from CLKDIV: 8 ticks per bit gives `f_clk / (8 (CLKDIV + 1))`, for example 115,741 baud (+0.47%) with CLKDIV 53 at 50 MHz.
The SPI programs drive CS_N themselves: it falls before the first byte of a transaction, stays low while bit 7 announces another byte, and rises after the last, staying high at least 8 cycles.
A slow Host only stretches the idle SCK between bytes, with CS_N held low, which SPI allows.
The I2C program holds SCL low between bytes while its transmit queue is empty, which UM10204 allows a controller to do; it releases both lines after STOP.
I2C timing in ticks: SCL low 14 with SDA changing 5 ticks after the falling edge, SCL high 11 ticks after the line is seen high, START hold 11, repeated START setup 13, STOP setup 11, bus free 16.
At 100 kHz that is a 5.6 µs low and 4.4 µs high phase; at 400 kHz 1.4 µs and 1.1 µs.

## Waveform capture and replay

`capture.pasm` records the time of every edge on one pin, and `replay.pasm` plays a recording back on another pin, both exact to the cycle and without knowing the protocol.
The edge capture unit (G3) timestamps each edge with the 32-bit cycle counter, so gaps up to 86 s at 50 MHz keep their exact length.
`tools/pe/proto/waveform.py` converts between the two formats and edits recordings: `invert_span` drives any stretch of time to the opposite level.
Capture requires receive-only buffering, autopush disabled, and an 8-cycle input filter.
Nonblocking pushes keep timestamp reads from stalling when the Host falls behind; dropped words set receive overflow.
`test/test_waveform.py` uses it to flip one data bit of a captured UART byte and checks that an independent UART receiver decodes the edited byte from the replay.

This turns the chip into a recorder and signal generator for reverse engineering and fault injection on protocols it has no program for: record an unknown exchange, study it on the Host, then replay it, or a deliberately damaged version of it, to the device under test.

| Property | Value |
| --- | --- |
| Resolution | 1 cycle (20 ns at 50 MHz) for both capture and replay |
| Shortest pulse | 8 cycles, enforced by the 8-cycle glitch filter on the captured pin; replay holds each level at least 3 cycles |
| Burst | 4 edges at the shortest spacing fill the 8-word receive queue after the Host drains the initial-level word |
| Sustained capture | One edge per 80 cycles (1.6 µs at 50 MHz) with a continuous receive-port read at `HSCK=f_clk/10`; polling adds transaction overhead and needs slower edges |
| Lost data | Overwritten edges set capture overrun; dropped timestamp words set receive overflow |
| Replay | Every word holds 3 to 32,770 cycles; the Host must keep the 8-word transmit queue from running dry |

Discard a recording if either sticky bit is set; lost data may leave timestamps incomplete or repeated.

## Host throughput

Buffers absorb pauses in Host service; only the Host port's sustained rate keeps up with a stream.
`test_host.test_host_receive_throughput` measures both through the top-level pins, with the test Host polling `LEVELS` and then reading every waiting word in one burst, in 4-bit mode at the fastest `HSCK` (`f_clk / 8`).

| Measure | Result |
| --- | --- |
| Sustained receive rate | One 16-bit word every 44 cycles without loss (18.2 Mbit/s at 50 MHz); every 42 cycles loses words |
| Burst absorption | At one word per 100 cycles the 8-word queue absorbs a 400-cycle pause after each burst, not 600: its 8 words cover 800 cycles, and reading them takes about 330 |
| Simultaneous TX and RX | `test_host.test_host_duplex_throughput` sustains one word each way every 128 cycles for 300 words with the 4+4 queue split (6.25 Mbit/s each way at 50 MHz); every 64 cycles overloads this schedule |

A Host with slower transactions, or one that also writes the transmit queue, sustains less.
The duplex test includes polling and both burst directions, with a pin peer checking output bytes, timestamp gaps checking receive loss, and sticky overflow checks.
Its sustainable profile is a demonstrated lower bound, not the maximum; protocol commands and record trailers cost more bandwidth.
Requirements of the stream programs follow from these numbers:

| Program | Stream | Load on the receive path | Pause the queue absorbs |
| --- | --- | --- | --- |
| `eth10_rx` at 50 MHz | 10 Mbit/s, 15 bits per word: a word every 75 cycles | 59% | About 270 cycles (5 µs) |
| `can_rx` or `can_line` at 50 MHz | 1 Mbit/s: a word every 750 cycles | 6% | About 5,700 cycles (115 µs) |
| `usb_ls_rx` at 48 MHz | 1.5 Mbit/s: a word every 480 cycles | 9% | About 3,500 cycles (73 µs) |

Ethernet in both directions at line rate, 10 Mbit/s each way, exceeds the demonstrated duplex rate of the Host port; half duplex works.

## Ethernet receive margin

IEEE 802.3 clause 14 requires a 10BASE-T receiver to accept transitions displaced by up to +/-13.5 ns, at up to 100 ppm frequency error at each end.
`tools/eth_margin.py` drives the model of G7 (`pe.model.ClockRecovery`, which `test_random` checks against the RTL every cycle) with the `eth10_rx` settings at 50 MHz, a random input phase, and 0 or +/-200 ppm, and counts frames that do not arrive as one record with every bit:

| Jitter on every transition | Frame | Frames | Lost |
| --- | --- | --- | --- |
| Uniform, +/-13.5 ns | 188 bytes | 3000 | 0 |
| 5.5 ns pattern-dependent plus uniform +/-8 ns | 188 bytes | 3000 | 0 |
| +13.5 or -13.5 ns at random | 72 bytes | 3000 | 0 |
| +13.5 or -13.5 ns at random | 188 bytes | 1500 | 0 |
| +13.5 or -13.5 ns at random | 1518 bytes | 150 | 0 |
| +14 or -14 ns at random | 188 bytes | 1500 | 0 |
| +14.5 or -14.5 ns at random | 188 bytes | 1500 | 5 |
| +15 or -15 ns at random | 188 bytes | 1500 | 654 |

The margin over the standard's worst case is therefore about 0.5 ns, set by the sampling: a 50 ns half bit, less both transitions' jitter and the 10 ns between samples.
`test/test_eth.py` checks the 13.5 ns cases through the chip's pins.

## Limits found during verification

These came out of writing and testing the programs; each is a property of the current design, not of the test.
Each is unfinished work toward the [mandatory baseline](../AGENTS.md#non-negotiable-protocol-baseline); [the design review](design-review.md#2-blocking-limitations) gives the required behavior and acceptance test for each.

| Limit | Protocols | Cause | Effect |
| --- | --- | --- | --- |
| `can_rx` and `can_line` resynchronize without a synchronization jump width limit | CAN | Every recessive-to-dominant edge in the window re-times the sample point (`can_line`: an edge 3-9 or 14-32 cycles after the sample, except one caused by the node's own TXD) | Tolerates a transmitter 2.2% fast to 3.5% slow in simulation, beyond CAN's 1.58%; a glitch inside the window can shift one bit's sample point. |
| Ethernet full duplex at line rate exceeds the Host link | 10BASE-T | 10 Mbit/s each way needs more than the 6.25 Mbit/s each way that `test_host_duplex_throughput` demonstrates | Half duplex works. Collision detection is possible because transmit and receive use different engines and G2 is free during transmit. |
| No collision handling or link integrity state | 10BASE-T | Not written yet | The transmitter sends link pulses, and a received link pulse makes a short record without an SFD, but nothing tracks link state, detects collisions or backs off. |
| One optional side-set pin leaves 3 delay bits | USB | Shared side-set and delay field | The USB host program runs its engine at CLKDIV 3 to reach 32 cycles per bit. |
| The G2 receive enable goes through an output pin | USB | The bit state machine reads its inputs only from the pin space | `usb_ls_host` uses output pin 12 as the receive enable, so that pin is not available for anything else. |
| The CAN transmit queue holds 4 words | CAN | Buffer mode FIFO_TX_RAM gives half of the 8 entries to the error counters and scratch RAM | 4 words hold 20 bits: a Host must refill within about 14 µs at 1 Mbit/s. If the queue runs dry inside a frame (B14) the node releases TXD within a bit, sets flag 4 and clears flag 6, then signals the error and receives on; `test_can_node_transmit_underrun` checks it. The Host must not clear the queue while the node is transmitting. |
| SWD supports ORUNDETECT = 0 only | SWD | Program size | With overrun detection enabled, ADIv5 expects a data phase after WAIT or FAULT, which the program skips. |
| Controller roles only for SPI, I2C, JTAG and SWD | SPI, I2C, JTAG, SWD | Not written yet | Peripheral, target and debug-target roles need their own programs. |

The timed wait and the ISR count (instruction set version 2) removed the earlier limits on CAN receive resynchronization, USB reply timeouts, SWD replies other than OK, and Ethernet frame boundaries that relied on Host timing.

## External hardware

| Protocol | Needed |
| --- | --- |
| UART | A pull-up on TX if it must idle high before configuration; level shifting for other voltages |
| SPI | A pull-up on CS_N, so the peripheral stays deselected before configuration; level shifting for other voltages |
| I2C | Pull-ups on SDA and SCL sized for the bus capacitance and speed (for example 4.7 kOhm at 100 kHz, 2.2 kOhm at 400 kHz for a short bus) |
| JTAG, SWD | Level shifting for targets below the chip's I/O voltage; a pull-up on SWDIO |
| PS/2 | Pull-ups on CLK and DATA |
| CAN | A CAN transceiver (TXD, RXD) |
| Low-speed USB | 1.5 kOhm pull-up on D- at the device end; 15 kOhm pull-downs at the host end; a 48 MHz board clock |
| 10BASE-T | Isolation magnetics, transmit resistor network and a receive comparator |
