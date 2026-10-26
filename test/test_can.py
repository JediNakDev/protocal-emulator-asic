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
from peers import Wave, now_ns, sleep_ns, sleep_until

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
        self.level = 1          # the delayed bus without a fault
        self.stuck = 0          # a fault: the bus reads recessive
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
            self.level = level
            self.chip.set_pin8(level | self.stuck)

    def hold_recessive(self, on):
        """A fault, as of a failing transceiver: every node reads the bus
        recessive, whatever is driven, while it lasts."""
        self.stuck = int(on)
        self.chip.set_pin8(self.level | self.stuck)


Tx = namedtuple("Tx", "ident data join ext rtr dlc bad_crc error_at flag_bits at_bit3 overload eof7"
                " hit", defaults=(b"", False, False, False, None, False, None, 6, False, False,
                                  False, ()))
Tx.__doc__ = """A frame for CanNode to send. Faults: a wrong CRC (bad_crc); an
active error flag of flag_bits dominant bits from stuffed bit error_at
instead of the rest of the frame; an overload flag in intermission bit 1
after it (overload); a dominant end of frame bit 7 (eof7); dominant bits
at the bit indices `hit`, counted from the start of frame. join: start
transmitting on a start of frame seen on the bus; at_bit3: start of frame in
intermission bit 3 after the frame on the bus, else after 11 idle bits."""
Rx = namedtuple("Rx", "ident data ok ext rtr dlc")


def bits_of(value, n):
    return [(value >> (n - 1 - i)) & 1 for i in range(n)]


def value_of(bits):
    v = 0
    for b in bits:
        v = v << 1 | b
    return v


