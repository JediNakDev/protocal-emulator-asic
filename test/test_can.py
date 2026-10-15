# SPDX-License-Identifier: Apache-2.0
"""CAN receive and transmit programs against an independent CAN node on a
wired-AND bus with transceiver delay. The bus and the node keep their own
timing in ns, whatever the chip clock."""

from collections import deque, namedtuple

import cocotb
from cocotb.triggers import Event, First

from chip import MHZ_50, Chip, assemble, fails_at_wrong_clock, program_source, regs
from pe import g2 as g2lib
from pe.proto import can
from peers import Wave, now_ns, sleep_until

PROFILE = MHZ_50
TXD, RXD = 0, 8
BIT_NS = 1000.0     # 1 Mbit/s
SAMPLE = 0.7        # sample point, as a fraction of the bit
SJW_NS = 200.0      # resynchronization jump width (2 of 10 time quanta)
LOOP_DELAY_NS = 80  # transceiver TXD -> bus -> RXD


def node_crc15(bits):
    reg = 0
    for b in bits:
        top = (reg >> 14) & 1
        reg = ((reg << 1) & 0x7FFF) ^ (0x4599 if top ^ b else 0)
    return reg


class CanBus:
    """Wired-AND bus: dominant (0) wins. The chip's RXD and every node see
    a change of the bus LOOP_DELAY_NS after the driver made it."""

    def __init__(self, chip, delay_ns=LOOP_DELAY_NS):
        self.chip = chip
        self.delay = delay_ns
        self.nodes = []
        self.queue = deque()
        self.wake = Event()
        self.line = Wave(chip.dut.ui_in, 6)  # the delayed bus, as RXD sees it
        cocotb.start_soon(self._watch_chip())
        cocotb.start_soon(self._deliver())

    def chip_tx(self):
        dut = self.chip.dut
        if (int(dut.uio_oe.value) >> TXD) & 1:
            return (int(dut.uio_out.value) >> TXD) & 1
        return 1

    def update(self):
        level = self.chip_tx()
        for node in self.nodes:
            level &= node.tx
        self.queue.append((now_ns() + self.delay, level))
        self.wake.set()

    async def _watch_chip(self):
        dut = self.chip.dut
        while True:
            await First(dut.uio_out.value_change, dut.uio_oe.value_change)
            self.update()

    async def _deliver(self):
        while True:
            while not self.queue:
                self.wake.clear()
                await self.wake.wait()
            t, level = self.queue.popleft()
            await sleep_until(t)
            self.chip.set_pin8(level)


Tx = namedtuple("Tx", "ident data join ext rtr dlc bad_crc",
                defaults=(b"", False, False, False, None, False))
Rx = namedtuple("Rx", "ident data ok ext rtr dlc")


def bits_of(value, n):
    return [(value >> (n - 1 - i)) & 1 for i in range(n)]


def value_of(bits):
    v = 0
    for b in bits:
        v = v << 1 | b
    return v


