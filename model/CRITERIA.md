# Pass criteria

Each criterion has an ID that the test suite (`make model`) reports as PASS or FAIL.
The suite fails if any criterion fails or is never exercised.
Every check observes the `uio` pins through an independent peer model, or the host bus, never engine internals.
Timing limits are in nanoseconds or bit times and are converted to cycles of the 40 MHz clock.

## General

| ID | Requirement |
| --- | --- |
| G1 | After reset, and after an engine restart, every `uio` pad is an input (`oe = 0`). |
| G2 | No test produces a pin double-write, an engine-engine conflict, bus contention with a peer, or a protocol violation reported by a peer. Two engines driving one pad is detected. |
| G3 | Every protocol is selected only by the program and configuration loaded through the host bus; the RTL model is identical for all tests. |
| G4 | Every program fits in 32 instruction words. |
| G5 | Four engines run UART TX, UART RX, SPI and I2C at the same time with correct results. |
| G6 | The CRC and line-coding helpers shared by the host and peer models match published check values and a hand-encoded USB packet. |

## UART, 115200 baud, 8N1

| ID | Requirement |
| --- | --- |
| U1 | Bytes sent by the TX program (including `00 FF 55 AA` and random data) are decoded correctly by the independent receiver. |
| U2 | TX baud error ≤ 0.5%, and every edge is within 1 clock of the ideal bit grid. |
| U3 | Back-to-back TX frames are 10 bit times long (one stop bit). |
| U4 | The RX program receives correctly from a sender with −3%, 0% and +3% baud error. |
| U5 | A bad stop bit raises the engine flag, drops the byte, and the next byte is received. |
| U6 | Full duplex: 64 bytes echoed by the peer arrive intact through two engines. |

## SPI controller, mode 0, MSB first

| ID | Requirement |
| --- | --- |
| S1 | SCK period is 4 clocks (10 MHz), with high and low phases ≥ 2 clocks each. |
| S2 | No SCK period inside a 256-byte frame exceeds 4 clocks when the RP2040 host streams the data. |
| S3 | MOSI never changes on a cycle where SCK rises. |
| S4 | W25Q128JV: JEDEC ID `EF 40 18`; read (03h) and fast read (0Bh) data correct; write enable, page program, busy polling and read-back correct. |
| S5 | CS stays high ≥ 50 ns between frames (tSHSL). |
| S6 | `spi_master` reads correctly when MISO appears one clock after the SCK edge reaches the pin. `spi_div6` also tolerates two extra clocks. |

## I2C controller, Fast-mode (400 kHz)

| ID | Requirement |
| --- | --- |
| I1 | fSCL ≤ 400 kHz, and every UM10204 Fast-mode minimum is met: tLOW 1.3 µs, tHIGH 0.6 µs, tSU;DAT 100 ns, tHD;STA 0.6 µs, tSU;STA 0.6 µs, tSU;STO 0.6 µs, tBUF 1.3 µs. |
| I2 | ADT7420: ID register 0xCB, temperature read, 2-byte register write and read-back, with repeated START. |
| I3 | An absent address is reported as NACK. |
| I4 | With the target stretching SCL for 10 µs after every ACK, transfers are correct and I1 still holds. |
| I5 | When idle, both lines are released and read high. |

## USB low-speed host (stretch goal)

| ID | Requirement |
| --- | --- |
| K1 | Host bit rate within 1.5 Mb/s ± 1.5%. |
| K2 | Every host packet decodes at the independent device: SYNC, NRZI, bit stuffing, PID check, CRC5 / CRC16. |
| K3 | Host EOP: SE0 for 1.25–1.50 µs, then J. |
| K4 | Control transfers: GET_DESCRIPTOR (SETUP, DATA0, ACK; IN data stages with toggling DATA1/DATA0 and valid CRC16; status stage) returns the 18-byte device descriptor. SET_ADDRESS takes effect, and the device then answers at the new address only. |
| K5 | Interrupt IN on EP1: NAK when no key is pressed; 8-byte HID reports with alternating DATA0/DATA1 when keys are pressed; every report is ACKed. |
| K6 | The host receives correctly from a device whose clock is off by −1.5% and +1.5%. |
| K7 | The host's handshake starts 2–7.5 bit times after the device's EOP (USB 2.0 §7.1.18). |
| K8 | A packet with a bad CRC gets no device reply. The host times out, restarts the engine and continues. |

## 10BASE-T Ethernet transmitter (stretch goal)

| ID | Requirement |
| --- | --- |
| E1 | Manchester half-bits are exactly 50 ns (10 Mb/s), and every bit has its mid-bit transition. |
| E2 | Frames decode at the independent receiver: 7 × 0x55 preamble, 0xD5 SFD, valid FCS (CRC-32), valid IPv4 header checksum, and the UDP payload sent by the host. |
| E3 | After the last bit, the line stays positive for ≥ 250 ns (TP_IDL), then returns to idle. |
| E4 | Back-to-back frames are separated by ≥ 9.6 µs (96 bit times). |
| E5 | While idle, link pulses are 100 ns wide and 8–24 ms apart. |
| E6 | A maximum-size frame (1500-byte IP packet) is streamed from the host without underrun. |

Ethernet reception is out of scope: it needs an external analog receiver and more oversampling than a 40 MHz single-edge clock gives.
