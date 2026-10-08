# SPDX-License-Identifier: Apache-2.0
"""Cycle-accurate reference model of the chip, written from docs/spec.md.

It covers everything behind the Host port: both engines, their buffers,
checksum registers and capture units, the bit state machine, the pin block,
the coordination flags, the sticky bits and the cycle counter. The Host port
itself is not modelled; the caller applies register writes at the cycle they
take effect, through the `Chip` attributes.

The model does not share code with the RTL and follows the specification's
wording, so a disagreement between them is a bug in one of the two (or a gap
in the specification). `test/test_random.py` runs random programs on both.

One call to `Chip.cycle` covers one `clk` cycle: the pads show the outputs
registered at the start of the cycle, the board resolves the pins, everything
evaluates, and the registers update at the closing edge.
"""

M16 = 0xFFFF
M32 = 0xFFFFFFFF

# Opcodes, bits 15:13
JMP0, JMP1, WAIT, IN, OUT, CTRL, MOV, SET = range(8)

OUT_PINS, OUT_X, OUT_Y, OUT_NULL, OUT_PINDIRS, OUT_PC, OUT_ISR, OUT_EXEC, OUT_LFSR, OUT_G2, OUT_RAM = range(11)


def _bit(v, i):
    return (v >> i) & 1


def _rev16(v):
    return int(f"{v & M16:016b}"[::-1], 2)


def _count(field, zero_means):
    return zero_means if field == 0 else field


def write_range(cur, base, cnt, data):
    """Pins base, base+1, ... (modulo 16) take data bits 0, 1, ...; pins
    13-15 have no register."""
    for k in range(13):
        o = (k - base) & 15
        if o < cnt:
            cur = (cur & ~(1 << k)) | (_bit(data, o) << k)
    return cur


def lfsr_step(mode, right, val, poly, d):
    """One checksum register step (G1). Returns (output bit, next value)."""
    top = val & 1 if right else val >> 31
    sh = val >> 1 if right else (val << 1) & M32
    ins = 0x80000000 if right else 1
    par = bin(val & poly).count("1") & 1
    if mode == 0:
        return d, sh ^ (poly if top ^ d else 0)
    out = d ^ par
    if mode == 1:
        return out, sh | (ins if out else 0)
    if mode == 2:
        return out, sh | (ins if d else 0)
    return out, sh | (ins if par else 0)


class Buffers:
    """Eight 16-bit entries shared by the transmit queue, receive queue and
    RAM (G5). A queue fills its entries in order from the lowest one and
    wraps; clearing it restarts at the lowest entry."""

    def __init__(self):
        self.mem = [0] * 8
        self.mode = 0
        self.clear()

    def clear(self):
        self.tx = []  # entry indices, oldest first
        self.rx = []
        self.tx_next = 0
        self.rx_next = 0

    def layout(self):
        """(transmit entries, receive entries, RAM entries) for the mode."""
        return {
            1: (range(0, 8), range(0), range(0)),
            2: (range(0), range(0, 8), range(0)),
            3: (range(0, 4), range(0), range(4, 8)),
            4: (range(0), range(4, 8), range(0, 4)),
            5: (range(0), range(0), range(0, 8)),
        }.get(self.mode, (range(0, 4), range(4, 8), range(0)))

    def set_mode(self, mode):
        self.mode = mode & 7
        self.clear()

    def tx_depth(self):
        return len(self.layout()[0])

    def rx_depth(self):
        return len(self.layout()[1])

    def is_ram(self, i):
        return i in self.layout()[2]

    def tx_empty(self):
        return len(self.tx) == 0

    def tx_full(self):
        return len(self.tx) == self.tx_depth()

    def rx_empty(self):
        return len(self.rx) == 0

    def rx_full(self):
        return len(self.rx) == self.rx_depth()

    def tx_head(self):
        return self.mem[self.tx[0]]

    def push_tx(self, word):
        """Host write to the transmit queue. Returns False if it was full."""
        if self.tx_full():
            return False
        ents = self.layout()[0]
        i = ents[self.tx_next % len(ents)]
        self.tx_next += 1
        self.mem[i] = word & M16
        self.tx.append(i)
        return True

    def pop_tx(self):
        self.tx.pop(0)

    def push_rx(self, word):
        ents = self.layout()[1]
        i = ents[self.rx_next % len(ents)]
        self.rx_next += 1
        self.mem[i] = word & M16
        self.rx.append(i)

    def pop_rx(self):
        """Host read of the receive queue; 0 when empty."""
        if not self.rx:
            return 0
        return self.mem[self.rx.pop(0)]


