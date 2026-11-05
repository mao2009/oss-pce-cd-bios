#!/usr/bin/env python3
"""CI-3 of the C-3 static checks: IRQ / MMIO contract. See c3_static_check.py.

Fail closed. Per bios file the contract may declare:
  mmio      {"addresses": ["0x1800", ...], "device": text, "mapping": "window" | "precondition",
             "entry": label (precondition), "callers": [files] (precondition)}
            Every page-0 ($0000-$1FFF, MPR0) memory operand must use a declared address, without indexing, and every
            declared address must be used. A memory operand whose address cannot be resolved and is not a label of a
            known segment of the file is a FAIL.
            "window": the file maps MPR0 to the hardware page itself. Each window is LDA #$FF / TAM #$01 ... TAM #$01
            (the CI-1 "restore"), in one straight-line run of one routine: no label, branch, jump, call, return,
            CLI, PLP or RTI inside it, and an SEI earlier in the routine with no CLI / PLP after it. Every page-0
            access must lie inside a window. A window file needs at least one window and one access.
            "precondition": the routine documents that its caller maps MPR0 = $FF. Every JSR / JMP / BSR in a bios
            file to any label exported by the routine's file is found by the tool; the calling file must be listed
            in "callers", and the closest TAM #$01 before the call in that file must be an LDA #$FF / TAM #$01
            (not a restore), with no other TAM #$01 in between. Callers outside the bios scope are NOT_STATIC.
  indirect  {"count": n, "reason": text} -- indirect (pointer) operands; run-time addresses: NOT_STATIC.
  st        [{"op": "st0" | "st1" | "st2", "purpose"}] -- VDC register writes independent of MPRs.
  vectors   {"irq_handlers": [labels], "reset": label} for a file with a VECTORS segment: exactly five .word entries
            (IRQ2/BRK, IRQ1, Timer, NMI, Reset); nothing else in the segment. A handler body runs from its label to
            the next non-local label; it must end in an unconditional BRA / JMP to a local label or itself, or RTI
            (no fall-through), and contain no TAM / TMA / ST0-2 / block transfer / JSR / JMP / BSR to another
            routine / page-0 access / indirect operand.
Any TAM that maps $FF through an MPR other than MPR0 is a FAIL here. Absolute accesses to pages 2-6 ($4000-$DFFF),
whose mapping a bios routine does not fix, are NOT_STATIC. Device-originated DMA, IRQ timing and real hardware are
always NOT_STATIC.
"""
import re

import c3_asm
from c3_static_check import BLOCKED, FAIL, NOT_STATIC, OK, where

MEMORY_OPS = {"lda", "ldx", "ldy", "sta", "stx", "sty", "stz", "adc", "sbc", "and", "ora", "eor", "cmp", "cpx",
              "cpy", "bit", "inc", "dec", "asl", "lsr", "rol", "ror", "tsb", "trb", "tst"} | c3_asm.ZP_FIRST_OPS
ST_OPS = {"st0", "st1", "st2"}
TRANSFERS = {"tii", "tin", "tdd", "tia", "tai"}
BRANCHES = {"bcc", "bcs", "beq", "bmi", "bne", "bpl", "bvc", "bvs", "bra", "bsr"} | \
    {f"{op}{i}" for op in ("bbr", "bbs") for i in range(8)}
CALLS = {"jsr", "jmp", "bsr"}
WINDOW_BREAKERS = BRANCHES | CALLS | {"rts", "rti", "cli", "plp", "brk"}


def _op(source, ln):
    return c3_asm.operand(ln.operand, source.symbols, ln.mnemonic)


def _target(ln):
    """Label operand of a branch / jump / call (BBRn / BBSn: the label after the comma)."""
    text = ln.operand
    if ln.mnemonic[:3] in ("bbr", "bbs") and "," in text:
        text = text.split(",", 1)[1]
    return text.strip()


