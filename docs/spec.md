# Programmable protocol emulator specification

This document defines the behavior the RTL implements.
When the RTL and this document disagree, one of them has a bug.
Terms follow `CONTEXT.md`: an **Engine** executes a **Program** that the **Host** loads.

## Design rules

- No hardware block is named after or dedicated to a protocol.
  Every feature is a general mechanism that at least one planned protocol needs and unplanned protocols can reuse.
- Every behavior visible at the pins has a defined cycle count.
- One clock domain (`clk`), with asynchronous reset asserted by `rst_n` and released synchronously.

## Block overview

| Block | Count | Purpose |
| --- | --- | --- |
| Host port | 1 | SPI-style target port that the Host uses to reach every register |
| Instruction memory | 1, shared | 64 instructions of 16 bits, written by the Host, read by both engines every cycle |
| Engine | 2 | Executes instructions, with shift registers, scratch registers, buffers, side-set and delay |
| Buffers | 1 per engine | 8 entries of 16 bits each, usable as transmit queue, receive queue or RAM (G5) |
| Checksum shift register (G1) | 1 per engine | 32-bit programmable LFSR for CRCs, parity, scramblers and test patterns |
| Edge capture (G3) | 1 per engine | Latches the global 32-bit cycle counter on a chosen pin edge |
| Bit state machine (G2) | 1, shared | 64-entry table that steps a 16-state machine on two input bits |
| Pin block | 1 | Input synchronizers, glitch filters, falling-edge sampling (G6) and output drive modes |
| Coordination flags | 8, shared | Set, cleared and waited on by engines, the capture units and the Host |

## Top-level pins

| Pin | Name | Direction | Function |
| --- | --- | --- | --- |
| `ui[0]` | `HCS_N` | in | Host port chip select, active low |
| `ui[1]` | `HSCK` | in | Host port clock |
| `ui[5:2]` | `HDI[3:0]` | in | Host port data in; `HDI[0]` only in 1-bit mode |
| `ui[7:6]` | `P8`, `P9` | in | Protocol pins 8 and 9, input only |
| `uo[3:0]` | `HDO[3:0]` | out | Host port data out; `HDO[0]` only in 1-bit mode |
| `uo[4]` | `HIRQ` | out | Host interrupt request, active high |
| `uo[7:5]` | `P10`-`P12` | out | Protocol pins 10 to 12, output only |
| `uio[7:0]` | `P0`-`P7` | in/out | Protocol pins 0 to 7, bidirectional with output enable |

### Pin space

Engines and the bit state machine address 16 pin indices.

| Index | Source when read | Effect of engine writes |
| --- | --- | --- |
| 0-7 | `uio[0..7]` after the input path | Drive value and direction of `uio[0..7]` |
| 8-9 | `ui[6..7]` after the input path | Ignored |
| 10-12 | The value currently driven on `uo[5..7]` | Drive value of `uo[5..7]` |
| 13 | Bit state machine output 0 | Ignored |
| 14 | Bit state machine output 1 | Ignored |
| 15 | Falling-edge sample of the pin chosen by `NEGSEL` | Ignored |

### Reset state (H5)

While `rst_n` is low and after it rises, until the Host configures otherwise:

- `uio_oe` is 0 on all eight pins, so every bidirectional pin is released.
- `uio_out` is 0.
- `uo[7:5]` (output-only protocol pins) drive 0.
- `HDO[3:0]` and `HIRQ` drive 0.
- Both engines are disabled and every pin is configured with drive mode "off".

Lines that must idle high, such as UART TX, I2C or USB, belong on `uio` pins, where an external pull-up sets the idle level before configuration.

Instruction memory and the bit state machine table are not reset; their contents are undefined until written.
Engines must not be enabled before the Host loads a program.

## Host port

