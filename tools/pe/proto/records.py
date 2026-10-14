# SPDX-License-Identifier: Apache-2.0
"""Count-terminated receive records (docs/protocols.md, "Record framing").

A receive program packs 15 bits per data word, first bit in bit 14, with
bit 15 clear (push threshold 15, `in` shifting left). At the end of a record
it pushes the partial last word, holding k = 0-14 bits in its low bits
(first bit in bit k - 1), then a trailer word 0x8000 | k:

    mov x, isrcnt
    push
    mov isr, ~null
    in x, 15        ; autopushed at threshold 15

Payload words never have bit 15 set, so the Host finds every boundary in the
word stream whenever it reads it.
"""

TRAILER = 0x8000


def split(words):
    """Split a word stream into records. Returns (records, rest): each record
    is a list of bits in arrival order, or None if its trailer is malformed;
    `rest` holds the words of a record that has not ended yet."""
    records, cur = [], []
    for w in words:
        if not w & TRAILER:
            cur.append(w & 0x7FFF)
            continue
        k = w & 0x7FFF
        if not cur or k > 14:
            records.append(None)
        else:
            bits = [(x >> (14 - i)) & 1 for x in cur[:-1] for i in range(15)]
            bits += [(cur[-1] >> (k - 1 - i)) & 1 for i in range(k)]
            records.append(bits)
        cur = []
    return records, cur


def words_for(bits):
    """The words a receive program pushes for one record (the inverse of
    `split`, for tests and tools)."""
    full = len(bits) // 15 * 15
    words = [sum(bits[i + j] << (14 - j) for j in range(15)) for i in range(0, full, 15)]
    rest = bits[full:]
    words.append(sum(b << (len(rest) - 1 - j) for j, b in enumerate(rest)))
    return words + [TRAILER | len(rest)]