class CanNode:
    """An error-active CAN 2.0B node (ISO 11898-1): transmits with
    arbitration, receives, and acknowledges good frames. On a stuff, form,
    CRC, ACK or bit error it sends an active error flag (recorded in
    `errors`), then the error delimiter and intermission; a dominant bit in
    intermission bits 1-2, error delimiter bit 8 or (receiving) end of frame
    bit 7 is an overload condition (recorded in `overloads`), answered with
    an overload flag. It hard-synchronizes on a start of frame and, while
    receiving, resynchronizes on recessive-to-dominant edges by up to
    SJW_NS; an edge further than that from its bit boundary is a timing
    violation. Its bit time may differ from the nominal one. A frame that
    loses arbitration is sent again; one that fails otherwise is not. With
    `ack` false it never acknowledges; `corrupt` holds, for each frame it
    receives in turn, the stuffed bit indices it overwrites dominant."""

    def __init__(self, bit_ns=BIT_NS):
        self.bit = bit_ns
        self.sample = SAMPLE * bit_ns
        self.tx = 1
        self.ack = True
        self.corrupt = deque()
        self.pending = []      # Tx
        self.received = []     # Rx
        self.sent = []         # (ident, acked)
        self.lost = []
        self.errors = []       # (kind, bit): stuff, form, crc, ack, bit
        self.overloads = []    # bits with an overload condition
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
    def frame_bits(tx):
        """The bits a node sends for `tx` on the bus, stuffed, from the start
        of frame to the end of the CRC."""
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
        return out

    def _begin(self, transmit):
        self.data_bits = []
        self.run, self.last = 0, None
        self.phase = "frame"   # frame, tail, flag, delim, inter
        self.need = None
        self.crc_end = None
        self.crc_ok = True
        self.transmitting = False
        self.done = False
        self.hit = set()
        if transmit:
            self.tx_frame = self.pending.pop(0)
            self.tx_bits = self.frame_bits(self.tx_frame)
            self.arb_end = 32 if self.tx_frame.ext else 12
            self.transmitting = True
            self.hit = set(self.tx_frame.hit)
        elif self.corrupt:
            self.hit = set(self.corrupt.popleft())

    def _flag(self, start, n=6):
        """An active error or overload flag in bits start..start + n - 1."""
        self.phase = "flag"
        self.flag = (start, start + n - 1)

    def _error(self, kind, k, start=None):
        self.errors.append((kind, k))
        self._flag(k + 1 if start is None else start)

    def _boundary(self, k):
        level = 1
        if self.phase == "frame" and self.transmitting:
            err = self.tx_frame.error_at
            if err is not None and k == err:
                self._flag(k, self.tx_frame.flag_bits)
            else:
                level = self.tx_bits[k]
        if self.phase == "flag":
            level = int(not self.flag[0] <= k <= self.flag[1])
        elif self.phase == "tail":
            if k == self.crc_end + 2 and not self.transmitting and self.crc_ok and self.ack:
                level = 0
            if k == self.crc_end + 10 and self.transmitting and self.tx_frame.eof7:
                level = 0
        if k in self.hit:
            level = 0
        self._drive(level)

    def _sample(self, k, b):
        getattr(self, "_sample_" + self.phase)(k, b)

    def _sample_frame(self, k, b):
        if self.transmitting and self.tx_bits[k] != b and k not in self.hit:
            self.transmitting = False  # become a receiver
            if self.tx_bits[k] and len(self.data_bits) <= self.arb_end:
                self.lost.append(self.tx_frame.ident)
                self.pending.insert(0, self.tx_frame._replace(join=False, at_bit3=False))
            else:
                self._error("bit", k)
                return
        if self.run == 5:
            if b == self.last:
                self._error("stuff", k)
                return
            self.run, self.last = 1, b  # stuff bit
            if self.need is not None and len(self.data_bits) == self.need:
                self.phase = "tail"
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

    def _sample_tail(self, k, b):
        j = k - self.crc_end   # 1 CRC delimiter, 2 ACK slot, 3 ACK delimiter, 4-10 end of frame
        mine = k in self.hit or (j == 10 and self.transmitting and self.tx_frame.eof7)
        if j == 2:
            if self.transmitting:
                self.sent.append((self.tx_frame.ident, b == 0))
                if b:
                    self._error("ack", k)
            return
        if j == 10 and not mine and b == 0 and not self.transmitting:
            self.overloads.append(k)
            self._flag(k + 1)
            return
        if b == 0 and not mine:
            self._error("form", k)
            return
        if j == 3 and not self.transmitting and not self.crc_ok:
            self._error("crc", k)
            return
        if j == 10:
            self._inter(2)
            if self.transmitting and self.tx_frame.overload:
                self._flag(k + 1)

    def _sample_flag(self, k, b):
        if k == self.flag[1]:
            self.phase = "delim"
            self.delim = 0

    def _sample_delim(self, k, b):
        if b:
            self.delim += 1
            if self.delim == 7:
                self._inter(3)  # delimiter bit 8, intermission bits 1-2
        elif self.delim:
            self._error("form", k)

    def _inter(self, n):
        self.phase = "inter"
        self.inter_left = n

    def _sample_inter(self, k, b):
        if b == 0 and k not in self.hit:
            self.overloads.append(k)
            self._flag(k + 1)
            return
        self.inter_left -= 1
        self.done = self.inter_left == 0

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
        self.crc_ok = node_crc15(bits) == 0
        if self.run < 5:  # else a stuff bit follows the CRC
            self.phase = "tail"
            self.crc_end = k
        if not self.transmitting:
            self.received.append(Rx(ident, data, self.crc_ok, bool(ext), bool(rtr), dlc))

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
        """One frame and what follows it, to intermission bit 2. Returns the
        start of intermission bit 3."""
        self._begin(transmit)
        s, k, prev = t0, 0, 0
        while not self.done:
            may_sync = (k > 0 and prev == 1 and self.phase == "frame"
                        and not self.transmitting)
            s = await self._bit(k, s, may_sync)
            prev = self.bus.line.level
            s += self.bit
            k += 1
        self._drive(1)
        return s

    async def _run(self):
        line = self.bus.line
        idle_since = now_ns()
        while True:
            since = max(idle_since, line.last_edge(now_ns()) or idle_since)
            if line.level == 0:
                join = bool(self.pending) and self.pending[0].join
                bit3 = await self._frame(now_ns(), transmit=join)
            elif self.pending and not self.pending[0].join and now_ns() - since >= 11 * self.bit:
                bit3 = await self._frame(now_ns(), transmit=True)
            else:
                await line.change(self.bit)
                continue
            while self.pending and self.pending[0].at_bit3:
                await sleep_until(bit3)
                bit3 = await self._frame(now_ns(), transmit=True)
            idle_since = now_ns()


async def setup_listen(dut, node, hz=PROFILE):
    """can_rx alone on engine 1: a listen-only receiver that never drives
    the bus (TXD stays released, pulled recessive), on a bus where a second
    node acknowledges `node`'s frames."""
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
    await chip.write(regs.G2_CTRL, regs.G2_ENABLE | regs.G2_OWNER1 | regs.G2_STEP_FEED
                     | regs.G2_EMIT)
    bus = CanBus(chip)
    node.attach(bus)
    CanNode(node.bit).attach(bus)  # acknowledges
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
    oe = Wave(dut.uio_oe, TXD)
    node.send(0x123, [0x00, 0x00, 0xFF, 0xFF, 0xA5])
    node.send(0x7FF, [0x55] * 8)
    frames = await receive_frames(chip, 2)
    assert frames[0] == (0x123, bytes([0x00, 0x00, 0xFF, 0xFF, 0xA5]), True), frames
    assert frames[1] == (0x7FF, bytes([0x55] * 8), True), frames
    assert node.sent == [(0x123, True), (0x7FF, True)]  # by the other node
    assert oe.initial == 0 and not oe.times, "listen-only: TXD never driven"


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


