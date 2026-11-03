# SPDX-License-Identifier: Apache-2.0
"""Two programs at once, one on each engine, against one independent peer per
protocol in the same simulated window, at the 50 MHz clock profile.

A single Host serves both engines in turn, since the Host port carries one
transaction at a time. Every pair must show, while the other program runs:

- wire timing in absolute time, from that protocol's own peer;
- data at the Host in both directions, compared word by word;
- no receive overflow, capture overrun or other sticky error, and no
  contention on any bidirectional pin;
- a real overlap: both protocols toggled inside a shared window, with their
  edges interleaved. `overlap` measures it and rejects sequential runs.

Each program has a role here. A role knows its pins, its configuration, its
workload and its peer, and it works on either engine at any memory offset.
docs/concurrency.md records the results."""

import itertools
import os
import random

import cocotb
from cocotb.triggers import ReadOnly

from chip import MHZ_50, Chip, assemble, fails_at_wrong_clock, program_source, regs
from pe import g2 as g2lib
from pe.proto import can, eth, i2c, ps2, spi, swd, waveform
from pe.proto.jtag import Scan
from peers import I2CTarget, SpiPeripheral, Wave, now_ns, sleep_ns, sleep_until, uart_decode, uart_transmit
from test_can import CanBus, CanNode
from test_eth import DST, SRC, TxMonitor, bits_of, drive_rx, wire_frame
from test_jtag import IDCODE_VALUE, IR_BYPASS, IR_SCRATCH, TCK, TDI, TDO_PIN, TMS, TapModel
from test_ps2 import CLK as PS2_CLK
from test_ps2 import DATA as PS2_DATA
from test_ps2 import DEVICE_CLKDIV, HOST_CLKDIV, DeviceModel, HostModel
from test_swd import DP_IDCODE, SWCLK, SWDIO, DebugPortModel

PROFILE = MHZ_50
CYCLE_NS = 1e9 / PROFILE  # the Host's conversion between cycles and ns
PAGE = 64
BIDIR, INPUTS = range(8), (8, 9)
OVERLAP_MIN_EDGES = 20      # of each protocol inside the shared window
OVERLAP_MIN_SWITCHES = 12   # changes of protocol along the merged edge list
EXACT_NS = 0.001            # an edge time that must be exact, against rounding


class Role:
    """One program on one engine, with its peer. Subclasses set the class
    attributes and fill in the steps. `fixed` pins are those a program or an
    existing peer hard-codes; `span` asks for that many adjacent bidirectional
    pins and `inputs` for that many input pins, allocated around them.
    `window_ns` is the time a workload should fill."""

    program = None
    fixed = ()
    span = 0
    inputs = 0
    min_window_ns = 300_000
    depth = 4            # transmit queue entries in this role's buffer mode
    urgent = False       # the wire breaks if the Host is late

    def __init__(self, chip, engine, offset, pins, window_ns):
        self.chip, self.dut = chip, chip.dut
        self.engine, self.offset, self.window_ns = engine, offset, window_ns
        self.owner = regs.OWNER_E1 if engine else regs.OWNER_E0
        self.pins = pins
        self.name = type(self).__name__
        self.rng = random.Random(f"{self.name}{engine}")
        self.tx, self.rx, self.sent = [], [], 0
        self.n_rx = 0
        self.waves = []
        self.pull = 0
        self.tasks = []

    async def load(self):
        self.prog = await self.chip.load_program(self.program, self.offset)

    def set_input(self, pin):
        """How a peer drives an input pin."""
        return {8: self.chip.set_pin8, 9: self.chip.set_pin9}.get(pin, lambda v: self.chip.drive_uio(pin, v))

    def input_wave(self, pin):
        return Wave(self.dut.ui_in, pin - 2) if pin in INPUTS else Wave(self.dut.uio_in, pin)

    async def pincfg(self, pin, value):
        await self.chip.pincfg(pin, self.owner | value)

    def prepare(self):
        """Before configuration: input idle levels and `self.pull`."""

    async def configure(self):
        raise NotImplementedError

    def arm(self):
        """Just before the engines start: peers and recording."""

    def go(self):
        """Just after the engines start: stimulus, which a stopped engine
        would miss."""

    async def prefill(self):
        """Transmit words that must be queued before the engine starts."""

    def busy(self):
        return self.sent < len(self.tx) or len(self.rx) < self.n_rx

    async def settle(self):
        """After the Host is done: let the last wire activity finish."""

    def check(self):
        """Returns a list of problems."""
        return []

    def edges(self):
        return sorted(t for w in self.waves for t in w.times)


def spawn(role, coro):
    task = cocotb.start_soon(coro)
    role.tasks.append(task)
    return task


async def until(cond, timeout_ns):
    deadline = now_ns() + timeout_ns
    while not cond():
        if now_ns() > deadline:
            return False
        await sleep_ns(1000)
    return True


# ------------------------------------------------------------------- UART
def uart_clkdiv(baud):
    return round(PROFILE / (8 * baud)) - 1


def uart_exact(line, bit_ns, t_end):
    """Every edge of every frame sits on the program's bit grid, to the
    picosecond rounding of the simulator: another engine must not move an
    edge by even one cycle. `uart_decode` allows far more, as a receiver does."""
    out, t = [], float("-inf")
    for te in line.edges(level=0):
        if te < t or te + 9.5 * bit_ns > t_end:
            continue
        t = te + 9.5 * bit_ns
        for e in line.edges(te, t):
            if abs((e - te) - round((e - te) / bit_ns) * bit_ns) > EXACT_NS:
                out.append(f"timing: UART edge {e - te:.3f} ns after a start edge is off the {bit_ns} ns grid")
    return out


