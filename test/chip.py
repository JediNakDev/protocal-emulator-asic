# SPDX-License-Identifier: Apache-2.0
"""Cocotb Host model: drives the Host port through the top-level pins only."""

import os
import sys

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from pe import regs  # noqa: E402
from pe.asm import assemble  # noqa: E402

CLK_NS = 20  # 50 MHz
HALF = 4     # HSCK half period in clk cycles, the specified minimum

HCS_N, HSCK = 0, 1


PROGRAMS = os.path.join(os.path.dirname(__file__), "..", "programs")


def program_source(name):
    with open(os.path.join(PROGRAMS, name + ".pasm")) as f:
        return f.read()


class Chip:
    def __init__(self, dut, clk_ps=CLK_NS * 1000):
        self.dut = dut
        self.clk_ps = clk_ps
        self.quad = False
        self.ui = 0x01  # HCS_N high
        # Shadows: cocotb applies writes later, so reading back a signal
        # written in the same time step returns the old value.
        self.ext_oe = 0
        self.ext_out = 0

    # ------------------------------------------------------------- basics
    async def start(self):
        cocotb.start_soon(Clock(self.dut.clk, self.clk_ps, unit="ps").start())
        self.dut.ena.value = 1
        self.dut.ext_oe.value = 0
        self.dut.ext_out.value = 0
        self.dut.pull_up.value = 0
        self._ui_apply()
        self.dut.rst_n.value = 0
        await ClockCycles(self.dut.clk, 5)
        self.dut.rst_n.value = 1
        await ClockCycles(self.dut.clk, 5)
        self.quad = False

    async def cycles(self, n):
        await ClockCycles(self.dut.clk, n)

    def _ui_apply(self):
        self.dut.ui_in.value = self.ui

    def set_ui_pin(self, bit, value):
        """Drive ui[bit]; protocol pins 8 and 9 are ui[6] and ui[7]."""
        if value:
            self.ui |= 1 << bit
        else:
            self.ui &= ~(1 << bit)
        self._ui_apply()

    def set_pin8(self, value):
        self.set_ui_pin(6, value)

    def set_pin9(self, value):
        self.set_ui_pin(7, value)

    def uo(self):
        return int(self.dut.uo_out.value)

    def uio(self):
        return int(self.dut.uio_in.value)

    def drive_uio(self, bit, value):
        """Board peer drives uio[bit] (push-pull). value None releases it."""
        oe, out = self.ext_oe, self.ext_out
        if value is None:
            oe &= ~(1 << bit)
        else:
            oe |= 1 << bit
            out = (out | (1 << bit)) if value else (out & ~(1 << bit))
        self.ext_oe, self.ext_out = oe, out
        self.dut.ext_oe.value = oe
        self.dut.ext_out.value = out

    def pull_up(self, mask):
        self.dut.pull_up.value = mask

    # ---------------------------------------------------------- host port
    def _set_host(self, cs_n, sck, data):
        self.ui = (self.ui & 0xC0) | (cs_n << HCS_N) | (sck << HSCK) | ((data & 0xF) << 2)
        self._ui_apply()

    def _hdo(self):
        return self.uo() & 0xF

    async def xfer(self, out_bytes, half=HALF):
        """One transaction. Returns the bytes the chip shifted out."""
        clk = self.dut.clk
        self._set_host(0, 0, 0)
        await ClockCycles(clk, half)
        got = []
        for b in out_bytes:
            r = 0
            groups = [(b >> 4) & 0xF, b & 0xF] if self.quad else [(b >> (7 - i)) & 1 for i in range(8)]
            for g in groups:
                self._set_host(0, 0, g)
                await ClockCycles(clk, half)
                hdo = self._hdo()
                r = (r << 4) | hdo if self.quad else (r << 1) | (hdo & 1)
                self._set_host(0, 1, g)
                await ClockCycles(clk, half)
            got.append(r)
        self._set_host(0, 0, 0)
        await ClockCycles(clk, half)
        self._set_host(1, 0, 0)
        await ClockCycles(clk, half)
        return got

    async def write(self, addr, data):
        if isinstance(data, int):
            data = [data]
        r = await self.xfer([addr & 0x7F] + list(data))
        return r

    async def read(self, addr, n=1):
        r = await self.xfer([0x80 | (addr & 0x7F)] + [0] * n)
        return r[1:]

    async def read8(self, addr):
        return (await self.read(addr, 1))[0]

    async def status(self):
        return (await self.xfer([0x80]))[0]

    async def write16(self, addr, words):
        data = []
        for w in words:
            data += [w & 0xFF, (w >> 8) & 0xFF]
        await self.write(addr, data)

    async def read16(self, addr, n=1):
        b = await self.read(addr, 2 * n)
        return [b[2 * i] | (b[2 * i + 1] << 8) for i in range(n)]

    async def read32(self, addr):
        b = await self.read(addr, 4)
        return b[0] | b[1] << 8 | b[2] << 16 | b[3] << 24

    async def write32(self, addr, v):
        await self.write(addr, [(v >> (8 * i)) & 0xFF for i in range(4)])

    async def set_quad(self):
        await self.write(regs.HOSTCFG, 1)
        self.quad = True

    # ----------------------------------------------------------- engines
    async def ereg(self, e, off, data):
        await self.write(regs.engine_reg(e, off), data)

    async def eread(self, e, off, n=1):
        return await self.read(regs.engine_reg(e, off), n)

    async def load_program(self, name, offset=0):
        """Load programs/<name>.pasm."""
        return await self.load(program_source(name), offset)

    async def load(self, prog, offset=0):
        """Write a Program (or source text) to instruction memory."""
        if isinstance(prog, str):
            prog = assemble(prog)
        await self.write(regs.IMEM_ADDR, offset)
        await self.write16(regs.IMEM_DATA, prog.words(offset))
        return prog

    async def setup_engine(self, e, prog, offset=0, clkdiv=0, shiftctrl=0, thresh=0,
                           out_base=0, out_count=1, set_base=0, set_count=1, in_base=0,
                           jmp_pin=0, side_base=0, execcfg=0, statuscfg=0):
        """Configure engine `e` for a loaded Program and jump to its start."""
        await self.ereg(e, regs.CLKDIV, [clkdiv & 0xFF, clkdiv >> 8,
                                         prog.wrap_target + offset, prog.wrap + offset])
        await self.ereg(e, regs.SHIFTCTRL, [shiftctrl, thresh,
                                            (out_base & 15) | ((out_count & 15) << 4),
                                            (set_base & 15) | ((set_count & 7) << 4),
                                            (in_base & 15) | ((jmp_pin & 15) << 4),
                                            prog.sidepin(side_base), execcfg, statuscfg])
        await self.write(regs.CMD, regs.CMD_RESTART0 << e)
        await self.force(e, offset & 0x3F)  # jmp to the program start

    async def force(self, e, word):
        await self.write16(regs.engine_reg(e, regs.INSTR), [word])

    async def force_asm(self, e, text, side_count=0, side_opt=False):
        hdr = f".side_set {side_count}{' opt' if side_opt else ''}\n" if side_count else ""
        p = assemble(hdr + text)
        for w in p.code:
            await self.force(e, w)

    async def enable(self, mask):
        await self.write(regs.CTRL, mask)

    async def push(self, e, words):
        await self.write16(regs.engine_reg(e, regs.FIFO), words)

    async def levels(self, e):
        v = (await self.eread(e, regs.LEVELS))[0]
        return v & 15, v >> 4

    async def pop(self, e, n=1):
        return await self.read16(regs.engine_reg(e, regs.FIFO), n)

    async def pop_wait(self, e, n, timeout=200000):
        """Read n receive words, waiting for them to arrive."""
        out = []
        waited = 0
        while len(out) < n:
            _, rx = await self.levels(e)
            if rx:
                out += await self.pop(e, min(rx, n - len(out)))
            else:
                await self.cycles(50)
                waited += 50
                if waited > timeout:
                    raise TimeoutError(f"engine {e}: got {len(out)} of {n} words")
        return out

    async def stream(self, e, tx, n_rx, depth=4, timeout=400000):
        """Feed `tx` words into engine e's transmit queue while collecting n_rx
        receive words, as a Host with flow control would."""
        rx, i, idle = [], 0, 0
        while i < len(tx) or len(rx) < n_rx:
            txl, rxl = await self.levels(e)
            progress = False
            if rxl and len(rx) < n_rx:
                rx += await self.pop(e, min(rxl, n_rx - len(rx)))
                progress = True
            free = depth - txl
            if free > 0 and i < len(tx):
                await self.push(e, tx[i:i + free])
                i += free
                progress = True
            if not progress:
                await self.cycles(20)
                idle += 20
                if idle > timeout:
                    raise TimeoutError(f"engine {e}: sent {i}/{len(tx)}, got {len(rx)}/{n_rx}")
        return rx

    async def dbg(self, e, sel):
        await self.write(regs.DBG_SEL, sel)
        return (await self.read16(regs.engine_reg(e, regs.DBG)))[0]

    async def pincfg(self, pin, value):
        await self.write(regs.PINCFG + pin, value)


async def edges(dut_signal, clk, n_cycles):
    """Sample a signal every cycle for n cycles. Returns the list of values."""
    out = []
    for _ in range(n_cycles):
        await RisingEdge(clk)
        out.append(int(dut_signal.value))
    return out
