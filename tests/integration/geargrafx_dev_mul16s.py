"""Real Geargrafx run of MA_MUL16S (slot $4F, Issue #494) through the DEV ROM entry $E0ED.

DEV-only, project-own contract (docs/ma-mul16s.md); NOT the reference System Card ABI, which stays UNVERIFIED.

A guest HuC6280 program (assembled with the pinned ca65/ld65 and written over the reset code of the built DEV ROM)
pushes the four operand bytes (multiplicand lo, hi, multiplier lo, hi), presets Y and P, JSRs the entry and stores the
product frame, P, Y and S after the call. The guest runs every multiplication on the pinned core; the host only
supplies operands (a table in work RAM, or a counter the guest increments) and compares the recorded result with
tools/ma_mul16s_model.expected() (Python integer multiplication, independent of the routine's algorithm).

geargrafx_mul16s_probe.cpp is an observer: it steps the core one instruction at a time, records the guest's result
area at a checkpoint PC, and arms CPU-address breakpoints for the routine's own instructions (PC window). A store of
the same value or a write-and-restore still raises the flag, which a before/after RAM diff cannot see. Per call the
routine may write only the six bytes it pushes below the JSR return address and the four frame bytes above it; it may
not write the JSR return bytes, any other stack byte, zero page, BSS, I/O or the ROM window, and may not read zero page,
BSS, I/O or stack bytes outside its own frame. MPR0-7 are compared after every instruction.

Suites:
  default  boundary/edge/random/flag tables at caller SP $0E (entry SP $08), $40 and $FF (entry SP $F9), the write
           and read traces on those runs, the final work-RAM diff, and the mutants (each must be rejected).
  --full   additionally the large sweeps: every 65,536 multiplier x 6 multiplicands and every 65,536 multiplicand x
           6 multipliers (786,432 calls) at caller SP $FF and again at $0E (1,572,864 calls), with the trace.
           Manual (`make dev-mul16s-sweep`), not part of CI.
Not checked: A and X (declared clobbered); T=1 is out of contract and no case runs it; VDC/PSG state is not observed.
Run: python3 tests/integration/geargrafx_dev_mul16s.py --rom build/debug/bios-dev/dev-only-not-compatible-syscard3.pce
Needs the pinned cc65 and Geargrafx core (make setup, TOOLS_DIR on Linux). Missing tools exit 3 (BLOCKED).
"""
import argparse
import hashlib
import itertools
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_rom import verify_dev_rom  # noqa: E402
from environment import executable  # noqa: E402
import geargrafx_api as api  # noqa: E402
import ma_mul16s_model as model  # noqa: E402

BLOCKED_EXIT = 3
SLOT = 0x4F
SENTINEL = 0xC3
TABLE = 0x2400  # work RAM: u16 count, then 6-byte entries (a lo, a hi, b lo, b hi, Y, P)
RES = 0x2200  # guest result area: product x4, P after, Y after, S just after the call, S after the 4 pops
RES_LEN = 8
GUEST_ZP = range(0, 8)  # work RAM index of the guest's pointer/counter/temp ($2000-$2007)
MAX_ENTRIES = (0x4000 - TABLE - 2) // 6
STEPS_PER_CALL = 1000  # a call takes about 400 instructions; the budget catches a routine that never returns
EDGES = (0x0000, 0x0001, 0xFFFF, 0x0002, 0xFFFE, 0x7FFF, 0x8001, 0x8000, 0x00FF, 0x0100, 0xFF00, 0xFF01,
         0x5555, 0xAAAA, 0x1234, 0xEDCC)
SWEEP_FIXED = (0x8000, 0x7FFF, 0xFFFF, 0x0001, 0x0000, 0x1234)
FLAG_BITS = (0x01, 0x02, 0x04, 0x08, 0x40, 0x80)  # C Z I D V N; B (0x10) and T (0x20) are 0 on input
CALLER_SPS = (0x0E, 0x40, 0xFF)  # caller SP before the pushes; entry SP = caller SP - 2 (JSR return) - 2 (php+phy)
RANDOM_PAIRS = 3000  # per default run; the host model checks 20,000 more
RANDOM_SEED = 494
# TRAMPOLINE is computed dynamically from the OVERRIDES segment end (see find_trampoline_address).
# It must not be hardcoded: adding/removing override sources changes the ROM layout, and a fixed
# address like $E700 can land inside another override's code.
SIGNED_BOUNDARY = ((-32768, -32768), (-32768, 32767), (32767, -32768), (32767, 32767), (-1, -1), (-1, 1), (1, -1),
                   (-1, 32767), (32767, -1), (-32768, -1), (-1, -32768), (-32768, 1), (1, -32768), (0, 0),
                   (0, -32768), (-32768, 0), (1, 1), (0x00FF, 0x0101), (0x0101, 0x00FF), (255, 256), (0x7FFF, 2))
