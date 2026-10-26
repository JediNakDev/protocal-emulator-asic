# SPDX-License-Identifier: Apache-2.0
"""10BASE-T transmit and receive programs against independent Manchester
peers in absolute time, at the 50 MHz clock profile."""

import os
import random
import zlib

import cocotb
from cocotb.triggers import Timer
from cocotb.utils import get_sim_time

from chip import MHZ_50, Chip, fails_at_wrong_clock, regs
from pe import g2 as g2lib
from pe.proto import eth
from peers import Wave, now_ns, sleep_ns

PROFILE = MHZ_50
TDP, TDM, PHASE, RX = 2, 3, 4, 8
BIT_NS = 100.0
TX_TOLERANCE_NS = 2.0  # transmitted transitions from the 100 ns grid
TP_IDL_NS = (250, 350)  # TD+ high after the last bit cell, 802.3 14.3.1.2.1
GATES = os.environ.get("GATES") == "yes"
DST = bytes.fromhex("ffffffffffff")
SRC = bytes.fromhex("020000000001")


async def new_chip(dut, hz=PROFILE):
    chip = Chip(dut, hz)
    await chip.start()
    await chip.set_quad()
    return chip


def wire_frame(payload):
    """Independent reference: preamble, SFD, header, padded payload, FCS."""
    body = DST + SRC + b"\x08\x00" + payload
    body += bytes(max(0, 60 - len(body)))
    return bytes([0x55] * 7 + [0xD5]) + body + zlib.crc32(body).to_bytes(4, "little")


class TxMonitor:
    """Records TD+ and TD- from construction on and decodes one frame in
    absolute time: the first TD+ change is the mid-bit transition of the
    first preamble bit; mid-bit transitions must follow every 100 ns, any
    other transition only at bit boundaries, TD- must be the complement of
    TD+ until the end of TP_IDL, and TP_IDL must hold TD+ high for
    250-350 ns after the last bit cell, followed by idle (both low)."""

    def __init__(self, dut):
        self.tdp = Wave(dut.uio_out, TDP)
        self.tdm = Wave(dut.uio_out, TDM)

    async def frame(self, n_bits, timeout_ns=None):
        timeout_ns = timeout_ns or (n_bits * BIT_NS + 20_000)
        t_end = now_ns() + timeout_ns
        while not self.tdp.times and now_ns() < t_end:
            await sleep_ns(1000)
        assert self.tdp.times, "no frame"
        t0 = self.tdp.times[0]
        await sleep_ns(max(0, t0 + n_bits * BIT_NS + 1000 - now_ns()))
        return self.decode(t0, n_bits)

    def decode(self, t0, n_bits):
        bits, out = [], []
        tol = TX_TOLERANCE_NS
        for k in range(n_bits):
            tk = t0 + k * BIT_NS
            near = self.tdp.edges(tk - BIT_NS / 4, tk + BIT_NS / 4)
            if len(near) != 1 or abs(near[0] - tk) > tol:
                out.append(f"timing: Manchester mid-bit {k} expected at {tk:.1f} ns, edges {near}")
                break
            bits.append(self.tdp.at(near[0]))
        t_last = t0 + (n_bits - 1) * BIT_NS + BIT_NS / 2  # end of the last bit cell
        for t in self.tdp.edges(t0, t_last - BIT_NS / 4):
            phase = (t - t0) % BIT_NS
            if min(phase, BIT_NS - phase) > tol and abs(phase - BIT_NS / 2) > tol:
                out.append(f"timing: TD+ transition off the bit grid at {t:.1f} ns")
                break
        rest = self.tdp.edges(t_last - BIT_NS / 4)
        if not out:
            if self.tdp.at(t_last + 1) != 1 or len(rest) == 0 or rest[-1] < t_last:
                out.append("TP_IDL: TD+ not high after the last bit")
            else:
                high = rest[-1] - t_last
                if not TP_IDL_NS[0] <= high <= TP_IDL_NS[1]:
                    out.append(f"timing: TP_IDL high for {high:.1f} ns")
                end = rest[-1]
                for t in self.tdp.edges(t0 - BIT_NS, end) + self.tdm.edges(t0 - BIT_NS, end):
                    if self.tdp.at(t) == self.tdm.at(t) and t < end:
                        out.append(f"TD- is not the complement of TD+ at {t:.1f} ns")
                        break
                if self.tdp.at(end) or self.tdm.at(end):
                    out.append("idle after TP_IDL: TD+ and TD- low")
        return bits, out


def bits_of(wire):
    return [(b >> j) & 1 for b in wire for j in range(8)]