class CanNode:
    """A CAN 2.0B node: transmits with arbitration, receives, and
    acknowledges good frames. It hard-synchronizes on a start of frame and,
    while receiving, resynchronizes on recessive-to-dominant edges by up to
    SJW_NS; an edge further than that from its bit boundary is a timing
    violation. Its bit time may differ from the nominal one. A frame to
    send may carry a wrong CRC (bad_crc) or join a start of frame seen on
    the bus (join) instead of waiting for the bus to be idle. A frame that
    loses arbitration is sent again once the bus is idle."""

    def __init__(self, bit_ns=BIT_NS):
        self.bit = bit_ns
        self.sample = SAMPLE * bit_ns
        self.tx = 1
        self.pending = []      # Tx
        self.received = []     # Rx
        self.sent = []         # (ident, acked)
        self.lost = []
        self.violations = []
        self.bus = None

    def send(self, ident, data=b"", **kw):
        self.pending.append(Tx(ident, bytes(data), **kw))

    def attach(self, bus):
        self.bus = bus
        bus.nodes.append(self)
        cocotb.start_soon(self._run())

    def _drive(self, level):
        if level != self.tx:
            self.tx = level
            self.bus.update()

    @staticmethod
    def _frame_bits(tx):
        dlc = len(tx.data) if tx.dlc is None else tx.dlc
        b = [0]
        if tx.ext:
            b += bits_of(tx.ident >> 18, 11) + [1, 1] + bits_of(tx.ident & 0x3FFFF, 18)
        else:
            b += bits_of(tx.ident, 11)
        b += [int(tx.rtr), 0, 0] + bits_of(dlc, 4)
        for byte in tx.data:
            b += bits_of(byte, 8)
        b += bits_of(node_crc15(b) ^ int(tx.bad_crc), 15)
        out, run, last = [], 0, None
        for x in b:
            out.append(x)
            run = run + 1 if x == last else 1
            last = x
            if run == 5:
                out.append(1 - x)
                last, run = 1 - x, 1
        return out + [1, 1, 1] + [1] * 7

    def _begin(self, transmit):
        self.data_bits = []
        self.run, self.last = 0, None
        self.stuffed = True
        self.need = None
        self.crc_end = None
        self.ack_drive = False
        self.transmitting = False
        self.done = False
        if transmit:
            self.tx_frame = self.pending.pop(0)
            self.tx_bits = self._frame_bits(self.tx_frame)
            self.arb_end = 32 if self.tx_frame.ext else 12
            self.transmitting = True

    def _boundary(self, k):
        if self.transmitting and k < len(self.tx_bits):
            self._drive(self.tx_bits[k])
        elif self.ack_drive and self.crc_end is not None and k == self.crc_end + 2:
            self._drive(0)
        else:
            self._drive(1)

    def _sample(self, k, b):
        if self.transmitting and self.stuffed and self.tx_bits[k] != b:
            self.transmitting = False  # become a receiver
            if self.tx_bits[k] and len(self.data_bits) <= self.arb_end:
                self.lost.append(self.tx_frame.ident)
                self.pending.insert(0, self.tx_frame._replace(join=False))  # retry
            else:
                self.violations.append(f"bit error in bit {k} of {self.tx_frame.ident:#x}")
        if self.stuffed:
            if self.run == 5:
                if b == self.last:
                    self.violations.append(f"stuff error in bit {k}")
                self.run, self.last = 1, b  # stuff bit
                if self.need is not None and len(self.data_bits) == self.need:
                    self.stuffed = False
                    self.crc_end = k
                return
            self.run = self.run + 1 if b == self.last else 1
            self.last = b
            self.data_bits.append(b)
            bits = self.data_bits
            n = len(bits)
            ext = n > 13 and bits[13]
            head = 39 if ext else 19
            if self.need is None and n == head:
                rtr = bits[32] if ext else bits[12]
                dlc = value_of(bits[head - 4:head])
                self.need = head + (0 if rtr else 8 * min(dlc, 8)) + 15
            if n == self.need:
                self._frame_done(k)
            return
        if k == self.crc_end + 2 and self.transmitting:
            self.sent.append((self.tx_frame.ident, b == 0))
        if k == self.crc_end + 10:
            self.done = True

    def _frame_done(self, k):
        bits = self.data_bits
        ext = bits[13]
        head = 39 if ext else 19
        rtr = bits[32] if ext else bits[12]
        dlc = value_of(bits[head - 4:head])
        ident = value_of(bits[1:12])
        if ext:
            ident = ident << 18 | value_of(bits[14:32])
        data = bytes(value_of(bits[head + 8 * j:head + 8 * j + 8])
                     for j in range(0 if rtr else min(dlc, 8)))
        ok = node_crc15(bits) == 0
        if self.run < 5:  # else a stuff bit follows the CRC
            self.stuffed = False
            self.crc_end = k
        if not self.transmitting:
            self.received.append(Rx(ident, data, ok, bool(ext), bool(rtr), dlc))
            self.ack_drive = ok

    async def _bit(self, k, s, may_sync):
        """Bit k, nominally starting at s: drive it at its boundary and sample
        it. Until the sample point, a recessive-to-dominant edge moves the
        boundary by the phase error, limited to SJW_NS. Returns the start."""
        line = self.bus.line
        driven = False
        while True:
            if not driven and now_ns() >= round(s * 1000) / 1000:
                self._boundary(k)
                driven = True
            # In whole ps, as the simulator keeps time: a deadline less than
            # 0.5 ps ahead would otherwise never be reached.
            wake = round((s + self.sample if driven else s) * 1000) / 1000
            if driven and now_ns() >= wake:
                self._sample(k, line.level)
                return s
            if not may_sync:
                await sleep_until(wake)
                continue
            if await line.change(wake - now_ns()) == 0:
                e = now_ns() - s
                if abs(e) > SJW_NS:
                    self.violations.append(
                        f"timing: CAN edge {e:+.0f} ns from the boundary of bit {k}")
                s += max(-SJW_NS, min(SJW_NS, e))
                may_sync = False

    async def _frame(self, t0, transmit):
        self._begin(transmit)
        s, k, prev = t0, 0, 0
        while not self.done:
            may_sync = k > 0 and prev == 1 and self.stuffed and not self.transmitting
            s = await self._bit(k, s, may_sync)
            prev = self.bus.line.level
            s += self.bit
            k += 1
        self._drive(1)

    async def _run(self):
        line = self.bus.line
        idle_since = now_ns()
        while True:
            if line.level == 0:
                join = bool(self.pending) and self.pending[0].join
                await self._frame(now_ns(), transmit=join)
                idle_since = now_ns()
                continue
            since = max(idle_since, line.last_edge(now_ns()) or idle_since)
            if self.pending and not self.pending[0].join and now_ns() - since >= 11 * self.bit:
                await self._frame(now_ns(), transmit=True)
                idle_since = now_ns()
                continue
            await line.change(self.bit)


