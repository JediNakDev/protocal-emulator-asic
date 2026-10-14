## How it works

This chip implements digital protocols in software.
Two engines run programs from a shared 64-instruction memory that the Host loads after power-up, so new protocols need no new silicon.
The full behavior is defined in [the specification](spec.md).

Each engine reads and drives 13 protocol pins with cycle-exact timing.
It has PIO-style instructions (`jmp`, `wait`, `in`, `out`, `push`, `pull`, `irq`, `mov`, `set`), side-set and delay, shift registers with automatic refill and drain, and an integer clock divider.
Each engine owns 8 words of buffering that can act as transmit queue, receive queue or RAM.
Instruction set version 2 adds waits with deadlines and shift-count reads.
Receive programs use these general operations for bounded USB replies, CAN resynchronization and exact message boundaries; [the design review](design-review.md) tracks the remaining mandatory protocol gaps.

General building blocks extend what the programs can do, without any protocol-specific hardware:

- A programmable 32-bit checksum shift register per engine computes any CRC up to 32 bits, parity, scramblers and test patterns as bits pass through.
- A shared bit state machine runs a 16-state, 64-entry table at up to one step per cycle, for line codes such as Manchester or NRZI, in either direction.
- A global 32-bit cycle counter with per-engine edge capture timestamps pin edges.
- Per-pin glitch filters, synchronizer bypass and falling-edge sampling refine the input timing.
- Pattern waits and indexed jumps help programs react quickly to multi-pin conditions and decode symbols.

Together these make the chip a cycle-exact recorder and signal generator for protocols it has no program for.
Waveform capture and replay programs timestamp pin edges with one-cycle resolution (20 ns at the 50 MHz target) and replay them, optionally edited by the Host for fault injection.
Capture requires an 8-cycle input filter and sufficient Host bandwidth; loss flags identify overwritten edges or dropped timestamp words.
See [protocol programs](protocols.md#waveform-capture-and-replay) for minimum pulse widths, buffering, capture rates, and replay requirements.

The Host reaches every register through an SPI-style port, 1 bit wide after reset and 4 bits wide after one register write.

## How to test

Run `make setup` once, then `make check` from the repository root.
The cocotb tests drive only the top-level pins: they load programs through the Host port and check protocol behavior with independent peer models.
They cover the Host port, instruction timing, the general building blocks, and protocol programs for UART, SPI, I2C, JTAG, SWD, PS/2, CAN, low-speed USB and 10BASE-T.
[Protocol programs](protocols.md) lists what each program does, what was verified and the limits found.
A reference model written from the specification runs random programs alongside the chip and must agree on every pin in every cycle.
Selected formal checks cover fetch consistency, buffer occupancy, and safe reset outputs; [the formal guide](../formal/README.md) defines their scope.

Publishing a release or prerelease runs CMOS5L hardening, precheck, functional gate-level tests, and a separate global-route timing estimate.
Recorded routed builds meet 50 MHz at the typical corner but fail setup timing at the slow corner.
The latest measured RTL (`17995b3`) has +4.05 ns typical-corner setup slack and -4.14 ns slow-corner setup slack; [the area log](area-log.md) records the source and reports for each measurement.

On the demo board, the RP2040 acts as Host:

1. Hold `HCS_N` high and keep `HSCK` low.
2. Read register 0x00 in 1-bit mode: send 0x80 and one more byte; the second byte returned is 0x50.
3. Load a program into instruction memory through `IMEM_ADDR` and `IMEM_DATA`.
4. Configure the engine and pins, then enable it through `CTRL`.

`HSCK` must stay high and low for at least 4 clock cycles each.

## External hardware

Protocol pins `P0`-`P7` are bidirectional and support push-pull or open-drain drive.
Lines that must idle high (UART TX, I2C, USB) need external pull-ups on these pins, because every pin is released after reset.
`P8` and `P9` are inputs only, and `P10`-`P12` are outputs only that drive 0 after reset.
I2C needs pull-up resistors on SDA and SCL.