# Trampoline payload codes used by the T-mutants. Each is entered at the trampoline address and ends with RTS ($60).
# The longest payload determines how many consecutive unused-ROM bytes must be verified.
TRAMPOLINE_PAYLOADS = (
    bytes.fromhex("08A50085002860"),   # T1: same-value zero-page write          (7 bytes)
    bytes.fromhex("08EE0023CE00232860"), # T2: write-and-restore BSS             (9 bytes)
    bytes.fromhex("08AD03142860"),     # T3: I/O read                            (6 bytes)
    bytes.fromhex("08AD00218D00212860"), # T4: same-value stack below frame      (9 bytes)
    bytes.fromhex("08A9F853042860"),   # T5: MPR change                          (8 bytes)
    bytes.fromhex("08AD34218D34212860"), # T6: same-value below lowest push      (9 bytes)
    bytes.fromhex("08AD3C218D3C212860"), # T7: same-value JSR return byte        (9 bytes)
    bytes.fromhex("08AD41218D41212860"), # T8: same-value above frame            (9 bytes)
    bytes.fromhex("08AD3B218D3B212860"), # T9: same-value JSR return low byte    (9 bytes)
    bytes.fromhex("08AD00222860"),     # T10: BSS read                           (6 bytes)
)
MAX_TRAMPOLINE_PAYLOAD = max(len(p) for p in TRAMPOLINE_PAYLOADS)  # 9 bytes


def parse_dev_map_segments(dev_map_text):
    """Parse the ld65 dev.map Segment list.

    Returns {name: {"start": int, "end": int, "size": int}}. The map's End column is the
    inclusive last byte address of the segment (Size == End - Start + 1), so the first
    unused byte after a segment is End + 1.
    """
    segments = {}
    for line in dev_map_text.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        hex_fields = parts[1:5]
        if not all(f and all(c in "0123456789ABCDEFabcdef" for c in f) for f in hex_fields):
            continue
        try:
            start, end, size, _align = (int(f, 16) for f in hex_fields)
        except ValueError:
            continue
        if size != end - start + 1:
            continue  # not a genuine segment row (defensive: ld65 maps Size = End - Start + 1)
        segments[parts[0]] = {"start": start, "end": end, "size": size}
    return segments


def check_trampoline_region(segments, addr, payload_len, rom_data=None):
    """Common safety validation shared by find_trampoline_address() and
    validate_trampoline_address(). Raises ValueError if the region is unsafe."""
    end_addr = addr + payload_len - 1  # inclusive last byte of the trampoline payload

    # $F000 boundary: the whole payload region (start AND end) must stay below $F000.
    if addr >= 0xF000 or end_addr >= 0xF000:
        raise ValueError(
            f"trampoline region ${addr:04X}-${end_addr:04X} crosses the $F000 boundary")

    # Never overlap any defined segment (OVERRIDES, BOOT, APITABLE, FALLBACK, VECTORS, ...).
    for name, seg in segments.items():
        if seg["start"] <= end_addr and addr <= seg["end"]:
            raise ValueError(
                f"trampoline ${addr:04X}-${end_addr:04X} overlaps {name} segment "
                f"${seg['start']:04X}-${seg['end']:04X}")

    # The entire payload region must be unused ROM ($FF).
    if rom_data is not None:
        for i in range(payload_len):
            rom_addr = addr + i - 0xE000
            if rom_addr < 0 or rom_addr >= len(rom_data) or rom_data[rom_addr] != 0xFF:
                raise ValueError(f"trampoline byte at ${addr+i:04X} is not unused ROM ($FF)")

    return addr


def find_trampoline_address(rom_dir, rom_path=None, payload_len=MAX_TRAMPOLINE_PAYLOAD):
    """Return a safe trampoline address right after the OVERRIDES segment end.

    The trampoline payload (up to `payload_len` bytes; the T-mutants need up to 9) is placed
    immediately after the OVERRIDES segment end so it never overlaps override code no matter how
    the ROM layout changes.

    Safety checks (the common check_trampoline_region):
    - OVERRIDES segment end must be found in dev.map (End is inclusive: first free byte = End + 1).
    - Address rounded up to a 2-byte boundary for JMP absolute targeting.
    - Region start AND end must stay below the $F000 boundary.
    - Region must not overlap OVERRIDES, BOOT or any other defined segment.
    - Every payload byte must be unused ROM ($FF); a missing/unreadable ROM or bad map fails safe.
    """
    text = (rom_dir / "dev.map").read_text()  # unreadable map -> fail safe
    segments = parse_dev_map_segments(text)
    if "OVERRIDES" not in segments:
        raise ValueError("OVERRIDES segment not found in dev.map")

    ov = segments["OVERRIDES"]
    # End is inclusive (last byte address): first free byte is End + 1.
    addr = ov["end"] + 1
    addr = (addr + 1) & ~1  # round up to next even address

    if rom_path is not None:
        if not rom_path.exists():
            raise ValueError(f"ROM not found: {rom_path}")
        rom_data = rom_path.read_bytes()  # unreadable ROM -> fail safe
    else:
        rom_data = None

    return check_trampoline_region(segments, addr, payload_len, rom_data)


