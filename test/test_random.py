# SPDX-License-Identifier: Apache-2.0
"""Random programs and configurations, run on the chip and on the reference
model (tools/pe/model.py), compared at every pad on every cycle and in all
state the Host can read afterwards.

Each seed resets the chip and loads random instruction memory, engine and pin
configuration, bit state machine table and initial register values, always
with the same sequence of Host transactions, so every seed starts the same
number of cycles after reset. Random board inputs drive the pins nobody else
drives while the engines run.

Environment variables:
  PE_RANDOM_SEEDS  number of seeds (default 60, 4 at gate level)
  PE_RANDOM_SEED   first seed (default 1); a failure reports the seed to rerun
"""

import os
import random

import cocotb
from cocotb.triggers import ReadOnly, RisingEdge

from chip import Chip
from pe import regs
from pe.model import Chip as Model

GATES = os.environ.get("GATES") == "yes"
SEEDS = int(os.environ.get("PE_RANDOM_SEEDS", 4 if GATES else 60))
FIRST_SEED = int(os.environ.get("PE_RANDOM_SEED", 1))

RUN = 400        # cycles the engines run
TAIL = 80        # cycles after disabling, with the board inputs held
G2_ON, EN_ON = 10, 60  # sampler cycles at which those Host writes start
EN_OFF = EN_ON + RUN
G2_OFF = EN_OFF + 50
CYCLES = G2_OFF + 50 + TAIL


# ------------------------------------------------------------- random setup
def random_instr(rng, toggling):
    """A random instruction, biased so that programs keep running: most
    blocking forms are rare, since nobody feeds or drains the queues, and
    most waits are on pins that the board toggles."""
    sd = 0 if rng.random() < 0.5 else rng.getrandbits(5)
    op = rng.choices(range(8), [9, 9, 2, 12, 14, 8, 18, 22])[0]
    lo = rng.getrandbits(8)
    if op == 2:
        src = rng.choices(range(4), [6, 2, 1, 1])[0]
        lo = (lo & 0x9F) | (src << 5)
        if src == 0 and toggling:
            lo = (lo & 0xE0) | rng.choice(toggling)
    elif op == 4 and lo >> 4 == 9 and rng.random() < 0.8:  # `out g2` blocks once the feed fills
        lo &= 0x0F
    elif op == 5:  # push, pull, irq: mostly non-blocking
        sub = rng.choices(range(4), [3, 3, 3, 1])[0]
        blk = rng.random() < (0.1 if sub == 2 else 0.2)
        lo = (sub << 6) | (lo & 0x2F) | (blk << 4)
    return (op << 13) | (sd << 8) | lo


def random_setup(rng):
    s = {}
    s["g2_table"] = [rng.getrandbits(8) for _ in range(64)]
    s["g2_in"] = [rng.randrange(18) if rng.random() < 0.9 else rng.getrandbits(5) for _ in range(2)]
    s["g2_state"] = rng.getrandbits(6)
    s["g2_ctrl"] = rng.getrandbits(5) if rng.random() < 0.7 else 0
    s["negsel"] = rng.getrandbits(4)
    s["pincfg"] = []
    for i in range(13):
        owner = rng.choices(range(4), [4, 4, 1, 1])[0]
        drive = rng.choices(range(4), [4, 2, 2, 2])[0]
        flt = rng.getrandbits(2) if rng.random() < 0.3 else 0  # mostly unfiltered
        s["pincfg"].append(owner | (drive << 2) | (rng.getrandbits(2) << 4) | (flt << 6))
    toggling = [i for i in range(8) if (s["pincfg"][i] >> 2) & 3 in (0, 2)] + [8, 9]
    s["imem"] = [random_instr(rng, toggling) for _ in range(64)]
    s["pull_up"] = rng.getrandbits(8)
    s["flags"] = rng.getrandbits(8) if rng.random() < 0.3 else 0
    s["engines"] = []
    for i in range(2):
        # Point the pin ranges mostly at pins this engine drives.
        owned = [p for p in range(13) if s["pincfg"][p] & 3 == i and (s["pincfg"][p] >> 2) & 3] or list(range(16))

        def base():
            return rng.choice(owned) if rng.random() < 0.8 else rng.getrandbits(4)

        outpin = base() | (rng.getrandbits(4) << 4)
        setpin = base() | (rng.getrandbits(3) << 4)
        sidepin = base() | (rng.choices(range(4), [1, 3, 2, 2])[0] << 4) | (rng.getrandbits(2) << 6)
        e = {
            "x": rng.getrandbits(16), "y": rng.getrandbits(16), "start": rng.randrange(64),
            "regs": [rng.choice([0, 0, 0, 1, 2, 3]), 0, rng.randrange(64), rng.randrange(64)]
                    + [shiftctrl(rng), rng.getrandbits(8), outpin, setpin,
                       rng.getrandbits(8), sidepin, rng.getrandbits(7), rng.getrandbits(5),
                       rng.getrandbits(5), rng.getrandbits(7), rng.getrandbits(3)],
            "poly": rng.getrandbits(32), "lfsr": rng.getrandbits(32),
            "entries": [rng.getrandbits(16) for _ in range(8)],
            "tx": [rng.getrandbits(16) for _ in range(8)],
        }
        s["engines"].append(e)
    return s


