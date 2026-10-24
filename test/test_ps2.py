# SPDX-License-Identifier: Apache-2.0
"""PS/2 device and host programs against independent PS/2 peers in absolute
time at the 50 MHz clock profile. The bounded-wait cases (design review
B11) run the host program against an absent device and a device that stops
clocking, and the device program against a host that inhibits it."""

import cocotb

from chip import MHZ_50, Chip, fails_at_wrong_clock, regs
from pe.proto import ps2
from peers import Wave, now_ns, sleep_ns

PROFILE = MHZ_50
DEVICE_CLKDIV = 249  # 5 us ticks
HOST_CLKDIV = 199    # 4 us ticks

CLK, DATA = 3, 4
US = 1000  # ns

# Limits from the PS/2 interface specification, in ns.
PHASE = (30 * US, 50 * US)        # each CLK low or high phase of a device clock
DATA_TO_FALL = (5 * US, 25 * US)  # device DATA change to the next CLK falling edge
RISE_TO_DATA = 5 * US             # CLK rising edge to a device DATA change
HOST_SETUP = 5 * US               # host DATA change to the next CLK rising edge
INHIBIT = 100 * US                # host CLK low before a request to send
FIRST_CLOCK = 15_000 * US         # request to send to the device's first clock
PACKET = 2_000 * US               # request to send to the end of the acknowledge
STALL = 100 * US                  # a peer waiting this long for an edge reports it


def frame_bits(byte):
    """Data, odd parity, stop."""
    bits = [(byte >> i) & 1 for i in range(8)]
    return bits + [1 - (sum(bits) & 1), 1]


def parse(bits):
    """Data, parity and stop bits -> (byte, parity and stop correct)."""
    byte = sum(b << i for i, b in enumerate(bits[:8]))
    return byte, bits == frame_bits(byte)


async def until(wave, level, timeout_ns=None):
    """Wait until the line is at `level`; returns the time, or None after
    timeout_ns."""
    deadline = None if timeout_ns is None else now_ns() + timeout_ns
    while wave.read() != level:
        left = None if deadline is None else deadline - now_ns()
        if await wave.change(left) is None:
            return None
    return now_ns()


def phase_violations(falls, rises):
    """Device clock phases: falls[i] < rises[i] < falls[i + 1]."""
    out = []
    for i, (f, r) in enumerate(zip(falls, rises)):
        if not PHASE[0] <= r - f <= PHASE[1]:
            out.append(f"timing: CLK low {(r - f) / US:.1f} us in clock {i + 1} at {f:.0f} ns")
    for i, (r, f) in enumerate(zip(rises, falls[1:])):
        if not PHASE[0] <= f - r <= PHASE[1]:
            out.append(f"timing: CLK high {(f - r) / US:.1f} us after clock {i + 1} at {r:.0f} ns")
    return out


class Lines:
    def __init__(self, chip):
        self.chip = chip
        self.clk = Wave(chip.dut.uio_in, CLK)
        self.data = Wave(chip.dut.uio_in, DATA)
        self.violations = []

    def pull(self, pin, low):
        self.chip.drive_uio(pin, 0 if low else None)

    def stalled(self, what):
        self.violations.append(f"stalled: {what} at {now_ns():.0f} ns")
        self.pull(CLK, False)
        self.pull(DATA, False)


