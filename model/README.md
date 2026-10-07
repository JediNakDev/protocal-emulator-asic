# Protocol engine C model

A cycle-accurate C model of the programmable protocol engine and the board it plugs into.
UART, SPI and I2C (required), plus USB low speed and 10BASE-T transmit (stretch goals), run as **programs** on the engine.
Models of real parts check them through the `uio` pins only.
This directory preserves the earlier four-engine architecture exploration.
The implemented RTL uses two engines with shared programmable primitives, as defined in [../docs/spec.md](../docs/spec.md).
The ISA, Host interface and resource budget differ, so this model's results do not verify the current RTL.

```sh
make model          # from the repository root, or: make -C model test
```

The suite reports every criterion in [CRITERIA.md](CRITERIA.md) as PASS or FAIL, and exits non-zero if any fails or is never exercised.
It runs in about two seconds.

## Design in one paragraph

Four identical engines share eight `uio` pads, the host bus and nothing else.
Each engine is a small timed bit mover: 32 instructions of 16 bits, 8-bit shift registers to and from 4-entry FIFOs, two 16-bit scratch registers, and one 16-bit timer.
The RP2040 on the demo board does whatever needs no cycle-exact timing: framing, CRCs, USB NRZI and bit stuffing.
Three features let the small instruction set cover all five protocols:

- `[tick]` makes a bit last exactly one timer period.
- **Edge re-phasing** restarts the timer on edges of a pin someone else drives (clock recovery).
- `xch` plus a **differential OUT pin** shifts a bit out and in, or drives a complementary pair, in a single cycle.

The clock is 40 MHz, the lowest rate that gives 10BASE-T an integer number of cycles (2) per half-bit.

## Files

| File | Contents |
| --- | --- |
| `pe.h` | Limits, instruction encoding, configuration and host-bus definitions, chip and board state |
| `chip.c` | Engines (`core_step`), host bus, pad logic with 2-FF synchronisers |
| `board.c` | Wire resolution with pull-ups and pull-downs, contention checks, cycle loop |
| `asm.c` | Two-pass assembler for `.pasm` sources |
| `prim.h` | Measurement primitives shared by the peers: timing spans, wire edges, bit grids |
| `host.c` | RP2040 host model: bus transactions, program loader, FIFO streaming, length-prefixed frames |
| `proto.c` | CRC-5/16/32, USB line coding, Ethernet/IPv4/UDP framing (host side, checked against published values) |
| `peers.c` | USB-UART bridge, W25Q128JV flash, ADT7420 sensor (`peers.h` also has `peer_drive`) |
| `peers_net.c` | USB low-speed keyboard, 10BASE-T receiver |
| `programs/*.pasm` | Protocol firmware |
| `test_main.c` | Test suite, organised by criterion |

## How the environment matches the hardware

| Constraint | Model |
| --- | --- |
| `ui_in[8]` read-only, `uo_out[8]` write-only | Host bus only (see below) |
| `uio[8]` with `out`, `oe`, `in` | Per-pad registered `out`/`oe`; inputs are read back from the wire |
| No open-drain pads | Per-logical-pin `od` bit: value 0 drives low, value 1 releases (`oe = 0`) |
| External pulls | An undriven wire reads 1, or 0 where the board has a pull-down (USB D+). Opposing drivers are reported. |
| One clock | 40 MHz |
| 2-FF input synchronisers | Engines read `wire(t-2)`; the host bus is synchronised the same way |
| Registered outputs | A pin written in cycle `t` changes on the wire in cycle `t+1` |
| One write per pin per cycle | Writing a logical pin twice in one instruction is an error, as is two engines driving one pad |
| Peer response time | Peers drive their reply one cycle after they observe an edge |
| No C state in the hot path | `core_step()` keeps nothing across cycles outside `pe_core`; every field is a flip-flop |

### Cycle order

