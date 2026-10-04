# SPDX-License-Identifier: Apache-2.0
"""Low-speed USB host programs against an independent USB device model."""

import cocotb
from cocotb.triggers import RisingEdge

from chip import Chip, assemble, program_source, regs
from pe import g2 as g2lib
from pe.proto import usb

DP, DM, RX_EN = 0, 1, 12
CLK_PS = 20834  # 48 MHz (47.998 MHz: the clock period must be even in ps)
DESCRIPTOR = bytes([0x12, 0x01, 0x10, 0x01, 0x00, 0x00, 0x00, 0x08])
SETUP_GET_DESCRIPTOR = bytes([0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x08, 0x00])


def dev_crc16(data):
    reg = 0xFFFF
    for byte in data:
        for i in range(8):
            if (reg ^ (byte >> i)) & 1:
                reg = (reg >> 1) ^ 0xA001
            else:
                reg >>= 1
    return reg ^ 0xFFFF


def dev_crc5(bits):
    reg = 0x1F
    for b in bits:
        if (reg ^ b) & 1:
            reg = (reg >> 1) ^ 0x14
        else:
            reg >>= 1
    return reg ^ 0x1F


class UsbDevice:
    """Low-speed device at address 0: receives with its own clock recovery,
    answers SETUP+DATA0 with ACK and IN with DATA1. Its bit time may differ
    from the host's."""

    def __init__(self, chip, bit=32.0):
        self.chip = chip
        self.bit = bit
        self.packets = []    # (pid, payload, ok) received from the host
        self.setup = None
        self.acks = 0
        self.next_tx = None  # (start cycle, levels)
        self.now = 0

    def _line(self):
        v = self.chip.uio()
        return (v >> DP) & 1, (v >> DM) & 1

    def _decode(self, levels):
        bits, prev = [], (0, 1)
        for lv in levels:
            bits.append(1 if lv == prev else 0)
            prev = lv
        if bits[:8] != [0] * 7 + [1]:
            return None
        out, run = [], 0
        for b in bits[8:]:
            if run == 6:
                run = 0
                continue
            out.append(b)
            run = run + 1 if b else 0
        if len(out) < 8:
            return None
        pid = sum(out[i] << i for i in range(4))
        if sum(out[4 + i] << i for i in range(4)) != pid ^ 0xF:
            return None
        rest = out[8:]
        if pid in (usb.PID_SETUP, usb.PID_IN, usb.PID_OUT):
            ok = len(rest) == 16 and dev_crc5(rest[:11]) == sum(rest[11 + i] << i for i in range(5))
            return pid, bytes(), ok
        data = bytes(sum(rest[8 * k + i] << i for i in range(8)) for k in range(len(rest) // 8))
        if pid in (usb.PID_DATA0, usb.PID_DATA1):
            ok = len(data) >= 2 and dev_crc16(data[:-2]) == data[-2] | data[-1] << 8
            return pid, data[:-2], ok
        return pid, data, True

    def _levels_for(self, pid, payload=None):
        bits = [0] * 7 + [1] + [(pid >> i) & 1 for i in range(4)] + [((pid ^ 0xF) >> i) & 1 for i in range(4)]
        if payload is not None:
            body = bytes(payload) + bytes([dev_crc16(payload) & 0xFF, dev_crc16(payload) >> 8])
            bits += [(byte >> i) & 1 for byte in body for i in range(8)]
        stuffed, run = [], 0
        for b in bits:
            stuffed.append(b)
            run = run + 1 if b else 0
            if run == 6:
                stuffed.append(0)
                run = 0
        levels, cur = [], (0, 1)
        for b in stuffed:
            if b == 0:
                cur = (1, 0) if cur == (0, 1) else (0, 1)
            levels.append(cur)
        return levels + [(0, 0), (0, 0), (0, 1)]

    def _reply(self, pid, payload=None):
        self.next_tx = (self.now + round(3 * self.bit), self._levels_for(pid, payload))

    def _handle(self, pkt):
        self.packets.append(pkt)
        pid, payload, ok = pkt
        if not ok:
            return
        if pid == usb.PID_SETUP:
            self.expect_setup_data = True
        elif pid == usb.PID_DATA0 and getattr(self, "expect_setup_data", False):
            self.expect_setup_data = False
            self.setup = payload
            self._reply(usb.PID_ACK)
        elif pid == usb.PID_IN:
            self._reply(usb.PID_DATA1, DESCRIPTOR)
        elif pid == usb.PID_ACK:
            self.acks += 1

    async def _transmit(self, levels):
        start = self.now
        for k, (dp, dm) in enumerate(levels):
            self.chip.drive_uio(DP, dp)
            self.chip.drive_uio(DM, dm)
            while self.now - start < round((k + 1) * self.bit):
                await RisingEdge(self.chip.dut.clk)
                self.now += 1
        self.chip.drive_uio(DP, None)
        self.chip.drive_uio(DM, None)

    async def run(self, clk):
        levels, prev, receiving, next_sample = [], (0, 1), False, None
        while True:
            await RisingEdge(clk)
            self.now += 1
            if self.next_tx and self.now >= self.next_tx[0]:
                _, tx = self.next_tx
                self.next_tx = None
                await self._transmit(tx)
                prev, receiving = (0, 1), False
                continue
            cur = self._line()
            if not receiving:
                if cur == (1, 0):  # K: start of SYNC
                    receiving, levels = True, []
                    next_sample = self.now + round(self.bit / 2)
                prev = cur
                continue
            if cur != prev and cur != (0, 0):
                next_sample = self.now + round(self.bit / 2)  # resynchronize on transitions
            prev = cur
            if self.now == next_sample:
                if cur == (0, 0):
                    receiving = False
                    pkt = self._decode(levels)
                    if pkt:
                        self._handle(pkt)
                    continue
                levels.append(cur)
                next_sample = self.now + round(self.bit)


async def setup(dut, device_bit):
    chip = Chip(dut, clk_ps=CLK_PS)
    await chip.start()
    await chip.set_quad()
    chip.pull_up(1 << DM)  # low-speed device pull-up on D-
    host = assemble(program_source("usb_ls_host"))
    rx = assemble(program_source("usb_ls_rx"))
    await chip.load(host, 0)
    await chip.load(rx, len(host))
    await chip.setup_engine(0, host, 0, clkdiv=3, shiftctrl=regs.AUTOPULL | regs.OUT_RIGHT, out_base=DP,
                            out_count=2, set_base=DP, set_count=2, in_base=DP, side_base=RX_EN)
    await chip.setup_engine(1, rx, len(host), clkdiv=7,
                            shiftctrl=regs.AUTOPUSH | regs.fifo_mode(regs.FIFO_JOIN_RX))
    await chip.write(regs.G2_ADDR, 0)
    await chip.write(regs.G2_DATA, g2lib.usb_nrzi_dpll())
    await chip.write(regs.G2_IN0, [DP, RX_EN])
    await chip.write(regs.G2_STATE, 0)
    await chip.write(regs.G2_CTRL, regs.G2_ENABLE | regs.G2_OWNER1 | regs.G2_STEP_TICK | regs.G2_EMIT)
    await chip.force_asm(0, "set pins, 2 side 0", side_count=1, side_opt=True)  # J, receive off
    for p in (DP, DM):
        await chip.pincfg(p, regs.DRIVE_PUSH_PULL)
    await chip.pincfg(RX_EN, regs.DRIVE_ALWAYS)
    dev = UsbDevice(chip, bit=device_bit)
    cocotb.start_soon(dev.run(dut.clk))
    contention = []

    async def watch():
        while True:
            await RisingEdge(dut.clk)
            if int(dut.contention.value) & 3:
                contention.append(dev.now)
    cocotb.start_soon(watch())
    await chip.enable(3)
    return chip, dev, contention


async def replies(chip, n, timeout=400000):
    words, waited = [], 0
    while True:
        _, rx = await chip.levels(1)
        if rx:
            words += await chip.pop(1, rx)
        recs = usb.split_records(words)
        if len(recs) >= n:
            return [usb.parse(r) for r in recs]
        await chip.cycles(200)
        waited += 200
        if waited > timeout:
            raise TimeoutError(f"{len(recs)} replies: {[hex(w) for w in words]}")


async def get_descriptor(dut, device_bit):
    chip, dev, contention = await setup(dut, device_bit)
    # SETUP stage: token + DATA0, device answers ACK.
    tx = usb.command(usb.token(usb.PID_SETUP, 0, 0), False)
    tx += usb.command(usb.data_packet(usb.PID_DATA0, SETUP_GET_DESCRIPTOR), True)
    await chip.stream(0, tx, 0)
    (ack,) = await replies(chip, 1)
    assert ack == (usb.PID_ACK, b"", True), ack
    assert dev.setup == SETUP_GET_DESCRIPTOR
    # DATA stage: IN, device answers DATA1, host ACKs right after.
    tx = usb.command(usb.token(usb.PID_IN, 0, 0), True) + usb.command(usb.handshake(usb.PID_ACK), False)
    await chip.stream(0, tx, 0)
    (data,) = await replies(chip, 1)
    assert data == (usb.PID_DATA1, DESCRIPTOR, True), data
    for _ in range(50):
        if dev.acks:
            break
        await chip.cycles(100)
    assert dev.acks == 1
    assert all(p[2] for p in dev.packets), dev.packets
    assert not contention, f"D+/D- driven by both sides at cycles {contention[:5]}"


@cocotb.test()
async def test_usb_get_descriptor(dut):
    await get_descriptor(dut, 32.0)


@cocotb.test()
async def test_usb_device_clock_fast(dut):
    """Low-speed devices may be 1.5% off; G2's clock recovery tracks it."""
    await get_descriptor(dut, 32.0 * 0.985)


@cocotb.test()
async def test_usb_device_clock_slow(dut):
    await get_descriptor(dut, 32.0 * 1.015)
