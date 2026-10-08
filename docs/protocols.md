# Protocol programs

Each protocol runs a program from `programs/`.
Host-side framing and transaction helpers for JTAG, SWD, PS/2, CAN, USB and Ethernet live in `tools/pe/proto/`.
Every one is verified in RTL simulation by a cocotb test (`test/test_<protocol>.py`) against an independent peer model that shares no code with the program or the host-side software.
UART, SPI and I2C are verified in `test/test_protocols.py`, which loads the same standalone program files used for upload.

The base protocol sources are `uart_tx.pasm`, `uart_rx.pasm`, `spi_mode0.pasm`, `spi_mode0_fast.pasm` and `i2c_write.pasm` in `programs/`.
Their comments describe the pin configuration, data packing and timing.

All results below are from RTL simulation.
They do not cover pad timing, metastability, clock duty cycle or analog front ends.

## Summary

| Protocol | Role | Program words | Hardware used | Clock | Verified |
| --- | --- | --- | --- | --- | --- |
| JTAG | Controller | 2 | Side-set, autopull/autopush | 50 MHz, TCK 6.25 MHz | IDCODE, IR capture, DR read/write, BYPASS against an IEEE 1149.1 TAP model |
| SWD | Host (probe) | 13 | Side-set, `out pindirs` for turnaround | 50 MHz, SWCLK 6.25 MHz | IDCODE read, CTRL/STAT write and read-back, SELECT write, bad-parity request recovery, no SWDIO contention |
| PS/2 | Device (keyboard) | 22 | Open-drain, `status`, side-set clock | 50 MHz | Scan codes to a host model; host command with request-to-send and acknowledge |
| PS/2 | Host | 25 | Open-drain, inhibit, `status` | 50 MHz | Device frames; reset command acknowledged, 0xFA and 0xAA received |
| CAN 2.0A | Receiver (listen-only) | 16 | G2 destuffer, emit into ISR | 50 MHz, 1 Mbit/s | Stuff-heavy and 8-byte frames; 0.1% clock offset |
| CAN 2.0A | Transmitter | 19 | G8 indexed jump, `irq wait` | 50 MHz, 1 Mbit/s | ACKed by a node; lost arbitration detected, winner received, retransmission ACKed |
| Low-speed USB | Host | 14 + 8 | G2 clock-recovering NRZI receiver, pattern wait, both engines | 48 MHz, 1.5 Mbit/s | GET_DESCRIPTOR (SETUP, DATA0, ACK, IN, DATA1, ACK) with device clocks exact, 1.5% fast and 1.5% slow |
| 10BASE-T | Transmitter | 15 | G8 indexed jump, 2-pin side-set | 40 MHz, 10 Mbit/s | 89-byte frame, TD- complementary, TP_IDL, FCS |
| 10BASE-T | Receiver | 9 | G2 Manchester decoder, falling-edge sampling (pin 15), emit into ISR | 40 MHz, 10 Mbit/s | Frames at five input phases and with +/- 8 ns jitter per transition |
| Any (waveform) | Capture and replay | 5 + 3 | Edge capture (G3), flag wait, autopush/autopull | 50 MHz, 1-cycle resolution | Random edges 4 to 100,003 cycles apart, captured and replayed with every interval exact; a UART byte edited on the Host and decoded by an independent receiver |

The largest pair that must run together, CAN transmit plus receive, uses 35 of the 64 instruction words.

CAN receive requires a push threshold of 15 and shifts left, packing each data word into bits 14:0 with bit 15 clear.
The final data word contains a 1 marker followed by zero padding, and `0xFFFF` separates records.
This framing preserves all payload values after destuffing, including consecutive `0xFF` bytes.
The Host decodes these records with `tools/pe/proto/can.py`.

## Waveform capture and replay

`capture.pasm` records the time of every edge on one pin, and `replay.pasm` plays a recording back on another pin, both exact to the cycle and without knowing the protocol.
The edge capture unit (G3) timestamps each edge with the 32-bit cycle counter, so gaps up to 86 s at 50 MHz keep their exact length.
`tools/pe/proto/waveform.py` converts between the two formats and edits recordings: `invert_span` drives any stretch of time to the opposite level.
`test/test_waveform.py` uses it to flip one data bit of a captured UART byte and checks that an independent UART receiver decodes the edited byte from the replay.