def _touches_mpr0(source, ln):
    """A TAM whose mask includes MPR0 (an unresolved mask counts as touching it)."""
    value = _op(source, ln).value
    return ln.mnemonic == "tam" and (value is None or value & 0x01)


def _routine_of(source, line, transparent=frozenset()):
    """(instructions, start, index): the routine containing `line` starts after the closest preceding line
    (label-only lines included) that carries a non-local label not listed in `transparent`."""
    ins = source.instructions()
    idx = ins.index(line)
    boundary = 0
    for ln in source.lines:
        if ln.number > line.number:
            break
        if any(not lab.startswith("@") and lab not in transparent for lab in ln.labels):
            boundary = ln.number
    start = next(k for k in range(idx + 1) if ins[k].number >= boundary)
    return ins, start, idx


def _labels_between(source, a, b):
    return [ln for ln in source.lines if a < ln.number <= b and ln.labels]


def _is_set_ff(source, ins, i):
    ln = ins[i]
    return (ln.mnemonic == "tam" and _op(source, ln).value == 0x01 and i > 0 and ins[i - 1].mnemonic == "lda"
            and _op(source, ins[i - 1]).value == 0xFF and _op(source, ins[i - 1]).mode == "immediate"
            and not _labels_between(source, ins[i - 1].number, ln.number))


def _straight(source, ins, a, b):
    """True when instructions a..b (indices) are one straight-line run: no label after a, no branch / jump /
    call / return strictly between them."""
    if _labels_between(source, ins[a].number, ins[b].number):
        return False
    return not any(x.mnemonic in WINDOW_BREAKERS for x in ins[a + 1:b])


def _base_text(ln):
    """Address expression of a memory operand, without the a: / z: prefix and the index (TST: second operand)."""
    text = ln.operand.split(",", 1)[1] if ln.mnemonic == "tst" and "," in ln.operand else ln.operand
    if ln.mnemonic in c3_asm.ZP_FIRST_OPS:
        text = text.split(",")[0]
    base = text.split(",")[0].strip().removeprefix("a:").removeprefix("z:")
    return base.strip("() ")


def _memory_accesses(source, report, path):
    """(page0 [(line, addr)], indirect [line], high [line], build_dependent [line]) of the file; operands that cannot
    be resolved and are neither a label of the file nor a build-dependent constant are FAIL."""
    page0, indirect, high, build_dependent = [], [], [], []
    for ln in source.instructions():
        if ln.mnemonic not in MEMORY_OPS:
            continue
        op = _op(source, ln)
        if op.mode != "memory":
            continue
        if op.indirect:
            indirect.append(ln)
            continue
        addr = op.logical_address()
        names = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", _base_text(ln)))
        if addr is None and names and names <= (source.conditional_constants | set(source.symbols)) and \
                names & source.conditional_constants:   # every name build-dependent or resolved (B1)
            build_dependent.append(ln)          # address set by a build option (e.g. -D): undecidable, not a FAIL
            continue
        if addr is None:
            seg = source.labels.get(op.symbol)
            base = ln.operand.split(",")[0].strip() if ln.mnemonic != "tst" else \
                ln.operand.split(",")[1].strip()
            base = base.removeprefix("a:").removeprefix("z:").strip()
            simple = re.match(rf"^{re.escape(op.symbol)}(\s*\+\s*(\$[0-9A-Fa-f]{{1,2}}|[0-9]{{1,3}}))?$", base) \
                if op.symbol else None
            if seg is not None and not simple:
                report.add("CI-3.02", FAIL, where(ln), f"label expression {base!r} is not a bare label or a label "
                           "plus a small offset: fail closed")
                continue
            absolute_zp = seg is not None and seg.upper() == "ZEROPAGE" and (
                op.forced == "a" or (op.index_reg == "y" and ln.mnemonic not in c3_asm.ZP_Y_OPS))
            if absolute_zp:
                report.add("CI-3.02", FAIL, where(ln), f"zero-page label {op.symbol!r} used with absolute addressing "
                           "(a: or ,Y on an opcode without zp,Y): page 0, possibly the hardware page")
                continue
            if seg is None:
                report.add("CI-3.02", FAIL, where(ln), f"memory operand {ln.operand!r} cannot be resolved and is not a "
                           "label of this file: fail closed (imports and expressions are outside the static scope)")
            elif seg.upper() == "ZEROPAGE":
                pass                            # zero page = WRAM page 1 ($2000-$20FF)
            elif seg.upper() in c3_asm.RAM_SEGMENTS:
                high.append(ln)                 # BSS: placed by the linker; page not fixed by the source
            # labels in ROM segments: the ROM bank of this code (MPR7); stores are refused by CI-1.08
            continue
        if addr < 0x2000:
            page0.append((ln, addr))
        elif 0x4000 <= addr < 0xE000:
            high.append(ln)
    return page0, indirect, high, build_dependent


