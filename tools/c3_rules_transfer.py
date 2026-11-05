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
from c3_static_check import BLOCKED, FAIL, NOT_STATIC, OK, where

OPS = {"tii", "tin", "tdd", "tia", "tai"}
KINDS = {"ram", "rom", "mmio"}


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
        out[name] = {"kind": r["kind"], "bank": int(r["bank"], 16),
                     "offsets": None if offsets is None else {int(o, 16) for o in offsets}}
    return out


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
            src, dst, length = (v & 0xFFFF for v in values)
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
            if dr["kind"] == "mmio":
                report.add("CI-2.06", BLOCKED, where(ln), f"declared I/O transfer to {d['dst_region']} "
                           f"({d.get('purpose', '')}): range consistent; allowed only once the production allowlist "
                           "(UNDECIDED) covers it")
            else:
                report.add("CI-2.05", OK, where(ln), f"{ln.mnemonic.upper()} ranges inside the declared regions "
                           "(text-level; run-time mapping not claimed)")
    report.add("CI-2.07", NOT_STATIC, f"bios scope ({total} block transfer site(s))", "mid-transfer mapping change, IRQ "
               "entry during a transfer, device DMA and run-time operand rewriting are not decidable from source text "
               "(emulator evidence #648 / #653 only; real hardware #655)")
