# SPDX-License-Identifier: Apache-2.0
"""Host-side CAN 2.0A software for programs/can_tx.pasm and can_rx.pasm."""

DOMINANT, RECESSIVE, ACK_SLOT, QUIET = 0, 1, 2, 3


def crc15(bits):
    crc = 0
    for b in bits:
        fb = ((crc >> 14) & 1) ^ b
        crc = (crc << 1) & 0x7FFF
        if fb:
            crc ^= 0x4599
    return crc


def frame_bits(ident, data):
    """Unstuffed bits from start of frame to the end of the CRC."""
    assert 0 <= ident < 0x800 and len(data) <= 8
    bits = [0]                                          # SOF
    bits += [(ident >> (10 - i)) & 1 for i in range(11)]
    bits += [0, 0, 0]                                   # RTR, IDE, r0
    bits += [(len(data) >> (3 - i)) & 1 for i in range(4)]
    for byte in data:
        bits += [(byte >> (7 - i)) & 1 for i in range(8)]
    crc = crc15(bits)
    bits += [(crc >> (14 - i)) & 1 for i in range(15)]
    return bits


def stuff(bits):
    out, run, last = [], 0, None
    for b in bits:
        out.append(b)
        run = run + 1 if b == last else 1
        last = b
        if run == 5:
            out.append(1 - b)
            last, run = 1 - b, 1
    return out


def tx_words(ident, data):
    """Transmit words for can_tx: the stuffed frame, CRC delimiter, ACK slot,
    ACK delimiter, 7 bits of end of frame and 3 of intermission."""
    sym = [RECESSIVE if b else DOMINANT for b in stuff(frame_bits(ident, data))]
    sym += [RECESSIVE, ACK_SLOT] + [QUIET] * 11
    while len(sym) % 8:
        sym.append(QUIET)
    return [sum(s << (2 * j) for j, s in enumerate(sym[i:i + 8])) for i in range(0, len(sym), 8)]


def split_records(words):
    """Split can_rx receive words into per-frame bit lists."""
    records, cur = [], []
    for w in words:
        if w == 0xFFFF:
            bits = [(x >> (15 - i)) & 1 for x in cur for i in range(16)]
            while bits and bits[-1] == 0:
                bits.pop()
            if bits:
                bits.pop()  # the end marker
            records.append(bits)
            cur = []
        else:
            cur.append(w)
    return records, cur


def parse(bits):
    """Destuffed record -> (id, data, crc_ok), or None if too short."""
    if len(bits) < 19 or bits[0] != 0:
        return None
    ident = sum(bits[1 + i] << (10 - i) for i in range(11))
    dlc = min(sum(bits[15 + i] << (3 - i) for i in range(4)), 8)
    end = 19 + 8 * dlc + 15
    if len(bits) < end:
        return None
    data = bytes(sum(bits[19 + 8 * k + i] << (7 - i) for i in range(8)) for k in range(dlc))
    return ident, data, crc15(bits[:end]) == 0