class HostModel(Lines):
    """PS/2 host peer for the device program. Reads device frames on CLK
    falling edges and checks their clock phases and DATA timing. Sends a
    command with a 110 us inhibit and a request to send, changing DATA 10 us
    after each CLK falling edge, and checks the device's response times.
    `hold_clk(True)` inhibits the bus until `hold_clk(False)`;
    `inhibit_after = n` holds CLK low for 110 us once the n-th clock of the
    next device frame rises, which aborts the frame."""

    def __init__(self, chip):
        super().__init__(chip)
        self.frames, self.frame_starts, self.acks, self.to_send = [], [], [], []
        self.aborted = 0
        self.inhibit_after = None
        self.holding = False

    def hold_clk(self, on):
        self.holding = on
        self.pull(CLK, on)

    async def _receive(self, t0):
        falls, rises, bits = [t0], [], [self.data.read()]
        while len(falls) < 11:
            t = await until(self.clk, 1, STALL)
            if t is None:
                return self.stalled("device frame, CLK low")
            rises.append(t)
            if self.inhibit_after == len(rises):
                self.inhibit_after = None
                self.aborted += 1
                self.pull(CLK, True)
                await sleep_ns(110 * US)
                if not self.data.read():
                    self.violations.append(f"device held DATA low through an inhibit at {now_ns():.0f} ns")
                self.pull(CLK, False)
                return
            t = await until(self.clk, 0, STALL)
            if t is None:
                return self.stalled("device frame, CLK high")
            falls.append(t)
            bits.append(self.data.read())
        t = await until(self.clk, 1, STALL)
        if t is None:
            return self.stalled("device frame, last clock")
        rises.append(t)
        byte, ok = parse(bits[1:])
        self.frames.append((byte, ok and bits[0] == 0))
        self.frame_starts.append(t0)
        out = phase_violations(falls, rises)
        start = self.data.last_edge(t0)
        if start is None or not DATA_TO_FALL[0] <= t0 - start <= DATA_TO_FALL[1]:
            out.append(f"timing: start bit DATA change {start} ns, CLK falling at {t0:.0f} ns")
        for t in self.data.edges(t0, rises[-1]):
            i = sum(r < t for r in rises) - 1  # the high phase after clock i + 1
            if i < 0 or i + 1 >= len(falls) or falls[i + 1] < t:
                out.append(f"timing: DATA changed while CLK low at {t:.0f} ns")
            elif t - rises[i] < RISE_TO_DATA or not DATA_TO_FALL[0] <= falls[i + 1] - t <= DATA_TO_FALL[1]:
                out.append(f"timing: DATA changed {(t - rises[i]) / US:.1f} us after CLK rising, "
                           f"{(falls[i + 1] - t) / US:.1f} us before CLK falling at {t:.0f} ns")
        self.violations += out

    async def _send(self, byte):
        self.pull(CLK, True)
        await sleep_ns(110 * US)
        self.pull(DATA, True)
        await sleep_ns(10 * US)
        self.pull(CLK, False)
        t_rts = now_ns()
        await sleep_ns(US)
        t = await until(self.clk, 0, FIRST_CLOCK)
        if t is None:
            return self.stalled("no clock after a request to send")
        falls, rises = [t], []
        for b in frame_bits(byte):
            await sleep_ns(10 * US)
            self.pull(DATA, not b)
            t = await until(self.clk, 1, STALL)
            if t is None:
                return self.stalled("command, CLK low")
            rises.append(t)
            t = await until(self.clk, 0, STALL)
            if t is None:
                return self.stalled("command, CLK high")
            falls.append(t)
        self.acks.append(self.data.read() == 0)
        t = await until(self.clk, 1, STALL)
        t_end = await until(self.data, 1, STALL)
        if t is None or t_end is None:
            return self.stalled("acknowledge")
        rises.append(t)
        out = phase_violations(falls, rises)
        if falls[0] - t_rts > FIRST_CLOCK:
            out.append(f"timing: first clock {(falls[0] - t_rts) / US:.0f} us after the request to send")
        if t_end - t_rts > PACKET:
            out.append(f"timing: command took {(t_end - t_rts) / US:.0f} us")
        self.violations += out

    async def run(self):
        while True:
            if self.to_send and not self.holding and self.clk.read() and self.data.read():
                await self._send(self.to_send.pop(0))
                continue
            if await self.clk.change(10 * US) == 0 and not self.holding:
                await self._receive(now_ns())