async def receive_with_offset(dut, bit_ns, hz=PROFILE):
    node = CanNode(bit_ns)
    chip = await setup_listen(dut, node, hz)
    for ident, data in OFFSET_PAYLOADS:
        node.send(ident, data)
    frames = await receive_frames(chip, len(OFFSET_PAYLOADS))
    assert frames == [(i, d, True) for i, d in OFFSET_PAYLOADS], f"received frames {frames}"


@cocotb.test()
async def test_can_receive_clock_offset(dut):
    """Resynchronization on every recessive-to-dominant edge: the transmitter
    1.5% slow (CAN allows up to 1.58% between two nodes)."""
    await receive_with_offset(dut, BIT_NS * 1.015)


@cocotb.test()
async def test_can_receive_clock_offset_fast(dut):
    """...and 1.5% fast."""
    await receive_with_offset(dut, BIT_NS * 0.985)


@cocotb.test()
async def test_can_receive_wrong_clock(dut):
    """At 40 MHz the 50 MHz receiver samples every 1.25 us, so across the
    stretches without an edge it skips bits of the 1 Mbit/s frames."""
    msg = await fails_at_wrong_clock(lambda hz: receive_with_offset(dut, BIT_NS, hz), PROFILE)
    assert "received frames" in msg or "got" in msg, msg


# ------------------------------------------------------------------ node


async def setup_node(dut, nodes, hz=PROFILE, early=(), early_ns=0):
    """can_node on engine 0 and can_line on engine 1: a node with error
    confinement. Peers attach once it has integrated into the idle bus; the
    `early` ones attach early_ns before the chip starts."""
    chip = Chip(dut, hz)
    await chip.start()
    await chip.set_quad()
    chip.set_pin8(1)
    node = assemble(program_source("can_node"))
    line = assemble(program_source("can_line"))
    assert len(node) + len(line) <= 512 and len(node) // 64 == (len(node) + len(line) - 1) // 64
    await chip.load(node, 0)
    await chip.load(line, len(node))
    await chip.setup_engine(0, node, 0, shiftctrl=regs.AUTOPULL | regs.OUT_RIGHT
                            | regs.fifo_mode(regs.FIFO_TX_RAM), thresh=0xF0,
                            side_base=TXD, jmp_pin=regs.PIN_G2_OUT1,
                            execcfg=node.label("tbl"), statuscfg=1)
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
    await chip.force(1, line.label("start", len(node)))
    await chip.write(regs.FLAGS, 0xFF)
    await chip.ereg(0, regs.RAM_ADDR, 4)
    await chip.write16(regs.engine_reg(0, regs.RAM_DATA), [0])  # bus integration
    await chip.force_asm(0, "nop side 1", side_count=1, side_opt=True)
    await chip.force_asm(0, f"set page, {node.address('bol') // 64}")
    await chip.force(0, node.label("bol"))
    await chip.pincfg(TXD, regs.DRIVE_ALWAYS | regs.OWNER_E0)
    bus = CanBus(chip)
    chip.can_bus = bus
    for n in early:
        n.attach(bus)
    await sleep_ns(early_ns)
    chip.started_ns = now_ns()
    await chip.enable(3)
    await sleep_ns(12 * BIT_NS)   # bus integration: 11 recessive bits
    for n in nodes:
        n.attach(bus)
    chip.can_words = []
    return chip


async def node_send(chip, ident, data=b"", stream=True, timeout=2_000_000, **kw):
    """Clear the transmit queue, queue a frame and request its transmission,
    then (stream) feed the rest of it. Returns False if the attempt ends
    before the frame is all queued. With stream=False only the first 4
    words are queued, for a frame expected to lose arbitration."""
    words = can.node_tx_words(ident, bytes(data), **kw)
    await chip.write(regs.CMD, regs.CMD_CLEAR0)
    await chip.push(0, words[:4])
    await chip.write(regs.FLAG_SET, 1 << 3)
    i, waited = 4, 0
    while stream and i < len(words):
        level, _ = await chip.levels(0)
        if level < 4:
            await chip.push(0, words[i:i + 4 - level])
            i += 4 - level
            continue
        if not (await chip.read8(regs.FLAGS)) & 0x48:  # not requested, not sending
            return False
        await drain(chip)
        await chip.cycles(20)
        waited += 20
        if waited > timeout:
            raise TimeoutError(f"queued {i} of {len(words)} words")
    return True


async def node_counters(chip):
    """(REC, TEC) of the chip's node; TEC is None while bus-off."""
    await chip.ereg(0, regs.RAM_ADDR, can.NODE_REC)
    rec, tec = await chip.read16(regs.engine_reg(0, regs.RAM_DATA), 2)
    return can.node_counter(rec), can.node_counter(tec)


