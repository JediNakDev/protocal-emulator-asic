# SPDX-License-Identifier: Apache-2.0
"""General primitives: checksum shift register (G1), bit state machine (G2),
edge capture (G3) and input options (G6)."""

import binascii
import zlib

import cocotb
from cocotb.triggers import FallingEdge, RisingEdge, Timer

from chip import Chip, regs
from pe import g2 as g2lib


async def new_chip(dut):
    chip = Chip(dut)
    await chip.start()
    await chip.set_quad()
    return chip


def crc16_usb(bits):
    """Reference CRC-16/USB over a bit list (bitwise, reflected)."""
    crc = 0xFFFF
    for b in bits:
        fb = (crc ^ b) & 1
        crc >>= 1
        if fb:
            crc ^= 0xA001
    return crc ^ 0xFFFF


def lfsr_model(bits, poly, value, mode):
    """Spec model of LFSR modes 1-3 in left direction (docs/spec.md)."""
    out = []
    for d in bits:
        par = bin(value & poly).count("1") & 1
        o = d ^ par
        ins = {1: o, 2: d, 3: par}[mode]
        value = ((value << 1) | ins) & 0xFFFFFFFF
        out.append(o)
    return out


def to_bits(words):
    return [(w >> i) & 1 for w in words for i in range(16)]


