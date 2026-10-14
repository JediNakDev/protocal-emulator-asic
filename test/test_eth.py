# SPDX-License-Identifier: Apache-2.0
"""10BASE-T transmit and receive programs against independent Manchester
peers, at 40 MHz."""

import os
import random
import zlib

import cocotb
from cocotb.triggers import RisingEdge, Timer
from cocotb.utils import get_sim_time

from chip import Chip, assemble, program_source, regs
from pe import g2 as g2lib
from pe.proto import eth

TDP, TDM, RX = 2, 3, 8
CLK_PS = 25000  # 40 MHz
GATES = os.environ.get("GATES") == "yes"
DST = bytes.fromhex("ffffffffffff")
SRC = bytes.fromhex("020000000001")


async def new_chip(dut):
    chip = Chip(dut, clk_ps=CLK_PS)
    await chip.start()
    await chip.set_quad()
    return chip


def wire_frame(payload):
    """Independent reference: preamble, SFD, header, padded payload, FCS."""
    body = DST + SRC + b"\x08\x00" + payload
    body += bytes(max(0, 60 - len(body)))
    return bytes([0x55] * 7 + [0xD5]) + body + zlib.crc32(body).to_bytes(4, "little")


async def decode_tx(chip, n_cycles):
    """Sample TD+/TD- every cycle and decode Manchester (IEEE 802.3)."""
    dut = chip.dut
    s = []
    for _ in range(n_cycles):
        await RisingEdge(dut.clk)
        v = int(dut.uio_out.value)
        s.append(((v >> TDP) & 1, (v >> TDM) & 1))
    tdp = [a for a, _ in s]
    t0 = next(i for i in range(1, len(tdp)) if tdp[i] != tdp[i - 1])  # first mid-bit edge
    bits, t = [], t0
    while t + 1 < len(tdp) and tdp[t] != tdp[t - 1]:
        bits.append(tdp[t])
        t += 4
    frame_samples = s[t0 - 2:t - 2]
    differential = all(a != b for a, b in frame_samples)
    idle_high = tdp[t - 2:t + 8]
    return bits, differential, idle_high, s[-1]


async def tx_setup(chip):
    prog = await chip.load_program("eth10_tx")
    await chip.setup_engine(0, prog, shiftctrl=regs.AUTOPULL | regs.OUT_RIGHT
                            | regs.fifo_mode(regs.FIFO_JOIN_TX), side_base=TDP,
                            execcfg=prog.label("table"), statuscfg=1)
    await chip.ereg(0, regs.SETPIN, TDP | (2 << 4))
    await chip.force_asm(0, "set pindirs, 3 side 0", side_count=2)
    await chip.pincfg(TDP, regs.DRIVE_PUSH_PULL)
    await chip.pincfg(TDM, regs.DRIVE_PUSH_PULL)


@cocotb.test()
async def test_eth_transmit(dut):
    chip = await new_chip(dut)
    await tx_setup(chip)
    payload = bytes(range(46)) + b"protocol emulator"
    wire = eth.frame(DST, SRC, 0x0800, payload)
    assert wire == wire_frame(payload)
    words = eth.tx_words(wire)
    await chip.push(0, words[:8])
    sampler = cocotb.start_soon(decode_tx(chip, len(wire) * 8 * 4 + 600))
    await chip.enable(1)
    await chip.stream(0, words[8:], 0, depth=8)
    bits, differential, idle_high, last = await sampler
    got = bytes(sum(bits[8 * k + j] << j for j in range(8)) for k in range(len(bits) // 8))
    assert len(bits) == len(wire) * 8, (len(bits), len(wire) * 8)
    assert got == wire
    assert differential, "TD- must be the complement of TD+ during the frame"
    assert all(idle_high), f"TP_IDL: TD+ high after the last bit, got {idle_high}"
    assert last == (0, 0), "idle after TP_IDL"


async def drive_rx(chip, wire, jitter_ns, start_ns, seed):
    """Drive the RX pin with Manchester at 100 ns per bit, each transition
    displaced by up to +/- jitter_ns, then TP_IDL and idle."""
    rng = random.Random(seed)
    halves = []
    for byte in wire:
        for j in range(8):
            b = (byte >> j) & 1
            halves += [1 - b, b]
    await Timer(start_ns, unit="ns")
    t = 0.0
    last = 0.0  # time of the last transition
    level = 0
    chip.set_pin8(0)
    for i, h in enumerate(halves):
        if h != level:
            d = rng.uniform(-jitter_ns, jitter_ns) if jitter_ns else 0.0
            await Timer(round((t + d) * 1000) - round(last * 1000), unit="ps")
            last = t + d
            chip.set_pin8(h)
            level = h
        t += 50.0
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
    await chip.write(regs.G2_DATA, g2lib.manchester_receiver())
    await chip.write(regs.G2_IN0, [RX, regs.PIN_NEG])
    await chip.write(regs.G2_STATE, 8)  # idle, line low
    await chip.write(regs.G2_CTRL, regs.G2_ENABLE | regs.G2_OWNER1 | regs.G2_STEP_CYCLE | regs.G2_EMIT)
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
    assert res is not None, record
    body, ok = res
    assert body == wire[8:-4], (body.hex(), wire[8:-4].hex())
    assert ok, "FCS"


async def no_overflow(chip):
    assert not (await chip.read8(regs.STICKY)) & regs.STK_RX_OVF1, "receive overflow"


async def receive(dut, payload, jitter_ns, start_ns, seed=1):
    chip = await new_chip(dut)
    chip.set_pin8(0)
    await rx_setup(chip)
    wire = wire_frame(payload)
    cocotb.start_soon(drive_rx(chip, wire, jitter_ns, start_ns, seed))
    (record,), words = await collect_records(chip, 1)
    check_frame(record, wire)
    await no_overflow(chip)


@cocotb.test()
async def test_eth_receive(dut):
    await receive(dut, bytes(range(46)) + b"hello", jitter_ns=0, start_ns=1000)


@cocotb.test()
async def test_eth_receive_jitter(dut):
    """Each transition displaced by up to +/- 8 ns. The decoder's margin is
    12.5 ns between two consecutive transitions; in simulation it passes at
    +/- 9 ns per transition and fails at +/- 10 ns."""
    await receive(dut, bytes(reversed(range(60))), jitter_ns=8, start_ns=1003, seed=7)


@cocotb.test()
async def test_eth_receive_phases(dut):
    """The input phase relative to the clock must not matter."""
    for k, start in enumerate((1001.0, 1006.25, 1012.5, 1018.75, 1023.0)):
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
    sampler = cocotb.start_soon(decode_tx(chip, len(wire) * 8 * 4 + 600))
    await chip.push(0, words[:8])
    await chip.stream(0, words[8:], 0, depth=8)
    bits, differential, idle_high, last = await sampler
    assert bits == [(b >> j) & 1 for b in wire for j in range(8)]
    assert differential and all(idle_high) and last == (0, 0)
    end = get_sim_time("ns")
    (t, width, tdm), = await td_pulses(dut, 1)
    assert width == 100 and tdm == 0
    assert 8e6 <= t - end <= 24e6, t - end
