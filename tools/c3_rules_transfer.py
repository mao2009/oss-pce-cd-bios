#!/usr/bin/env python3
"""CI-2 of the C-3 static checks: block transfer contract (TII / TIN / TDD / TIA / TAI). See c3_static_check.py.

Per bios file the contract lists every block transfer in source order ("transfers"), each with:
  op, src, dst, len         the operands as numbers (hex strings); they must equal the statically resolved operands
  mpr                       {page: bank} for every logical page the transfer touches (source and destination)
  src_region / dst_region   names from the top-level "regions" table (kind ram / rom / mmio)
  purpose
A legitimate I/O transfer (e.g. TIA to the VDC data port) is declared with a destination region of kind "mmio"; it is
not refused for being I/O, but it depends on the production allowlist, which is UNDECIDED, so it reports BLOCKED.
Address sequences follow the pinned core (measured in #648): TDD decrements both, TIN keeps the destination, TIA
alternates the destination between dst and dst+1, TAI alternates the source; length 0 means 65536 bytes.

What a static check cannot decide (reported NOT_STATIC): a mapping change or IRQ entry in the middle of a transfer
(#648 / #653 are emulator observations, real hardware #655), device DMA (#367), operands rewritten at run time.
"""
import c3_asm
from c3_static_check import BLOCKED, FAIL, NOT_STATIC, OK, resolve_tam_value, where

OPS = {"tii", "tin", "tdd", "tia", "tai"}
KINDS = {"ram", "rom", "mmio"}
# Hardware memory-map facts used to sanity-check region declarations (PCE / CD-ROM2):
HW_BANK = 0xFF                                                    # hardware (I/O) page
RAM_BANKS = set(range(0x68, 0x88)) | set(range(0xF7, 0xFC))     # (Super) CD-ROM RAM, BRAM, WRAM (SGX $F9-$FB)
ROM_BANK_LIMIT = 0x68                                             # HuCARD / System Card ROM banks below $68


def sequences(op, src, dst, length):
    count = length or 0x10000
    s = [(src - i if op == "tdd" else src + (i & 1) if op == "tai" else src + i) & 0xFFFF for i in range(count)]
    d = [(dst - i if op == "tdd" else dst if op == "tin" else dst + (i & 1) if op == "tia" else dst + i) & 0xFFFF
         for i in range(count)]
    return s, d


def _in_region(addr, mpr, region):
    bank = mpr.get(addr >> 13)
    if bank is None or bank != region["bank"]:
        return False
    offsets = region.get("offsets")
    return offsets is None or (addr & 0x1FFF) in offsets


def _regions(contract, report):
    out = {}
    for name, r in contract.get("regions", {}).items():
        if name.startswith("_"):
            continue
        if not isinstance(r, dict) or r.get("kind") not in KINDS or "bank" not in r:
            report.add("CI-2.00", FAIL, f"regions.{name}", "region needs kind ram / rom / mmio and a bank")
            continue
        offsets = r.get("offsets")
        try:
            bank = int(r["bank"], 16)
            offs = None if offsets is None else {int(o, 16) for o in offsets}
        except (TypeError, ValueError):
            report.add("CI-2.00", FAIL, f"regions.{name}", "malformed bank / offsets")
            continue
        consistent = {"mmio": bank == HW_BANK, "ram": bank in RAM_BANKS,
                      "rom": bank < ROM_BANK_LIMIT}[r["kind"]]
        if not consistent:
            report.add("CI-2.00", FAIL, f"regions.{name}", f"kind {r['kind']} does not match bank ${bank:02X} "
                       "(memory-map facts: $FF = hardware page, $68-$87 / $F7-$FB = RAM, below $68 = ROM)")
            continue
        out[name] = {"kind": r["kind"], "bank": bank, "offsets": offs}
    return out


def _mapping_status(source, line, pages, mpr):
    """Cross-check the declared mapping of every touched page against the TAMs of the same routine before the
    transfer. FAIL: a TAM gives a different bank. NOT_STATIC: a page is not mapped in this routine (the mapping
    comes from the caller / environment) or the TAM value cannot be decided. (OK, ...) only when every page is
    established by a TAM with a decidable value equal to the declaration."""
    ins = source.instructions()
    idx = ins.index(line)
    start_line = 0
    for ln in source.lines:          # routine start: the closest preceding non-local label
        if ln.number >= line.number:
            break
        if any(not lab.startswith("@") for lab in ln.labels):
            start_line = ln.number
    unknown = []
    for page in sorted(pages):
        tam = next((x for x in reversed(ins[:idx]) if x.number >= start_line and x.mnemonic == "tam" and
                    ((c3_asm.operand(x.operand, source.symbols).value is None) or
                     (c3_asm.operand(x.operand, source.symbols).value >> page) & 1)), None)
        if tam is None:
            unknown.append(f"page {page}: not mapped in this routine")
            continue
        if c3_asm.operand(tam.operand, source.symbols).value is None:
            unknown.append(f"page {page}: TAM mask at line {tam.number} not resolved")
            continue
        kind, value = resolve_tam_value(source, tam)
        if kind != "imm":
            unknown.append(f"page {page}: TAM value at line {tam.number} not decidable ({value})")
            continue
        if value != mpr[page]:
            return FAIL, (f"declared mapping page {page} = ${mpr[page]:02X} contradicts the TAM at line {tam.number} "
                          f"(${value:02X})")
    if unknown:
        return NOT_STATIC, ("memory attributes not established by the source: " + "; ".join(unknown) +
                            " (declared mapping taken as an assumption; not STATIC_OK)")
    return OK, ""


