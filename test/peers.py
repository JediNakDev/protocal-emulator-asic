# SPDX-License-Identifier: Apache-2.0
"""Independent protocol peers. They only watch and drive pins, and share no
code or state with the programs under test."""

from cocotb.triggers import ClockCycles, RisingEdge, Timer
from cocotb.utils import get_sim_time


async def uart_receive(clk, line, bit_cycles, nbytes, timeout=100000):
    """Decode 8N1 frames from line() sampled every cycle. Checks that every
    bit holds steady over its middle half and that the stop bit is high."""
    out = []
    waited = 0
    prev = line()
    while len(out) < nbytes:
        await RisingEdge(clk)
        waited += 1
        if waited > timeout:
            raise TimeoutError(f"UART: received {out}")
        cur = line()
        if prev == 1 and cur == 0:  # start edge
            samples = [cur]
            for _ in range(10 * bit_cycles - bit_cycles // 2):
                await RisingEdge(clk)
                samples.append(line())
            bits = []
            for i in range(10):
                lo, hi = i * bit_cycles + bit_cycles // 4, i * bit_cycles + 3 * bit_cycles // 4
                window = samples[lo:hi]
                assert len(set(window)) == 1, f"bit {i} unstable: {samples}"
                bits.append(window[0])
            assert bits[0] == 0, "start bit"
            assert bits[9] == 1, f"stop bit, frame {bits}"
            out.append(sum(b << i for i, b in enumerate(bits[1:9])))
            cur = line()
        prev = cur
    return out


async def uart_transmit(clk, set_line, bit_cycles, data, stop=1):
    for b in data:
        frame = [0] + [(b >> i) & 1 for i in range(8)] + [stop]
        for bit in frame:
            set_line(bit)
            await ClockCycles(clk, bit_cycles)
        set_line(1)
        await ClockCycles(clk, 2 * bit_cycles)


async def spi_peripheral(clk, sck, mosi, set_miso, miso_bytes, nbits, mode=0):
    """SPI peripheral in mode 0-3, most significant bit first. CPOL is the
    SCK idle level; with CPHA 0 it samples MOSI on the leading edge and
    changes MISO on the trailing one, with CPHA 1 the other way round.
    Returns the MOSI bytes."""
    cpol, cpha = mode >> 1, mode & 1
    miso_bits = [(b >> (7 - i)) & 1 for b in miso_bytes for i in range(8)]
    out = iter(miso_bits + [0] * nbits)
    if not cpha:
        set_miso(next(out))  # the first bit is out before the first edge
    got = []
    prev = sck()
    assert prev == cpol, f"SCK must idle at CPOL = {cpol}"
    while len(got) < nbits:
        await RisingEdge(clk)
        cur = sck()
        if cur != prev:
            leading = prev == cpol
            if leading != bool(cpha):
                got.append(mosi())
            elif cpha or got:
                set_miso(next(out))
        prev = cur
    return [sum(got[8 * i + j] << (7 - j) for j in range(8)) for i in range(nbits // 8)]


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