```
cycle t: wire(t) = resolve(pads registered at t-1, peer drives set at t-1, pulls)
         peers observe wire(t), set their drive for t+1
         chip:   host command (if the synchronised strobe toggled)
                 each engine executes one cycle, reading uio = wire(t-2)
                 pad registers <- engine pin state            (visible at t+1)
```

## Instruction set

Every instruction is 16 bits: `[15:13]` opcode, `[12]` side-set enable, `[11]` side-set value, `[10:8]` delay (0–6 cycles, or 7 = wait for the next timer tick), `[7:0]` arguments.
An instruction takes one cycle plus its delay.

| Opcode | Syntax | Effect |
| --- | --- | --- |
| 0 JMP | `jmp [cond] label` | cond: always, `!x`, `x--`, `!y`, `y--`, `pin`, `!pin`, `!osre`. `x--`/`y--` test for non-zero, then decrement. |
| 1 WAIT | `wait 0\|1 pin p` | Stall until the synchronised pin matches |
| 2 IN | `in pins\|x\|y\|null, n` | Shift `n` bits into ISR; autopush at 8 |
| 3 OUT | `out pins\|x\|y\|null\|pindirs\|pc, n` | Shift `n` bits out of OSR. `out x, n` shifts into X (`X = X << n \| bits`), so two OUTs load 16 bits. |
| 4 XCH | `xch [n]` | `out pins, n` and `in pins, n` in the same cycle |
| 5 PUSH/PULL | `push [noblock]`, `pull [noblock]` | Explicit FIFO transfer. `pull noblock` on an empty FIFO leaves the OSR alone, so `jmp !osre` can test for data. |
| 6 MOV | `mov dst, [~]src` | dst: `pins x y pc isr osr`; src: `pins x y null isr osr`. Source `pins` reads all four logical pins. |
| 7 SET | `set pin p, v`; `set pins\|pindirs\|x\|y\|flag\|timer, v` | 5-bit immediate. `set timer` restarts the timer with T0 or T1. |

**Timer.**
The 16-bit timer ticks every T0 cycles.
`[tick]` holds the engine after an instruction until the next tick, so a bit lasts exactly T0 cycles however many instructions it takes.

**Edge re-phasing.**
With `.resync rise|fall|both`, an edge on the JMP pin restarts the timer with T1, but only while this engine is not driving that pin.
UART RX re-centres on every edge.
I2C measures SCL high time from the moment a stretching target releases SCL.
USB re-centres its sampling on every transition and ignores its own transmissions.

**FIFO behaviour.**
Autopull refills the OSR in the same cycle an OUT empties it, so byte boundaries add no gap (10BASE-T relies on this).
If the FIFO is empty, the next OUT stalls.
An instruction blocked by a FIFO has no effect, including its side-set, until it can complete.
A blocked WAIT applies its side-set immediately, because the condition usually depends on it.

**Differential OUT pin.**
With `.out pin diff`, OUT and MOV to the out pin drive the inverse on the next logical pin in the same cycle.
`set pins` still writes both pins independently, for USB SE0 and Ethernet idle.

### Configuration bytes (per engine)

| Byte | Contents |
| --- | --- |
| 0–3 | Logical pin `i`: `[2:0]` uio pad, `[3]` open-drain, `[4]` connected (unconnected pins read 0 and never drive) |
| 4 | `[1:0]` OUT pin, `[3:2]` IN pin, `[5:4]` side-set pin, `[7:6]` JMP pin |
| 5 | `[0]` OUT LSB-first, `[1]` IN LSB-first, `[2]` autopull, `[3]` autopush, `[4]` differential OUT, `[6:5]` re-phase edge |
| 6, 7 | Wrap bottom, wrap top |
| 8–9, 10–11 | T0, T1 (little-endian) |
| 12 | Entry PC after restart |

The assembler produces everything except the pad numbers and T0/T1, which the host chooses.
The same program therefore runs on any pins at any rate.
It accepts up to 64 labels and rejects excess labels before writing its label tables.

## Host bus