async def drain(chip):
    """Keep can_line's records: its receive queue holds 8 words."""
    _, rx = await chip.levels(1)
    if rx:
        chip.can_words += await chip.pop(1, rx)


async def node_frames(chip, n, timeout=600000):
    """The first n frames of closed can_line records since setup."""
    words, waited = chip.can_words, 0
    while True:
        await drain(chip)
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


# With a clock offset, also the frames with the longest stretches between
# resynchronizing edges.
OFFSET_FRAMES = NODE_FRAMES + [Tx(i, d) for i, d in OFFSET_PAYLOADS]


async def node_acks(dut, bit_ns=BIT_NS, frames=NODE_FRAMES):
    peer = CanNode(bit_ns)
    chip = await setup_node(dut, [peer])
    for tx in frames:
        peer.pending.append(tx)
    got = await node_frames(chip, len(frames))
    assert got == [frame(tx) for tx in frames], got
    assert peer.sent == [(tx.ident, True) for tx in frames], peer.sent
    assert not peer.errors and not peer.violations, (peer.errors, peer.violations[:5])
    assert await node_counters(chip) == (0, 0)


@cocotb.test()
async def test_can_node_acks(dut):
    """A node transmitting alone is acknowledged by the chip."""
    await node_acks(dut)


@cocotb.test()
async def test_can_node_acks_clock_offset(dut):
    """...by a transmitter 1.5% slow, also after ten bits without an edge to
    resynchronize on..."""
    await node_acks(dut, BIT_NS * 1.015, OFFSET_FRAMES)


@cocotb.test()
async def test_can_node_acks_clock_offset_fast(dut):
    """...and one 1.5% fast."""
    await node_acks(dut, BIT_NS * 0.985, OFFSET_FRAMES)


@cocotb.test()
async def test_can_node_bad_crc_not_acked(dut):
    """A frame with a wrong CRC is not acknowledged: the transmitter flags
    the ACK error at the ACK delimiter and the chip the CRC error after it,
    counting it (REC + 1). The next frame is acknowledged (REC - 1)."""
    peer = CanNode()
    chip = await setup_node(dut, [peer])
    bad = [Tx(0x321, b"\x01\x02", bad_crc=True), Tx(0x1234, b"", ext=True, bad_crc=True)]
    good = Tx(0x322, b"\x03")
    peer.pending += bad + [good]
    frames = await node_frames(chip, 5)
    want = [frame(bad[0], acked=False, crc_ok=False), can.FLAG,
            frame(bad[1], acked=False, crc_ok=False), can.FLAG, frame(good)]
    assert frames == want, frames
    assert peer.sent == [(0x321, False), (0x1234, False), (0x322, True)], peer.sent
    assert [e for e, _ in peer.errors] == ["ack", "ack"], peer.errors
    assert not peer.violations, peer.violations[:5]
    assert await node_counters(chip) == (1, 0)


@cocotb.test()
async def test_can_node_error_recovery(dut):
    """A frame broken off by an error flag, in the identifier (page 0), in
    the data (page 1) or in place of the stuff bit after a CRC that is
    right so far, is not acknowledged; the chip returns to idle and
    acknowledges the next frame, which begins with a recessive bit."""
    peer = CanNode()
    chip = await setup_node(dut, [peer])
    stuff_after_crc = len(CanNode.frame_bits(Tx(0x104))) - 1
    broken = [Tx(0x2AA, b"\x12\x34", error_at=6), Tx(0x2AB, b"\x5a" * 8, error_at=40),
              Tx(0x104, error_at=stuff_after_crc)]
    good = Tx(0x4AC, b"\x56")
    peer.pending += broken + [good]
    frames = await node_frames(chip, 4)
    assert frames == [None, None, None, frame(good)], frames
    assert peer.sent == [(0x4AC, True)], peer.sent
    assert not peer.errors, peer.errors
    assert not peer.violations, peer.violations[:5]
    assert not (await chip.read8(regs.FLAGS)) & 0x40, "flag 6 clear"
    assert await node_counters(chip) == (2, 0), "3 stuff errors, 1 frame received"


async def transmit_session(dut, hz=PROFILE, bit_ns=BIT_NS):
    """The chip sends frames, which the peer checks and acknowledges, then
    acknowledges one from the peer."""
    peer = CanNode(bit_ns)
    chip = await setup_node(dut, [peer], hz)
    sent = [Tx(0x456, b"\x11\x22"), Tx(0x1000028, b"\x55", ext=True), Tx(0x104),
            Tx(0x7FF, rtr=True, dlc=8)]
    for n, tx in enumerate(sent, 1):
        ok = await node_send(chip, tx.ident, tx.data, ext=tx.ext, rtr=tx.rtr, dlc=tx.dlc)
        assert not peer.violations, peer.violations[:5]
        assert ok, f"sending {tx.ident:#x} failed: {peer.errors}"
        frames = await node_frames(chip, n)
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
async def test_can_node_transmit_clock_offset(dut):
    """...to and from a node 1.5% slow..."""
    await transmit_session(dut, bit_ns=BIT_NS * 1.015)


