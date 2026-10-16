# SPDX-License-Identifier: Apache-2.0
"""Host-side SWD driver for programs/swd.pasm (ARM ADIv5 wire protocol)."""

ACK_OK, ACK_WAIT, ACK_FAULT = 0b001, 0b010, 0b100
ACK_NONE = 0b111  # nobody drove SWDIO: the pull-up reads ones


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
        self.ops = []  # "read" or "write", per transaction

    def sequence(self, nbits, value):
        """Drive nbits (1-16) of value, first bit lowest."""
        assert 1 <= nbits <= 16
        self.tx += [(nbits - 1) << 1, value & 0xFFFF]

    def line_reset(self):
        """At least 50 ones, then two idle cycles."""
        for _ in range(4):
            self.sequence(16, 0xFFFF)
        self.idle()

    def idle(self, n=2):
        self.sequence(n, 0)

    def transaction(self, req, read, value=0):
        """Any request byte, for example one with bad parity."""
        self.tx.append(1 | (req & 0xFF) << 1 | int(read) << 9)
        if not read:
            self.tx += [value & 0xFFFF, value >> 16, parity(value)]
        self.ops.append("read" if read else "write")

    def read(self, apndp, addr):
        self.transaction(request(apndp, 1, addr), True)
        self.idle()

    def write(self, apndp, addr, value):
        self.transaction(request(apndp, 0, addr), False, value)
        self.idle()

    def decode(self, rx):
        """Per transaction: (ack, data, parity_ok) for a read with ACK OK,
        (ack,) otherwise. Returns (results, number of words used)."""
        out, i = [], 0
        for kind in self.ops:
            if i >= len(rx):
                break
            ack = (rx[i] >> 13) & 7
            i += 1
            if kind == "read" and ack == ACK_OK:
                if i + 3 > len(rx):
                    break
                data = rx[i] | rx[i + 1] << 16
                out.append((ack, data, parity(data) == rx[i + 2] >> 15))
                i += 3
            else:
                out.append((ack,))
        return out, i
