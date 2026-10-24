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
DP_ABORT, DP_CTRL_STAT, DP_SELECT = 0x0, 0x4, 0x8   # DP write addresses
STICKYERR = 1 << 5                                  # CTRL/STAT
STKERRCLR = 1 << 2                                  # ABORT


class DebugPortModel:
    """SW-DP (ADIv5, ORUNDETECT = 0): samples SWDIO on SWCLK rising edges
    and changes its own bits `tco_ns` after a rising edge. Edge numbers
    count from the start bit (1). An access port transaction can be made
    to fail, which sets STICKYERR, or to stay busy for a number of later
    requests. While STICKYERR is set the DP answers FAULT, and while the
    AP is busy WAIT, to every request except a DPIDR or CTRL/STAT read and
    an ABORT write; ABORT with STKERRCLR clears STICKYERR. `check()`
    reports SWCLK periods off the nominal rate and host data setup or hold
    times below 10 ns at the edges where the host drives."""

    def __init__(self, chip, tco_ns=10.0):
        self.chip = chip
        self.tco_ns = tco_ns
        self.swclk = Wave(chip.dut.uio_in, SWCLK)
        self.host_data = Wave(chip.dut.uio_out, SWDIO)
        self.host_samples = []
        self.ap_fails = False  # the next AP transaction ends in an error
        self.ap_busy_for = 0   # the next AP transaction stays busy this many requests
        self.ap_busy = 0
        self.sticky = 0
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
        exempt = not apndp and (addr in (0x0, 0x4) if rnw else addr == DP_ABORT)
        if self.sticky & STICKYERR and not exempt:
            self.ack = swd.ACK_FAULT
        elif self.ap_busy and not exempt:
            self.ack = swd.ACK_WAIT
            self.ap_busy -= 1
        else:
            self.ack = swd.ACK_OK
            if apndp:
                if self.ap_fails:
                    self.sticky |= STICKYERR
                self.ap_busy = self.ap_busy_for
                self.ap_fails, self.ap_busy_for = False, 0
        self.log.append(("request", req, self.ack))
        self.edge = 8
        self.state = "transfer"
        if rnw:
            dp = {0x0: DP_IDCODE, 0x4: self.ctrl_stat | self.sticky, 0xC: 0}
            self.rdata = 0 if apndp else dp.get(addr, 0)

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
                self.log.append(("write", self.apndp, self.addr, self.wdata, ok))
                if ok and not self.apndp:
                    if self.addr == DP_ABORT and self.wdata & STKERRCLR:
                        self.sticky &= ~STICKYERR
                    elif self.addr == DP_CTRL_STAT:
                        self.ctrl_stat = self.wdata & ~STICKYERR  # sticky bits read only
                    elif self.addr == DP_SELECT:
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
    assert ("write", 0, 0x4, 0x50000000, True) in dp.log
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
async def test_swd_wait(dut):
    """An AP write leaves the AP busy: the next two requests get WAIT, with
    no data phase (ADIv5 with ORUNDETECT = 0), except a CTRL/STAT read. The
    write's data would read as a valid request (0xA5, a DP IDCODE read) if
    it were sent anyway, and the target would then drive SWDIO against the
    host. The program drops the data, reports the ACK, and the Host retries."""
    chip, dp, contention = await setup(dut)
    dp.ap_busy_for = 2
    b = swd.Batch()
    b.line_reset()
    b.write(1, 0x0, 0x23000002)     # AP CSW: OK, then busy
    b.write(0, DP_SELECT, 0xA5)     # WAIT
    b.read(0, DP_CTRL_STAT)         # OK while busy
    b.write(0, DP_SELECT, 0xA5)     # WAIT
    b.write(0, DP_SELECT, 0xA5)     # retried: OK
    b.read(0, 0x0)
    res = await run(chip, b, 12)  # 4 words per successful read, 1 otherwise
    assert res == [(swd.ACK_OK,), (swd.ACK_WAIT,), (swd.ACK_OK, 0, True), (swd.ACK_WAIT,),
                   (swd.ACK_OK,), (swd.ACK_OK, DP_IDCODE, True)], res
    assert [e[2] for e in dp.log if e[0] == "request"] == [
        swd.ACK_OK, swd.ACK_WAIT, swd.ACK_OK, swd.ACK_WAIT, swd.ACK_OK, swd.ACK_OK], dp.log
    assert dp.select == 0xA5
    assert not contention, "host and target drove SWDIO at the same time"
    assert not dp.check()


@cocotb.test()
async def test_swd_fault_and_abort(dut):
    """An AP transaction error sets STICKYERR: the target answers FAULT to
    AP and DP accesses, with no data phase, but still answers CTRL/STAT
    reads, which show the error. The Host clears it through ABORT and the
    accesses that faulted then succeed."""
    chip, dp, contention = await setup(dut)
    dp.ap_fails = True
    b = swd.Batch()
    b.line_reset()
    b.write(1, 0x4, 0x20000000)     # AP TAR: OK, the transaction fails
    b.read(1, 0xC)                  # AP DRW: FAULT
    b.write(0, DP_SELECT, 0xA5)     # FAULT
    b.read(0, DP_CTRL_STAT)         # OK: STICKYERR set
    b.write(0, DP_ABORT, STKERRCLR)
    b.read(0, DP_CTRL_STAT)         # OK: clear
    b.write(0, DP_SELECT, 0xA5)     # OK
    b.read(1, 0xC)                  # OK
    res = await run(chip, b, 17)
    assert res == [(swd.ACK_OK,), (swd.ACK_FAULT,), (swd.ACK_FAULT,), (swd.ACK_OK, STICKYERR, True),
                   (swd.ACK_OK,), (swd.ACK_OK, 0, True), (swd.ACK_OK,), (swd.ACK_OK, 0, True)], res
    assert dp.select == 0xA5
    assert not contention, "host and target drove SWDIO at the same time"
    assert not dp.check()