@cocotb.test()
async def test_can_node_transmit_clock_offset_fast(dut):
    """...and one 1.5% fast."""
    await transmit_session(dut, bit_ns=BIT_NS * 0.985)


@cocotb.test()
async def test_can_node_transmit_host_pause(dut):
    """The 4-word transmit queue holds 20 bits: a Host that queues the first
    4 words of a long frame and then pauses for 14 us loses nothing."""
    peer = CanNode()
    chip = await setup_node(dut, [peer])
    tx = Tx(0x1ABCDEF, bytes(range(0xF8, 0x100)), ext=True)
    words = can.node_tx_words(tx.ident, tx.data, ext=True)
    assert len(words) > 16, len(words)
    await chip.write(regs.CMD, regs.CMD_CLEAR0)
    await chip.push(0, words[:4])
    await chip.write(regs.FLAG_SET, 1 << 3)
    await sleep_ns(14_000)
    await chip.stream(0, words[4:], 0, depth=4)
    assert await node_frames(chip, 1) == [frame(tx)]
    assert peer.received == [Rx(tx.ident, tx.data, True, True, False, 8)], peer.received
    assert not peer.violations, peer.violations[:5]
    assert not (await chip.read8(regs.FLAGS)) & 0x10, "flag 4 clears on success"


@cocotb.test()
async def test_can_node_transmit_underrun(dut):
    """Exhaust a valid frame prefix ending dominant, then stop Host service.
    The transmitter must release TXD instead of stalling on its next pull,
    and report the failure in flag 4 (B14)."""
    chip = await setup_node(dut, [])
    txd = Wave(dut.uio_out, TXD)
    words = can.node_tx_words(0x123, bytes(range(8)))
    assert (words[3] >> 12) & 7 == can.NODE_D
    await chip.write(regs.CMD, regs.CMD_CLEAR0)
    await chip.push(0, words[:4])
    await chip.write(regs.FLAG_SET, 1 << 3)
    await sleep_ns(60_000)
    assert int(dut.uio_out.value) & 1, "transmit underrun holds TXD dominant"
    # SOF plus 20 supplied bits; allow the transceiver loop delay and the
    # instruction/synchronizer latency, but no extra complete bit held low.
    # Then the node, now receiving, finds six recessive bits (a stuff error)
    # and sends an error flag.
    t = txd.times
    release = next(t[j] for j in range(1, len(t)) if txd.levels[j]
                   and (j + 1 == len(t) or t[j + 1] - t[j] > 5 * BIT_NS))
    assert release - t[0] < 22 * BIT_NS, t
    flags = await chip.read8(regs.FLAGS)
    assert flags & 0x10, "underrun flag 4 missing"
    assert not flags & 0x40, "transmit flag 6 remains set"
    await sleep_ns(20_000)
    assert int(dut.uio_out.value) & 1, "transmit underrun never returns to idle"
    # The same engines must accept another request without reset or reload.
    peer = CanNode()
    peer.attach(chip.can_bus)
    await node_send(chip, 0x456, b"\x12")
    await node_frames(chip, 2)
    assert peer.received == [Rx(0x456, b"\x12", True, False, False, 1)], peer.received
    assert not peer.violations, peer.violations
    assert not (await chip.read8(regs.FLAGS)) & 0x10, "flag 4 clears on success"


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
    assert (await chip.read8(regs.FLAGS)) & 0x10, "flag 4: not sent"
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
    ext = Tx(0x123 << 18 | 0x5, b"\x01", ext=True)
    assert frames == [frame(Tx(0x123, b"\x02")), frame(ext)], frames
    assert peer.lost == [0x123 << 18 | 0x5]
    assert peer.sent == [(0x123 << 18 | 0x5, True)], peer.sent
    assert not peer.violations, peer.violations[:5]


# ------------------------------------------------------- error confinement


def bus_index(tx, destuffed):
    """Index on the bus of destuffed bit `destuffed` (0 = start of frame) of
    the frame for tx."""
    n, run, last = -1, 0, None
    for k, b in enumerate(CanNode.frame_bits(tx)):
        if run == 5:
            run, last = 1, b
            continue
        run = run + 1 if b == last else 1
        last = b
        n += 1
        if n == destuffed:
            return k
    raise ValueError(destuffed)


def crc_end(tx):
    """Bus index of the last bit before the CRC delimiter."""
    return len(CanNode.frame_bits(tx)) - 1