def to_words(bits):
    return [sum(bits[16 * i + j] << j for j in range(16)) for i in range(len(bits) // 16)]


async def checksum_through_out(chip, words, poly, value, cfg, shiftctrl):
    prog = await chip.load("out null, 1")
    await chip.setup_engine(0, prog, shiftctrl=regs.AUTOPULL | shiftctrl)
    await chip.write32(regs.engine_reg(0, regs.LFSR_POLY), poly)
    await chip.write32(regs.engine_reg(0, regs.LFSR_VALUE), value)
    await chip.ereg(0, regs.LFSRCFG, cfg)
    await chip.enable(1)
    for i in range(0, len(words), 4):
        await chip.push(0, words[i:i + 4])
        await chip.cycles(70)
    await chip.enable(0)
    return await chip.read32(regs.engine_reg(0, regs.LFSR_VALUE))


@cocotb.test()
async def test_crc32_reflected(dut):
    """Ethernet CRC-32: right direction, reflected polynomial, LSB first."""
    chip = await new_chip(dut)
    data = bytes(range(0x30, 0x40))
    words = [data[i] | data[i + 1] << 8 for i in range(0, len(data), 2)]
    v = await checksum_through_out(chip, words, 0xEDB88320, 0xFFFFFFFF,
                                   regs.LFSR_FEED_OUT | regs.LFSR_CRC | regs.LFSR_RIGHT,
                                   regs.OUT_RIGHT)
    assert v ^ 0xFFFFFFFF == zlib.crc32(data), hex(v)


@cocotb.test()
async def test_crc16_left_aligned(dut):
    """CRC-16/CCITT-FALSE: left direction, polynomial left-aligned, MSB first."""
    chip = await new_chip(dut)
    data = b"123456789A"
    words = [data[i] << 8 | data[i + 1] for i in range(0, len(data), 2)]
    v = await checksum_through_out(chip, words, 0x1021 << 16, 0xFFFF << 16,
                                   regs.LFSR_FEED_OUT | regs.LFSR_CRC, 0)
    assert v >> 16 == binascii.crc_hqx(data, 0xFFFF), hex(v)


async def transform(chip, words, poly, value, mode):
    """Pass bits through `out x, 1` (stepping the LFSR) into the ISR."""
    prog = await chip.load(".wrap_target\nout x, 1\nin x, 1\n.wrap")
    await chip.setup_engine(0, prog, shiftctrl=regs.AUTOPULL | regs.AUTOPUSH | regs.OUT_RIGHT
                            | regs.IN_RIGHT)
    await chip.write32(regs.engine_reg(0, regs.LFSR_POLY), poly)
    await chip.write32(regs.engine_reg(0, regs.LFSR_VALUE), value)
    await chip.ereg(0, regs.LFSRCFG, regs.LFSR_FEED_OUT | (mode << 2))
    await chip.enable(1)
    out = []
    for w in words:
        await chip.push(0, [w])
        out += await chip.pop_wait(0, 1)
    await chip.enable(0)
    return out


@cocotb.test()
async def test_scrambler_round_trip(dut):
    chip = await new_chip(dut)
    poly, seed = 0x48, 0x5A  # s[n] = d[n] ^ s[n-4] ^ s[n-7]
    data = [0x0000, 0xFFFF, 0x1234, 0xA5A5, 0x0F0F]
    scrambled = await transform(chip, data, poly, seed, 1)
    assert to_bits(scrambled) == lfsr_model(to_bits(data), poly, seed, 1)
    recovered = await transform(chip, scrambled, poly, seed, 2)
    assert recovered == data


@cocotb.test()
async def test_prbs7(dut):
    """Additive mode with zero data generates PRBS7 (x^7 + x^6 + 1)."""
    chip = await new_chip(dut)
    got = to_bits(await transform(chip, [0] * 9, 0x60, 0x7F, 3))
    ref, a = [], 0x7F
    for _ in range(len(got)):
        nb = ((a >> 6) ^ (a >> 5)) & 1
        a = ((a << 1) | nb) & 0x7F
        ref.append(nb)
    assert got == ref
    assert got[:127] == got[127:254] or len(got) < 254


@cocotb.test()
async def test_g2_manchester_encoder(dut):
    """G2 fed by `out g2, 1`, stepping on engine ticks, drives a pin directly."""
    chip = await new_chip(dut)
    prog = await chip.load("out g2, 1")
    await chip.setup_engine(0, prog, clkdiv=1, shiftctrl=regs.AUTOPULL | regs.OUT_RIGHT)
    await chip.write(regs.G2_ADDR, 0)
    await chip.write(regs.G2_DATA, g2lib.manchester_encoder())
    await chip.write(regs.G2_IN0, [regs.G2_SRC_FEED_BIT, regs.G2_SRC_FEED_FULL])
    await chip.write(regs.G2_CTRL, regs.G2_ENABLE | regs.G2_STEP_TICK)
    await chip.pincfg(0, regs.OWNER_G2_OUT0 | regs.DRIVE_ALWAYS)
    words = [0xC3A5, 0x0F31]
    await chip.push(0, words)
    sampler = cocotb.start_soon(_sample(chip, 200, lambda: chip.uio() & 1))
    await chip.enable(1)
    s = await sampler
    expect = []
    for b in to_bits(words):
        expect += [1 - b] * 2 + [b] * 2
    hits = [i for i in range(len(s) - len(expect)) if s[i:i + len(expect)] == expect]
    assert hits, s


async def _sample(chip, n, fn):
    out = []
    for _ in range(n):
        await RisingEdge(chip.dut.clk)
        out.append(fn())
    return out


@cocotb.test()
async def test_g2_nrzi_decode_into_isr(dut):
    """H1: G2 samples a pin on engine ticks and emits decoded bits into the
    owner's ISR with autopush, while the checksum register (feed IN) sees
    the same bits. No instructions handle the data."""
    chip = await new_chip(dut)
    chip.set_pin8(1)
    prog = await chip.load("idle: jmp idle")
    await chip.setup_engine(0, prog, clkdiv=3, shiftctrl=regs.AUTOPUSH | regs.IN_RIGHT
                            | regs.fifo_mode(regs.FIFO_JOIN_RX))
    await chip.write32(regs.engine_reg(0, regs.LFSR_POLY), 0xA001)
    await chip.write32(regs.engine_reg(0, regs.LFSR_VALUE), 0xFFFF)
    await chip.ereg(0, regs.LFSRCFG, regs.LFSR_FEED_IN | regs.LFSR_CRC | regs.LFSR_RIGHT)
    await chip.write(regs.G2_ADDR, 0)
    await chip.write(regs.G2_DATA, g2lib.nrzi_decoder())
    await chip.write(regs.G2_IN0, [8, 0])
    await chip.write(regs.G2_STATE, 1)  # previous level: idle high
    await chip.write(regs.G2_CTRL, regs.G2_ENABLE | regs.G2_STEP_TICK | regs.G2_EMIT)
    await chip.enable(1)

    payload = [0x80, 0x2D, 0x00, 0x10, 0xFE, 0x55, 0xAA]
    bits = [(b >> i) & 1 for b in payload for i in range(8)]
    level, line = 1, []
    for b in bits:
        if b == 0:
            level ^= 1
        line.append(level)
    for v in line:
        chip.set_pin8(v)
        await chip.cycles(4)
    await chip.cycles(40)
    await chip.write(regs.G2_CTRL, 0)
    await chip.enable(0)

    _, rx = await chip.levels(0)
    words = await chip.pop(0, rx)
    cnt = (await chip.dbg(0, 5)) & 0x1F
    isr = await chip.dbg(0, 3)
    got = to_bits(words) + [(isr >> (16 - cnt + i)) & 1 for i in range(cnt)]
    # Idle (no transitions) decodes as ones before and after the payload.
    s = "".join(map(str, got))
    p = "".join(map(str, bits))
    assert p in s, (s, p)
    crc = await chip.read32(regs.engine_reg(0, regs.LFSR_VALUE))
    assert (crc & 0xFFFF) ^ 0xFFFF == crc16_usb(got)


@cocotb.test()
async def test_capture_and_overrun(dut):
    """G3: edge timestamps from the global counter, flag and overrun."""
    chip = await new_chip(dut)
    await chip.ereg(0, regs.CAPCFG, [8 | (1 << 4) | (1 << 6), 2])  # pin 8 rising, flag 2

    async def pulses():
        await RisingEdge(dut.clk)
        chip.set_pin8(1)
        await chip.cycles(50)
        chip.set_pin8(0)
        await chip.cycles(450)
        chip.set_pin8(1)  # exactly 500 cycles after the first rising edge

    driver = cocotb.start_soon(pulses())
    await chip.cycles(120)
    t0 = await chip.read32(regs.engine_reg(0, regs.CAPTURE))
    assert (await chip.read8(regs.FLAGS)) & 0x04
    await chip.write(regs.FLAGS, 0x04)
    await driver
    await chip.cycles(20)
    t1 = await chip.read32(regs.engine_reg(0, regs.CAPTURE))
    assert t1 - t0 == 500, (t0, t1)
    # The engine reads the capture through `in capl`.
    await chip.force_asm(0, "in capl, 16\nmov x, isr")
    assert await chip.dbg(0, 1) == t1 & 0xFFFF
    # Overrun: flag 2 is still set when the next edge arrives.
    assert not (await chip.read8(regs.STICKY)) & regs.STK_CAP_OVR0
    chip.set_pin8(0)
    await chip.cycles(10)
    chip.set_pin8(1)
    await chip.cycles(10)
    assert (await chip.read8(regs.STICKY)) & regs.STK_CAP_OVR0


@cocotb.test()
async def test_capture_pin_change_is_not_an_edge(dut):
    """G3: selecting a pin whose level differs from the previously watched
    pin records no capture and sets no flag; only a later edge does."""
    chip = await new_chip(dut)
    chip.set_pin8(1)
    await chip.cycles(10)
    # The unit watched pin 0 (low) since reset; pin 8 is high.
    await chip.ereg(0, regs.CAPCFG, [8 | (3 << 4) | (1 << 6), 2])  # pin 8, both edges, flag 2
    await chip.cycles(10)
    assert await chip.read32(regs.engine_reg(0, regs.CAPTURE)) == 0
    assert not (await chip.read8(regs.FLAGS)) & 0x04
    chip.set_pin8(0)
    await chip.cycles(10)
    assert await chip.read32(regs.engine_reg(0, regs.CAPTURE)) != 0
    assert (await chip.read8(regs.FLAGS)) & 0x04


async def edge_pair(chip, drive, t_ns=None):
    """Drive pin 8 and pin 9 high together; return (capture0, capture1)."""
    if t_ns is not None:
        await RisingEdge(chip.dut.clk)
        await Timer(t_ns, unit="ns")
    drive()
    await chip.cycles(30)
    c0 = await chip.read32(regs.engine_reg(0, regs.CAPTURE))
    c1 = await chip.read32(regs.engine_reg(1, regs.CAPTURE))
    return c0, c1


@cocotb.test()
async def test_filter_bypass_latency(dut):
    """G6: a 4-cycle filter adds 4 cycles; bypass removes the 2-cycle sync."""
    chip = await new_chip(dut)
    await chip.ereg(0, regs.CAPCFG, 8 | (1 << 4))
    await chip.ereg(1, regs.CAPCFG, 9 | (1 << 4))

    def both_high():
        chip.ui |= 0xC0
        chip._ui_apply()

    def both_low():
        chip.ui &= 0x3F
        chip._ui_apply()

    await chip.pincfg(8, regs.pin_filter(2))
    c0, c1 = await edge_pair(chip, both_high)
    assert c0 - c1 == 4, (c0, c1)
    both_low()
    await chip.cycles(20)
    # A 3-cycle glitch never passes the 4-cycle filter.
    before = c0
    await RisingEdge(dut.clk)
    chip.set_pin8(1)
    await chip.cycles(3)
    chip.set_pin8(0)
    await chip.cycles(30)
    assert await chip.read32(regs.engine_reg(0, regs.CAPTURE)) == before
    # Bypass on pin 9 (no filter on 8): 2 cycles earlier.
    await chip.pincfg(8, 0)
    await chip.pincfg(9, regs.PIN_BYPASS)
    c0, c1 = await edge_pair(chip, both_high)
    assert c0 - c1 == 2, (c0, c1)


@cocotb.test()
async def test_falling_edge_sample(dut):
    """G6: pin 15 samples NEGSEL's pin half a cycle after its rising sample."""
    chip = await new_chip(dut)
    await chip.write(regs.NEGSEL, 8)
    await chip.ereg(0, regs.CAPCFG, 8 | (1 << 4))
    await chip.ereg(1, regs.CAPCFG, regs.PIN_NEG | (1 << 4))
    # Change in the first half of a cycle: the falling-edge sample sees it first.
    c0, c1 = await edge_pair(chip, lambda: chip.set_pin8(1), t_ns=3)
    assert c0 - c1 == 1, (c0, c1)
    chip.set_pin8(0)
    await chip.cycles(20)
    # Change in the second half: both see it in the same cycle.
    await RisingEdge(dut.clk)
    await FallingEdge(dut.clk)
    await Timer(3, unit="ns")
    chip.set_pin8(1)
    await chip.cycles(30)
    c0 = await chip.read32(regs.engine_reg(0, regs.CAPTURE))
    c1 = await chip.read32(regs.engine_reg(1, regs.CAPTURE))
    assert c0 == c1, (c0, c1)
