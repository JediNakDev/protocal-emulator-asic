# SPDX-License-Identifier: Apache-2.0
"""Held-out protocols: WS2812 transmit, 1-Wire controller and I2S transmit,
written after the RTL was finished and run with no RTL change, to measure
how far the chip reaches beyond the protocols it was designed around. The
peers here check the wire in absolute time against the published limits
cited in docs/heldout-protocols.md and share no code with the programs.
Each program also runs where it must fail, as a negative control."""

import random

import cocotb

from chip import MHZ_50, Chip, fails_at_wrong_clock, program_source, regs
from peers import Wave, clock_violations, now_ns, setup_hold_violations, sleep_until

PROFILE = MHZ_50
UO_PIN = {10: 5, 11: 6, 12: 7}  # protocol pin to uo_out bit

async def new_chip(dut, hz=PROFILE):
    chip = Chip(dut, hz)
    await chip.start()
    await chip.set_quad()
    return chip


async def must_fail(session):
    """Negative control: returns the message of the check that caught it."""
    try:
        await session()
    except AssertionError as e:
        return str(e)
    raise AssertionError("the negative control passed")


def median(values):
    return sorted(values)[len(values) // 2]


# ----------------------------------------------------------------- WS2812
# Worldsemi WS2812B datasheet V1.0 (Jan 2016): nominal +/- 150 ns.
WS_T0H, WS_T1H = (250, 550), (700, 1000)
WS_T0L, WS_T1L = (700, 1000), (250, 550)
WS_TBIT = (650, 1850)   # TH + TL = 1.25 us +/- 600 ns
WS_RES = 50_000
WS_RATE = 800_000
WS_DIN = 10


def ws2812_decode(line, t_end):
    """Decode a Wave of DIN into frames of bits, each closed by a reset gap.
    Every high and low time and every bit period must meet the datasheet.
    Returns (frames, gaps, bit periods, violations)."""
    frames, gaps, periods, out = [], [], [], []
    bits = []
    rises = line.edges(level=1)
    for tr, next_rise in zip(rises, rises[1:] + [t_end]):
        tf = line.next_edge(tr)
        if tf is None:
            out.append(f"timing: DIN stuck high from {tr:.0f} ns")
            break
        high, low = tf - tr, next_rise - tf
        if WS_T0H[0] <= high <= WS_T0H[1]:
            bit, limits = 0, WS_T0L
        elif WS_T1H[0] <= high <= WS_T1H[1]:
            bit, limits = 1, WS_T1L
        else:
            out.append(f"timing: DIN high {high:.0f} ns at {tr:.0f} ns is neither code")
            bit, limits = 0, WS_T0L
        bits.append(bit)
        if low >= WS_RES:
            frames.append(bits)
            gaps.append(low)
            bits = []
        elif not limits[0] <= low <= limits[1]:
            out.append(f"timing: DIN low {low:.0f} ns after a {bit} code at {tf:.0f} ns")
        else:
            periods.append(high + low)
            if not WS_TBIT[0] <= high + low <= WS_TBIT[1]:
                out.append(f"timing: bit period {high + low:.0f} ns at {tr:.0f} ns")
    if bits:
        out.append(f"timing: {len(bits)} bits without a reset gap")
    return frames, gaps, periods, out


def ws2812_pixels(bits, count):
    """A chain of `count` pixels: each keeps the first 24 bits it receives,
    G then R then B, most significant bit first, and forwards the rest."""
    assert len(bits) == 24 * count, f"frame of {len(bits)} bits for {count} pixels"
    pixels = []
    for i in range(0, len(bits), 24):
        v = int("".join(map(str, bits[i:i + 24])), 2)
        g, r, b = v >> 16, (v >> 8) & 0xFF, v & 0xFF
        pixels.append((r, g, b))
    return pixels


def ws2812_words(pixels):
    """Host side: frame length, then the bits; the padding in the last word
    is all ones, so a program that sent it would lengthen the frame."""
    bits = [(byte >> i) & 1 for r, g, b in pixels for byte in (g, r, b) for i in range(7, -1, -1)]
    words = [len(bits) - 1]
    for i in range(0, len(bits), 16):
        chunk = bits[i:i + 16]
        chunk += [1] * (16 - len(chunk))
        words.append(int("".join(map(str, chunk)), 2))
    return words


async def ws2812_session(dut, frames, hz=PROFILE):
    chip = await new_chip(dut, hz)
    din = Wave(dut.uo_out, UO_PIN[WS_DIN])
    prog = await chip.load_program("ws2812")
    await chip.setup_engine(0, prog, clkdiv=2, shiftctrl=regs.AUTOPULL | regs.fifo_mode(regs.FIFO_JOIN_TX),
                            side_base=WS_DIN)
    await chip.pincfg(WS_DIN, regs.DRIVE_ALWAYS)
    await chip.enable(1)
    for pixels in frames:  # the program alone keeps the frames apart
        await chip.stream(0, ws2812_words(pixels), 0, depth=8)
    while (await chip.levels(0))[0]:
        await chip.cycles(100)
    await sleep_until(now_ns() + 30_000 + 500_000)  # the last word, then the reset gap
    got, gaps, periods, violations = ws2812_decode(din, now_ns())
    assert not violations, violations[:5]
    assert len(got) == len(frames), f"{len(got)} frames of {[len(f) for f in got]} bits"
    for bits, pixels in zip(got, frames):
        assert ws2812_pixels(bits, len(pixels)) == pixels
    rate = 1e9 / median(periods)
    assert abs(rate - WS_RATE) <= 0.01 * WS_RATE, f"timing: bit rate {rate:.0f} Hz"
    highs = sorted({round(din.next_edge(t) - t) for t in din.edges(level=1)})
    lows = sorted({round(din.next_edge(t) - t) for t in din.edges(level=0)[:-1]} - {round(g) for g in gaps})
    dut._log.info(f"WS2812: high {highs} ns, low {lows[:2]} ns, bit {min(periods):.0f}-{max(periods):.0f} ns, "
                  f"{rate:.0f} Hz, reset gap at least {min(gaps) / 1000:.1f} us, {len(got)} frames")
    din.stop()


WS_FRAMES = [
    [(0xFF, 0x00, 0x00), (0x00, 0xFF, 0x00), (0x00, 0x00, 0xFF), (0xA5, 0x5A, 0x3C)],
    [(random.Random(i).getrandbits(8), i * 17, 255 - i) for i in range(12)],
    [(0xFF, 0xFF, 0xFF), (0x00, 0x00, 0x00), (0x80, 0x01, 0x7E)],
]


@cocotb.test()
async def test_ws2812_pixels(dut):
    """Three frames back to back, one of them longer than the queue: every
    high and low time inside the datasheet, each frame closed by a reset
    gap, the pixels as a chain would latch them."""
    await ws2812_session(dut, WS_FRAMES)


@cocotb.test()
async def test_ws2812_wrong_clock(dut):
    """At 40 MHz the 0 code is low and the 1 code high for 1050 ns, over the
    1000 ns limit."""
    msg = await fails_at_wrong_clock(lambda hz: ws2812_session(dut, WS_FRAMES[:1], hz), PROFILE)
    assert "timing: DIN low 1050 ns" in msg, msg


# -------------------------------------------------------------------- I2S
# NXP UM11732 Rev. 3.0 (Feb 2022), in terms of the clock period T: HIGH and
# LOW at least 0.35T, transmitter delay at most 0.8T and hold at least 0,
# receiver setup at least 0.2T and hold at least 0.
I2S_BCLK, I2S_LRCLK, I2S_SD = 10, 11, 12
I2S_NOMINAL = 48_000
I2S_CYCLES = 33      # clk cycles per BCLK at the profile
I2S_ERROR = 0.014    # 33 cycles reach -1.36%, the nearest to 48 kHz
I2S_SLOT = 16


def i2s_receive(bclk, lrclk, sd, bit_ns):
    """Receiver latching SD and LRCLK on each rising BCLK. A word ends with
    the bit latched where LRCLK first shows the other channel, and the next
    bit is its MSB. Returns (left, right, pauses, violations)."""
    rises = bclk.edges(level=1)
    falls = bclk.edges(level=0)
    t = median([b - a for a, b in zip(rises, rises[1:])])
    out, _ = clock_violations(bclk, True, 1e9 / bit_ns)
    pauses = sum(1 for a, b in zip(rises, rises[1:]) if b - a >= 1.5 * bit_ns)
    high = min(b - a for a, b in zip(rises, [bclk.next_edge(r) for r in rises]) if b is not None)
    low = min(bclk.next_edge(f) - f for f in falls if bclk.next_edge(f) is not None)
    if min(high, low) < 0.35 * t:
        out.append(f"timing: BCLK high {high:.0f} ns, low {low:.0f} ns, T {t:.0f} ns")
    out += setup_hold_violations(sd, rises, 0.2 * t, 0, "SD")
    out += setup_hold_violations(lrclk, rises, 0.2 * t, 0, "LRCLK")
    for line, name in ((sd, "SD"), (lrclk, "LRCLK")):
        for e in line.edges(rises[0]):
            fell = bclk.last_edge(e)
            rise = bclk.next_edge(e)
            paused = rise is None or rise - fell >= 1.5 * bit_ns  # counted by the caller
            if bclk.at(e) != 0 or (e - fell > 0.8 * t and not paused):
                out.append(f"timing: {name} changes {e - fell:.0f} ns after a BCLK edge at {e:.0f} ns")
    words, bits = [], []
    channel = lrclk.at(rises[0])
    synced = False
    for r in rises:
        ws = lrclk.at(r)
        bits.append(sd.at(r))
        if ws != channel:
            if synced:
                words.append((channel, bits))
            synced, channel, bits = True, ws, []
    if bits:
        out.append(f"{len(bits)} bits left after the last word")
    for i, (ch, word) in enumerate(words):
        if len(word) != I2S_SLOT:
            out.append(f"word {i} has {len(word)} bits")
        if ch != i & 1:
            out.append(f"word {i} on channel {ch}")
    samples = [int("".join(map(str, word)), 2) for _, word in words]
    return samples[::2], samples[1::2], pauses, out


async def i2s_session(dut, samples, hz=PROFILE, source=None, pause_after=None):
    """Streams (left, right) pairs. With pause_after, the Host stops after
    that many words until the queue has run dry and BCLK has stalled."""
    chip = await new_chip(dut, hz)
    bclk = Wave(dut.uo_out, UO_PIN[I2S_BCLK])
    lrclk = Wave(dut.uo_out, UO_PIN[I2S_LRCLK])
    sd = Wave(dut.uo_out, UO_PIN[I2S_SD])
    prog = await (chip.load(source) if source else chip.load_program("i2s"))
    await chip.setup_engine(0, prog, clkdiv=2, shiftctrl=regs.AUTOPULL | regs.fifo_mode(regs.FIFO_JOIN_TX),
                            out_base=I2S_SD, side_base=I2S_BCLK)
    await chip.force_asm(0, "nop side 2", side_count=2)
    for pin in (I2S_BCLK, I2S_LRCLK, I2S_SD):
        await chip.pincfg(pin, regs.DRIVE_ALWAYS)
    await chip.cycles(2)
    await chip.enable(1)
    words = [w for pair in samples for w in pair]
    if pause_after is None:
        await chip.stream(0, words, 0, depth=8)
    else:
        await chip.stream(0, words[:pause_after], 0, depth=8)
        await sleep_until(now_ns() + 200_000)
        await chip.stream(0, words[pause_after:], 0, depth=8)
    await sleep_until(now_ns() + 150_000)
    left, right, pauses, violations = i2s_receive(bclk, lrclk, sd, I2S_CYCLES * 1e9 / PROFILE)
    assert not violations, violations[:5]
    assert pauses == (0 if pause_after is None else 1), f"BCLK paused {pauses} times"
    assert list(zip(left, right)) == samples, f"samples {list(zip(left, right))[:4]}"
    frames = lrclk.edges(level=0)
    fs = 1e9 / median([b - a for a, b in zip(frames[1:], frames[2:])])
    error = (fs - I2S_NOMINAL) / I2S_NOMINAL
    assert abs(error) <= I2S_ERROR, f"timing: frame rate {fs:.1f} Hz, {error:+.2%} from 48 kHz"
    high = sorted({round(bclk.next_edge(t) - t) for t in bclk.edges(level=1)})
    setup = min(t - sd.last_edge(t) for t in bclk.edges(level=1) if sd.last_edge(t) is not None)
    dut._log.info(f"I2S: {fs:.1f} Hz, {error:+.2%} from {I2S_NOMINAL} Hz, BCLK high {high} ns, "
                  f"SD setup at least {setup:.0f} ns, {len(left)} frames")
    for wave in (bclk, lrclk, sd):
        wave.stop()


_rng = random.Random(0x12C5)
I2S_SAMPLES = [(0x8000, 0x7FFF), (0xFFFF, 0x0000), (0xAAAA, 0x5555), (0x0001, 0x8001)]
I2S_SAMPLES += [(_rng.getrandbits(16), _rng.getrandbits(16)) for _ in range(12)]


@cocotb.test()
async def test_i2s_transmit(dut):
    """16 stereo frames, twice the queue: every sample on the right channel,
    LRCLK leading the MSB by one clock, and the UM11732 limits on the clock,
    data and word select, with no pause in BCLK."""
    await i2s_session(dut, I2S_SAMPLES)


@cocotb.test()
async def test_i2s_underrun(dut):
    """A Host that stops, once between frames and once between channels,
    pauses BCLK at a bit boundary; no sample is lost or misaligned."""
    await i2s_session(dut, I2S_SAMPLES[:8], pause_after=8)
    await i2s_session(dut, I2S_SAMPLES[:8], pause_after=5)


@cocotb.test()
async def test_i2s_wrong_clock(dut):
    """At 40 MHz the frame rate is 37,879 Hz and BCLK 25% slow."""
    msg = await fails_at_wrong_clock(lambda hz: i2s_session(dut, I2S_SAMPLES[:4], hz), PROFILE)
    assert "timing: clock period" in msg, msg


@cocotb.test()
async def test_i2s_left_justified_rejected(dut):
    """LRCLK moved one clock late, with the MSB, is left-justified format,
    not I2S: the receiver's word boundaries must catch it."""
    source = program_source("i2s")
    for old, new in (("side 2 [4]  ; left LSB, LRCLK rises", "side 0 [4]"),
                     ("side 3 [5]\nright", "side 1 [5]\nright"),
                     ("side 0 [4]  ; right LSB, LRCLK falls", "side 2 [4]")):
        assert old in source
        source = source.replace(old, new)
    head, tail = source.rsplit("side 1 [5]", 1)
    source = head + "side 3 [5]" + tail
    msg = await must_fail(lambda: i2s_session(dut, I2S_SAMPLES[:4], source=source))
    assert "word 0 has 17 bits" in msg, msg


# ----------------------------------------------------------------- 1-Wire
# Limits from the Maxim DS18S20/DS18B20 datasheets' AC table, in ns; the
# program's slot shape follows Maxim Application Note 126.
OW_RSTL, OW_RSTH = 480_000, 480_000
OW_LOW0 = (60_000, 120_000)
OW_LOW1 = (1_000, 15_000)
OW_REC, OW_SLOT = 1_000, 60_000
OW_SAMPLE = (15_000, 60_000)  # where a device samples a write slot
OW_READ_ROM = 0x33
OW_DQ = 3


def crc8(data):
    """Maxim CRC8, x^8 + x^5 + x^4 + 1, least significant bit first."""
    crc = 0
    for byte in data:
        for _ in range(8):
            mix = (crc ^ byte) & 1
            crc >>= 1
            if mix:
                crc ^= 0x8C
            byte >>= 1
    return crc


class OneWireDevice:
    """A device with a ROM code, on the wire only: reset and presence, the
    Read ROM command, and read slots. Its timing is set to the edges of the
    datasheet: `pd_high` and `pd_low` shape the presence pulse, and `rdv` is
    how long it holds a 0 in a read slot, measured from the falling edge."""

    def __init__(self, chip, line, rom, present=True, pd_high=15_000, pd_low=60_000, rdv=15_000):
        self.chip, self.line, self.rom = chip, line, rom
        self.present, self.pd_high, self.pd_low, self.rdv = present, pd_high, pd_low, rdv
        self.resets = 0
        self.commands = []
        self.violations = []
        self.bits = []
        self.tx = []

    async def _level(self, v):
        while self.line.read() != v:
            await self.line.change()

    def _sample(self, t0):
        """A write slot: the level must hold across the whole window, since
        a device may sample anywhere in it."""
        a, b = t0 + OW_SAMPLE[0], t0 + OW_SAMPLE[1]
        for e in self.line.edges(a, b):
            self.violations.append(f"timing: DQ changes {e - t0:.0f} ns into a write slot")
        self.bits.append(self.line.at(a))
        if len(self.bits) == 8:
            command = sum(b << i for i, b in enumerate(self.bits))
            self.commands.append(command)
            self.bits = []
            if command == OW_READ_ROM:
                self.tx = [(byte >> i) & 1 for byte in self.rom for i in range(8)]

    async def run(self):
        while True:
            await self._level(0)
            t0 = now_ns()
            sending = bool(self.tx)
            bit = self.tx.pop(0) if sending else 1
            if not bit:
                self.chip.drive_uio(OW_DQ, 0)
                await sleep_until(t0 + self.rdv)
                self.chip.drive_uio(OW_DQ, None)
            await self._level(1)
            t1 = now_ns()
            if t1 - t0 >= OW_RSTL:
                self.resets += 1
                self.bits, self.tx = [], []
                if self.present:
                    await sleep_until(t1 + self.pd_high)
                    self.chip.drive_uio(OW_DQ, 0)
                    await sleep_until(t1 + self.pd_high + self.pd_low)
                    self.chip.drive_uio(OW_DQ, None)
                    await self._level(1)
            elif not sending:
                await sleep_until(t0 + OW_SAMPLE[1])
                self._sample(t0)


def onewire_master_violations(oe):
    """Timing of the chip's own drive on DQ, from its output enable, so that
    a device holding the line does not hide it. Returns (violations, the low
    times seen by kind)."""
    out = []
    lows = {"reset": [], "0": [], "1": []}
    before = None
    for start in oe.edges(level=1):
        end = oe.next_edge(start)
        low = end - start
        if low >= OW_RSTL:
            kind = "reset"
        elif OW_LOW0[0] <= low <= OW_LOW0[1]:
            kind = "0"
        elif OW_LOW1[0] <= low <= OW_LOW1[1]:
            kind = "1"
        else:
            out.append(f"timing: DQ driven low {low:.0f} ns at {start:.0f} ns")
            kind = "1"
        lows[kind].append(low)
        if before:
            t0, t1, last = before
            if start - t1 < (OW_RSTH if last == "reset" else OW_REC):
                out.append(f"timing: DQ released {start - t1:.0f} ns before {start:.0f} ns")
            if last != "reset" and start - t0 < OW_SLOT + OW_REC:
                out.append(f"timing: slots {start - t0:.0f} ns apart at {start:.0f} ns")
        before = (start, end, kind)
    return out, lows


OW_RESET = 1


def ow_slots(n, levels):
    return (n - 1) << 1 | levels << 4


def ow_decode(word, n):
    return word >> (16 - n)


async def ow_op(chip, word):
    await chip.push(0, [word])
    return (await chip.pop_wait(0, 1))[0]


async def onewire_setup(dut, hz):
    chip = await new_chip(dut, hz)
    chip.pull_up(1 << OW_DQ)
    prog = await chip.load_program("onewire")
    await chip.setup_engine(0, prog, clkdiv=149, shiftctrl=regs.IN_RIGHT | regs.OUT_RIGHT,
                            out_base=OW_DQ, set_base=OW_DQ, in_base=OW_DQ)
    await chip.force_asm(0, "set pins, 1")
    await chip.pincfg(OW_DQ, regs.DRIVE_OPEN_DRAIN)
    await chip.write32(regs.engine_reg(0, regs.LFSR_POLY), 0x8C)
    await chip.write32(regs.engine_reg(0, regs.LFSR_VALUE), 0)
    await chip.ereg(0, regs.LFSRCFG, regs.LFSR_FEED_IN | regs.LFSR_CRC | regs.LFSR_RIGHT)
    return chip


async def onewire_session(dut, rom, hz=PROFILE, **timing):
    """Reset, then Read ROM with the ROM code's first byte read in slots of
    3 and 5, and the CRC computed on chip."""
    chip = await onewire_setup(dut, hz)
    oe = Wave(dut.uio_oe, OW_DQ)
    out = Wave(dut.uio_out, OW_DQ)
    contention = Wave(dut.contention, OW_DQ)
    device = OneWireDevice(chip, Wave(dut.uio_in, OW_DQ), rom, **timing)
    task = cocotb.start_soon(device.run())
    await chip.enable(1)
    reset = await ow_op(chip, OW_RESET)
    echo = ow_decode(await ow_op(chip, ow_slots(8, OW_READ_ROM)), 8)
    await chip.write32(regs.engine_reg(0, regs.LFSR_VALUE), 0)  # the command is not in the CRC
    low = ow_decode(await ow_op(chip, ow_slots(3, 7)), 3)
    high = ow_decode(await ow_op(chip, ow_slots(5, 31)), 5)
    got = [high << 3 | low]
    for _ in rom[1:]:
        got.append(ow_decode(await ow_op(chip, ow_slots(8, 0xFF)), 8))
    crc = await chip.read32(regs.engine_reg(0, regs.LFSR_VALUE))
    await chip.cycles(200)
    task.cancel()

    violations, lows = onewire_master_violations(oe)
    violations += device.violations
    assert not violations, violations[:5]
    assert not contention.times, "DQ contention"
    driven_high = [t for t in oe.edges(level=1) if out.at(t)] + [t for t in out.edges(level=1) if oe.at(t)]
    assert not driven_high, f"DQ driven high at {driven_high[:3]}: not open-drain"
    assert device.resets == 1, f"{device.resets} resets"
    assert reset >> 14 == 0b10, f"no presence: reset word {reset:#06x}"
    assert echo == OW_READ_ROM and device.commands == [OW_READ_ROM], f"command {echo:#x}, {device.commands}"
    assert bytes(got) == rom, f"ROM {bytes(got).hex()}"
    assert crc == 0, f"CRC8 residue {crc:#x}"
    starts = oe.edges(level=1)
    after_reset = starts[1] - oe.next_edge(starts[0])
    slots = [b - a for a, b in zip(starts[1:], starts[2:])]
    dut._log.info(f"1-Wire: reset low {lows['reset'][0] / 1000:.2f} us, then released "
                  f"{after_reset / 1000:.2f} us; write 0 low {min(lows['0']) / 1000:.2f} us, write 1 or read "
                  f"low {min(lows['1']) / 1000:.2f} us, slots {min(slots) / 1000:.2f} us apart at least")
    for wave in (oe, out, contention, device.line):
        wave.stop()


def rom_code(family, serial):
    body = bytes([family]) + serial
    return body + bytes([crc8(body)])


OW_ROM = rom_code(0x01, bytes.fromhex("A5C30F7E9012"))


@cocotb.test()
async def test_onewire_crc8_vector(dut):
    """The peer's CRC8 against the worked ROM code of Maxim Application Note
    27, so that it does not just agree with the chip."""
    assert crc8(bytes.fromhex("021CB801000000")) == 0xA2


@cocotb.test()
async def test_onewire_read_rom(dut):
    """Read ROM at both edges of the datasheet: the shortest and earliest
    presence pulse with a device that lets go of a 0 at 15 us, then the
    latest and longest with one that holds it 60 us."""
    await onewire_session(dut, OW_ROM, pd_high=15_000, pd_low=60_000, rdv=15_000)
    await onewire_session(dut, OW_ROM, pd_high=60_000, pd_low=240_000, rdv=60_000)


@cocotb.test()
async def test_onewire_sample_point(dut):
    """Finds the read sample at the pin: a line held low until 11.9 us after
    the slot starts reads 1 and until 12.1 us reads 0, inside the 15 us for
    which a device's data is valid."""
    chip = await onewire_setup(dut, PROFILE)
    line = Wave(dut.uio_in, OW_DQ)
    await chip.enable(1)
    for hold, level in ((11_900, 1), (12_100, 0)):
        async def device():
            while line.read():
                await line.change()
            t0 = now_ns()
            chip.drive_uio(OW_DQ, 0)
            await sleep_until(t0 + hold)
            chip.drive_uio(OW_DQ, None)
        cocotb.start_soon(device())
        assert ow_decode(await ow_op(chip, ow_slots(1, 1)), 1) == level, f"held {hold} ns"
    line.stop()


@cocotb.test()
async def test_onewire_bad_crc(dut):
    """One flipped ROM bit leaves a nonzero residue in the chip's CRC."""
    bad = OW_ROM[:3] + bytes([OW_ROM[3] ^ 0x10]) + OW_ROM[4:]
    msg = await must_fail(lambda: onewire_session(dut, bad))
    assert msg.startswith("CRC8"), msg


@cocotb.test()
async def test_onewire_no_device_and_short(dut):
    """An empty bus reads no presence; a bus stuck low reads low at both
    samples, which tells a short from a device."""
    chip = await onewire_setup(dut, PROFILE)
    await chip.enable(1)
    assert (await ow_op(chip, OW_RESET)) >> 14 == 0b11
    chip.drive_uio(OW_DQ, 0)
    assert (await ow_op(chip, OW_RESET)) >> 14 == 0b00
    chip.drive_uio(OW_DQ, None)
    await chip.cycles(10)
    assert (await ow_op(chip, OW_RESET)) >> 14 == 0b11


@cocotb.test()
async def test_onewire_wrong_clock(dut):
    """At 40 MHz every tick is 25% long: the presence sample lands after a
    60 us pulse has ended. At 62.5 MHz a write 0 is only 50 us low."""
    msg = await fails_at_wrong_clock(lambda hz: onewire_session(dut, OW_ROM, hz), PROFILE)
    assert msg.startswith("no presence"), msg
    msg = await fails_at_wrong_clock(lambda hz: onewire_session(dut, OW_ROM, hz), PROFILE, 62_500_000)
    assert "timing:" in msg, msg
