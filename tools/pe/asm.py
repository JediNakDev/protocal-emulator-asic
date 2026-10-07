# SPDX-License-Identifier: Apache-2.0
"""Assembler for protocol emulator programs. Encoding: docs/spec.md.

Syntax follows RP2040 PIO assembly where the instruction sets overlap:

    .program uart_tx
    .side_set 1 opt
    .wrap_target
        pull       side 1 [7]
        set x, 7   side 0 [7]
    bitloop:
        out pins, 1
        jmp x-- bitloop [6]
    .wrap

Comments start with ';' or '//'. Numbers may be decimal, 0x or 0b.
`.define NAME value` creates a constant.
"""

import re
from dataclasses import dataclass, field

JMP_COND = {"": 0, "!x": 1, "x--": 2, "!y": 3, "y--": 4, "x!=y": 5, "pin": 6, "!osre": 7}
WAIT_SRC = {"gpio": 0, "pin": 1, "flag": 2, "pattern": 3}
IN_SRC = {
    "pins": 0, "x": 1, "y": 2, "null": 3, "ram": 4, "count": 5, "isr": 6, "osr": 7,
    "lfsrl": 8, "lfsrh": 9, "capl": 10, "caph": 11,
}
OUT_DEST = {
    "pins": 0, "x": 1, "y": 2, "null": 3, "pindirs": 4, "pc": 5, "isr": 6, "exec": 7,
    "lfsr": 8, "g2": 9, "ram": 10,
}
MOV_DEST = {"pins": 0, "x": 1, "y": 2, "pindirs": 3, "exec": 4, "pc": 5, "isr": 6, "osr": 7}
MOV_SRC = {"pins": 0, "x": 1, "y": 2, "null": 3, "status": 4, "isr": 5, "osr": 6, "ram": 7}
SET_DEST = {"pins": 0, "x": 1, "y": 2, "pindirs": 4}

MEM_SIZE = 64


class AsmError(Exception):
    pass


@dataclass
class Program:
    name: str
    code: list            # 16-bit words, jump targets relative to the program start
    jumps: list           # indices of `jmp` words whose address needs relocation
    labels: dict
    wrap_target: int
    wrap: int
    side_count: int = 0
    side_opt: bool = False
    side_pindirs: bool = False
    origin: int = None
    defines: dict = field(default_factory=dict)

    def __len__(self):
        return len(self.code)

    def words(self, offset):
        """Machine words relocated to load at `offset`."""
        if offset + len(self.code) > MEM_SIZE:
            raise AsmError(f"{self.name}: does not fit at offset {offset}")
        out = list(self.code)
        for i in self.jumps:
            out[i] = (out[i] & ~0x3F) | ((out[i] + offset) & 0x3F)
        return out

    def label(self, name, offset=0):
        return (self.labels[name] + offset) % MEM_SIZE

    def sidepin(self, base):
        """SIDEPIN register value for this program's side-set config."""
        return (base & 15) | (self.side_count << 4) | (self.side_opt << 6) | (self.side_pindirs << 7)


def _num(tok, defines):
    tok = tok.strip()
    if tok in defines:
        return defines[tok]
    try:
        return int(tok, 0)
    except ValueError:
        raise AsmError(f"bad number: {tok!r}") from None


def _strip(line):
    for c in (";", "//"):
        if c in line:
            line = line[: line.index(c)]
    return line.strip()


def assemble(text):
    """Assemble one program. Returns a Program."""
    name = "program"
    defines = {}
    labels = {}
    lines = []
    side_count, side_opt, side_pindirs = 0, False, False
    wrap_target = None
    wrap = None
    origin = None

    # Pass 1: directives and labels.
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = _strip(raw)
        if not line:
            continue
        if line.startswith("."):
            parts = line.split()
            d = parts[0]
            if d == ".program":
                name = parts[1]
            elif d == ".define":
                defines[parts[1]] = _num(parts[2], defines)
            elif d == ".side_set":
                side_count = _num(parts[1], defines)
                side_opt = "opt" in parts[2:]
                side_pindirs = "pindirs" in parts[2:]
                if not 0 <= side_count <= 3:
                    raise AsmError(f"line {lineno}: side_set count must be 0-3")
            elif d == ".wrap_target":
                wrap_target = len(lines)
            elif d == ".wrap":
                wrap = len(lines) - 1
            elif d == ".origin":
                origin = _num(parts[1], defines)
            else:
                raise AsmError(f"line {lineno}: unknown directive {d}")
            continue
        while True:
            m = re.match(r"^([A-Za-z_]\w*):\s*(.*)$", line)
            if not m:
                break
            labels[m.group(1)] = len(lines)
            line = m.group(2)
        if line:
            lines.append((lineno, line))

    if wrap_target is None:
        wrap_target = 0
    if wrap is None:
        wrap = len(lines) - 1

    code = []
    jumps = []
    for lineno, line in lines:
        try:
            word, is_jmp = _encode(line, labels, defines, side_count, side_opt)
        except AsmError as e:
            raise AsmError(f"line {lineno}: {e}") from None
        if is_jmp:
            jumps.append(len(code))
        code.append(word)

    if len(code) > MEM_SIZE:
        raise AsmError(f"{name}: {len(code)} instructions exceed {MEM_SIZE}")

    return Program(name, code, jumps, labels, wrap_target, wrap, side_count, side_opt,
                   side_pindirs, origin, defines)