def _windows(source, report):
    """[(set_index, restore_index)] of the file; FAIL findings for broken windows."""
    ins = source.instructions()
    out = []
    for i in range(len(ins)):
        if not _is_set_ff(source, ins, i):
            continue
        _, start, _ = _routine_of(source, ins[i])
        seis = [k for k in range(start, i) if ins[k].mnemonic == "sei"]
        if ins[i].conditional or ins[i - 1].conditional or (seis and ins[seis[-1]].conditional):
            report.add("CI-3.02", FAIL, where(ins[i]), "MPR0 window set-up or its SEI is inside a conditional "
                       "(.if / .ifdef) block: not every build masks IRQs / maps MPR0 (unsupported)")
            continue
        if not seis or not _straight(source, ins, seis[-1], i) or any(
                x.mnemonic in ("cli", "plp", "rti") for x in ins[seis[-1]:i]):
            report.add("CI-3.02", FAIL, where(ins[i]), "MPR0 -> hardware page without an SEI on the same straight-line "
                       "path of the routine (or with CLI / PLP after it)")
            continue
        j = next((k for k in range(i + 1, len(ins)) if ins[k].mnemonic == "tam" and
                  _op(source, ins[k]).value == 0x01), None)
        if j is not None and ins[j].conditional:
            report.add("CI-3.02", FAIL, where(ins[j]), "the restoring TAM is inside a conditional block")
            continue
        if j is None:
            report.add("CI-3.02", FAIL, where(ins[i]), "MPR0 hardware-page window is never restored")
            continue
        span = ins[i + 1:j]
        breaker = next((x for x in span if x.mnemonic in WINDOW_BREAKERS or (
            x.mnemonic == "tam" and (_op(source, x).value or 0x01) & 0x01)), None)   # any other TAM touching MPR0
        immediate_close = ins[j - 1].mnemonic == "lda" and _op(source, ins[j - 1]).mode == "immediate"
        if breaker is not None or _labels_between(source, ins[i].number, ins[j].number) or immediate_close:
            where_ = breaker or ins[j]
            report.add("CI-3.02", FAIL, where(where_), "MPR0 window is not one straight-line run up to its restoring TAM "
                       "(label, branch, call, return, CLI / PLP inside, or the 'restore' maps $FF again)")
            continue
        out.append((i, j))
    return out


def _exports(source):
    names = set()
    for ln in source.lines:
        if ln.directive in (".export", ".exportzp"):
            names |= {n.strip().split("=")[0].strip() for n in ln.operand.split(",") if n.strip()}
    return names