async def setup_listen(dut, node, hz=PROFILE):
    """can_rx alone on engine 1: a listen-only receiver that never drives
    the bus (TXD stays released, pulled recessive)."""
    chip = Chip(dut, hz)
    await chip.start()
    await chip.set_quad()
    chip.set_pin8(1)  # recessive, before the bus watches it
    rx = assemble(program_source("can_rx"))
    await chip.load(rx, 0)
    await chip.setup_engine(1, rx, 0, shiftctrl=regs.AUTOPUSH | regs.OUT_RIGHT
                            | regs.fifo_mode(regs.FIFO_JOIN_RX), thresh=15,
                            in_base=RXD, jmp_pin=regs.PIN_G2_OUT1)
    await chip.write(regs.G2_ADDR, 0)
    await chip.write(regs.G2_DATA, g2lib.can_destuffer())
    await chip.write(regs.G2_IN0, [regs.G2_SRC_FEED_BIT, regs.G2_SRC_FEED_BIT])
    await chip.write(regs.G2_STATE, 0)
    await chip.write(regs.G2_CTRL, regs.G2_ENABLE | regs.G2_OWNER1 | regs.G2_STEP_FEED | regs.G2_EMIT)
    node.attach(CanBus(chip))
    await chip.enable(2)
    return chip


async def receive_frames(chip, n, timeout=400000):
    words, waited = [], 0
    while True:
        _, rx = await chip.levels(1)
        if rx:
            words += await chip.pop(1, rx)
        records, _ = can.split_records(words)
        if len(records) >= n:
            return [can.parse(r) for r in records]
        await chip.cycles(200)
        waited += 200
        if waited > timeout:
            raise TimeoutError(f"got {len(records)} frames: {[hex(w) for w in words]}")


