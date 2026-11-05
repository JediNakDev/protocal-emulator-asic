# Concurrent protocols

Which programs can run at the same time, one on each engine, measured against independent peers.
`test/test_concurrency.py` produces every result here.

All results are from RTL simulation with Icarus Verilog at the 50 MHz clock profile.
They do not cover pad timing, metastability, clock duty cycle, analog front ends, gate-level behavior or a real Host.
A pass means the two programs worked together in the tested traffic and Host schedule below; it is not a guarantee for other traffic.

## What a pass means

Both engines start in one `CTRL` write, each with its own peer, and one Host serves both, since the Host port carries one transaction at a time.
A pair passes only if all of these hold in the same run:

- **Wire timing, in absolute time, for both protocols.** Each protocol's own peer checks it while the other runs: UART frames, and every UART TX edge on its bit grid to the cycle; SPI SCK rate, MOSI setup and hold, CS_N timing; every UM10204 I2C minimum and the SCL period; JTAG and SWD clock rate, setup and hold; PS/2 clock phases; Manchester transitions and TP_IDL on 10BASE-T; capture and replay intervals exact to the cycle.
- **Data at the Host in both directions.** Host to wire and wire to Host are compared word by word. Where a protocol runs one way (UART, capture, replay, CAN listen-only, Ethernet), the pair covers the directions its two programs have.
- **No lost or hidden errors.** `STICKY` reads 0, the UART framing flag is clear, and no bidirectional pin sees contention that outlasts its time step.
- **Real overlap.** Both protocols have at least 20 edges inside their shared window, the merged edge list changes protocol at least 12 times, and the window covers at least half of the shorter run. The smallest measured window is 226.8 µs.

Pins are allocated around the ones a program or peer hard-codes.
The second program sits right behind the first in one memory page when both fit, otherwise in page 1, so relocation is exercised without changing a program.
`test_pair_*` runs each pair with its first program on engine 0; `PE_SWAP_ENGINES=1` runs them all on the opposite engines.

### Traffic and Host schedule

| Program | Rate in the sweep | Workload |
| --- | --- | --- |
| `uart_tx`, `uart_rx` | 115,200 baud (3.125 Mbaud in the two `fastest` tests) | Random bytes with 0x00, 0xFF, 0x55 |
| `spi_cpha0` | Mode 0, SCK 6.25 MHz (`spi_cpha0_fast`, 12.5 MHz, in one test) | Transactions of 1, 2 and 6 random bytes |
| `i2c` | 400 kHz, target stretching 2 µs (100 kHz stretched 7 µs in one test) | Write, read back with repeated START, NACK of a wrong address |
| `jtag` | TCK 6.25 MHz | Reset, IDCODE, IR, scratch DR, BYPASS, repeated |
| `swd` | SWCLK 6.25 MHz | DPIDR read, CTRL/STAT write and read, repeated |
| `ps2_device` | 12.5 kHz | Scan code, host command with acknowledge, reply |
| `ps2_host` | 12.5 kHz | Device frame, reset command, two replies |
| `can_rx` | 1 Mbit/s, listen-only | 8-byte frames between two peer nodes |
| `eth10_tx` | 10 Mbit/s | One frame of about 375 bytes (1250 beside PS/2 and UART) |
| `eth10_rx` | 10 Mbit/s, +/-13.5 ns jitter | Minimum frames at the 9.6 µs gap |
| `capture` | 8-cycle filter | Bursts of 1-3 edges 8-20 cycles apart, 400-1200 cycles between bursts |
| `replay` | Pin 10 | Intervals of 120-900 cycles |

Beside a program that breaks when the Host is late (`eth10_tx`, `eth10_rx`, `capture`, `replay`), the Host moves one word per visit to the other engine and revisits the urgent one after it.
Otherwise it serves the engines in turn with full bursts.

A pass does not mean line rate.
SPI, JTAG and SWD stretch their clocks between bytes when the Host is late, which their protocols allow; beside a 115,200 baud UART, SPI averaged about 3.7 MHz against its 6.25 MHz burst rate.

## Limits by program

Words come from `tools/measure.py`. G1, the checksum register, is per engine and not a shared block; it is listed because only one program uses it.

