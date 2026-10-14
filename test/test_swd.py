# SPDX-License-Identifier: Apache-2.0
"""SWD host program against an independent ADIv5 debug-port model, in
absolute time at the 50 MHz clock profile."""

import cocotb
from cocotb.triggers import RisingEdge

from chip import MHZ_50, Chip, fails_at_wrong_clock, regs
from pe.proto import swd
from peers import Wave, clock_violations, now_ns, setup_hold_violations, sleep_ns

PROFILE = MHZ_50
SWCLK_HZ = 6_250_000  # 8 cycles per SWCLK at CLKDIV 0

SWDIO, SWCLK = 0, 2
DP_IDCODE = 0x2BA01477


class DebugPortModel:
    """SW-DP: samples SWDIO on SWCLK rising edges and changes its own bits
    `tco_ns` after a rising edge. Edge numbers count from the start bit (1).
    `check()` reports SWCLK periods off the nominal rate and host data
    setup or hold times below 10 ns at the edges where the host drives."""

    def __init__(self, chip, tco_ns=10.0):
        self.chip = chip
        self.tco_ns = tco_ns
        self.swclk = Wave(chip.dut.uio_in, SWCLK)
        self.host_data = Wave(chip.dut.uio_out, SWDIO)
        self.host_samples = []
        self.acks = []  # ACKs to give the next valid requests instead of OK
        self.state = "reset_wait"
        self.ones = 0
        self.ctrl_stat = 0
        self.select = 0
        self.log = []

    async def _later(self, bit):
        await sleep_ns(self.tco_ns)
        self.chip.drive_uio(SWDIO, bit)

    def _drive(self, bit):
        cocotb.start_soon(self._later(bit))

    def _release(self):
        cocotb.start_soon(self._later(None))

    def _start(self, req):
        start, apndp, rnw = req & 1, (req >> 1) & 1, (req >> 2) & 1
        addr = ((req >> 3) & 3) << 2
        par, stop, park = (req >> 5) & 1, (req >> 6) & 1, (req >> 7) & 1
        if not (start == 1 and stop == 0 and park == 1 and par == (apndp ^ rnw ^ (addr >> 2 & 1) ^ (addr >> 3 & 1))):
            self.log.append(("bad_request", req))
            self.state = "lockout"
            return
        self.apndp, self.rnw, self.addr = apndp, rnw, addr
        self.ack = self.acks.pop(0) if self.acks else swd.ACK_OK
        self.log.append(("request", req, self.ack))
        self.edge = 8
        self.state = "transfer"
        if rnw:
            self.rdata = {0x0: DP_IDCODE, 0x4: self.ctrl_stat, 0xC: 0}.get(addr, 0)

    def _transfer_edge(self, bit):
        self.edge += 1
        e = self.edge
        if 9 <= e <= 11:
            self._drive((self.ack >> (e - 9)) & 1)
        elif self.ack != swd.ACK_OK:
            # WAIT or FAULT (ORUNDETECT = 0): no data phase; after a
            # turnaround the next request may start.
            self._release()
            self.state = "turnaround"
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

    async def run(self):
        dut = self.chip.dut
        while True:
            if await self.swclk.change():
                if (int(dut.uio_oe.value) >> SWDIO) & 1:
                    self.host_samples.append(now_ns())
                self._edge((self.chip.uio() >> SWDIO) & 1)

    def check(self):
        out, checked = clock_violations(self.swclk, True, SWCLK_HZ)
        assert checked, "no SWCLK period to check"
        return out + setup_hold_violations(self.host_data, self.host_samples, 10, 10, "SWDIO")


async def setup(dut, hz=PROFILE):
    chip = Chip(dut, hz)
    await chip.start()
    await chip.set_quad()
    chip.pull_up(1 << SWDIO)
    prog = await chip.load_program("swd")
    await chip.setup_engine(0, prog, shiftctrl=regs.OUT_RIGHT | regs.IN_RIGHT | regs.AUTOPULL
                            | regs.AUTOPUSH, out_base=SWDIO, in_base=SWDIO, side_base=SWCLK)
    await chip.ereg(0, regs.SETPIN, SWCLK | (1 << 4))
    await chip.force_asm(0, "set pindirs, 1 side 0", side_count=1, side_opt=True)  # SWCLK out
    await chip.ereg(0, regs.SETPIN, SWDIO | (1 << 4))  # the program turns SWDIO around
    await chip.pincfg(SWDIO, regs.DRIVE_PUSH_PULL)
    await chip.pincfg(SWCLK, regs.DRIVE_PUSH_PULL)
    dp = DebugPortModel(chip)
    cocotb.start_soon(dp.run())
    contention = []

    async def watch():
        while True:
            await RisingEdge(dut.clk)
            if int(dut.contention.value):
                contention.append(1)
    cocotb.start_soon(watch())
    await chip.enable(1)
    return chip, dp, contention