def _encode(line, labels, defines, side_count, side_opt):
    # Delay: trailing [n]
    delay = 0
    m = re.search(r"\[\s*([^\]]+)\]\s*$", line)
    if m:
        delay = _num(m.group(1), defines)
        line = line[: m.start()].strip()
    # Side-set: trailing `side v`
    side = None
    m = re.search(r"\bside\s+(\S+)\s*$", line)
    if m:
        side = _num(m.group(1), defines)
        line = line[: m.start()].strip()

    parts = line.split(None, 1)
    op = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""
    a = [x.strip() for x in args.split(",")] if args else []
    words = args.lower().split()

    is_jmp = False
    if op == "nop":
        base = (6 << 13) | (2 << 5) | 2
    elif op == "jmp":
        toks = args.split()
        if len(toks) == 1:
            cond, target = "", toks[0]
        elif len(toks) == 2:
            cond, target = toks[0].lower(), toks[1]
        else:
            raise AsmError(f"bad jmp: {line}")
        if cond not in JMP_COND:
            raise AsmError(f"bad jmp condition {cond}")
        c = JMP_COND[cond]
        if target in labels:
            addr = labels[target]
            is_jmp = True
        else:
            addr = _num(target, defines)
        base = ((c >> 2) << 13) | ((c & 3) << 6) | (addr & 0x3F)
    elif op == "wait":
        toks = args.split()
        if len(toks) < 2:
            raise AsmError(f"bad wait: {line}")
        pol = _num(toks[0], defines)
        src = toks[1].lower()
        if src not in WAIT_SRC:
            raise AsmError(f"bad wait source {src}")
        idx = 0 if src == "pattern" else _num(toks[2], defines)
        base = (2 << 13) | ((pol & 1) << 7) | (WAIT_SRC[src] << 5) | (idx & 0x1F)
    elif op == "in":
        src, n = a[0].lower(), _num(a[1], defines)
        if src not in IN_SRC or not 1 <= n <= 16:
            raise AsmError(f"bad in: {line}")
        base = (3 << 13) | (IN_SRC[src] << 4) | (n & 15)
    elif op == "out":
        dst, n = a[0].lower(), _num(a[1], defines)
        if dst not in OUT_DEST or not 1 <= n <= 16:
            raise AsmError(f"bad out: {line}")
        base = (4 << 13) | (OUT_DEST[dst] << 4) | (n & 15)
    elif op in ("push", "pull"):
        flag = ("iffull" if op == "push" else "ifempty") in words
        block = 0 if "noblock" in words else 1
        sub = 0 if op == "push" else 1
        base = (5 << 13) | (sub << 6) | (flag << 5) | (block << 4)
    elif op == "irq":
        clr, wait = 0, 0
        toks = args.split()
        if toks and toks[0].lower() in ("set", "wait", "clear"):
            clr = toks[0].lower() == "clear"
            wait = toks[0].lower() == "wait"
            toks = toks[1:]
        if len(toks) != 1:
            raise AsmError(f"bad irq: {line}")
        base = (5 << 13) | (2 << 6) | (clr << 5) | (wait << 4) | (_num(toks[0], defines) & 7)
    elif op == "mov":
        dst, src = a[0].lower(), a[1].lower().replace(" ", "")
        opbits = 0
        if src.startswith("~") or src.startswith("!"):
            opbits, src = 1, src[1:]
        elif src.startswith("::"):
            opbits, src = 2, src[2:]
        if dst not in MOV_DEST or src not in MOV_SRC:
            raise AsmError(f"bad mov: {line}")
        base = (6 << 13) | (MOV_DEST[dst] << 5) | (opbits << 3) | MOV_SRC[src]
    elif op == "set":
        dst, v = a[0].lower(), _num(a[1], defines)
        if dst not in SET_DEST or not 0 <= v <= 31:
            raise AsmError(f"bad set: {line}")
        base = (7 << 13) | (SET_DEST[dst] << 5) | v
    else:
        raise AsmError(f"unknown instruction {op}")

    # Side-set and delay field, bits 12:8.
    dbits = 5 - side_count - (1 if side_opt else 0)
    if not 0 <= delay < (1 << dbits):
        raise AsmError(f"delay {delay} does not fit in {dbits} bits")
    fld = delay
    if side_count:
        if side is None:
            if not side_opt:
                raise AsmError("side-set value required (side_set is not optional)")
        else:
            if not 0 <= side < (1 << side_count):
                raise AsmError(f"side value {side} does not fit")
            fld |= side << dbits
            if side_opt:
                fld |= 1 << 4
    elif side is not None:
        raise AsmError("side-set used but .side_set is 0")
    return base | (fld << 8), is_jmp
