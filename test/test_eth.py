# SPDX-License-Identifier: Apache-2.0
"""10BASE-T transmit and receive programs against independent Manchester
peers, at 40 MHz."""

import random
import zlib

import cocotb
from cocotb.triggers import RisingEdge, Timer

from chip import Chip, assemble, program_source, regs
from pe import g2 as g2lib
from pe.proto import eth

TDP, TDM, RX = 2, 3, 8
CLK_PS = 25000  # 40 MHz
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


@cocotb.test()
async def test_eth_transmit(dut):
    chip = await new_chip(dut)
    prog = await chip.load_program("eth10_tx")
    await chip.setup_engine(0, prog, shiftctrl=regs.AUTOPULL | regs.OUT_RIGHT
                            | regs.fifo_mode(regs.FIFO_JOIN_TX), side_base=TDP,
                            execcfg=prog.label("table"))
    await chip.ereg(0, regs.SETPIN, TDP | (2 << 4))
    await chip.force_asm(0, "set pindirs, 3 side 0", side_count=2)
    await chip.pincfg(TDP, regs.DRIVE_PUSH_PULL)
    await chip.pincfg(TDM, regs.DRIVE_PUSH_PULL)
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
    level = 0
    chip.set_pin8(0)
    for i, h in enumerate(halves):
        if h != level:
            d = rng.uniform(-jitter_ns, jitter_ns) if jitter_ns else 0.0
            await Timer(round((t + d) * 1000) - round(getattr(chip, "_rx_t", 0) * 1000), unit="ps")
            chip._rx_t = t + d
            chip.set_pin8(h)
            level = h
        t += 50.0
    await Timer(round((t + 250 - chip._rx_t) * 1000), unit="ps")  # TP_IDL high
    chip.set_pin8(1)
    await Timer(300, unit="ns")
    chip.set_pin8(0)


async def rx_setup(chip):
    prog = await chip.load_program("eth10_rx")
    await chip.setup_engine(1, prog, shiftctrl=regs.AUTOPUSH | regs.fifo_mode(regs.FIFO_JOIN_RX))
    await chip.write(regs.NEGSEL, RX)
    await chip.write(regs.G2_ADDR, 0)
    await chip.write(regs.G2_DATA, g2lib.manchester_receiver())
    await chip.write(regs.G2_IN0, [RX, regs.PIN_NEG])
    await chip.write(regs.G2_STATE, 8)  # idle, line low
    await chip.write(regs.G2_CTRL, regs.G2_ENABLE | regs.G2_OWNER1 | regs.G2_STEP_CYCLE | regs.G2_EMIT)
    await chip.enable(2)


async def collect_frame(chip, timeout=200000):
    words, waited = [], 0
    while True:
        done = (await chip.read8(regs.FLAGS)) & 0x08
        _, rx = await chip.levels(1)
        if rx:
            words += await chip.pop(1, rx)
            continue
        if done:
            await chip.write(regs.FLAGS, 0x08)
            return words
        await chip.cycles(20)
        waited += 20
        if waited > timeout:
            raise TimeoutError(f"{len(words)} words")


async def receive(dut, payload, jitter_ns, start_ns, seed=1):
    chip = await new_chip(dut)
    chip.set_pin8(0)
    await rx_setup(chip)
    wire = wire_frame(payload)
    cocotb.start_soon(drive_rx(chip, wire, jitter_ns, start_ns, seed))
    words = await collect_frame(chip)
    res = eth.parse(eth.record_bits(words))
    assert res is not None, [hex(w) for w in words]
    body, ok = res
    assert body == wire[8:-4], (body.hex(), wire[8:-4].hex())
    assert ok, "FCS"
    assert not (await chip.read8(regs.STICKY)) & regs.STK_RX_OVF1, "receive overflow"


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
