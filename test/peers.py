# SPDX-License-Identifier: Apache-2.0
"""Independent protocol peers. They only watch and drive pins, share no
code or state with the programs under test, and keep their own timing in
absolute time, whatever the chip clock: a program run at the wrong clock
fails their checks."""

from bisect import bisect_right

import cocotb
from cocotb.triggers import First, RisingEdge, Timer
from cocotb.utils import get_sim_time


def now_ns():
    return get_sim_time("ps") / 1000


async def sleep_until(t_ns):
    """Wait until absolute time t_ns (no drift when called repeatedly)."""
    ps = round(t_ns * 1000) - get_sim_time("ps")
    if ps > 0:
        await Timer(ps, unit="ps")


async def sleep_ns(ns):
    await sleep_until(now_ns() + ns)


class Wave:
    """Records every change of one bit of a signal with its time in ns, from
    construction on. The bit is read from the resolved signal, so a Wave on
    `uio_in` sees whoever drives the line."""

    def __init__(self, signal, bit):
        self.signal = signal
        self.bit = bit
        self.start = now_ns()
        self.initial = self.read()
        self.times = []
        self.levels = []
        self._task = cocotb.start_soon(self._watch())

    def read(self):
        return (int(self.signal.value) >> self.bit) & 1

    @property
    def level(self):
        return self.levels[-1] if self.levels else self.initial

    async def _watch(self):
        while True:
            await self.signal.value_change
            v = self.read()
            if v != self.level:
                self.times.append(now_ns())
                self.levels.append(v)

    async def change(self, timeout_ns=None):
        """Wait for the next change of the bit; returns the new level, or
        None if `timeout_ns` passed first."""
        level = self.read()
        deadline = None if timeout_ns is None else get_sim_time("ps") + round(timeout_ns * 1000)
        while True:
            if deadline is None:
                await self.signal.value_change
            else:
                left = deadline - get_sim_time("ps")
                if left <= 0:
                    return None
                await First(self.signal.value_change, Timer(left, unit="ps"))
            v = self.read()
            if v != level:
                return v

    def at(self, t):
        """Level at time t, after any change at exactly t."""
        i = bisect_right(self.times, t)
        return self.levels[i - 1] if i else self.initial

    def edges(self, t0=float("-inf"), t1=float("inf"), level=None):
        """Times of the changes in (t0, t1], optionally only those to `level`."""
        lo, hi = bisect_right(self.times, t0), bisect_right(self.times, t1)
        return [t for t, v in zip(self.times[lo:hi], self.levels[lo:hi]) if level is None or v == level]

    def last_edge(self, t):
        """Time of the last change at or before t, or None."""
        i = bisect_right(self.times, t)
        return self.times[i - 1] if i else None

    def next_edge(self, t):
        """Time of the first change after t, or None."""
        i = bisect_right(self.times, t)
        return self.times[i] if i < len(self.times) else None

    def stop(self):
        self._task.cancel()


