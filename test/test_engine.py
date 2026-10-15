# SPDX-License-Identifier: Apache-2.0
"""Engine instruction semantics and timing, observed through the top-level pins."""

import cocotb
from cocotb.triggers import RisingEdge

from chip import Chip, assemble, regs


def runs(samples):
    """Collapse samples into [(value, length), ...]."""
    out = []
    for s in samples:
        if out and out[-1][0] == s:
            out[-1][1] += 1
        else:
            out.append([s, 1])
    return [tuple(r) for r in out]


async def sample(chip, n, fn):
    out = []
    for _ in range(n):
        await RisingEdge(chip.dut.clk)
        out.append(fn())
    return out


async def enable_and_sample(chip, mask, n, fn):
    """Start sampling, then enable engines, so no cycle after enable is missed."""
    task = cocotb.start_soon(sample(chip, n, fn))
    await chip.enable(mask)
    return await task


async def new_chip(dut):
    chip = Chip(dut)
    await chip.start()
    await chip.set_quad()
    return chip


async def run_program(chip, src, e=0, offset=0, **kw):
    prog = await chip.load(src, offset)
    await chip.setup_engine(e, prog, offset, **kw)
    return prog


def uio_bit(chip, b):
    return lambda: (int(chip.dut.uio_out.value) >> b) & 1


@cocotb.test()
async def test_set_delay_wrap_timing(dut):
    chip = await new_chip(dut)
    await run_program(chip, """
        .wrap_target
            set pins, 1 [3]
            set pins, 0 [5]
        .wrap
    """)
    await chip.force_asm(0, "set pindirs, 1")
    await chip.pincfg(0, regs.DRIVE_PUSH_PULL)
    await chip.enable(1)
    r = runs(await sample(chip, 80, uio_bit(chip, 0)))[1:-1]
    assert r and all(length == (4 if v else 6) for v, length in r), r

    # Clock divider: every cycle count scales by CLKDIV + 1.
    await chip.enable(0)
    await chip.ereg(0, regs.CLKDIV, [2, 0])
    await chip.enable(1)
    r = runs(await sample(chip, 200, uio_bit(chip, 0)))[1:-1]
    assert r and all(length == (12 if v else 18) for v, length in r), r


@cocotb.test()
async def test_loop_count(dut):
    """jmp x-- loops take one cycle per iteration, with no branch penalty."""
    chip = await new_chip(dut)
    await run_program(chip, """
            set pins, 1
            set x, 9
        loop:
            jmp x-- loop
            set pins, 0
        stop:
            jmp stop
    """)
    await chip.force_asm(0, "set pindirs, 1")
    await chip.pincfg(0, regs.DRIVE_PUSH_PULL)
    r = runs(await enable_and_sample(chip, 1, 60, uio_bit(chip, 0)))
    assert (1, 12) in r, r


@cocotb.test()
async def test_jmp_conditions(dut):
    """Forced jumps on a disabled engine: PC moves to 50 only if taken."""
    chip = await new_chip(dut)
    await chip.ereg(0, regs.INPIN, 8 << 4)  # jmp pin = pin 8

    async def check(setup, cond, taken):
        await chip.force_asm(0, "jmp 0")
        for line in setup:
            await chip.force_asm(0, line)
        await chip.force_asm(0, f"jmp {cond} 50")
        pc = (await chip.dbg(0, 0)) & 0x3F
        assert pc == (50 if taken else 0), (setup, cond, pc)

    await check([], "", True)
    await check(["set x, 0"], "!x", True)
    await check(["set x, 1"], "!x", False)
    await check(["set x, 1"], "x--", True)
    assert await chip.dbg(0, 1) == 0
    await check(["set x, 0"], "x--", False)
    assert await chip.dbg(0, 1) == 0xFFFF
    await check(["set y, 0"], "!y", True)
    await check(["set y, 2"], "y--", True)
    assert await chip.dbg(0, 2) == 1
    await check(["set x, 3", "set y, 3"], "x!=y", False)
    await check(["set x, 3", "set y, 4"], "x!=y", True)
    chip.set_pin8(0)
    await check([], "pin", False)
    chip.set_pin8(1)
    await check([], "pin", True)
    await chip.write(regs.CMD, regs.CMD_RESTART0)  # OSR empty
    await check([], "!osre", False)
    await check(["pull noblock"], "!osre", True)
    # Pin pattern as the jmp pin condition (G4): pins & Y == X.
    await chip.ereg(0, regs.EXECCFG, 0x40)
    chip.drive_uio(0, 1)
    chip.drive_uio(1, 0)
    assert (await chip.read8(regs.PINS_L)) & 3 == 1
    await check(["set y, 3", "set x, 1"], "pin", True)
    await check(["set y, 3", "set x, 3"], "pin", False)