`ui_in[7]` is a toggle strobe, `ui_in[6:4]` a command and `ui_in[3:0]` a nibble.
The host sets the command and nibble, then toggles the strobe in a later cycle.
`uo_out` shows the last status or popped byte.

| Cmd | Name | Nibble | Effect |
| --- | --- | --- | --- |
| 0 | LO | data | Hold low nibble |
| 1 | HI | data | `{nibble, hold}` to the selected target, then advance the pointer |
| 2 | SEL | `[3:2]` 0 TX FIFO, 1 IMEM, 2 CFG; `[1:0]` engine | Select target, reset pointer |
| 3 | CTRL | run mask (restart mask = hold) | Restart clears PC, registers, FIFOs, flags and pins |
| 4 | POP | `[1:0]` engine | `uo_out` = RX FIFO head |
| 5 | STAT | `[0]` engine pair | `uo_out` = two status nibbles: `[0]` RX not empty, `[1]` TX full, `[2]` idle, `[3]` flag |
| 6 | CLRF | engine mask | Clear sticky flags |

With the RP2040 changing a pin every two chip cycles, a byte write takes 8 cycles and a status read or pop takes 7.
The loader restarts and stops the selected engine before writing program or configuration bytes, releasing its old outputs before pin remapping.
Other engines keep running during the load.

## Protocols at 40 MHz

| Program | Words | Rate | Host framing |
| --- | --- | --- | --- |
| `uart_tx` | 8 | 115 274 baud (T0 = 347, +0.06%) | One byte per frame |
| `uart_rx` | 11 | Same; tested with a sender at ±3% | One byte per frame; a framing error sets the flag and drops the byte |
| `spi_master` | 14 | SCK 10 MHz (clk/4), mode 0, no inter-byte gap | `n-1` as two bytes, then `n` bytes; `n` bytes come back; CS spans the frame |
| `spi_div6` | 14 | SCK 6.67 MHz (clk/6); tolerates slower targets | Same |
| `i2c_master` | 27 | SCL 400 kHz (T0 = 60, T1 = 36), clock stretching | Command byte `[7]` ACK out, `[6:5]` op (0 START, 1 STOP, 2 XFER); XFER takes a data byte (0xFF to read) and returns the data and ACK bytes |
| `usb_ls_host` | 26 | 1.481 Mb/s (T0 = 27, −1.2%); EOP, turnaround, edge-tracking receive | Line states after NRZI and stuffing, with a 15-bit count and a "listen" bit; the reply comes back as D+ samples with an end marker |
| `eth10_tx` | 23 | 10 Mb/s Manchester, link pulses, TP_IDL, interframe gap | Bit count, then the frame bytes (preamble to FCS) |

Default wiring on the TT demo board's `uio` Pmod:

- SPI: `uio[0..3]` = CS, MOSI, MISO, SCK.
- UART: `uio[4]` = TX, `uio[5]` = RX.
- I2C: `uio[6]` = SCL, `uio[7]` = SDA.
- USB: D+/D− on `uio[0]`/`uio[1]`, with a 15 kΩ pull-down on D+.
- Ethernet: TD+/TD− on `uio[2]`/`uio[3]`, through resistors to an RJ45 jack with built-in magnetics.

Every program runs on any pad; the remap test proves it.

Peers, chosen from parts that can be plugged in after fabrication:

- **USB-UART bridge** (e.g. Pmod USBUART): checks every edge against the bit grid, can transmit off-rate, can send a bad stop bit, and can echo.
- **W25Q128JV**: JEDEC ID, read, fast read, WREN/WRDI, status, page program and erase with busy times.
  It checks the mode, the SCK timing and MOSI setup, and can delay MISO.
- **ADT7420** (Pmod TMP2): register pointer, ID 0xCB, clock stretching.
  It checks every UM10204 Fast-mode limit.