async def node_attempt(chip, tx):
    """The chip sends tx and the attempt ends (flags 3 and 6 clear). Returns
    whether it succeeded (flag 4 clear)."""
    await node_send(chip, tx.ident, tx.data, ext=tx.ext, rtr=tx.rtr, dlc=tx.dlc)
    waited = 0
    while (flags := await chip.read8(regs.FLAGS)) & 0x48:
        await drain(chip)
        await chip.cycles(200)
        waited += 200
        if waited > 2_000_000:
            raise TimeoutError(f"attempt never ended: flags {flags:#x}")
    return not flags & 0x10


async def passive(chip):
    return bool((await chip.read8(regs.FLAGS)) & 0x20)


def dominant_runs(wave, t0):
    """Lengths in bits of the dominant periods of a wave after t0."""
    t, lv = wave.times, wave.levels
    return [round((t[j + 1] - t[j]) / BIT_NS, 1) for j in range(len(t) - 1)
            if lv[j] == 0 and t[j] >= t0]


@cocotb.test()
async def test_can_node_receive_error_counts(dut):
    """Receive errors (ISO 11898-1 rules 1, 2, 6, 8): an error flag of 20
    dominant bits cuts a frame; the chip finds the stuff error (+1), a
    dominant bit after its own flag (+8) and 8 of them (+8); 19 bits give
    +1 +8. A dominant CRC delimiter is a form error (+1), which the
    transmitter answers late (+8). A good frame counts -1. Past 127 the
    chip is error passive and its flags recessive; the next good frame sets
    REC to 127, error active again."""
    peer = CanNode()
    chip = await setup_node(dut, [peer])
    tx = Tx(0x2AA, b"\x12\x34")
    bits = CanNode.frame_bits(tx)
    e = next(k for k in range(20, len(bits)) if bits[k - 1])
    good = Tx(0x4AC, b"\x56")

    sent = 0

    async def after(t, rec):
        nonlocal sent
        sent += 1
        peer.pending.append(t)
        await node_frames(chip, sent)
        assert await node_counters(chip) == (rec, 0), (t, await node_counters(chip))

    await after(tx._replace(error_at=e, flag_bits=20), 17)
    await after(tx._replace(error_at=e, flag_bits=19), 26)
    await after(good, 25)
    form = Tx(0x321, b"\x01", hit=(crc_end(Tx(0x321, b"\x01")) + 1,))
    await after(form, 34)
    for rec in range(51, 51 + 17 * 6, 17):
        await after(tx._replace(error_at=e, flag_bits=20), rec)
    assert await passive(chip), "error passive at REC 136"
    flags_at = now_ns()
    txd = Wave(dut.uio_out, TXD)
    await after(tx._replace(error_at=e, flag_bits=20), 153)
    assert not [r for r in dominant_runs(txd, flags_at) if r > 1.5], "passive: no active flag"
    await after(good, 127)
    assert not await passive(chip), "error active at REC 127"
    assert not peer.violations, peer.violations[:5]
    assert [k for k, _ in peer.errors] == ["form"], peer.errors


@cocotb.test()
async def test_can_node_transmit_error_counts(dut):
    """Transmit errors: a bit error in the DLC is TEC + 8 and leaves flag 4
    set; a recessive stuff bit in the arbitration field overwritten
    dominant is a stuff error that does not count (exception 3b); the
    retransmission succeeds, TEC - 1."""
    peer = CanNode()
    chip = await setup_node(dut, [peer])
    tx = Tx(0x123, b"\x01")
    zero = Tx(0x000, b"\x01")
    assert CanNode.frame_bits(zero)[:6] == [0] * 5 + [1]  # stuff bit 5, before RTR
    peer.corrupt += [{bus_index(tx, 18)}, {5}]           # DLC bit 0 (recessive)
    assert not await node_attempt(chip, tx)
    assert await node_counters(chip) == (0, 8)
    assert not await node_attempt(chip, zero)
    assert await node_counters(chip) == (0, 8), "3b: no count"
    assert await node_attempt(chip, tx)
    assert await node_counters(chip) == (0, 7)
    assert peer.received[-1] == Rx(0x123, b"\x01", True, False, False, 1), peer.received
    assert [k for k, _ in peer.errors] == ["stuff", "stuff"], peer.errors
    assert not peer.violations, peer.violations[:5]


