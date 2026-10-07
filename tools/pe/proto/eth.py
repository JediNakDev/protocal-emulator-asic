# SPDX-License-Identifier: Apache-2.0
"""Host-side 10BASE-T software for programs/eth10_tx.pasm and eth10_rx.pasm."""

import zlib

PREAMBLE_SFD = bytes([0x55] * 7 + [0xD5])


def frame(dst, src, ethertype, payload):
    """Frame bytes on the wire: preamble, SFD, header, payload padded to the
    60-byte minimum, FCS."""
    body = bytes(dst) + bytes(src) + ethertype.to_bytes(2, "big") + bytes(payload)
    body += bytes(max(0, 60 - len(body)))
    return PREAMBLE_SFD + body + zlib.crc32(body).to_bytes(4, "little")


def tx_words(wire_bytes):
    bits = len(wire_bytes) * 8
    data = wire_bytes + bytes(len(wire_bytes) % 2)
    return [bits - 1] + [data[i] | data[i + 1] << 8 for i in range(0, len(data), 2)]


def record_bits(words):
    """Bits of one receive record, without the end marker and padding."""
    bits = [(w >> (15 - i)) & 1 for w in words for i in range(16)]
    while bits and bits[-1] == 0:
        bits.pop()
    if bits:
        bits.pop()
    return bits


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