async def tx_setup(chip):
    prog = await chip.load_program("eth10_tx")
    await chip.setup_engine(0, prog, shiftctrl=regs.AUTOPULL | regs.OUT_RIGHT
                            | regs.fifo_mode(regs.FIFO_JOIN_TX), side_base=TDP,
                            execcfg=prog.label("table"), statuscfg=1)
    # Mid-bit transitions: S (PHASE, never driven) flips TD+ and TD- half a
    # cycle after the program writes it.
    mask = 1 << TDP | 1 << TDM
    await chip.write(regs.NEGOUT, [PHASE, mask & 0xFF, mask >> 8])
    await chip.ereg(0, regs.SETPIN, TDP | (3 << 4))
    await chip.force_asm(0, "set pindirs, 3 side 0", side_count=3)
    await chip.pincfg(TDP, regs.DRIVE_PUSH_PULL)
    await chip.pincfg(TDM, regs.DRIVE_PUSH_PULL)


async def transmit_session(dut, hz=PROFILE):
    chip = await new_chip(dut, hz)
    await tx_setup(chip)
    payload = bytes(range(46)) + b"protocol emulator"
    wire = eth.frame(DST, SRC, 0x0800, payload)
    assert wire == wire_frame(payload)
    words = eth.tx_words(wire)
    await chip.push(0, words[:8])
    monitor = TxMonitor(dut)
    await chip.enable(1)
    await chip.stream(0, words[8:], 0, depth=8)
    bits, violations = await monitor.frame(len(wire) * 8)
    assert not violations, violations[:5]
    assert bits == bits_of(wire)


@cocotb.test()
async def test_eth_transmit(dut):
    await transmit_session(dut)


@cocotb.test()
async def test_eth_wrong_clock(dut):
    """At 40 MHz the 50 MHz transmit program sends 8 Mbit/s, which the
    monitor rejects."""
    msg = await fails_at_wrong_clock(lambda hz: transmit_session(dut, hz), PROFILE)
    assert "timing: Manchester" in msg, msg


async def drive_rx(chip, wire, jitter_ns, start_ns, seed, isi_ns=0.0, ppm=0.0, extreme=False):
    """Drive the RX pin with Manchester at 100 ns per bit (scaled by `ppm`),
    then TP_IDL and idle. Each transition is displaced by up to +/- jitter_ns
    at random, and by isi_ns of pattern-dependent jitter: late after a
    100 ns pulse, early after a 50 ns one, as a band-limited cable does."""
    rng = random.Random(seed)
    half_ns = 50.0 * (1 + ppm * 1e-6)
    halves = []
    for byte in wire:
        for j in range(8):
            b = (byte >> j) & 1
            halves += [1 - b, b]
    await Timer(start_ns, unit="ns")
    t = 0.0
    last = 0.0  # time of the last transition
    nominal = None  # its time without jitter
    level = 0
    chip.set_pin8(0)
    for i, h in enumerate(halves):
        if h != level:
            d = (rng.choice((-jitter_ns, jitter_ns)) if extreme else
                 rng.uniform(-jitter_ns, jitter_ns)) if jitter_ns else 0.0
            if isi_ns and nominal is not None:
                d += isi_ns if t - nominal > 1.5 * half_ns else -isi_ns
            await Timer(round((t + d) * 1000) - round(last * 1000), unit="ps")
            last, nominal = t + d, t
            chip.set_pin8(h)
            level = h
        t += half_ns
    # TP_IDL: high from the end of the last bit cell for at least 250 ns.
    await Timer(round((t - last) * 1000), unit="ps")
    chip.set_pin8(1)
    await Timer(300, unit="ns")
    chip.set_pin8(0)


async def link_pulse(chip, start_ns):
    """A normal link pulse: 100 ns high on an idle line."""
    await Timer(start_ns, unit="ns")
    chip.set_pin8(1)
    await Timer(100, unit="ns")
    chip.set_pin8(0)


async def rx_setup(chip):
    prog = await chip.load_program("eth10_rx")
    await chip.setup_engine(1, prog, shiftctrl=regs.AUTOPUSH | regs.fifo_mode(regs.FIFO_JOIN_RX), thresh=15)
    await chip.write(regs.NEGSEL, RX)
    await chip.write(regs.G2_ADDR, 0)
    await chip.write(regs.G2_DATA, g2lib.recovered_bits())
    await chip.write(regs.G2_IN0, [regs.G2_SRC_CR_DATA, regs.G2_SRC_CR_LOCKED])
    await chip.write(regs.G2_STATE, 0x20)  # out1: not locked
    await chip.write(regs.G2_CTRL, regs.G2_ENABLE | regs.G2_OWNER1 | regs.G2_STEP_STROBE | regs.G2_EMIT)
    await chip.write(regs.CR_CTRL, eth.rx_clock_recovery(RX))
    await chip.enable(2)