@cocotb.test()
async def test_forced_step_debug(dut):
    chip = await new_chip(dut)
    await chip.load("set y, 3\nset y, 7\nset y, 9\njmp 0")
    await chip.force_asm(0, "set x, 21")
    assert await chip.dbg(0, 1) == 21
    await chip.force_asm(0, "jmp 0")
    for expect_pc, expect_y in [(1, 3), (2, 7), (3, 9), (0, 9)]:
        await chip.write(regs.CMD, regs.CMD_STEP0)
        assert (await chip.dbg(0, 0)) & 0x3F == expect_pc
        assert await chip.dbg(0, 2) == expect_y
    assert await chip.dbg(0, 6) == assemble("set y, 3").code[0]  # next instruction
    # mov operations.
    await chip.force_asm(0, "set x, 1\nmov y, ::x")
    assert await chip.dbg(0, 2) == 0x8000
    await chip.force_asm(0, "mov y, ~x")
    assert await chip.dbg(0, 2) == 0xFFFE


@cocotb.test()
async def test_autopull_zero_cycle(dut):
    """One bit per cycle across word boundaries: autopull costs no cycles."""
    chip = await new_chip(dut)
    await run_program(chip, "out pins, 1", shiftctrl=regs.AUTOPULL | regs.OUT_RIGHT)
    await chip.force_asm(0, "set pindirs, 1")
    await chip.pincfg(0, regs.DRIVE_PUSH_PULL)
    words = [0xF0A5, 0x3C96, 0x1248]
    await chip.push(0, words)
    bits = [(w >> i) & 1 for w in words for i in range(16)]
    s = await enable_and_sample(chip, 1, 100, uio_bit(chip, 0))
    hits = [i for i in range(len(s) - 48) if s[i:i + 48] == bits]
    assert hits, s
    # After the queue empties the engine stalls with the last bit held.
    assert all(v == bits[-1] for v in s[hits[0] + 48:])
    st = await chip.status()
    assert st & 0x10, "engine 0 must report a stall"


@cocotb.test()
async def test_autopush_and_full_stall(dut):
    chip = await new_chip(dut)
    await run_program(chip, "in count, 16", shiftctrl=regs.AUTOPUSH)
    await chip.enable(1)
    await chip.cycles(20)
    assert await chip.levels(0) == (0, 4)
    w = await chip.pop(0, 4)
    assert all(w[i + 1] == (w[i] + 1) & 0xFFFF for i in range(3)), w
    # The engine resumes after the Host frees space.
    await chip.cycles(20)
    assert await chip.levels(0) == (0, 4)
    assert not (await chip.read8(regs.STICKY)) & regs.STK_RX_OVF0


@cocotb.test()
async def test_side_set_and_stall(dut):
    chip = await new_chip(dut)
    await run_program(chip, """
        .side_set 1
        .wrap_target
            nop side 1 [1]
            nop side 0 [1]
        .wrap
    """, side_base=1)
    await chip.force_asm(0, "set pindirs, 3", )
    await chip.ereg(0, regs.SETPIN, 0 | (2 << 4))
    await chip.force_asm(0, "set pindirs, 3")
    await chip.pincfg(1, regs.DRIVE_PUSH_PULL)
    await chip.enable(1)
    r = runs(await sample(chip, 40, uio_bit(chip, 1)))[1:-1]
    assert r and all(length == 2 for _, length in r), r

    # Side-set applies even while the instruction stalls.
    await chip.enable(0)
    await run_program(chip, """
        .side_set 1 opt
            nop side 0
            pull block side 1
    """, side_base=1)
    await chip.enable(1)
    await chip.cycles(10)
    assert uio_bit(chip, 1)() == 1
    assert (await chip.status()) & 0x10