- **USB low-speed keyboard**: decodes NRZI, bit stuffing, PIDs, CRC5 and CRC16, and recovers its clock on edges.
  It answers GET_DESCRIPTOR, SET_ADDRESS and SET_CONFIGURATION, gives HID reports with data toggles, and NAKs when configured but idle.
  Configuration 1 enables EP1 after the status handshake; configuration 0 disables it, and unsupported configuration values stall.
  Reconfiguration resets EP1's data toggle to DATA0.
  It runs with ±1.5% clock error, ignores bad CRCs, and checks EOP width, bit rate and handshake timing.
- **10BASE-T receiver**: requires exact 50 ns half-bits and a mid-bit transition in every bit.
  It checks preamble, SFD, FCS, TP_IDL, interframe gap, and link pulse width and spacing.

## Results (all 37 criteria pass)

| Area | Measured |
| --- | --- |
| UART | 48 bytes, edges exactly on the 347-cycle grid, 3470 cycles per back-to-back frame; RX at ±3%; framing error flagged; 64-byte full-duplex echo |
| SPI | JEDEC `EF 40 18`, 252-byte read, fast read, page program + polling + read-back; SCK 10 MHz with no gaps across a 256-byte frame (1.24 MB/s including host traffic) |
| SPI margin | `spi_master` needs MISO within one clock of the SCK edge reaching the pin; `spi_div6` tolerates two more clocks |
| I2C | 400.0 kHz; tLOW 1.50 µs, tHIGH 1.00 µs, tSU;DAT 1.2 µs, tHD;STA 1.50 µs, tSU;STA 1.00 µs, tSU;STO 1.00 µs, tBUF 4.50 µs; the same with a target stretching 10 µs per ACK |
| USB | Device descriptor, SET_ADDRESS, SET_CONFIGURATION, disabled endpoints before configuration and after deconfiguration, NAK, HID reports with DATA0/DATA1 and toggle reset on reconfiguration; host at −1.23% (limit ±1.5%), EOP 1.35 µs (limit 1.25–1.50 µs), handshake 3.7–3.9 bit times after the device EOP (limit 2–7.5); keyboard clock at ±1.5% and recovery from a bad-CRC packet |
| Ethernet | 4 UDP frames with valid FCS and IP checksums, including a 1518-byte frame streamed from the host; half-bits exactly 50 ns, TP_IDL 300 ns, interframe gap ≥ 12.1 µs, link pulses 100 ns every 14.7 ms |
| Concurrency | UART TX, UART RX, SPI and I2C correct on four engines at once |
| Checker sanity | Removing edge re-phasing from UART RX, I2C or USB fails U4, I4 or K4–K8; a 3-cycle Ethernet half-bit fails E1/E2, a short EOP fails K3, a short interframe gap fails E4, a short TP_IDL fails E3, two stop bits fail U3 |

## State budget

| Per engine | Bits |
| --- | --- |
| Instruction memory (32 × 16) | 512 |
| Configuration (pin map 20, roles 8, mode 7, wrap 10, T0/T1 32, entry 5) | 82 |
| FIFOs (2 × 4 × 8 + pointers) | 78 |
| Registers (PC 5, X/Y 32, ISR/OSR 24, timer 16, delay 4, pins 8, re-phase 1, flags/run/stall 4) | 94 |

Four engines plus the host interface (72) come to about 3,140 bits: 2,048 in instruction memory, which can be a latch array, and about 1,100 flip-flops.
That is an estimated 30–35% of the 6×4 area.
The GDS flow has to confirm this.

## Limitations

- Ethernet is transmit-only: receiving needs an analog front end and more oversampling than a 40 MHz single-edge clock gives.
- USB is the host role at low speed.
  The RP2040 does NRZI, stuffing and CRC, and its response time is part of the measured handshake timing.
- Pad speeds and voltages need confirming for IHP CMOS5L.
  SPI at clk/4 needs MISO within 50 ns of SCK leaving the chip; use `spi_div6` otherwise.
- No metastability or analog edge-rate modelling; I2C is single-master; write the instruction memory only while an engine is stopped.
