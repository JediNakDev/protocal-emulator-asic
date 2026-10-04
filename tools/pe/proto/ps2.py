# SPDX-License-Identifier: Apache-2.0
"""Host-side PS/2 framing for programs/ps2_device.pasm and ps2_host.pasm."""


def _odd_parity(b):
    return 1 - (bin(b).count("1") & 1)


def device_frame(b):
    """11-bit device-to-host frame, LSB first: start, data, parity, stop."""
    return (b << 1) | (_odd_parity(b) << 9) | (1 << 10)


def host_frame(b):
    """10 bits sent after the host's start bit: data, parity, stop."""
    return b | (_odd_parity(b) << 8) | (1 << 9)


def decode(word):
    """Receive word with 10 bits in bits 15:6 -> (byte, parity_ok, stop_ok)."""
    v = word >> 6
    b = v & 0xFF
    return b, ((v >> 8) & 1) == _odd_parity(b), (v >> 9) & 1 == 1


def acked(word):
    return (word >> 15) & 1 == 0
