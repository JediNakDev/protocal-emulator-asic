# SPDX-License-Identifier: Apache-2.0
"""Host-side low-speed USB software for programs/usb_ls_host.pasm."""

from . import records

PID_OUT, PID_IN, PID_SETUP = 0x1, 0x9, 0xD
PID_DATA0, PID_DATA1 = 0x3, 0xB
PID_ACK, PID_NAK, PID_STALL = 0x2, 0xA, 0xE

J, K, SE0 = 2, 1, 0

# Reply deadline in host-program ticks (4 cycles at CLKDIV 3, 8 per bit):
# the reply must start within 17 bit times of the end of the host's SE0.
REPLY_DEADLINE = 127

# Bus reset: SE0 for whole units of 12297 ticks, 1.025 ms at 48 MHz.
RESET_UNIT_TICKS = 12297
TICK_S = 4 / 48e6


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


def command(packet_bits, reply, deadline=REPLY_DEADLINE):
    """Transmit words for one packet: the command word, then line states.
    With `reply`, the program waits up to `deadline` ticks for an answer."""
    states = line_states(packet_bits)
    assert len(states) <= 256 and 0 < deadline < 128
    words = [(len(states) - 1) | ((deadline if reply else 0) << 8)]
    for i in range(0, len(states), 8):
        chunk = states[i:i + 8]
        words.append(sum(s << (2 * j) for j, s in enumerate(chunk)))
    return words


def reset(ms=10.0):
    """Command word for a bus reset: SE0 for at least `ms` milliseconds
    (USB 2.0 section 7.1.7.5 requires 10 ms), then J."""
    units = -(-round(ms * 1e-3 / TICK_S) // RESET_UNIT_TICKS)
    assert 0 < units <= 256
    return [(units - 1) | 0x8000]


def split_records(words):
    """usb_ls_rx receive words -> complete records of bits, one per reply."""
    return records.split(words)[0]


def parse(bits):
    """Decoded record -> (pid, payload, crc_ok), or None without a SYNC:
    no reply arrived before the deadline, or it was not a packet.
    Leading ones are idle line before the SYNC."""
    if bits is None:
        return None
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
