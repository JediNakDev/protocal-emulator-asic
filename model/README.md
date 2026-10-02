# Protocol engine C model

A cycle-accurate C model of the programmable protocol engine and the board it plugs into.
UART, SPI and I2C run as **programs** on the engine, and peer models of real Pmod parts check them through the `uio` pins only.
The RTL does not exist yet.
This model fixes the ISA, the pin and timing rules, and the resource budget the RTL must meet.

```sh
make model          # from the repository root, or: make -C model test
```

The suite runs in under a second and exits non-zero on any failure.
Build with `-DPE_CLK_HZ=25000000.0` to rerun everything at another clock; all timing constants are derived from it.

## Files

| File | Contents |
| --- | --- |
| `pe.h` | Limits, instruction encoding, configuration and host-bus definitions, chip and board state |
| `chip.c` | Engines (`core_step`), host bus, pad logic with 2-FF synchronisers |
| `board.c` | Wire resolution with pull-ups, contention checks, cycle loop |
| `asm.c` | Two-pass assembler for `.pasm` sources |
| `host.c` | RP2040 host model: bus transactions, program loader, FIFO streaming |
| `peers.c` | USB-UART, W25Q128JV flash and ADT7420 sensor models, which also check protocol timing |
| `programs/*.pasm` | The protocol firmware |
| `test_main.c` | Test suite |

## How the environment matches the hardware

| Constraint | Model |
| --- | --- |
| `ui_in[8]` read-only, `uo_out[8]` write-only | Host bus only (see below) |
| `uio[8]` with `out`, `oe`, `in` | Per-pad registered `out`/`oe`; inputs read back from the wire |
| No open-drain pads | Per-logical-pin `od` bit: value 0 drives low (`oe=1, out=0`), value 1 releases (`oe=0`) |
| External pull-ups | An undriven `uio` wire reads 1; two drivers fighting each other are reported as an error |
| One clock | `PE_CLK_HZ`, 50 MHz by default |
| 2-FF input synchronisers | Engines read `wire(t-2)`; the host bus is synchronised the same way |
| Registered outputs | A pin written in cycle `t` changes on the wire in cycle `t+1` |
| One write per pin per cycle | Writing a logical pin twice in one instruction (e.g. side-set and `set`) is an error, as is two engines driving one pad |
| Peer response time | Peers drive their reply one cycle after they observe an edge |
| Instruction memory | 32 × 16-bit words per engine |
| Registers | `X`, `Y` (8-bit scratch), `ISR`, `OSR` (8-bit shift with counters), one 16-bit timer |
| FIFOs | 4 × 8-bit host→engine (TX) and 4 × 8-bit engine→host (RX) per engine |
| Engines | 2 |
| No C state in the hot path | `core_step()` keeps nothing across cycles outside `pe_core`; every field is a flip-flop |

### Cycle order

```
cycle t: wire(t) = resolve(pads registered at t-1, peer drives set at t-1, pull-ups)
         peers observe wire(t), set their drive for t+1
         chip:   host command (if the synchronised strobe toggled)
                 each engine executes one cycle, reading uio = wire(t-2)
                 pad registers <- engine pin state            (visible at t+1)
```

A peer's reply is therefore visible to an engine four cycles after the engine moved the pin: one cycle through the output register, one in the peer, and two in the synchroniser.
In real time, this allows two clock periods (40 ns at 50 MHz) for the output pad, the peer's clock-to-output delay, the input pad and synchroniser setup.

## Instruction set

Every instruction is 16 bits: `[15:13]` opcode, `[12:8]` delay/side-set, `[7:0]` arguments.
An instruction takes one cycle plus its delay.

| Opcode | Syntax | Effect |
| --- | --- | --- |
| 0 JMP | `jmp [cond] label` | cond: always, `!x`, `x--`, `!y`, `y--`, `pin`, `!pin`, `!osre`; `x--`/`y--` test non-zero, then decrement |
| 1 WAIT | `wait 0\|1 pin p [resync]` | Stall until the synchronised pin matches. `resync` restarts the timer with T1 when the wait completes |
| 2 IN | `in pins\|x\|y\|null\|isr\|osr, n` | Shift `n` bits into ISR; autopush at 8 |
| 3 OUT | `out pins\|x\|y\|null\|pindirs\|pc\|isr, n` | Shift `n` bits out of OSR; autopull when OSR is empty |
| 4 XCH | `xch [n]` | `out pins, n` and `in pins, n` in the same cycle |
| 5 PUSH/PULL | `push [noblock]`, `pull [noblock]` | Explicit FIFO transfer; `push noblock` on a full FIFO sets the overflow flag |
| 6 MOV | `mov dst, [~\|::]src` | dst: `pins x y pc isr osr`; src: `pins x y null isr osr`; invert or bit-reverse |
| 7 SET | `set pin p, v`; `set pins\|pindirs\|x\|y\|flag\|timer, v` | 5-bit immediate; `set pins` skips the side-set pin; `set timer, 0\|1` restarts the timer with T0 or T1 |