class DeviceModel(Lines):
    """PS/2 device peer for the host program: clocks at 12.5 kHz (40 us low,
    40 us high), changes DATA 20 us before each falling edge, reads the host's
    DATA at each CLK rising edge and acknowledges commands; answers 0xFF
    with 0xFA, 0xAA and other commands with 0xFA. Checks that the host
    holds CLK low for at least 100 us before a request to send and changes
    DATA only while CLK is low, at least 5 us before it rises.
    `stop_after = n` stops clocking after n clocks of the next frame or
    command, as a device unplugged mid-transfer."""

    def __init__(self, chip):
        super().__init__(chip)
        self.commands, self.to_send = [], []
        self.stop_after = None

    def _stop(self, n):
        if self.stop_after == n:
            self.stop_after = None
            self.pull(DATA, False)
            return True
        return False

    async def _clock(self, high_ns):
        """One clock, 40 us low; returns the time CLK rose."""
        self.pull(CLK, True)
        await sleep_ns(40 * US)
        self.pull(CLK, False)
        t = now_ns()
        await sleep_ns(high_ns)
        return t

    async def _send(self, byte):
        for n, b in enumerate([0] + frame_bits(byte)):
            self.pull(DATA, not b)
            await sleep_ns(20 * US)
            await self._clock(20 * US)
            if self._stop(n + 1):
                return
        self.pull(DATA, False)

    async def _command(self, t_fall):
        t_rise = await until(self.clk, 1)
        requests = self.data.edges(t_fall, t_rise, level=0)
        if not requests or self.data.read():
            return  # an inhibit without a request to send
        t_rts = requests[-1]
        if t_rts - t_fall < INHIBIT:
            self.violations.append(f"timing: inhibit {(t_rts - t_fall) / US:.1f} us before the request to send")
        await sleep_ns(100 * US)
        t0, bits, rises = now_ns(), [], []
        for n in range(10):
            rises.append(await self._clock(40 * US if n < 9 else 20 * US))
            bits.append(self.data.at(rises[-1]))
            if self._stop(n + 1):
                return
        self.pull(DATA, True)  # acknowledge
        await sleep_ns(20 * US)
        await self._clock(20 * US)
        self.pull(DATA, False)
        for t in self.data.edges(t0, rises[-1]):
            nxt = next((r for r in rises if r > t), None)
            if self.clk.at(t) or nxt is None or nxt - t < HOST_SETUP:
                self.violations.append(f"timing: host DATA changed at {t:.0f} ns, CLK "
                                       f"{'high' if self.clk.at(t) else 'low'}")
        byte, ok = parse(bits)
        self.commands.append((byte, ok))
        if ok:
            self.to_send += [0xFA, 0xAA] if byte == 0xFF else [0xFA]

    async def run(self):
        while True:
            if not self.clk.read():  # the host inhibits, perhaps during the end of a frame
                await self._command(self.clk.last_edge(now_ns()))
            elif self.to_send and self.data.read():
                await self._send(self.to_send.pop(0))
            else:
                await self.clk.change(10 * US)


def cycles(chip, ns):
    return int(ns * chip.hz / 1e9)


async def setup(dut, name, clkdiv, hz):
    chip = Chip(dut, hz)
    await chip.start()
    await chip.set_quad()
    chip.pull_up((1 << CLK) | (1 << DATA))
    prog = await chip.load_program(name)
    await chip.setup_engine(0, prog, clkdiv=clkdiv, shiftctrl=regs.OUT_RIGHT | regs.IN_RIGHT,
                            out_base=DATA, set_base=DATA, in_base=DATA, jmp_pin=CLK,
                            side_base=CLK, statuscfg=1)
    await chip.force_asm(0, "set pins, 1 side 1", side_count=1, side_opt=True)
    await chip.pincfg(CLK, regs.DRIVE_OPEN_DRAIN)
    await chip.pincfg(DATA, regs.DRIVE_OPEN_DRAIN)
    return chip


async def wait_for(cond, timeout_ns, what):
    deadline = now_ns() + timeout_ns
    while not cond():
        if now_ns() > deadline:
            raise TimeoutError(what)
        await sleep_ns(20 * US)


