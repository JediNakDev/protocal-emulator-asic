# SPDX-License-Identifier: Apache-2.0
"""Waveform capture and replay (programs/capture.pasm, programs/replay.pasm):
any digital signal, recorded and played back exact to the cycle, unchanged or
edited by the Host."""

import random

import cocotb
from cocotb.triggers import RisingEdge

from chip import Chip, regs
from peers import uart_receive, uart_transmit
from pe.proto import waveform

CAPTURE_AT, REPLAY_AT = 0, 8
PIN_IN, PIN_OUT = 8, 10  # ui[6] in, uo[5] out


async def setup(dut, idle):
    """Engine 0 captures pin 8, idle at `idle`; engine 1 replays on pin 10."""
    chip = Chip(dut)
    await chip.start()
    chip.set_pin8(idle)
    await chip.set_quad()
    cap = await chip.load_program("capture", CAPTURE_AT)
    rep = await chip.load_program("replay", REPLAY_AT)
    await chip.pincfg(PIN_IN, regs.pin_filter(3))  # 8-cycle glitch filter
    await chip.pincfg(PIN_OUT, regs.OWNER_E1 | regs.DRIVE_ALWAYS)
    await chip.setup_engine(0, cap, CAPTURE_AT, shiftctrl=regs.fifo_mode(regs.FIFO_JOIN_RX),
                            in_base=PIN_IN)
    await chip.ereg(0, regs.CAPCFG, [PIN_IN | (3 << 4) | (1 << 6), 0])  # both edges, flag 0
    await chip.setup_engine(1, rep, REPLAY_AT, shiftctrl=regs.AUTOPULL | regs.OUT_RIGHT |
                            regs.fifo_mode(regs.FIFO_JOIN_TX), out_base=PIN_OUT, out_count=1)
    return chip


async def drive(chip, level, intervals):
    """Drive pin 8: `level`, then an edge after each interval. Returns the
    cycle of each edge."""
    chip.set_pin8(level)
    t, times = 0, []
    for n in intervals:
        for _ in range(n):
            await RisingEdge(chip.dut.clk)
            t += 1
        level ^= 1
        chip.set_pin8(level)
        times.append(t)
    return times


async def record(chip, n_edges, timeout=2_000_000):
    """Collect capture words for `n_edges` edges, as the Host drains them."""
    words, idle = [], 0
    while len(words) < 1 + 2 * n_edges:
        _, rx = await chip.levels(0)
        if rx:
            words += await chip.pop(0, rx)
            idle = 0
        else:
            await chip.cycles(20)
            idle += 20
            assert idle < timeout, f"captured {len(words)} words"
    assert not (await chip.read8(regs.STICKY)) & (regs.STK_CAP_OVR0 | regs.STK_RX_OVF0), "capture data was lost"
    return waveform.from_capture(words)


async def watch(chip, n_edges):
    """Cycle of each change of pin 10, from the first one on."""
    clk, prev, t, times = chip.dut.clk, None, 0, []
    while len(times) < n_edges:
        await RisingEdge(clk)
        t += 1
        v = (chip.uo() >> 5) & 1
        if prev is not None and v != prev:
            times.append(t)
        prev = v
    return times


async def play(chip, words):
    """Fill the transmit queue, start engine 1 and keep the queue fed."""
    await chip.push(1, words[:8])
    await chip.enable(3)
    i = 8
    while i < len(words):
        tx, _ = await chip.levels(1)
        if tx < 8:
            await chip.push(1, words[i:i + 8 - tx])
            i += 8 - tx
        else:
            await chip.cycles(20)


@cocotb.test()
async def test_capture_and_replay_exact(dut):
    """Random edges, from 8 cycles apart to more than 2**16: every captured
    and every replayed interval equals the driven one exactly."""
    chip = await setup(dut, 0)
    rng = random.Random(7)
    intervals = []
    for _ in range(12):
        intervals += [rng.randint(8, 20) for _ in range(rng.randint(1, 3))]  # a burst
        intervals.append(rng.randint(1500, 4000))                             # Host catches up
    intervals[5] = 70_000   # longer than one replay word and than 16 bits
    intervals[17] = 100_003
    await chip.enable(1)
    await chip.cycles(10)
    driver = cocotb.start_soon(drive(chip, 0, intervals))
    level, times = await record(chip, len(intervals))
    driven = await driver
    assert level == 0
    assert waveform.intervals(times) == waveform.intervals(driven)

    watcher = cocotb.start_soon(watch(chip, len(times)))
    await play(chip, waveform.to_replay(level, times, lead=100))
    replayed = await watcher
    assert waveform.intervals(replayed) == waveform.intervals(times)


