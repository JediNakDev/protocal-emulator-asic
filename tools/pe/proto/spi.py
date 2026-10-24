# SPDX-License-Identifier: Apache-2.0
"""Host-side SPI software for programs/spi_cpha0.pasm, spi_cpha0_fast.pasm
and spi_cpha1.pasm."""

MORE = 1 << 7  # another byte follows in the same transaction: CS_N stays low


def transaction(data):
    """Transmit words for one chip-select transaction of the bytes `data`."""
    data = list(data)
    assert data, "a transaction has at least one byte"
    return [b << 8 | (MORE if i < len(data) - 1 else 0) for i, b in enumerate(data)]


def decode(word):
    """A receive word -> the byte read from MISO."""
    return word & 0xFF