@cocotb.test()
async def test_can_receive(dut):
    node = CanNode()
    chip = await setup_listen(dut, node)
    node.send(0x123, [0x00, 0x00, 0xFF, 0xFF, 0xA5])
    node.send(0x7FF, [0x55] * 8)
    frames = await receive_frames(chip, 2)
    assert frames[0] == (0x123, bytes([0x00, 0x00, 0xFF, 0xFF, 0xA5]), True), frames
    assert frames[1] == (0x7FF, bytes([0x55] * 8), True), frames
    assert node.sent == [(0x123, False), (0x7FF, False)]  # listen-only: no ACK


@cocotb.test()
async def test_can_receive_separator_payload(dut):
    """Destuffed all-ones words must remain payload, across frame boundaries."""
    node = CanNode()
    chip = await setup_listen(dut, node)
    expected = [(0x123, b"\xff" * 8, True),
                (0x456, b"\xff\xff\xff\x00\xff\xff\xff\xff", True),
                (0x7FF, b"", True)]
    for ident, payload, _ in expected:
        node.send(ident, payload)
    assert await receive_frames(chip, len(expected)) == expected


# The second frame has the longest stretches between recessive-to-dominant
# edges that stuffing allows: eight times five dominant then five recessive
# bits, ten bits without an edge to resynchronize on.
OFFSET_PAYLOADS = [
    (0x555, bytes([0x0F, 0xF0, 0x3C, 0xC3, 0x00, 0xFF, 0x81, 0x7E])),
    (0x000, bytes([0x3C] * 8)),
]


async def receive_with_offset(dut, bit_ns):
    node = CanNode(bit_ns)
    chip = await setup_listen(dut, node)
    for ident, data in OFFSET_PAYLOADS:
        node.send(ident, data)
    frames = await receive_frames(chip, len(OFFSET_PAYLOADS))
    assert frames == [(i, d, True) for i, d in OFFSET_PAYLOADS], frames


@cocotb.test()
async def test_can_receive_clock_offset(dut):
    """Resynchronization on every recessive-to-dominant edge: the transmitter
    1.5% slow (CAN allows up to 1.58% between two nodes)."""
    await receive_with_offset(dut, BIT_NS * 1.015)


@cocotb.test()
async def test_can_receive_clock_offset_fast(dut):
    """...and 1.5% fast."""
    await receive_with_offset(dut, BIT_NS * 0.985)


# ------------------------------------------------------------------ node


async def setup_node(dut, nodes, hz=PROFILE):
    """can_node on engine 0 and can_line on engine 1: a node that
    transmits, arbitrates and acknowledges."""
    chip = Chip(dut, hz)
    await chip.start()
    await chip.set_quad()
    chip.set_pin8(1)
    node = assemble(program_source("can_node"))
    line = assemble(program_source("can_line"))
    await chip.load(node, 0)
    await chip.load(line, len(node))
    await chip.setup_engine(0, node, 0, shiftctrl=regs.AUTOPULL | regs.OUT_RIGHT
                            | regs.fifo_mode(regs.FIFO_JOIN_TX), thresh=0x80,
                            side_base=TXD, jmp_pin=regs.PIN_G2_OUT1,
                            execcfg=node.label("tbl"))
    await chip.ereg(0, regs.LFSRCFG, regs.LFSR_FEED_IN | regs.LFSR_CRC)
    await chip.write32(regs.engine_reg(0, regs.LFSR_POLY), 0x4599 << 17)
    await chip.setup_engine(1, line, len(node), shiftctrl=regs.OUT_RIGHT
                            | regs.fifo_mode(regs.FIFO_JOIN_RX), thresh=15,
                            in_base=RXD, jmp_pin=TXD)
    await chip.write(regs.G2_ADDR, 0)
    await chip.write(regs.G2_DATA, g2lib.can_line())
    await chip.write(regs.G2_IN0, [RXD, TXD, 0])  # in0, in1, state
    await chip.write(regs.G2_CTRL, regs.G2_ENABLE | regs.G2_OWNER1 | regs.G2_STEP_FEED
                     | regs.G2_EMIT | regs.G2_EMIT_OTHER)
    await chip.force_asm(1, f"set y, {line.defines['N1']}")
    await chip.force(1, line.label("idle", len(node)))
    await chip.force_asm(0, "nop side 1", side_count=1, side_opt=True)
    await chip.force(0, node.label("quiet"))
    await chip.pincfg(TXD, regs.DRIVE_ALWAYS | regs.OWNER_E0)
    bus = CanBus(chip)
    for n in nodes:
        n.attach(bus)
    await chip.enable(3)
    chip.can_words = []
    return chip