@cocotb.test()
async def test_flags_between_engines(dut):
    chip = await new_chip(dut)
    p0 = assemble("irq wait 1\nset pins, 1\nhalt: jmp halt")
    p1 = assemble("wait 1 flag 1\nset pins, 1\nhalt: jmp halt")
    await chip.load(p0, 0)
    await chip.load(p1, 10)
    await chip.setup_engine(0, p0, 0, set_base=0)
    await chip.setup_engine(1, p1, 10, set_base=1)
    await chip.force_asm(0, "set pindirs, 1")
    await chip.force_asm(1, "set pindirs, 1")
    await chip.pincfg(0, regs.DRIVE_PUSH_PULL)
    await chip.pincfg(1, regs.DRIVE_PUSH_PULL | regs.OWNER_E1)
    await chip.enable(1)
    await chip.cycles(10)
    assert (await chip.read8(regs.FLAGS)) == 0x02
    assert int(dut.uio_out.value) & 3 == 0
    assert (await chip.status()) & 0x10, "engine 0 waits for the flag to clear"
    await chip.enable(3)
    await chip.cycles(10)
    assert int(dut.uio_out.value) & 3 == 3
    assert (await chip.read8(regs.FLAGS)) == 0


@cocotb.test()
async def test_out_exec(dut):
    """A1: instructions streamed through the transmit queue execute."""
    chip = await new_chip(dut)
    await run_program(chip, "out exec, 16", shiftctrl=regs.AUTOPULL)
    words = [assemble(s).code[0] for s in ("set x, 17", "set y, 5", "mov isr, x", "push")]
    await chip.push(0, words)
    await chip.enable(1)
    await chip.cycles(20)
    assert await chip.pop(0) == [17]
    await chip.enable(0)
    assert await chip.dbg(0, 2) == 5
    assert (await chip.dbg(0, 0)) & 0x3F == 0, "exec'd instructions do not move PC"


@cocotb.test()
async def test_indexed_jump(dut):
    """G8: out pc, n jumps to EXECCFG jump base + data."""
    chip = await new_chip(dut)
    await run_program(chip, """
        start: out pc, 2
               jmp h0
               jmp h1
               jmp h2
               jmp h3
        h0:    set pins, 1
               jmp start
        h1:    set pins, 2
               jmp start
        h2:    set pins, 3
               jmp start
        h3:    set pins, 4
               jmp start
    """, shiftctrl=regs.AUTOPULL | regs.OUT_RIGHT, set_count=3, execcfg=1)
    await chip.ereg(0, regs.SETPIN, 0 | (3 << 4))
    await chip.force_asm(0, "set pindirs, 7")
    for p in range(3):
        await chip.pincfg(p, regs.DRIVE_PUSH_PULL)
    codes = [0, 1, 2, 3, 2, 1, 0, 3]
    word = sum(c << (2 * i) for i, c in enumerate(codes))
    await chip.push(0, [word])
    s = await enable_and_sample(chip, 1, 80, lambda: int(chip.dut.uio_out.value) & 7)
    r = runs(s)
    seq = [v for v, _ in r if v]
    assert seq == [c + 1 for c in codes], r
    assert all(length == 4 for v, length in r[1:-1]), r


@cocotb.test()
async def test_pattern_wait(dut):
    """G4: wait until (pins & Y) == X."""
    chip = await new_chip(dut)
    await run_program(chip, """
            wait 1 pattern
            set pins, 1
        halt: jmp halt
    """, set_base=3)
    await chip.force_asm(0, "set x, 5\nset y, 7\nset pindirs, 1")
    await chip.pincfg(3, regs.DRIVE_PUSH_PULL)
    for b, v in enumerate([0, 0, 1]):
        chip.drive_uio(b, v)
    await chip.enable(1)
    await chip.cycles(20)
    assert uio_bit(chip, 3)() == 0
    chip.drive_uio(0, 1)
    await chip.cycles(5)
    assert uio_bit(chip, 3)() == 1