This turns the chip into a recorder and signal generator for reverse engineering and fault injection on protocols it has no program for: record an unknown exchange, study it on the Host, then replay it, or a deliberately damaged version of it, to the device under test.

| Property | Value |
| --- | --- |
| Resolution | 1 cycle (20 ns at 50 MHz) for both capture and replay |
| Shortest pulse | 4 cycles, enforced by the 4-cycle glitch filter on the captured pin; replay holds each level at least 3 cycles |
| Burst | 4 edges at the shortest spacing fill the 8-word receive queue |
| Sustained capture | One edge per 80 cycles (1.6 µs at 50 MHz) with a Host draining at the minimum `HSCK` timing; one per 60 cycles overruns |
| Lost edges | Reported by the capture overrun sticky bit |
| Replay | Every word holds 3 to 32,770 cycles; the Host must keep the 8-word transmit queue from running dry |

## Limits found during verification

These came out of writing and testing the programs; each is a property of the current design, not of the test.

| Limit | Protocols | Cause | Effect |
| --- | --- | --- | --- |
| CAN receive has no resynchronization inside a frame | CAN | No free register for a timed edge search, and no wait-with-timeout instruction | Works from -0.2% to +0.4% clock offset for an 8-byte frame (tested with a 72% sample point); CAN allows up to 1.58% with resynchronization. Fine with crystal clocks (0.01%), not with ceramic resonators. |
| The chip does not acknowledge CAN frames it receives | CAN | Only one engine can drive a pin, and the receiver needs a CRC verdict within one bit time | Works as a listen-only receiver and as a transmitter on a bus with at least one other node. Not a complete CAN node. |
| CAN error frames and error counters are not implemented | CAN | Program size; left to the Host | A stuff error ends the record; the Host discards frames with bad CRC. |
| Ethernet receive jitter tolerance is about +/- 9 ns per transition | 10BASE-T | 16-state decoder with half-cycle sampling at 40 MHz | Passes at +/- 9 ns and fails at +/- 10 ns in simulation; IEEE 802.3 asks for more margin. Lab-grade with short cables. |
| Ethernet full duplex at line rate exceeds the Host link | 10BASE-T | 10 Mbit/s each way needs 20 Mbit/s, which equals the Host port's raw maximum at 40 MHz | Half duplex works. Collision detection is possible because transmit and receive use different engines and G2 is free during transmit. |
| Ethernet frame boundaries rely on Host timing | 10BASE-T | No count of bits per record and no separator word that payload cannot contain | The Host must read each frame within the interframe gap plus 1.6 us after flag 3 is set. |
| One optional side-set pin leaves 3 delay bits | USB | Shared side-set and delay field | The USB host program runs its engine at CLKDIV 3 to reach 32 cycles per bit. |
| No reply timeout | USB, SWD | No wait-with-timeout instruction | A missing USB reply stalls the program until the Host forces a jump; SWD decides on WAIT/FAULT only after the transfer. |
| Link pulses (NLP) are not generated | 10BASE-T | Not written yet | A link partner that requires link integrity will not bring the link up. |

The two hardware changes that would remove the most limits are a wait with timeout (CAN resynchronization, USB and SWD reply timeouts) and a way for an engine to read its own ISR count (exact record lengths without markers or Host timing).

## External hardware

| Protocol | Needed |
| --- | --- |
| JTAG, SWD | Level shifting for targets below the chip's I/O voltage; a pull-up on SWDIO |
| PS/2 | Pull-ups on CLK and DATA |
| CAN | A CAN transceiver (TXD, RXD) |
| Low-speed USB | 1.5 kOhm pull-up on D- at the device end; 15 kOhm pull-downs at the host end; a 48 MHz board clock |
| 10BASE-T | Isolation magnetics, transmit resistor network and a receive comparator; a 40 MHz board clock |