# ------------------------------------------------------------ timing checks
def clock_violations(clock, rising, rate_hz, tol=0.01, t0=float("-inf"), t1=float("inf")):
    """Check a controller's clock against its nominal rate. A controller may
    pause the clock (between bytes, or while its transmit queue is empty),
    so gaps of 1.5 periods or more are pauses; the periods below that form
    the bursts. Every burst period must be at least the nominal period less
    `tol`, and their median within `tol` of it. Returns (violations, periods
    checked). A clock 25% slow, as in the negative controls, still has its
    periods checked, since they stay below 1.5 periods."""
    period = 1e9 / rate_hz
    ts = clock.edges(t0, t1, level=1 if rising else 0)
    burst = [(a, b - a) for a, b in zip(ts, ts[1:]) if b - a < 1.5 * period]
    out = [f"timing: clock period {d:.1f} ns at {a:.0f} ns, nominal {period:.1f} ns"
           for a, d in burst if d < (1 - tol) * period]
    if burst:
        median = sorted(d for _, d in burst)[len(burst) // 2]
        if abs(median - period) > tol * period:
            out.append(f"timing: clock period {median:.1f} ns (median), nominal {period:.1f} ns")
    return out, len(burst)


def setup_hold_violations(data, sample_times, t_su, t_h, name="data"):
    """Each sample needs `data` stable t_su before and t_h after it."""
    out = []
    for ts in sample_times:
        before, after = data.last_edge(ts), data.next_edge(ts)
        if before is not None and ts - before < t_su:
            out.append(f"timing: {name} setup {ts - before:.1f} ns before the edge at {ts:.0f} ns")
        if after is not None and after - ts < t_h:
            out.append(f"timing: {name} hold {after - ts:.1f} ns after the edge at {ts:.0f} ns")
    return out


# ------------------------------------------------------------------- UART
def uart_decode(line, baud, t_end, stop=1, tol=0.01, jitter=0.02):
    """Decode 8N1 frames from a Wave of an idle-high line, up to t_end.
    Each frame is sampled at the middle of its bits, timed from the falling
    edge of its start bit. Every edge inside a frame must be k bit times
    after that edge within tol * k + jitter bit times (1% rate error plus
    2% of a bit). Returns (bytes, violations)."""
    bit = 1e9 / baud
    data, out = [], []
    t = float("-inf")
    for te in line.edges(level=0):
        if te < t:
            continue
        end = te + 9.5 * bit
        if end > t_end:
            break
        if line.at(te - 1e-3) != 1:
            continue
        bits = [line.at(te + (i + 0.5) * bit) for i in range(10)]
        for e in line.edges(te, end):
            k = round((e - te) / bit)
            dev = e - te - k * bit
            if abs(dev) > (tol * k + jitter) * bit:
                out.append(f"timing: UART edge {k} bits after a start edge is {dev:+.1f} ns off")
        if bits[0] != 0:
            out.append(f"UART start bit high at {te:.0f} ns")
        if bits[9] != stop:
            out.append(f"UART stop bit {bits[9]} at {te:.0f} ns")
        data.append(sum(b << i for i, b in enumerate(bits[1:9])))
        t = end
    return data, out


async def uart_receive(line, baud, nbytes, timeout_ns=10e6):
    """Wait for nbytes frames on a Wave; returns (bytes, violations)."""
    deadline = now_ns() + timeout_ns
    while True:
        data, out = uart_decode(line, baud, now_ns())
        if len(data) >= nbytes:
            return data[:nbytes], out
        if now_ns() > deadline:
            raise TimeoutError(f"UART: received {data}")
        await sleep_ns(1e9 / baud)


async def uart_transmit(set_line, baud, data, stop=1, idle_bits=2):
    """Send 8N1 frames at exactly `baud`."""
    bit = 1e9 / baud
    t = now_ns()
    for b in data:
        for level in [0] + [(b >> i) & 1 for i in range(8)] + [stop]:
            set_line(level)
            t += bit
            await sleep_until(t)
        set_line(1)
        t += idle_bits * bit
        await sleep_until(t)


# -------------------------------------------------------------------- SPI
class SpiPeripheral:
    """SPI peripheral in mode 0-3, most significant bit first. CPOL is the
    SCK idle level; with CPHA 0 it samples MOSI on the leading edge and
    changes MISO on the trailing one, with CPHA 1 the other way round.
    MISO changes `tco_ns` after its edge, like a real device's
    clock-to-output time. `check()` reports SCK periods off the nominal
    rate and MOSI setup or hold below the minimums."""

    def __init__(self, sck, mosi, set_miso, mode, miso_bytes, rate_hz,
                 tco_ns=8.0, t_su=10.0, t_h=10.0):
        self.sck, self.mosi, self.set_miso = sck, mosi, set_miso
        self.cpol, self.cpha = mode >> 1, mode & 1
        self.miso_bits = [(b >> (7 - i)) & 1 for b in miso_bytes for i in range(8)]
        self.rate_hz, self.tco_ns, self.t_su, self.t_h = rate_hz, tco_ns, t_su, t_h
        self.samples = []

    async def _miso_later(self, bit):
        await sleep_ns(self.tco_ns)
        self.set_miso(bit)

    async def run(self, nbits):
        """Returns the MOSI bytes of nbits clocks."""
        out = iter(self.miso_bits + [0] * nbits)
        if not self.cpha:
            self.set_miso(next(out))  # the first bit is out before the first edge
        got = []
        assert self.sck.read() == self.cpol, f"SCK must idle at CPOL = {self.cpol}"
        while len(got) < nbits:
            level = await self.sck.change()
            leading = level != self.cpol
            if leading != bool(self.cpha):
                self.samples.append(now_ns())
                got.append(self.mosi.read())
            elif self.cpha or got:
                cocotb.start_soon(self._miso_later(next(out)))
        return [sum(got[8 * i + j] << (7 - j) for j in range(8)) for i in range(nbits // 8)]

    def check(self):
        sample_rising = (self.cpol == 0) != bool(self.cpha)
        out, checked = clock_violations(self.sck, sample_rising, self.rate_hz)
        assert checked, "no SCK period to check"
        return out + setup_hold_violations(self.mosi, self.samples, self.t_su, self.t_h, "MOSI")


# UM10204 Table 10 minimums in ns, and the maximum SCL frequency.
I2C_TIMING = {
    100_000: dict(hd_sta=4000, low=4700, high=4000, su_sta=4700, su_dat=250, su_sto=4000, buf=4700),
    400_000: dict(hd_sta=600, low=1300, high=600, su_sta=600, su_dat=100, su_sto=600, buf=1300),
}


class I2CTarget:
    """I2C target with a 256-byte memory on open-drain lines: a write's first
    data byte sets the address pointer, further bytes are stored and reads
    return bytes from the pointer. It ACKs its own address only, stretches
    SCL after every acknowledge clock, and checks the bus timing against
    UM10204 in absolute time, whatever the chip clock."""

    def __init__(self, chip, sda_bit, scl_bit, address, stretch_ns=0, speed=100_000):
        self.chip = chip
        self.sda_bit = sda_bit
        self.scl_bit = scl_bit
        self.address = address
        self.stretch_ns = stretch_ns
        self.speed = speed
        self.limits = I2C_TIMING[speed]
        self.mem = bytearray(256)
        self.ptr = 0
        self.transactions = []   # bytes seen on the bus, per START
        self.starts = 0
        self.stops = 0
        self.violations = []
        self.periods = []        # SCL rising edge to rising edge, ns

    def _lines(self):
        v = self.chip.uio()
        return (v >> self.sda_bit) & 1, (v >> self.scl_bit) & 1

    def _check(self, name, value):
        if value < self.limits[name]:
            self.violations.append((name, value, self.limits[name]))

    def _drive(self, bit):
        self.chip.drive_uio(self.sda_bit, None if bit else 0)

    async def run(self, clk):
        sda_p, scl_p = self._lines()
        t_rise = t_fall = t_sda = t_start = t_stop = None
        active, n, clocked = False, 0, False
        shift, first, matched, reading, nacked, driving = 0, True, False, False, False, False
        cur = None
        while True:
            await RisingEdge(clk)
            now = get_sim_time("ns")
            sda, scl = self._lines()
            if scl and not scl_p:                                   # SCL rises
                if t_fall is not None and active:
                    self._check("low", now - t_fall)
                if t_sda is not None and active:
                    self._check("su_dat", now - t_sda)
                if t_rise is not None and active:
                    self.periods.append(now - t_rise)
                t_rise, t_sda = now, None
                clocked = True
                if active and n < 8 and not reading:
                    shift = (shift << 1) | sda
                elif active and n == 8 and reading:
                    nacked = bool(sda)
            elif scl_p and not scl:                                 # SCL falls
                if t_rise is not None and active:
                    self._check("high", now - t_rise)
                if t_start is not None and t_start > (t_rise or 0):
                    self._check("hd_sta", now - t_start)
                t_fall = now
                if active and clocked:
                    clocked = False
                    if n == 7:
                        if reading:
                            self._drive(1)                          # release for the ACK
                        else:
                            byte = shift & 0xFF
                            cur.append(byte)
                            if first:
                                matched = byte >> 1 == self.address
                                reading_next = bool(byte & 1)
                            elif matched:
                                if first_data:
                                    self.ptr = byte
                                else:
                                    self.mem[self.ptr] = byte
                                    self.ptr = (self.ptr + 1) & 0xFF
                                first_data = False
                            if matched:
                                self._drive(0)                      # ACK
                    elif n == 8:
                        if first:
                            first, first_data, reading = False, True, matched and reading_next
                        elif reading and nacked:
                            reading = False
                            matched = False                         # wait for STOP
                        self._drive(1)
                        if reading:
                            shift = self.mem[self.ptr]
                            cur.append(shift)
                            self.ptr = (self.ptr + 1) & 0xFF
                            self._drive(shift >> 7)
                        if self.stretch_ns:
                            self.chip.drive_uio(self.scl_bit, 0)
                            await Timer(self.stretch_ns, unit="ns")
                            self.chip.drive_uio(self.scl_bit, None)
                            n, shift = 0, shift if reading else 0
                            sda_p, scl_p = self._lines()[0], 0
                            continue
                        shift = shift if reading else 0
                    elif reading:
                        self._drive((shift >> (6 - n)) & 1)
                    n = (n + 1) % 9
            elif scl and sda_p and not sda:                         # START
                if active and t_rise is not None:
                    self._check("su_sta", now - t_rise)
                elif t_stop is not None:
                    self._check("buf", now - t_stop)
                self.starts += 1
                cur = []
                self.transactions.append(cur)
                active, n, clocked, shift, first, reading = True, 0, False, 0, True, False
                t_start = now
            elif scl and not sda_p and sda:                         # STOP
                if t_rise is not None:
                    self._check("su_sto", now - t_rise)
                self.stops += 1
                active, cur, t_stop, reading = False, None, now, False
                self._drive(1)
            elif sda != sda_p and not scl:                          # data change
                if t_fall is not None and now - t_fall <= 0:
                    self.violations.append(("hd_dat", now - t_fall, 1))
                t_sda = now
            sda_p, scl_p = sda, scl
