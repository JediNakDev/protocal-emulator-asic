# SPDX-License-Identifier: Apache-2.0
"""Host side of waveform capture and replay (programs/capture.pasm and
programs/replay.pasm).

A waveform is an initial level and the cycle times of its edges; every edge
inverts the level.
"""

REPLAY_MIN = 3                  # cycles one replay word holds its level at least
REPLAY_MAX = REPLAY_MIN + 0x7FFF


def from_capture(words):
    """Receive words of capture.pasm -> (initial level, edge times). The
    32-bit counter wraps every 2**32 cycles; times are unwrapped so that they
    keep increasing, which assumes edges less than 2**32 cycles apart."""
    level, times, wraps, prev = words[0] & 1, [], 0, None
    for lo, hi in zip(words[1::2], words[2::2]):
        raw = lo | (hi << 16)
        if prev is not None and raw < prev:
            wraps += 1
        prev = raw
        times.append(raw + (wraps << 32))
    return level, times


def intervals(times):
    return [b - a for a, b in zip(times, times[1:])]


def invert_span(level, times, start, end):
    """Invert the waveform between two times: as if the line were driven to
    the opposite level from `start` until `end`. Returns new (level, times)."""
    edges = [t for t in times if not start <= t < end]
    edges += [start, end]
    edges.sort()
    out = []
    for t in edges:  # coinciding edges cancel
        if out and out[-1] == t:
            out.pop()
        else:
            out.append(t)
    return level, out


def to_replay(level, times, lead=REPLAY_MIN):
    """Replay words: `level` for `lead` cycles, then each edge at the same
    distance from the previous one as in `times`. The level after the last
    edge holds until the queue runs dry."""
    runs = [lead] + intervals(times) + [REPLAY_MIN]
    words = []
    for i, run in enumerate(runs):
        if run < REPLAY_MIN:
            raise ValueError(f"run of {run} cycles is shorter than {REPLAY_MIN}")
        lv = level ^ (i & 1)
        while run > REPLAY_MAX:
            part = REPLAY_MAX if run - REPLAY_MAX >= REPLAY_MIN else run - REPLAY_MIN
            words.append(lv | ((part - REPLAY_MIN) << 1))
            run -= part
        words.append(lv | ((run - REPLAY_MIN) << 1))
    return words
