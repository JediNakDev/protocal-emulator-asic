# SPDX-License-Identifier: Apache-2.0
"""UART, SPI and I2C implemented as programs, checked by independent peers
in absolute time at the 50 MHz clock profile. Each program also runs at a
wrong clock as a negative control, which the peers must reject."""

import cocotb

from chip import MHZ_50, Chip, fails_at_wrong_clock, regs
from pe.proto import i2c, spi
from peers import I2CTarget, SpiPeripheral, Wave, sleep_ns, uart_receive, uart_transmit

PROFILE = MHZ_50


async def new_chip(dut, hz=PROFILE):
    chip = Chip(dut, hz)
    await chip.start()
    await chip.set_quad()
    return chip


def uart_clkdiv(baud):
    """The programs take 8 ticks per bit: baud = f / (8 (CLKDIV + 1))."""
    return round(PROFILE / (8 * baud)) - 1


async def uart_tx_session(dut, baud, hz=PROFILE):
    chip = await new_chip(dut, hz)
    chip.pull_up(0x01)
    prog = await chip.load_program("uart_tx")
    await chip.setup_engine(0, prog, clkdiv=uart_clkdiv(baud), shiftctrl=regs.OUT_RIGHT,
                            out_base=0, side_base=0)
    await chip.force_asm(0, "set pins, 1\nset pindirs, 1", side_count=1, side_opt=True)
    await chip.pincfg(0, regs.DRIVE_PUSH_PULL)
    data = [0x00, 0xFF, 0x55, 0xAA, 0x0F, 0x80]
    rx = cocotb.start_soon(uart_receive(Wave(dut.uio_in, 0), baud, len(data)))
    await chip.enable(1)
    await chip.stream(0, data, 0, timeout=20 * len(data) * 10 * PROFILE // baud)
    got, violations = await rx
    assert not violations, violations
    assert got == data, got


@cocotb.test()
async def test_uart_tx(dut):
    """115200 baud: CLKDIV 53 gives 115,741 baud (+0.47%)."""
    await uart_tx_session(dut, 115_200)


@cocotb.test()
async def test_uart_tx_fast(dut):
    """3.125 Mbaud, the fastest rate at 8 ticks per bit (CLKDIV 1)."""
    await uart_tx_session(dut, 3_125_000)


async def uart_rx_session(dut, baud, hz=PROFILE):
    chip = await new_chip(dut, hz)
    chip.set_pin8(1)
    prog = await chip.load_program("uart_rx")
    await chip.setup_engine(0, prog, clkdiv=uart_clkdiv(baud), shiftctrl=regs.IN_RIGHT,
                            in_base=8, jmp_pin=8)
    await chip.enable(1)
    data = [0x00, 0xFF, 0x55, 0xA3, 0x7E]
    await uart_transmit(chip.set_pin8, baud, data)
    words = await chip.pop_wait(0, len(data), timeout=20 * 10 * PROFILE // baud)
    assert [w >> 8 for w in words] == data, f"UART bytes {[hex(w >> 8) for w in words]}"
    assert (await chip.read8(regs.FLAGS)) & 0x10 == 0, "UART framing error"
    return chip


@cocotb.test()
async def test_uart_rx(dut):
    chip = await uart_rx_session(dut, 115_200)
    # A frame with a low stop bit raises flag 4 and is not pushed.
    await uart_transmit(chip.set_pin8, 115_200, [0x12], stop=0)
    await chip.cycles(1000)
    assert (await chip.read8(regs.FLAGS)) & 0x10
    assert await chip.levels(0) == (0, 0)


@cocotb.test()
async def test_uart_wrong_clock(dut):
    """At 40 MHz the 50 MHz settings send 92,593 baud, which the receiver
    rejects, and receive garbage from a 115200 baud sender."""
    msg = await fails_at_wrong_clock(lambda hz: uart_tx_session(dut, 115_200, hz), PROFILE)
    assert "timing:" in msg, msg
    msg = await fails_at_wrong_clock(lambda hz: uart_rx_session(dut, 115_200, hz), PROFILE)
    assert msg.startswith("UART") or "words" in msg, msg  # wrong bytes, framing or lost bytes


SPI_MOSI, SPI_SCK, SPI_CS_N, SPI_MISO = 0, 1, 2, 9


async def spi_run(dut, program_name, bypass, transactions, data_in, mode=0, rate=6_250_000,
                  hz=PROFILE, pause_ns=0):
    """Runs each transaction (a list of bytes) under its own chip select,
    queued back to back. With pause_ns, the Host instead holds back the
    last byte of each transaction that long, so SCK idles with CS_N held
    low."""
    chip = await new_chip(dut, hz)
    chip.pull_up(1 << SPI_CS_N)
    prog = await chip.load_program(program_name)
    await chip.setup_engine(0, prog, shiftctrl=regs.AUTOPUSH, thresh=8, out_base=SPI_MOSI,
                            set_base=SPI_MOSI, set_count=3, in_base=SPI_MISO, side_base=SPI_SCK)
    await chip.force_asm(0, "set pindirs, 7 side 2", side_count=2)  # SCK idle, CS_N high
    await chip.pincfg(SPI_MOSI, regs.DRIVE_PUSH_PULL)
    await chip.pincfg(SPI_SCK, regs.DRIVE_PUSH_PULL | (regs.PIN_INVERT if mode & 2 else 0))
    await chip.pincfg(SPI_CS_N, regs.DRIVE_PUSH_PULL)
    await chip.pincfg(SPI_MISO, regs.PIN_BYPASS if bypass else 0)
    await chip.cycles(2)
    periph = SpiPeripheral(Wave(dut.uio_in, SPI_SCK), Wave(dut.uio_in, SPI_MOSI),
                           Wave(dut.uio_in, SPI_CS_N), chip.set_pin9, mode, data_in, rate)
    task = cocotb.start_soon(periph.run(len(transactions)))
    await chip.enable(1)
    rx = []
    if pause_ns:
        for t in transactions:
            words = spi.transaction(t)
            rx += await chip.stream(0, words[:-1], len(words) - 1)
            await sleep_ns(pause_ns)
            rx += await chip.stream(0, words[-1:], 1)
    else:  # back to back: each transaction is queued before the last ends
        words = [w for t in transactions for w in spi.transaction(t)]
        rx = await chip.stream(0, words, len(words))
    assert await task == transactions
    violations = periph.check()
    assert not violations, violations[:5]
    expected = [b for t in transactions for b in data_in[:len(t)]]
    assert [spi.decode(w) for w in rx] == expected, [hex(w) for w in rx]
    await chip.cycles(20)
    assert periph.sck.read() == mode >> 1, "SCK idles at CPOL after the transfer"
    assert periph.cs_n.read() == 1, "CS_N high after the transfer"


SPI_OUT = [[0x9F, 0x00, 0xA5, 0x3C, 0xFF, 0x81], [0x05], [0x03, 0x12]]
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
    """4 cycles per bit (12.5 MHz) needs the MISO synchronizer bypassed."""
    await spi_run(dut, "spi_cpha0_fast", True, SPI_OUT, SPI_IN, rate=12_500_000)


@cocotb.test()
async def test_spi_host_pause(dut):
    """A Host 20 us late with the last byte of a transaction only stretches
    the idle SCK: CS_N stays low, so the peripheral sees one transaction."""
    for name, mode in (("spi_cpha0", 0), ("spi_cpha1", 3)):
        await spi_run(dut, name, False, [[0x9F, 0x00, 0xA5], [0x3C, 0x81]], SPI_IN, mode,
                      pause_ns=20_000)


@cocotb.test()
async def test_spi_wrong_clock(dut):
    """SCK runs 20% slow at 40 MHz: every program fails the rate check."""
    for name, mode, rate in (("spi_cpha0", 0, 6_250_000), ("spi_cpha1", 1, 6_250_000),
                             ("spi_cpha0_fast", 0, 12_500_000)):
        msg = await fails_at_wrong_clock(
            lambda hz: spi_run(dut, name, name.endswith("fast"), SPI_OUT[:1], SPI_IN, mode, rate, hz),
            PROFILE)
        assert "timing: clock period" in msg, (name, msg)


async def i2c_setup(dut, speed, stretch_ns, hz):
    chip = await new_chip(dut, hz)
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


async def i2c_session(dut, speed, stretch_ns=0, hz=PROFILE):
    chip, target, contention = await i2c_setup(dut, speed, stretch_ns, hz)
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
    assert not target.violations, f"timing: {target.violations}"
    fastest = min(target.periods)
    assert 1e9 / speed <= fastest <= 1.1e9 / speed, f"timing: SCL period {fastest} ns at {speed} Hz"
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


@cocotb.test()
async def test_i2c_wrong_clock(dut):
    """At 40 MHz the Fast-mode settings give a 320 kHz SCL."""
    msg = await fails_at_wrong_clock(lambda hz: i2c_session(dut, 400_000, hz=hz), PROFILE)
    assert "timing: SCL period" in msg, msg