class Engine:
    def __init__(self, index):
        self.index = index
        # Configuration (engine registers), as reset
        self.clkdiv = 0
        self.wrap_bottom = 0
        self.wrap_top = 63
        self.autopush = self.autopull = self.in_right = self.out_right = False
        self.push_thresh = self.pull_thresh = 0  # 4-bit fields; 0 means 16
        self.out_base, self.out_count = 0, 1     # count 0 means 16
        self.set_base, self.set_count = 0, 1
        self.in_base, self.jmp_pin = 0, 0
        self.side_base, self.side_count, self.side_opt, self.side_dirs = 0, 0, False, False
        self.jmp_base, self.jmp_pattern = 0, False
        self.status_n, self.status_rx = 0, False
        self.lfsr_feed, self.lfsr_mode, self.lfsr_right = 0, 0, False
        self.cap_pin, self.cap_edge, self.cap_flag_en, self.cap_flag = 0, 0, False, 0
        self.lfsr_poly = 0
        self.buf = Buffers()
        # Execution state
        self.pc = 0
        self.x = self.y = 0
        self.isr = self.isr_count = 0
        self.osr, self.osr_count = 0, 16
        self.delay = 0
        self.exec_valid, self.exec_instr = False, 0
        self.irq_waiting = False
        self.stalled = False
        self.pin_out = self.pin_dir = 0
        self.lfsr = 0
        self.div = 0
        self.capture = 0
        self.cap_prev = 0

    # -- derived configuration
    def push_thr(self):
        return _count(self.push_thresh, 16)

    def pull_thr(self):
        return _count(self.pull_thresh, 16)

    def set_cnt(self):
        return min(self.set_count, 5)

    def osr_empty(self):
        return self.osr_count >= self.pull_thr()

    def next_instruction(self, imem):
        return self.exec_instr if self.exec_valid else imem[self.pc]

    def side_fields(self, sd):
        """(side-set enabled, value, delay) from instruction bits 12:8."""
        c = self.side_count
        if self.side_opt:
            return bool(_bit(sd, 4)) and c > 0, (sd >> (4 - c)) & ((1 << c) - 1), sd & ((1 << (4 - c)) - 1)
        return c > 0, sd >> (5 - c), sd & ((1 << (5 - c)) - 1)

    def evaluate(self, chip, enable, pins, owns_g2, emit, emit_bit):
        """Compute this cycle's effects. Returns a dict applied by `commit`."""
        b = self.buf
        e = {"flag_set": 0, "flag_clr": 0, "sticky": 0, "feed_wr": False, "feed_bit": 0}
        n = {}  # next register values

        div_tick = enable and self.div == self.clkdiv
        tick = div_tick if enable else self.exec_valid
        e["div_tick"] = div_tick
        n["div"] = 0 if (not enable or div_tick) else self.div + 1

        # Edge capture (G3)
        cap_in = _bit(pins, self.cap_pin)
        ev = ((self.cap_edge & 1) and cap_in and not self.cap_prev) or ((self.cap_edge & 2) and not cap_in and self.cap_prev)
        n["cap_prev"] = cap_in
        if ev:
            n["capture"] = chip.counter
            if self.cap_flag_en:
                e["flag_set"] |= 1 << self.cap_flag
                if _bit(chip.flags, self.cap_flag):
                    e["sticky"] |= 1 << (6 + self.index)

        lfsr = self.lfsr
        isr, isr_count = self.isr, self.isr_count
        osr, osr_count = self.osr, self.osr_count
        x, y = self.x, self.y
        pin_out, pin_dir = self.pin_out, self.pin_dir
        tx_pop, rx_push = False, None
        ram_write = None

        if tick and self.delay:
            n["delay"] = self.delay - 1
        elif tick:
            instr = self.next_instruction(chip.imem)
            op = instr >> 13
            f = (instr >> 4) & 15
            nb = _count(instr & 15, 16)
            mask = (1 << nb) - 1
            one_bit = (instr & 15) == 1
            pins_rot = ((pins >> self.in_base) | (pins << (16 - self.in_base))) & M16
            stall = False
            jump = None
            exec_new = None

            in_steps = op == IN and one_bit and self.lfsr_feed & 2
            out_steps = op == OUT and one_bit and self.lfsr_feed & 1 and f != OUT_LFSR
            touches_isr = op == IN or (op == CTRL and (instr >> 6) & 3 == 0) or (op == MOV and (instr >> 5) & 7 == 6) or (op == OUT and f == OUT_ISR)
            touches_lfsr = in_steps or out_steps or (op == OUT and f == OUT_LFSR)

            if emit and (touches_isr or (touches_lfsr and self.lfsr_feed & 2)):
                stall = True
            elif op in (JMP0, JMP1):
                cond = ((instr >> 13) & 1) << 2 | (instr >> 6) & 3
                if cond == 0:
                    taken = True
                elif cond == 1:
                    taken = x == 0
                elif cond == 2:
                    taken = x != 0
                    x = (x - 1) & M16
                elif cond == 3:
                    taken = y == 0
                elif cond == 4:
                    taken = y != 0
                    y = (y - 1) & M16
                elif cond == 5:
                    taken = x != y
                elif cond == 6:
                    taken = (pins & self.y) == self.x if self.jmp_pattern else bool(_bit(pins, self.jmp_pin))
                else:
                    taken = not self.osr_empty()
                if taken:
                    jump = instr & 63
            elif op == WAIT:
                pol = _bit(instr, 7)
                src = (instr >> 5) & 3
                if src == 0:
                    v = _bit(pins, instr & 15)
                elif src == 1:
                    v = _bit(pins, (self.in_base + (instr & 15)) & 15)
                elif src == 2:
                    v = _bit(chip.flags, instr & 7)
                else:
                    v = int((pins & self.y) == self.x)
                if v != pol:
                    stall = True
                elif src == 2 and pol:
                    e["flag_clr"] |= 1 << (instr & 7)
            elif op == IN:
                src = {0: pins_rot, 1: self.x, 2: self.y, 4: b.mem[self.y & 7], 5: chip.counter & M16,
                       6: self.isr, 7: self.osr, 8: self.lfsr & M16, 9: self.lfsr >> 16,
                       10: self.capture & M16, 11: self.capture >> 16}.get(f, 0)
                if in_steps:
                    d, lfsr = lfsr_step(self.lfsr_mode, self.lfsr_right, self.lfsr, self.lfsr_poly, src & 1)
                    src = (src & ~1) | d
                if self.in_right:
                    isr = ((self.isr >> nb) | (src << (16 - nb))) & M16
                else:
                    isr = ((self.isr << nb) | (src & mask)) & M16
                isr_count = min(16, self.isr_count + nb)
                if self.autopush and isr_count >= self.push_thr():
                    if b.rx_full():
                        stall = True
                    else:
                        rx_push = isr
                        isr, isr_count = 0, 0
            elif op == OUT:
                refill = self.autopull and self.osr_empty()
                if refill and b.tx_empty():
                    stall = True
                elif f == OUT_G2 and owns_g2 and chip.g2.feed_full:
                    stall = True
                else:
                    src = b.tx_head() if refill else self.osr
                    tx_pop = refill
                    if self.out_right:
                        data, osr = src & mask, src >> nb
                    else:
                        data, osr = src >> (16 - nb), (src << nb) & M16
                    osr_count = min(16, (0 if refill else self.osr_count) + nb)
                    if out_steps:
                        data, lfsr = lfsr_step(self.lfsr_mode, self.lfsr_right, self.lfsr, self.lfsr_poly, data & 1)
                    if f == OUT_PINS:
                        pin_out = write_range(pin_out, self.out_base, _count(self.out_count, 16), data)
                    elif f == OUT_X:
                        x = data
                    elif f == OUT_Y:
                        y = data
                    elif f == OUT_PINDIRS:
                        pin_dir = write_range(pin_dir, self.out_base, _count(self.out_count, 16), data)
                    elif f == OUT_PC:
                        jump = (self.jmp_base + data) & 63
                    elif f == OUT_ISR:
                        isr, isr_count = data, nb
                    elif f == OUT_EXEC:
                        exec_new = data
                    elif f == OUT_LFSR:
                        lfsr = ((self.lfsr << nb) | data) & M32
                    elif f == OUT_G2:
                        if owns_g2:
                            e["feed_wr"], e["feed_bit"] = True, data & 1
                    elif f == OUT_RAM:
                        if b.is_ram(self.y & 7):
                            ram_write = (self.y & 7, data)
            elif op == CTRL:
                sub = (instr >> 6) & 3
                cond_bit, blk = _bit(instr, 5), _bit(instr, 4)
                if sub == 0:  # push
                    if not cond_bit or self.isr_count >= self.push_thr():
                        if b.rx_full():
                            if blk:
                                stall = True
                            else:
                                e["sticky"] |= 1 << self.index
                                isr, isr_count = 0, 0
                        else:
                            rx_push = self.isr
                            isr, isr_count = 0, 0
                elif sub == 1:  # pull
                    if not cond_bit or self.osr_empty():
                        if b.tx_empty():
                            if blk:
                                stall = True
                            else:
                                osr, osr_count = self.x, 0
                        else:
                            tx_pop = True
                            osr, osr_count = b.tx_head(), 0
                elif sub == 2:  # irq
                    flag = instr & 7
                    if cond_bit:
                        e["flag_clr"] |= 1 << flag
                    elif not self.irq_waiting:
                        e["flag_set"] |= 1 << flag
                        if blk:
                            n["irq_waiting"] = True
                            stall = True
                    elif _bit(chip.flags, flag):
                        stall = True
                    else:
                        n["irq_waiting"] = False
            elif op == MOV:
                srcsel = instr & 7
                if srcsel == 4:
                    level = len(b.rx) if self.status_rx else len(b.tx)
                    src = M16 if level < self.status_n else 0
                else:
                    src = {0: pins_rot, 1: self.x, 2: self.y, 3: 0, 5: self.isr, 6: self.osr, 7: b.mem[self.y & 7]}[srcsel]
                o = (instr >> 3) & 3
                v = (~src & M16) if o == 1 else _rev16(src) if o == 2 else src
                dst = (instr >> 5) & 7
                if dst == 0:
                    pin_out = write_range(pin_out, self.out_base, _count(self.out_count, 16), v)
                elif dst == 1:
                    x = v
                elif dst == 2:
                    y = v
                elif dst == 3:
                    pin_dir = write_range(pin_dir, self.out_base, _count(self.out_count, 16), v)
                elif dst == 4:
                    exec_new = v
                elif dst == 5:
                    jump = v & 63
                elif dst == 6:
                    isr, isr_count = v, 0
                else:
                    osr, osr_count = v, 0
            elif op == SET:
                dst, v = (instr >> 5) & 7, instr & 31
                if dst == 0:
                    pin_out = write_range(pin_out, self.set_base, self.set_cnt(), v)
                elif dst == 1:
                    x = v
                elif dst == 2:
                    y = v
                elif dst == 4:
                    pin_dir = write_range(pin_dir, self.set_base, self.set_cnt(), v)

            # Side-set: on every issue, after (so over) the instruction's own pin writes
            side_en, side_val, dly = self.side_fields((instr >> 8) & 31)
            n["stalled"] = stall
            if stall:
                # A stalled instruction has no effect except side-set, and the
                # flag that a first `irq wait` sets before waiting.
                x, y, isr, isr_count, osr, osr_count, lfsr = self.x, self.y, self.isr, self.isr_count, self.osr, self.osr_count, self.lfsr
                pin_out, pin_dir = self.pin_out, self.pin_dir
                tx_pop, rx_push, ram_write = False, None, None
            if side_en:
                if self.side_dirs:
                    pin_dir = write_range(pin_dir, self.side_base, self.side_count, side_val)
                else:
                    pin_out = write_range(pin_out, self.side_base, self.side_count, side_val)
            if not stall:
                if jump is not None:
                    n["pc"] = jump
                elif not self.exec_valid:
                    n["pc"] = self.wrap_bottom if self.pc == self.wrap_top else (self.pc + 1) & 63
                n["exec_valid"] = exec_new is not None
                if exec_new is not None:
                    n["exec_instr"] = exec_new & M16
                n["delay"] = 0 if exec_new is not None else dly

        # Bit state machine emission into the ISR (G2)
        if emit:
            d = emit_bit
            if self.lfsr_feed & 2:
                d, lfsr = lfsr_step(self.lfsr_mode, self.lfsr_right, lfsr, self.lfsr_poly, d)
            isr_e = ((d << 15) | (self.isr >> 1)) if self.in_right else ((self.isr << 1) & M16) | d
            cnt_e = min(16, self.isr_count + 1)
            if self.autopush and cnt_e >= self.push_thr():
                if b.rx_full():
                    e["sticky"] |= 1 << self.index
                else:
                    rx_push = isr_e
                isr, isr_count = 0, 0
            else:
                isr, isr_count = isr_e, cnt_e

        n.update(x=x, y=y, isr=isr, isr_count=isr_count, osr=osr, osr_count=osr_count, lfsr=lfsr,
                 pin_out=pin_out, pin_dir=pin_dir)
        e["next"] = n
        e["tx_pop"], e["rx_push"], e["ram_write"] = tx_pop, rx_push, ram_write
        return e

    def commit(self, e):
        for k, v in e["next"].items():
            setattr(self, k, v)
        if e["tx_pop"]:
            self.buf.pop_tx()
        if e["rx_push"] is not None:
            self.buf.push_rx(e["rx_push"])
        if e["ram_write"] is not None:
            i, v = e["ram_write"]
            self.buf.mem[i] = v & M16