class UartTx(Role):
    program = "uart_tx"
    span = 1
    min_window_ns = 1_000_000

    def __init__(self, *args, baud=115_200, peer_baud=None, clkdiv=None):
        super().__init__(*args)
        self.baud, self.peer_baud = baud, peer_baud or baud
        self.clkdiv = uart_clkdiv(baud) if clkdiv is None else clkdiv
        self.pin = self.pins["span"][0]
        n = max(6, int(self.window_ns * baud / 10e9))
        self.data = ([0x00, 0xFF, 0x55, 0xAA] + [self.rng.randrange(256) for _ in range(n)])[:n]
        self.tx = list(self.data)

    def prepare(self):
        self.pull = 1 << self.pin

    async def configure(self):
        await self.chip.setup_engine(self.engine, self.prog, self.offset, clkdiv=self.clkdiv,
                                     shiftctrl=regs.OUT_RIGHT, out_base=self.pin,
                                     set_base=self.pin, side_base=self.pin)
        await self.chip.force_asm(self.engine, "set pins, 1\nset pindirs, 1", side_count=1, side_opt=True)
        await self.pincfg(self.pin, regs.DRIVE_PUSH_PULL)

    def arm(self):
        self.line = Wave(self.dut.uio_in, self.pin)
        self.waves = [self.line]

    async def settle(self):
        """The last words are still in the queue when the Host is done."""
        await until(lambda: len(uart_decode(self.line, self.baud, now_ns())[0]) >= len(self.data)
                    or len(self.line.times) > 12 * len(self.data), 30e9 / self.baud * self.depth)
        await sleep_ns(12e9 / self.baud)

    def check(self):
        got, out = uart_decode(self.line, self.peer_baud, now_ns())
        out += uart_exact(self.line, 8 * (self.clkdiv + 1) * CYCLE_NS, now_ns())
        if got != self.data:
            out.append(f"UART TX bytes: sent {len(self.data)}, peer decoded {len(got)}: {got[:8]}")
        return out


class UartRx(Role):
    program = "uart_rx"
    inputs = 1
    min_window_ns = 1_000_000

    def __init__(self, *args, baud=115_200, peer_baud=None):
        super().__init__(*args)
        self.baud, self.peer_baud = baud, peer_baud or baud
        self.pin = self.pins["inputs"][0]
        n = max(6, int(self.window_ns * baud / 12e9))
        self.data = ([0x00, 0xFF, 0x55, 0xA3] + [self.rng.randrange(256) for _ in range(n)])[:n]
        self.n_rx = n

    def prepare(self):
        self.set_input(self.pin)(1)

    async def configure(self):
        await self.chip.setup_engine(self.engine, self.prog, self.offset, clkdiv=uart_clkdiv(self.baud),
                                     shiftctrl=regs.IN_RIGHT, in_base=self.pin, jmp_pin=self.pin)

    def arm(self):
        self.waves = [self.input_wave(self.pin)]

    def go(self):
        spawn(self, uart_transmit(self.set_input(self.pin), self.peer_baud, self.data))

    def busy(self):
        return not self.tasks[0].done()

    async def settle(self):
        await sleep_ns(40e9 / self.baud)  # a word reaches the Host after the stop bit

    def check(self):
        got = [w >> 8 for w in self.rx]
        return [] if got == self.data else [f"UART RX bytes: {got[:8]}, expected {self.data[:8]}"]


# -------------------------------------------------------------------- I2C
class I2c(Role):
    program = "i2c"
    span = 2
    min_window_ns = 300_000
    TARGET = 0x42

    def __init__(self, *args, speed=400_000, stretch_ns=2000, peer_speed=None):
        super().__init__(*args)
        self.speed, self.stretch_ns, self.peer_speed = speed, stretch_ns, peer_speed or speed
        self.sda, self.scl = self.pins["span"][:2]
        self.expected, self.bus = [], []
        rounds = max(1, int(self.window_ns / (10 * 9 * (1e9 / speed) + 8 * stretch_ns)))
        for k in range(rounds):
            reg, a, b = 0x10 + 3 * k, self.rng.randrange(256), self.rng.randrange(256)
            self.tx += i2c.write(self.TARGET, [reg, a, b])
            self.tx += i2c.write(self.TARGET, [reg], stop=False) + i2c.read(self.TARGET, 2)
            self.tx += i2c.write(0x21, [])
            self.expected += [(0x84, 0), (reg, 0), (a, 0), (b, 0), (0x84, 0), (reg, 0), (0x85, 0), (a, 0),
                              (b, 1), (0x42, 1)]
            self.bus += [[0x84, reg, a, b], [0x84, reg], [0x85, a, b], [0x42]]
        self.stops_expected = 3 * rounds
        self.n_rx = len(self.expected)

    def prepare(self):
        self.pull = 1 << self.sda | 1 << self.scl

    async def configure(self):
        await self.chip.setup_engine(self.engine, self.prog, self.offset, clkdiv=i2c.CLKDIV[self.speed],
                                     out_base=self.sda, set_base=self.sda, in_base=self.sda,
                                     side_base=self.scl)
        await self.chip.force_asm(self.engine, "set pins, 1 side 1", side_count=1)
        await self.pincfg(self.sda, regs.DRIVE_OPEN_DRAIN)
        await self.pincfg(self.scl, regs.DRIVE_OPEN_DRAIN)

    def arm(self):
        self.target = I2CTarget(self.chip, self.sda, self.scl, address=self.TARGET,
                                stretch_ns=self.stretch_ns, speed=self.peer_speed)
        self.waves = [Wave(self.dut.uio_in, self.scl), Wave(self.dut.uio_in, self.sda)]
        cocotb.start_soon(self.target.run(self.dut.clk))

    async def settle(self):
        await until(lambda: self.target.stops == self.stops_expected, 200_000)

    def check(self):
        out = [f"timing: I2C {v}" for v in self.target.violations]
        got = [i2c.decode(w) for w in self.rx]
        if got != self.expected:
            out.append(f"I2C words at the Host: {got[:6]}, expected {self.expected[:6]}")
        if self.target.transactions != self.bus:
            out.append(f"I2C bus transactions: {self.target.transactions[:4]}, expected {self.bus[:4]}")
        period = 1e9 / self.peer_speed
        fastest = min(self.target.periods, default=0)
        if not period - EXACT_NS <= fastest <= 1.1 * period:
            out.append(f"timing: SCL period {fastest} ns at {self.peer_speed} Hz")
        return out


