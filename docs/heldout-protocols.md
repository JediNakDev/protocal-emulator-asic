# Held-out protocols

Flexibility is the main judging criterion, and every other program in `programs/` targets a named competition protocol or capture and replay.
These three protocols were chosen after the RTL was finished and were written with no change to `src/`, as direct evidence that the chip can be reprogrammed for protocols it was not designed for.

**All results below are from RTL simulation only.**
They do not cover pad timing, line capacitance, rise times, metastability or the duty cycle of `clk`.
No held-out protocol has run on silicon, on a gate-level netlist or against a real device.

## Result

All three work: each has a program loaded through the top-level Host pins, an independent peer in `test/test_heldout.py` that passes, and at least one negative control.
None needed an RTL change.

| Protocol | Role | Words | Clock | Engines | Pins | Outcome |
| --- | --- | --- | --- | --- | --- | --- |
| WS2812 | Transmitter, GRB, 24 bits per pixel | 10 | 50 MHz, CLKDIV 2 | 1 | 1 output | Passes; 793.7 kHz, -0.79% from 800 kHz |
| 1-Wire | Controller, standard speed, reset, presence, write and read slots, Read ROM | 19 | 50 MHz, CLKDIV 149 | 1 | 1 open-drain | Passes; CRC8 computed on chip, verdict read by the Host |
| I2S | Controller transmitter, stereo, 16 bits, 32 clocks per frame | 12 | 50 MHz, CLKDIV 2 | 1 | 3 outputs | Passes; 47,348.5 Hz, -1.36% from 48 kHz |

Each uses engine 0 alone, so engine 1, the bit state machine (G2), clock recovery (G7) and edge capture (G3) stay free.
Running two of these at once was not tested.

## Test results

```sh
make test COCOTB_TEST_MODULES=test_heldout
```

`TESTS=12 PASS=12 FAIL=0 SKIP=0` on Icarus Verilog 13.0 with the latch instruction store, the default, and the same with `FLOP=yes`.
`test_heldout` is not in the default module list of `test/Makefile`, so `make test` and CI do not run it yet.

| Test | Shows |
| --- | --- |
| `test_ws2812_pixels` | Three frames back to back, one of 12 pixels (longer than the queue); all times inside the datasheet; pixels as a chain latches them |
| `test_ws2812_wrong_clock` | Negative control: at 40 MHz the peer rejects a 1050 ns low |
| `test_i2s_transmit` | 16 stereo frames, twice the queue; samples, channels, one-clock LRCLK lead, all UM11732 limits, no BCLK pause |
| `test_i2s_underrun` | Host stops between frames and between channels; BCLK pauses once, no sample lost or misaligned |
| `test_i2s_wrong_clock` | Negative control: at 40 MHz the peer rejects the BCLK period |
| `test_i2s_left_justified_rejected` | Negative control: a program with LRCLK one clock late gives a 17-bit word |
| `test_onewire_crc8_vector` | The peer's CRC8 matches the A2 hex ROM example of Application Note 27 |
| `test_onewire_read_rom` | Read ROM at both edges of the datasheet's presence and read-data timing; open-drain, no contention, CRC residue 0 |
| `test_onewire_sample_point` | The read sample found at the pin: between 11.9 and 12.1 µs after the slot starts |
| `test_onewire_bad_crc` | Negative control: one flipped ROM bit leaves a nonzero CRC residue |
| `test_onewire_no_device_and_short` | An empty bus reads no presence; a bus held low reads low at both samples |
| `test_onewire_wrong_clock` | Negative controls: at 40 MHz presence is missed; at 62.5 MHz a write 0 is only 50 µs low |

The peers share no code with the programs and measure the wire in nanoseconds of simulated time, with limits taken from the sources below.

## WS2812

Program: `programs/ws2812.pasm`.
Pin P10 (`uo[5]`), which drives 0 from reset, with drive mode "always".
Hardware used: 1-pin side-set, autopull at 16, the 8-word transmit-only buffer, clock divider, `mov x, ::x` for the long gap count.

The Host sends the frame length in bits, then the pixels packed into 16-bit words, because the shift registers are 16 bits and a pixel is 24.
The chip times every bit and makes the reset gap itself.