class G2:
    """Bit state machine (G2)."""

    def __init__(self):
        self.table = [0] * 64
        self.enable, self.owner, self.mode, self.emit_en = False, 0, 0, False
        self.in_sel = [0, 0]
        self.state, self.out = 0, 0
        self.feed_full, self.feed_bit = False, 0
        self.emit_pending, self.emit_bit = False, 0

    def source(self, sel, pins):
        if sel < 16:
            return _bit(pins, sel)
        if sel == 16:
            return self.feed_bit
        if sel == 17:
            return int(self.feed_full)
        return 0

    def write_state(self, v):
        """Host write to G2_STATE."""
        self.state, self.out = v & 15, (v >> 4) & 3
        self.feed_full = False
        self.emit_pending = False

    def evaluate(self, pins, owner_tick, feed_wr, feed_bit):
        entry = self.table[(self.state << 2) | (self.source(self.in_sel[1], pins) << 1) | self.source(self.in_sel[0], pins)]
        step = self.enable and (self.mode == 0 or (self.mode == 1 and owner_tick) or (self.mode == 2 and self.feed_full))
        n = {"emit_pending": bool(step and _bit(entry, 6) and self.emit_en)}
        if step:
            n.update(state=entry & 15, out=(entry >> 4) & 3, emit_bit=_bit(entry, 4))
        if step and self.feed_full and (self.mode == 2 or _bit(entry, 7)):
            n["feed_full"] = False
        elif feed_wr and not self.feed_full:
            n.update(feed_full=True, feed_bit=feed_bit)
        return n

    def commit(self, n):
        for k, v in n.items():
            setattr(self, k, v)


