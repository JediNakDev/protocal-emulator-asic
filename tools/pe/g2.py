# SPDX-License-Identifier: Apache-2.0
"""Table builder for the bit state machine (G2). Format: docs/spec.md."""

from dataclasses import dataclass


@dataclass
class Step:
    next: int = 0         # next state, 0-15
    out0: int = 0
    out1: int = 0
    emit: bool = False    # deliver out0 to the owner engine
    take: bool = False    # consume the feed bit


def build(rule):
    """Build a 64-byte table from rule(state, in0, in1) -> Step."""
    table = []
    for index in range(64):
        state, in1, in0 = index >> 2, (index >> 1) & 1, index & 1
        s = rule(state, in0, in1)
        if not 0 <= s.next <= 15:
            raise ValueError(f"next state {s.next} out of range")
        table.append((s.next & 15) | (s.out0 & 1) << 4 | (s.out1 & 1) << 5
                     | int(s.emit) << 6 | int(s.take) << 7)
    return table


def nrzi_decoder():
    """in0 = line sample. Emits 1 for no transition, 0 for a transition.
    State bit 0 holds the previous sample."""
    def rule(state, in0, in1):
        prev = state & 1
        return Step(next=in0, out0=int(in0 == prev), emit=True)
    return build(rule)


def manchester_encoder():
    """IEEE 802.3 convention: 0 is high then low, 1 is low then high.
    in0 = feed bit, in1 = feed full. Two steps per bit; out0 is the line."""
    def rule(state, in0, in1):
        if state == 0:
            if in1:
                return Step(next=1 + in0, out0=1 - in0, take=True)
            return Step(next=0, out0=0)
        return Step(next=0, out0=1 if state == 2 else 0)
    return build(rule)