async def node_send(chip, ident, data=b"", stream=True, **kw):
    """Queue a frame and request its transmission. With stream=False only
    the first 8 words are queued, for a frame expected to lose arbitration."""
    words = can.node_tx_words(ident, bytes(data), **kw)
    await chip.push(0, words[:8])
    await chip.write(regs.FLAG_SET, 1 << 3)
    if stream and words[8:]:
        await chip.stream(0, words[8:], 0, depth=8)


async def node_frames(chip, n, timeout=600000):
    """The first n frames of closed can_line records since setup."""
    words, waited = chip.can_words, 0
    while True:
        _, rx = await chip.levels(1)
        if rx:
            words += await chip.pop(1, rx)
        frames, _ = can.raw_frames(words)
        if len(frames) >= n:
            return frames
        await chip.cycles(200)
        waited += 200
        if waited > timeout:
            raise TimeoutError(f"got {frames}: {[hex(w) for w in words]}")


def frame(tx, acked=True, crc_ok=True):
    """The can.Frame the chip records for a Tx."""
    dlc = len(tx.data) if tx.dlc is None else tx.dlc
    return can.Frame(tx.ident, tx.data, crc_ok, acked, tx.ext, tx.rtr, dlc)


# Standard, extended and remote frames, and frames whose CRC ends with five
# equal bits, so that a stuff bit (dominant, then recessive) comes before
# the CRC delimiter and moves the acknowledgement slot.
NODE_FRAMES = [
    Tx(0x123, b"\x11\x22"),
    Tx(0x104),
    Tx(0x114),
    Tx(0x1000028, b"\x55", ext=True),
    Tx(0x1ABCDEF, bytes(range(0xF8, 0x100)), ext=True),
    Tx(0x7FF, rtr=True, dlc=3),
    Tx(0x0AB, ext=True, rtr=True, dlc=15),
    Tx(0x000, b"\xff" * 8, dlc=12),
]


async def node_acks(dut, bit_ns=BIT_NS):
    peer = CanNode(bit_ns)
    chip = await setup_node(dut, [peer])
    for tx in NODE_FRAMES:
        peer.pending.append(tx)
    frames = await node_frames(chip, len(NODE_FRAMES))
    assert frames == [frame(tx) for tx in NODE_FRAMES], frames
    assert peer.sent == [(tx.ident, True) for tx in NODE_FRAMES], peer.sent
    assert not peer.violations, peer.violations[:5]


@cocotb.test()
async def test_can_node_acks(dut):
    """A node transmitting alone is acknowledged by the chip."""
    await node_acks(dut)


@cocotb.test()
async def test_can_node_acks_clock_offset(dut):
    """...by a transmitter 1.5% slow and one 1.5% fast."""
    await node_acks(dut, BIT_NS * 1.015)


@cocotb.test()
async def test_can_node_acks_clock_offset_fast(dut):
    await node_acks(dut, BIT_NS * 0.985)


@cocotb.test()
async def test_can_node_bad_crc_not_acked(dut):
    """A frame with a wrong CRC is not acknowledged; the next one is."""
    peer = CanNode()
    chip = await setup_node(dut, [peer])
    bad = [Tx(0x321, b"\x01\x02", bad_crc=True), Tx(0x1234, b"", ext=True, bad_crc=True)]
    good = Tx(0x322, b"\x03")
    peer.pending += bad + [good]
    frames = await node_frames(chip, 3)
    assert frames == [frame(t, acked=False, crc_ok=False) for t in bad] + [frame(good)], frames
    assert peer.sent == [(0x321, False), (0x1234, False), (0x322, True)], peer.sent