@cocotb.test()
async def test_ram_mode(dut):
    """G5: transmit queue plus RAM in entries 4-7."""
    chip = await new_chip(dut)
    await chip.ereg(0, regs.SHIFTCTRL, regs.fifo_mode(regs.FIFO_TX_RAM))
    await chip.ereg(0, regs.RAM_ADDR, 4)
    await chip.write16(regs.engine_reg(0, regs.RAM_DATA), [0x1111, 0x2222, 0x3333, 0x4444])
    await chip.ereg(0, regs.RAM_ADDR, 4)
    assert await chip.read16(regs.engine_reg(0, regs.RAM_DATA), 4) == [0x1111, 0x2222, 0x3333, 0x4444]
    await chip.force_asm(0, "set y, 5\nmov x, ram")
    assert await chip.dbg(0, 1) == 0x2222
    await chip.force_asm(0, "set y, 6\nmov osr, ~null\nout ram, 16")
    await chip.force_asm(0, "set y, 7\nin ram, 16\nmov x, isr")
    assert await chip.dbg(0, 1) == 0x4444
    # Writes to entries owned by the transmit queue are ignored.
    await chip.push(0, [0xAAAA, 0xBBBB])
    await chip.force_asm(0, "set y, 1\nmov osr, ~null\nout ram, 16")
    await chip.ereg(0, regs.RAM_ADDR, 0)
    await chip.write16(regs.engine_reg(0, regs.RAM_DATA), [0xBEEF])
    await chip.ereg(0, regs.RAM_ADDR, 0)
    assert await chip.read16(regs.engine_reg(0, regs.RAM_DATA), 2) == [0xAAAA, 0xBBBB]
    await chip.ereg(0, regs.RAM_ADDR, 4)
    got = await chip.read16(regs.engine_reg(0, regs.RAM_DATA), 4)
    assert got == [0x1111, 0x2222, 0xFFFF, 0x4444], [hex(g) for g in got]
    # The transmit queue still streams.
    await chip.force_asm(0, "pull\nmov x, osr")
    assert await chip.dbg(0, 1) == 0xAAAA


@cocotb.test()
async def test_buffer_join_and_noblock(dut):
    chip = await new_chip(dut)
    await chip.ereg(0, regs.SHIFTCTRL, regs.fifo_mode(regs.FIFO_JOIN_TX))
    await chip.push(0, list(range(9)))
    assert await chip.levels(0) == (8, 0)
    assert (await chip.read8(regs.STICKY)) & regs.STK_TX_OVF0
    # The disabled receive queue counts as full: a non-blocking push drops.
    await chip.force_asm(0, "push noblock")
    assert (await chip.read8(regs.STICKY)) & regs.STK_RX_OVF0
    # Receive join: 8 entries.
    await chip.ereg(1, regs.SHIFTCTRL, regs.fifo_mode(regs.FIFO_JOIN_RX) | regs.AUTOPUSH)
    await chip.load("in count, 16", 20)
    await chip.setup_engine(1, assemble("in count, 16"), 20,
                            shiftctrl=regs.fifo_mode(regs.FIFO_JOIN_RX) | regs.AUTOPUSH)
    await chip.enable(2)
    await chip.cycles(30)
    assert await chip.levels(1) == (0, 8)
    # pull noblock on an empty queue copies X.
    await chip.force_asm(1, "set x, 13\npull noblock\nmov y, osr")
    assert await chip.dbg(1, 2) == 13


@cocotb.test()
async def test_status_source(dut):
    chip = await new_chip(dut)
    await chip.ereg(0, regs.STATUSCFG, 2)  # transmit level < 2
    await chip.force_asm(0, "mov x, status")
    assert await chip.dbg(0, 1) == 0xFFFF
    await chip.push(0, [1, 2, 3])
    await chip.force_asm(0, "mov x, status")
    assert await chip.dbg(0, 1) == 0


async def pulse_after_wait(chip, wait_line, x, delay_cycles=0, during=None, clkdiv=0):
    """Run `set pins, 1`, the wait, `set pins, 0` with X preloaded. Returns
    (high cycles on pin 0, X afterwards). `during(chip)` runs while it waits."""
    await chip.enable(0)
    await run_program(chip, f"""
            set pins, 1
            {wait_line}
            set pins, 0
        halt: jmp halt
    """, clkdiv=clkdiv)
    await chip.force_asm(0, f"set pindirs, 1\nset pins, 0\nset x, {x}")
    await chip.pincfg(0, regs.DRIVE_PUSH_PULL)
    task = cocotb.start_soon(sample(chip, 200 * (clkdiv + 1), uio_bit(chip, 0)))
    side = cocotb.start_soon(during(chip)) if during else None
    await chip.enable(1)
    if side:
        await side
    s = await task
    high = [length for v, length in runs(s) if v]
    assert len(high) == 1, runs(s)
    return high[0], await chip.dbg(0, 1)


