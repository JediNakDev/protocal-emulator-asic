# Board wiring guide

**No hardware has been tested.**
The chip has not been fabricated, and no wiring in this guide has been built.
Every protocol result in this repository comes from RTL simulation against ideal pins.
Pull-ups in simulation rise instantly, and no simulation covers pad timing, line capacitance or analog front ends.

This guide lists every external connection the design needs on the Tiny Tapeout ETR demo board.
It was written against demo board schematic v3.2.

## How values are labelled

| Label | Meaning |
| --- | --- |
| **[repo: file]** | From repo: the value is stated in that file of this repository |
| **[std: source]** | Standards-based recommendation: taken from that standard or datasheet, not tested with this chip |
| **[board: source]** | A fact about the published demo board or its firmware, not a choice made here |
| **not specified** | No source gives a value; do not assume one |

Board sources, as read on 2026-11-01:

- [ETR board specification](https://tinytapeout.com/specs/pcb-etr/) and [recommended pinouts](https://tinytapeout.com/specs/pinouts/).
- [Demo board repository](https://github.com/TinyTapeout/tt-demo-pcb) at `bbf3dd1`: README, [schematic v3.2](https://github.com/TinyTapeout/tt-demo-pcb/blob/main/doc/tt-etr-dbv3.pdf) and pinout diagram.
- [MicroPython firmware](https://github.com/TinyTapeout/tt-micropython-firmware) at `d485c7a`: `config.md`, `src/ttboard/demoboard.py`, `src/ttboard/pins/`.

## Demo board

| Item | Value | Source |
| --- | --- | --- |
| Digital I/O voltage | 3.3 V | [repo: docs/competition-update.md]; [board: quick start guide](https://tinytapeout.com/guides/get-started-demoboard-etr/) |
| Analog pads | None | [repo: docs/competition-update.md] |
| Project pins | 24 digital (8 in, 8 out, 8 bidirectional), plus clock and reset | [repo: docs/competition-update.md] |
| Host | The board's RP2350B runs the Host port from its GPIO pins | [board: schematic]; the organizers accept any documented Host [repo: docs/competition-update.md] |
| Pmod headers | Input, Bidir and Output, 12-pin, female, host side, 3.3 V on pins 6 and 12, ground on pins 5 and 11 | [board: schematic] |
| Pmod signal pins | Pins 1-4 carry bits 0-3, pins 7-10 carry bits 4-7 | [board: schematic] |
| Clock and reset access | RP2350 and the Project I/O SIL header; not on the Pmods | [board: pinout diagram] |
| 5 V access | VBUS on the Management header, behind a 350 mA polyfuse shared with the whole board | [board: schematic] |
| Power budget for external parts | not specified beyond that fuse | |

### Shared pins: read this before wiring

Every project pin also goes to an RP2350 GPIO, so an external part never has a pin to itself.

1. **P8 and P9 need firmware support.**
   They are `ui[6]` and `ui[7]`, and in `ASIC_RP_CONTROL` mode the firmware makes every `ui` GPIO an output [board: `pins.py`, `begin_asiconboard`].
   An external signal on P8 or P9 then fights the RP2350.
   Host firmware must switch GPIO23 and GPIO24 back to inputs, with pulls off, before a front end drives them.
   `ASIC_MANUAL_INPUTS` is not the answer: it leaves every `ui` GPIO an input, so the RP2350 cannot run the Host port.
   UART RX, SPI MISO, JTAG TDO, CAN RXD, Ethernet RX and capture all use these pins in the tested wiring.
2. **All DIP switches must be off.**
   Each switch connects a `ui` pin to 3.3 V through 1 kΩ [board: schematic].
   A switch left on holds a Host line or P8/P9 high against its driver.
   When its contention check is on, the firmware also leaves a `ui` pin undriven if it reads high [board: `pins.py`, `dieOnInputControlSwitchHigh`].
3. **Do not plug a Pmod module into the Input header.**
   Pins 1-4 and 7-8 carry the Host port.
   Connect single wires to pins 9 (P8) and 10 (P9) and leave the rest alone.
4. **Do not drive the Output header pins 1-4 or 7.**
   They carry `HDO[3:0]` and `HIRQ`.
5. **Remove the RP2350 pull-downs on protocol pins.**
   The firmware enables a pull-down on every GPIO it treats as an input [board: `gpio_map_dbv3.py`, `default_pull`].
   That loads pull-ups and fights weak ones.
   Keep `uio_oe_pico = 0`, so the RP2350 drives no `uio` pin, and turn the pulls off.
6. **Check RP2350 erratum E9 on the board in hand.**
   On affected RP2350 silicon an input pad can sit near 2.2 V, and the published workaround is an external pull-down of 8.2 kΩ or less ([background](https://hackaday.com/2024/09/04/the-worsening-raspberry-pi-rp2350-e9-erratum-situation/)).
   This matters for the lines this design leaves weakly pulled low, such as USB D+ and D- with 15 kΩ.
   Whether the board's stepping is affected is not specified. Measure the idle level on each line with a meter before connecting a device.
7. **The 7-segment display loads the outputs.**
   Every `uo` pin drives a segment through a solder jumper and 510 Ω [board: schematic], about 2.5 mA at 3.3 V with the part's typical 2 V forward drop.
   This includes `HDO[3:0]`, `HIRQ` and P10-P12.
   Open the jumpers on the bottom of the board if the load matters; WS2812, I2S and replay use these pins.

## Pin table

Directions and names are from `info.yaml`.
Each was checked against `src/project.v`, `src/pe_pins.v` and the pin space in `docs/spec.md`; they agree.

Drive modes are set by `PINCFGn` [repo: docs/spec.md].
Pad current, slew rate, capacitance and input thresholds are **not specified** for every pin, because CMOS5L has no characterized pad numbers.

| Chip pin | Name | Direction | Drive capability | After reset | RP2350 | Header |
| --- | --- | --- | --- | --- | --- | --- |
| `clk` | Clock | In | Input | | GPIO16 through 47 Ω | SIL only |
| `rst_n` | Reset, active low | In | Input | | GPIO14; see [reset](#reset) | SIL only |
| `ui[0]` | `HCS_N` | In | Host drives | Host holds high | GPIO17 | Input 1 |
| `ui[1]` | `HSCK` | In | Host drives | Host holds low | GPIO18 | Input 2 |
| `ui[2]` | `HDI0` | In | Host drives | | GPIO19 | Input 3 |
| `ui[3]` | `HDI1` | In | Host drives | | GPIO20 | Input 4 |
| `ui[4]` | `HDI2` | In | Host drives | | GPIO21 | Input 7 |
| `ui[5]` | `HDI3` | In | Host drives | | GPIO22 | Input 8 |
| `ui[6]` | `P8` | In | Input only: synchronizer, optional bypass, filter | Input | GPIO23 | Input 9 |
| `ui[7]` | `P9` | In | Input only: synchronizer, optional bypass, filter | Input | GPIO24 | Input 10 |
| `uo[0]` | `HDO0` | Out | Always driven | 0 | GPIO33 | Output 1 |
| `uo[1]` | `HDO1` | Out | Always driven | 0 | GPIO34 | Output 2 |
| `uo[2]` | `HDO2` | Out | Always driven | 0 | GPIO35 | Output 3 |
| `uo[3]` | `HDO3` | Out | Always driven | 0 | GPIO36 | Output 4 |
| `uo[4]` | `HIRQ` | Out | Always driven, active high | 0 | GPIO37 | Output 7 |
| `uo[5]` | `P10` | Out | Output only: off (drives 0), push-pull, always; open-drain acts as push-pull | 0 | GPIO38 | Output 8 |
| `uo[6]` | `P11` | Out | As P10 | 0 | GPIO39 | Output 9 |
| `uo[7]` | `P12` | Out | As P10 | 0 | GPIO40 | Output 10 |
| `uio[0]` | `P0` | In/out | Off, push-pull, open-drain, always | Released | GPIO25 | Bidir 1 |
| `uio[1]` | `P1` | In/out | As P0 | Released | GPIO26 | Bidir 2 |
| `uio[2]` | `P2` | In/out | As P0 | Released | GPIO27 | Bidir 3 |
| `uio[3]` | `P3` | In/out | As P0 | Released | GPIO28 | Bidir 4 |
| `uio[4]` | `P4` | In/out | As P0 | Released | GPIO29 | Bidir 7 |
| `uio[5]` | `P5` | In/out | As P0 | Released | GPIO30 | Bidir 8 |
| `uio[6]` | `P6` | In/out | As P0 | Released | GPIO31 | Bidir 9 |
| `uio[7]` | `P7` | In/out | As P0 | Released | GPIO32 | Bidir 10 |

RP2350 numbers are from the board README, schematic and firmware class `GPIOMapTTDBv3`, which agree.
Pin indices 13-15 are internal and have no pad.
`ena` is ignored by the design.

## Protocol wiring

The pins below are the tested assignment: each comes from the test that runs the program, and the program comments were checked against it.
Program comments define most pins by role, so many can move, but only the assignment shown has run in simulation.
Pins the program fixes are marked.

No protocol below may be wired on the assumption that it works at the stated speed on real pads; see [known unknowns](#known-unknowns).

### UART

| Item | Value |
| --- | --- |
| Program | `programs/uart_tx.pasm`, `programs/uart_rx.pasm`; one engine each |
| Pins | TX: P0, Bidir 1. RX: P8, Input 9 |
| Pull-ups | TX to 3.3 V if the line must idle high before configuration [repo: docs/protocols.md]; value not specified. Remote end drives RX |
| Front end | None for 3.3 V logic |
| Level shifting | For other voltages [repo: docs/protocols.md]; part not specified |
| Clock profile | 50 MHz. Baud = f_clk / (8 (CLKDIV + 1)); CLKDIV 53 gives 115,741 baud [repo: docs/protocols.md] |
| Pmod mapping | TT UART top row is TXD on `uio[1]`, RXD on `uio[2]`. TX and RX pins are free, so OUTPIN = P1 and INPIN = jmp pin = P2 fit it; not tested. The RP2040 serial pinouts `ui[3]`/`uo[4]` and `ui[1]`/`uo[0]` cannot be used: those pins are `HDI1`/`HIRQ` and `HSCK`/`HDO0` |
| Checked | `test/test_protocols.py`: `out_base=0`, `in_base=8, jmp_pin=8`. Matches `info.yaml` |

### SPI

| Item | Value |
| --- | --- |
| Program | `programs/spi_cpha0.pasm` (modes 0, 2), `spi_cpha1.pasm` (modes 1, 3), `spi_cpha0_fast.pasm` (modes 0, 2 at clk/4); one engine |
| Pins | MOSI: P0, Bidir 1. SCK: P1, Bidir 2. CS_N: P2, Bidir 3. MISO: P9, Input 10. Fixed by the program: CS_N = SCK + 1 |
| Pull-ups | CS_N to 3.3 V, so the peripheral stays deselected before configuration [repo: docs/protocols.md]; value not specified |
| Front end | None. CPOL 1 uses the PINCFG invert bit on SCK, not hardware |
| Level shifting | For other voltages [repo: docs/protocols.md]; part not specified |
| Clock profile | 50 MHz. SCK 6.25 MHz; 12.5 MHz with `spi_cpha0_fast` and the synchronizer bypassed on MISO [repo: docs/protocols.md]. **The 12.5 MHz case is unverified at the pads** |
| Pmod mapping | No direct fit. TT SPI top row is CS `uio[0]`, MOSI `uio[1]`, MISO `uio[2]`, SCK `uio[3]`, and CS_N = SCK + 1 rules it out. Closest: MOSI P1, MISO P2, SCK P3, CS_N P4, with a wire from Bidir 7 to the module's pin 1 and nothing on P0; not tested, and the test's `set pindirs` setup assumes P0-P2 are adjacent. The bottom row is impossible: CS_N would land on P8, an input |
| Checked | `test/test_protocols.py`: `SPI_MOSI, SPI_SCK, SPI_CS_N, SPI_MISO = 0, 1, 2, 9`. Matches `info.yaml` |

### I2C

| Item | Value |
| --- | --- |
| Program | `programs/i2c.pasm`, controller; one engine |
| Pins | SDA: P3, Bidir 4. SCL: P4, Bidir 7. Fixed by the program: SCL = SDA + 1 (`wait 1 pin 1`). Both open-drain |
| Pull-ups | 4.7 kΩ at 100 kHz, 2.2 kΩ at 400 kHz for a short bus, SDA and SCL to 3.3 V [repo: docs/protocols.md] |
| Pull-up limits | Lower bound at 3.3 V: (3.3 - 0.4) V / 3 mA = 967 Ω. Upper bound: t_r / (0.8473 C_b), so 4.7 kΩ holds to 251 pF at 1000 ns and 2.2 kΩ to 161 pF at 300 ns [std: NXP UM10204]. The chip sinking 3 mA at 0.4 V is unverified |
| Front end | None |
| Level shifting | For other voltages; part not specified |
| Clock profile | 50 MHz. CLKDIV 19 for 100 kHz, 4 for 400 kHz [repo: programs/i2c.pasm] |
| Pmod mapping | TT I2C top row is SCL `uio[2]`, SDA `uio[3]`, the reverse of SCL = SDA + 1. A module fits with one wire: its SDA pin lands on P3 as tested, and a wire from Bidir 7 goes to the module's SCL pin (Pmod pin 3), with P2 left unused. The bottom row is impossible: SCL would land on P8 |
| Checked | `test/test_protocols.py`: `sda, scl = 3, 4`. Matches `info.yaml` |

### JTAG

| Item | Value |
| --- | --- |
| Program | `programs/jtag.pasm`, controller; one engine |
| Pins | TDI: P0, Bidir 1. TMS: P1, Bidir 2. TCK: P2, Bidir 3. TDO: P9, Input 10. TMS = TDI + 1 by the program. No TRST or system reset pin |
| Pull-ups | None in the repo; not specified |
| Front end | None |
| Level shifting | For targets below the chip's I/O voltage [repo: docs/protocols.md]; part not specified |
| Clock profile | 50 MHz, TCK 6.25 MHz [repo: programs/jtag.pasm] |
| Pmod mapping | No recommended pinout exists |
| Checked | `test/test_jtag.py`: `TDI, TMS, TCK, TDO_PIN = 0, 1, 2, 9`. Matches `info.yaml` |

### SWD

| Item | Value |
| --- | --- |
| Program | `programs/swd.pasm`, host (probe); one engine |
| Pins | SWDIO: P0, Bidir 1. SWCLK: P2, Bidir 3. P1 unused |
| Pull-ups | SWDIO to 3.3 V [repo: docs/protocols.md]; value not specified |
| Front end | None |
| Level shifting | For targets below the chip's I/O voltage [repo: docs/protocols.md]; part not specified. SWDIO reverses direction, so the shifter must be bidirectional |
| Clock profile | 50 MHz, SWCLK 6.25 MHz [repo: programs/swd.pasm] |
| Pmod mapping | No recommended pinout exists |
| Checked | `test/test_swd.py`: `SWDIO, SWCLK = 0, 2`. Matches `info.yaml` |

### PS/2

| Item | Value |
| --- | --- |
| Program | `programs/ps2_device.pasm`, `programs/ps2_host.pasm`; one engine |
| Pins | CLK: P3, Bidir 4. DATA: P4, Bidir 7. Fixed by the program: `.define CLK 3` in both, `.define DATA 4` in `ps2_device`; moving them means editing and reassembling. Both open-drain |
| Pull-ups | CLK and DATA [repo: docs/protocols.md]; value not specified in the repo. 1-10 kΩ [std: [Chapweske, PS/2 protocol](https://www.burtonsys.com/ps2_chapweske.htm)] |
| Front end | None beyond the level shifter |
| Level shifting | Needed for a real PS/2 device, which runs from 4.5-5.5 V [std: Chapweske]. Use a bidirectional open-drain shifter with pull-ups on both sides [std: NXP AN10441]. Never pull P3 or P4 to 5 V; 5 V tolerance of the pads is not specified |
| Device power | 5 V from VBUS on the Management header. Device current not specified |
| Clock profile | 50 MHz. CLKDIV 249 (device), 199 (host) [repo: program comments] |
| Pmod mapping | No recommended pinout exists |
| Checked | `test/test_ps2.py`: `CLK, DATA = 3, 4`. Matches the `.define` lines and `info.yaml` |

### CAN

| Item | Value |
| --- | --- |
| Program | `programs/can_rx.pasm` (listen-only, one engine), or `can_node.pasm` on engine 0 with `can_line.pasm` on engine 1 (both engines, 511 of 512 words) |
| Pins | TXD: P0, Bidir 1. RXD: P8, Input 9. RXD is fixed by the program: `.define RXD 8` |
| Pull-ups | TXD: a pull-up keeps the transceiver recessive while P0 is released, since low on TXD means dominant. Needed because P0 floats after reset and the RP2350 pull-down would pull it low; value not specified. RXD: driven by the transceiver |
| Transceiver | "A CAN transceiver (TXD, RXD)" [repo: docs/protocols.md]; part not specified in the repo. 3.3 V part: SN65HVD230, 3.0-3.6 V, 1 Mbit/s, ISO 11898-2, RS pin low for high-speed mode [std: TI SLOS346O] |
| Bus | 120 Ω across CANH and CANL at each end of the bus [std: ISO 11898-2] |
| Level shifting | None with a 3.3 V transceiver. A 5 V transceiver's RXD must be shifted down; part not specified |
| Clock profile | 50 MHz, 1 Mbit/s, 50 cycles per bit [repo: programs/can_line.pasm] |
| Limit | Tests model 80 ns from TXD through the bus to RXD (`LOOP_DELAY_NS`). The SN65HVD230 driver delay alone is listed as up to 120 ns at RS = 0 V [std: TI SLOS346O], so a real loop may be longer; this is untested |
| Pmod mapping | No recommended pinout exists. TXD and RXD span the Bidir and Input headers |
| Checked | `test/test_can.py`: `TXD, RXD = 0, 8`. Matches the `.define` lines, `can_line` and `can_node` comments, and `info.yaml` |

### Low-speed USB

Pad-speed sensitive: **unverified**.

| Item | Value |
| --- | --- |
| Program | `programs/usb_ls_host.pasm` on engine 0, `programs/usb_ls_rx.pasm` on engine 1; both engines. Host role only |
| Pins | D+: P0, Bidir 1. D-: P1, Bidir 2. Receive enable: P12, an internal link to the bit state machine. Leave Output 10 unconnected; it still toggles the display's segment |
| Pull-downs | 15 kΩ from D+ to ground and D- to ground at the host end [repo: docs/protocols.md] |
| Pull-up | 1.5 kΩ on D- at the device end [repo: docs/protocols.md]. It belongs to the device; do not add it to the board |
| VBUS | 5 V to the device, from VBUS on the Management header [std: USB 2.0]. Shared 350 mA fuse; device current not specified |
| Front end | No USB transceiver. Series resistors and edge-shaping capacitors: not specified. Low-speed edges must take 75-300 ns into 200-600 pF [std: USB 2.0, via TI TUSB2036 datasheet], and whether a bare 3.3 V pad does this is unknown |
| Receiver | The chip reads D+ alone (`G2_IN0` = D+, `wait 1 pin 0`), with no differential receiver. Thresholds not specified |
| Level shifting | None |
| Clock profile | **48 MHz only**: a 1.5 Mbit/s bit is 32 cycles [repo: docs/protocols.md]. Do not use the 50 MHz profile |
| Pmod mapping | No recommended pinout exists |
| Checked | `test/test_usb.py`: `DP, DM, RX_EN = 0, 1, 12`. Matches `usb_ls_host` comments and `info.yaml` |

### 10BASE-T

Pad-speed sensitive: **unverified**.

| Item | Value |
| --- | --- |
| Program | `programs/eth10_tx.pasm` on engine 0, `programs/eth10_rx.pasm` on engine 1; both engines. Half duplex [repo: docs/protocols.md] |
| Pins | TD+: P2, Bidir 3. TD-: P3, Bidir 4. RX: P8, Input 9. P4 is taken as the internal phase signal S, drive off: leave Bidir 7 unconnected and give P4 to nothing else |
| Pull-ups | None |
| Front end | Isolation magnetics, transmit resistor network and receive comparator [repo: docs/protocols.md]. Part numbers and resistor values: not specified |
| Transmit target | 2.2-2.8 V peak differential into 100 Ω [std: IEEE 802.3 14.3.1.2.1, via [Hilscher note](https://hilscher.atlassian.net/wiki/spaces/NETX/pages/144190605/PHY%3A+In+10BT+mode+differential+output+voltage+harmonics+requirement+not+fulfilled)]. Reaching it from 3.3 V takes about 25 mA from the pads, whose rating is not specified; an external line driver may be needed |
| Receive target | The comparator output must follow RD+ minus RD- and stay low when idle, as in `test_eth.py`. Reject below 300 mV peak, accept above 585 mV [std: PHY datasheets such as Intel 82555 and GD82559ER, following 802.3 clause 14; the clause text itself was not read]. Comparator part: not specified |
| Level shifting | None for logic; the comparator output must be 3.3 V |
| Clock profile | 50 MHz only [repo: docs/design-review.md]. Half-cycle output timing and double sampling both depend on the duty cycle of `clk` [repo: docs/spec.md] |
| Pmod mapping | No recommended pinout exists |
| Checked | `test/test_eth.py`: `TDP, TDM, PHASE, RX = 2, 3, 4, 8`. Matches `eth10_tx` comments and `info.yaml` |

### Capture and replay

| Item | Value |
| --- | --- |
| Program | `programs/capture.pasm` on engine 0, `programs/replay.pasm` on engine 1; both engines |
| Pins | Capture: P8, Input 9. Replay: P10, Output 8. Either program can take another pin of the right direction; these are the tested ones |
| Pull-ups | None added by this design. The line under test must have its own idle level; P8 has no pull |
| Front end | None. The 8-cycle glitch filter on the capture pin is on chip [repo: programs/capture.pasm] |
| Level shifting | For other voltages; part not specified. Connect ground to the device under test |
| Clock profile | 50 MHz, one-cycle resolution, 20 ns [repo: docs/protocols.md] |
| Pmod mapping | No recommended pinout exists |
| Checked | `test/test_waveform.py`: `PIN_IN, PIN_OUT = 8, 10`, comment `ui[6]`, `uo[5]`. Matches `info.yaml` |

### Held-out protocols

All three use engine 0 at 50 MHz and have no recommended Pmod pinout.
Values are from `docs/heldout-protocols.md`, and pins were checked against `test/test_heldout.py` (`WS_DIN = 10`, `OW_DQ = 3`, `I2S_BCLK, I2S_LRCLK, I2S_SD = 10, 11, 12`) and `info.yaml`.

| Protocol | Program | Pins | Pull-ups | Front end and level shifting |
| --- | --- | --- | --- | --- |
| WS2812 | `programs/ws2812.pasm` | DIN: P10, Output 8; drive "always" | None | Level shifter required at 5 V supply: input needs 3.5 V, above 3.3 V I/O. Part not specified |
| 1-Wire | `programs/onewire.pasm` | DQ: P3, Bidir 4; open-drain | 5 kΩ on DQ, from the DS18B20 datasheet | None. No strong pull-up, so no parasite-powered devices |
| I2S | `programs/i2s.pasm` | BCLK: P10, Output 8. LRCLK: P11, Output 9. SD: P12, Output 10; drive "always" | None | None at 3.3 V. The TT Audio Pmod pinout is a different interface. The program fixes LRCLK = BCLK + 1 |

## Host loading sequence

No Host firmware for the demo board exists in this repository.
The sequence below is what `test/chip.py` does through the top-level pins in simulation, and firmware must do the same.
The RP2350 hardware SPI blocks do not fit: `HDO0` is on GPIO33, which is not an SPI receive pin [board: README]. Use PIO or software.

Port rules [repo: docs/spec.md]: mode 0, `HSCK` idles low, both sides sample on the rising edge, `HSCK` high and low at least 4 `clk` cycles each, so at most f_clk / 8.
Data moves most significant bit first.
The first byte is the command: bit 7 set for read, bits 6:0 the address.

1. **Prepare.** Set the DIP switches off. Keep `HCS_N` high and `HSCK` low.
2. **Select and clock.** Select the project and start the clock with `rst_n` held low. See [clock profiles](#clock-profiles).
3. **Reset.** Release `rst_n` and wait for it to rise. See [reset](#reset).
4. **Identity.** In 1-bit mode send `0x80, 0x00`; the second byte returned is `0x50` (`ID`). Send `0x81, 0x00`; the second byte is `0x05` (`VERSION`). Stop if either differs.
5. **Width (optional).** Write `0x09, 0x01` to set `HOSTCFG.QUAD`. It takes effect at the next `HCS_N` fall.
6. **Instruction memory.** Write `IMEM_ADDR_H` (0x1D) and `IMEM_ADDR` (0x0A), then send every program word to `IMEM_DATA` (0x0B), low byte first, in one transaction. The port address stays fixed and the write address steps. Write every word the program uses; never rely on memory contents.
7. **Bit state machine.** For CAN, USB and Ethernet, write the table through `G2_ADDR` and `G2_DATA`, then `G2_IN0`, `G2_IN1`, `G2_STATE` and `G2_CTRL`. Tables are in `tools/pe/g2.py`.
8. **Clock recovery.** For Ethernet receive, write `NEGSEL` and `CR_CTRL` to `CR_SAMPLE` (`tools/pe/proto/eth.py`).
9. **Engine configuration.** Write `CLKDIV`, wrap bounds, `SHIFTCTRL`, `THRESH`, `OUTPIN`, `SETPIN`, `INPIN`, `SIDEPIN`, `EXECCFG` and `STATUSCFG`. Each program's comments give its values, and `Chip.setup_engine` writes them in order. Programs using the LFSR or the capture unit also need `LFSRCFG`, `LFSR_POLY` or `CAPCFG`, as in `test/test_can.py` and `test/test_waveform.py`.
10. **Start address.** Pulse the engine's restart bit in `CMD`, then force `set page` and a `jmp` to the program start through `INSTR`.
11. **Idle levels.** Force `set pins` and `set pindirs` so the pin registers hold the idle state.
12. **Pins.** Write `PINCFGn` last, so each pad turns on already at its idle level. `can_node` also needs `FLAGS` cleared and buffer RAM entry 4 at 0 first [repo: docs/protocols.md]; `test/test_can.py` writes both.
13. **Enable.** Write the engine mask to `CTRL` (0x02).
14. **Check.** Read `STICKY` (0x07); nonzero means data was lost.

## Reset

Reset is `rst_n`, asynchronous on assertion and synchronous on release [repo: docs/spec.md], so `clk` must run for it to finish.
The Host or the board's Project RESET button drives it, and `tt.reset_project(True)` holds it low [board: `demoboard.py`].

The board has 10 kΩ to 3.3 V, then 1 kΩ to the `rst_n` net with 100 nF to ground [board: schematic].
The firmware releases reset by making GPIO14 an input, so `rst_n` rises through about 11 kΩ, a time constant near 1.1 ms.
Wait for that, then poll `ID` until it reads `0x50`. A read during reset does not return it.
The minimum low time is not specified for silicon; tests hold it for 5 cycles.

After reset [repo: docs/spec.md]:

- Both engines are disabled, and every pin has drive mode "off".
- `uio` pins are released, and a pin held by the chip lets go as soon as `rst_n` falls (`test_host.test_reset_state`).
- P10-P12, `HDO[3:0]` and `HIRQ` drive 0.
- Host port is back in 1-bit mode, and every register is at its default.

**Treat instruction memory as undefined after reset and load it every time.**
The sources disagree: `src/pe_store.v` and `docs/spec.md` clear it to 0, while `README.md` and `docs/design-review.md` call it undefined.
Clearing has not been shown on silicon.
The rule above is safe either way, and the same goes for the bit state machine table and the buffers.
Never enable an engine before step 6 of the loading sequence.

## Reload after deselection

Deselecting the design on the demo board powers it down and loses its state [repo: docs/competition-update.md].
Nothing survives: programs, tables, configuration, buffers, flags, the 4-bit port width, and the CAN error counters.

To deselect and come back:

1. Read anything needed from the receive queues **before** deselecting; it cannot be recovered afterwards.
2. Select the project. Bidirectional GPIOs are reset to inputs when a project is enabled [board: `config.md`], and the firmware may reset the RP2350 clock to its default [board: `demoboard.py`], so set the clock again after selecting.
3. Release GPIO23 and GPIO24 again if P8 or P9 are in use.
4. Run the whole [loading sequence](#host-loading-sequence) from reset. Do not skip any step because the design "was configured before".

A CAN node comes back with its error counters at 0 and integrates into the bus again.
Pad levels while the design is deselected are not specified, so use external pull-ups to hold a line idle, and expect a glitch on anything sensitive, such as a WS2812 strip.
Power loss is not simulated. Reset tests cover the logic, not this path.

## Clock profiles

| Profile | Protocols | Source |
| --- | --- | --- |
| 50 MHz | UART, SPI, I2C, JTAG, SWD, PS/2, CAN, 10BASE-T, capture and replay, WS2812, 1-Wire, I2S | `docs/design-review.md`, test `PROFILE` constants |
| 48 MHz | Low-speed USB only | `test/test_usb.py`, `tools/pe/proto/usb.py` |

There is one clock per board setting, and no PLL or fractional divider on chip, so USB cannot run beside a 50 MHz program.
UART, SPI, I2C, JTAG, SWD and PS/2 can in principle run at 48 MHz with changed `CLKDIV` values [repo: docs/design-review.md], but they have only been tested at 50 MHz.
CAN, Ethernet and the held-out programs have fixed cycle counts: at the wrong clock their negative-control tests fail.
`info.yaml` records 50 MHz only.

The RP2350 generates the clock with PWM on GPIO16 [board: `demoboard.py`].
Set it from the firmware config:

```ini
[tt_um_jedinakdev_protocol_emulator]
mode = ASIC_RP_CONTROL
start_in_reset = yes
# HCS_N high, HSCK low
ui_in = 0x01
uio_oe_pico = 0
# 48e6 for low-speed USB
clock_frequency = 50e6
```

Or at run time with `tt.clock_project_PWM(50_000_000)`, or `48_000_000` for USB.
The firmware then picks a system clock with an even divisor to the project clock, which by reading the code gives 100 MHz or 96 MHz and a 50% duty cycle.
This snippet has not been run.
Confirm the frequency and duty cycle on a scope; Ethernet depends on the duty cycle.
Do not press the Project CLOCK button while the PWM runs.

To change profile [repo: docs/design-review.md]:

1. Write `CTRL = 0`.
2. Hold `rst_n` low and change the clock.
3. Release reset.
4. Run the whole [loading sequence](#host-loading-sequence).

## Known unknowns

At the time of the organizer email, CMOS5L had no returned silicon and no characterized pad numbers; Tiny Tapeout was checking for numbers from the similar SG13G2 process [repo: docs/competition-update.md].
Every item below stays unverified until pad data or a chip exists.

| Item | Unknown | Most exposed |
| --- | --- | --- |
| Pad output | Drive current, rise and fall time, maximum toggle rate | **10BASE-T**, **USB**, SPI at 12.5 MHz |
| Pad input | Thresholds, hysteresis, capacitance, 5 V tolerance | **USB** single-ended receive, I2C, PS/2 |
| Tiny Tapeout mux path | Delay and skew on every signal between pad and project | **10BASE-T** half-cycle timing, SPI bypass, capture |
| `clk` duty cycle at the chip | PWM, 47 Ω and mux path; no measurement | **10BASE-T** transmit and receive |
| Chip timing | 50 MHz is not closed at every corner: +5.48 ns typical, -3.16 ns slow setup slack on the current 8x4 source, with final checks open [repo: docs/info.md] | All 50 MHz protocols |
| Ethernet front end | No part numbers or resistor values; pad current may not reach the 802.3 level | **10BASE-T** |
| USB edge shaping | No series or capacitor values; simulation has ideal edges | **USB** |
| Board loading | RP2350 pads, headers, display and wiring add capacitance that no one has measured | Every fast line |
| Pads while deselected | Level not specified | WS2812, CAN TXD |
| Board revision | This guide reads schematic v3.2; the winners' board is "the latest" ETR board [repo: docs/competition-update.md] | All board facts |
| RP2350 stepping | Erratum E9 status of the fitted part | P0-P9 idle levels |

## Source disagreements

Found while checking pins.
Pin numbers themselves agree across `info.yaml`, RTL, program comments and tests; these are the differences that remain.

| Sources | Difference | This guide |
| --- | --- | --- |
| `docs/spec.md`, `src/pe_store.v` against `README.md`, `docs/design-review.md` | Instruction memory cleared by reset, or undefined | Treats it as undefined |
| `docs/info.md`, `model/` against the ETR board | RP2040 as Host; the ETR board has an RP2350B. The TT pinouts page also still says RP2040 | Uses RP2350 |
| `docs/info.md`, `docs/spec.md` against `docs/protocols.md`, `test/test_usb.py` | USB listed among lines needing pull-ups or idling high; a host needs pull-downs, and the pull-up is in the device | Uses pull-downs |
| `docs/spec.md`, `docs/heldout-protocols.md` | `ui[7:6]` listed beside `P8`, `P9`, and `uo[7:5]` beside P10-P12, which reads backwards. RTL and `info.yaml` have `ui[6]` = P8, `uo[5]` = P10 | Uses RTL order |
| `docs/protocols.md` against PS/2 devices | Lists pull-ups only, with no mention of 5 V devices | Adds a level shifter |
| `docs/protocols.md` against `programs/eth10_tx.pasm` | External hardware does not mention that P4 is taken | Reserves P4 |
| Repository docs against the board | No document says P8 and P9 are shared with RP2350 outputs | See [shared pins](#shared-pins-read-this-before-wiring) |
| Tested pins against TT Pmod pinouts | Tested I2C and SPI pins do not match, and both programs' adjacent-pin rules rule out a direct fit | One-wire workarounds, untested |