async def transmit_session(dut, hz=PROFILE):
    """The chip sends frames, which the peer checks and acknowledges, then
    acknowledges one from the peer."""
    peer = CanNode()
    chip = await setup_node(dut, [peer], hz)
    sent = [Tx(0x456, b"\x11\x22"), Tx(0x1000028, b"\x55", ext=True), Tx(0x104),
            Tx(0x7FF, rtr=True, dlc=8)]
    for tx in sent:
        await node_send(chip, tx.ident, tx.data, ext=tx.ext, rtr=tx.rtr, dlc=tx.dlc)
        frames = await node_frames(chip, sent.index(tx) + 1)
        assert not peer.violations, peer.violations[:5]
    assert frames == [frame(tx) for tx in sent], frames
    assert peer.received == [Rx(t.ident, t.data, True, t.ext, t.rtr, frame(t).dlc)
                             for t in sent], peer.received
    assert not (await chip.read8(regs.FLAGS)) & 0x48, "flags 3 and 6 clear when done"
    peer.send(0x123, b"\xa5")
    frames = await node_frames(chip, len(sent) + 1)
    assert frames[-1] == frame(Tx(0x123, b"\xa5")), frames
    assert peer.sent == [(0x123, True)]


@cocotb.test()
async def test_can_node_transmit(dut):
    await transmit_session(dut)


@cocotb.test()
async def test_can_node_wrong_clock(dut):
    """At 40 MHz the 50 MHz programs send 1.25 us bits, whose edges the
    peer finds outside its resynchronization jump width."""
    msg = await fails_at_wrong_clock(lambda hz: transmit_session(dut, hz), PROFILE)
    assert "timing: CAN edge" in msg, msg


@cocotb.test()
async def test_can_node_arbitration(dut):
    """Both start together; the lower identifier wins. The chip continues
    as a receiver and acknowledges the winner, then the Host clears the
    rest of the lost frame and sends it again."""
    peer = CanNode()
    chip = await setup_node(dut, [peer])
    peer.send(0x100, b"\x99", join=True)  # joins on the chip's start of frame
    await node_send(chip, 0x400, b"\x01", stream=False)
    frames = await node_frames(chip, 1)
    assert frames == [frame(Tx(0x100, b"\x99"))], frames
    assert peer.sent == [(0x100, True)], peer.sent
    assert peer.lost == []
    assert not (await chip.read8(regs.FLAGS)) & 0x40, "flag 6 clears on the loss"
    await chip.write(regs.CMD, regs.CMD_CLEAR0)
    await node_send(chip, 0x400, b"\x01")
    frames = await node_frames(chip, 2)
    assert frames[1] == frame(Tx(0x400, b"\x01")), frames
    assert peer.received == [Rx(0x400, b"\x01", True, False, False, 1)], peer.received
    assert not peer.violations, peer.violations[:5]


@cocotb.test()
async def test_can_node_wins_arbitration(dut):
    """The chip's lower identifier wins against an extended frame with the
    same base identifier; the peer receives it, then sends its own."""
    peer = CanNode()
    chip = await setup_node(dut, [peer])
    peer.send(0x123 << 18 | 0x5, b"\x01", ext=True, join=True)
    await node_send(chip, 0x123, b"\x02")
    frames = await node_frames(chip, 2)
    assert frames == [frame(Tx(0x123, b"\x02")), frame(Tx(0x123 << 18 | 0x5, b"\x01", ext=True))], frames
    assert peer.lost == [0x123 << 18 | 0x5]
    assert peer.sent == [(0x123 << 18 | 0x5, True)], peer.sent
    assert not peer.violations, peer.violations[:5]

