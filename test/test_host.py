# SPDX-License-Identifier: Apache-2.0
"""Host port, register access, reset state (H5) and status reporting."""

import cocotb

from chip import Chip, regs


@cocotb.test()
async def test_reset_state(dut):
    """H5: after reset every uio pin is released and every output drives 0."""
    chip = Chip(dut)
    await chip.start()
    for _ in range(20):
        assert int(dut.uio_oe.value) == 0
        assert int(dut.uio_out.value) == 0
        assert chip.uo() == 0
        await chip.cycles(1)
    # Reset again in the middle of activity: enable an engine driving a pin.
    prog = await chip.load("set pins, 1\njmp 0")
    await chip.setup_engine(0, prog)
    await chip.force_asm(0, "set pindirs, 1")
    await chip.pincfg(0, regs.DRIVE_PUSH_PULL)
    await chip.enable(1)
    await chip.cycles(10)
    assert int(dut.uio_oe.value) & 1 == 1
    dut.rst_n.value = 0
    await chip.cycles(1)
    assert int(dut.uio_oe.value) == 0, "reset must release pins immediately"
    assert chip.uo() == 0
    dut.rst_n.value = 1
    await chip.cycles(10)
    assert int(dut.uio_oe.value) == 0
    assert (await chip.read8(regs.CTRL)) == 0


@cocotb.test()
async def test_id_both_widths(dut):
    chip = Chip(dut)
    await chip.start()
    assert await chip.read(regs.ID, 2) == [regs.ID_VALUE, regs.VERSION_VALUE]
    await chip.set_quad()
    assert await chip.read(regs.ID, 2) == [regs.ID_VALUE, regs.VERSION_VALUE]
    assert await chip.read8(regs.HOSTCFG) == 1
    # Back to 1-bit.
    await chip.write(regs.HOSTCFG, 0)
    chip.quad = False
    assert await chip.read8(regs.ID) == regs.ID_VALUE


@cocotb.test()
async def test_register_readback(dut):
    chip = Chip(dut)
    await chip.start()
    await chip.set_quad()
    # Auto-increment across a multi-byte register.
    await chip.write32(regs.engine_reg(1, regs.LFSR_POLY), 0xDEADBEEF)
    assert await chip.read32(regs.engine_reg(1, regs.LFSR_POLY)) == 0xDEADBEEF
    await chip.write32(regs.engine_reg(0, regs.LFSR_VALUE), 0x12345678)
    assert await chip.read32(regs.engine_reg(0, regs.LFSR_VALUE)) == 0x12345678
    vals = [0x5A, 0xA5, 0x3F, 0x15, 0x7F]
    await chip.ereg(0, regs.CLKDIV, vals)
    assert await chip.eread(0, regs.CLKDIV, 5) == [0x5A, 0xA5, 0x3F, 0x15, 0x7F & 0x7F]
    for p in range(13):
        await chip.pincfg(p, (p * 37) & 0xF3)  # keep drive modes off or push-pull only
    for p in range(13):
        assert await chip.read8(regs.PINCFG + p) == (p * 37) & 0xF3
    assert await chip.read8(0x7F & 0x2D) == 0  # unused
    await chip.write(regs.IRQ_MASK, 0xA5)
    assert await chip.read8(regs.IRQ_MASK) == 0xA5


@cocotb.test()
async def test_status_sticky_hirq(dut):
    chip = Chip(dut)
    await chip.start()
    await chip.set_quad()
    st = await chip.status()
    assert st & 0x0F == 0x0A, f"both TX queues not full, RX empty: {st:#x}"
    # Overfill engine 0's 4-entry transmit queue.
    await chip.push(0, [1, 2, 3, 4, 5])
    assert await chip.levels(0) == (4, 0)
    sticky = await chip.read8(regs.STICKY)
    assert sticky == regs.STK_TX_OVF0
    st = await chip.status()
    assert st & 0x80 and not st & 0x02
    # Underflow on read of an empty receive queue returns 0.
    assert await chip.pop(1) == [0]
    assert await chip.read8(regs.STICKY) == regs.STK_TX_OVF0 | regs.STK_RX_UNF1
    # HIRQ follows the masks.
    assert chip.uo() & 0x10 == 0
    await chip.write(regs.STICKY_MASK, regs.STK_RX_UNF1)
    await chip.cycles(2)
    assert chip.uo() & 0x10
    await chip.write(regs.STICKY, 0xFF)
    await chip.cycles(2)
    assert await chip.read8(regs.STICKY) == 0
    assert chip.uo() & 0x10 == 0
    # Flags: Host set, IRQ, write-1-to-clear.
    await chip.write(regs.IRQ_MASK, 0x04)
    await chip.write(regs.FLAG_SET, 0x06)
    await chip.cycles(2)
    assert await chip.read8(regs.FLAGS) == 0x06
    assert chip.uo() & 0x10
    await chip.write(regs.FLAGS, 0x04)
    await chip.cycles(2)
    assert await chip.read8(regs.FLAGS) == 0x02
    assert chip.uo() & 0x10 == 0


@cocotb.test()
async def test_counter(dut):
    chip = Chip(dut)
    await chip.start()
    await chip.set_quad()
    a = await chip.read32(regs.COUNTER)
    await chip.cycles(1000)
    b = await chip.read32(regs.COUNTER)
    # One quad read of 5 bytes takes about 10 HSCK periods of 8 cycles plus overhead.
    assert 1000 < b - a < 1300, (a, b)


@cocotb.test()
async def test_interrupted_read_keeps_data(dut):
    """A receive-queue read cut short before its high byte must not pop."""
    chip = Chip(dut)
    await chip.start()
    await chip.set_quad()
    # Engine 0 pushes two words through a forced sequence.
    await chip.force_asm(0, "set x, 21\nmov isr, x\npush\nset x, 9\nmov isr, x\npush")
    await chip.cycles(10)
    assert await chip.levels(0) == (0, 2)
    lo = await chip.read(regs.engine_reg(0, regs.FIFO), 1)  # low byte only
    assert lo == [21]
    assert await chip.levels(0) == (0, 2)
    assert await chip.pop(0, 2) == [21, 9]
    assert await chip.levels(0) == (0, 0)
