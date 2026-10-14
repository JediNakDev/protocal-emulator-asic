# SPDX-License-Identifier: Apache-2.0
"""UART, SPI and I2C implemented as programs, checked by independent peers."""

import cocotb

from chip import Chip, regs
from pe.proto import i2c
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


async def spi_run(dut, program_name, bypass, data_out, data_in, mode=0):
    chip = await new_chip(dut)
    prog = await chip.load_program(program_name)
    autopull = 0 if mode & 1 else regs.AUTOPULL  # the CPHA 1 program pulls itself
    await chip.setup_engine(0, prog, shiftctrl=autopull | regs.AUTOPUSH,
                            thresh=8 | (8 << 4), out_base=0, in_base=9, side_base=1)
    await chip.ereg(0, regs.SETPIN, 0 | (2 << 4))
    await chip.force_asm(0, "set pindirs, 3 side 0", side_count=1)
    await chip.pincfg(0, regs.DRIVE_PUSH_PULL)
    await chip.pincfg(1, regs.DRIVE_PUSH_PULL | (regs.PIN_INVERT if mode & 2 else 0))
    await chip.pincfg(9, regs.PIN_BYPASS if bypass else 0)
    sck = lambda: (chip.uio() >> 1) & 1  # noqa: E731
    await chip.cycles(2)
    periph = cocotb.start_soon(spi_peripheral(
        dut.clk, sck, lambda: chip.uio() & 1, chip.set_pin9, data_in, 8 * len(data_out), mode))
    await chip.push(0, [b << 8 for b in data_out[:4]])
    await chip.enable(1)
    rx = []
    for b in data_out[4:]:
        rx += await chip.pop_wait(0, 1)
        await chip.push(0, [b << 8])
    rx += await chip.pop_wait(0, len(data_out) - len(rx))
    assert await periph == data_out
    assert [w & 0xFF for w in rx] == data_in, [hex(w) for w in rx]
    await chip.cycles(20)
    assert sck() == mode >> 1, "SCK idles at CPOL after the transfer"


SPI_OUT = [0x9F, 0x00, 0xA5, 0x3C, 0xFF, 0x81]
SPI_IN = [0x5A, 0xC3, 0x01, 0x80, 0x7E, 0x00]


@cocotb.test()
async def test_spi_controller_mode0(dut):
    await spi_run(dut, "spi_cpha0", False, SPI_OUT, SPI_IN, mode=0)


@cocotb.test()
async def test_spi_controller_mode1(dut):
    await spi_run(dut, "spi_cpha1", False, SPI_OUT, SPI_IN, mode=1)


@cocotb.test()
async def test_spi_controller_mode2(dut):
    await spi_run(dut, "spi_cpha0", False, SPI_OUT, SPI_IN, mode=2)


@cocotb.test()
async def test_spi_controller_mode3(dut):
    await spi_run(dut, "spi_cpha1", False, SPI_OUT, SPI_IN, mode=3)


@cocotb.test()
async def test_spi_controller_12mhz_bypass(dut):
    """4 cycles per bit (12.5 MHz at 50 MHz) needs the MISO synchronizer bypassed."""
    await spi_run(dut, "spi_cpha0_fast", True, [0x9F, 0x00, 0xA5, 0x3C],
                  [0x5A, 0xC3, 0x01, 0x80])


async def i2c_setup(dut, speed, stretch_ns):
    chip = await new_chip(dut)
    sda, scl = 3, 4
    chip.pull_up((1 << sda) | (1 << scl))
    prog = await chip.load_program("i2c")
    await chip.setup_engine(0, prog, clkdiv=i2c.CLKDIV[speed], out_base=sda, set_base=sda,
                            in_base=sda, side_base=scl)
    # Release both lines in the pin registers before enabling open-drain drive.
    await chip.force_asm(0, "set pins, 1 side 1", side_count=1)
    await chip.pincfg(sda, regs.DRIVE_OPEN_DRAIN)
    await chip.pincfg(scl, regs.DRIVE_OPEN_DRAIN)
    target = I2CTarget(chip, sda, scl, address=0x42, stretch_ns=stretch_ns, speed=speed)
    cocotb.start_soon(target.run(dut.clk))
    contention = []

    async def watch():
        while True:
            await chip.cycles(1)
            if int(dut.contention.value):
                contention.append(1)
    cocotb.start_soon(watch())
    await chip.enable(1)
    return chip, target, contention


async def i2c_transfer(chip, words):
    rx = await chip.stream(0, words, len(words), timeout=4_000_000)
    return [i2c.decode(w) for w in rx]


async def i2c_session(dut, speed, stretch_ns=0):
    chip, target, contention = await i2c_setup(dut, speed, stretch_ns)
    # Write two bytes at register 0x10.
    got = await i2c_transfer(chip, i2c.write(0x42, [0x10, 0xAB, 0xCD]))
    assert got == [(0x84, 0), (0x10, 0), (0xAB, 0), (0xCD, 0)], got
    assert target.mem[0x10:0x12] == b"\xab\xcd"
    # Set the pointer, then read both back after a repeated START; the last
    # byte is not acknowledged.
    got = await i2c_transfer(chip, i2c.write(0x42, [0x10], stop=False) + i2c.read(0x42, 2))
    assert got == [(0x84, 0), (0x10, 0), (0x85, 0), (0xAB, 0), (0xCD, 1)], got
    # Another address is not acknowledged.
    got = await i2c_transfer(chip, i2c.write(0x21, []))
    assert got == [(0x42, 1)], got
    for _ in range(100):  # the last STOP follows the last receive word
        if target.stops == 3:
            break
        await chip.cycles(100)
    assert target.transactions == [[0x84, 0x10, 0xAB, 0xCD], [0x84, 0x10], [0x85, 0xAB, 0xCD], [0x42]], \
        target.transactions
    assert target.starts == 4 and target.stops == 3
    assert not target.violations, target.violations
    fastest = min(target.periods)
    assert 1e9 / speed <= fastest <= 1.1e9 / speed, f"SCL period {fastest} ns at {speed} Hz"
    assert not contention


@cocotb.test()
async def test_i2c_controller_100khz(dut):
    """Standard-mode writes, reads with repeated START, NACK, and the target
    stretching SCL by 7 us after each acknowledge clock."""
    await i2c_session(dut, 100_000, stretch_ns=7000)


@cocotb.test()
async def test_i2c_controller_400khz(dut):
    """Fast-mode, with the target stretching SCL by 2 us."""
    await i2c_session(dut, 400_000, stretch_ns=2000)


@cocotb.test()
async def test_i2c_controller_400khz_no_stretch(dut):
    await i2c_session(dut, 400_000)
