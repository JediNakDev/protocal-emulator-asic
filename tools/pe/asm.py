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

Additions beyond PIO: `wait <pol> <gpio|pin|flag> <n> timeout` counts its
checks down in X and leaves X at 0 only if it timed out, and
`mov <dest>, isrcnt` / `mov <dest>, osrcnt` read the shift counts.

A program may span the eight pages of instruction memory. `set page, label`
selects the page of a label for the next jump taken, so `set page, far`
followed by `jmp far` crosses pages. The assembler checks that every jump to a
label in another page follows such a `set page`, and that straight-line code
does not run across a page boundary (execution wraps within its page). The
wrap bounds are page addresses, so the wrap happens at the `.wrap` address in
every page; the assembler rejects a program that would reach that address in
another page without a jump.

Comments start with ';' or '//'. Numbers may be decimal, 0x or 0b.
`.define NAME value` creates a constant.
`set x, label` loads the label's page address, which must be 0-31, and
`set x, ~label` loads 63 minus it, for `mov dest, ~x`: either way a program
can hold a return address for `mov pc`.
`.org offset` places the next instruction at a program-relative word offset,
padding unused words with `jmp 0`; it cannot move backwards.
"""

import re
from dataclasses import dataclass, field

# Opcodes, bits 15:13; `jmp` uses 00x, with bit 13 as part of its condition.
OP_JMP, OP_WAIT, OP_IN, OP_OUT, OP_PUSH_PULL_IRQ, OP_MOV, OP_SET = 0, 2, 3, 4, 5, 6, 7
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
MOV_COUNT = {"isrcnt": 0, "osrcnt": 1}  # sources of the count operation (3)
WAIT_INDEX_LIMIT = {"gpio": 16, "pin": 16, "flag": 8}
SET_DEST = {"pins": 0, "x": 1, "y": 2, "page": 3, "pindirs": 4}

PAGE_SIZE = 64   # an engine fetches from one page; `set page` selects the next
PAGES = 8
MEM_SIZE = PAGES * PAGE_SIZE


class AsmError(Exception):
    pass


@dataclass
class Program:
    name: str
    code: list            # 16-bit words, jump targets relative to the program start
    jumps: list           # (index, label) of `jmp` words whose address needs relocation
    labels: dict
    wrap_target: int
    wrap: int
    side_count: int = 0
    side_opt: bool = False
    side_pindirs: bool = False
    origin: int = None
    defines: dict = field(default_factory=dict)
    pages: list = field(default_factory=list)   # (index, label) of `set page, label`
    sets: list = field(default_factory=list)    # (index, label, inverted) of `set x, label`

    def __len__(self):
        return len(self.code)

    def words(self, offset):
        """Machine words relocated to load at memory address `offset` (0-511).
        Jump targets are page addresses; `set page, label` gets the label's page."""
        if not 0 <= offset or offset + len(self.code) > MEM_SIZE:
            raise AsmError(f"{self.name}: does not fit in memory at offset {offset}")
        out = list(self.code)
        for i, lbl in self.pages:
            out[i] = (out[i] & ~7) | (self.labels[lbl] + offset) // PAGE_SIZE
        self._check_pages(out, offset)
        for i, lbl, inv in self.sets:
            addr = (self.labels[lbl] + offset) % PAGE_SIZE
            v = PAGE_SIZE - 1 - addr if inv else addr
            if v > 31:
                raise AsmError(f"{self.name}: `set` of {'~' if inv else ''}{lbl} needs {v},"
                               " beyond 31; use the other form")
            out[i] = (out[i] & ~0x1F) | v
        for i, lbl in self.jumps:
            out[i] = (out[i] & ~0x3F) | ((self.labels[lbl] + offset) & 0x3F)
        return out

    def _check_pages(self, words, offset):
        """Check that execution never runs across a page boundary and that
        every jump to a label goes to the label's page: the jump page there,
        over all paths, is that page. A jump sets the jump page equal to the
        page it lands in, so every label starts in its own page."""
        n = len(words)
        page = lambda i: (i + offset) // PAGE_SIZE
        top = (self.wrap + offset) % PAGE_SIZE
        state = [set() for _ in range(n)]
        work = [0, *(t for t in self.labels.values() if t < n)]
        for i in work:
            state[i].add(page(i))
        while work or any(not st for st in state):
            if not work:
                # Reached only by `out pc` or `mov pc`, which also land in the jump page.
                i = next(i for i, st in enumerate(state) if not st)
                state[i].add(page(i))
                work.append(i)
            i = work.pop()
            w = words[i]
            jp = {w & 7} if _is_set_page(w) else state[i]
            if _ends_flow(w):
                continue
            if i != self.wrap and (i + offset) % PAGE_SIZE == top:
                raise AsmError(f"{self.name}: instruction {i} is at the wrap address in its"
                               " page and would wrap; move `.wrap`")
            nxt = self.wrap_target if i == self.wrap else i + 1
            if nxt >= n:
                continue
            if page(nxt) != page(i):
                raise AsmError(f"{self.name}: instruction {i} runs across a page boundary;"
                               " execution wraps within its page")
            if not jp <= state[nxt]:
                state[nxt] |= jp
                work.append(nxt)
        for i, lbl in self.jumps:
            target = self.labels[lbl]
            if state[i] != {page(target)}:
                raise AsmError(f"{self.name}: jump at {i} to {lbl} may go to the wrong page;"
                               f" use `set page, {lbl}` before it")

    def label(self, name, offset=0):
        """Page address of a label for a program loaded at `offset`."""
        return (self.labels[name] + offset) % PAGE_SIZE

    def address(self, name, offset=0):
        """Memory address (0-511) of a label for a program loaded at `offset`."""
        return self.labels[name] + offset

    def page_address(self, index, offset=0):
        """Page address of instruction `index` for a program loaded at `offset`."""
        return (index + offset) % PAGE_SIZE

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
            elif d == ".org":
                target = _num(parts[1], defines)
                if not len(lines) <= target < MEM_SIZE:
                    raise AsmError(f"line {lineno}: .org must advance within instruction memory")
                lines.extend((lineno, "jmp 0") for _ in range(target - len(lines)))
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
    pages = []
    sets = []
    for lineno, line in lines:
        try:
            word, reloc = _encode(line, labels, defines, side_count, side_opt)
        except AsmError as e:
            raise AsmError(f"line {lineno}: {e}") from None
        if reloc and reloc[0] == "set":
            sets.append((len(code), reloc[1], reloc[2]))
        elif reloc:
            (jumps if reloc[0] == "jmp" else pages).append((len(code), reloc[1]))
        code.append(word)

    if len(code) > MEM_SIZE:
        raise AsmError(f"{name}: {len(code)} instructions exceed the memory of {MEM_SIZE}")

    return Program(name, code, jumps, labels, wrap_target, wrap, side_count, side_opt,
                   side_pindirs, origin, defines, pages, sets)