| Program | Protocol | Engines | Words | Pins | G2 | G7 | G1 | Other shared state | Clock profile |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `uart_tx` | UART TX | 1 | 4 | 1 bidirectional | | | | | 50 MHz |
| `uart_rx` | UART RX | 1 | 9 | 1 input | | | | Flag 4 | 50 MHz |
| `uart_tx` + `uart_rx` | UART full duplex | 2 | 13 | 2 | | | | Flag 4 | 50 MHz |
| `spi_cpha0`, `spi_cpha0_fast`, `spi_cpha1` | SPI | 1 | 11 | 3 adjacent outputs, 1 input | | | | | 50 MHz |
| `i2c` | I2C | 1 | 24 | 2 adjacent bidirectional | | | | | 50 MHz |
| `jtag` | JTAG | 1 | 2 | 3 outputs, 1 input | | | | | 50 MHz |
| `swd` | SWD | 1 | 48 | 2 bidirectional | | | | | 50 MHz |
| `ps2_device` | PS/2 device | 1 | 34 | 2, hard-coded 3 and 4 | | | | | 50 MHz |
| `ps2_host` | PS/2 host | 1 | 42 | 2, hard-coded 3 and 4 | | | | | 50 MHz |
| `can_rx` | CAN listen-only | 1 | 21 | 1 input, hard-coded 8 | Yes | | | | 50 MHz |
| `can_node` + `can_line` | CAN node | 2 | 472 + 39 | 2 | Yes | | Yes | All 8 flags, buffer RAM, 511 of 512 words | 50 MHz |
| `usb_ls_host` + `usb_ls_rx` | Low-speed USB | 2 | 37 + 5 | 2 bidirectional, pin 12 | Yes | | | Flag 2 | 48 MHz |
| `eth10_tx` | 10BASE-T TX | 1 | 30 | 3 adjacent (TD+, TD-, S, never driven) | | | | `NEGOUT`, `NEGMASK` | 50 MHz |
| `eth10_rx` | 10BASE-T RX | 1 | 7 | 1 input | Yes | Yes | | `NEGSEL`, flag 3 | 50 MHz |
| `capture` | Waveform capture | 1 | 7 | 1 input, sharing a watched line is allowed | | | | Flag 0 | 50 MHz |
| `replay` | Waveform replay | 1 | 3 | 1 output | | | | | 50 MHz |

## Pair results

Sweep traffic and both engine orders unless a note says otherwise.
Thirteen single-engine programs make 78 pairs: 73 measured-pass, 1 measured-pass at reduced traffic and measured-fail at sweep traffic, 1 measured-fail, 1 blocked by a resource, 1 blocked by hard-coded pins, 1 not measured.

Key: **P** measured-pass, **F** measured-fail, **L** passes only at lighter traffic, **G** blocked by G2, **H** blocked by hard-coded pins, **N** not measured.

| | UART RX | SPI | I2C | JTAG | SWD | PS/2 dev | PS/2 host | CAN RX | Eth TX | Eth RX | Capture | Replay |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **UART TX** | P | P | P | P | P | P | P | P | P | P | P | P |
| **UART RX** | | P | P | P | P | P | P | P | P | P | P | P |
| **SPI** | | | P | P | P | P | P | P | P | P | P | P |
| **I2C** | | | | P | P | P | P | P | P | P | P | P |
| **JTAG** | | | | | N | P | P | P | P | P | P | P |
| **SWD** | | | | | | P | P | P | P | P | P | P |
| **PS/2 dev** | | | | | | | H | P | P | P | P | P |
| **PS/2 host** | | | | | | | | P | P | P | P | P |
| **CAN RX** | | | | | | | | | P | G | P | P |
| **Eth TX** | | | | | | | | | | F | P | P |
| **Eth RX** | | | | | | | | | | | L | P |
| **Capture** | | | | | | | | | | | | P |

| Pair | Outcome | Cause |
| --- | --- | --- |
| CAN RX + Eth RX | Blocked by resource: G2 | One bit state machine; both need it for their whole run |
| PS/2 device + PS/2 host | Blocked by resource: pins 3 and 4 | Both programs name them in `wait gpio`. A reassembled copy on other pins could run; none was measured |
| JTAG + SWD | Not measured | Not a chip limit: both test peers hard-code pins 0 and 2, while the programs can move |
| Eth TX + Eth RX | Measured-fail | Host port bandwidth. A word each way every 80 cycles needs 64 cycles of data transfer before any command or poll; the transmit queue ran dry inside a frame with bursts of 8 and of 4 (`test_limit_eth_full_duplex`) |
| Eth RX + capture | Measured-fail at sweep traffic, measured-pass at about 70% of its edges | Host port load, with no change at the chip: Ethernet receive takes about 60% of the port. At 400-1200 cycles between bursts, capture lost 3 of 103 words and set `STICKY` bit 1; at 800-1600 the pair passes. Bursts of 4 or fewer make it worse by spending the port on polling |

