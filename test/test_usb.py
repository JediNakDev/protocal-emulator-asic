# SPDX-License-Identifier: Apache-2.0
"""Low-speed USB host programs against an independent USB device model in
absolute time at the 48 MHz clock profile."""

import cocotb
from cocotb.triggers import First, RisingEdge, Timer

from chip import MHZ_48, Chip, assemble, fails_at_wrong_clock, program_source, regs
from pe import g2 as g2lib
from pe.proto import usb
from peers import now_ns, sleep_ns, sleep_until

PROFILE = MHZ_48
DP, DM, RX_EN = 0, 1, 12
J_LEVELS, K_LEVELS, SE0_LEVELS = (0, 1), (1, 0), (0, 0)
BIT_NS = 1e9 / 1.5e6
EOP_NS = (1250, 1500)          # host end-of-packet SE0, USB 2.0 table 7-10
SUSPEND_NS = 3_000_000         # a device suspends after 3 ms of idle bus
RESET_DETECT_NS = 2_500        # a longer SE0 is a reset
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
    """Low-speed device at address 0, in absolute time: receives with its
    own clock recovery, answers SETUP+DATA0 with ACK and IN with DATA1. Its
    bit time is `rate` times the nominal 666.7 ns. Checks the host's bit
    rate against 1.5 Mbit/s +/- 1.5% and its end-of-packet SE0 width
    (1.25-1.50 us). Records keep-alives, bus resets (SE0 longer than
    2.5 us, with their length) and suspends (3 ms without bus activity)."""

    def __init__(self, chip, rate=1.0, reply_after=1.5):
        self.chip = chip
        self.bit = BIT_NS * rate
        self.reply_after = reply_after  # bit times from the end of SE0 to SYNC
        self.mute_in = False            # ignore IN tokens, as if absent
        self.packets = []    # (pid, payload, ok) received from the host
        self.setup = None
        self.acks = 0
        self.keepalives, self.resets, self.suspends = [], [], []
        self.activity = []   # (start, end) of everything but idle J
        self.violations = []
        self.last_activity = now_ns()

    def _line(self):
        v = self.chip.uio()
        return (v >> DP) & 1, (v >> DM) & 1

    async def _change(self, timeout_ns=None):
        """Wait for the line state to change; False after timeout_ns."""
        cur = self._line()
        deadline = None if timeout_ns is None else now_ns() + timeout_ns
        while True:
            if deadline is None:
                await self.chip.dut.uio_in.value_change
            else:
                left = round((deadline - now_ns()) * 1000)
                if left <= 0:
                    return False
                await First(self.chip.dut.uio_in.value_change, Timer(left, unit="ps"))
            if self._line() != cur:
                return True

    def _check_eop(self, t0, t1):
        if not EOP_NS[0] <= t1 - t0 <= EOP_NS[1]:
            self.violations.append(f"timing: USB end-of-packet SE0 {t1 - t0:.0f} ns at {t0:.0f} ns")

    def _check_rate(self, edges):
        """Each transition must lie within 1.5% per bit, plus 0.1 bit, of
        the nominal bit grid started by the first K."""
        k = 0
        for a, b in zip(edges, edges[1:]):
            k += round((b - a) / BIT_NS)
            off = b - edges[0] - k * BIT_NS
            if abs(off) > (0.015 * k + 0.1) * BIT_NS:
                self.violations.append(f"timing: USB bit rate, transition {k} bits after "
                                       f"{edges[0]:.0f} ns is {off:+.0f} ns off")
                return

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

    def _handle(self, pkt):
        """Returns the reply's levels, or None."""
        self.packets.append(pkt)
        pid, payload, ok = pkt
        if not ok:
            return None
        if pid == usb.PID_SETUP:
            self.expect_setup_data = True
        elif pid == usb.PID_DATA0 and getattr(self, "expect_setup_data", False):
            self.expect_setup_data = False
            self.setup = payload
            return self._levels_for(usb.PID_ACK)
        elif pid == usb.PID_IN and not self.mute_in:
            return self._levels_for(usb.PID_DATA1, DESCRIPTOR)
        elif pid == usb.PID_ACK:
            self.acks += 1
        return None

    async def _transmit(self, levels):
        start = now_ns()
        for k, (dp, dm) in enumerate(levels):
            self.chip.drive_uio(DP, dp)
            self.chip.drive_uio(DM, dm)
            await sleep_until(start + (k + 1) * self.bit)
        self.chip.drive_uio(DP, None)
        self.chip.drive_uio(DM, None)

    async def _receive(self, t0):
        """From the first K of a SYNC: sample each bit half a bit after the
        last transition, until a sample finds SE0."""
        levels, edges, next_sample, t_se0 = [], [t0], t0 + self.bit / 2, None
        while True:
            if await self._change(next_sample - now_ns()):
                if self._line() != SE0_LEVELS:
                    edges.append(now_ns())
                    next_sample = now_ns() + self.bit / 2  # resynchronize
                else:
                    t_se0 = now_ns()
                continue
            if self._line() != SE0_LEVELS:
                levels.append(self._line())
                next_sample += self.bit
                continue
            # Half a bit into the two-bit SE0: decide; the J after it starts
            # 1.5 bits later.
            t_sample = now_ns()
            self._check_rate(edges)
            pkt = self._decode(levels)
            reply = self._handle(pkt) if pkt else None
            await self._change()
            self._check_eop(t_se0, now_ns())
            if reply:
                await sleep_until(t_sample + (1.5 + self.reply_after) * self.bit)
                await self._transmit(reply)
            return

    async def run(self):
        while True:
            cur = self._line()
            if cur == J_LEVELS:
                left = SUSPEND_NS - (now_ns() - self.last_activity)
                if not await self._change(left):
                    self.suspends.append(now_ns())
                    await self._change()
                continue
            t = now_ns()
            if cur == K_LEVELS:
                await self._receive(t)
            elif cur == SE0_LEVELS:
                await self._change()
                if now_ns() - t > RESET_DETECT_NS:
                    self.resets.append(now_ns() - t)
                else:
                    self.keepalives.append(t)
                    self._check_eop(t, now_ns())
            else:
                await self._change()
            self.last_activity = now_ns()
            self.activity.append((t, now_ns()))