The delay/side-set field depends on the side-set mode set by `.side_set`:

| Mode | Side-set | Delay values | `[tick]` code |
| --- | --- | --- | --- |
| none | — | 0–30 | 31 |
| `.side_set p` | 1 bit, every instruction | 0–14 | 15 |
| `.side_set p opt` | enable bit + 1 bit | 0–6 | 7 |

**Timer.**
The 16-bit timer ticks every T0 cycles.
`[tick]` holds the engine after an instruction until the next tick, so a protocol bit lasts exactly T0 cycles regardless of how many instructions it contains.
`wait ... resync` and `set timer` re-phase the timer, giving edge-aligned sampling for receivers.
The same mechanism would provide clock recovery for USB low speed.

**Stalls.**
An instruction blocked by a FIFO (pull or autopull on an empty TX FIFO, push or autopush to a full RX FIFO) has no effect until it can complete, including its side-set.
A blocked WAIT applies its side-set immediately, because the condition it waits for usually depends on it (for example, releasing SCL, then waiting for it to go high).
SPI relies on this: a FIFO stall holds SCK high instead of corrupting the sample timing.

### Configuration bytes (per engine)

| Byte | Contents |
| --- | --- |
| 0–3 | Logical pin `i`: `[2:0]` uio pad, `[3]` open-drain emulation |
| 4 | `[1:0]` OUT pin, `[3:2]` IN pin, `[5:4]` side-set pin, `[7:6]` JMP pin |
| 5 | `[1:0]` side-set mode, `[2]` OUT LSB-first, `[3]` IN LSB-first, `[4]` autopull, `[5]` autopush |
| 6, 7 | Wrap bottom, wrap top |
| 8–9, 10–11 | T0, T1 (little-endian) |
| 12 | Entry PC after restart |

The assembler produces bytes 4–7 and 12 and the open-drain bits.
The host sets the pad mapping and T0/T1, so the same program runs on any pins at any rate.

## Host bus

`ui_in[7]` is a toggle strobe, `ui_in[6:4]` a command and `ui_in[3:0]` a nibble.
The host sets the command and nibble, then toggles the strobe in a later cycle.
`uo_out` is a register that shows the last status or popped byte.

| Cmd | Name | Nibble | Effect |
| --- | --- | --- | --- |
| 0 | LO | data | Hold low nibble |
| 1 | HI | data | `{nibble, hold}` to the selected target, then advance the pointer |
| 2 | SEL | `[3:2]` 0 TX FIFO, 1 IMEM, 2 CFG; `[0]` engine | Select target, reset pointer |
| 3 | CTRL | `[1:0]` run, `[3:2]` restart | Restart clears PC, registers, FIFOs, flags and pins |
| 4 | POP | `[0]` engine | `uo_out` = RX FIFO head |
| 5 | STAT | — | `uo_out` = status: per engine `[0]` RX not empty, `[1]` TX full, `[2]` idle, `[3]` flag |
| 6 | CLRF | engine mask | Clear sticky flags |

With the RP2040 changing a pin every two chip cycles, a byte write takes 8 cycles and a status read or pop takes 7.

## Protocols

Default wiring on the TT demo board's `uio` Pmod: SPI on `uio[0..3]` (CS, MOSI, MISO, SCK, the standard Pmod SPI order), UART TX/RX on `uio[4]`/`uio[5]`, I2C SCL/SDA on `uio[6]`/`uio[7]`.
The pin-remap test runs the same programs on other pads.

