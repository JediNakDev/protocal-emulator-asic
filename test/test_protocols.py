# SPDX-License-Identifier: Apache-2.0
"""UART, SPI and I2C implemented as programs, checked by independent peers."""

import cocotb

from chip import Chip, regs
from peers import I2CTarget, spi_peripheral, uart_receive, uart_transmit


async def new_chip(dut):
    chip = Chip(dut)
    await chip.start()
    await chip.set_quad()
    return chip


@cocotb.test()
async def test_uart_tx(dut):
    chip = await new_chip(dut)
    chip.pull_up(0x01)
    prog = await chip.load_program("uart_tx")
    await chip.setup_engine(0, prog, clkdiv=1, shiftctrl=regs.OUT_RIGHT, out_base=0, side_base=0)
    await chip.force_asm(0, "set pins, 1\nset pindirs, 1", side_count=1, side_opt=True)
    await chip.pincfg(0, regs.DRIVE_PUSH_PULL)
    data = [0x00, 0xFF, 0x55, 0xAA, 0x0F, 0x80]
    rx = cocotb.start_soon(uart_receive(dut.clk, lambda: chip.uio() & 1, 16, len(data)))
    await chip.push(0, data[:4])
    await chip.enable(1)
    await chip.cycles(200)
    await chip.push(0, data[4:])
    assert await rx == data


@cocotb.test()
async def test_uart_rx(dut):
    chip = await new_chip(dut)
    chip.set_pin8(1)
    prog = await chip.load_program("uart_rx")
    await chip.setup_engine(0, prog, clkdiv=1, shiftctrl=regs.IN_RIGHT, in_base=8, jmp_pin=8)
    await chip.enable(1)
    data = [0x00, 0xFF, 0x55, 0xA3, 0x7E]
    await uart_transmit(dut.clk, chip.set_pin8, 16, data)
    words = await chip.pop_wait(0, len(data))
    assert [w >> 8 for w in words] == data, [hex(w) for w in words]
    assert (await chip.read8(regs.FLAGS)) & 0x10 == 0
    # A frame with a low stop bit raises flag 4 and is not pushed.
    await uart_transmit(dut.clk, chip.set_pin8, 16, [0x12], stop=0)
    await chip.cycles(100)
    assert (await chip.read8(regs.FLAGS)) & 0x10
    assert await chip.levels(0) == (0, 0)


async def spi_run(dut, program_name, bypass, data_out, data_in):
    chip = await new_chip(dut)
    prog = await chip.load_program(program_name)
    await chip.setup_engine(0, prog, shiftctrl=regs.AUTOPULL | regs.AUTOPUSH,
                            thresh=8 | (8 << 4), out_base=0, in_base=9, side_base=1)
    await chip.ereg(0, regs.SETPIN, 0 | (2 << 4))
    await chip.force_asm(0, "set pindirs, 3 side 0", side_count=1)
    await chip.pincfg(0, regs.DRIVE_PUSH_PULL)
    await chip.pincfg(1, regs.DRIVE_PUSH_PULL)
    await chip.pincfg(9, regs.PIN_BYPASS if bypass else 0)
    periph = cocotb.start_soon(spi_peripheral(
        dut.clk, lambda: (chip.uio() >> 1) & 1, lambda: chip.uio() & 1,
        chip.set_pin9, data_in, 8 * len(data_out)))
    await chip.push(0, [b << 8 for b in data_out[:4]])
    await chip.enable(1)
    rx = []
    for b in data_out[4:]:
        rx += await chip.pop_wait(0, 1)
        await chip.push(0, [b << 8])
    rx += await chip.pop_wait(0, len(data_out) - len(rx))
    assert await periph == data_out
    assert [w & 0xFF for w in rx] == data_in, [hex(w) for w in rx]


@cocotb.test()
async def test_spi_controller_mode0(dut):
    await spi_run(dut, "spi_mode0", False, [0x9F, 0x00, 0xA5, 0x3C, 0xFF, 0x81],
                  [0x5A, 0xC3, 0x01, 0x80, 0x7E, 0x00])


@cocotb.test()
async def test_spi_controller_12mhz_bypass(dut):
    """4 cycles per bit (12.5 MHz at 50 MHz) needs the MISO synchronizer bypassed."""
    await spi_run(dut, "spi_mode0_fast", True, [0x9F, 0x00, 0xA5, 0x3C],
                  [0x5A, 0xC3, 0x01, 0x80])


@cocotb.test()
async def test_i2c_controller_write(dut):
    chip = await new_chip(dut)
    sda, scl = 3, 4
    chip.pull_up((1 << sda) | (1 << scl))
    prog = await chip.load_program("i2c_write")
    await chip.setup_engine(0, prog, clkdiv=1, out_base=sda, set_base=sda, in_base=sda,
                            side_base=scl)
    # Release both lines in the pin registers before enabling open-drain drive.
    await chip.force_asm(0, "set pins, 1 side 1", side_count=1, side_opt=True)
    await chip.pincfg(sda, regs.DRIVE_OPEN_DRAIN)
    await chip.pincfg(scl, regs.DRIVE_OPEN_DRAIN)
    target = I2CTarget(chip, sda, scl, address=0x42, stretch=40)
    cocotb.start_soon(target.run(dut.clk))

    contention = []

    async def watch():
        while True:
            await chip.cycles(1)
            if int(dut.contention.value):
                contention.append(1)
    cocotb.start_soon(watch())

    def word(byte, start=False, stop=False):
        return (start << 15) | (byte << 7) | (stop << 6)

    await chip.enable(1)
    # Transaction 1: address 0x42 write, two data bytes.
    await chip.push(0, [word(0x84, start=True), word(0x11), word(0xEE, stop=True)])
    acks = await chip.pop_wait(0, 3)
    assert acks == [0, 0, 0], acks
    # Transaction 2: wrong address is NACKed.
    await chip.push(0, [word(0x20, start=True, stop=True)])
    acks = await chip.pop_wait(0, 1)
    assert acks == [1], acks
    await chip.cycles(200)
    assert target.transactions == [[0x84, 0x11, 0xEE], [0x20]], target.transactions
    assert target.starts == 2 and target.stops == 2
    assert not contention