# -------------------------------------------------------------------- SPI
class Spi(Role):
    program = "spi_cpha0"
    span = 3
    inputs = 1
    MISO_BYTES = [0x5A, 0xC3, 0x01, 0x80, 0x7E, 0x00]

    def __init__(self, *args, rate=6_250_000, peer_rate=None):
        super().__init__(*args)
        self.rate, self.peer_rate = rate, peer_rate or rate
        self.bypass = rate > 6_250_000
        if self.bypass:
            self.program = "spi_cpha0_fast"
        self.mosi, self.sck, self.cs_n = self.pins["span"][:3]
        self.miso = self.pins["inputs"][0]
        self.transactions = []
        while sum(map(len, self.transactions)) * 1500 < self.window_ns:
            self.transactions.append([self.rng.randrange(256) for _ in range(self.rng.choice((1, 2, 6)))])
        self.tx = [w for t in self.transactions for w in spi.transaction(t)]
        self.n_rx = len(self.tx)

    def prepare(self):
        self.pull = 1 << self.cs_n

    async def configure(self):
        await self.chip.setup_engine(self.engine, self.prog, self.offset, shiftctrl=regs.AUTOPUSH, thresh=8,
                                     out_base=self.mosi, set_base=self.mosi, set_count=3,
                                     in_base=self.miso, side_base=self.sck)
        await self.chip.force_asm(self.engine, "set pindirs, 7 side 2", side_count=2)
        await self.pincfg(self.mosi, regs.DRIVE_PUSH_PULL)
        await self.pincfg(self.sck, regs.DRIVE_PUSH_PULL)
        await self.pincfg(self.cs_n, regs.DRIVE_PUSH_PULL)
        await self.chip.pincfg(self.miso, regs.PIN_BYPASS if self.bypass else 0)
        await self.chip.cycles(2)

    def arm(self):
        uio = self.dut.uio_in
        self.periph = SpiPeripheral(Wave(uio, self.sck), Wave(uio, self.mosi), Wave(uio, self.cs_n),
                                    self.set_input(self.miso), 0, self.MISO_BYTES, self.peer_rate)
        self.waves = [self.periph.sck, self.periph.cs_n]
        self.peer = cocotb.start_soon(self.periph.run(len(self.transactions)))

    async def settle(self):
        await until(self.peer.done, 20_000)

    def check(self):
        if not self.peer.done():
            return [f"SPI peripheral saw {len(self.periph.cs_n.edges(level=1))} transactions"]
        out = self.periph.check()
        if self.peer.result() != self.transactions:
            out.append("SPI MOSI bytes differ at the peripheral")
        expected = [b for t in self.transactions for b in self.MISO_BYTES[:len(t)]]
        got = [spi.decode(w) for w in self.rx]
        if got != expected:
            out.append(f"SPI MISO bytes at the Host: {got[:8]}, expected {expected[:8]}")
        return out


# ------------------------------------------------------------------- JTAG
class Jtag(Role):
    """Pins come from the peer, test_jtag.TapModel."""

    program = "jtag"
    fixed = (TDI, TMS, TCK, TDO_PIN)

    def __init__(self, *args):
        super().__init__(*args)
        self.scan, self.expected = Scan(), []
        scratch = 0xC3
        for _ in range(max(1, int(self.window_ns / 16_000))):
            value = self.rng.randrange(256)
            self.scan.reset()
            self.scan.dr(0, 32)
            self.scan.ir(IR_SCRATCH, 4)
            self.scan.dr(value, 8)
            self.scan.ir(IR_BYPASS, 4)
            self.scan.dr(0b1011, 4)
            self.expected += [IDCODE_VALUE, 0b0001, scratch, 0b0001, 0b0110]
            scratch = value
        self.tx = self.scan.words()
        self.n_rx = self.scan.n_rx_words()

    async def configure(self):
        await self.chip.setup_engine(self.engine, self.prog, self.offset,
                                     shiftctrl=regs.AUTOPULL | regs.AUTOPUSH | regs.OUT_RIGHT | regs.IN_RIGHT,
                                     out_base=TDI, out_count=2, in_base=TDO_PIN, side_base=TCK)
        await self.chip.ereg(self.engine, regs.SETPIN, TDI | (3 << 4))
        await self.chip.force_asm(self.engine, "set pindirs, 7 side 0", side_count=1)
        for p in (TDI, TMS, TCK):
            await self.pincfg(p, regs.DRIVE_PUSH_PULL)

    def arm(self):
        self.tap = TapModel(self.chip)
        self.waves = [self.tap.tck, self.tap.tdi]
        cocotb.start_soon(self.tap.run())

    def check(self):
        out = self.tap.check()
        got = self.scan.results(self.rx)
        if got != self.expected:
            out.append(f"JTAG scans at the Host: {got[:5]}, expected {self.expected[:5]}")
        return out


# -------------------------------------------------------------------- SWD
class Swd(Role):
    """Pins come from the peer, test_swd.DebugPortModel."""

    program = "swd"
    fixed = (SWDIO, SWCLK)

    def __init__(self, *args):
        super().__init__(*args)
        self.batch, self.expected = swd.Batch(), []
        self.batch.line_reset()
        for _ in range(max(1, int(self.window_ns / 22_000))):
            value = self.rng.randrange(1 << 30) & ~0x3F
            self.batch.read(0, 0x0)
            self.batch.write(0, 0x4, value)
            self.batch.read(0, 0x4)
            self.expected += [(swd.ACK_OK, DP_IDCODE, True), (swd.ACK_OK,), (swd.ACK_OK, value, True)]
        self.tx = self.batch.tx
        self.n_rx = sum(4 if len(e) == 3 else 1 for e in self.expected)

    def prepare(self):
        self.pull = 1 << SWDIO

    async def configure(self):
        e = self.engine
        await self.chip.setup_engine(e, self.prog, self.offset,
                                     shiftctrl=regs.OUT_RIGHT | regs.IN_RIGHT | regs.AUTOPULL | regs.AUTOPUSH,
                                     out_base=SWDIO, in_base=SWDIO, side_base=SWCLK)
        await self.chip.ereg(e, regs.SETPIN, SWCLK | (1 << 4))
        await self.chip.force_asm(e, "set pindirs, 1 side 0", side_count=1, side_opt=True)
        await self.chip.ereg(e, regs.SETPIN, SWDIO | (1 << 4))
        await self.pincfg(SWDIO, regs.DRIVE_PUSH_PULL)
        await self.pincfg(SWCLK, regs.DRIVE_PUSH_PULL)

    def arm(self):
        self.dp = DebugPortModel(self.chip)
        self.waves = [self.dp.swclk]
        cocotb.start_soon(self.dp.run())

    def check(self):
        out = self.dp.check()
        got, used = self.batch.decode(self.rx)
        if got != self.expected or used != len(self.rx):
            out.append(f"SWD results at the Host: {got[:4]}, expected {self.expected[:4]}")
        return out