def _check_precondition(path, source, mmio, sources, files, report):
    entry = mmio.get("entry")
    if not entry or entry not in source.labels:
        report.add("CI-3.03", FAIL, path, f"precondition entry {entry!r} is not a label of the file")
    exported = _exports(source) | ({entry} if entry else set())
    declared = set(mmio.get("callers", []))
    found = set()
    for cpath, csrc in sources.items():
        cins = csrc.instructions()
        for k, ln in enumerate(cins):
            if ln.mnemonic not in CALLS | BRANCHES or _target(ln) not in exported or cpath == path:
                continue
            found.add(cpath)
            if cpath not in declared:
                report.add("CI-3.03", FAIL, where(ln), f"undeclared caller of {_target(ln)} (MPR0 = $FF "
                           "precondition not established by the contract)")
                continue
            pass_decl = files.get(cpath, {}).get("mmio_pass_labels", {})
            pass_labels = {lab for lab, reason in pass_decl.items() if reason}   # each needs a stated reason
            _, start, _ = _routine_of(csrc, ln, frozenset(pass_labels))
            tams = [m for m in range(start, k) if _touches_mpr0(csrc, cins[m])]
            # The reader does not track which conditional block a line is in, so any conditional MPR0 set-up or other
            # TAM touching MPR0 on the path to the call is a FAIL, whether or not the call itself is conditional.
            if tams and any(cins[m].conditional for m in tams + [tams[-1] - 1]):
                report.add("CI-3.03", FAIL, where(ln), "an MPR0 set-up or TAM touching MPR0 before this call is inside "
                           "a conditional block (the C-3 reader cannot show that every build calling here maps MPR0)")
                continue
            between = [lab for x in _labels_between(csrc, cins[tams[-1]].number, ln.number) for lab in x.labels] \
                if tams else []
            targeted = {(x.operand.split(",", 1)[1] if x.mnemonic[:3] in ("bbr", "bbs") and "," in x.operand
                         else x.operand).strip() for s in sources.values() for x in s.instructions()
                        if x.mnemonic in BRANCHES | CALLS}   # BBRn / BBSn zp, label: the target follows the comma
            bad_labels = [lab for lab in between if lab not in pass_labels or lab in targeted]
            breaks = tams and any(x.mnemonic in WINDOW_BREAKERS for x in cins[tams[-1] + 1:k])
            if not tams or not _is_set_ff(csrc, cins, tams[-1]) or bad_labels or breaks:
                report.add("CI-3.03", FAIL, where(ln), "no straight-line LDA #$FF / TAM #$01 before the call in the "
                           "same routine (missing, a restore, a branch, or an undeclared / targeted label "
                           f"{bad_labels} in between)")
            else:
                report.add("CI-3.03", OK, where(ln), "caller maps MPR0 = $FF before the call (text-level)")
                passed = sorted(set(between) & pass_labels)
                if passed:
                    report.add("CI-3.03", NOT_STATIC, where(ln), f"declared pass-through label(s) {passed} between the "
                               "TAM and the call: no bios-scope branch targets them, but an entry from outside the bios "
                               "scope would skip the MPR0 setup")
    for missing in sorted(declared - found):
        report.add("CI-3.03", FAIL, missing, f"declared caller does not call {path} (stale declaration)")
    report.add("CI-3.03", NOT_STATIC, path, ("no caller in the bios scope; " if not found else "") +
               "callers outside the bios scope (fixtures, other builds) and run-time paths are not verified statically")