def _is_set_page(word):
    return word >> 13 == OP_SET and (word >> 5) & 7 == SET_DEST["page"]


def _ends_flow(word):
    """Whether execution never falls through `word` to the next address:
    an unconditional `jmp`, or `out pc` / `mov pc`."""
    op = word >> 13
    if op == OP_JMP and (word >> 6) & 3 == JMP_COND[""]:  # condition 0: bit 13 is 0 too
        return True
    if op == OP_OUT and (word >> 4) & 15 == OUT_DEST["pc"]:
        return True
    return op == OP_MOV and (word >> 5) & 7 == MOV_DEST["pc"]


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

    reloc = None
    if op == "nop":
        base = (OP_MOV << 13) | (MOV_DEST["y"] << 5) | MOV_SRC["y"]
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
            reloc = ("jmp", target)
        else:
            addr = _num(target, defines)
        base = ((OP_JMP | c >> 2) << 13) | ((c & 3) << 6) | (addr & 0x3F)
    elif op == "wait":
        toks = args.split()
        timed = bool(toks) and toks[-1].lower() == "timeout"
        if timed:
            toks = toks[:-1]
        if len(toks) < 2:
            raise AsmError(f"bad wait: {line}")
        pol = _num(toks[0], defines)
        src = toks[1].lower()
        if src not in WAIT_SRC or pol not in (0, 1):
            raise AsmError(f"bad wait: {line}")
        if src == "pattern":
            if len(toks) != 2:
                raise AsmError(f"bad wait: {line}")
            if timed:
                raise AsmError("a pattern wait cannot time out: the pattern uses X")
            idx = 0
        else:
            if len(toks) != 3:
                raise AsmError(f"bad wait: {line}")
            idx = _num(toks[2], defines)
            if not 0 <= idx < WAIT_INDEX_LIMIT[src]:
                raise AsmError(f"wait {src} index {idx} out of range")
        base = (OP_WAIT << 13) | (pol << 7) | (WAIT_SRC[src] << 5) | (int(timed) << 4) | idx
    elif op == "in":
        src, n = a[0].lower(), _num(a[1], defines)
        if src not in IN_SRC or not 1 <= n <= 16:
            raise AsmError(f"bad in: {line}")
        base = (OP_IN << 13) | (IN_SRC[src] << 4) | (n & 15)
    elif op == "out":
        dst, n = a[0].lower(), _num(a[1], defines)
        if dst not in OUT_DEST or not 1 <= n <= 16:
            raise AsmError(f"bad out: {line}")
        base = (OP_OUT << 13) | (OUT_DEST[dst] << 4) | (n & 15)
    elif op in ("push", "pull"):
        flag = ("iffull" if op == "push" else "ifempty") in words
        block = 0 if "noblock" in words else 1
        sub = 0 if op == "push" else 1
        base = (OP_PUSH_PULL_IRQ << 13) | (sub << 6) | (flag << 5) | (block << 4)
    elif op == "irq":
        clr, wait = 0, 0
        toks = args.split()
        if toks and toks[0].lower() in ("set", "wait", "clear"):
            clr = toks[0].lower() == "clear"
            wait = toks[0].lower() == "wait"
            toks = toks[1:]
        if len(toks) != 1:
            raise AsmError(f"bad irq: {line}")
        base = (OP_PUSH_PULL_IRQ << 13) | (2 << 6) | (clr << 5) | (wait << 4) | (_num(toks[0], defines) & 7)
    elif op == "mov":
        dst, src = a[0].lower(), a[1].lower().replace(" ", "")
        opbits = 0
        if src.startswith("~") or src.startswith("!"):
            opbits, src = 1, src[1:]
        elif src.startswith("::"):
            opbits, src = 2, src[2:]
        if dst in MOV_DEST and opbits == 0 and src in MOV_COUNT:
            opbits, srcbits = 3, MOV_COUNT[src]
        elif dst in MOV_DEST and src in MOV_SRC:
            srcbits = MOV_SRC[src]
        else:
            raise AsmError(f"bad mov: {line}")
        base = (OP_MOV << 13) | (MOV_DEST[dst] << 5) | (opbits << 3) | srcbits
    elif op == "set":
        dst = a[0].lower()
        if dst == "page" and a[1] in labels:
            v, reloc = 0, ("page", a[1])
        elif dst in ("x", "y") and a[1].lstrip("~") in labels:
            v, reloc = 0, ("set", a[1].lstrip("~"), a[1].startswith("~"))
        else:
            v = _num(a[1], defines)
        if dst not in SET_DEST or not 0 <= v <= 31 or (dst == "page" and v >= PAGES):
            raise AsmError(f"bad set: {line}")
        base = (OP_SET << 13) | (SET_DEST[dst] << 5) | v
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
    return base | (fld << 8), reloc