The Host port is a target on an SPI-style bus in mode 0: `HSCK` idles low, and both sides sample on the rising edge.
The chip oversamples `HCS_N`, `HSCK` and `HDI` with two-flop synchronizers, so the Host clock is not a second clock domain.

### Timing requirements

| Parameter | Minimum |
| --- | --- |
| `HSCK` high time | 4 `clk` cycles |
| `HSCK` low time | 4 `clk` cycles |
| `HCS_N` falling to first `HSCK` rising | 4 `clk` cycles |
| Last `HSCK` falling to `HCS_N` rising | 4 `clk` cycles |
| `HCS_N` high time between transactions | 4 `clk` cycles |

The maximum `HSCK` frequency is therefore `f_clk / 8`, which is 6.25 MHz at 50 MHz.
In 4-bit mode that is 25 Mbit/s raw, or about 3.1 MB/s.
The Host must change `HDI` only after a rising edge, for example on the falling edge.
The chip changes `HDO` between 3 and 6 `clk` cycles after a rising edge of `HSCK`, so data is stable at the next rising edge.

### Width

After reset the port is 1 bit wide: data in on `HDI[0]`, data out on `HDO[0]`, most significant bit first, 8 clocks per byte.
Setting `HOSTCFG.QUAD` switches to 4 bits per clock on `HDI[3:0]` and `HDO[3:0]`, high nibble first, 2 clocks per byte.
The width is latched when `HCS_N` falls, so a write to `HOSTCFG` takes effect in the next transaction.
In 1-bit mode `HDO[3:1]` drive 0.

### Transactions

A transaction starts when `HCS_N` falls and ends when it rises.
The first byte is a command: bit 7 is 1 for read and 0 for write, and bits 6:0 are the register address.

- **Write:** every following byte is written to the current address.
- **Read:** every following byte returns the value at the current address.

The chip outputs the `STATUS` byte while the command byte is shifted in, and again during every write data byte.
After each data byte, the address increments by one, except at **port** addresses, where it stays fixed.

Port addresses are 16 bits wide and transfer the low byte first, then the high byte.
A port write takes effect when its high byte arrives.
A port read takes effect (for example a FIFO pop) only after its high byte has been completely shifted out, so a transaction cut short never loses data.
Receive queue reads snapshot the complete word and empty status when the low byte is fetched.
An initially empty read returns zero and sets the underflow sticky bit after the high byte completes, without consuming a word that arrived during the transfer.
The low/high byte toggle resets at every command byte.

| `STATUS` bit | Meaning |
| --- | --- |
| 0 | Engine 0 receive queue not empty |
| 1 | Engine 0 transmit queue not full |
| 2 | Engine 1 receive queue not empty |
| 3 | Engine 1 transmit queue not full |
| 4 | Engine 0 stalled |
| 5 | Engine 1 stalled |
| 6 | `HIRQ` is asserted |
| 7 | At least one `STICKY` bit is set |

## Register map

Unlisted addresses read 0 and ignore writes.
Multi-byte registers are little-endian.

### Global registers

