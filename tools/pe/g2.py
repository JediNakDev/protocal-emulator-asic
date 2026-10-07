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


def can_destuffer():
    """CAN bit destuffer. in0 = sampled bus bit, fed once per bit.
    Emits every data bit, drops stuff bits, and raises out1 (end) on the
    sixth equal bit: the end of frame, or a stuff error.
    States: 0 idle, 1-5 a run of c dominant bits, 9-13 a run of c recessive
    bits, 6 after a dominant violation (error flag)."""
    def rule(state, in0, in1):
        b = in0
        if state == 0:
            if b:
                return Step(next=0, out1=1)
            return Step(next=1, out0=0, emit=True)          # start of frame
        if state == 6:
            return Step(next=0 if b else 6, out1=1)
        level, run = (1, state - 8) if state >= 8 else (0, state)
        if not 1 <= run <= 5:
            return Step(next=0, out1=1)                     # unused states
        if run == 5:
            if b != level:
                return Step(next=b * 8 + 1)                  # stuff bit: dropped
            return Step(next=0 if b else 6, out1=1)          # six equal bits
        nxt = b * 8 + (run + 1 if b == level else 1)
        return Step(next=nxt, out0=b, emit=True)
    return build(rule)


def usb_nrzi_dpll():
    """NRZI receiver with clock recovery, stepping 4 times per bit.
    in0 = line sample (D+), in1 = receive enable.
    State bits 1:0 = phase of the next step, bit 2 = previous sample,
    bit 3 = a transition happened in the current bit.
    A transition sets the phase so the bit is sampled two steps later;
    at phase 2 it emits 1 for no transition and 0 for a transition."""
    def rule(state, in0, in1):
        phase, level, trans = state & 3, (state >> 2) & 1, (state >> 3) & 1
        if not in1:
            return Step(next=in0 << 2)
        edge = int(in0 != level)
        if phase == 2:
            nxt_phase = 1 if edge else 3
            return Step(next=nxt_phase | in0 << 2 | edge << 3, out0=1 - trans, emit=True)
        nxt_phase = 1 if edge else (phase + 1) & 3
        return Step(next=nxt_phase | in0 << 2 | (trans | edge) << 3)
    return build(rule)


def manchester_receiver():
    """Manchester decoder stepping every cycle on two samples per cycle:
    in0 = rising-edge sample, in1 = falling-edge sample (pin 15, NEGSEL set
    to the same pin). Designed for 4 cycles per bit (10 Mbit/s at 40 MHz).
    After a mid-bit transition, steps 1 and 2 are ignored (they hold the
    possible bit-boundary transition), step 3 accepts only a transition
    between its two samples, and steps 4 and 5 accept any transition.
    Each mid-bit transition emits the new level (IEEE 802.3: low-to-high is 1).
    Without a transition by step 5 the decoder goes idle and raises out1;
    from idle it locks on the next rising transition (a preamble starts with
    a rising mid-bit transition). out0 follows the line.
    States: 1-3 the steps after a transition, 4+L and 6+L steps 4 and 5
    with the line at L, 8+L idle with the line at L."""
    def rule(state, in0, in1):
        a, b = in0, in1
        if state in (1, 2):
            return Step(next=state + 1, out0=b)
        if state == 3:
            if a != b:
                return Step(next=1, out0=b, emit=True)
            return Step(next=4 + b, out0=b)
        if state in (4, 5, 6, 7):
            level = state & 1
            if level != a or a != b:
                return Step(next=1, out0=b, emit=True)
            if state < 6:
                return Step(next=6 + b, out0=b)
            return Step(next=8 + b, out0=b, out1=1)
        level = state & 1 if state in (8, 9) else b
        if (level == 0 and (a or b)) or (a == 0 and b == 1):
            return Step(next=1, out0=1, emit=True)
        return Step(next=8 + b, out0=b, out1=1)
    return build(rule)