async def collect_records(chip, n, pauses=None, timeout=400000):
    """Read receive words until n records have ended. The Host never looks
    at flags or timing: the trailers delimit the records. `pauses` yields
    extra idle cycles between reads, as a busy Host would have."""
    words, waited = [], 0
    while True:
        records, _ = eth.split_records(words)
        if len(records) >= n:
            return records, words
        _, rx = await chip.levels(1)
        if rx:
            words += await chip.pop(1, rx)
        else:
            await chip.cycles(20)
            waited += 20
            if waited > timeout:
                raise TimeoutError(f"{len(records)} records, {len(words)} words")
        if pauses:
            await chip.cycles(next(pauses))


def check_frame(record, wire):
    res = eth.parse(record)
    assert res is not None, f"unparsable record {record[:8]}, {len(record)} words"
    body, ok = res
    assert body == wire[8:-4], (body.hex(), wire[8:-4].hex())
    assert ok, "FCS"


async def no_overflow(chip):
    assert not (await chip.read8(regs.STICKY)) & regs.STK_RX_OVF1, "receive overflow"


async def receive(dut, payload, jitter_ns, start_ns, seed=1, hz=PROFILE, isi_ns=0.0, ppm=0.0, extreme=False):
    chip = await new_chip(dut, hz)
    chip.set_pin8(0)
    await rx_setup(chip)
    wire = wire_frame(payload)
    cocotb.start_soon(drive_rx(chip, wire, jitter_ns, start_ns, seed, isi_ns, ppm, extreme))
    # A bit takes 5 cycles at 50 MHz: allow the whole frame plus margin.
    records, words = await collect_records(chip, 1, timeout=20_000 + 50 * len(wire))
    assert len(records) == 1, f"frame split into {len(records)} records"
    check_frame(records[0], wire)
    await no_overflow(chip)


@cocotb.test()
async def test_eth_receive(dut):
    await receive(dut, bytes(range(46)) + b"hello", jitter_ns=0, start_ns=1000)


# IEEE 802.3 clause 14: the receiver must accept transitions displaced by
# up to +/- 13.5 ns. Clock recovery samples on both edges at 50 MHz (a 10 ns
# grid) and corrects its phase from the transition nearest where it expects
# one, so what limits it is the sampling: a 50 ns half bit, minus both
# edges' jitter, minus the 10 ns between samples. tools/eth_margin.py
# measures it on the model of G7 at every phase and 0 and +/- 200 ppm: no
# frame lost up to +/- 14 ns with every transition at an extreme of the
# range (docs/protocols.md#ethernet-receive-margin).
JITTER_NS = 13.5


@cocotb.test()
async def test_eth_receive_jitter(dut):
    """Each transition displaced at random by up to +/- 13.5 ns, at several
    input phases and at the 802.3 frequency tolerance (+/- 100 ppm at each
    end, so +/- 200 ppm between them)."""
    cases = [(1003.0, 0), (1009.4, 200), (1016.9, -200), (1021.3, 200)]
    for k, (start, ppm) in enumerate(cases):
        await receive(dut, bytes(reversed(range(60))), jitter_ns=JITTER_NS, start_ns=start, seed=7 + k, ppm=ppm)


@cocotb.test()
async def test_eth_receive_extreme_jitter(dut):
    """Every transition at one endpoint of the permitted jitter range, at
    random. At 40 MHz with 12.5 ns between samples this pattern defeated the
    receiver (B12): as the sampling grid slides past the transitions at
    +/- 200 ppm the two jitter values quantize unevenly, which a receiver
    that starts cold cannot tell from a phase error."""
    for k, ppm in enumerate((-200, 0, 200)):
        await receive(dut, bytes(reversed(range(60))), jitter_ns=JITTER_NS,
                      start_ns=1003 + k * 7.1, seed=7 + k, ppm=ppm, extreme=True)
    rng = random.Random(12)
    for k, (start, ppm) in enumerate(((1001.3, 200), (1005.7, -200))):
        payload = bytes(rng.getrandbits(8) for _ in range(166))
        await receive(dut, payload, jitter_ns=JITTER_NS, start_ns=start, seed=30 + k,
                      ppm=ppm, extreme=True)


@cocotb.test()
async def test_eth_receive_cable_jitter(dut):
    """The 13.5 ns as a cable delivers it: 5.5 ns of pattern-dependent jitter
    (a transition after a 100 ns pulse is late, after a 50 ns pulse early)
    plus up to +/- 8 ns at random."""
    rng = random.Random(11)
    for k, start in enumerate((1002.0, 1013.7)):
        payload = bytes(rng.getrandbits(8) for _ in range(60))
        await receive(dut, payload, jitter_ns=JITTER_NS - 5.5, isi_ns=5.5, start_ns=start, seed=20 + k,
                      ppm=(200, -200)[k])