| Address | Name | Access | Description |
| --- | --- | --- | --- |
| 0x00 | `ID` | R | 0x50 |
| 0x01 | `VERSION` | R | 0x01 |
| 0x02 | `CTRL` | RW | Bit 0 enables engine 0, bit 1 enables engine 1 |
| 0x03 | `CMD` | W | Write 1 to pulse: bit 0 restarts engine 0, bit 1 restarts engine 1, bit 2 steps engine 0, bit 3 steps engine 1, bit 4 clears engine 0 buffers, bit 5 clears engine 1 buffers, bit 6 resets both clock dividers |
| 0x04 | `FLAGS` | RW | Read the 8 coordination flags; write 1 to clear |
| 0x05 | `FLAG_SET` | W | Write 1 to set flags |
| 0x06 | `IRQ_MASK` | RW | Flags that assert `HIRQ` |
| 0x07 | `STICKY` | RW | Error flags; write 1 to clear (see below) |
| 0x08 | `STICKY_MASK` | RW | Sticky bits that assert `HIRQ` |
| 0x09 | `HOSTCFG` | RW | Bit 0 `QUAD` |
| 0x0A | `IMEM_ADDR` | RW | Instruction memory write address, 6 bits |
| 0x0B | `IMEM_DATA` | W port | Writes one instruction at `IMEM_ADDR`, then increments `IMEM_ADDR` modulo 64 |
| 0x0C | `G2_ADDR` | RW | Bit state machine table write address, 6 bits |
| 0x0D | `G2_DATA` | W | Writes one table entry at `G2_ADDR`, then increments `G2_ADDR` modulo 64 (8-bit, not a port) |
| 0x0E | `G2_CTRL` | RW | See bit state machine |
| 0x0F | `G2_IN0` | RW | Input 0 source, 5 bits |
| 0x10 | `G2_IN1` | RW | Input 1 source, 5 bits |
| 0x11 | `G2_STATE` | RW | Bits 3:0 state, bits 5:4 outputs, bit 6 feed full (read only); a write also empties the feed |
| 0x12 | `NEGSEL` | RW | Pin (0-9) sampled on the falling edge into pin 15 |
| 0x13 | `DBG_SEL` | RW | Selects what each engine's `DBG` port returns, 3 bits |
| 0x14 | `PINS_L` | R | Pin space bits 7:0 |
| 0x15 | `PINS_H` | R | Pin space bits 15:8 |
| 0x16-0x19 | `COUNTER` | R | Global cycle counter; reading 0x16 latches bits 31:8 for the following reads |
| 0x20-0x2C | `PINCFG0`-`PINCFG12` | RW | Per-pin configuration |

Writes to `IMEM_DATA` (0x0B) do not auto-increment the bus address; `G2_DATA` (0x0D) does not either.

| `STICKY` bit | Set when |
| --- | --- |
| 0 | Engine 0 receive overflow: a non-blocking `push` to a full queue, or a bit state machine word dropped |
| 1 | Engine 1 receive overflow |
| 2 | Host wrote engine 0's transmit queue while full; the word is dropped |
| 3 | Host wrote engine 1's transmit queue while full |
| 4 | Host read engine 0's receive queue while empty; the data read is 0 |
| 5 | Host read engine 1's receive queue while empty |
| 6 | Engine 0 capture overrun |
| 7 | Engine 1 capture overrun |

`HIRQ` is `|(FLAGS & IRQ_MASK) | |(STICKY & STICKY_MASK)`, registered, so it follows its sources by one cycle.

### Per-pin configuration (`PINCFGn`)

| Bits | Field | Values |
| --- | --- | --- |
| 1:0 | Owner | 0 engine 0, 1 engine 1, 2 bit state machine output 0, 3 bit state machine output 1 |
| 3:2 | Drive | 0 off (released, output 0), 1 push-pull (direction from owner), 2 open-drain (drive 0 or release), 3 always drive |
| 4 | Invert | Inverts the output value before the drive stage |
| 5 | Bypass | Skips the 2-flop synchronizer (G6); see input path |
| 7:6 | Filter | 0 none, 1, 2 or 3 for a 2, 4 or 8 cycle glitch filter (G6) |

A bit state machine owner always counts as direction "output".
On output-only pins 10-12, open-drain behaves as push-pull, and push-pull with direction "input" drives 0.
Pins 8 and 9 have no output; their owner, drive and invert fields are ignored.
Pins 10-12 have no input path; their bypass and filter fields are ignored.

### Engine registers

Engine 0 occupies 0x40-0x5F and engine 1 occupies 0x60-0x7F.
Offsets are relative to the block base.

