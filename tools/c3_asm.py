#!/usr/bin/env python3
"""Minimal, fail-closed reader of the project's ca65 HuC6280 sources for the C-3 static checks (#473 C-3).

It is NOT an assembler. It reads one source file line by line and reports, for each line, the labels, the
mnemonic, the operand text, the current segment and whether the line sits inside a conditional block
(.if/.ifdef/.ifndef ... .endif: every branch is read, so the result is a superset of any one build). It resolves
simple constants (NAME = $xx / %bin / decimal / other constants, with + - < >) and classifies memory operands
(immediate / zero page / absolute, with the ca65 "a:" / "z:" forcing prefixes).

Anything it does not understand is reported as UNSUPPORTED instead of being skipped: .macro, .incbin, .define,
.repeat, macro invocations it cannot recognise, and operand expressions it cannot evaluate. The callers treat
UNSUPPORTED as a failure for files in the checked scope.
"""
import re
from dataclasses import dataclass, field
from pathlib import Path

MNEMONICS = set("""
adc and asl bbr0 bbr1 bbr2 bbr3 bbr4 bbr5 bbr6 bbr7 bbs0 bbs1 bbs2 bbs3 bbs4 bbs5 bbs6 bbs7 bcc bcs beq bit bmi
bne bpl bra brk bsr bvc bvs cla clc cld cli clv clx cly cmp cpx cpy csh csl dec dex dey eor inc inx iny jmp jsr
lda ldx ldy lsr nop ora pha php phx phy pla plp plx ply rmb0 rmb1 rmb2 rmb3 rmb4 rmb5 rmb6 rmb7 rol ror rti rts
sax say sbc sec sed sei set smb0 smb1 smb2 smb3 smb4 smb5 smb6 smb7 st0 st1 st2 sta stx sty stz sxy tai tam tax
tay tdd tia tii tin tma trb tsb tst tsx txa txs tya
""".split())
UNSUPPORTED_DIRECTIVES = {".macro", ".incbin", ".define", ".repeat", ".mac", ".endmacro", ".endmac"}
CONDITIONAL_OPEN = {".if", ".ifdef", ".ifndef", ".ifblank", ".ifnblank", ".ifconst"}
CONDITIONAL_OTHER = {".else", ".elseif", ".endif"}
SEGMENT_SHORTCUTS = {".code": "CODE", ".zeropage": "ZEROPAGE", ".bss": "BSS", ".data": "DATA", ".rodata": "RODATA"}
RAM_SEGMENTS = {"ZEROPAGE", "BSS"}   # RAM segments by convention (placed by the RAM-side linker configs);
                                     # src/bank0.cfg places the code segments (CODE, OVERRIDES, BOOT, ...) in ROM0
ZP_Y_OPS = {"ldx", "stx"}            # the only opcodes with a zero page,Y form; others assemble abs,Y
ZP_FIRST_OPS = {f"{op}{i}" for op in ("bbr", "bbs", "rmb", "smb") for i in range(8)}


@dataclass
class Line:
    file: str
    number: int
    labels: list
    mnemonic: str          # lower case, "" when none
    operand: str           # operand text, comment removed, stripped
    segment: str
    conditional: bool
    directive: str = ""    # lower case directive (".word", ".segment" ...) or ""
    text: str = ""


@dataclass
class Source:
    path: str
    lines: list = field(default_factory=list)
    symbols: dict = field(default_factory=dict)     # constants: name -> int
    labels: dict = field(default_factory=dict)      # label -> segment
    unsupported: list = field(default_factory=list) # (line number, reason)
    conditional_constants: set = field(default_factory=set)  # assigned inside .if blocks: build-dependent
    includes: list = field(default_factory=list)

    def instructions(self):
        return [ln for ln in self.lines if ln.mnemonic]


def _strip_comment(text):
    out, quote = [], None
    for ch in text:
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            out.append(ch)
        elif ch == ";":
            break
        else:
            out.append(ch)
    return "".join(out).rstrip()


_NUM = re.compile(r"^(\$[0-9A-Fa-f]+|%[01]+|[0-9]+)$")
_SYM = re.compile(r"^[A-Za-z_@][A-Za-z0-9_]*$")


def number(token):
    token = token.strip()
    if token.startswith("$"):
        return int(token[1:], 16)
    if token.startswith("%"):
        return int(token[1:], 2)
    return int(token, 10)


def evaluate(expr, symbols):
    """Value of a simple constant expression, or None when it cannot be resolved statically."""
    expr = expr.strip()
    if not expr:
        return None
    if expr[0] in "<>":
        inner = evaluate(expr[1:], symbols)
        if inner is None:
            return None
        return inner & 0xFF if expr[0] == "<" else (inner >> 8) & 0xFF
    if expr.startswith("(") and expr.endswith(")") and expr.count("(") == 1:
        return evaluate(expr[1:-1], symbols)
    parts = re.split(r"([+-])", expr)
    total, sign = 0, 1
    for part in parts:
        part = part.strip()
        if part in ("+", "-"):
            sign = 1 if part == "+" else -1
            continue
        if not part:
            return None
        if _NUM.match(part):
            value = number(part)
        elif _SYM.match(part) and part in symbols:
            value = symbols[part]
        else:
            return None
        total += sign * value
    return total