def shiftctrl(rng):
    """Autopush and autopull mostly off, as they block when nobody drains or
    feeds the queues."""
    v = rng.getrandbits(7) & ~(regs.AUTOPUSH | regs.AUTOPULL)
    return v | (regs.AUTOPUSH if rng.random() < 0.15 else 0) | (regs.AUTOPULL if rng.random() < 0.15 else 0)


def calibration_setup():
    """Engine 0: `set pins, 1` on pin 10, then `in count, 16` and `push`."""
    s = random_setup(random.Random(0))
    s["imem"] = [0xE001, 0x6050, 0xA010, 0x0003] + [0x0003] * 60
    s["g2_ctrl"] = 0
    s["pincfg"] = [0] * 10 + [0x0C, 0, 0]  # pin 10: engine 0, always drive
    s["flags"] = 0
    for e in s["engines"]:
        e["start"] = 0
        e["regs"] = [0, 0, 0, 63, 0, 0, 0x10, 0x1A, 0, 0, 0, 0, 0, 0, 0]
    s["engines"][1]["start"] = 3
    return s


def stimulus(rng, s):
    """Per-cycle board inputs: (ext_oe, ext_out, ui pins 8-9)."""
    drive = [(s["pincfg"][i] >> 2) & 3 for i in range(8)]
    free = [i for i in range(8) if drive[i] == 0]   # nobody else drives these
    od = [i for i in range(8) if drive[i] == 2]     # open drain: may pull low
    oe = sum(1 << i for i in free)
    out, low, ui = rng.getrandbits(8), 0, rng.getrandbits(2)
    rate = rng.choice([0.05, 0.15, 0.4])
    seq = []
    for c in range(CYCLES):
        if c < EN_OFF:
            for i in free:
                if rng.random() < rate:
                    out ^= 1 << i
            for i in od:
                if rng.random() < rate / 2:
                    low ^= 1 << i
            for i in range(2):
                if rng.random() < rate:
                    ui ^= 1 << i
        seq.append((oe | low, out & ~low & 0xFF, ui))
    return seq