@cocotb.test()
async def test_timed_wait(dut):
    """A timed wait checks once per tick; X counts the checks left and is 0
    afterwards only if the wait timed out."""
    chip = await new_chip(dut)
    chip.set_pin8(0)
    # Never satisfied: completes on the 20th check, so set + 20 + 1 cycles.
    assert await pulse_after_wait(chip, "wait 1 gpio 8 timeout", 20) == (21, 0)
    # Already satisfied: completes on the first check and keeps X.
    chip.set_pin8(1)
    assert await pulse_after_wait(chip, "wait 1 gpio 8 timeout", 20) == (2, 20)
    # X = 0 before the wait: it completes at once, as timed out, even though
    # the condition holds.
    assert await pulse_after_wait(chip, "wait 1 gpio 8 timeout", 0) == (2, 0)
    # The delay applies once, after completion.
    chip.set_pin8(0)
    assert await pulse_after_wait(chip, "wait 1 gpio 8 timeout [3]", 5) == (9, 0)
    # The clock divider scales every check.
    assert await pulse_after_wait(chip, "wait 1 gpio 8 timeout", 5, clkdiv=2) == (18, 0)

    # Satisfied on check k leaves X = N - k + 1 >= 1, and the wait lasted k
    # cycles. Pin 8 rises `start` cycles after the peer sees pin 0 rise, so
    # the first check that sees it is check start + a fixed latency (peer
    # observation and the 2-cycle input path): every cycle counts once.
    def rise_after(start):
        async def rise(chip):
            while not uio_bit(chip, 0)():
                await RisingEdge(chip.dut.clk)
            await chip.cycles(start)
            chip.set_pin8(1)
        return rise

    latency = None
    for start in range(0, 32):
        chip.set_pin8(0)
        high, x = await pulse_after_wait(chip, "wait 1 gpio 8 timeout", 30, during=rise_after(start))
        if latency is None:
            latency = (30 - x + 1) - start
            assert 1 <= latency <= 4, latency
        k = start + latency
        if k <= 30:
            assert (high, x) == (k + 1, 30 - k + 1), (start, high, x)
        else:
            assert (high, x) == (31, 0), (start, high, x)


@cocotb.test()
async def test_timed_wait_flag_and_reserved(dut):
    chip = await new_chip(dut)
    # A satisfied flag wait clears the flag, as without a timeout.
    await chip.write(regs.FLAG_SET, 0x04)
    assert await pulse_after_wait(chip, "wait 1 flag 2 timeout", 7) == (2, 7)
    assert await chip.read8(regs.FLAGS) == 0
    # A timed-out flag wait leaves the flags alone.
    assert await pulse_after_wait(chip, "wait 1 flag 2 timeout", 7) == (8, 0)
    assert await chip.read8(regs.FLAGS) == 0
    # A timed wait on a disabled engine retries every cycle, as forced.
    await chip.enable(0)
    await chip.force_asm(0, "set x, 12")
    await chip.force_asm(0, "wait 1 flag 5 timeout")
    await chip.cycles(20)
    assert await chip.dbg(0, 1) == 0
    assert not (await chip.dbg(0, 0)) & 0x80, "exec slot empty after the timeout"
    # A timed pattern wait is reserved and runs as a nop, leaving X alone.
    await chip.force_asm(0, "jmp 9\nset x, 3\nset y, 1")
    chip.drive_uio(0, 0)
    await chip.force(0, 0x40F0)  # wait 1 pattern, timed
    assert (await chip.dbg(0, 0)) & 0x3F == 9
    assert await chip.dbg(0, 1) == 3


