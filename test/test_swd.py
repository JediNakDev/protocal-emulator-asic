# SPDX-License-Identifier: Apache-2.0
"""SWD host program against an independent ADIv5 debug-port model."""

import cocotb
from cocotb.triggers import RisingEdge

from chip import Chip, regs
from pe.proto import swd

SWDIO, SWCLK = 0, 2
DP_IDCODE = 0x2BA01477


class DebugPortModel:
    """SW-DP: samples SWDIO on SWCLK rising edges and changes its own bits
    right after a rising edge. Edge numbers count from the start bit (1)."""

    def __init__(self, chip):
        self.chip = chip
        self.state = "reset_wait"
        self.ones = 0
        self.ctrl_stat = 0
        self.select = 0
        self.log = []

    def _drive(self, bit):
        self.chip.drive_uio(SWDIO, bit)

    def _release(self):
        self.chip.drive_uio(SWDIO, None)

    def _start(self, req):
        start, apndp, rnw = req & 1, (req >> 1) & 1, (req >> 2) & 1
        addr = ((req >> 3) & 3) << 2
        par, stop, park = (req >> 5) & 1, (req >> 6) & 1, (req >> 7) & 1
        if not (start == 1 and stop == 0 and park == 1 and par == (apndp ^ rnw ^ (addr >> 2 & 1) ^ (addr >> 3 & 1))):
            self.log.append(("bad_request", req))
            self.state = "lockout"
            return
        self.apndp, self.rnw, self.addr = apndp, rnw, addr
        self.ack = 0b001
        self.edge = 8
        self.state = "transfer"
        if rnw:
            self.rdata = {0x0: DP_IDCODE, 0x4: self.ctrl_stat, 0xC: 0}.get(addr, 0)

    def _transfer_edge(self, bit):
        self.edge += 1
        e = self.edge
        if 9 <= e <= 11:
            self._drive((self.ack >> (e - 9)) & 1)
        elif self.rnw:
            if 12 <= e <= 43:
                self._drive((self.rdata >> (e - 12)) & 1)
            elif e == 44:
                self._drive(bin(self.rdata).count("1") & 1)
            elif e == 45:
                self._release()
                self.log.append(("read", self.addr, self.rdata))
                self.state = "turnaround"  # the next rising edge is the turnaround
        else:
            if e == 12:
                self._release()
                self.wdata = 0
            elif 14 <= e <= 45:
                self.wdata |= bit << (e - 14)
            elif e == 46:
                ok = bit == (bin(self.wdata).count("1") & 1)
                self.log.append(("write", self.addr, self.wdata, ok))
                if ok and self.addr == 0x4:
                    self.ctrl_stat = self.wdata
                elif ok and self.addr == 0x8:
                    self.select = self.wdata
                self.state = "idle"

    def _edge(self, bit):
        if self.state == "transfer":
            self._transfer_edge(bit)
            return
        if self.state == "turnaround":
            self.state = "idle"
            return
        # Line reset: 50 or more ones while the host drives.
        self.ones = self.ones + 1 if bit else 0
        if self.ones >= 50:
            self.state = "reset_seen"
            return
        if self.state == "reset_seen":
            if not bit:
                self.state = "idle"
        elif self.state == "idle":
            if bit:
                self.state = "request"
                self.req_bits = [1]
        elif self.state == "request":
            self.req_bits.append(bit)
            if len(self.req_bits) == 8:
                self._start(sum(b << i for i, b in enumerate(self.req_bits)))

    async def run(self, clk):
        prev = 0
        while True:
            await RisingEdge(clk)
            v = self.chip.uio()
            clk_level = (v >> SWCLK) & 1
            if clk_level and not prev:
                self._edge((v >> SWDIO) & 1)
            prev = clk_level


async def setup(dut):
    chip = Chip(dut)
    await chip.start()
    await chip.set_quad()
    chip.pull_up(1 << SWDIO)
    prog = await chip.load_program("swd")
    await chip.setup_engine(0, prog, shiftctrl=regs.OUT_RIGHT | regs.IN_RIGHT,
                            out_base=SWDIO, in_base=SWDIO, side_base=SWCLK)
    await chip.ereg(0, regs.SETPIN, SWCLK | (1 << 4))
    await chip.force_asm(0, "set pindirs, 1 side 0", side_count=1, side_opt=True)
    await chip.pincfg(SWDIO, regs.DRIVE_PUSH_PULL)
    await chip.pincfg(SWCLK, regs.DRIVE_PUSH_PULL)
    dp = DebugPortModel(chip)
    cocotb.start_soon(dp.run(dut.clk))
    contention = []

    async def watch():
        while True:
            await RisingEdge(dut.clk)
            if int(dut.contention.value):
                contention.append(1)
    cocotb.start_soon(watch())
    await chip.enable(1)
    return chip, dp, contention


@cocotb.test()
async def test_swd_read_write(dut):
    chip, dp, contention = await setup(dut)
    b = swd.Batch()
    b.line_reset()
    b.read(0, 0x0)                  # DP IDCODE
    b.write(0, 0x4, 0x50000000)     # CTRL/STAT: power-up requests
    b.read(0, 0x4)
    b.write(0, 0x8, 0x000000F0)     # SELECT
    rx = await chip.stream(0, b.tx, b.n_rx_words())
    res = b.decode(rx)
    assert res[0] == (swd.ACK_OK, DP_IDCODE, True), res[0]
    assert res[1] == (swd.ACK_OK,)
    assert res[2] == (swd.ACK_OK, 0x50000000, True), res[2]
    assert res[3] == (swd.ACK_OK,)
    assert dp.select == 0xF0
    assert ("write", 0x4, 0x50000000, True) in dp.log
    assert not contention, "host and target drove SWDIO at the same time"


@cocotb.test()
async def test_swd_bad_request_and_recovery(dut):
    """A request with bad parity gets no ACK (the pull-up reads 0b111); a
    line reset recovers the target."""
    chip, dp, contention = await setup(dut)
    b = swd.Batch()
    b.line_reset()
    good = swd.request(0, 1, 0)
    b.tx += [7 | swd.DRIVE, good ^ (1 << 5)]  # flip the parity bit
    b.reads += [1, 3]
    b.tx += [0 | swd.READ, 2 | swd.READ]
    b.idle()
    b.line_reset()
    b.read(0, 0x0)
    rx = await chip.stream(0, b.tx, b.n_rx_words())
    assert rx[1] >> 13 == 0b111, hex(rx[1])
    assert b.decode(rx) == [(swd.ACK_OK, DP_IDCODE, True)]
    assert dp.log[0][0] == "bad_request"
    assert not contention