@dataclass
class Operand:
    mode: str              # "none" | "immediate" | "memory" | "accumulator" | "other"
    value: object          # int or None (unresolved)
    forced: str = ""       # "a" (absolute) / "z" (zero page) / ""
    indexed: bool = False
    indirect: bool = False
    text: str = ""
    index_reg: str = ""   # "x" / "y" / ""
    symbol: str = ""      # leading identifier of the address expression, if any
    mnemonic: str = ""

    def logical_address(self):
        """Logical CPU address of a memory operand (zero page = $2000 + value on the HuC6280), or None.

        A value <= $FF is zero page unless forced absolute (a:) or indexed by Y on an opcode without a zero
        page,Y form (only LDX / STX have one): ca65 then assembles absolute,Y."""
        if self.mode != "memory" or self.value is None or self.indirect:
            return None
        absolute_y = self.index_reg == "y" and self.mnemonic not in ZP_Y_OPS
        if self.forced == "z" or (self.forced != "a" and not absolute_y and 0 <= self.value <= 0xFF):
            return 0x2000 + (self.value & 0xFF)
        return self.value & 0xFFFF


def operand(text, symbols, mnemonic=""):
    t = text.strip()
    if mnemonic == "tst" and "," in t:            # TST #imm, mem[,x]: the memory operand is second
        t = t.split(",", 1)[1].strip()
    elif mnemonic in ZP_FIRST_OPS and "," in t:     # BBRn / BBSn zp, label: the zero-page operand first
        t = t.split(",", 1)[0].strip()
    if not t:
        return Operand("none", None, text=t)
    if t.lower() == "a":
        return Operand("accumulator", None, text=t)
    if t.startswith("#"):
        return Operand("immediate", evaluate(t[1:], symbols), text=t)
    forced = ""
    if t[:2].lower() in ("a:", "z:"):
        forced, t = t[0].lower(), t[2:]
    indirect = t.startswith("(")
    base = t
    indexed = False
    index_reg = ""
    m = re.match(r"^\(?(.*?)\)?\s*,\s*([xXyY])\)?$", t)
    if m:
        base, indexed, index_reg = m.group(1), True, m.group(2).lower()
    if indirect:
        base = base.strip("()").split(",")[0]
    value = evaluate(base, symbols)
    sym = re.match(r"^[<>]?\s*([A-Za-z_@][A-Za-z0-9_]*)", base.strip())
    return Operand("memory", value, forced, indexed, indirect, text, index_reg, sym.group(1) if sym else "",
                   mnemonic)


def parse(path, root=None):
    path = Path(path)
    rel = str(path.relative_to(root)) if root else str(path)
    rel = rel.replace("\\", "/")
    src = Source(rel)
    segment, depth = "CODE", 0
    raw_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    # first pass: constants (NAME = expr), in order; unresolved ones stay unknown. A constant assigned inside a
    # conditional block (.if / .ifdef / .ifndef ...) depends on the build (e.g. a -D option), so it is NOT
    # resolved: every use of it stays undecidable (NOT_STATIC). An unconditional constant assigned more than
    # once is unsupported (ca65 rejects a plain redefinition; the reader does not model .set).
    cdepth = 0
    assigned = {}
    for number_, raw in enumerate(raw_lines, 1):
        body = _strip_comment(raw).strip()
        first = re.split(r"\s+", body, maxsplit=1)[0].lower() if body else ""
        if first in CONDITIONAL_OPEN:
            cdepth += 1
            continue
        if first == ".endif":
            cdepth = max(0, cdepth - 1)
            continue
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:?=\s*(.+)$", body)
        if not m:
            continue
        if cdepth > 0 or m.group(1) in src.conditional_constants:
            src.conditional_constants.add(m.group(1))
            src.symbols.pop(m.group(1), None)
            continue
        if m.group(1) in assigned:
            src.unsupported.append((number_, f"constant {m.group(1)} is assigned more than once"))
        assigned[m.group(1)] = number_
        value = evaluate(m.group(2), src.symbols)
        if value is not None:
            src.symbols[m.group(1)] = value
    for number_, raw in enumerate(raw_lines, 1):
        body = _strip_comment(raw).strip()
        if not body:
            continue
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*\s*:?=", body):
            continue
        labels = []
        while True:
            m = re.match(r"^([A-Za-z_@][A-Za-z0-9_]*):\s*(.*)$", body)
            if not m:
                break
            labels.append(m.group(1))
            src.labels[m.group(1)] = segment
            body = m.group(2).strip()
        if not body:
            src.lines.append(Line(rel, number_, labels, "", "", segment, depth > 0, text=raw))
            continue
        parts = re.split(r"\s+", body, maxsplit=1)   # any whitespace (space or TAB) ends the statement word
        word, rest = parts[0], (parts[1] if len(parts) > 1 else "")
        word_l = word.lower()
        if word_l.startswith("."):
            if word_l in UNSUPPORTED_DIRECTIVES:
                src.unsupported.append((number_, f"directive {word_l} is not supported by the C-3 reader"))
            elif word_l in CONDITIONAL_OPEN:
                depth += 1
            elif word_l == ".endif":
                depth = max(0, depth - 1)
            elif word_l == ".segment":
                segment = rest.strip().strip('"')
            elif word_l in SEGMENT_SHORTCUTS:
                segment = SEGMENT_SHORTCUTS[word_l]
            elif word_l == ".include":
                src.includes.append(rest.strip().strip('"'))
            src.lines.append(Line(rel, number_, labels, "", rest.strip(), segment, depth > 0, word_l, raw))
            continue
        if word_l in MNEMONICS:
            src.lines.append(Line(rel, number_, labels, word_l, rest.strip(), segment, depth > 0, text=raw))
            continue
        src.unsupported.append((number_, f"unrecognised statement {word!r} (macro invocation or unknown mnemonic)"))
    return src