| Program | Words | Rate at 50 MHz | Host framing |
| --- | --- | --- | --- |
| `uart_tx` | 8 | 115 207 baud (T0 = 434) | One byte per frame |
| `uart_rx` | 11 | Same; tolerates ±4% baud error | One byte per frame; framing error sets the flag and drops the byte |
| `spi_master` | 13 | SCK 12.5 MHz (clk/4), mode 0, MSB first, no inter-byte gap | `[n-1]` then `n` bytes; `n` bytes come back; CS spans the frame |
| `spi_div6` | 13 | SCK 8.33 MHz (clk/6); otherwise identical | Same |
| `i2c_master` | 27 | SCL 400.0 kHz (T0 = 75, T1 = 46), clock stretching | Command byte `[7]` ACK out, `[6:5]` op (0 START, 1 STOP, 2 XFER); XFER takes a data byte (0xFF to read) and returns the data and ACK bytes |

Peers, chosen from parts that can be plugged into the board's Pmod header after fabrication:

- **USB-UART bridge** (e.g. Digilent Pmod USBUART): checks every edge against the bit grid, can transmit with a baud-rate error, can send a bad stop bit, and can echo.
- **Winbond W25Q128JV**: JEDEC ID, read, fast read, WREN/WRDI, status, page program and sector erase with busy times. It checks the mode, SCK timing and that MOSI is stable at SCK rising edges.
- **ADT7420** (Digilent Pmod TMP2) at 0x48: register pointer, read-only registers, ID 0xCB, optional clock stretching. It checks the Fast-mode timing limits of UM10204.

## Results

| Test | Result |
| --- | --- |
| Reset | All `uio` pads are inputs |
| UART TX | 48 bytes back-to-back; every edge on the 434-cycle grid (0 cycles error) |
| UART RX | ±3% tested in CI; a sweep passes ±4% and fails at ±5% |
| UART framing | Bad stop bit sets the flag, the byte is dropped, and the next byte arrives |
| UART full duplex | Two engines, 64 bytes echoed through the peer at +1% baud error |
| SPI | JEDEC ID `EF 40 18`; 252-byte read, fast read, page program with status polling and read-back. SCK period is 4 cycles throughout a 256-byte frame (no gaps): 1.55 MB/s including host traffic |
| I2C | ID 0xCB, temperature 25.0 °C, 2-byte register write and read-back, repeated START, NACK from an absent address. All Fast-mode minimums met: tLOW 1.50 µs, tHIGH 1.00 µs, tSU;DAT 1.26 µs, tBUF 3.94 µs |
| I2C, stretching | Same, with the target holding SCL for 10 µs after every ACK |
| SPI margin | `spi_master` reads correctly only when MISO arrives with no extra delay; `spi_div6` tolerates 2 extra cycles |
| Reprogramming | One engine reloaded from UART TX to SPI while the other runs I2C |
| Pin remap | UART TX on `uio[7]`, SPI with MOSI and MISO pads swapped |
| Checker sanity | Two engines driving one pad is reported. Removing `resync` from the I2C program makes the stretching test fail (tHIGH 0.48 µs) |

## Findings for the RTL

- **State budget.**
  Per engine: 512 bits of instruction memory, about 100 configuration bits, 78 FIFO bits and about 80 register bits, roughly 770 flip-flops.
  Two engines plus the host interface come to about 1 600 flip-flops, of which 1 024 are instruction memory.
  Use the GDS flow to decide whether a latch array or an SRAM macro is needed before increasing memory.
- **Instruction memory.**
  32 words is enough: the largest program (I2C) uses 27.
  JMP and SET make up half of all instructions; XCH and the `[tick]` delay code are what make SPI and UART/I2C fit.
- **SPI round trip.**
  At clk/4, MISO for bit k is sampled on the cycle SCK falls for bit k+1, which leaves two cycles (40 ns) between SCK leaving the chip and MISO reaching the synchroniser.
  The margin test confirms it: one extra cycle of target delay breaks clk/4 reads, while `spi_div6` (one more cycle per SCK phase, 8.33 MHz) tolerates two.
  Check the CMOS5L pad delays against this budget before committing to 12.5 MHz.
- **Edge re-phasing.**
  Without re-phasing the timer on the observed SCL edge, clock stretching shortens tHIGH below the specification. `resync` is required, not optional.
- **Host bandwidth.**
  An RP2040 changing a pin every two chip cycles keeps a 12.5 MHz SPI stream gap-free with 4-entry FIFOs.
  A slower host stretches SCK high between bytes, which SPI permits.

## Limitations

- Pad speeds and voltages are sky130 figures; confirm them for IHP CMOS5L in the template.
- The model does not cover metastability or analog edge rates (open-drain rise time is treated as one cycle).
- Engines do not arbitrate on I2C; the controller supports a single master.
- Writing instruction memory while an engine runs takes effect immediately; stop the engine first.
- USB and Ethernet are not implemented.