# ------------------------------------------------------------------- PS/2
class Ps2Device(Role):
    """Pins come from the program, which hard-codes CLK and DATA in its
    `wait gpio` instructions."""

    program = "ps2_device"
    fixed = (PS2_CLK, PS2_DATA)
    min_window_ns = 3_500_000
    clkdiv = DEVICE_CLKDIV

    def prepare(self):
        self.pull = 1 << PS2_CLK | 1 << PS2_DATA

    async def configure(self):
        await self.chip.setup_engine(self.engine, self.prog, self.offset, clkdiv=self.clkdiv,
                                     shiftctrl=regs.OUT_RIGHT | regs.IN_RIGHT, out_base=PS2_DATA,
                                     set_base=PS2_DATA, in_base=PS2_DATA, jmp_pin=PS2_CLK,
                                     side_base=PS2_CLK, statuscfg=1)
        await self.chip.force_asm(self.engine, "set pins, 1 side 1", side_count=1, side_opt=True)
        await self.pincfg(PS2_CLK, regs.DRIVE_OPEN_DRAIN)
        await self.pincfg(PS2_DATA, regs.DRIVE_OPEN_DRAIN)

    def arm(self):
        self.peer = HostModel(self.chip)
        self.problems = []
        self.waves = [self.peer.clk, self.peer.data]
        cocotb.start_soon(self.peer.run())
        spawn(self, self.script())

    def busy(self):
        return not self.tasks[0].done()

    async def script(self):
        """A scan code, a host command the chip acknowledges, and the reply."""
        self.tx.append(ps2.device_frame(0x1C))
        if not await until(lambda: self.peer.frames, 2_000_000):
            self.problems.append("no PS/2 frame at the host")
        await sleep_ns(60_000)  # the program polls CLK until 15 us after the last rising edge
        self.peer.to_send.append(0xED)
        if not await until(lambda: self.rx and self.peer.acks, 3_000_000):
            self.problems.append(f"no PS/2 command at the Host: {self.rx}")
        self.tx.append(ps2.device_frame(0xFA))
        if not await until(lambda: len(self.peer.frames) == 2, 2_000_000):
            self.problems.append("no PS/2 reply at the host")

    def check(self):
        out = self.problems + self.peer.violations
        if self.peer.frames != [(0x1C, True), (0xFA, True)]:
            out.append(f"PS/2 frames at the host: {self.peer.frames}")
        if [ps2.decode(w) for w in self.rx] != [(0xED, True, True)] or self.peer.acks != [True]:
            out.append(f"PS/2 command at the Host: {self.rx}, acks {self.peer.acks}")
        return out


class Ps2Host(Ps2Device):
    program = "ps2_host"
    clkdiv = HOST_CLKDIV

    def arm(self):
        self.peer = DeviceModel(self.chip)
        self.problems = []
        self.waves = [self.peer.clk, self.peer.data]
        cocotb.start_soon(self.peer.run())
        spawn(self, self.script())

    async def script(self):
        """A self-test frame, then a reset command with its two answers."""
        self.peer.to_send.append(0xAA)
        if not await until(lambda: self.rx, 2_000_000):
            self.problems.append("no PS/2 frame at the Host")
        self.tx.append(ps2.host_frame(0xFF))
        if not await until(lambda: len(self.rx) >= 4, 5_000_000):
            self.problems.append(f"PS/2 words at the Host: {self.rx}")

    def check(self):
        out = self.problems + self.peer.violations
        if self.peer.commands != [(0xFF, True)]:
            out.append(f"PS/2 commands at the device: {self.peer.commands}")
        if len(self.rx) != 4 or not ps2.acked(self.rx[1]):
            return out + [f"PS/2 words at the Host: {self.rx}"]
        got = [ps2.decode(w) for w in (self.rx[0], self.rx[2], self.rx[3])]
        if got != [(0xAA, True, True), (0xFA, True, True), (0xAA, True, True)]:
            out.append(f"PS/2 frames at the Host: {got}")
        return out


# -------------------------------------------------------------------- CAN
class ListenBus(CanBus):
    """The bus of a listen-only receiver. The base class reads pin TXD as the
    chip's transmitter; here another program may own that pin."""

    def chip_tx(self):
        return 1


class CanRx(Role):
    """Listen-only. RXD is hard-coded in the program; G2 is a shared block."""

    program = "can_rx"
    fixed = (8,)
    min_window_ns = 300_000
    depth = 0

    def __init__(self, *args):
        super().__init__(*args)
        self.frames = []
        for k in range(max(1, int(self.window_ns / 120_000))):
            self.frames.append((0x100 + 37 * k, bytes(self.rng.randrange(256) for _ in range(8))))

    def prepare(self):
        self.chip.set_pin8(1)

    async def configure(self):
        chip = self.chip
        await chip.setup_engine(self.engine, self.prog, self.offset, shiftctrl=regs.AUTOPUSH | regs.OUT_RIGHT
                                | regs.fifo_mode(regs.FIFO_JOIN_RX), thresh=15, in_base=8,
                                jmp_pin=regs.PIN_G2_OUT1)
        await chip.write(regs.G2_ADDR, 0)
        await chip.write(regs.G2_DATA, g2lib.can_destuffer())
        await chip.write(regs.G2_IN0, [regs.G2_SRC_FEED_BIT, regs.G2_SRC_FEED_BIT])
        await chip.write(regs.G2_STATE, 0)
        await chip.write(regs.G2_CTRL, regs.G2_ENABLE | (regs.G2_OWNER1 if self.engine else 0)
                         | regs.G2_STEP_FEED | regs.G2_EMIT)

    def arm(self):
        self.node = CanNode()
        bus = ListenBus(self.chip)
        self.node.attach(bus)
        CanNode(self.node.bit).attach(bus)  # acknowledges
        self.waves = [bus.line]

    def go(self):
        for ident, data in self.frames:
            self.node.send(ident, data)

    def busy(self):
        return len(can.split_records(self.rx)[0]) < len(self.frames)

    def check(self):
        got = [can.parse(r) for r in can.split_records(self.rx)[0]]
        out = []
        if got != [(i, d, True) for i, d in self.frames]:
            out.append(f"CAN frames at the Host: {got[:3]}")
        if self.node.sent != [(i, True) for i, _ in self.frames]:
            out.append(f"CAN frames on the bus: {self.node.sent[:3]}")
        return out + self.node.violations + [f"CAN error {e}" for e in self.node.errors]


