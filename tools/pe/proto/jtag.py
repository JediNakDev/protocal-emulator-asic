# SPDX-License-Identifier: Apache-2.0
"""Host-side JTAG driver for programs/jtag.pasm.

Builds TMS/TDI cycle sequences, packs them for the transmit queue, and
extracts TDO data from the receive words. Sequences start and end in
Run-Test/Idle, except `reset`, which starts anywhere.
"""


class Scan:
    """A list of (tms, tdi) cycles plus the cycle ranges whose TDO is wanted."""

    def __init__(self):
        self.cycles = []
        self.reads = []  # (start index, length)

    def clock(self, tms, tdi=0):
        self.cycles.append((tms & 1, tdi & 1))

    def reset(self):
        """Test-Logic-Reset from any state, then Run-Test/Idle."""
        for _ in range(5):
            self.clock(1)
        self.clock(0)

    def _shift(self, value, length, ir):
        # Run-Test/Idle -> Select-DR -> [Select-IR] -> Capture -> Shift
        self.clock(1)
        if ir:
            self.clock(1)
        self.clock(0)
        self.clock(0)
        self.reads.append((len(self.cycles), length))
        for i in range(length):
            last = i == length - 1
            self.clock(1 if last else 0, (value >> i) & 1)  # last bit exits to Exit1
        self.clock(1)  # Update
        self.clock(0)  # Run-Test/Idle

    def ir(self, value, length):
        self._shift(value, length, True)

    def dr(self, value, length):
        self._shift(value, length, False)

    def words(self):
        """Transmit words, padded with idle cycles to a whole number of words.
        Padding keeps TMS low, which stays in Run-Test/Idle."""
        cyc = list(self.cycles)
        while len(cyc) % 16:
            cyc.append((0, 0))
        out = []
        for i in range(0, len(cyc), 8):
            w = 0
            for j, (tms, tdi) in enumerate(cyc[i:i + 8]):
                w |= (tdi << (2 * j)) | (tms << (2 * j + 1))
            out.append(w)
        return out

    def n_rx_words(self):
        n = len(self.cycles)
        return (n + 15) // 16

    def results(self, rx_words):
        """TDO values of every requested shift, as integers (first bit = LSB)."""
        bits = [(w >> i) & 1 for w in rx_words for i in range(16)]
        return [sum(bits[start + i] << i for i in range(length)) for start, length in self.reads]