@cocotb.test()
async def test_can_node_error_passive_suspend(dut):
    """A node that nobody acknowledges: 16 ACK errors take TEC to 128 and
    the chip error passive. Its passive flag is recessive and, with no
    dominant bit during it, TEC stays 128 (exception 3a); a dominant bit
    during it counts (+8). Error passive, after a frame it sends it
    suspends transmission for 8 bits: a frame the peer starts in
    intermission bit 3 goes first, though the chip's identifier is lower."""
    peer = CanNode()
    peer.ack = False
    chip = await setup_node(dut, [peer])
    tx = Tx(0x0F0, b"\xc3")
    for n in range(1, 17):
        assert not await node_attempt(chip, tx)
        assert await node_counters(chip) == (0, 8 * n), n
        assert await passive(chip) == (n == 16), n
    t0 = now_ns()
    txd = Wave(dut.uio_out, TXD)
    assert not await node_attempt(chip, tx)
    assert await node_counters(chip) == (0, 128), "3a"
    assert max(dominant_runs(txd, t0)) < 5.5, "passive flag recessive"
    peer.corrupt.append({crc_end(tx) + 4})                # end of frame bit 1
    assert not await node_attempt(chip, tx)
    assert await node_counters(chip) == (0, 136), "3a: a dominant bit seen"
    peer.ack = True
    assert await node_attempt(chip, tx)
    assert await node_counters(chip) == (0, 135) and await passive(chip)
    before = len(await node_frames(chip, 0))
    b1, b2 = Tx(0x050, b"\x05"), Tx(0x001, b"\x01")
    assert await node_send(chip, b1.ident, b1.data)
    peer.send(0x7F0, b"\x07", at_bit3=True)              # after b1, in bit 3
    while (await chip.levels(0))[0]:
        await chip.cycles(50)
    assert await node_send(chip, b2.ident, b2.data)       # while b1 is on the bus
    frames = await node_frames(chip, before + 3)
    assert frames[before:] == [frame(b1), frame(Tx(0x7F0, b"\x07")), frame(b2)], frames[before:]
    assert peer.lost == [], peer.lost
    assert not peer.violations, peer.violations[:5]
    assert await node_counters(chip) == (0, 133)


@cocotb.test()
async def test_can_node_bus_off(dut):
    """32 bit errors take TEC past 255: the chip goes bus-off, TEC reads
    0xFFFF and TXD stays released although the Host requests a frame. It
    recovers after 128 occurrences of 11 recessive bits, error active with
    both counters 0, and sends the frame."""
    peer = CanNode()
    chip = await setup_node(dut, [peer])
    tx = Tx(0x123, b"\x01")
    peer.corrupt += [{bus_index(tx, 18)}] * 32
    for n in range(1, 33):
        assert not await node_attempt(chip, tx)
        rec, tec = await node_counters(chip)
        assert (rec, tec) == (0, 8 * n if n < 32 else None), (n, rec, tec)
    line = chip.can_bus.line
    good = Tx(0x456, b"\x12")
    assert await node_send(chip, good.ident, good.data)
    frames = await node_frames(chip, 33)
    assert frames[-1] == frame(good), frames[-1]
    t, lv = line.times, line.levels
    j = next(j for j in range(1, len(t)) if lv[j] == 0 and t[j] - t[j - 1] > 1000 * BIT_NS)
    gap = (t[j] - t[j - 1]) / BIT_NS  # from the last bus activity to the start of frame
    assert 128 * 11 <= gap <= 128 * 11 + 3, f"recovered after {gap:.1f} bits"
    assert await node_counters(chip) == (0, 0)
    assert not await passive(chip)
    assert peer.received[-1] == Rx(0x456, b"\x12", True, False, False, 1)
    assert not peer.violations, peer.violations[:5]


@cocotb.test()
async def test_can_node_overload(dut):
    """Overload conditions: a dominant intermission bit 1, end of frame bit
    7 while receiving, and error delimiter bit 8. The chip answers each with
    a 6-bit overload flag and counts nothing: the frame with a dominant end
    of frame bit 7 is received (REC 1 -> 0)."""
    peer = CanNode()
    chip = await setup_node(dut, [peer])
    txd = Wave(dut.uio_out, TXD)
    peer.send(0x101, b"\x01", overload=True)
    peer.send(0x102, b"\x02")
    frames = await node_frames(chip, 3)
    assert frames == [frame(Tx(0x101, b"\x01")), can.FLAG, frame(Tx(0x102, b"\x02"))], frames
    assert any(5.8 <= r <= 6.2 for r in dominant_runs(txd, 0)), dominant_runs(txd, 0)
    assert await node_counters(chip) == (0, 0)
    bad = Tx(0x321, b"\x01\x02", bad_crc=True)
    peer.pending += [bad, Tx(0x103, b"\x03", eof7=True)]
    frames = await node_frames(chip, 7)
    assert frames[3:] == [frame(bad, acked=False, crc_ok=False), can.FLAG,
                          frame(Tx(0x103, b"\x03")), can.FLAG], frames
    assert await node_counters(chip) == (0, 0)
    peer.pending.append(bad._replace(hit=(crc_end(bad) + 17,)))  # delimiter bit 8
    frames = await node_frames(chip, 10)
    assert frames[7:] == [frame(bad, acked=False, crc_ok=False), can.FLAG, can.FLAG], frames
    assert await node_counters(chip) == (1, 0)
    assert not peer.violations, peer.violations[:5]


