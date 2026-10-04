# SPDX-License-Identifier: Apache-2.0
"""Independent protocol peers. They only watch and drive pins, and share no
code or state with the programs under test."""

from cocotb.triggers import ClockCycles, RisingEdge


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


async def spi_peripheral(clk, sck, mosi, set_miso, miso_bytes, nbits):
    """SPI mode 0 peripheral: samples MOSI on SCK rising, changes MISO on
    SCK falling, most significant bit first. Returns the MOSI bytes."""
    miso_bits = [(b >> (7 - i)) & 1 for b in miso_bytes for i in range(8)]
    idx = 0
    set_miso(miso_bits[0])
    got = []
    prev = sck()
    while len(got) < nbits:
        await RisingEdge(clk)
        cur = sck()
        if not prev and cur:
            got.append(mosi())
        elif prev and not cur:
            idx += 1
            set_miso(miso_bits[idx] if idx < len(miso_bits) else 0)
        prev = cur
    return [sum(got[8 * i + j] << (7 - j) for j in range(8)) for i in range(nbits // 8)]


class I2CTarget:
    """I2C target on open-drain lines. ACKs bytes when the address matches,
    and stretches SCL after every ACK clock."""

    def __init__(self, chip, sda_bit, scl_bit, address, stretch=0):
        self.chip = chip
        self.sda_bit = sda_bit
        self.scl_bit = scl_bit
        self.address = address
        self.stretch = stretch
        self.transactions = []
        self.starts = 0
        self.stops = 0

    def _lines(self):
        v = self.chip.uio()
        return (v >> self.sda_bit) & 1, (v >> self.scl_bit) & 1

    async def run(self, clk):
        sda_p, scl_p = self._lines()
        bits = []
        cur = None
        matched = False
        acking = False
        nbit = 0
        while True:
            await RisingEdge(clk)
            sda, scl = self._lines()
            if scl and scl_p and sda_p and not sda:  # START
                self.starts += 1
                cur = []
                self.transactions.append(cur)
                bits, nbit, matched = [], 0, False
            elif scl and scl_p and not sda_p and sda:  # STOP
                self.stops += 1
                cur = None
            elif cur is not None and scl and not scl_p:  # SCL rising
                if nbit < 8:
                    bits.append(sda)
                nbit += 1
            elif cur is not None and not scl and scl_p:  # SCL falling
                if nbit == 8:
                    byte = sum(b << (7 - i) for i, b in enumerate(bits))
                    if not cur:
                        matched = (byte >> 1) == self.address
                    cur.append(byte)
                    if matched:
                        self.chip.drive_uio(self.sda_bit, 0)
                        acking = True
                elif nbit == 9:
                    if acking:
                        self.chip.drive_uio(self.sda_bit, None)
                        acking = False
                    bits, nbit = [], 0
                    if self.stretch:
                        self.chip.drive_uio(self.scl_bit, 0)
                        await ClockCycles(clk, self.stretch)
                        self.chip.drive_uio(self.scl_bit, None)
                        # SCL was low until now; the release itself may be
                        # the next rising edge, so do not record it as high.
                        sda_p, scl_p = self._lines()[0], 0
                        continue
            sda_p, scl_p = sda, scl