# ------------------------------------------------------- chip as device
async def device_setup(dut, hz=PROFILE):
    chip = await setup(dut, "ps2_device", DEVICE_CLKDIV, hz)
    host = HostModel(chip)
    cocotb.start_soon(host.run())
    await chip.enable(1)
    return chip, host


async def keyboard_session(dut, hz=PROFILE):
    chip, host = await device_setup(dut, hz)
    scan = [0x1C, 0xF0, 0x1C]  # "A" pressed and released
    await chip.push(0, [ps2.device_frame(b) for b in scan])
    await wait_for(lambda: len(host.frames) == 3 or host.violations, 6_000 * US, "scan codes")
    assert not host.violations, host.violations[:5]
    assert host.frames == [(b, True) for b in scan], host.frames
    # The host sends "set LEDs" (0xED); the chip receives and acknowledges it.
    host.to_send.append(0xED)
    (word,) = await chip.pop_wait(0, 1, timeout=cycles(chip, 3_000 * US))
    assert ps2.decode(word) == (0xED, True, True), hex(word)
    await wait_for(lambda: host.acks, 500 * US, "acknowledge")
    assert host.acks == [True]
    # The keyboard answers 0xFA.
    await chip.push(0, [ps2.device_frame(0xFA)])
    await wait_for(lambda: len(host.frames) == 4, 2_000 * US, "0xFA")
    assert host.frames[3] == (0xFA, True)
    assert not host.violations, host.violations[:5]


@cocotb.test()
async def test_ps2_chip_as_keyboard(dut):
    await keyboard_session(dut)


@cocotb.test()
async def test_ps2_device_inhibited(dut):
    """An inhibited bus defers a frame until CLK has been released for
    50 us; an inhibit mid-frame aborts it with DATA released and 0xFFFF
    reported, and the Host sends the frame again."""
    chip, host = await device_setup(dut)
    host.hold_clk(True)
    t0 = now_ns()
    await chip.push(0, [ps2.device_frame(0x1C)])
    await sleep_ns(1_000 * US)
    assert not host.data.edges(t0), "the device sent while inhibited"
    host.hold_clk(False)
    t_release = now_ns()
    await wait_for(lambda: host.frames, 2_000 * US, "frame after the inhibit")
    assert host.frames == [(0x1C, True)], host.frames
    assert host.frame_starts[0] - t_release >= 50 * US, host.frame_starts[0] - t_release

    host.inhibit_after = 4
    await chip.push(0, [ps2.device_frame(0x5A)])
    (word,) = await chip.pop_wait(0, 1, timeout=cycles(chip, 2_000 * US))
    assert ps2.failed(word), hex(word)
    assert host.aborted == 1 and len(host.frames) == 1, (host.aborted, host.frames)
    await chip.push(0, [ps2.device_frame(0x5A)])
    await wait_for(lambda: len(host.frames) == 2, 2_000 * US, "frame sent again")
    assert host.frames[1] == (0x5A, True), host.frames
    assert not host.violations, host.violations[:5]


@cocotb.test()
async def test_ps2_device_wrong_clock(dut):
    """At 30 MHz the 50 MHz settings stretch each clock phase to 67 us."""
    msg = await fails_at_wrong_clock(lambda hz: keyboard_session(dut, hz), PROFILE, 30_000_000)
    assert "timing: CLK" in msg, msg


# --------------------------------------------------------- chip as host
async def host_setup(dut, hz=PROFILE, device=True):
    chip = await setup(dut, "ps2_host", HOST_CLKDIV, hz)
    kbd = DeviceModel(chip) if device else None
    if kbd:
        cocotb.start_soon(kbd.run())
    await chip.enable(1)
    return chip, kbd