# --------------------------------------------------------------- 10BASE-T
class EthRx(Role):
    """Uses G2 and G7; its receive pin is the peer's, test_eth.drive_rx."""

    program = "eth10_rx"
    fixed = (8,)
    depth = 0
    urgent = True

    def __init__(self, *args):
        super().__init__(*args)
        self.wires = []
        for _ in range(max(2, int(self.window_ns / 70_000))):
            self.wires.append(wire_frame(bytes(self.rng.randrange(256) for _ in range(46))))

    def prepare(self):
        self.chip.set_pin8(0)

    async def configure(self):
        chip = self.chip
        await chip.setup_engine(self.engine, self.prog, self.offset, shiftctrl=regs.AUTOPUSH
                                | regs.fifo_mode(regs.FIFO_JOIN_RX), thresh=15)
        await chip.write(regs.NEGSEL, 8)
        await chip.write(regs.G2_ADDR, 0)
        await chip.write(regs.G2_DATA, g2lib.recovered_bits())
        await chip.write(regs.G2_IN0, [regs.G2_SRC_CR_DATA, regs.G2_SRC_CR_LOCKED])
        await chip.write(regs.G2_STATE, 0x20)
        await chip.write(regs.G2_CTRL, regs.G2_ENABLE | (regs.G2_OWNER1 if self.engine else 0)
                         | regs.G2_STEP_STROBE | regs.G2_EMIT)
        await chip.write(regs.CR_CTRL, eth.rx_clock_recovery(8))

    def arm(self):
        self.waves = [Wave(self.dut.ui_in, 6)]

    def go(self):
        spawn(self, self.drive())

    async def drive(self):
        for i, wire in enumerate(self.wires):
            await drive_rx(self.chip, wire, 13.5, 9_600, seed=i)

    def busy(self):
        return not self.tasks[0].done() or len(eth.split_records(self.rx)[0]) < len(self.wires)

    def check(self):
        records = eth.split_records(self.rx)[0]
        if len(records) != len(self.wires):
            return [f"Ethernet records at the Host: {len(records)} of {len(self.wires)}"]
        out = []
        for record, wire in zip(records, self.wires):
            parsed = eth.parse(record)
            if parsed is None or parsed != (wire[8:-4], True):
                out.append(f"Ethernet frame at the Host differs: {record[:4]}")
        return out


class MovedTxMonitor(TxMonitor):
    """test_eth.TxMonitor on other pins."""

    def __init__(self, dut, tdp, tdm):
        self.tdp = Wave(dut.uio_out, tdp)
        self.tdm = Wave(dut.uio_out, tdm)


class EthTx(Role):
    """TD+, TD- and S on adjacent pins. S is never driven, but its pin
    register feeds NEGOUT, so its pin is taken."""

    program = "eth10_tx"
    span = 3
    depth = 8
    urgent = True

    def __init__(self, *args):
        super().__init__(*args)
        size = min(1500, max(46, int(self.window_ns / 800)))
        self.tdp, self.tdm, self.phase = self.pins["span"][:3]
        self.payload = bytes(self.rng.randrange(256) for _ in range(size))
        self.wire = eth.frame(DST, SRC, 0x0800, self.payload)
        self.tx = eth.tx_words(self.wire)

    async def configure(self):
        chip, e = self.chip, self.engine
        await chip.setup_engine(e, self.prog, self.offset, shiftctrl=regs.AUTOPULL | regs.OUT_RIGHT
                                | regs.fifo_mode(regs.FIFO_JOIN_TX), side_base=self.tdp,
                                execcfg=self.prog.label("table", self.offset), statuscfg=1)
        mask = 1 << self.tdp | 1 << self.tdm
        await chip.write(regs.NEGOUT, [self.phase, mask & 0xFF, mask >> 8])
        await chip.ereg(e, regs.SETPIN, self.tdp | (3 << 4))
        await chip.force_asm(e, "set pindirs, 3 side 0", side_count=3)
        await self.pincfg(self.tdp, regs.DRIVE_PUSH_PULL)
        await self.pincfg(self.tdm, regs.DRIVE_PUSH_PULL)
        await self.pincfg(self.phase, regs.DRIVE_OFF)

    def arm(self):
        self.monitor = MovedTxMonitor(self.dut, self.tdp, self.tdm)
        self.waves = [self.monitor.tdp]

    async def prefill(self):
        await self.chip.push(self.engine, self.tx[:8])
        self.sent = 8

    async def settle(self):
        self.bits, self.violations = await self.monitor.frame(len(self.wire) * 8)

    def check(self):
        out = list(self.violations)
        if self.bits != bits_of(self.wire):
            out.append("Ethernet bits on the wire differ")
        return out


# --------------------------------------------------------------- waveform
class Capture(Role):
    """Records every edge of an independent signal on an input pin. The
    signal comes in bursts, as a protocol frame would, and the Host drains
    two words per edge in the time left by the other program."""

    program = "capture"
    inputs = 1
    depth = 0
    urgent = True

    def __init__(self, *args, gap=(400, 1200)):
        super().__init__(*args)
        self.pin = self.pins["inputs"][0]
        self.cycles = []
        while sum(self.cycles) * CYCLE_NS < self.window_ns:
            self.cycles += [self.rng.randint(8, 20) for _ in range(self.rng.randint(1, 3))]
            self.cycles.append(self.rng.randint(*gap))
        self.n_rx = 1 + 2 * len(self.cycles)

    def prepare(self):
        self.set_input(self.pin)(0)

    async def configure(self):
        chip = self.chip
        await chip.pincfg(self.pin, regs.pin_filter(3))
        await chip.setup_engine(self.engine, self.prog, self.offset, shiftctrl=regs.fifo_mode(regs.FIFO_JOIN_RX),
                                in_base=self.pin)
        await chip.ereg(self.engine, regs.CAPCFG, [self.pin | (3 << 4) | (1 << 6), 0])

    def arm(self):
        self.waves = [self.input_wave(self.pin)]

    def go(self):
        spawn(self, self.drive())

    def busy(self):
        return not self.tasks[0].done()

    async def settle(self):
        await sleep_ns(1000 * CYCLE_NS)

    async def drive(self):
        await sleep_ns(40 * CYCLE_NS)  # the program reads the idle level first
        set_line, level = self.set_input(self.pin), 0
        t = now_ns() + CYCLE_NS / 2
        self.driven = []
        for n in self.cycles:
            t += n * CYCLE_NS
            await sleep_until(t)
            level ^= 1
            set_line(level)
            self.driven.append(now_ns())

    def check(self):
        if len(self.rx) != self.n_rx:
            return [f"capture: {len(self.rx)} of {self.n_rx} words"]
        level, times = waveform.from_capture(self.rx)
        captured = [round(c * CYCLE_NS, 3) for c in waveform.intervals(times)]
        driven = [round(b - a, 3) for a, b in zip(self.driven, self.driven[1:])]
        out = [] if level == 0 else ["capture: wrong initial level"]
        if captured != driven:
            bad = next((i for i, (a, b) in enumerate(zip(captured, driven)) if a != b), 0)
            out.append(f"timing: captured interval {bad} is {captured[bad]} ns, driven {driven[bad]} ns")
        return out