@cocotb.test()
async def test_count_sources(dut):
    """mov reads the ISR and OSR counts (operation 3)."""
    chip = await new_chip(dut)
    await chip.write(regs.CMD, regs.CMD_RESTART0)

    async def counts():
        await chip.force_asm(0, "mov x, isrcnt\nmov y, osrcnt")
        return await chip.dbg(0, 1), await chip.dbg(0, 2)

    assert await counts() == (0, 16), "after restart the OSR is empty"
    await chip.force_asm(0, "in null, 3\npull noblock\nout null, 5")
    assert await counts() == (3, 5)
    await chip.force_asm(0, "in null, 16\nout null, 16")
    assert await counts() == (16, 16), "counts saturate at 16"
    await chip.force_asm(0, "push noblock")
    assert await counts() == (0, 16)
    # The count lands in any destination, for example as a jump target.
    await chip.force_asm(0, "in null, 9\nmov pc, isrcnt")
    assert (await chip.dbg(0, 0)) & 0x3F == 9
    # Other count sources read 0.
    await chip.force_asm(0, "set x, 7")
    await chip.force(0, 0xC000 | (1 << 5) | (3 << 3) | 5)  # mov x, count source 5
    assert await chip.dbg(0, 1) == 0


@cocotb.test()
async def test_instruction_pages(dut):
    """Each engine fetches from the page in EXECCFG bit 7: the same page
    addresses run different programs, jumps and wrap stay in the page, and a
    Host write while running reaches the page it addresses."""
    chip = await new_chip(dut)
    src = """
        set x, {v}
    loop:
        in x, 16
        push
        jmp loop
    """
    for e, (offset, v) in enumerate(((3, 5), (64 + 3, 9))):
        prog = await chip.load(src.format(v=v), offset)
        await chip.setup_engine(e, prog, offset, shiftctrl=regs.fifo_mode(regs.FIFO_JOIN_RX))
    assert (await chip.eread(1, regs.EXECCFG))[0] == regs.EXECCFG_PAGE1
    await chip.enable(3)
    await chip.cycles(40)
    await chip.enable(0)
    assert set(await chip.pop(0, 4)) == {5}
    assert set(await chip.pop(1, 4)) == {9}
    # Replace `set x` in page 1 only, then rerun both from their starts.
    await chip.write(regs.IMEM_ADDR, 64 + 3)
    await chip.write16(regs.IMEM_DATA, [assemble("set x, 17").code[0]])
    for e in range(2):
        await chip.write(regs.CMD, regs.CMD_CLEAR0 << e)
        await chip.force(e, 3)  # jmp 3, in the engine's own page
    await chip.enable(3)
    await chip.cycles(40)
    await chip.enable(0)
    assert set(await chip.pop(0, 4)) == {5}
    assert set(await chip.pop(1, 4)) == {17}


@cocotb.test()
async def test_page_jumps(dut):
    """`set page` selects the page of the next jump taken: the instructions
    after it and a jump not taken stay in the current page, the next taken
    jump changes page, and a program spans both pages. A Host write to
    EXECCFG sets the jump page too, and a restart keeps it."""
    chip = await new_chip(dut)
    home = await chip.load("""
        set y, 3
        in y, 16
        push
    done:
        jmp done
    """, 0)
    span = await chip.load("""
        set y, 1
        set page, far
        in y, 16           ; still page 0
        push
        jmp !y far         ; not taken: the page stays
        jmp far            ; address 63, the last of page 0
    far:
        set y, 2
        in y, 16
        push
        set page, 0
        jmp 0
    """, 58)
    assert span.address("far", 58) == 64
    rx = regs.fifo_mode(regs.FIFO_JOIN_RX)
    await chip.setup_engine(0, span, 58, shiftctrl=rx)
    await chip.enable(1)
    await chip.cycles(40)
    await chip.enable(0)
    assert await chip.pop(0, 3) == [1, 2, 3]
    assert (await chip.eread(0, regs.EXECCFG))[0] == 0
    # A Host page write moves the forced jump to page 1 as well.
    await chip.ereg(0, regs.EXECCFG, regs.EXECCFG_PAGE1)
    await chip.force(0, 0)  # jmp 0: address 64
    assert (await chip.eread(0, regs.EXECCFG))[0] == regs.EXECCFG_PAGE1
    await chip.enable(1)
    await chip.cycles(40)
    await chip.enable(0)
    assert await chip.pop(0, 2) == [2, 3]
    # A forced `set page` holds across a restart until the next jump.
    await chip.force_asm(0, "set page, 1")
    await chip.write(regs.CMD, regs.CMD_RESTART0)
    assert (await chip.eread(0, regs.EXECCFG))[0] == 0
    await chip.force(0, 0)
    await chip.enable(1)
    await chip.cycles(40)
    await chip.enable(0)
    assert await chip.pop(0, 2) == [2, 3]
    assert home.label("done") == 3