class Chip:
    def __init__(self):
        self.imem = [0] * 64
        self.engines = [Engine(0), Engine(1)]
        self.g2 = G2()
        self.enable = 0          # CTRL bits 1:0
        self.flags = 0
        self.sticky = 0
        self.counter = 0
        self.pincfg = [0] * 13
        self.negsel = 0
        # Input path registers for pins 0-9
        self.sync1 = self.sync2 = 0
        self.filt = 0
        self.fcount = [0] * 10
        self.neg = 0             # falling-edge sample, retimed

    # -- pin block
    def pads(self):
        """(uio_out, uio_oe, uo[7:5]) driven during this cycle."""
        uio_out = uio_oe = uo = 0
        for i in range(13):
            cfg = self.pincfg[i]
            owner = cfg & 3
            if owner < 2:
                e = self.engines[owner]
                v, d = _bit(e.pin_out, i), _bit(e.pin_dir, i)
            else:
                v, d = _bit(self.g2.out, owner - 2), 1
            v ^= _bit(cfg, 4)
            drive = (cfg >> 2) & 3
            oe, val = ((0, 0), (d, v), (1 - v, 0), (1, v))[drive]
            if i < 8:
                uio_out |= val << i
                uio_oe |= oe << i
            elif i >= 10:
                # Output-only: open-drain acts as push-pull, a released pin drives 0.
                pad = v if drive == 2 else (oe & val)
                uo |= pad << (i - 10)
        return uio_out, uio_oe, uo

    def pin_space(self, raw):
        """The 16 pins engines read this cycle, from raw pad values 0-9."""
        pins = 0
        for i in range(10):
            cfg = self.pincfg[i]
            path = _bit(raw, i) if cfg & 0x20 else _bit(self.sync2, i)
            pins |= (path if (cfg >> 6) == 0 else _bit(self.filt, i)) << i
        _, _, uo = self.pads()
        return pins | (uo << 10) | (self.g2.out << 13) | (self.neg << 15)

    def cycle(self, ext_oe=0, ext_out=0, pull_up=0, ui_pins=0):
        """Advance one clock cycle. Board inputs hold for the whole cycle;
        `ui_pins` is pins 8 and 9. Returns the pads of this cycle."""
        uio_out, uio_oe, uo = self.pads()
        drive0 = (uio_oe & ~uio_out) | (ext_oe & ~ext_out)
        drive1 = (uio_oe & uio_out) | (ext_oe & ext_out)
        uio_in = ~drive0 & (drive1 | pull_up) & 0xFF
        raw = uio_in | ((ui_pins & 3) << 8)
        pins = self.pin_space(raw)

        g2_owner = self.g2.owner
        effects = []
        for i, eng in enumerate(self.engines):
            emit = self.g2.emit_pending and g2_owner == i
            effects.append(eng.evaluate(self, bool(_bit(self.enable, i)), pins, g2_owner == i, emit, self.g2.emit_bit))
        own = effects[g2_owner]
        g2n = self.g2.evaluate(pins, own["div_tick"], own["feed_wr"], own["feed_bit"])

        # Closing edge
        flag_set = effects[0]["flag_set"] | effects[1]["flag_set"]
        flag_clr = effects[0]["flag_clr"] | effects[1]["flag_clr"]
        self.flags = (self.flags & ~flag_clr) | flag_set
        self.sticky |= effects[0]["sticky"] | effects[1]["sticky"]
        for eng, e in zip(self.engines, effects):
            eng.commit(e)
        self.g2.commit(g2n)
        for i in range(10):
            path = _bit(raw, i) if self.pincfg[i] & 0x20 else _bit(self.sync2, i)
            n_req = (2, 2, 4, 8)[self.pincfg[i] >> 6]
            if path == _bit(self.filt, i):
                self.fcount[i] = 0
            elif self.fcount[i] + 1 == n_req:
                self.filt ^= 1 << i
                self.fcount[i] = 0
            else:
                self.fcount[i] += 1
        self.sync2, self.sync1 = self.sync1, raw
        self.neg = _bit(raw, self.negsel) if self.negsel < 10 else 0
        self.counter = (self.counter + 1) & M32
        return uio_out, uio_oe, uo

    def settle_inputs(self, raw):
        """Input path state after `raw` has been stable for a long time."""
        self.sync1 = self.sync2 = self.filt = raw
        self.fcount = [0] * 10
        self.neg = _bit(raw, self.negsel) if self.negsel < 10 else 0