# ------------------------------------------------------------ chip side
async def load(chip, s):
    """Configure the chip. Always the same transactions, whatever the values."""
    await chip.set_quad()
    await chip.write(regs.IMEM_ADDR, 0)
    await chip.write16(regs.IMEM_DATA, s["imem"])
    await chip.write(regs.G2_ADDR, 0)
    await chip.write(regs.G2_DATA, s["g2_table"])
    await chip.write(regs.G2_IN0, s["g2_in"] + [s["g2_state"]])
    await chip.write(regs.NEGSEL, s["negsel"])
    await chip.write(regs.PINCFG, s["pincfg"])
    for i, e in enumerate(s["engines"]):
        await chip.write16(regs.engine_reg(i, regs.INSTR), xy_loader(e["x"], e["y"]))
        await chip.ereg(i, regs.SHIFTCTRL, regs.fifo_mode(regs.FIFO_RAM))
        await chip.ereg(i, regs.RAM_ADDR, 0)
        await chip.write16(regs.engine_reg(i, regs.RAM_DATA), e["entries"])
        await chip.write(regs.engine_reg(i, regs.CLKDIV), e["regs"])
        await chip.write32(regs.engine_reg(i, regs.LFSR_POLY), e["poly"])
        await chip.write32(regs.engine_reg(i, regs.LFSR_VALUE), e["lfsr"])
    await chip.write(regs.CMD, regs.CMD_RESTART0 | regs.CMD_RESTART1 | regs.CMD_CLEAR0 | regs.CMD_CLEAR1)
    for i, e in enumerate(s["engines"]):
        await chip.push(i, e["tx"])
        await chip.force(i, e["start"])  # jmp start
    await chip.write(regs.FLAG_SET, s["flags"])
    await chip.cycles(20)


def xy_loader(x, y):
    """Forced instructions that load X and Y through the ISR, with the
    engine registers still at their reset values."""
    words = []
    for dst, v in ((1, x), (2, y)):
        for shift, n in ((11, 5), (6, 5), (1, 5), (0, 1)):
            words.append(0xE000 | (2 << 5) | ((v >> shift) & ((1 << n) - 1)))  # set y, part
            words.append(0x6000 | (2 << 4) | n)                                  # in y, n
        words.append(0xC000 | (dst << 5) | 5)                                    # mov dst, isr
    return words


async def run(chip, s, stim):
    """Sample the pads every cycle while applying the stimulus and the
    scheduled Host writes. Returns the per-cycle pads."""
    dut = chip.dut
    schedule = {
        G2_ON: (regs.G2_CTRL, s["g2_ctrl"]),
        EN_ON: (regs.CTRL, 3),
        EN_OFF: (regs.CTRL, 0),
        G2_OFF: (regs.G2_CTRL, 0),
    }
    trace = []
    for c in range(CYCLES):
        await RisingEdge(dut.clk)
        oe, out, ui = stim[c]
        dut.ext_oe.value = oe
        dut.ext_out.value = out
        chip.set_ui_pin(6, ui & 1)
        chip.set_ui_pin(7, ui >> 1)
        if c in schedule:
            cocotb.start_soon(chip.write(*schedule[c]))
        await ReadOnly()
        trace.append((int(dut.uio_out.value), int(dut.uio_oe.value), int(dut.uo_out.value) >> 5))
    await RisingEdge(dut.clk)
    await chip.cycles(20)
    return trace


async def readback(chip):
    st = {}
    for e in range(2):
        for sel in range(7):
            st[f"e{e}.dbg{sel}"] = await chip.dbg(e, sel)
        tx, rx = await chip.levels(e)
        st[f"e{e}.levels"] = (tx, rx)
        st[f"e{e}.lfsr"] = await chip.read32(regs.engine_reg(e, regs.LFSR_VALUE))
        st[f"e{e}.capture"] = await chip.read32(regs.engine_reg(e, regs.CAPTURE))
        await chip.ereg(e, regs.RAM_ADDR, 0)
        st[f"e{e}.entries"] = await chip.read16(regs.engine_reg(e, regs.RAM_DATA), 8)
        st[f"e{e}.rx"] = await chip.pop(e, rx) if rx else []
    st["flags"] = await chip.read8(regs.FLAGS)
    st["sticky"] = await chip.read8(regs.STICKY)
    st["g2_state"] = await chip.read8(regs.G2_STATE)
    return st


