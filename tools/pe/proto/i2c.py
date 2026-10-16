# SPDX-License-Identifier: Apache-2.0
"""Host-side I2C software for programs/i2c.pasm."""

START, STOP = 1 << 15, 1 << 5
CLKDIV = {100_000: 19, 400_000: 4}  # at 50 MHz: 25 ticks per SCL period


def byte_word(nine, start=False, stop=False):
    """A transmit word: the nine SDA bits (first highest), START, STOP."""
    assert 0 <= nine < 512
    return (START if start else 0) | (nine << 6) | (STOP if stop else 0)


def write(addr, data, stop=True):
    """START, the address for writing, then the data bytes."""
    data = list(data)
    out = [byte_word((addr << 1) << 1 | 1, start=True, stop=stop and not data)]
    for i, b in enumerate(data):
        out.append(byte_word(b << 1 | 1, stop=stop and i == len(data) - 1))
    return out


def read(addr, n, stop=True):
    """START (repeated if inside a transaction), the address for reading,
    then n bytes, acknowledging all but the last."""
    out = [byte_word(((addr << 1) | 1) << 1 | 1, start=True, stop=stop and n == 0)]
    for i in range(n):
        last = i == n - 1
        out.append(byte_word(0x1FE | int(last), stop=stop and last))
    return out


def decode(word):
    """A receive word -> (byte on SDA, ACK bit: 0 acknowledged)."""
    return (word >> 1) & 0xFF, word & 1