class CaptureLine(Capture):
    """Records a line of the other program instead of a signal of its own,
    which is the wiring of a sniffer. The pin has one PINCFG, so its filter
    is added to what the other role set up, and the other program sees the
    filtered line too. Configure this role second."""

    inputs = 0

    def __init__(self, *args, line=0, level=1):
        Role.__init__(self, *args)
        self.pin, self.level = line, level

    def prepare(self):
        """The other role drives the line."""

    async def configure(self):
        chip = self.chip
        setup = await chip.read8(regs.PINCFG + self.pin)
        await chip.pincfg(self.pin, setup | regs.pin_filter(3))
        await chip.setup_engine(self.engine, self.prog, self.offset, shiftctrl=regs.fifo_mode(regs.FIFO_JOIN_RX),
                                in_base=self.pin)
        await chip.ereg(self.engine, regs.CAPCFG, [self.pin | (3 << 4) | (1 << 6), 0])

    def arm(self):
        self.line = self.input_wave(self.pin)
        self.waves = [self.line]

    def go(self):
        """The other program makes the edges."""

    def busy(self):
        return not self.line.times or now_ns() - self.line.times[-1] < 300_000

    def check(self):
        edges = len(self.line.times)
        if len(self.rx) != 1 + 2 * edges:
            return [f"capture: {len(self.rx)} words for {edges} edges"]
        level, times = waveform.from_capture(self.rx)
        captured = [round(c * CYCLE_NS, 3) for c in waveform.intervals(times)]
        wire = [round(b - a, 3) for a, b in zip(self.line.times, self.line.times[1:])]
        out = [] if level == self.level else ["capture: wrong initial level"]
        if captured != wire:
            bad = next((i for i, (a, b) in enumerate(zip(captured, wire)) if a != b), 0)
            out.append(f"timing: captured interval {bad} is {captured[bad]} ns, wire {wire[bad]} ns")
        return out


class Replay(Role):
    """Plays a recording on output pin 10. The Host must keep the queue fed:
    a late word stretches the current level, which the intervals show."""

    program = "replay"
    fixed = (10,)
    depth = 8
    urgent = True

    def __init__(self, *args, gap=(120, 900)):
        super().__init__(*args)
        self.cycles = []
        while sum(self.cycles) * CYCLE_NS < self.window_ns:
            self.cycles.append(self.rng.randint(*gap))
        self.times = [sum(self.cycles[:i + 1]) for i in range(len(self.cycles))]
        self.tx = waveform.to_replay(0, self.times, lead=100)

    async def configure(self):
        await self.pincfg(10, regs.DRIVE_ALWAYS)
        await self.chip.setup_engine(self.engine, self.prog, self.offset, shiftctrl=regs.AUTOPULL | regs.OUT_RIGHT
                                     | regs.fifo_mode(regs.FIFO_JOIN_TX), out_base=10, out_count=1)

    def arm(self):
        self.line = Wave(self.dut.uo_out, 5)
        self.waves = [self.line]

    async def prefill(self):
        await self.chip.push(self.engine, self.tx[:8])
        self.sent = 8

    async def settle(self):
        await until(lambda: len(self.line.times) >= len(self.cycles), 1000 * CYCLE_NS * self.depth)

    def check(self):
        got = [round(b - a, 3) for a, b in zip(self.line.times, self.line.times[1:])]
        want = [round(c * CYCLE_NS, 3) for c in self.cycles[1:]]
        if got != want:
            bad = next((i for i, (a, b) in enumerate(zip(got, want)) if a != b), min(len(got), len(want)))
            return [f"timing: replayed interval {bad}: {got[bad:bad + 2]} ns, recorded {want[bad:bad + 2]} ns"]
        return []


# ---------------------------------------------------------------- the pair
def allocate(classes):
    """Pins for two roles: hard-coded pins first, then adjacent bidirectional
    runs and input pins from what is left. Raises AssertionError if they
    do not fit, naming the pins."""
    taken = [p for c in classes for p in c.fixed]
    assert len(taken) == len(set(taken)), f"pin conflict: {[c.__name__ for c in classes]} on {sorted(taken)}"
    plans = []
    for c in classes:
        span = []
        if c.span:
            start = next((p for p in BIDIR if all(q in BIDIR and q not in taken
                                                  for q in range(p, p + c.span))), None)
            assert start is not None, f"no {c.span} adjacent pins for {c.__name__}"
            span = list(range(start, start + c.span))
            taken += span
        inputs = []
        for _ in range(c.inputs):
            pin = next((p for p in (*INPUTS, *BIDIR) if p not in taken), None)
            assert pin is not None, f"no input pin for {c.__name__}"
            inputs.append(pin)
            taken.append(pin)
        plans.append({"span": span, "inputs": inputs})
    return plans


def place(classes):
    """Memory offsets: the second program right behind the first when both
    fit a page, so that the pair shares one, otherwise in the next page."""
    sizes = [len(assemble(program_source(c.program))) for c in classes]
    return [0, sizes[0] if sum(sizes) <= PAGE else max(PAGE, sizes[0] + PAGE - sizes[0] % PAGE)]


def overlap(a, b):
    """Measures how two protocols overlapped on the wires: the window both
    were active in, the edges of each inside it, and how often the merged
    edge list changes protocol. Raises AssertionError if they did not really
    run together."""
    ta, tb = a.edges(), b.edges()
    assert ta and tb, f"no wire activity: {a.name} {len(ta)} edges, {b.name} {len(tb)} edges"
    t0, t1 = max(ta[0], tb[0]), min(ta[-1], tb[-1])
    assert t1 > t0, f"no overlap: {a.name} {ta[0]:.0f}-{ta[-1]:.0f} ns, {b.name} {tb[0]:.0f}-{tb[-1]:.0f} ns"
    na = [t for t in ta if t0 <= t <= t1]
    nb = [t for t in tb if t0 <= t <= t1]
    merged = sorted([(t, 0) for t in na] + [(t, 1) for t in nb])
    switches = sum(x[1] != y[1] for x, y in zip(merged, merged[1:]))
    report = dict(window_us=(t1 - t0) / 1000, edges=(len(na), len(nb)), switches=switches)
    assert len(na) >= OVERLAP_MIN_EDGES and len(nb) >= OVERLAP_MIN_EDGES, f"too few shared edges: {report}"
    assert switches >= OVERLAP_MIN_SWITCHES, f"edges not interleaved: {report}"
    shorter = min(ta[-1] - ta[0], tb[-1] - tb[0])
    assert t1 - t0 >= 0.5 * shorter, f"only part of the shorter run was shared: {report}"
    return report


async def watch_contention(dut, seen):
    """Any contention that lasts past its time step; `tb.contention` also
    glitches for a delta cycle while a pin's value and enable settle."""
    while True:
        await dut.contention.value_change
        await ReadOnly()
        if dut.contention.value.is_resolvable and int(dut.contention.value):
            seen.append((now_ns(), int(dut.contention.value)))