| Offset | Name | Access | Description |
| --- | --- | --- | --- |
| 0x00-0x01 | `CLKDIV` | RW | The engine advances once every `CLKDIV + 1` cycles |
| 0x02 | `WRAP_BOTTOM` | RW | 6 bits |
| 0x03 | `WRAP_TOP` | RW | 6 bits |
| 0x04 | `SHIFTCTRL` | RW | Bit 0 autopush, bit 1 autopull, bit 2 `in` shifts right, bit 3 `out` shifts right, bits 6:4 buffer mode; any write clears both queues |
| 0x05 | `THRESH` | RW | Bits 3:0 push threshold, bits 7:4 pull threshold (0 means 16) |
| 0x06 | `OUTPIN` | RW | Bits 3:0 base, bits 7:4 count (0 means 16) |
| 0x07 | `SETPIN` | RW | Bits 3:0 base, bits 6:4 count (0-5) |
| 0x08 | `INPIN` | RW | Bits 3:0 `in` base, bits 7:4 `jmp pin` index |
| 0x09 | `SIDEPIN` | RW | Bits 3:0 base, bits 5:4 count (0-3), bit 6 optional, bit 7 side-set drives directions |
| 0x0A | `EXECCFG` | RW | Bits 5:0 jump base (G8), bit 6 `jmp pin` tests the pin pattern instead (G4) |
| 0x0B | `STATUSCFG` | RW | Bits 3:0 level `N`, bit 4 selects the receive queue (1) or transmit queue (0) |
| 0x0C | `LFSRCFG` | RW | Bits 1:0 feed, bits 3:2 mode, bit 4 shifts right |
| 0x0D | `CAPCFG` | RW | Bits 3:0 pin, bits 5:4 edge (0 off, 1 rising, 2 falling, 3 both), bit 6 sets a flag |
| 0x0E | `CAPFLAG` | RW | Flag index, 3 bits |
| 0x0F | `INSTR` / `DBG` | W port / R port | Write: forced instruction. Read: value chosen by `DBG_SEL` |
| 0x10 | `FIFO` | W port / R port | Write: push to the transmit queue. Read: pop from the receive queue |
| 0x11 | `LEVELS` | R | Bits 3:0 transmit level, bits 7:4 receive level |
| 0x12 | `RAM_ADDR` | RW | 3 bits |
| 0x13 | `RAM_DATA` | RW port | Reads or writes buffer entry `RAM_ADDR`, then increments `RAM_ADDR` |
| 0x14-0x17 | `LFSR_POLY` | RW | 32-bit feedback pattern |
| 0x18-0x1B | `LFSR_VALUE` | RW | 32-bit register value; a Host write wins over an engine update in the same cycle |
| 0x1C-0x1F | `CAPTURE` | R | Last captured counter value |

| `DBG_SEL` | Value |
| --- | --- |
| 0 | Bits 5:0 PC, bit 6 stalled, bit 7 exec slot full, bit 8 waiting on a flag, bits 13:9 delay counter |
| 1 | X |
| 2 | Y |
| 3 | ISR |
| 4 | OSR |
| 5 | Bits 4:0 ISR count, bits 12:8 OSR count |
| 6 | The instruction the engine will execute next |
| 7 | Clock divider counter |

`DBG` reads are only guaranteed consistent between the two bytes while the engine is disabled.

Engine registers reset to 0, except `WRAP_TOP` (63), the `OUTPIN` count (1) and the `SETPIN` count (1).
After reset each engine's PC, X, Y, ISR and pin registers are 0, and its OSR is marked empty.

## Input path (G6)

Pins 0-9 pass through: synchronizer (2 flops, unless bypassed), then the glitch filter, then into the pin space.

- **Latency:** a pin change is visible to instructions 2 cycles after the rising clock edge that first samples it.
  With bypass the latency is 0, and the raw pad value reaches engine logic combinationally.
- **Bypass warning:** bypass on an asynchronous signal can cause metastability, which shows up as rare, non-repeatable misbehavior.
  Use it only when the signal is synchronous to `clk`, or when occasional wrong samples are acceptable.