async def setup(dut, rate=1.0, reply_after=1.5, hz=PROFILE, enable=True):
    chip = Chip(dut, hz)
    await chip.start()
    await chip.set_quad()
    chip.pull_up(1 << DM)  # low-speed device pull-up on D-
    host = assemble(program_source("usb_ls_host"))
    rx = assemble(program_source("usb_ls_rx"))
    await chip.load(host, 0)
    await chip.load(rx, len(host))
    await chip.setup_engine(0, host, 0, clkdiv=3, shiftctrl=regs.AUTOPULL | regs.OUT_RIGHT, out_base=DP,
                            out_count=2, set_base=DP, set_count=2, in_base=DP, side_base=RX_EN,
                            execcfg=host.label("table"), statuscfg=1)
    await chip.setup_engine(1, rx, len(host), clkdiv=7, thresh=15,
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
    dev = UsbDevice(chip, rate=rate, reply_after=reply_after)
    cocotb.start_soon(dev.run())
    contention = []

    async def watch():
        while True:
            await RisingEdge(dut.clk)
            if int(dut.contention.value) & 3:
                contention.append(now_ns())
    cocotb.start_soon(watch())
    if enable:
        await chip.enable(3)
    return chip, dev, contention


async def replies(chip, n, timeout=400000):
    """The next n reply records, parsed; None for a reply that did not come."""
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


async def get_descriptor(dut, rate=1.0, hz=PROFILE):
    chip, dev, contention = await setup(dut, rate, hz=hz)
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
    assert not dev.violations, dev.violations[:5]
    assert not contention, f"D+/D- driven by both sides at {contention[:5]} ns"


@cocotb.test()
async def test_usb_get_descriptor(dut):
    await get_descriptor(dut)


@cocotb.test()
async def test_usb_device_clock_fast(dut):
    """Low-speed devices may be 1.5% off; G2's clock recovery tracks it."""
    await get_descriptor(dut, 0.985)


@cocotb.test()
async def test_usb_device_clock_slow(dut):
    await get_descriptor(dut, 1.015)


@cocotb.test()
async def test_usb_wrong_clock(dut):
    """At 50 MHz the 48 MHz settings send 1.5625 Mbit/s, 4% fast."""
    msg = await fails_at_wrong_clock(lambda hz: get_descriptor(dut, hz=hz), PROFILE)
    assert "timing: USB bit rate" in msg, msg


async def in_transaction(chip):
    tx = usb.command(usb.token(usb.PID_IN, 0, 0), True)
    await chip.stream(0, tx, 0)
    (reply,) = await replies(chip, 1)
    return reply


@cocotb.test()
async def test_usb_missing_reply_is_bounded(dut):
    """A device that does not answer: the program gives up at its deadline,
    reports a record without a SYNC, and runs the next command without any
    Host intervention."""
    chip, dev, contention = await setup(dut)
    dev.mute_in = True
    start = now_ns()
    assert await in_transaction(chip) is None
    # Bounded: the IN token, the 17-bit deadline and the Host's polling.
    assert now_ns() - start < 80 * BIT_NS, now_ns() - start
    # Not stuck: the next transaction runs and succeeds, and the program
    # returns to its idle loop, which sends keep-alives.
    dev.mute_in = False
    assert await in_transaction(chip) == (usb.PID_DATA1, DESCRIPTOR, True)
    t = now_ns()
    await sleep_ns(1_000_000)
    assert [k for k in dev.keepalives if k > t], "no keep-alive after the transaction"
    assert not dev.violations, dev.violations[:5]
    assert not contention, f"D+/D- driven by both sides at {contention[:5]} ns"


async def reply_at(dut, reply_after):
    chip, dev, contention = await setup(dut, reply_after=reply_after)
    reply = await in_transaction(chip)
    await sleep_ns(40 * BIT_NS)  # let a late reply finish
    assert not contention, f"D+/D- driven by both sides at {contention[:5]} ns"
    return reply


@cocotb.test()
async def test_usb_reply_inside_deadline(dut):
    """USB 2.0 7.1.19.1: the host waits at least 16 bit times for a reply.
    A reply starting 16.75 bit times after the end of SE0 is received."""
    assert await reply_at(dut, 16.75) == (usb.PID_DATA1, DESCRIPTOR, True)


@cocotb.test()
async def test_usb_reply_after_deadline(dut):
    """...and at most 18: a reply starting at 17.25 bit times is missing."""
    assert await reply_at(dut, 17.25) is None


@cocotb.test()
async def test_usb_keep_alive(dut):
    """Without the program the device suspends after 3 ms of idle bus; with
    it, the bus is never idle for 1 ms: keep-alives fill every pause,
    before and after a transaction, and the device stays awake."""
    chip, dev, contention = await setup(dut, enable=False)
    await sleep_ns(3_500_000)
    assert len(dev.suspends) == 1, dev.suspends
    t0 = now_ns()
    await chip.enable(3)
    await sleep_ns(10_000_000)
    assert await in_transaction(chip) == (usb.PID_DATA1, DESCRIPTOR, True)
    await sleep_ns(3_000_000)
    assert len(dev.suspends) == 1, dev.suspends
    spans = [(t0, t0)] + [a for a in dev.activity if a[0] >= t0]
    idle = max(b[0] - a[1] for a, b in zip(spans, spans[1:]))
    assert len(dev.keepalives) >= 14 and idle <= 1_000_000, (len(dev.keepalives), idle)
    assert not dev.violations, dev.violations[:5]
    assert not contention, f"D+/D- driven by both sides at {contention[:5]} ns"


@cocotb.test()
async def test_usb_bus_reset(dut):
    """A reset command holds SE0 for at least 50 ms, the minimum for a root
    port (USB 2.0 7.1.7.5, TDRSTR), and less than one more 1.025 ms unit,
    then the device answers as usual."""
    chip, dev, contention = await setup(dut)
    await chip.push(0, usb.reset())
    await sleep_ns(52_000_000)
    assert len(dev.resets) == 1 and 50_000_000 <= dev.resets[0] <= 51_025_000, dev.resets
    assert not dev.suspends, dev.suspends
    assert await in_transaction(chip) == (usb.PID_DATA1, DESCRIPTOR, True)
    assert not dev.violations, dev.violations[:5]
    assert not contention, f"D+/D- driven by both sides at {contention[:5]} ns"