async def visit(chip, role, limit):
    """One poll-then-burst visit to an engine, at most `limit` words each
    way. Returns whether it moved anything."""
    txl, rxl = await chip.levels(role.engine)
    if rxl:
        role.rx += await chip.pop(role.engine, min(rxl, limit))
    words = role.tx[role.sent:role.sent + min(role.depth - txl, limit)]
    if words:
        await chip.push(role.engine, words)
        role.sent += len(words)
    return bool(rxl or words)


async def serve(chip, roles, timeout_ns, burst=8):
    """The Host, serving the engines in turn. When a role is urgent, the
    other one moves one word per visit and the urgent one is visited after
    it, which is the schedule a Host with a deadline would use. `burst`
    limits the words an urgent visit moves each way."""
    deadline = now_ns() + timeout_ns
    urgent = [r for r in roles if r.urgent]
    while any(r.busy() for r in roles):
        progress = False
        for r in roles:
            progress |= await visit(chip, r, burst if r.urgent or not urgent else 1)
            if not r.urgent:
                for u in urgent:
                    progress |= await visit(chip, u, burst)
        if not progress:
            await chip.cycles(20)
        if now_ns() > deadline:
            state = {r.name: (r.sent, len(r.tx), len(r.rx), r.n_rx) for r in roles}
            raise TimeoutError(f"Host service timed out: {state}")


async def drain(chip, roles):
    """Words that arrived after the last round of `serve`."""
    for r in roles:
        _, rxl = await chip.levels(r.engine)
        if rxl:
            r.rx += await chip.pop(r.engine, rxl)


async def start(chip, roles, running=0):
    """Starts `roles` together, beside any engines in the mask `running`."""
    for r in roles:
        r.arm()
        await r.prefill()
    await chip.enable(running | sum(1 << r.engine for r in roles))
    for r in roles:
        r.go()


async def run_pair(dut, classes, options=(None, None), hz=PROFILE, engines=(0, 1), sequential=False,
                   staggered=False, fault=None, burst=8):
    """Runs two roles at once and returns the overlap report. Raises with
    every problem of both protocols, each prefixed with its role's name.
    `staggered` loads and starts the second role while the first runs;
    `sequential` is the control for the overlap check; `fault(chip)`, a
    coroutine, runs beside the pair."""
    chip = Chip(dut, hz)
    await chip.start()
    await chip.set_quad()
    plans, offsets = allocate(classes), place(classes)
    window = max(c.min_window_ns for c in classes)
    roles = [c(chip, e, o, p, window, **(kw or {}))
             for c, e, o, p, kw in zip(classes, engines, offsets, plans, options)]
    pull = 0
    for r in roles:
        r.prepare()
        pull |= r.pull
    chip.pull_up(pull)
    contention = []
    cocotb.start_soon(watch_contention(dut, contention))
    span = window * 6 + 2_000_000
    if staggered:
        first, second = roles
        await first.load()
        await first.configure()
        await start(chip, [first])
        await queue_first(chip, first, 4)
        await second.load()
        await second.configure()
        await start(chip, [second], running=1 << first.engine)
    else:
        for r in roles:
            await r.load()
        for r in roles:
            await r.configure()
        assert await chip.read8(regs.STICKY) == 0, "sticky error after configuration"
        if not sequential:
            await start(chip, roles)
    if fault:
        cocotb.start_soon(fault(chip))
    if sequential:
        for r in roles:
            await start(chip, [r])
            await serve(chip, [r], span)
            await r.settle()
            await drain(chip, [r])
            await chip.enable(0)
    else:
        await serve(chip, roles, span, burst)
        for r in roles:
            await r.settle()
        await drain(chip, roles)
    problems = []
    for r in roles:
        problems += [f"{r.name}: {p}" for p in r.check()[:3]]
    flags, sticky = await chip.read8(regs.FLAGS), await chip.read8(regs.STICKY)
    if any(isinstance(r, UartRx) for r in roles) and flags & 0x10:
        problems.append("UartRx: framing error flag")
    if sticky:
        problems.append(f"sticky errors {sticky:#04x}")
    if contention:
        problems.append(f"contention on the pins: {contention[:3]}")
    assert not problems, "; ".join(problems)
    report = overlap(*roles)
    dut._log.info(f"PAIR {roles[0].name}@e{roles[0].engine}+{roles[1].name}@e{roles[1].engine} "
                  f"offsets {offsets}: window {report['window_us']:.1f} us, edges {report['edges']}, "
                  f"switches {report['switches']}")
    return report


async def queue_first(chip, role, n):
    """Queue the first n transmit words of a role."""
    words = role.tx[:n]
    await chip.push(role.engine, words)
    role.sent = len(words)


async def rejected(session):
    """Negative control: the session must fail; returns its message."""
    try:
        await session
    except (AssertionError, TimeoutError) as e:
        return str(e)
    raise AssertionError("the control passed")


# ----------------------------------------------------------------- the pairs
@cocotb.test()
async def test_uart_tx_with_i2c(dut):
    """115200 baud beside Fast-mode I2C, with the target stretching SCL."""
    await run_pair(dut, (UartTx, I2c))


@cocotb.test()
async def test_uart_tx_with_i2c_standard_mode(dut):
    """Standard-mode I2C, stretched by 7 us, on engine 0 this time."""
    await run_pair(dut, (I2c, UartTx), ({"speed": 100_000, "stretch_ns": 7000}, None))


@cocotb.test()
async def test_uart_tx_fastest_with_i2c(dut):
    """3.125 Mbaud, the fastest rate of the program, beside I2C."""
    await run_pair(dut, (UartTx, I2c), ({"baud": 3_125_000}, None))


@cocotb.test()
async def test_uart_rx_with_spi(dut):
    await run_pair(dut, (UartRx, Spi))


@cocotb.test()
async def test_uart_rx_fastest_with_spi_fastest(dut):
    """3.125 Mbaud in, 12.5 MHz SCK with the MISO bypass."""
    await run_pair(dut, (UartRx, Spi), ({"baud": 3_125_000}, {"rate": 12_500_000}))


@cocotb.test()
async def test_capture_with_uart_tx(dut):
    await run_pair(dut, (Capture, UartTx))


@cocotb.test()
async def test_capture_with_uart_tx_engines_swapped(dut):
    await run_pair(dut, (Capture, UartTx), engines=(1, 0))


@cocotb.test()
async def test_start_beside_running_uart_tx(dut):
    """I2C is loaded into the same memory page, configured and started while
    UART frames are on the wire: every UART edge stays on its bit grid."""
    await run_pair(dut, (UartTx, I2c), staggered=True)