- **Glitch filter:** the filtered value changes only after the input has held the new value for N consecutive cycles (N = 2, 4 or 8).
  This adds N cycles of latency.
- **Falling-edge sample:** pin 15 holds the pin selected by `NEGSEL`, sampled on the falling edge of `clk` and retimed to the rising edge.
  It has the same 2-cycle latency as a synchronized pin, and its sample is taken half a cycle after that pin's rising-edge sample.
  Reading the selected pin and pin 15 together gives two samples per cycle.
  The spacing between the two samples depends on the duty cycle of `clk`.

## Output path

Engine pin registers update on the clock edge that ends the executing cycle.
The pad changes in the following cycle, with no extra register stage.
Bit state machine outputs follow the same rule.

## Engines

Each engine has:

| State | Width | Notes |
| --- | --- | --- |
| PC | 6 | Address in the shared instruction memory |
| X, Y | 16 each | Scratch registers |
| ISR | 16 | Input shift register, with a 0-16 count of bits shifted in |
| OSR | 16 | Output shift register, with a 0-16 count of bits shifted out (16 means empty) |
| Delay counter | 5 | Counts down delay cycles |
| Exec slot | 16 + valid | Holds an instruction to run next instead of the one at PC |
| Pin registers | 13 + 13 | Output value and direction per pin |

### Timing

The clock divider produces a **tick** once every `CLKDIV + 1` cycles while the engine is enabled.
A disabled engine ticks only when the Host steps it, or when its exec slot holds an instruction.

On each tick:

1. If the delay counter is not zero, it decrements, and nothing else happens.
2. Otherwise the engine **issues** an instruction: the exec slot if full, otherwise the instruction at PC.
3. The instruction either completes or **stalls**.
   A stalled instruction has no effect except side-set, and issues again on the next tick.
4. On completion, PC moves to the jump target if the instruction jumped.
   Otherwise, an instruction from memory advances PC: to `WRAP_BOTTOM` if PC equals `WRAP_TOP`, else to PC + 1.
   An instruction from the exec slot that does not jump leaves PC unchanged.
5. On completion, the delay counter loads the instruction's delay field.

So, with `CLKDIV` 0, every instruction takes `1 + delay` cycles plus any stall cycles.
Jumps, wrap and the zero-cycle autopull do not add cycles.

Restart (`CMD`) clears the ISR, its count, the OSR (marked empty), the delay counter, the exec slot, any flag wait, the stall status and the clock divider phase.
It does not change PC, X, Y or pin registers; set PC with a forced `jmp`.

### Forced instructions

A Host write to `INSTR` loads the exec slot.
An enabled engine runs it on its next tick that is not a delay cycle.
A disabled engine runs it on the next cycle, and retries every cycle if it stalls.

### Instruction encoding

All instructions are 16 bits.
Bits 12:8 are the side-set and delay field in every instruction.

| Bits 15:13 | Instruction | Bits 7:0 |
| --- | --- | --- |
| 00x | `jmp` | Bit 13 and bits 7:6 condition, bits 5:0 address |
| 010 | `wait` | Bit 7 polarity, bits 6:5 source, bits 4:0 index |
| 011 | `in` | Bits 7:4 source, bits 3:0 bit count (0 means 16) |
| 100 | `out` | Bits 7:4 destination, bits 3:0 bit count (0 means 16) |
| 101 | `push`, `pull`, `irq` | Bits 7:6: 00 `push`, 01 `pull`, 10 `irq`, 11 reserved |
| 110 | `mov` | Bits 7:5 destination, bits 4:3 operation, bits 2:0 source |
| 111 | `set` | Bits 7:5 destination, bits 4:0 value |

Reserved encodings execute as `nop`, including their side-set and delay.
`nop` is assembled as `mov y, y`.

### Side-set and delay

`SIDEPIN` sets the side-set count `c` (0-3) and whether side-set is optional.

