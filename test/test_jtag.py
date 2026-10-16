# SPDX-License-Identifier: Apache-2.0
"""JTAG controller program against an independent IEEE 1149.1 TAP model,
in absolute time at the 50 MHz clock profile."""

import cocotb

from chip import MHZ_50, Chip, fails_at_wrong_clock, regs
from pe.proto.jtag import Scan
from peers import Wave, clock_violations, now_ns, setup_hold_violations, sleep_ns

PROFILE = MHZ_50
TCK_HZ = 6_250_000  # 8 cycles per TCK at CLKDIV 0

TDI, TMS, TCK, TDO_PIN = 0, 1, 2, 9

IDCODE_VALUE = 0x4BA00477
IR_IDCODE, IR_SCRATCH, IR_BYPASS = 0b1110, 0b1010, 0b1111

# Next state for TMS = 0 and TMS = 1.
TAP = {
    "TLR": ("RTI", "TLR"), "RTI": ("RTI", "SDS"),
    "SDS": ("CDR", "SIS"), "CDR": ("SDR", "E1D"), "SDR": ("SDR", "E1D"),
    "E1D": ("PDR", "UDR"), "PDR": ("PDR", "E2D"), "E2D": ("SDR", "UDR"),
    "UDR": ("RTI", "SDS"),
    "SIS": ("CIR", "TLR"), "CIR": ("SIR", "E1I"), "SIR": ("SIR", "E1I"),
    "E1I": ("PIR", "UIR"), "PIR": ("PIR", "E2I"), "E2I": ("SIR", "UIR"),
    "UIR": ("RTI", "SDS"),
}


class TapModel:
    """A target with a 4-bit IR, IDCODE, BYPASS and an 8-bit scratch register.
    Samples TMS/TDI on TCK rising and changes TDO `tco_ns` after TCK falls.
    `check()` reports TCK periods off the nominal rate and TMS or TDI
    setup or hold times below 10 ns."""

    def __init__(self, chip, tco_ns=10.0):
        self.chip = chip
        self.tco_ns = tco_ns
        self.tck = Wave(chip.dut.uio_in, TCK)
        self.tms = Wave(chip.dut.uio_in, TMS)
        self.tdi = Wave(chip.dut.uio_in, TDI)
        self.rises = []
        self.state = "TLR"
        self.ir = IR_IDCODE
        self.scratch = 0xC3
        self.sr = 0
        self.sr_len = 0
        self.tck_rises = 0

    def _dr_len(self):
        return {IR_IDCODE: 32, IR_SCRATCH: 8}.get(self.ir, 1)

    def _rise(self, tms, tdi):
        st = self.state
        if st == "CDR":
            self.sr_len = self._dr_len()
            self.sr = {IR_IDCODE: IDCODE_VALUE, IR_SCRATCH: self.scratch}.get(self.ir, 0)
        elif st == "CIR":
            self.sr_len, self.sr = 4, 0b0001
        elif st in ("SDR", "SIR"):
            self.sr = (self.sr >> 1) | (tdi << (self.sr_len - 1))
        self.state = TAP[st][tms]
        if self.state == "TLR":
            self.ir = IR_IDCODE

    def _fall(self):
        if self.state == "UIR":
            self.ir = self.sr & 0xF
        elif self.state == "UDR" and self.ir == IR_SCRATCH:
            self.scratch = self.sr & 0xFF
        if self.state in ("SDR", "SIR"):
            cocotb.start_soon(self._tdo_later(self.sr & 1))

    async def _tdo_later(self, bit):
        await sleep_ns(self.tco_ns)
        self.chip.set_pin9(bit)

    async def run(self):
        while True:
            if await self.tck.change():
                self.tck_rises += 1
                self.rises.append(now_ns())
                self._rise(self.tms.read(), self.tdi.read())
            else:
                self._fall()

    def check(self):
        out, checked = clock_violations(self.tck, True, TCK_HZ)
        assert checked, "no TCK period to check"
        out += setup_hold_violations(self.tms, self.rises, 10, 10, "TMS")
        return out + setup_hold_violations(self.tdi, self.rises, 10, 10, "TDI")


async def jtag_session(dut, hz=PROFILE):
    chip = Chip(dut, hz)
    await chip.start()
    await chip.set_quad()
    prog = await chip.load_program("jtag")
    await chip.setup_engine(0, prog, shiftctrl=regs.AUTOPULL | regs.AUTOPUSH | regs.OUT_RIGHT
                            | regs.IN_RIGHT, out_base=TDI, out_count=2, in_base=TDO_PIN,
                            side_base=TCK)
    await chip.ereg(0, regs.SETPIN, TDI | (3 << 4))
    await chip.force_asm(0, "set pindirs, 7 side 0", side_count=1)
    for p in (TDI, TMS, TCK):
        await chip.pincfg(p, regs.DRIVE_PUSH_PULL)
    tap = TapModel(chip)
    cocotb.start_soon(tap.run())

    scan = Scan()
    scan.reset()
    scan.dr(0, 32)                  # IDCODE is selected after reset
    scan.ir(IR_SCRATCH, 4)
    scan.dr(0x5A, 8)
    scan.dr(0xA5, 8)
    scan.ir(IR_BYPASS, 4)
    scan.dr(0b1011, 4)

    await chip.enable(1)
    rx = await chip.stream(0, scan.words(), scan.n_rx_words())
    idcode, ir_cap, scr0, scr1, ir_cap2, bypass = scan.results(rx)
    assert idcode == IDCODE_VALUE, hex(idcode)
    assert ir_cap == 0b0001 and ir_cap2 == 0b0001
    assert scr0 == 0xC3 and scr1 == 0x5A, (hex(scr0), hex(scr1))
    assert tap.scratch == 0xA5
    assert bypass == 0b0110, bin(bypass)  # one-cycle delay through BYPASS
    assert tap.state == "RTI"
    assert tap.tck_rises == len(scan.words()) * 8
    violations = tap.check()
    assert not violations, violations[:5]


@cocotb.test()
async def test_jtag_idcode_ir_dr_bypass(dut):
    await jtag_session(dut)


@cocotb.test()
async def test_jtag_wrong_clock(dut):
    msg = await fails_at_wrong_clock(lambda hz: jtag_session(dut, hz), PROFILE)
    assert "timing: clock period" in msg, msg