def check(contract, sources, report):
    regions = _regions(contract, report)
    total = 0
    for path, source in sources.items():
        decl = contract["files"][path].get("transfers", [])
        sites = [ln for ln in source.instructions() if ln.mnemonic in OPS]
        total += len(sites)
        if len(sites) != len(decl):
            report.add("CI-2.01", FAIL, path, f"{len(sites)} block transfer(s) in the source, {len(decl)} declared")
        for i, ln in enumerate(sites):
            d = decl[i] if i < len(decl) else None
            if d is None or d.get("op") != ln.mnemonic:
                report.add("CI-2.01", FAIL, where(ln), f"undeclared or mismatched {ln.mnemonic.upper()}")
                continue
            parts = [p.strip() for p in ln.operand.split(",")]
            values = [c3_asm.evaluate(p, source.symbols) for p in parts] if len(parts) == 3 else [None]
            if None in values:
                report.add("CI-2.02", FAIL, where(ln), f"operands {ln.operand!r} are not static constants (run-time or "
                           "linker-placed operands are outside the supported static scope)")
                continue
            if any(v < 0 or v > 0xFFFF for v in values):
                report.add("CI-2.02", FAIL, where(ln), "an operand is outside $0000-$FFFF")
                continue
            src, dst, length = values
            if [src, dst, length] != [int(d[k], 16) for k in ("src", "dst", "len")]:
                report.add("CI-2.03", FAIL, where(ln), "declared operands differ from the source")
                continue
            mpr = {int(k): int(v, 16) for k, v in d.get("mpr", {}).items()}
            s_seq, d_seq = sequences(ln.mnemonic, src, dst, length)
            pages = {a >> 13 for a in s_seq + d_seq}
            missing = sorted(p for p in pages if p not in mpr)
            if missing:
                report.add("CI-2.04", FAIL, where(ln), f"mapping of page(s) {missing} not declared: fail closed")
                continue
            sr, dr = regions.get(d.get("src_region")), regions.get(d.get("dst_region"))
            if sr is None or dr is None:
                report.add("CI-2.05", FAIL, where(ln), "source / destination region not in the regions table")
                continue
            if dr["kind"] == "rom":
                report.add("CI-2.05", FAIL, where(ln), "destination region is ROM (write to ROM / own code)")
                continue
            bad_s = next((a for a in s_seq if not _in_region(a, mpr, sr)), None)
            bad_d = next((a for a in d_seq if not _in_region(a, mpr, dr)), None)
            if bad_s is not None or bad_d is not None:
                which = f"source ${bad_s:04X}" if bad_s is not None else f"destination ${bad_d:04X}"
                report.add("CI-2.05", FAIL, where(ln), f"{which} is outside the declared region")
                continue
            status, detail = _mapping_status(source, ln, pages, mpr)
            if status == FAIL:
                report.add("CI-2.08", FAIL, where(ln), detail)
                continue
            if sr["kind"] == "mmio" or dr["kind"] == "mmio":
                which = " and ".join(n for n, r in (("source", sr), ("destination", dr)) if r["kind"] == "mmio")
                report.add("CI-2.06", BLOCKED, where(ln), f"declared I/O transfer ({which} "
                           f"{d['src_region'] if sr['kind'] == 'mmio' else d['dst_region']}; {d.get('purpose', '')}): "
                           "range consistent; allowed only once the production allowlist (UNDECIDED) covers it")
            if status == NOT_STATIC:
                report.add("CI-2.08", NOT_STATIC, where(ln), detail)
            elif sr["kind"] != "mmio" and dr["kind"] != "mmio":
                report.add("CI-2.05", OK, where(ln), f"{ln.mnemonic.upper()} ranges inside the declared regions, "
                           "every page mapped by a TAM of this routine (text-level; run-time not claimed)")
    report.add("CI-2.07", NOT_STATIC, f"bios scope ({total} block transfer site(s))", "mid-transfer mapping change, IRQ "
               "entry during a transfer, device DMA and run-time operand rewriting are not decidable from source text "
               "(emulator evidence #648 / #653 only; real hardware #655)")