- Not optional: bits 12:(13-c) are the side-set value, and the remaining low bits are the delay.
- Optional: bit 12 enables side-set for this instruction, the next `c` bits are the value, and the rest is the delay.

The delay field holds 5 - c bits, or 4 - c when optional.
Side-set writes pins `base` to `base + c - 1` (modulo 16), or their directions when `SIDEPIN` bit 7 is set.
Side-set happens on every issue, including stalled issues, and takes priority over other pin writes in the same instruction.

### Pin ranges

`out pins`, `mov pins`, `set pins` and side-set write a contiguous range starting at a base pin, wrapping modulo 16.
Bit 0 of the data goes to the base pin.
`in pins` and `mov` from pins read the pin space rotated so that bit 0 is the `in` base pin.

### `jmp`

| Condition | Mnemonic | Jumps when |
| --- | --- | --- |
| 0 | (none) | Always |
| 1 | `!x` | X is 0 |
| 2 | `x--` | X is not 0; X decrements either way |
| 3 | `!y` | Y is 0 |
| 4 | `y--` | Y is not 0; Y decrements either way |
| 5 | `x!=y` | X differs from Y |
| 6 | `pin` | The `jmp pin` is 1, or the pin pattern matches if `EXECCFG` bit 6 is set |
| 7 | `!osre` | The OSR count is below the pull threshold |

### `wait`

| Source | Mnemonic | Waits until |
| --- | --- | --- |
| 0 | `gpio n` | Pin `n` (absolute, 0-15) equals the polarity |
| 1 | `pin n` | Pin `in_base + n` (modulo 16) equals the polarity |
| 2 | `flag n` | Flag `n` equals the polarity; waiting for 1 clears the flag on completion |
| 3 | `pattern` | Polarity 1: `(pins & Y) == X`. Polarity 0: they differ (G4) |

### `in`

Sources: 0 `pins`, 1 `x`, 2 `y`, 3 `null`, 4 `ram` (entry `Y[2:0]`), 5 `count` (counter bits 15:0), 6 `isr`, 7 `osr`, 8 `lfsrl`, 9 `lfsrh`, 10 `capl`, 11 `caph`, 12-15 read 0.

`in src, n` shifts the low `n` bits of the source into the ISR, from the left or right per `SHIFTCTRL`, and adds `n` to the count (saturating at 16).
With autopush, if the new count reaches the push threshold, the new ISR value is pushed to the receive queue and the ISR and its count clear, in the same cycle.
If the queue is full, the `in` stalls.

### `out`

Destinations: 0 `pins`, 1 `x`, 2 `y`, 3 `null`, 4 `pindirs`, 5 `pc` (G8), 6 `isr`, 7 `exec`, 8 `lfsr`, 9 `g2`, 10 `ram` (entry `Y[2:0]`), 11-15 discard.

`out dest, n` shifts `n` bits out of the OSR, from the left or right per `SHIFTCTRL`, and adds `n` to the count (saturating at 16).
The bits shifted out form the data, zero-extended to 16 bits.
With autopull, if the OSR count has reached the pull threshold, the OSR refills from the transmit queue and the `out` takes its bits from the new data in the same cycle.
If the queue is empty, the `out` stalls.

- `pc`: PC becomes `jump base + data` (modulo 64), an indexed jump.
- `isr`: the ISR becomes the data and its count becomes `n`.
- `exec`: the data is placed in the exec slot and runs on the next tick; the `out`'s own delay is ignored.
- `lfsr`: the checksum register shifts left by `n` and takes the data in its low bits; this never steps the checksum.
- `g2`: hands data bit 0 to the bit state machine feed; stalls while the feed is full; acts as `null` unless this engine owns the bit state machine.
- `ram`: writes the data to buffer entry `Y[2:0]`; ignored if that entry is not in a RAM role.

### `push`, `pull`