# ------------------------------------------------------------ model side
def model_for(s, counter, latency):
    m = Model()
    m.imem = list(s["imem"])
    m.g2.table = list(s["g2_table"])
    m.g2.in_sel = list(s["g2_in"])
    m.g2.write_state(s["g2_state"])
    m.negsel = s["negsel"]
    m.pincfg = list(s["pincfg"])
    m.flags = s["flags"]
    m.counter = counter
    for i, e in enumerate(s["engines"]):
        eng = m.engines[i]
        r = e["regs"]
        eng.clkdiv = r[0] | (r[1] << 8)
        eng.wrap_bottom, eng.wrap_top = r[2] & 63, r[3] & 63
        eng.autopush, eng.autopull, eng.in_right, eng.out_right = (bool((r[4] >> b) & 1) for b in range(4))
        eng.buf.set_mode((r[4] >> 4) & 7)
        eng.push_thresh, eng.pull_thresh = r[5] & 15, r[5] >> 4
        eng.out_base, eng.out_count = r[6] & 15, r[6] >> 4
        eng.set_base, eng.set_count = r[7] & 15, (r[7] >> 4) & 7
        eng.in_base, eng.jmp_pin = r[8] & 15, r[8] >> 4
        eng.side_base, eng.side_count = r[9] & 15, (r[9] >> 4) & 3
        eng.side_opt, eng.side_dirs = bool(r[9] & 0x40), bool(r[9] & 0x80)
        eng.jmp_base, eng.jmp_pattern = r[10] & 63, bool(r[10] & 0x40)
        eng.status_n, eng.status_rx = r[11] & 15, bool(r[11] & 0x10)
        eng.lfsr_feed, eng.lfsr_mode, eng.lfsr_right = r[12] & 3, (r[12] >> 2) & 3, bool(r[12] & 0x10)
        eng.cap_pin, eng.cap_edge, eng.cap_flag_en = r[13] & 15, (r[13] >> 4) & 3, bool(r[13] & 0x40)
        eng.cap_flag = r[14] & 7
        eng.lfsr_poly, eng.lfsr = e["poly"], e["lfsr"]
        eng.x, eng.y, eng.pc = e["x"], e["y"], e["start"]
        eng.buf.mem = list(e["entries"])
        for w in e["tx"]:
            if not eng.buf.push_tx(w):
                m.sticky |= 1 << (2 + i)
    return m


def model_run(m, s, stim, latency):
    """Run the model through the sampled cycles. Returns the per-cycle pads."""
    writes = {
        G2_ON + latency: lambda: set_g2_ctrl(m, s["g2_ctrl"]),
        EN_ON + latency: lambda: setattr(m, "enable", 3),
        EN_OFF + latency: lambda: setattr(m, "enable", 0),
        G2_OFF + latency: lambda: set_g2_ctrl(m, 0),
    }
    trace = []
    for c in range(CYCLES):
        if c in writes:
            writes[c]()
        oe, out, ui = stim[c]
        if c == 0:
            settle(m, oe, out, s["pull_up"], ui)
        trace.append(m.cycle(oe, out, s["pull_up"], ui))
    return trace


def set_g2_ctrl(m, v):
    m.g2.enable, m.g2.owner, m.g2.mode, m.g2.emit_en = bool(v & 1), (v >> 1) & 1, (v >> 2) & 3, bool(v & 0x10)


def settle(m, oe, out, pull_up, ui):
    """Input path state after the setup, during which the inputs held."""
    uio_out, uio_oe, _ = m.pads()
    drive0 = (uio_oe & ~uio_out) | (oe & ~out)
    drive1 = (uio_oe & uio_out) | (oe & out)
    raw = (~drive0 & (drive1 | pull_up) & 0xFF) | (ui << 8)
    m.settle_inputs(raw)
    for e in m.engines:
        e.cap_prev = (m.pin_space(raw) >> e.cap_pin) & 1