async def run(chip, b, n_rx):
    rx = await chip.stream(0, b.tx, n_rx)
    res, used = b.decode(rx)
    assert used == len(rx), (res, rx)
    return res


async def read_write_session(dut, hz=PROFILE):
    chip, dp, contention = await setup(dut, hz)
    b = swd.Batch()
    b.line_reset()
    b.read(0, 0x0)                  # DP IDCODE
    b.write(0, 0x4, 0x50000000)     # CTRL/STAT: power-up requests
    b.read(0, 0x4)
    b.write(0, 0x8, 0x000000F0)     # SELECT
    res = await run(chip, b, 10)
    assert res[0] == (swd.ACK_OK, DP_IDCODE, True), res[0]
    assert res[1] == (swd.ACK_OK,)
    assert res[2] == (swd.ACK_OK, 0x50000000, True), res[2]
    assert res[3] == (swd.ACK_OK,)
    assert dp.select == 0xF0
    assert ("write", 0x4, 0x50000000, True) in dp.log
    assert not contention, "host and target drove SWDIO at the same time"
    violations = dp.check()
    assert not violations, violations[:5]


@cocotb.test()
async def test_swd_read_write(dut):
    await read_write_session(dut)


@cocotb.test()
async def test_swd_wrong_clock(dut):
    msg = await fails_at_wrong_clock(lambda hz: read_write_session(dut, hz), PROFILE)
    assert "timing: clock period" in msg, msg


@cocotb.test()
async def test_swd_bad_request_and_recovery(dut):
    """A request with bad parity gets no ACK (the pull-up reads 0b111); the
    program skips the data phase, and a line reset recovers the target."""
    chip, dp, contention = await setup(dut)
    b = swd.Batch()
    b.line_reset()
    b.transaction(swd.request(0, 1, 0) ^ (1 << 5), read=True)  # flip the parity bit
    b.idle()
    b.line_reset()
    b.read(0, 0x0)
    res = await run(chip, b, 5)
    assert res == [(swd.ACK_NONE,), (swd.ACK_OK, DP_IDCODE, True)], res
    assert dp.log[0][0] == "bad_request"
    assert not contention
    assert not dp.check()


@cocotb.test()
async def test_swd_wait_and_fault(dut):
    """WAIT and FAULT have no data phase (ADIv5 with ORUNDETECT = 0). The
    write's data would read as a valid request (0xA5, a DP IDCODE read) if
    it were sent anyway, and the target would then drive SWDIO against the
    host. The program drops the data, reports the ACK, and goes on."""
    chip, dp, contention = await setup(dut)
    dp.acks = [swd.ACK_WAIT, swd.ACK_FAULT]
    b = swd.Batch()
    b.line_reset()
    b.write(0, 0x8, 0x000000A5)     # WAIT
    b.read(0, 0x4)                  # FAULT
    b.write(0, 0x8, 0x000000A5)     # retried: OK
    b.read(0, 0x0)
    res = await run(chip, b, 7)
    assert res == [(swd.ACK_WAIT,), (swd.ACK_FAULT,), (swd.ACK_OK,), (swd.ACK_OK, DP_IDCODE, True)], res
    requests = [e[1:] for e in dp.log if e[0] == "request"]
    assert requests == [(swd.request(0, 0, 0x8), swd.ACK_WAIT), (swd.request(0, 1, 0x4), swd.ACK_FAULT),
                        (swd.request(0, 0, 0x8), swd.ACK_OK), (swd.request(0, 1, 0x0), swd.ACK_OK)], dp.log
    assert dp.select == 0xA5
    assert not contention, "host and target drove SWDIO at the same time"
    assert not dp.check()