async def host_session(dut, hz=PROFILE):
    chip, kbd = await host_setup(dut, hz)
    kbd.to_send.append(0xAA)  # self-test passed
    (word,) = await chip.pop_wait(0, 1, timeout=cycles(chip, 2_000 * US))
    assert ps2.decode(word) == (0xAA, True, True), hex(word)
    # Reset command: acknowledged on the wire, then 0xFA and 0xAA.
    await chip.push(0, [ps2.host_frame(0xFF)])
    ack, fa, aa = await chip.pop_wait(0, 3, timeout=cycles(chip, 5_000 * US))
    assert ps2.acked(ack), hex(ack)
    assert kbd.commands == [(0xFF, True)]
    assert ps2.decode(fa) == (0xFA, True, True)
    assert ps2.decode(aa) == (0xAA, True, True)
    assert not kbd.violations, kbd.violations[:5]


@cocotb.test()
async def test_ps2_chip_as_host(dut):
    await host_session(dut)


@cocotb.test()
async def test_ps2_host_absent_device(dut):
    """With no device, a command's request to send times out after 4096
    ticks (16.4 ms; a device must clock within 15 ms): 0xFFFF, both lines
    released. A device connected afterwards gets the next command."""
    chip, _ = await host_setup(dut, device=False)
    clk, data = Wave(dut.uio_in, CLK), Wave(dut.uio_in, DATA)
    await chip.push(0, [ps2.host_frame(0xF4)])
    (word,) = await chip.pop_wait(0, 1, timeout=cycles(chip, 20_000 * US))
    assert ps2.failed(word), hex(word)
    (t_rts,) = data.edges(level=0)
    (t_release,) = data.edges(t_rts, level=1)
    assert FIRST_CLOCK <= t_release - t_rts <= 17_000 * US, t_release - t_rts
    assert clk.read() and data.read(), "lines released"

    kbd = DeviceModel(chip)
    cocotb.start_soon(kbd.run())
    await chip.push(0, [ps2.host_frame(0xF4)])
    ack, fa = await chip.pop_wait(0, 2, timeout=cycles(chip, 5_000 * US))
    assert ps2.acked(ack), hex(ack)
    assert kbd.commands == [(0xF4, True)]
    assert ps2.decode(fa) == (0xFA, True, True)
    assert not kbd.violations, kbd.violations[:5]


@cocotb.test()
async def test_ps2_host_device_stops(dut):
    """A device that stops clocking mid-frame or mid-command makes the
    program report 0xFFFF and release the lines instead of hanging; the
    transfers that follow succeed."""
    chip, kbd = await host_setup(dut)
    kbd.stop_after = 4
    kbd.to_send.append(0x1C)
    (word,) = await chip.pop_wait(0, 1, timeout=cycles(chip, 2_000 * US))
    assert ps2.failed(word), hex(word)
    kbd.to_send.append(0x1C)
    (word,) = await chip.pop_wait(0, 1, timeout=cycles(chip, 2_000 * US))
    assert ps2.decode(word) == (0x1C, True, True), hex(word)

    kbd.stop_after = 5
    await chip.push(0, [ps2.host_frame(0xED)])
    (word,) = await chip.pop_wait(0, 1, timeout=cycles(chip, 2_000 * US))
    assert ps2.failed(word), hex(word)
    assert kbd.commands == [] and kbd.clk.read() and kbd.data.read()
    await chip.push(0, [ps2.host_frame(0xED)])
    ack, fa = await chip.pop_wait(0, 2, timeout=cycles(chip, 5_000 * US))
    assert ps2.acked(ack), hex(ack)
    assert kbd.commands == [(0xED, True)]
    assert ps2.decode(fa) == (0xFA, True, True)
    assert not kbd.violations, kbd.violations[:5]


@cocotb.test()
async def test_ps2_host_wrong_clock(dut):
    """At 60 MHz the 50 MHz settings inhibit for only 90 us."""
    msg = await fails_at_wrong_clock(lambda hz: host_session(dut, hz), PROFILE, 60_000_000)
    assert "timing: inhibit" in msg, msg