| Parameter | Datasheet | Achieved |
| --- | --- | --- |
| T0H | 0.4 µs ± 150 ns | 420 ns |
| T0L | 0.85 µs ± 150 ns | 840 ns |
| T1H | 0.85 µs ± 150 ns | 840 ns |
| T1L | 0.4 µs ± 150 ns | 420 ns |
| TH + TL | 1.25 µs ± 600 ns | 1260 ns, every bit |
| Bit rate | 800 kHz | 793.65 kHz (-0.79%) |
| RES | above 50 µs | at least 369.9 µs |

Source: Worldsemi, [WS2812B Datasheet and Specifications V1.0, Jan 2016](https://cdn.shopify.com/s/files/1/0174/1800/files/WS2812B-LED-datasheet.pdf), data transfer time table and 24-bit composition (G7 first).
Other copies of the sheet differ slightly (for example T1H 0.8 µs, T1L 0.45 µs), and the 420/840 ns shape is inside those windows too.
The 369.9 µs gap is also above the 280 µs quoted for some newer parts; that figure comes from secondary sources and was not confirmed in a WS2812B datasheet.

External parts: at 5 V supply the input needs 0.7 VDD, or 3.5 V, which is above the demo board's 3.3 V I/O, so a level shifter is required; the simulated peer does not model levels.

Limits:

- 3 cycles per tick gives 63 cycles per bit, so 800 kHz is not reached exactly; the error is inside the datasheet tolerance.
- A frame holds up to 65,536 bits, 2,730 pixels; the longest tested is 12 pixels.
- The Host must keep the queue fed: 8 words cover 160 µs. If it runs dry DIN holds low, and a pause of 50 µs or more on the wire ends the frame early at the pixels. This case is not tested.

## 1-Wire

Program: `programs/onewire.pasm`.
Pin P3 (`uio[3]`), open-drain, DQ for `out`, `set` and `in`.
Hardware used: open-drain drive, the input synchronizer, clock divider (3 µs ticks), G1 in right-shift CRC mode with polynomial 0x8C on `in` bits.

The program offers two operations: a reset with presence detect, and 1 to 8 time slots with a level for each.
A write 1 and a read are the same slot, so every slot is sampled and each word returns what the bus showed, which also echoes writes.
The Host builds Read ROM (0x33) from those: it clears `LFSR_VALUE` after the command echo, reads eight bytes, and reads `LFSR_VALUE`, which is 0 for a good ROM code.
So the CRC is computed on chip by G1, and the verdict is the Host's.

| Parameter | Limit | AN126 value | Achieved |
| --- | --- | --- | --- |
| Reset low (tRSTL) | at least 480 µs | 480 µs | 486.00 µs |
| Presence sample after release | low from 60 to 75 µs in every case below | 70 µs | 69 µs by cycle count; passes at both edges below |
| Reset high (tRSTH) | at least 480 µs | 480 µs (70 + 410) | 504.00 µs |
| Presence pulse accepted | tPDHIGH 15-60 µs, tPDLOW 60-240 µs | | 15 + 60 µs and 60 + 240 µs tested |
| Write 0 low (tLOW0) | 60-120 µs | 60 µs | 63.00 µs |
| Write 1 and read low (tLOW1) | 1-15 µs | 6 µs | 6.00 µs |
| Read sample | within 15 µs of the falling edge (tRDV) | 15 µs | 12 µs, found between 11.9 and 12.1 µs at the pin |
| Slot (tSLOT + tREC) | at least 60 + 1 µs | 70 µs | 72.00 µs |
| Recovery (tREC) | at least 1 µs | 10 µs | 9 µs in the write 0 slot |
| Level in a write slot | stable from 15 to 60 µs | | checked on every write slot |

Sources: Maxim, [Application Note 126, 1-Wire Communication Through Software](https://www.analog.com/en/resources/technical-articles/1wire-communication-through-software.html), 2002, for the master slot shape;
the AC table of the [DS18S20 datasheet](https://static.rapidonline.com/pdf/50-3678.pdf) for the limits, and the [DS18B20 datasheet](https://cdn.shopify.com/s/files/1/1509/1638/files/DS18B20_KY-001_datasheet.pdf) for the sampling window, the CRC and Read ROM (33h).
Both datasheets were read from third-party copies, and AN126 from a university copy of the 2002 note, as the Analog Devices site did not respond; of Application Note 27 only excerpts were read.
The CRC is x^8 + x^5 + x^4 + 1, with [Application Note 27](https://www.analog.com/en/resources/technical-articles/understanding-and-using-cyclic-redundancy-checks-with-maxim-1wire-and-ibutton-products.html) for the check value.

The times differ from AN126 on purpose.
The 3 µs tick rounds them; each is chosen on the safe side of its limit.
AN126's 15 µs sample would sit exactly on the end of tRDV, while the chip's input path and any real rise time need margin, so the program samples at 12 µs, which leaves 6 µs after release for the line to rise.

External parts: a pull-up on DQ, 5 kΩ in the DS18B20 datasheet.
The testbench pull-up rises instantly, so the rise-time margin above is untested.

Limits:

- Standard speed only; overdrive is not implemented.
- No strong pull-up for parasite-powered devices, since an open-drain pin never drives high.
- Only Read ROM runs against the peer. Slots of 1 to 8 are tested, with the first ROM byte read as 3 + 5, but Search ROM with several devices is not.
- No single instruction clears G1, so the Host clears it with a register write and must wait for the command echo first. A program could read the residue with `in lfsrl` and shift G1 clear with `out lfsr`, at the cost of extra words, so the verdict stays on the Host here.

## I2S

Program: `programs/i2s.pasm`.
Pins P10 (BCLK), P11 (LRCLK), P12 (SD), `uo[7:5]`, drive mode "always".
Hardware used: 2-pin side-set, `out pins`, autopull at 16, the 8-word transmit-only buffer, clock divider.

| Parameter | UM11732 | Achieved |
| --- | --- | --- |
| Word select | LRCLK low for left, changes one clock before the MSB | Yes; checked at every word |
| Data | MSB first, latched on the rising edge | Yes, 16 bits per channel |
| Clock period T | | 660 ns, BCLK 1.515 MHz |
| HIGH tHC | at least 0.35T (231 ns) | 360 ns (0.545T) |
| LOW tLC | at least 0.35T (231 ns) | 300 ns (0.455T) |
| Transmitter delay tdtr | at most 0.8T (528 ns) | 0 ns, SD and LRCLK change with the falling edge |
| Receiver setup tsr | at least 0.2T (132 ns) | 300 ns |
| Hold thtr, thr | at least 0 | 360 ns, SD holds through the high phase |

Source: NXP, [UM11732 I2S bus specification, Rev. 3.0, 17 February 2022](https://www.nxp.com/docs/en/user-manual/UM11732.pdf).

**Rate:** 50 MHz / (33 × 32) = 47,348.5 Hz, measured from LRCLK, which is 651.5 Hz or 1.36% below 48 kHz.
BCLK takes a whole number of `clk` cycles, and 48 kHz needs 32.55.

| Cycles per BCLK | Frame rate | Against |
| --- | --- | --- |
| 32 | 48,828.1 Hz | +1.73% from 48 kHz |
| 33 (used) | 47,348.5 Hz | -1.36% from 48 kHz |
| 35 | 44,642.9 Hz | +1.23% from 44.1 kHz |

Other clocks do better by arithmetic, for example 26 cycles at 40 MHz gives 48,077 Hz (+0.16%) and 34 cycles at 48 MHz gives 44,118 Hz (+0.04%); both need different delays in the program and neither was tested.

External parts: none for the three signals at 3.3 V.

Limits:

- A rate error of 1.36% at 50 MHz is real for audio: a DAC that follows BCLK plays slightly flat. The cause is that the chip has no fractional clock divider.
- The Host must supply 2 words per frame, 189 kB/s; the queue covers 84 µs. An underrun pauses BCLK instead of sending silence, so frames stay aligned, but a DAC may click or lose lock.
- With 2-pin side-set the delay field is 3 bits, 8 ticks per instruction, so CLKDIV must prescale: BCLK takes a multiple of 11 cycles in this program.
- No MCLK output, no receive direction, no 24-bit or 64-clock frames.

## What this says about flexibility

Three protocols the design never targeted took 41 of the 512 instruction words in total and no RTL change.
Their shapes differ: a single-wire pulse-width code with no clock, a half-duplex open-drain bus with a device answering inside a slot, and a synchronous three-wire stream.

The limits found are of degree:

- The missing fractional clock divider costs I2S its exact rate and WS2812 its exact 800 kHz. Only the I2S error matters in practice.
- Autopull and the 16-bit shift registers need 24-bit pixels packed across words, which the Host does.
- G1 has no one-instruction clear, which leaves the 1-Wire CRC verdict on the Host to save words.
- Every stream depends on the Host keeping up, since the buffers hold 8 words.

No specification or RTL error was found.