def model_state(m):
    st = {}
    for i, e in enumerate(m.engines):
        st[f"e{i}.dbg0"] = (e.delay << 9) | (e.irq_waiting << 8) | (e.exec_valid << 7) | (e.stalled << 6) | e.pc
        st[f"e{i}.dbg1"] = e.x
        st[f"e{i}.dbg2"] = e.y
        st[f"e{i}.dbg3"] = e.isr
        st[f"e{i}.dbg4"] = e.osr
        st[f"e{i}.dbg5"] = (e.osr_count << 8) | e.isr_count
        st[f"e{i}.dbg6"] = e.next_instruction(m.imem)
        st[f"e{i}.levels"] = (len(e.buf.tx), len(e.buf.rx))
        st[f"e{i}.lfsr"] = e.lfsr
        st[f"e{i}.capture"] = e.capture
        st[f"e{i}.entries"] = list(e.buf.mem)
        st[f"e{i}.rx"] = [e.buf.mem[j] for j in e.buf.rx]
    st["flags"] = m.flags
    st["sticky"] = m.sticky
    st["g2_state"] = (m.g2.feed_full << 6) | (m.g2.out << 4) | m.g2.state
    return st


def first_mismatch(chip_trace, model_trace):
    for c, (a, b) in enumerate(zip(chip_trace, model_trace)):
        if a != b:
            return c
    return None


def fmt_pads(p):
    return f"uio_out={p[0]:08b} uio_oe={p[1]:08b} uo[7:5]={p[2]:03b}"


# ------------------------------------------------------------------- test
async def one_run(chip, s, stim_seed):
    """Reset the chip with the first cycle's board inputs, then configure and
    run it."""
    dut = chip.dut
    stim = stimulus(random.Random(stim_seed), s)
    dut.rst_n.value = 0
    chip.quad = False
    chip.ui = 0x01 | (stim[0][2] << 6)
    chip._ui_apply()
    chip.pull_up(s["pull_up"])
    dut.ext_oe.value, dut.ext_out.value = stim[0][0], stim[0][1]
    await chip.cycles(5)
    dut.rst_n.value = 1
    await chip.cycles(5)
    await load(chip, s)
    trace = await run(chip, s, stim)
    state = await readback(chip)
    return stim, trace, state


@cocotb.test()
async def test_random_programs_match_model(dut):
    """The chip and the reference model agree on random programs."""
    # Calibrate the two constants the model cannot know: how many cycles a
    # Host write takes to apply, and the counter value at sampler cycle 0.
    chip = Chip(dut)
    await chip.start()
    s = calibration_setup()
    stim, trace, state = await one_run(chip, s, 0)
    rise = next(c for c, p in enumerate(trace) if p[2] & 1)
    latency = rise - 1 - EN_ON
    counter = state["e0.rx"][0] - (EN_ON + latency + 1)
    dut._log.info(f"calibration: Host write latency {latency} cycles, counter at start {counter}")

    seeds = range(FIRST_SEED, FIRST_SEED + SEEDS)
    for seed in [None] + list(seeds):
        if seed is not None:
            s = random_setup(random.Random(seed))
            stim, trace, state = await one_run(chip, s, seed)
        m = model_for(s, counter, latency)
        expect = model_run(m, s, stim, latency)
        name = "calibration" if seed is None else f"seed {seed} (PE_RANDOM_SEED={seed} PE_RANDOM_SEEDS=1)"
        c = first_mismatch(trace, expect)
        assert c is None, (
            f"{name}: pads differ first in sampler cycle {c} (engines enabled at {EN_ON + latency}):\n"
            f"  chip  {fmt_pads(trace[c])}\n  model {fmt_pads(expect[c])}")
        settled = model_state(m)
        for _ in range(30):
            m.cycle(*stim[-1][:2], s["pull_up"], stim[-1][2])
        want = model_state(m)
        if want != settled:
            dut._log.info(f"{name}: model state still changing after the run; pads compared only")
            continue
        diff = {k: (state[k], want[k]) for k in want if state[k] != want[k]}
        assert not diff, f"{name}: final state differs (chip, model): {diff}"
    dut._log.info(f"{len(seeds)} random seeds match the model")
