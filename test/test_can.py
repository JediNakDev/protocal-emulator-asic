# SPDX-License-Identifier: Apache-2.0
"""CAN receive and transmit programs against an independent CAN node on a
wired-AND bus with transceiver delay. The bus and the node keep their own
timing in ns, whatever the chip clock."""

from collections import deque

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


class CanNode:
    """A CAN 2.0A node: transmits with arbitration, receives, and
    acknowledges good frames. It hard-synchronizes on a start of frame and,
    while receiving, resynchronizes on recessive-to-dominant edges by up to
    SJW_NS; an edge further than that from its bit boundary is a timing
    violation. Its bit time may differ from the nominal one."""

    def __init__(self, bit_ns=BIT_NS):
        self.bit = bit_ns
        self.sample = SAMPLE * bit_ns
        self.tx = 1
        self.pending = []      # (id, data, join_only)
        self.received = []     # (id, data, crc_ok)
        self.sent = []         # (id, acked)
        self.lost = []
        self.violations = []
        self.bus = None

    def attach(self, bus):
        self.bus = bus
        bus.nodes.append(self)
        cocotb.start_soon(self._run())

    def _drive(self, level):
        if level != self.tx:
            self.tx = level
            self.bus.update()

    def _frame_bits(self, ident, data):
        b = [0] + [(ident >> (10 - i)) & 1 for i in range(11)] + [0, 0, 0]
        b += [(len(data) >> (3 - i)) & 1 for i in range(4)]
        for byte in data:
            b += [(byte >> (7 - i)) & 1 for i in range(8)]
        c = node_crc15(b)
        b += [(c >> (14 - i)) & 1 for i in range(15)]
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
        self.raw = []
        self.data_bits = []
        self.run, self.last = 0, None
        self.stuffed = True
        self.crc_end = None
        self.ack_drive = False
        self.transmitting = False
        self.done = False
        if transmit:
            ident, data, _ = self.pending.pop(0)
            self.tx_id = ident
            self.tx_bits = self._frame_bits(ident, data)
            self.transmitting = True

    def _boundary(self, k):
        if self.transmitting and k < len(self.tx_bits):
            self._drive(self.tx_bits[k])
        elif self.ack_drive and self.crc_end is not None and k == self.crc_end + 2:
            self._drive(0)
        else:
            self._drive(1)

    def _sample(self, k, b):
        if self.transmitting and k <= 12 and self.tx_bits[k] == 1 and b == 0:
            self.transmitting = False  # lost arbitration: become a receiver
            self.lost.append(self.tx_id)
        if self.stuffed:
            if self.run == 5:
                self.run, self.last = 1, b  # stuff bit
                return
            self.run = self.run + 1 if b == self.last else 1
            self.last = b
            self.data_bits.append(b)
            n = len(self.data_bits)
            if n >= 19:
                dlc = min(sum(self.data_bits[15 + i] << (3 - i) for i in range(4)), 8)
                if n == 19 + 8 * dlc + 15:
                    self.stuffed = False
                    self.crc_end = k
                    bits = self.data_bits
                    ok = node_crc15(bits) == 0
                    ident = sum(bits[1 + i] << (10 - i) for i in range(11))
                    data = bytes(sum(bits[19 + 8 * j + i] << (7 - i) for i in range(8))
                                 for j in range(dlc))
                    if not self.transmitting:
                        self.received.append((ident, data, ok))
                        self.ack_drive = ok
            return
        if k == self.crc_end + 2 and self.transmitting:
            self.sent.append((self.tx_id, b == 0))
        if k == self.crc_end + 10:
            self.done = True

    async def _bit(self, k, s, may_sync):
        """Bit k, nominally starting at s: drive it at its boundary and sample
        it. Until the sample point, a recessive-to-dominant edge moves the
        boundary by the phase error, limited to SJW_NS. Returns the start."""
        line = self.bus.line
        driven = False
        while True:
            if not driven and now_ns() >= s:
                self._boundary(k)
                driven = True
            wake = s + self.sample if driven else s
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
                join = bool(self.pending) and self.pending[0][2]
                await self._frame(now_ns(), transmit=join)
                idle_since = now_ns()
                continue
            since = max(idle_since, line.last_edge(now_ns()) or idle_since)
            if self.pending and not self.pending[0][2] and now_ns() - since >= 11 * self.bit:
                await self._frame(now_ns(), transmit=True)
                idle_since = now_ns()
                continue
            await line.change(self.bit)