def _check_vectors(path, source, decl, report):
    vector_lines = [ln for ln in source.lines if ln.segment == "VECTORS" and (ln.mnemonic or ln.directive)]
    if not vector_lines and decl.get("role") == "reset_init" and not decl.get("vectors"):
        report.add("CI-3.05", NOT_STATIC, path, "reset entry without a VECTORS segment in this file: the interrupt "
                   "vectors of its builds live outside the bios scope (fixtures / build options); their handlers "
                   "are not checked")
        return
    if not vector_lines:
        if decl.get("vectors"):
            report.add("CI-3.05", FAIL, path, "vectors declared but the file has no VECTORS segment")
        return
    for ln in vector_lines:
        if ln.directive not in (".word", ".segment"):
            report.add("CI-3.05", FAIL, where(ln), f"{ln.directive or ln.mnemonic} in VECTORS: only .word is supported")
    vdecl = decl.get("vectors", {})
    handlers = set(vdecl.get("irq_handlers", []))
    targets = [(ln, t.strip()) for ln in vector_lines if ln.directive == ".word" for t in ln.operand.split(",")]
    if len(targets) != 5:
        report.add("CI-3.05", FAIL, path, f"{len(targets)} vector words; expected 5 (IRQ2/BRK, IRQ1, Timer, NMI, Reset)")
        return
    for ln, target in targets[:4]:
        if target not in handlers:
            report.add("CI-3.05", FAIL, where(ln), f"interrupt vector to undeclared handler {target!r}")
    if targets[4][1] != vdecl.get("reset"):
        report.add("CI-3.05", FAIL, where(targets[4][0]), f"reset vector {targets[4][1]!r} is not the declared reset "
                   "entry (its mapping setup is checked by CI-1)")
    for label in sorted(handlers):
        body, inside = [], False
        for ln in source.lines:
            if label in ln.labels:
                inside = True
            elif inside and any(not lab.startswith("@") for lab in ln.labels):
                break
            if inside and ln.mnemonic:
                body.append(ln)
        if not body:
            report.add("CI-3.05", FAIL, path, f"handler {label!r} not found")
            continue
        bad = None
        for ln in body:
            op = _op(source, ln)
            if ln.mnemonic in {"tam", "tma", "brk"} | ST_OPS | TRANSFERS:
                bad = (ln, "changes a mapping or touches I/O")
            elif ln.mnemonic in CALLS | BRANCHES and not _target(ln).startswith("@") and _target(ln) != label:
                bad = (ln, "calls or jumps to another routine (not followed by the static check)")
            elif ln.mnemonic in MEMORY_OPS and op.mode == "memory" and (op.indirect or op.logical_address() is None
                                                                        or op.logical_address() < 0x2000):
                bad = (ln, "uses an indirect, unresolved or page-0 operand")
            if bad:
                break
        last = body[-1]
        if bad is None and not (last.mnemonic == "rti" or (last.mnemonic in ("bra", "jmp") and (
                last.operand.strip().startswith("@") or last.operand.strip() == label))):
            bad = (last, "does not end in RTI or an unconditional loop (falls through)")
        if bad:
            report.add("CI-3.05", FAIL, where(bad[0]), f"handler {label!r} {bad[1]}")
        else:
            report.add("CI-3.05", OK, f"{path}:{label}", "handler has no mapping / I/O change, no call out, no "
                       "fall-through (text-level)")