### Further measurements

| Test | Outcome |
| --- | --- |
| UART TX + I2C, 115,200 baud and 400 kHz, stretched 2 µs | Measured-pass |
| I2C + UART TX, 100 kHz, stretched 7 µs | Measured-pass |
| UART TX + I2C, 3.125 Mbaud | Measured-pass |
| UART RX + SPI, 115,200 baud and 6.25 MHz | Measured-pass |
| UART RX + SPI, 3.125 Mbaud and 12.5 MHz with MISO bypass | Measured-pass |
| Capture + UART TX, both engine orders | Measured-pass |
| I2C loaded into UART TX's page and started while UART frames are on the wire | Measured-pass: every UART edge stayed on its bit grid |
| Capture of UART TX's own line, same pin | Measured-pass |
| Capture of I2C's own SCL, 100 kHz, same pin | Measured-pass |
| Capture of the CAN bus beside `can_rx`, same pin, 1 Mbit/s | Measured-fail in capture: the queue overflows at two words about every 100 cycles. `can_rx` keeps every frame through the 8-cycle filter |

### Two-engine programs

These need both engines, so nothing runs beside them; none of these combinations was simulated, since the third engine does not exist.

| Program | Beside anything | Resource |
| --- | --- | --- |
| CAN node (`can_node` + `can_line`) | Blocked | Engines; also 511 of 512 words, all 8 flags and G2 |
| Low-speed USB | Blocked | Engines; also the 48 MHz clock profile against 50 MHz for everything else |
| UART full duplex | Blocked beside a third program | Engines. As a pair it is the measured UART TX + UART RX |
| 10BASE-T full duplex | Blocked beside a third program | Engines, and measured-fail on its own above |

## Design-review targets

| Target | Result |
| --- | --- |
| Debug probe with a console: SWD or JTAG with UART RX | Measured-pass, SWD in one page with UART RX (57 words) |
| Sensor bridge: I2C with SPI | Measured-pass |
| CAN node with a sniffer | Blocked by engines. Listen-only CAN with capture of another signal passes; capture of the CAN bus itself loses words |
| A single-engine protocol with capture of its own lines | Measured-pass for UART TX and for I2C at 100 kHz; the limit is two Host words per edge, not the pin |

## Negative controls

The same checks fail when they should.
A wrong-rate peer fails only its own protocol: the other one stays clean in the same run.

| Control | Rejected by |
| --- | --- |
| UART peer expects 125,000 baud | UART timing; I2C clean |
| I2C target holds Fast-mode traffic to Standard-mode limits | I2C timing; UART clean |
| UART sender 8% fast | Bytes at the Host; SPI clean |
| SPI peripheral expects 5 MHz | SCK clock period; UART clean |
| Three required pairs at 40 MHz | Both protocols of each pair, each in its own check |
| One protocol after the other | The overlap check alone |
| A peer fights the UART TX pin | The contention check |
| Capture edges faster than the Host drains | `STICKY` and a short recording |

## Reproduce

```sh
make test COCOTB_TEST_MODULES=test_concurrency SIM_BUILD=sim_build/job2 COCOTB_RESULTS_FILE=sim_build/job2/results.xml
PE_SWAP_ENGINES=1 COCOTB_TEST_FILTER=test_pair_ make test COCOTB_TEST_MODULES=test_concurrency SIM_BUILD=sim_build/job2_swap COCOTB_RESULTS_FILE=sim_build/job2_swap/results.xml
```

`test_concurrency` is not in the default module list of `test/Makefile`, so `make test` and `make check` do not run it.
The measured-fail pairs are tests that require the documented failure, so a later change that fixes one shows up as a failing test.

Recorded at base commit `620b794`, working tree with this test: see [run results](#run-results).

## Run results

Recorded on 2026-10-31 with Icarus Verilog 13.0, cocotb 2.0.1, base commit `620b794`, and the `test/test_concurrency.py` in this working tree.

| Run | Cases | Passed | Failed |
| --- | --- | --- | --- |
| `test_concurrency` | 95 | 95 | 0 |
| `test_concurrency` with `PE_SWAP_ENGINES=1`, engines swapped (73 are `test_pair_*`) | 95 | 95 | 0 |

A passing `test_limit_*` case means the documented failure still occurs.
