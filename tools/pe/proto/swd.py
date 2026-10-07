# SPDX-License-Identifier: Apache-2.0
"""Host-side SWD driver for programs/swd.pasm (ARM ADIv5 wire protocol)."""

ACK_OK, ACK_WAIT, ACK_FAULT = 0b001, 0b010, 0b100

DRIVE = 1 << 5
READ = 1 << 4


def _write(nbits, value):
    return [(nbits - 1) | DRIVE, value & 0xFFFF]


def _read(nbits, drive=False):
    return [(nbits - 1) | READ | (DRIVE if drive else 0)]


def parity(v):
    return bin(v).count("1") & 1


def request(apndp, rnw, addr):
    a2, a3 = (addr >> 2) & 1, (addr >> 3) & 1
    p = apndp ^ rnw ^ a2 ^ a3
    return 1 | apndp << 1 | rnw << 2 | a2 << 3 | a3 << 4 | p << 5 | 0 << 6 | 1 << 7


class Batch:
    """Commands for a sequence of SWD operations plus a decoder for the
    receive words they produce."""

    def __init__(self):
        self.tx = []
        self.reads = []  # per read command: number of bits
        self.ops = []    # (kind, index of first read word)

    def _rd(self, nbits, drive=False):
        self.tx += _read(nbits, drive)
        self.reads.append(nbits)
        return len(self.reads) - 1

    def line_reset(self):
        """At least 50 ones, then two idle cycles."""
        for _ in range(4):
            self.tx += _write(16, 0xFFFF)
        self.tx += _write(2, 0)

    def idle(self, n=2):
        self.tx += _write(n, 0)

    def read(self, apndp, addr):
        self.tx += _write(8, request(apndp, 1, addr))
        first = self._rd(1)       # turnaround
        self._rd(3)               # ACK
        self._rd(16)              # data[15:0]
        self._rd(16)              # data[31:16]
        self._rd(1)               # parity
        self._rd(1)               # turnaround back to the host
        self.idle()
        self.ops.append(("read", first))

    def write(self, apndp, addr, value):
        self.tx += _write(8, request(apndp, 0, addr))
        first = self._rd(1)       # turnaround
        self._rd(3)               # ACK
        self._rd(1)               # turnaround back to the host
        self.tx += _write(16, value & 0xFFFF)
        self.tx += _write(16, value >> 16)
        self.tx += _write(1, parity(value))
        self.idle()
        self.ops.append(("write", first))

    def n_rx_words(self):
        return len(self.reads)

    def decode(self, rx):
        """Per operation: (ack, data, parity_ok) for reads, (ack,) for writes."""
        vals = [w >> (16 - n) for w, n in zip(rx, self.reads)]
        out = []
        for kind, i in self.ops:
            ack = vals[i + 1]
            if kind == "read":
                data = vals[i + 2] | vals[i + 3] << 16
                out.append((ack, data, parity(data) == vals[i + 4]))
            else:
                out.append((ack,))
        return out
