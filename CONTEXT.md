# Project context

This is an open-source entry for [Jane Street's protocol emulator ASIC competition](https://blog.janestreet.com/protocol-emulator-asic-competition/).
The chip is intended for hardware debugging, experimentation, and reverse engineering.
A small programmable engine reads pins, drives pins, and controls timing precisely enough to implement protocols in firmware.
Its usefulness comes from supporting new protocols after fabrication, within its timing and I/O limits.

The competition starts with UART, SPI, and I2C and values unique functionality and novel design or verification methods.
RP2040 PIO and TI PRU are architectural inspiration.
Low-speed USB and 10 Mbit Ethernet are optional stretch goals.

## Constraints and current state

- Process and flow: IHP 130nm CMOS5L through Tiny Tapeout.
- Allocation: 6x4 tiles under the currently published rules.
- Submission deadline: January 18, 2027; recheck the announcement before submission.
- Clock: initial 50 MHz target, pending routed timing validation.
- Repository: CMOS5L Verilog template with cocotb tests and Verilator lint.
- Current RTL: the emulator in `docs/spec.md`, verified in RTL simulation; not yet through a CMOS5L GDS build.

`docs/roadmap.md` contains the proposed architecture and milestones, beginning with UART transmission.
These are working plans, separate from the competition rules.

## Terms

- **Engine:** hardware that executes pin and timing instructions.
- **Program:** instructions uploaded to the chip to implement protocol behavior.
- **Host:** the external system that loads programs and exchanges control and data with the chip.