@cocotb.test()
async def test_can_node_start_in_intermission_bit3(dut):
    """A start of frame in intermission bit 3: the chip receives the frame;
    with a frame of its own waiting it joins it as a start of frame and
    arbitrates, winning with the lower identifier."""
    peer = CanNode()
    chip = await setup_node(dut, [peer])
    peer.send(0x300, b"\x03")
    peer.send(0x301, b"\x04", at_bit3=True)
    frames = await node_frames(chip, 2)
    assert frames == [frame(Tx(0x300, b"\x03")), frame(Tx(0x301, b"\x04"))], frames
    assert peer.sent == [(0x300, True), (0x301, True)], peer.sent
    peer.send(0x200, b"\x02")
    peer.send(0x280, b"\x08", at_bit3=True)
    line = chip.can_bus.line
    while line.level:
        await chip.cycles(20)
    assert await node_send(chip, 0x100, b"\x01")          # while 0x200 is on the bus
    frames = await node_frames(chip, 5)
    assert frames[2:] == [frame(Tx(0x200, b"\x02")), frame(Tx(0x100, b"\x01")),
                          frame(Tx(0x280, b"\x08"))], frames
    assert peer.lost == [0x280], peer.lost
    assert not peer.errors and not peer.violations, (peer.errors, peer.violations[:5])


@cocotb.test()
async def test_can_node_bus_integration(dut):
    """Started in the data field of a frame, the chip waits for 11 recessive
    bits (the acknowledgement delimiter, end of frame and intermission)
    before it takes part: it neither acknowledges nor flags that frame,
    though five recessive stuffed bits look like idle, and it receives the
    frames after it."""
    sender, acker = CanNode(), CanNode()
    first = Tx(0x07F, b"\xff" * 8)
    sender.pending += [first, Tx(0x155, b"\x00" * 8), Tx(0x123, b"\x11\x22")]
    chip = await setup_node(dut, [], early=(sender, acker), early_ns=(11 + 40) * BIT_NS)
    start, line = chip.started_ns, chip.can_bus.line
    # The first frame is on the bus: edges in the 6 bits before the start
    # and more than 30 bits after it.
    assert line.edges(start - 6 * BIT_NS, start), line.edges()
    txd = Wave(dut.uio_out, TXD)
    frames = await node_frames(chip, 2)
    assert line.edges(start + 30 * BIT_NS, start + 40 * BIT_NS), line.edges()
    assert frames == [frame(Tx(0x155, b"\x00" * 8)), frame(Tx(0x123, b"\x11\x22"))], frames
    assert sender.sent == [(0x07F, True), (0x155, True), (0x123, True)], sender.sent
    assert dominant_runs(txd, 0) == [1.0, 1.0], dominant_runs(txd, 0)  # the two ACK bits
    assert await node_counters(chip) == (0, 0)
    for n in (sender, acker):
        assert not n.errors and not n.overloads and not n.violations, (n.errors, n.violations[:5])


async def recessive_at(bus, k):
    """Hold the bus recessive across the sample point of bit k of the next
    frame (counted from the start of frame, stuffed): 0.3 to 0.9 of it."""
    line = bus.line
    while line.level:
        await line.change()
    sof = line.last_edge(now_ns())
    await sleep_until(sof + (k + 0.3) * BIT_NS)
    bus.hold_recessive(True)
    await sleep_until(sof + (k + 0.9) * BIT_NS)
    bus.hold_recessive(False)


@cocotb.test()
async def test_can_node_bit_error_in_flag(dut):
    """Rules 4 and 5: a bit error while the chip sends an active error flag
    (the bus held recessive at its sample) counts +8 for its role, and a new
    error flag follows. As the transmitter: an ACK error (TEC + 8), then a
    bit error in the first bit of its flag (+8). As a receiver: a form error
    at the acknowledgement delimiter, which carries the transmitter's flag
    (REC + 1), then a bit error in the first bit of its flag (+8)."""
    peer = CanNode()
    peer.ack = False
    chip = await setup_node(dut, [peer])
    bus = chip.can_bus
    tx = Tx(0x0F0, b"\xc3")
    cocotb.start_soon(recessive_at(bus, crc_end(tx) + 3))   # ACK delimiter
    assert not await node_attempt(chip, tx)
    assert await node_counters(chip) == (0, 16)
    bad = Tx(0x321, b"\x01\x02", bad_crc=True)
    cocotb.start_soon(recessive_at(bus, crc_end(bad) + 4))  # end of frame bit 1
    peer.pending.append(bad)
    await node_frames(chip, len(await node_frames(chip, 0)) + 2)
    await sleep_ns(30 * BIT_NS)
    assert await node_counters(chip) == (9, 16)
    peer.ack = True
    assert await node_attempt(chip, tx)
    assert await node_counters(chip) == (9, 15)
    assert not peer.violations, peer.violations[:5]