def validate_trampoline_address(dev_map_text, rom_data=None, payload_len=MAX_TRAMPOLINE_PAYLOAD):
    """Standalone validation used by negative test cases.

    Reconstructs the trampoline address from dev.map and runs the same common safety
    check as find_trampoline_address (no duplicated logic).
    """
    segments = parse_dev_map_segments(dev_map_text)
    if "OVERRIDES" not in segments:
        raise ValueError("OVERRIDES segment not found in dev.map")
    ov = segments["OVERRIDES"]
    addr = (ov["end"] + 1 + 1) & ~1  # End inclusive -> first free byte, rounded up
    return check_trampoline_region(segments, addr, payload_len, rom_data)


def guest_source(kind, sp, entry, sweep=None):
    """ca65 source of the guest. kind 'table': operands/Y/P come from the table at TABLE. kind 'sweep': `sweep` =
    (fixed value, 'multiplier'|'multiplicand'); the other operand is a 16-bit counter 0..65535, Y = counter lo ^ $A5 and
    P = (counter lo & $CB) | $04 (N V D Z C vary, I=1, T=0, B=0). The guest never multiplies by itself."""
    head = f"""\
.setcpu "huc6280"
.segment "CODE"
ptr = $00
cnt = $02
ytmp = $04
RES = ${RES:04X}
.export start, rec_point, halt
start:
    sei
    lda #$F8
    tam #$02                 ; MPR1 = work RAM $2000-$3FFF
    ldx #${(sp + 1) & 0xFF:02X}
    beq @nosent
@sent:
    lda #${SENTINEL:02X}
    sta $2100,x              ; caller stack data above the caller SP
    inx
    bne @sent
@nosent:
"""
    if kind == "table":
        head += f"""\
    lda ${TABLE:04X}
    sta cnt
    lda ${TABLE + 1:04X}
    sta cnt+1
    lda #<${TABLE + 2:04X}
    sta ptr
    lda #>${TABLE + 2:04X}
    sta ptr+1
"""
        push = """\
    lda (ptr),y
    pha
    iny
    lda (ptr),y
    pha
    iny
    lda (ptr),y
    pha
    iny
    lda (ptr),y
    pha
    iny
    lda (ptr),y
    sta ytmp
    iny
    lda (ptr),y
    pha                      ; caller P
    eor #$FF                 ; A != P at the JSR, so a routine that pushes A instead of P is caught
"""
        tail = """\
    clc
    lda ptr
    adc #6
    sta ptr
    bcc @c1
    inc ptr+1
@c1:
    lda cnt
    bne @c2
    dec cnt+1
@c2:
    dec cnt
    lda cnt
    ora cnt+1
    bne nextcall
"""
    else:
        fixed, role = sweep
        head += "    stz cnt\n    stz cnt+1\n"
        fixed_push = f"    lda #${fixed & 0xFF:02X}\n    pha\n    lda #${fixed >> 8:02X}\n    pha\n"
        count_push = "    lda cnt\n    pha\n    lda cnt+1\n    pha\n"
        push = (fixed_push + count_push) if role == "multiplier" else (count_push + fixed_push)  # multiplicand first
        push += """\
    lda cnt
    eor #$A5
    sta ytmp                 ; Y = counter lo ^ $A5
    lda cnt
    and #$CB
    ora #$04
    pha                      ; caller P
    eor #$FF                 ; A != P at the JSR, so a routine that pushes A instead of P is caught
"""
        tail = """\
    inc cnt
    bne nextcall
    inc cnt+1
    bne nextcall
"""
    call = f"""\
nextcall:
    ldx #${sp:02X}
    txs                      ; caller SP before the 4 pushes
    ldy #0
{push}    ldy ytmp
    plp                      ; caller P (T=0, B=0)
    jsr ${entry:04X}
    php
    pla
    sta RES+4                ; P after the call
    sty RES+5                ; Y after the call
    tsx
    stx RES+6                ; S just after the call
    pla
    sta RES+3
    pla
    sta RES+2
    pla
    sta RES+1
    pla
    sta RES+0
    tsx
    stx RES+7                ; S after the 4 pops
rec_point:
    cld                      ; the caller P may have had D=1
"""
    return head + call + tail + "halt:\n    bra halt\n"