@cocotb.test()
async def test_capture_of_uart_tx_own_line(dut):
    await run_pair(dut, (UartTx, CaptureLine), (None, {"line": 0}))


@cocotb.test()
async def test_capture_of_i2c_own_clock(dut):
    """SCL at 100 kHz, an edge every 5 us or so."""
    await run_pair(dut, (I2c, CaptureLine), ({"speed": 100_000, "stretch_ns": 0}, {"line": 1}))


# Every pair of single-engine programs whose pins fit. A pair left out has a
# reason in docs/concurrency.md.
ROLES = (UartTx, UartRx, Spi, I2c, Jtag, Swd, Ps2Device, Ps2Host, CanRx, EthTx, EthRx, Capture, Replay)
NOT_SWEPT = {
    (CanRx, EthRx),      # one G2
    (Jtag, Swd),         # both peers hard-code pins 0 and 2
    (Ps2Device, Ps2Host),  # both programs hard-code pins 3 and 4
    (EthTx, EthRx),      # measured failure: test_limit_eth_full_duplex
    (EthRx, Capture),    # measured failure: test_limit_ethrx_with_capture_bursts
}
SWEEP = [p for p in itertools.combinations(ROLES, 2) if p not in NOT_SWEPT]


# PE_SWAP_ENGINES=1 runs every pair of the sweep on the opposite engines.
SWEEP_ENGINES = (1, 0) if os.environ.get("PE_SWAP_ENGINES") else (0, 1)


def sweep_test(a, b):
    async def run(dut):
        await run_pair(dut, (a, b), engines=SWEEP_ENGINES)
    run.__name__ = run.__qualname__ = f"test_pair_{a.__name__}_{b.__name__}".lower()
    return cocotb.test()(run)


for _a, _b in SWEEP:
    globals()[f"test_pair_{_a.__name__}_{_b.__name__}".lower()] = sweep_test(_a, _b)
del _a, _b


# ------------------------------------------------------------- measured limits
@cocotb.test()
async def test_limit_eth_full_duplex(dut):
    """Measured failure, Host port bandwidth: 10 Mbit/s each way moves a word
    each way every 80 cycles, and the transmit queue runs dry inside a frame.
    Shorter bursts spend more of the port on polling."""
    for burst in (8, 4):
        msg = await rejected(run_pair(dut, (EthTx, EthRx), burst=burst))
        assert "EthTx: timing: Manchester" in msg, msg


@cocotb.test()
async def test_limit_ethrx_with_capture_bursts(dut):
    """Measured failure, Host port load: Ethernet receive takes about 60% of
    the port, and edges fill the 8-word capture queue in what is left."""
    msg = await rejected(run_pair(dut, (EthRx, Capture)))
    assert "Capture: capture:" in msg and "sticky errors 0x02" in msg, msg


@cocotb.test()
async def test_ethrx_with_capture_lighter_traffic(dut):
    """The same pair passes with about 70% of those edges. Capture on engine 1,
    with no change at the chip: the limit is the Host's, not a shared block's."""
    await run_pair(dut, (EthRx, Capture), (None, {"gap": (800, 1600)}))


@cocotb.test()
async def test_limit_capture_of_can_bus(dut):
    """Measured failure, Host port load: at 1 Mbit/s a recording of the bus
    needs two words about every 100 cycles, and the capture queue overflows.
    The receiver beside it keeps every frame, through the capture filter."""
    msg = await rejected(run_pair(dut, (CanRx, CaptureLine), (None, {"line": 8})))
    assert "sticky errors 0x02" in msg and "CaptureLine: capture:" in msg and "CanRx:" not in msg, msg


# ------------------------------------------------------------ negative controls
@cocotb.test()
async def test_control_uart_peer_wrong_rate(dut):
    """The UART peer expects 8% more baud; only the UART may fail."""
    msg = await rejected(run_pair(dut, (UartTx, I2c), ({"peer_baud": 125_000}, None)))
    assert "UartTx: timing:" in msg and "I2c:" not in msg, msg


@cocotb.test()
async def test_control_i2c_peer_wrong_rate(dut):
    """A Standard-mode target rejects Fast-mode timing; the UART stays clean."""
    msg = await rejected(run_pair(dut, (UartTx, I2c), (None, {"peer_speed": 100_000})))
    assert "I2c: timing:" in msg and "UartTx:" not in msg, msg


@cocotb.test()
async def test_control_uart_rx_peer_wrong_rate(dut):
    """A sender 8% fast garbles the Host's bytes; SPI stays clean."""
    msg = await rejected(run_pair(dut, (UartRx, Spi), ({"peer_baud": 125_000}, None)))
    assert "UartRx:" in msg and "Spi:" not in msg, msg


@cocotb.test()
async def test_control_spi_peer_wrong_rate(dut):
    msg = await rejected(run_pair(dut, (UartRx, Spi), (None, {"peer_rate": 5_000_000})))
    assert "Spi: timing: clock period" in msg and "UartRx:" not in msg, msg


@cocotb.test()
async def test_control_wrong_clock(dut):
    """At 40 MHz both protocols of each required pair break, each in its own
    check."""
    for pair, names in (((UartTx, I2c), ("UartTx:", "I2c:")), ((UartRx, Spi), ("UartRx:", "Spi:")),
                        ((Capture, UartTx), ("Capture:", "UartTx:"))):
        msg = await fails_at_wrong_clock(lambda hz: run_pair(dut, pair, hz=hz), PROFILE)
        assert all(n in msg for n in names), msg


@cocotb.test()
async def test_control_sequential_is_not_overlap(dut):
    """One protocol after the other passes every protocol check, and the
    overlap check alone rejects it."""
    msg = await rejected(run_pair(dut, (UartTx, I2c), sequential=True))
    assert msg.startswith("no overlap"), msg


@cocotb.test()
async def test_control_contention_is_seen(dut):
    async def fight(chip):
        await sleep_ns(100_000)
        chip.drive_uio(0, 1)
        chip.drive_uio(0, None)
        chip.drive_uio(0, 0)
        await sleep_ns(40_000)
        chip.drive_uio(0, None)
    msg = await rejected(run_pair(dut, (UartTx, I2c), fault=fight))
    assert "contention on the pins" in msg, msg


@cocotb.test()
async def test_control_capture_loss_is_reported(dut):
    """Edges faster than the shared Host drains them: the loss must show as
    a sticky error and a short recording, never as a clean run."""
    msg = await rejected(run_pair(dut, (Capture, UartTx), ({"gap": (24, 30)}, None)))
    assert "sticky errors" in msg and "Capture:" in msg, msg