async def setup(dut, node, hz=PROFILE):
    chip = Chip(dut, hz)
    await chip.start()
    await chip.set_quad()
    chip.set_pin8(1)  # recessive, before the bus watches it
    tx = assemble(program_source("can_tx"))
    rx = assemble(program_source("can_rx"))
    await chip.load(tx, 0)
    await chip.load(rx, len(tx))
    await chip.setup_engine(0, tx, 0, shiftctrl=regs.AUTOPULL | regs.OUT_RIGHT, set_base=TXD,
                            in_base=RXD, jmp_pin=RXD, execcfg=tx.label("table"))
    await chip.setup_engine(1, rx, len(tx), shiftctrl=regs.AUTOPUSH | regs.OUT_RIGHT
                            | regs.fifo_mode(regs.FIFO_JOIN_RX), thresh=15,
                            in_base=RXD, jmp_pin=regs.PIN_G2_OUT1)
    await chip.write(regs.G2_ADDR, 0)
    await chip.write(regs.G2_DATA, g2lib.can_destuffer())
    await chip.write(regs.G2_IN0, [regs.G2_SRC_FEED_BIT, regs.G2_SRC_FEED_BIT])
    await chip.write(regs.G2_STATE, 0)
    await chip.write(regs.G2_CTRL, regs.G2_ENABLE | regs.G2_OWNER1 | regs.G2_STEP_FEED | regs.G2_EMIT)
    await chip.force_asm(0, "set pins, 1")
    await chip.pincfg(TXD, regs.DRIVE_ALWAYS)
    node.attach(CanBus(chip))
    await chip.enable(3)
    chip.can_tx_prog = tx
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
    chip = await setup(dut, node)
    node.pending.append((0x123, bytes([0x00, 0x00, 0xFF, 0xFF, 0xA5]), False))
    node.pending.append((0x7FF, bytes([0x55] * 8), False))
    frames = await receive_frames(chip, 2)
    assert frames[0] == (0x123, bytes([0x00, 0x00, 0xFF, 0xFF, 0xA5]), True), frames
    assert frames[1] == (0x7FF, bytes([0x55] * 8), True), frames


@cocotb.test()
async def test_can_receive_separator_payload(dut):
    """Destuffed all-ones words must remain payload, across frame boundaries."""
    node = CanNode()
    chip = await setup(dut, node)
    expected = [(0x123, b"\xff" * 8, True),
                (0x456, b"\xff\xff\xff\x00\xff\xff\xff\xff", True),
                (0x7FF, b"", True)]
    for ident, payload, _ in expected:
        node.pending.append((ident, payload, False))
    assert await receive_frames(chip, len(expected)) == expected


async def transmit_session(dut, hz=PROFILE):
    """The chip sends a frame, which the node checks and acknowledges, then
    receives one from the node."""
    node = CanNode()
    chip = await setup(dut, node, hz)
    words = can.tx_words(0x456, b"\x11\x22")
    await chip.push(0, words[:4])
    rx = await chip.stream(0, words[4:], 1) if len(words) > 4 else await chip.pop_wait(0, 1)
    assert not node.violations, node.violations[:5]
    assert node.received == [(0x456, b"\x11\x22", True)], node.received
    assert rx == [0], f"ACK slot level {rx}"
    own = await receive_frames(chip, 1)
    assert own[0] == (0x456, b"\x11\x22", True)
    node.pending.append((0x123, b"\xa5", False))
    frames = await receive_frames(chip, 1)
    assert frames == [(0x123, b"\xa5", True)], frames


@cocotb.test()
async def test_can_transmit_acked(dut):
    await transmit_session(dut)


@cocotb.test()
async def test_can_wrong_clock(dut):
    """At 40 MHz the 50 MHz programs send 1.25 us bits, whose edges the
    node finds outside its resynchronization jump width."""
    msg = await fails_at_wrong_clock(lambda hz: transmit_session(dut, hz), PROFILE)
    assert "timing: CAN edge" in msg, msg


@cocotb.test()
async def test_can_arbitration(dut):
    """Both start together; the lower identifier wins. The chip stops,
    receives the winner's frame, then retransmits."""
    node = CanNode()
    chip = await setup(dut, node)
    node.pending.append((0x100, b"\x99", True))  # joins on the chip's start of frame
    words = can.tx_words(0x400, b"\x01")
    await chip.push(0, words[:4])
    for _ in range(200):
        if (await chip.read8(regs.FLAGS)) & 1:
            break
        await chip.cycles(100)
    assert (await chip.read8(regs.FLAGS)) & 1, "the chip must detect lost arbitration"
    frames = await receive_frames(chip, 1)
    assert frames[0] == (0x100, b"\x99", True)
    assert node.sent == [(0x100, False)]  # the chip is listen-only: no ACK
    assert not node.violations, node.violations[:5]
    # Recover and retransmit.
    await chip.write(regs.CMD, regs.CMD_CLEAR0 | regs.CMD_RESTART0)
    await chip.force(0, chip.can_tx_prog.label("next"))
    await chip.write(regs.FLAGS, 0x01)
    rx = await chip.stream(0, words, 1)
    assert rx == [0]
    assert node.received == [(0x400, b"\x01", True)]
    assert not node.violations, node.violations[:5]


# The second frame has the longest stretches between recessive-to-dominant
# edges that stuffing allows: eight times five dominant then five recessive
# bits, ten bits without an edge to resynchronize on.
OFFSET_PAYLOADS = [
    (0x555, bytes([0x0F, 0xF0, 0x3C, 0xC3, 0x00, 0xFF, 0x81, 0x7E])),
    (0x000, bytes([0x3C] * 8)),
]


async def receive_with_offset(dut, bit_ns):
    node = CanNode(bit_ns)
    chip = await setup(dut, node)
    for ident, data in OFFSET_PAYLOADS:
        node.pending.append((ident, data, False))
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
