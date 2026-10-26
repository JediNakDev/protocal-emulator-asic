# SPDX-License-Identifier: Apache-2.0
"""Host-side 10BASE-T software for programs/eth10_tx.pasm and eth10_rx.pasm."""

import zlib

from .. import regs
from . import records

PREAMBLE_SFD = bytes([0x55] * 7 + [0xD5])

# Clock recovery (G7) for eth10_rx at 50 MHz with two samples per cycle, so
# 10 samples per 100 ns bit. Edges count within +/- 0.44 bit of the mid-bit
# transition and the one nearest the expected phase corrects it; the strobe
# takes the sample 0.44 bit after the transition, the gain settles at 2^-6,
# and lock is lost at the third bit without a mid-bit transition: the end of
# the frame. Measured margin: docs/protocols.md.
RX_INC = 6554  # 65536 / 10, rounded: +31 ppm
RX_WIN = 112
RX_SAMPLE = 112
RX_SHIFT = 6
RX_LOSS = 2


def rx_clock_recovery(pin):
    """CR_CTRL to CR_SAMPLE (0x30-0x35) for eth10_rx receiving on `pin`;
    NEGSEL must select the same pin."""
    return [regs.CR_ENABLE | regs.CR_DOUBLE | regs.CR_LOCK_RISING | regs.cr_pin(pin),
            regs.cr_gain(RX_SHIFT, RX_LOSS), RX_INC & 0xFF, RX_INC >> 8, RX_WIN, RX_SAMPLE]


def frame(dst, src, ethertype, payload):
    """Frame bytes on the wire: preamble, SFD, header, payload padded to the
    60-byte minimum, FCS."""
    body = bytes(dst) + bytes(src) + ethertype.to_bytes(2, "big") + bytes(payload)
    body += bytes(max(0, 60 - len(body)))
    return PREAMBLE_SFD + body + zlib.crc32(body).to_bytes(4, "little")


def tx_words(wire_bytes):
    """eth10_tx transmit words: the number of bit pairs - 1, then the bits."""
    pairs = len(wire_bytes) * 4
    data = wire_bytes + bytes(len(wire_bytes) % 2)
    return [pairs - 1] + [data[i] | data[i + 1] << 8 for i in range(0, len(data), 2)]


def split_records(words):
    """eth10_rx receive words -> (records of bits, words of an unfinished
    record). See tools/pe/proto/records.py."""
    return records.split(words)


def parse(bits):
    """Received bits -> (frame body without FCS, fcs_ok), or None without an SFD.
    Bits before the SFD (a partial preamble) and trailing bits past the last
    whole byte are ignored."""
    s = "".join(map(str, bits))
    i = s.find("10101011")
    if i < 0:
        return None
    rest = bits[i + 8:]
    data = bytes(sum(rest[8 * k + j] << j for j in range(8)) for k in range(len(rest) // 8))
    if len(data) < 5:
        return None
    body, fcs = data[:-4], data[-4:]
    return body, zlib.crc32(body) == int.from_bytes(fcs, "little")