def assemble_guest(source, reset, folder, name):
    """Assemble + link at CPU address `reset`; returns (code, {label: address})."""
    src, cfg = folder / f"{name}.s", folder / f"{name}.cfg"
    src.write_text(source, encoding="utf-8")
    cfg.write_text(f"MEMORY {{ M: start = ${reset:04X}, size = $0F00, file = %O; }}\n"
                   f"SEGMENTS {{ CODE: load = M, type = ro; }}\n", encoding="utf-8")
    obj, out, lbl = folder / f"{name}.o", folder / f"{name}.bin", folder / f"{name}.lbl"
    for command in ([executable("ca65"), str(src), "-o", str(obj)],
                    [executable("ld65"), "-C", str(cfg), "-o", str(out), "-Ln", str(lbl), str(obj)]):
        done = subprocess.run(command, capture_output=True, text=True)
        if done.returncode:
            raise ValueError(f"guest {Path(command[0]).name} failed: {done.stdout}{done.stderr}")
    labels = {m.group(2): int(m.group(1), 16) for m in re.finditer(r"al ([0-9A-F]+) \.(\w+)", lbl.read_text())}
    return out.read_bytes(), labels


def table_entries(pairs, offset=0):
    """(a, b, y, p) per pair: Y and the six P flags C Z I D V N cycle through all 64 combinations (T=0, B=0)."""
    result = []
    for i, (a, b) in enumerate(pairs, offset):
        flags = 0
        for bit, mask in enumerate(FLAG_BITS):
            if (i >> bit) & 1:
                flags |= mask
        result.append((a & 0xFFFF, b & 0xFFFF, (i * 37 + 0x5A) & 0xFF, flags))
    return result


def table_bytes(entries):
    body = b"".join(bytes([a & 0xFF, a >> 8, b & 0xFF, b >> 8, y, p]) for a, b, y, p in entries)
    return len(entries).to_bytes(2, "little") + body


def sweep_entries(fixed, role):
    """The (a, b, y, p) the sweep guest generates: counter v = 0..65535 in the role's slot."""
    for v in range(0x10000):
        yield ((fixed, v) if role == "multiplier" else (v, fixed), (v & 0xFF) ^ 0xA5, (v & 0xCB) | 0x04)