@cocotb.test()
async def test_edit_and_replay_uart(dut):
    """Fault injection without protocol hardware: capture two UART bytes,
    invert one data bit's span on the Host, and replay; an independent UART
    receiver decodes the edited byte."""
    chip = await setup(dut, 1)
    bit = 200  # 250 kbaud: the Host drains two words per edge as they come
    await chip.enable(1)
    await chip.cycles(20)
    data = [0x55, 0xA3]
    sender = cocotb.start_soon(uart_transmit(dut.clk, chip.set_pin8, bit, data))
    edges_per_byte = [sum(1 for a, b in zip(f, f[1:]) if a != b)
                      for f in ([1, 0] + [(d >> i) & 1 for i in range(8)] + [1] for d in data)]
    level, times = await record(chip, sum(edges_per_byte))
    await sender
    assert level == 1

    # Byte 1 starts at its start bit's falling edge; flip its data bit 3.
    start1 = times[edges_per_byte[0]]
    flip = start1 + (1 + 3) * bit
    level, times = waveform.invert_span(level, times, flip, flip + bit)

    rx = cocotb.start_soon(uart_receive(dut.clk, lambda: (chip.uo() >> 5) & 1, bit, len(data)))
    await play(chip, waveform.to_replay(level, times, lead=200))
    assert await rx == [0x55, 0xA3 ^ (1 << 3)]


@cocotb.test()
async def test_capture_backpressure_reports_loss(dut):
    """A full receive queue must not hide a lost edge."""
    chip = await setup(dut, 0)
    await chip.enable(1)
    await chip.cycles(20)
    assert await chip.pop(0, 1) == [0]
    for i in range(6):
        chip.set_pin8((i + 1) & 1)
        await chip.cycles(100)
    assert await chip.levels(0) == (0, 8)
    sticky = await chip.read8(regs.STICKY)
    words = await chip.pop(0, 8)
    await chip.cycles(30)
    _, remaining = await chip.levels(0)
    words += await chip.pop(0, remaining)
    times = [lo | (hi << 16) for lo, hi in zip(words[::2], words[1::2])]
    complete = len(times) == 6 and waveform.intervals(times) == [100] * 5
    assert complete or sticky & (regs.STK_CAP_OVR0 | regs.STK_RX_OVF0), f"unreported edge loss: {times}, sticky={sticky:#x}"


@cocotb.test()
async def test_constant_recording_replay(dut):
    """A recording with no transitions must retain its initial level."""
    for level in (0, 1):
        chip = await setup(dut, level)
        words = waveform.to_replay(*waveform.from_capture([level]))
        await chip.push(1, words)
        await chip.enable(2)
        await chip.cycles(20)
        for _ in range(20):
            await RisingEdge(dut.clk)
            assert (chip.uo() >> 5) & 1 == level


@cocotb.test()
async def test_invert_span_replay_transitions(dut):
    """An inversion preserves internal transitions and cancels boundary edges."""
    for start, end, expected in ((120, 280, [100, 120, 200, 280, 300]),
                                 (100, 300, [200])):
        chip = await setup(dut, 0)
        level, times = waveform.invert_span(0, [100, 200, 300], start, end)
        watcher = cocotb.start_soon(watch(chip, len(times)))
        await play(chip, waveform.to_replay(level, times, lead=100))
        observed = await watcher
        assert waveform.intervals(observed) == waveform.intervals(expected)
        # Catch extra transitions after the expected final edge.
        await chip.cycles(400)
        assert (chip.uo() >> 5) & 1 == (level ^ (len(expected) & 1))


@cocotb.test()
async def test_capture_minimum_burst_all_phases(dut):
    """Four minimum-width edges survive every phase of the five-cycle loop."""
    for phase in range(5):
        chip = await setup(dut, 0)
        await chip.enable(1)
        await chip.cycles(20)
        assert await chip.pop(0, 1) == [0]
        await chip.cycles(phase)
        await drive(chip, 0, [8] * 4)
        await chip.cycles(10)
        words = await chip.pop(0, 8)
        _, times = waveform.from_capture([0] + words)
        assert not (await chip.read8(regs.STICKY)) & (regs.STK_CAP_OVR0 | regs.STK_RX_OVF0)
        assert waveform.intervals(times) == [8] * 3


@cocotb.test()
async def test_capture_sustained_rate(dut):
    """A continuous RX read keeps up at one edge per 80 cycles."""
    chip = await setup(dut, 0)
    await chip.enable(1)
    await chip.cycles(20)
    assert await chip.pop(0, 1) == [0]
    driver = cocotb.start_soon(drive(chip, 0, [80] * 40))
    await chip.cycles(160)  # prefill before starting the streaming read
    # Four bytes per timestamp at HSCK=f_clk/10: exactly 80 cycles per edge.
    data = (await chip.xfer([0x80 | regs.engine_reg(0, regs.FIFO)] + [0] * 160, half=5))[1:]
    words = [lo | (hi << 8) for lo, hi in zip(data[::2], data[1::2])]
    _, times = waveform.from_capture([0] + words)
    await driver
    assert not (await chip.read8(regs.STICKY)) & (regs.STK_CAP_OVR0 | regs.STK_RX_OVF0 | regs.STK_RX_UNF0)
    assert waveform.intervals(times) == [80] * 39
