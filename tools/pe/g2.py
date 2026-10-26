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
    return build(can_destuffer_rule)


def can_destuffer_rule(state, in0, in1):
    """The rule of can_destuffer, shared with can_line."""
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


def can_line():
    """CAN bit destuffer for a node that also transmits. in0 = sampled bus
    bit, fed once per bit; in1 = the node's own TXD at that sample.
    Emits every data bit and drops stuff bits, like can_destuffer.
    out0 = the sampled bus bit, on every step, so a program can test the
    last bit with `jmp pin` whether it was a stuff bit or not.
    out1 = an error at this bit: six equal bits (end of frame, a stuff error
    or an error flag), or a bit error, TXD dominant while the bus is recessive.
    States as in can_destuffer."""
    def rule(state, in0, in1):
        s = can_destuffer_rule(state, in0, in1)
        s.out0 = in0
        s.out1 |= int(in1 == 0 and in0 == 1)
        return s
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


def recovered_bits():
    """Bits from clock recovery (G7), stepping on its strobes (step mode 3):
    in0 = recovered data (source 18), in1 = locked (source 19). While locked,
    each strobe emits the data bit and holds out1 low; the strobe that loses
    lock emits nothing and raises out1, which also marks the idle line
    before the first lock when G2_STATE starts with out1 set. One state."""
    def rule(state, in0, in1):
        if in1:
            return Step(next=0, out0=in0, emit=True)
        return Step(next=0, out1=1)
    return build(rule)