class Probe:
    """Compile once, run many: a probe run is one core boot plus the whole guest loop."""

    def __init__(self, folder):
        self.folder = folder
        self.exe = api.compile_probe(folder, "geargrafx_mul16s_probe.cpp", "geargrafx-mul16s-probe")
        (folder / "original.bin").write_bytes(bytes(2352 * 32))
        self.cue = folder / "original.cue"
        self.cue.write_text('FILE "original.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
        self.counter = itertools.count(1)

    def run(self, rom_bytes, config):
        index = next(self.counter)
        rom, cfg = self.folder / f"run{index}.pce", self.folder / f"run{index}.cfg"
        rom.write_bytes(rom_bytes)
        cfg.write_text("\n".join(config) + "\n")
        done = subprocess.run([str(self.exe), str(rom), str(self.cue), str(cfg)], capture_output=True, text=True,
                              timeout=3600)
        rom.unlink()
        cfg.unlink()
        if done.returncode not in (0, 9):
            raise ValueError(f"probe exit {done.returncode}: {done.stderr[:300]}")
        out = {"records": [], "viol": [], "mprchg": [], "obs": {}}
        for line in done.stdout.splitlines():
            if line.startswith("REC "):
                out["records"].append(bytes.fromhex(line[4:]))
            elif line.startswith("OBS "):
                row = json.loads(line[4:])
                out["obs"][row["phase"]] = row
            elif line.startswith("VIOL "):
                out["viol"].append(json.loads(line[5:]))
            elif line.startswith("MPRCHG "):
                out["mprchg"].append(json.loads(line[7:]))
            elif line.startswith("SUMMARY "):
                out["summary"] = json.loads(line[8:])
        if "summary" not in out or set(out["obs"]) != {"loaded", "final"}:
            raise ValueError("probe output incomplete")
        return out


def routine_window(data, rom_dir):
    """(body start, end) of the override body: the table JMP target and the size from the ld65 map."""
    table = 3 * SLOT
    if data[table] != 0x4C:
        raise ValueError("slot $4F table entry is not a JMP")
    body = data[table + 1] | data[table + 2] << 8
    text = (rom_dir / "dev.map").read_text()
    sizes = dict(re.findall(r"(slot_\w+)\.o:\s+OVERRIDES\s+Offs=\w+\s+Size=(\w+)", text))
    size = int(sizes["slot_4F"], 16)
    if data[body - 0xE000 + size - 1] != 0x60:
        raise ValueError("slot $4F body does not end with RTS")
    return body, body + size


def trace_config(c, window, extra=0):
    """Window + breakpoints for caller SP c. Routine-allowed writes: its six pushes c-11..c-6 and the frame c-3..c.
    Allowed reads: c-11..c (its own bytes and the JSR return it pops)."""
    lo, hi = window
    base = 0x2100
    lines = [f"window 0x{lo:04X} 0x{hi + extra:04X}"]
    w = [(0x0000, 0x20FF), (0x2200, 0xFFFF)]
    r = [(0x0000, 0x20FF), (0x2200, 0xDFFF)]
    if c - 12 >= 0:
        w.append((base, base + c - 12))
        r.append((base, base + c - 12))
    w.append((base + c - 5, base + c - 4))  # the JSR return address belongs to the caller
    if c < 0xFF:
        w.append((base + c + 1, 0x21FF))
        r.append((base + c + 1, 0x21FF))
    lines += [f"wbp 0x{a:04X} 0x{b:04X}" for a, b in w] + [f"rbp 0x{a:04X} 0x{b:04X}" for a, b in r]
    return lines


def check(label, run, expected_rows, c, halt_pc, errors_by_kind=None):
    """Compare one probe run with the contract. Returns {kind: [message]} (empty dict = PASS).
    expected_rows: list of (a, b, y, p). Kinds: halt, count, product, P, Y, S, mpr, ram, trace."""
    errs = {}

    def add(kind, text):
        errs.setdefault(kind, []).append(f"{label}: {text}")

    s, final, loaded = run["summary"], run["obs"]["final"], run["obs"]["loaded"]
    if not s["ok"] or final["pc"] != halt_pc:
        add("halt", f"guest did not reach its halt loop (pc ${final['pc']:04X}, steps {s['steps']})")
    recs = run["records"]
    if len(recs) != len(expected_rows) and s["ok"]:
        add("count", f"{len(recs)} records, expected {len(expected_rows)}")
    for (a, b, y, p), rec in zip(expected_rows, recs):
        got, p_after, y_after, s_now, s_end = int.from_bytes(rec[0:4], "little"), rec[4], rec[5], rec[6], rec[7]
        want = model.expected(a, b)
        if got != want:
            add("product", f"a={a:04x} b={b:04x} got {got:08x} want {want:08x}")
        if (p_after ^ p) & 0xEF:  # B (bit 4) ignored, T (bit 5) must be 0 like the input
            add("P", f"a={a:04x} b={b:04x} P {p:02x}->{p_after:02x}")
        if y_after != y:
            add("Y", f"a={a:04x} b={b:04x} Y {y:02x}->{y_after:02x}")
        if s_now != (c - 4) & 0xFF or s_end != c:
            add("S", f"a={a:04x} b={b:04x} S after call ${s_now:02x} (want ${(c - 4) & 0xFF:02x}), after pops ${s_end:02x}")
    for kind, key in (("trace", "viol"), ("mpr", "mprchg")):
        if run[key]:
            add(kind, f"{len(run[key])}+ {key} events, first {run[key][0]}")
    if s["violations"] and not run["viol"]:
        add("trace", f"{s['violations']} violations")
    if s["mpr_changes"]:
        add("mpr", f"{s['mpr_changes']} MPR changes during the run")
    for index in (0, 2, 3, 4, 5, 6, 7):  # MPR1 is set by the guest (absolute $F8 below)
        if loaded["mpr"][index] != final["mpr"][index] and "mpr" not in errs:
            add("mpr", f"MPR{index} ${loaded['mpr'][index]:02X} -> ${final['mpr'][index]:02X}")
    if final["mpr"][1] != 0xF8 or final["mpr"][7] != 0:
        add("mpr", f"MPR1/MPR7 not absolute ({final['mpr']})")
    pre, post = bytes.fromhex(loaded["work_ram_hex"]), bytes.fromhex(final["work_ram_hex"])
    stack = range(0x100 + c - 11, 0x100 + c + 1)
    above = range(0x100 + c + 1, 0x200)
    allowed = set(GUEST_ZP) | set(range(RES - 0x2000, RES - 0x2000 + RES_LEN)) | set(stack) | set(above)
    changed = [i for i in range(0x2000) if pre[i] != post[i]]
    outside = [i for i in changed if i not in allowed]
    if outside:
        add("ram", f"work RAM changed outside the expected set: {', '.join(f'${0x2000 + i:04X}' for i in outside[:8])}"
                   f" ({len(outside)} total)")
    lost = [i for i in above if post[i] != SENTINEL]
    if lost:
        add("ram", f"caller stack data above SP overwritten at ${0x2000 + lost[0]:04X} (value ${post[lost[0]]:02X})")
    return errs


class Rig:
    """Built ROM + probe + helper to run a table or sweep guest with the full trace."""

    def __init__(self, data, rom_dir, folder):
        self.data = data
        self.reset = int.from_bytes(data[0x1FFE:0x2000], "little")
        self.entry = 0xE000 + 3 * SLOT
        self.window = routine_window(data, rom_dir)
        self.folder = folder
        self.probe = Probe(folder)
        self.n = itertools.count(1)

    def variant(self, code, patches=()):
        rom = bytearray(self.data)
        for offset, value in patches:
            rom[offset:offset + len(value)] = value
        rom[self.reset - 0xE000:self.reset - 0xE000 + len(code)] = code
        return bytes(rom)

    def run_table(self, entries, c, patches=(), extra_window=0, trace=True, max_per_call=STEPS_PER_CALL):
        if len(entries) > MAX_ENTRIES:
            raise ValueError("too many entries for one run")
        n = next(self.n)
        code, labels = assemble_guest(guest_source("table", c, self.entry), self.reset, self.folder, f"g{n}")
        cfg = [f"halt 0x{labels['halt']:04X}", f"max {max(len(entries), 1) * max_per_call + 5000}",
               f"rec 0x{labels['rec_point']:04X} 0x{RES:04X} {RES_LEN}", f"poke 0x{TABLE:04X} {table_bytes(entries).hex()}"]
        cfg += trace_config(c, self.window, extra_window) if trace else [f"window 0x{self.window[0]:04X} 0x{self.window[1]:04X}"]
        run = self.probe.run(self.variant(code, patches), cfg)
        return run, labels["halt"]

    def run_sweep(self, fixed, role, c=0xFF, trace=False):
        n = next(self.n)
        code, labels = assemble_guest(guest_source("sweep", c, self.entry, (fixed, role)), self.reset, self.folder,
                                      f"s{n}")
        cfg = [f"halt 0x{labels['halt']:04X}", f"max {65536 * STEPS_PER_CALL + 5000}",
               f"rec 0x{labels['rec_point']:04X} 0x{RES:04X} {RES_LEN}"]
        cfg += trace_config(c, self.window) if trace else [f"window 0x{self.window[0]:04X} 0x{self.window[1]:04X}"]
        run = self.probe.run(self.variant(code), cfg)
        return run, labels["halt"]


def build_default_tables():
    """name -> list of (a, b): boundaries, signed combinations, all EDGES x EDGES, commuted pairs, seeded random."""
    rng = random.Random(RANDOM_SEED)
    boundary = [(a & 0xFFFF, b & 0xFFFF) for a, b in SIGNED_BOUNDARY]
    boundary += [(b, a) for a, b in boundary]  # commutativity
    signs = [(sa * x, sb * y) for sa in (1, -1) for sb in (1, -1) for x in (0, 1, 2, 0x7FFF, 255, 256)
             for y in (1, 3, 0x7FFF, 128) if -32768 <= sa * x <= 32767 and -32768 <= sb * y <= 32767]
    boundary += [(a & 0xFFFF, b & 0xFFFF) for a, b in signs]
    edges = [(a, b) for a in EDGES for b in EDGES]
    random_pairs = [(rng.randrange(65536), rng.randrange(65536)) for _ in range(RANDOM_PAIRS)]
    return {"boundary": boundary, "edges": edges, "random": random_pairs}


def chunks(items, size=MAX_ENTRIES):
    for i in range(0, len(items), size):
        yield i, items[i:i + size]


def find_unique(data, pattern, window, name):
    lo, hi = window[0] - 0xE000, window[1] - 0xE000
    hits = [i for i in range(lo, hi - len(pattern) + 1) if data[i:i + len(pattern)] == pattern]
    if len(hits) != 1:
        raise ValueError(f"{name}: pattern {pattern.hex()} found {len(hits)} times in the slot $4F body, want 1")
    return hits[0]


def mutants(data, window, trampoline_addr):
    """name -> (patches [(file offset, bytes)], required error kinds, window extra, c). One edit each; the length and
    stack effect are kept except M5 (a lost PLA), which must be caught as a failed return."""
    f = lambda pat, name: find_unique(data, bytes.fromhex(pat), window, name)
    nop_block = lambda head, name: (lambda at: [(at + 5, b"\xEA" * data[at + 4])])(f(head, name))  # BPL n -> n NOPs
    # Place the JMP to the trampoline inside the routine body (just past its entry) so the
    # trampoline (right after the OVERRIDES segment end) does not overlap the JMP's 3 bytes, and
    # so the trampoline's RTS returns to the instruction after the JMP and the body can still
    # reach its own RTS for a clean halt. body_end here is the JMP write offset inside the body.
    body_end = window[0] - 0xE000 + 3
    # The trampoline sits right after the OVERRIDES segment end, rounded up to an even address
    # (find_trampoline_address). For the last override it equals window[1] (the first byte past the
    # body); when that byte is odd it is window[1] + 1, the single alignment pad byte. The pad is
    # never written or executed (the payload region is checked by check_trampoline_region), and the
    # trampoline lies inside the extended trace window (window[1] + 0x100). Any larger gap is rejected.
    if not window[0] <= trampoline_addr <= window[1] + 1:
        raise ValueError("trampoline is outside the routine's PC window")
    jmp = lambda code: [(body_end, bytes([0x4C, trampoline_addr & 0xFF, trampoline_addr >> 8])), (trampoline_addr - 0xE000, code)]
    return {
        "M1_multiplicand_correction_NOP": (nop_block("BD0B2110", "M1"), {"product"}, 0, 0xFF),
        "M2_multiplier_correction_NOP": (nop_block("BD042110", "M2"), {"product"}, 0, 0xFF),
        "M3_product_top_byte_not_stored": ([(f("9D0921", "M3"), b"\xEA\xEA\xEA")], {"product"}, 0, 0xFF),
        "M4_Y_not_restored": ([(f("7A2860", "M4"), b"\x68")], {"Y"}, 0, 0xFF),
        "M5_stack_restore_broken": ([(f("6868686 87A".replace(" ", ""), "M5"), b"\xEA")], {"halt", "S"}, 0, 0xFF),
        "M6_PLP_as_PLA": ([(f("2860", "M6"), b"\x68")], {"P"}, 0, 0xFF),
        "M7_PHP_as_PHA": ([(window[0] - 0xE000, b"\x48")], {"P"}, 0, 0xFF),
        "M8_CLD_as_NOP": ([(window[0] - 0xE000 + 1, b"\xEA")], {"product"}, 0, 0xFF),
        "M9_correction_SBC_as_ADC": ([(f("FD0321", "M9"), b"\x7D")], {"product"}, 0, 0xFF),
        "M10_ROR_as_ROL": ([(f("7E0A21", "M10"), b"\x3E")], {"product"}, 0, 0xFF),
        # Value-neutral side effects the final RAM diff cannot see: the product, Y, P (PHP/PLP around the probe) and S stay correct.
        "T1_same_value_zero_page_write": (jmp(TRAMPOLINE_PAYLOADS[0]), {"trace"}, 0x100, 0x40),
        "T2_write_and_restore_bss": (jmp(TRAMPOLINE_PAYLOADS[1]), {"trace"}, 0x100, 0x40),
        "T3_io_read": (jmp(TRAMPOLINE_PAYLOADS[2]), {"trace"}, 0x100, 0x40),
        "T4_same_value_stack_below_frame": (jmp(TRAMPOLINE_PAYLOADS[3]), {"trace"}, 0x100, 0x40),
        # c = $40: $2134 is the byte just below the routine's lowest push, $213C a JSR return byte, $2141 the first byte above the frame.
        "T6_same_value_below_lowest_push": (jmp(TRAMPOLINE_PAYLOADS[5]), {"trace"}, 0x100, 0x40),
        "T7_same_value_jsr_return_byte": (jmp(TRAMPOLINE_PAYLOADS[6]), {"trace"}, 0x100, 0x40),
        "T8_same_value_above_frame": (jmp(TRAMPOLINE_PAYLOADS[7]), {"trace"}, 0x100, 0x40),
        "T9_same_value_jsr_return_low_byte": (jmp(TRAMPOLINE_PAYLOADS[8]), {"trace"}, 0x100, 0x40),
        "T10_bss_read": (jmp(TRAMPOLINE_PAYLOADS[9]), {"trace"}, 0x100, 0x40),
        "T5_mpr_change": (jmp(TRAMPOLINE_PAYLOADS[4]), {"mpr"}, 0x100, 0x40),
    }


def flag_matrix():
    """Four pairs x all 64 C/Z/I/D/V/N combinations x 4 Y values (P and Y preservation for every flag state)."""
    pairs = [(0x8000, 0x8000), (0x1234, 0xEDCC), (0, 0), (0xFFFF, 0x7FFF)]
    entries = []
    for a, b in pairs:
        for combo in range(64):
            flags = sum(m for bit, m in enumerate(FLAG_BITS) if (combo >> bit) & 1)
            entries += [(a, b, y, flags) for y in (0x00, 0xFF, 0x5A, 0xA5)]
    return entries


def jobs_for_default():
    tables = build_default_tables()
    jobs = []
    for c in CALLER_SPS:
        for name, pairs in tables.items():
            for start, part in chunks(pairs):
                jobs.append((f"{name}-sp{c:02x}-{start}", table_entries(part, start), c))
        for start, part in chunks(flag_matrix()):
            jobs.append((f"flags-sp{c:02x}-{start}", part, c))
    return jobs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rom", type=Path, required=True)
    parser.add_argument("--full", action="store_true", help="also run the 786,432-call sweeps (manual)")
    parser.add_argument("--jobs", type=int, default=min(8, os.cpu_count() or 1))
    args = parser.parse_args()
    data = args.rom.read_bytes()
    verify_dev_rom(data, manifest=args.rom.parent / "manifest.json")
    started = time.time()
    evidence = {"scope": "DEV ROM real-core run of project-own MA_MUL16S, NOT System Card ABI compatibility",
                "rom_sha256": hashlib.sha256(data).hexdigest(), "caller_sps": list(CALLER_SPS),
                "runs": [], "mutants": [], "sweeps": []}
    with tempfile.TemporaryDirectory(prefix="pce-dev-mul16s-") as tmp:
        rig = Rig(data, args.rom.parent, Path(tmp))
        evidence["window"] = [f"${rig.window[0]:04X}", f"${rig.window[1]:04X}"]
        trampoline_addr = find_trampoline_address(args.rom.parent, args.rom, payload_len=MAX_TRAMPOLINE_PAYLOAD)
        evidence["trampoline"] = f"${trampoline_addr:04X}"
        jobs = jobs_for_default()
        calls = 0

        def do(job):
            label, entries, c = job
            run, halt = rig.run_table(entries, c)
            return job, run, check(label, run, entries, c, halt)

        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            for (label, entries, c), run, errs in pool.map(do, jobs):
                if errs:
                    raise ValueError(f"{label}: FAIL {sorted(errs)}: {next(iter(errs.values()))[0]}")
                calls += len(entries)
                evidence["runs"].append({"label": label, "calls": len(entries), "steps": run["summary"]["steps"],
                                         "routine_steps": run["summary"]["routine_steps"],
                                         "violations": run["summary"]["violations"],
                                         "mpr_changes": run["summary"]["mpr_changes"]})
        evidence["calls"] = calls
        print(f"PASS {calls} calls in {len(jobs)} runs (caller SP {', '.join(f'${c:02X}' for c in CALLER_SPS)}), "
              f"write/read trace clean, {time.time() - started:.0f}s", flush=True)
        # Mutants: reduced table (boundaries + part of EDGES + part of the flag matrix), one patched ROM each.
        tables = build_default_tables()
        reduced = table_entries(tables["boundary"] + tables["edges"][:100], 0) + flag_matrix()[:128]
        control, control_halt = rig.run_table(reduced, 0x40)
        if check("control", control, reduced, 0x40, control_halt):
            raise ValueError("unmutated control run failed")
        plan = mutants(data, rig.window, trampoline_addr)

        def do_mutant(item):
            name, (patches, required, extra, c) = item
            run, halt = rig.run_table(reduced, c, patches=patches, extra_window=extra)
            return name, required, run, check(name, run, reduced, c, halt)

        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            for name, required, run, errs in pool.map(do_mutant, plan.items()):
                if not errs or not (set(errs) & required):
                    raise ValueError(f"mutant {name} was NOT rejected as {sorted(required)} (seen: {sorted(errs)})")
                if name.startswith("T") and name != "T5_mpr_change":
                    # A trace mutant is detected by the write/read trace; "halt" may accompany it
                    # because the probe stops at the first trace violation before the guest reaches
                    # its halt loop. Any other error kind (product/Y/P/S/mpr/ram) means the mutant
                    # changed visible state beyond the trace, which is not value-neutral.
                    trace_only = set(errs) - {"halt"}
                    if trace_only != {"trace"}:
                        raise ValueError(f"{name}: a value-neutral side effect must be seen only by the trace, got {sorted(errs)}")
                evidence["mutants"].append({"mutant": name, "kinds": sorted(errs), "required": sorted(required),
                                            "first": next(iter(errs.values()))[0]})
        print(f"PASS mutants rejected {len(evidence['mutants'])}/{len(plan)}: "
              + ", ".join(m["mutant"].split("_")[0] for m in evidence["mutants"]), flush=True)
        if args.full:
            total = 0

            def do_sweep(item):
                fixed, role, c = item
                run, halt = rig.run_sweep(fixed, role, c, trace=True)
                rows = [(pair[0], pair[1], y, p) for pair, y, p in sweep_entries(fixed, role)]
                return item, run, check(f"sweep-{role}-{fixed:04x}-sp{c:02x}", run, rows, c, halt)

            with ThreadPoolExecutor(max_workers=args.jobs) as pool:
                for (fixed, role, c), run, errs in pool.map(do_sweep, [(f, r, c) for c in (0xFF, 0x0E)
                                                                       for r in ("multiplier", "multiplicand")
                                                                       for f in SWEEP_FIXED]):
                    if errs:
                        raise ValueError(f"sweep {role} {fixed:04x} sp {c:02x}: FAIL {sorted(errs)}: "
                                         f"{next(iter(errs.values()))[0]}")
                    total += len(run["records"])
                    evidence["sweeps"].append({"fixed": fixed, "role": role, "caller_sp": c,
                                               "calls": len(run["records"]), "steps": run["summary"]["steps"],
                                               "violations": run["summary"]["violations"]})
                    print(f"PASS sweep {role} (fixed ${fixed:04X}, caller SP ${c:02X}): {len(run['records'])} calls, "
                          f"trace clean", flush=True)
            print(f"PASS full sweep: {total} calls, {time.time() - started:.0f}s")
    name = "geargrafx-dev-mul16s-full-evidence.json" if args.full else "geargrafx-dev-mul16s-evidence.json"
    (args.rom.parent / name).write_text(json.dumps(evidence, indent=2) + "\n")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        status = "BLOCKED" if "BLOCKED" in str(error) or "missing tool" in str(error) else "FAIL"
        print(f"DEV MUL16S integration {status}: {error}", file=sys.stderr)
        raise SystemExit(BLOCKED_EXIT if status == "BLOCKED" else 1) from error
