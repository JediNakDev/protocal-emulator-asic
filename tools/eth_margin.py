# SPDX-License-Identifier: Apache-2.0
"""Characterize 10BASE-T receive margin of clock recovery (G7).

Drives the model of G7 (pe.model.ClockRecovery, which the random-program
tests check against the RTL cycle by cycle) with the eth10_rx profile
(pe.proto.eth.rx_clock_recovery) at 50 MHz, two samples per cycle, as the
chip and G2 run it: each strobe while locked is one received bit, and the
strobe that loses lock ends the record. Frames are preamble, SFD and random
bytes, at a random input phase, at 0 and +/- 200 ppm, followed by TP_IDL. A
frame counts as received if it arrives as one record holding every bit after
the SFD, followed by fewer than 8 bits from TP_IDL (dribble bits, which
pe.proto.eth.parse drops).

Jitter models, each transition displaced by:
  uniform  a uniform random amount in +/- J
  extreme  +J or -J at random (the worst case at the 802.3 limit)
  cable    5.5 ns of pattern-dependent jitter (late after a 100 ns pulse,
           early after a 50 ns one) plus a uniform amount in +/- (J - 5.5)

Run from the repository root:
  python3 tools/eth_margin.py              the standard table
  python3 tools/eth_margin.py extreme 15 188 150
Results are JSON lines on stdout. This is a model result; the cocotb tests
in test/test_eth.py check selected cases through the chip's pins.
"""

import json
import os
import random
import sys
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pe.model import ClockRecovery  # noqa: E402
from pe.proto import eth  # noqa: E402

CLK_NS = 20.0   # 50 MHz
PIN = 8         # RX; NEGSEL selects it, so pin 15 is its falling-edge sample
PPMS = (-200, 0, 200)

# Jitter model, J in ns, frame bytes, frames.
TABLE = [
    ("uniform", 13.5, 188, 3000),
    ("cable", 13.5, 188, 3000),
    ("extreme", 13.5, 72, 3000),
    ("extreme", 13.5, 188, 1500),
    ("extreme", 13.5, 1518, 150),
    ("extreme", 14.0, 188, 1500),
    ("extreme", 14.5, 188, 1500),
    ("extreme", 15.0, 188, 1500),
]


def transitions(bits, mode, jitter, ppm, rng, t0):
    """(time, level) of the Manchester transitions of `bits`, then TP_IDL:
    high for 300 ns after the last bit cell, then low."""
    half = 50.0 * (1 + ppm * 1e-6)
    halves = []
    for b in bits:
        halves += [1 - b, b]
    out, level, t, nominal = [], 0, t0, None
    for h in halves:
        if h != level:
            if mode == "extreme":
                d = rng.choice((-jitter, jitter))
            elif mode == "cable":
                d = rng.uniform(-(jitter - 5.5), jitter - 5.5)
                if nominal is not None:
                    d += 5.5 if t - nominal > 1.5 * half else -5.5
            else:
                d = rng.uniform(-jitter, jitter)
            out.append((t + d, h))
            nominal, level = t, h
        t += half
    if level == 0:
        out.append((t, 1))
    out.append((t + 300.0, 0))
    return out


def receive(edges):
    """Records of received bits, as G2 forms them from G7's strobes."""
    cr = ClockRecovery()
    ctrl, gain, inc_lo, inc_hi, win, sample = eth.rx_clock_recovery(PIN)
    cr.write_ctrl(ctrl)
    cr.write_gain(gain)
    cr.inc, cr.win, cr.sample = inc_lo | inc_hi << 8, win, sample
    records, cur, i, level = [], [], 0, 0
    end = edges[-1][0] + 1000.0

    def level_at(t):
        nonlocal i, level
        while i < len(edges) and edges[i][0] <= t:
            level = edges[i][1]
            i += 1
        return level

    k = 0
    while k * CLK_NS < end:
        s0 = level_at(k * CLK_NS)
        s1 = level_at(k * CLK_NS + CLK_NS / 2)
        n = cr.evaluate(s0 << PIN | s1 << 15)
        cr.commit(n)
        if n["strobe"]:
            if n["locked"]:
                cur.append(n["data"])
            elif cur:
                records.append(cur)
                cur = []
        k += 1
    if cur:
        records.append(cur)
    return records


def frame_ok(seed, mode, jitter, nbytes):
    rng = random.Random(seed)
    payload = [rng.getrandbits(1) for _ in range(8 * nbytes)]
    sfd = [1, 0] * 28 + [1, 0, 1, 0, 1, 0, 1, 1]
    ppm = PPMS[seed % len(PPMS)]
    edges = transitions(sfd + payload, mode, jitter, ppm, rng, 200.0 + rng.uniform(0, CLK_NS))
    records = receive(edges)
    if len(records) != 1:
        return False
    s = "".join(map(str, records[0]))
    i = s.find("10101011")
    tail = records[0][i + 8:] if i >= 0 else []
    return tail[:len(payload)] == payload and len(tail) - len(payload) < 8


def _job(args):
    return frame_ok(*args)


def run(mode, jitter, nbytes, frames, pool):
    jobs = [(9000 + s, mode, jitter, nbytes) for s in range(frames)]
    lost = sum(not ok for ok in pool.imap(_job, jobs))
    return dict(jitter=mode, ns=jitter, bytes=nbytes, frames=frames, lost=lost)


def main(argv):
    cases = TABLE if not argv else [(argv[0], float(argv[1]), int(argv[2]), int(argv[3]))]
    with Pool() as pool:
        for c in cases:
            print(json.dumps(run(*c, pool)), flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
