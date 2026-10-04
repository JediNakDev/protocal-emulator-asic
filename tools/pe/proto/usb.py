# SPDX-License-Identifier: Apache-2.0
"""Host-side low-speed USB software for programs/usb_ls_host.pasm."""

PID_OUT, PID_IN, PID_SETUP = 0x1, 0x9, 0xD
PID_DATA0, PID_DATA1 = 0x3, 0xB
PID_ACK, PID_NAK, PID_STALL = 0x2, 0xA, 0xE

J, K, SE0 = 2, 1, 0


def pid_byte(pid):
    return pid | ((pid ^ 0xF) << 4)


def crc5(bits):
    crc = 0x1F
    for b in bits:
        crc = (crc >> 1) ^ 0x14 if (crc ^ b) & 1 else crc >> 1
    return crc ^ 0x1F


def crc16(data):
    crc = 0xFFFF
    for byte in data:
        for i in range(8):
            b = (byte >> i) & 1
            crc = (crc >> 1) ^ 0xA001 if (crc ^ b) & 1 else crc >> 1
    return crc ^ 0xFFFF


def _bits(data):
    return [(byte >> i) & 1 for byte in data for i in range(8)]


def token(pid, addr, endp):
    fields = [(addr >> i) & 1 for i in range(7)] + [(endp >> i) & 1 for i in range(4)]
    c = crc5(fields)
    return _bits([pid_byte(pid)]) + fields + [(c >> i) & 1 for i in range(5)]


def data_packet(pid, payload):
    c = crc16(payload)
    return _bits([pid_byte(pid)] + list(payload) + [c & 0xFF, c >> 8])


def handshake(pid):
    return _bits([pid_byte(pid)])


def line_states(packet_bits, idle=2):
    """Idle J, SYNC, packet with bit stuffing and NRZI, then end of packet."""
    bits = [0] * 7 + [1] + list(packet_bits)
    stuffed, run = [], 0
    for b in bits:
        stuffed.append(b)
        run = run + 1 if b else 0
        if run == 6:
            stuffed.append(0)
            run = 0
    states, level = [J] * idle, J
    for b in stuffed:
        if b == 0:
            level = K if level == J else J
        states.append(level)
    return states + [SE0, SE0, J]


def command(packet_bits, reply):
    """Transmit words for one packet: the command word, then line states."""
    states = line_states(packet_bits)
    assert len(states) <= 256
    words = [(len(states) - 1) | (int(reply) << 8)]
    for i in range(0, len(states), 8):
        chunk = states[i:i + 8]
        words.append(sum(s << (2 * j) for j, s in enumerate(chunk)))
    return words


def split_records(words):
    records, cur = [], []
    for w in words:
        if w == 0xFFFF:
            bits = [(x >> (15 - i)) & 1 for x in cur for i in range(16)]
            while bits and bits[-1] == 0:
                bits.pop()
            if bits:
                bits.pop()  # end marker
            records.append(bits)
            cur = []
        else:
            cur.append(w)
    return records


def parse(bits):
    """Decoded record -> (pid, payload, crc_ok), or None without a SYNC.
    Leading ones are idle line before the SYNC."""
    i = 0
    while i < len(bits) and bits[i] == 1:
        i += 1
    if bits[i:i + 8] != [0] * 7 + [1]:
        return None
    raw, out, run = bits[i + 8:], [], 0
    for b in raw:
        if run == 6:
            run = 0  # stuff bit
            continue
        out.append(b)
        run = run + 1 if b else 0
    data = [sum(out[8 * k + j] << j for j in range(8)) for k in range(len(out) // 8)]
    if not data or (data[0] & 0xF) != ((data[0] >> 4) ^ 0xF):
        return None
    pid, body = data[0] & 0xF, bytes(data[1:])
    if pid in (PID_DATA0, PID_DATA1):
        return pid, body[:-2], len(body) >= 2 and crc16(body[:-2]) == body[-2] | body[-1] << 8
    return pid, body, True
