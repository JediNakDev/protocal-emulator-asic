# SPDX-License-Identifier: Apache-2.0
"""CAN receive and transmit programs against an independent CAN node on a
wired-AND bus with transceiver delay."""

from collections import deque

import cocotb
from cocotb.triggers import RisingEdge

from chip import Chip, assemble, program_source, regs
from pe import g2 as g2lib
from pe.proto import can

TXD, RXD = 0, 8
LOOP_DELAY = 4  # transceiver TXD -> bus -> RXD, in cycles


def node_crc15(bits):
    reg = 0
    for b in bits:
        top = (reg >> 14) & 1
        reg = ((reg << 1) & 0x7FFF) ^ (0x4599 if top ^ b else 0)
    return reg


class CanNode:
    """A CAN 2.0A node: transmits with arbitration, receives, and
    acknowledges good frames. Bit time may differ from the chip's."""

    def __init__(self, bit=50.0, sample=35):
        self.bit = bit
        self.sample = sample
        self.tx = 1
        self.pending = []      # (id, data, join_only)
        self.received = []     # (id, data, crc_ok)
        self.sent = []         # (id, acked)
        self.lost = []
        self.state = "idle"
        self.recessive = 0
        self.now = 0

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
        self.state = "frame"
        self.t0 = self.now
        self.k = 0
        self.raw = []
        self.data_bits = []
        self.run, self.last = 0, None
        self.stuffed = True
        self.crc_end = None
        self.ack_drive = False
        self.transmitting = False
        if transmit:
            ident, data, _ = self.pending.pop(0)
            self.tx_id = ident
            self.tx_bits = self._frame_bits(ident, data)
            self.transmitting = True

    def _boundary(self, k):
        if self.transmitting and k < len(self.tx_bits):
            self.tx = self.tx_bits[k]
        elif self.ack_drive and self.crc_end is not None and k == self.crc_end + 2:
            self.tx = 0
        else:
            self.tx = 1

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
                    if self.transmitting:
                        self.own = True
                    else:
                        self.received.append((ident, data, ok))
                        self.ack_drive = ok
            return
        if k == self.crc_end + 2 and self.transmitting:
            self.sent.append((self.tx_id, b == 0))
        if k == self.crc_end + 10:
            self.state = "idle"
            self.recessive = 0
            self.tx = 1

    def step(self, b):
        """Advance one cycle; b is the bus level this node sees."""
        self.now += 1
        if self.state == "idle":
            self.recessive = self.recessive + 1 if b else 0
            if b == 0:
                join = bool(self.pending) and self.pending[0][2]
                self._begin(transmit=join)
                self._boundary(0)
            elif self.pending and not self.pending[0][2] and self.recessive >= 11 * self.bit:
                self._begin(transmit=True)
                self._boundary(0)
            return
        t = self.now - self.t0
        while round((self.k + 1) * self.bit) <= t:
            self.k += 1
            self._boundary(self.k)
        if t == round(self.k * self.bit + self.sample):
            self._sample(self.k, b)


async def bus(chip, node):
    """Wired-AND bus: dominant (0) wins. RXD and the node see it delayed."""
    dut = chip.dut
    line = deque([1] * LOOP_DELAY)
    while True:
        await RisingEdge(dut.clk)
        chip_tx = (int(dut.uio_out.value) >> TXD) & 1 if (int(dut.uio_oe.value) >> TXD) & 1 else 1
        line.append(chip_tx & node.tx)
        seen = line.popleft()
        chip.set_pin8(seen)
        node.step(seen)


async def setup(dut, node):
    chip = Chip(dut)
    await chip.start()
    await chip.set_quad()
    chip.set_pin8(1)
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
    cocotb.start_soon(bus(chip, node))
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


@cocotb.test()
async def test_can_transmit_acked(dut):
    node = CanNode()
    chip = await setup(dut, node)
    await chip.push(0, can.tx_words(0x456, b"\x11\x22")[:4])
    words = can.tx_words(0x456, b"\x11\x22")
    rx = await chip.stream(0, words[4:], 1) if len(words) > 4 else await chip.pop_wait(0, 1)
    assert rx == [0], f"ACK slot level {rx}"
    assert node.received == [(0x456, b"\x11\x22", True)], node.received
    own = await receive_frames(chip, 1)
    assert own[0] == (0x456, b"\x11\x22", True)


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
    # Recover and retransmit.
    await chip.write(regs.CMD, regs.CMD_CLEAR0 | regs.CMD_RESTART0)
    await chip.force(0, chip.can_tx_prog.label("next"))
    await chip.write(regs.FLAGS, 0x01)
    rx = await chip.stream(0, words, 1)
    assert rx == [0]
    assert node.received == [(0x400, b"\x01", True)]


@cocotb.test()
async def test_can_receive_clock_offset(dut):
    """Bit timing is synchronized only at start of frame, so the clock
    offset limit is small: 0.1% across a full 8-byte frame still works."""
    node = CanNode(bit=50.05)
    chip = await setup(dut, node)
    data = bytes([0x0F, 0xF0, 0x3C, 0xC3, 0x00, 0xFF, 0x81, 0x7E])
    node.pending.append((0x555, data, False))
    frames = await receive_frames(chip, 1)
    assert frames[0] == (0x555, data, True)