- `push [iffull] [block|noblock]`: copies the ISR to the receive queue and clears the ISR and its count.
  `iffull` does nothing unless the count has reached the push threshold.
  On a full queue, `block` (default) stalls; `noblock` drops the data, clears the ISR and sets the receive overflow sticky bit.
- `pull [ifempty] [block|noblock]`: loads the OSR from the transmit queue and sets its count to 0.
  `ifempty` does nothing unless the OSR count has reached the pull threshold.
  On an empty queue, `block` stalls; `noblock` copies X into the OSR instead.

### `irq`

- `irq set n` sets flag `n`.
- `irq wait n` sets flag `n`, then stalls until it is cleared by anyone.
- `irq clear n` clears flag `n`.

If one source sets a flag and another clears it in the same cycle, the set wins.

### `mov`

Destinations: 0 `pins`, 1 `x`, 2 `y`, 3 `pindirs`, 4 `exec`, 5 `pc`, 6 `isr` (count becomes 0), 7 `osr` (count becomes 0, meaning full).
Sources: 0 `pins`, 1 `x`, 2 `y`, 3 `null`, 4 `status`, 5 `isr`, 6 `osr`, 7 `ram` (entry `Y[2:0]`).
Operations: 0 none, 1 invert (`~`), 2 bit-reverse (`::`), 3 none.
`status` is all ones if the selected queue's level is below `N`, else all zeros.

### `set`

Destinations: 0 `pins` (`SETPIN` range), 1 `x`, 2 `y`, 4 `pindirs`; others do nothing.
The 5-bit value is zero-extended.

## Buffers (G5)

Each engine owns 8 entries of 16 bits.
`SHIFTCTRL` bits 6:4 choose the mode; changing them clears both queues.

| Mode | Transmit queue (Host to engine) | Receive queue (engine to Host) | RAM |
| --- | --- | --- | --- |
| 0, 6, 7 | Entries 0-3 | Entries 4-7 | None |
| 1 | Entries 0-7 | Disabled | None |
| 2 | Disabled | Entries 0-7 | None |
| 3 | Entries 0-3 | Disabled | Entries 4-7 |
| 4 | Disabled | Entries 4-7 | Entries 0-3 |
| 5 | Disabled | Disabled | Entries 0-7 |

A disabled queue counts as both empty and full, so `pull block` and `push block` on it stall forever.
RAM reads by the engine (`Y[2:0]`) or the Host (`RAM_ADDR`) may address any entry, which lets the Host inspect queue contents.
RAM writes from either side only take effect on entries in the RAM role.
If the engine and the Host write the same entry in the same cycle, the engine's write wins and the Host's is lost.

## Checksum shift register (G1)

Each engine has a 32-bit register `V` with a 32-bit feedback pattern `P`.

**Feed** (`LFSRCFG` bits 1:0) chooses which bits step it: 0 none, 1 bits leaving the OSR, 2 bits entering the ISR, 3 both.
Only `out` and `in` instructions with a bit count of exactly 1 step the register; wider shifts pass through untouched.
Bits that the bit state machine delivers into the ISR also step it when feed bit 1 is set.

**Direction** (`LFSRCFG` bit 4): left shifts toward bit 31 and uses bit 31 as the top bit; right shifts toward bit 0 and uses bit 0.
Shift-in bits enter at bit 0 (left) or bit 31 (right).

For each input bit `d`, with `par = parity(V & P)`:

| Mode | Name | Output bit | Next value |
| --- | --- | --- | --- |
| 0 | CRC | `d` (unchanged) | `shift(V) ^ (top ^ d ? P : 0)` |
| 1 | Self-synchronizing scramble | `d ^ par` | `shift(V)` with the output bit shifted in |
| 2 | Self-synchronizing descramble | `d ^ par` | `shift(V)` with `d` shifted in |
| 3 | Additive scramble or pattern generator | `d ^ par` | `shift(V)` with `par` shifted in |