def check(contract, sources, report):
    files = contract["files"]
    for path, source in sources.items():
        decl = files[path]
        for ln in source.instructions():   # $FF through an MPR other than MPR0
            if ln.mnemonic == "tam" and (_op(source, ln).value or 0) & 0xFE:
                ins = source.instructions()
                k = ins.index(ln)
                if k > 0 and ins[k - 1].mnemonic == "lda" and _op(source, ins[k - 1]).value == 0xFF:
                    report.add("CI-3.02", FAIL, where(ln), "TAM maps the hardware page ($FF) through an MPR other "
                               "than MPR0")
        for ln in source.instructions():
            if ln.mnemonic in CALLS and not re.match(r"^[A-Za-z_@][A-Za-z0-9_]*$", ln.operand.strip()):
                report.add("CI-3.03", FAIL, where(ln), f"{ln.mnemonic.upper()} {ln.operand.strip()!r}: not a bare label "
                           "(indirect or expression), so callers and targets cannot be found statically")
        page0, indirect, high, build_dependent = _memory_accesses(source, report, path)
        if build_dependent:
            names = sorted({_op(source, ln).symbol for ln in build_dependent})
            report.add("CI-3.02", NOT_STATIC, path, f"{len(build_dependent)} access(es) through build-dependent "
                       f"constant(s) {names} (assigned inside a conditional block, e.g. a -D option): the address, "
                       "and so whether it is page 0 / the hardware page, cannot be decided from the text")
        mmio = decl.get("mmio")
        for ln, addr in page0:
            if _op(source, ln).indexed:
                report.add("CI-3.02", FAIL, where(ln), f"indexed page-0 access ${addr:04X},x/y: its reach is not a "
                           "declared address")
        if page0 and mmio is None:
            for ln, addr in page0:
                report.add("CI-3.02", FAIL, where(ln), f"undeclared access to ${addr:04X} (page 0 = MPR0, possibly the "
                           "hardware page): fail closed")
        elif mmio is not None:
            allowed = {int(a, 16) for a in mmio.get("addresses", [])}
            bad = [(ln, a) for ln, a in page0 if a not in allowed]
            for ln, addr in bad:
                report.add("CI-3.02", FAIL, where(ln), f"access to ${addr:04X} not in the declared MMIO addresses")
            unused = sorted(allowed - {a for _, a in page0})
            if unused or not allowed:
                report.add("CI-3.02", FAIL, path, "declared MMIO addresses missing or not used (stale declaration): "
                           f"{[f'${a:04X}' for a in unused]}")
            mode = mmio.get("mapping")
            if mode == "window":
                windows = _windows(source, report)
                ins = source.instructions()
                spans = [(ins[i].number, ins[j].number) for i, j in windows]
                outside = [(ln, a) for ln, a in page0 if not any(s < ln.number < e for s, e in spans)]
                for ln, addr in outside:
                    report.add("CI-3.02", FAIL, where(ln), f"access to ${addr:04X} outside every MPR0 window")
                if not windows:
                    report.add("CI-3.02", FAIL, path, "window mapping declared but no valid MPR0 window in the file")
                elif not outside and not bad and page0 and not build_dependent:   # B2: no undecidable access
                    report.add("CI-3.02", OK, path, f"{len(page0)} {mmio.get('device', 'MMIO')} access(es) inside "
                               f"{len(windows)} SEI-masked, straight-line MPR0 window(s) closed by the restoring TAM "
                               "(text-level)")
            elif mode == "precondition":
                _check_precondition(path, source, mmio, sources, files, report)
            else:
                report.add("CI-3.02", FAIL, path, f"MMIO mapping {mode!r} must be 'window' or 'precondition'")
            if not bad:
                report.add("CI-3.02", BLOCKED, path, "which devices the production BIOS may touch is part of the "
                           "UNDECIDED production allowlist")
        if high:
            report.add("CI-3.02", NOT_STATIC, path, f"{len(high)} access(es) to pages 2-6 or linker-placed RAM: the "
                       "mapping is not fixed by the source")
        ind = decl.get("indirect")
        if indirect and (ind is None or ind.get("count") != len(indirect) or not ind.get("reason")):
            report.add("CI-3.04", FAIL, path, f"{len(indirect)} indirect access(es); declare count and reason")
        elif indirect:
            report.add("CI-3.04", NOT_STATIC, path, f"{len(indirect)} indirect access(es): effective addresses are "
                       f"run-time values ({ind['reason']})")
        elif ind is not None:
            report.add("CI-3.04", FAIL, path, "indirect accesses declared but none in the source (stale)")
        st_sites = [ln for ln in source.instructions() if ln.mnemonic in ST_OPS]
        st_decl = decl.get("st", [])
        if [ln.mnemonic for ln in st_sites] != [d.get("op") for d in st_decl]:
            report.add("CI-3.01", FAIL, path, f"ST0/ST1/ST2 sites {[ln.mnemonic for ln in st_sites]} do not match the "
                       "declaration")
        elif st_sites:
            report.add("CI-3.01", BLOCKED, path, "declared VDC register writes (independent of MPRs; #365 / #429); "
                       "production allowlist UNDECIDED")
        _check_vectors(path, source, decl, report)
    report.add("CI-3.06", NOT_STATIC, "bios scope", "device-originated DMA (CD / ADPCM, #367), IRQ timing and IRQs "
               "newly asserted inside an instruction (#653), and real hardware (#655) are not decidable from source text")
