# SPDX-License-Identifier: Apache-2.0
"""Host-side CAN software for the listen-only receiver programs/can_rx.pasm
(CAN 2.0A) and for the node programs/can_node.pasm with can_line.pasm, which
transmits, arbitrates and acknowledges (CAN 2.0A and 2.0B)."""

from collections import namedtuple

from . import records

NODE_D, NODE_A, NODE_R, NODE_E, NODE_Q = 0, 1, 2, 3, 7   # can_node symbols
NODE_REC, NODE_TEC = 6, 7       # can_node RAM entries of the error counters

Frame = namedtuple("Frame", "ident data crc_ok acked ext rtr dlc")
FLAG = "flag"       # parse_raw: an active error flag or an overload flag


def crc15(bits):
    crc = 0
    for b in bits:
        fb = ((crc >> 14) & 1) ^ b
        crc = (crc << 1) & 0x7FFF
        if fb:
            crc ^= 0x4599
    return crc


def _bits(value, n):
    return [(value >> (n - 1 - i)) & 1 for i in range(n)]


def frame_bits(ident, data, ext=False, rtr=False, dlc=None):
    """Unstuffed bits from start of frame to the end of the CRC. A remote
    frame (rtr) has no data; its DLC is the length requested."""
    dlc = len(data) if dlc is None else dlc
    assert len(data) <= 8 and 0 <= dlc <= 15 and not (rtr and data)
    assert len(data) == min(dlc, 8) or rtr
    bits = [0]                                          # SOF
    if ext:
        assert 0 <= ident < 1 << 29
        bits += _bits(ident >> 18, 11) + [1, 1]         # base identifier, SRR, IDE
        bits += _bits(ident & 0x3FFFF, 18) + [int(rtr), 0, 0]   # RTR, r1, r0
    else:
        assert 0 <= ident < 0x800
        bits += _bits(ident, 11) + [int(rtr), 0, 0]     # RTR, IDE, r0
    bits += _bits(dlc, 4)
    for byte in data:
        bits += _bits(byte, 8)
    return bits + _bits(crc15(bits), 15)


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


def split_records(words):
    """can_rx receive words -> (records of destuffed bits, words of an
    unfinished record). See tools/pe/proto/records.py."""
    return records.split(words)


def parse(bits):
    """Destuffed record -> (id, data, crc_ok), or None if too short."""
    if bits is None or len(bits) < 19 or bits[0] != 0:
        return None
    ident = sum(bits[1 + i] << (10 - i) for i in range(11))
    dlc = min(sum(bits[15 + i] << (3 - i) for i in range(4)), 8)
    end = 19 + 8 * dlc + 15
    if len(bits) < end:
        return None
    data = bytes(sum(bits[19 + 8 * k + i] << (7 - i) for i in range(8)) for k in range(dlc))
    return ident, data, crc15(bits[:end]) == 0


def node_tx_words(ident, data, ext=False, rtr=False, dlc=None):
    """Transmit words for can_node: the stuffed frame after the start of
    frame as 3-bit symbols, 5 per word, bits 2:0 first. A recessive bit is A
    in the arbitration field (up to the RTR bit, with the stuff bits before
    it) and R after it; a dominant one is D; the CRC delimiter is E."""
    bits = frame_bits(ident, data, ext, rtr, dlc)
    arb_end = 32 if ext else 12             # destuffed index of RTR
    sym, run, last = [], 0, None
    for i, b in enumerate(bits):
        if i:
            sym.append(NODE_D if not b else NODE_A if i <= arb_end else NODE_R)
        run = run + 1 if b == last else 1
        last = b
        if run == 5:                        # a stuff bit of the other level
            last, run = 1 - b, 1
            sym.append(NODE_D if last == 0 else NODE_A if i < arb_end else NODE_R)
    sym.append(NODE_E)
    while len(sym) % 5:
        sym.append(NODE_Q)
    return [sum(s << (3 * j) for j, s in enumerate(sym[i:i + 5])) for i in range(0, len(sym), 5)]


def node_counter(stored):
    """can_node RAM entry NODE_REC or NODE_TEC -> the error counter, or None
    while bus-off (TEC only)."""
    return None if stored == 0xFFFF else 255 - stored


def _value(bits):
    return sum(b << (len(bits) - 1 - i) for i, b in enumerate(bits))


def _header(bits):
    """(ext, header length, rtr, dlc) once the header is in the destuffed
    `bits`, else None."""
    if len(bits) < 14:
        return None
    ext = bits[13]
    head = 39 if ext else 19
    if len(bits) < head:
        return None
    return ext, head, bits[32] if ext else bits[12], _value(bits[head - 4:head])


def _frame_length(bits):
    """Destuffed length from start of frame to the end of the CRC, once the
    header is in `bits`, else None."""
    h = _header(bits)
    if h is None:
        return None
    _, head, rtr, dlc = h
    return head + (0 if rtr else 8 * min(dlc, 8)) + 15


def _decode(bits, acked):
    ext, head, rtr, dlc = _header(bits)
    ident = _value(bits[1:12])
    if ext:
        ident = ident << 18 | _value(bits[14:32])
    data = bytes(_value(bits[head + 8 * k:head + 8 * k + 8])
                 for k in range(0 if rtr else min(dlc, 8)))
    return Frame(ident, data, crc15(bits) == 0, acked, bool(ext), bool(rtr), dlc)


def parse_raw(raw):
    """can_line raw (stuffed) bus bits -> the frames they hold, in order.
    A frame begins at a dominant bit after recessive ones; it is acked if
    the bus was dominant in its acknowledgement slot. A frame cut short by
    a stuff error, one with a dominant CRC delimiter, or one the bits end
    in is returned as None, and six dominant bits where a frame could begin
    (after its end of frame bit 1, or after the ACK slot) as FLAG: an
    error or overload flag. Bits from either until the next 7 recessive
    bits are skipped."""
    frames, i = [], 0
    while i < len(raw):
        if raw[i]:
            i += 1
            continue
        if raw[i:i + 6] == [0] * 6:                     # no frame starts so
            frames.append(FLAG)
            i = _skip_flags(raw, i)
            continue
        bits, run, last, need = [], 0, None, None
        while i < len(raw) and len(bits) != need:
            b = raw[i]
            i += 1
            if run == 5:
                if b == last:
                    break                               # stuff error
                run, last = 1, b                        # stuff bit
                continue
            run = run + 1 if b == last else 1
            last = b
            bits.append(b)
            need = need or _frame_length(bits)
        ok = len(bits) == need
        if ok and run == 5 and i < len(raw):
            ok = raw[i] != last                         # stuff bit after the CRC
            i += 1
        if ok and i + 1 >= len(raw):
            frames.append(None)
            break
        if not ok or raw[i] == 0:                       # or a dominant CRC delimiter
            frames.append(None)
            i = _skip_flags(raw, i)
            continue
        frames.append(_decode(bits, raw[i + 1] == 0))
        i += 2
        if i < len(raw) and raw[i] == 0:                # a flag from the ACK delimiter
            frames.append(FLAG)
            i = _skip_flags(raw, i)
            continue
        i += 1                                          # delimiter, slot, delimiter
    return frames


def _skip_flags(raw, i):
    """Index of the next run of 7 recessive bits from i: the end of the
    flags of an error or overload frame."""
    while i < len(raw) and not all(raw[i:i + 7]):
        i += 1
    return i


def raw_frames(words):
    """can_line receive words -> (frames of every closed record, words of a
    record that has not ended yet)."""
    recs, rest = records.split(words)
    return [f for r in recs for f in (parse_raw(r) if r is not None else [None])], rest