The output bit replaces the original bit on its way to the destination or into the ISR.
For a CRC narrower than 32 bits in left mode, left-align it: put the polynomial (without its top term) in the top bits of `P`, and read the result from the top bits of `V`.
In right mode, put the bit-reversed polynomial in the low bits; this covers reflected CRCs of any width.
Parity is a 1-bit CRC: left mode with `P = 0x80000000`, result in bit 31.

## Edge capture and counter (G3)

A global 32-bit counter increments every `clk` cycle from reset and wraps after 2^32 cycles (86 s at 50 MHz).
Each engine's capture unit watches pin `CAPCFG[3:0]` in the pin space, after the input path.
On the selected edge it latches the counter into `CAPTURE`, and, if `CAPCFG` bit 6 is set, sets flag `CAPFLAG`.
If that flag is still set when the next edge arrives, the capture overrun sticky bit is set and `CAPTURE` is overwritten.
The captured time includes the pin's input latency, which is constant for a given configuration.

## Bit state machine (G2)

One table of 64 entries of 8 bits, written by the Host through `G2_ADDR` and `G2_DATA`.
The index is `{state[3:0], in1, in0}`.

| Entry bits | Meaning |
| --- | --- |
| 3:0 | Next state |
| 5:4 | Next outputs (out1, out0) |
| 6 | Emit: deliver out0 to the owner engine |
| 7 | Take: consume the feed bit |

| `G2_CTRL` bits | Meaning |
| --- | --- |
| 0 | Enable |
| 1 | Owner engine |
| 3:2 | Step mode: 0 every cycle, 1 on each owner tick, 2 when the feed is full, 3 never |
| 4 | Emit into the owner's ISR |

Input sources (`G2_IN0`, `G2_IN1`): 0-15 the pin space, 16 the feed bit, 17 feed full, others 0.

On each step, state and outputs load from the entry.
Outputs are registered, so they reach a pin one cycle after the step.
In step mode 2, every step consumes the feed bit; in other modes the take bit consumes it if the feed is full.

**Feed:** the owner engine's `out g2, n` writes one bit into a 1-bit feed.
A write is accepted only when the feed is empty, so an engine can hand over at most one bit every 2 cycles.

**Emit:** when the emit bit is set and `G2_CTRL` bit 4 is set, out0 shifts into the owner's ISR one cycle after the step, without any instruction.
It follows the same rules as a 1-bit `in`: direction, count, the checksum feed, and autopush.
If autopush finds the receive queue full, the word is dropped, the ISR clears, and the receive overflow sticky bit is set; the bit state machine never stalls.
In a cycle with an emission, an owner instruction that would modify the ISR or step the checksum register stalls for that cycle.

## Coordination flags

There are 8 flags shared by both engines, the capture units and the Host.
Within one cycle, all sets and clears combine, and a set wins over a clear.

## Instruction memory

64 entries of 16 bits, written only by the Host.
Each engine reads the entry at its PC in the same cycle.
A write takes effect at the end of the cycle that carries it.
Writing the entry an enabled engine is about to execute gives undefined results for that instruction; stop or steer the engine away first.

### Storage implementation (H2)

Instruction memory and the bit state machine table share one storage module.
The default build uses flip-flops.
Defining `PE_LATCH_STORE` builds the same module from latches, which use about 60% of the area.
The latch build is not yet verified on IHP CMOS5L and is not selected for tapeout.
In the latch build, a write becomes visible during the second half of the cycle after it is issued.

## Limits

- One instruction per tick; at 50 MHz the shortest pin period from a 2-instruction loop is 40 ns.
- Host link throughput is at most `f_clk / 8` clocks per second, 4 bits each.
- The checksum register processes at most one bit per cycle per engine.
- The bit state machine has 16 states and 2 inputs; codes needing more state must run in software.
- RTL simulation does not cover metastability, pad timing or the duty cycle of `clk`.
