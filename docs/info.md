## How it works

This chip implements digital protocols in software.
Two engines run programs from a shared 64-instruction memory that the Host loads after power-up, so new protocols need no new silicon.
The full behavior is defined in [the specification](spec.md).

Each engine reads and drives 13 protocol pins with cycle-exact timing.
It has PIO-style instructions (`jmp`, `wait`, `in`, `out`, `push`, `pull`, `irq`, `mov`, `set`), side-set and delay, shift registers with automatic refill and drain, and an integer clock divider.
Each engine owns 8 words of buffering that can act as transmit queue, receive queue or RAM.

General building blocks extend what the programs can do, without any protocol-specific hardware:

- A programmable 32-bit checksum shift register per engine computes any CRC up to 32 bits, parity, scramblers and test patterns as bits pass through.
- A shared bit state machine runs a 16-state, 64-entry table at up to one step per cycle, for line codes such as Manchester or NRZI, in either direction.
- A global 32-bit cycle counter with per-engine edge capture timestamps pin edges.
- Per-pin glitch filters, synchronizer bypass and falling-edge sampling refine the input timing.
- Pattern waits and indexed jumps help programs react quickly to multi-pin conditions and decode symbols.

The Host reaches every register through an SPI-style port, 1 bit wide after reset and 4 bits wide after one register write.

## How to test

Run `make setup` once, then `make check` from the repository root.
The cocotb tests drive only the top-level pins: they load programs through the Host port and check protocol behavior with independent peer models.
They cover the Host port, instruction timing, the general building blocks, and protocol programs for UART, SPI, I2C, JTAG, SWD, PS/2, CAN, low-speed USB and 10BASE-T.
[Protocol programs](protocols.md) lists what each program does, what was verified and the limits found.

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