@cocotb.test(skip=GATES)
async def test_eth_receive_long_frame(dut):
    """A maximum-length frame (1518 bytes) at +200 ppm with +/- 13.5 ns of
    jitter: the loop tracks the frequency offset over 12,000 bits."""
    rng = random.Random(5)
    payload = bytes(rng.getrandbits(8) for _ in range(1500))
    await receive(dut, payload, jitter_ns=JITTER_NS, start_ns=1004.4, seed=5, ppm=200)


@cocotb.test()
async def test_eth_receive_wrong_clock(dut):
    """At 40 MHz the 50 MHz clock recovery settings expect 10 samples per bit
    where there are 8: a 20% frequency error, far outside what the loop
    tracks, so the frame that passes at 50 MHz breaks up."""
    msg = await fails_at_wrong_clock(
        lambda hz: receive(dut, bytes(reversed(range(60))), jitter_ns=JITTER_NS, start_ns=1003, seed=7, hz=hz),
        PROFILE)
    assert "unparsable record" in msg or "records" in msg, msg


@cocotb.test()
async def test_eth_receive_phases(dut):
    """The input phase relative to the clock must not matter."""
    for k, start in enumerate((1001.0, 1005.0, 1010.0, 1015.0, 1019.0)):
        await receive(dut, bytes([k] * 46), jitter_ns=0, start_ns=start, seed=k)


@cocotb.test()
async def test_eth_receive_back_to_back(dut):
    """Frames at the minimum interframe gap (9.6 us), with link pulses in
    between and a Host that pauses between reads: each frame's record has
    exactly its own bits, whenever the Host reads it."""
    chip = await new_chip(dut)
    chip.set_pin8(0)
    await rx_setup(chip)
    rng = random.Random(3)
    wires = [wire_frame(bytes(rng.getrandbits(8) for _ in range(n))) for n in (46, 60, 50)]

    async def traffic():
        await link_pulse(chip, 1000)
        for k, wire in enumerate(wires):
            await drive_rx(chip, wire, 0, 2000 if k == 0 else 9600, seed=k)
        await link_pulse(chip, 16000)

    cocotb.start_soon(traffic())
    pauses = iter(rng.randrange(0, 160) for _ in range(100000))
    records, words = await collect_records(chip, 5, pauses)
    pulses = [records[0], records[4]]
    assert all(r is not None and len(r) < 8 and eth.parse(r) is None for r in pulses), pulses
    for record, wire in zip(records[1:4], wires):
        check_frame(record, wire)
    await no_overflow(chip)


async def td_pulses(dut, n):
    """Watch TD+/TD- through their changes only (no per-cycle sampling, so
    milliseconds of idle line simulate quickly). Returns n (start ns, width
    ns, TD- during the pulse) for the TD+ pulses."""
    pulses, rise, tdm = [], None, None
    while len(pulses) < n:
        await dut.uio_out.value_change
        v = int(dut.uio_out.value)
        tdp, now = (v >> TDP) & 1, get_sim_time("ns")
        if tdp and rise is None:
            rise, tdm = now, (v >> TDM) & 1
        elif not tdp and rise is not None:
            pulses.append((rise, now - rise, tdm))
            rise = None
    return pulses


# 33 ms of simulated time: too long for the gate-level run, which the
# random comparisons cover for instruction timing.
@cocotb.test(skip=GATES)
async def test_eth_link_pulses(dut):
    """IEEE 802.3 14.2.1.1: an idle transmitter sends a 100 ns link pulse
    every 16 +/- 8 ms. A frame queued while idle starts at once, and the
    pulses resume after it."""
    chip = await new_chip(dut)
    await tx_setup(chip)
    watch = cocotb.start_soon(td_pulses(dut, 1))
    await chip.enable(1)
    start = get_sim_time("ns")
    (t, width, tdm), = await watch
    assert width == 100 and tdm == 0
    assert 8e6 <= t - start <= 24e6, t - start
    # A frame between pulses: it starts within 10 cycles of its command.
    wire = eth.frame(DST, SRC, 0x0800, bytes(46))
    words = eth.tx_words(wire)
    monitor = TxMonitor(dut)
    await chip.push(0, words[:8])
    await chip.stream(0, words[8:], 0, depth=8)
    bits, violations = await monitor.frame(len(wire) * 8)
    assert not violations, violations[:5]
    assert bits == bits_of(wire)
    end = get_sim_time("ns")
    (t, width, tdm), = await td_pulses(dut, 1)
    assert width == 100 and tdm == 0
    assert 8e6 <= t - end <= 24e6, t - end
