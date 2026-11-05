#!/usr/bin/env python3
"""C-3 static contract checks for the project's own HuC6280 sources (#473, C-3 design).

This tool checks STATIC facts about the source text against the machine-readable contract
spec/c3_static_contracts.json. It does not change any source, does not assemble, and does not prove run-time safety:
a static result says only that the source text is consistent with what the contract declares.

Verdicts (never PASS):
  FAIL           a contract rule is broken, or something is undeclared / unresolved / unsupported (fail closed)
  STATIC_OK      the source text matches the declaration; nothing about run-time behaviour is claimed
  BLOCKED        the declaration depends on a decision that is not approved (e.g. the production allowlist)
  NOT_STATIC     the property cannot be decided from source text (run-time, device or hardware evidence needed)
Exit status 1 when any FAIL is reported, else 0. BLOCKED and NOT_STATIC never become STATIC_OK.

Rule groups: CI-1 (MPR / stack contract) in this file; CI-2 (block transfers) and CI-3 (IRQ / MMIO) are separate
modules (RULE_MODULES). The contract lists the groups it is checked with ("rule_groups"); a declared group whose
module is missing is a FAIL, never a silent skip. The production allowlist stays UNDECIDED (Owner decision): the
verification contract "MPR1 = $F8 only" (#497) is NOT used as a production allowlist here.

    python3 tools/c3_static_check.py [--json] [--contract spec/c3_static_contracts.json]
"""
import argparse
import importlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import c3_asm  # noqa: E402

FAIL, OK, BLOCKED, NOT_STATIC = "FAIL", "STATIC_OK", "BLOCKED", "NOT_STATIC"
CONTRACT = ROOT / "spec" / "c3_static_contracts.json"
RULE_MODULES = {"CI-2": "c3_rules_transfer", "CI-3": "c3_rules_irq_mmio"}   # groups beyond the built-in CI-1
ROLES = {"reset_init", "api", "internal"}
STORES = {"sta", "stx", "sty", "stz"}


class Report:
    def __init__(self):
        self.findings = []

    def add(self, rule, verdict, where, message):
        self.findings.append({"rule": rule, "verdict": verdict, "where": where, "message": message})

    def failed(self):
        return any(f["verdict"] == FAIL for f in self.findings)

    def counts(self):
        out = {}
        for f in self.findings:
            out[f["verdict"]] = out.get(f["verdict"], 0) + 1
        return out


def where(line):
    return f"{line.file}:{line.number}"


def previous_instruction(source, line):
    """The instruction just before `line` in the same straight-line run (no label in between), or None."""
    ins = source.instructions()
    index = ins.index(line)
    if index == 0 or line.labels:
        return None
    prev = ins[index - 1]
    between = [ln for ln in source.lines if prev.number < ln.number < line.number and ln.labels]
    return None if between else prev


def resolve_tam_value(source, line):
    """('imm', value) when A comes from LDA #imm right before the TAM, ('dynamic', how) otherwise."""
    prev = previous_instruction(source, line)
    if prev is None:
        return ("dynamic", "label or start of routine before the TAM")
    if prev.mnemonic == "lda":
        op = c3_asm.operand(prev.operand, source.symbols)
        if op.mode == "immediate" and op.value is not None:
            return ("imm", op.value & 0xFF)
    return ("dynamic", f"A comes from {prev.mnemonic.upper()}")


def check_scope(contract, root, report):
    scope = contract["scope"]
    bios, fixture = set(scope["bios"]), set(scope["fixture"])
    for overlap in sorted(bios & fixture):
        report.add("CI-1.01", FAIL, overlap, "file is listed as both bios and fixture")
    files = sorted(str(p.relative_to(root)).replace("\\", "/") for p in (root / "src").rglob("*.s"))
    for f in files:
        if f not in bios and f not in fixture:
            report.add("CI-1.01", FAIL, f, "source file is not classified (bios or fixture): fail closed")
    for f in sorted((bios | fixture) - set(files)):
        report.add("CI-1.01", FAIL, f, "classified file does not exist (stale contract)")
    for f in sorted(fixture):
        reason = scope.get("fixture_reasons", {}).get(f)
        if not reason:
            report.add("CI-1.01", FAIL, f, "fixture without a stated reason")
    return [f for f in files if f in bios]


def check_allowlist(contract, report):
    allow = contract.get("production_allowlist", {})
    values = {k: v for k, v in allow.items() if k not in ("status", "approved_by", "review_ref", "note")}
    status = allow.get("status")
    if status == "UNDECIDED":
        if any(v is not None for v in values.values()):
            report.add("CI-1.07", FAIL, "production_allowlist", "values present while the allowlist is UNDECIDED "
                       "(an unapproved allowlist must not be filled in)")
        else:
            report.add("CI-1.07", BLOCKED, "production_allowlist", "production allowlist is UNDECIDED (Owner decision); "
                       "every rule that would need it reports BLOCKED, not STATIC_OK")
    elif status == "APPROVED":
        if not allow.get("approved_by") or not allow.get("review_ref"):
            report.add("CI-1.07", FAIL, "production_allowlist", "APPROVED without approved_by / review_ref")
        else:
            report.add("CI-1.07", BLOCKED, "production_allowlist", "approval is recorded but this tool cannot verify "
                       "it; an Owner check of the review reference is required")
    else:
        report.add("CI-1.07", FAIL, "production_allowlist", f"unknown allowlist status {status!r}")


def check_file_mpr(source, decl, report):
    role = decl.get("role")
    if role not in ROLES:
        report.add("CI-1.02", FAIL, source.path, f"unknown or missing role {role!r}")
        return
    for number, reason in source.unsupported:
        report.add("CI-1.02", FAIL, f"{source.path}:{number}", reason)
    declared = decl.get("tam", [])
    sites = [ln for ln in source.instructions() if ln.mnemonic == "tam"]
    if len(sites) != len(declared):
        report.add("CI-1.03", FAIL, source.path, f"{len(sites)} TAM site(s) in the source, {len(declared)} declared")
    saves = {}
    for ln in source.instructions():
        if ln.mnemonic == "tma":
            op = c3_asm.operand(ln.operand, source.symbols)
            if op.mode == "immediate" and op.value is not None:
                saves.setdefault(op.value, ln.number)
    for i, ln in enumerate(sites):
        op = c3_asm.operand(ln.operand, source.symbols)
        if op.mode != "immediate" or op.value is None:
            report.add("CI-1.04", FAIL, where(ln), f"TAM operand {ln.operand!r} is not a resolvable immediate mask")
            continue
        mask = op.value & 0xFF
        if mask == 0:
            report.add("CI-1.04", FAIL, where(ln), "TAM #$00 maps nothing (undefined intent)")
            continue
        kind, value = resolve_tam_value(source, ln)
        d = declared[i] if i < len(declared) else None
        if d is None:
            report.add("CI-1.03", FAIL, where(ln), f"undeclared TAM #${mask:02X}")
            continue
        if int(d["mask"], 16) != mask:
            report.add("CI-1.03", FAIL, where(ln), f"declared mask {d['mask']} != source mask ${mask:02X}")
            continue
        if d["value"] == "restore":
            if kind != "dynamic" or mask not in saves or saves[mask] > ln.number or not d.get("reason"):
                report.add("CI-1.04", FAIL, where(ln), "declared 'restore' needs a dynamic value, an earlier TMA of the "
                           "same mask in the file, and a reason")
                continue
        else:
            want = int(d["value"], 16)
            if kind != "imm" or value != want:
                report.add("CI-1.04", FAIL, where(ln), f"declared value {d['value']} but the source gives "
                           f"{'$%02X' % value if kind == 'imm' else value}")
                continue
        if mask & 0x02:   # MPR1 = the stack page
            if role != "reset_init":
                report.add("CI-1.05", FAIL, where(ln), "TAM changes MPR1 (stack page) outside a reset-init file "
                           "(static contract S-1)")
                continue
            if d["value"] == "restore":
                report.add("CI-1.05", FAIL, where(ln), "MPR1 must not be set from a run-time value")
                continue
            report.add("CI-1.05", BLOCKED, where(ln), f"reset-time MPR1 = {d['value']}: which bank the production stack "
                       "may use is UNDECIDED (the #497 verification value is not a production allowlist)")
            continue
        report.add("CI-1.03", OK, where(ln), f"TAM #${mask:02X} = {d['value']} matches the declaration "
                   f"({d.get('purpose', 'no purpose given')}); run-time mapping not claimed")
    for ln in source.instructions():
        if ln.mnemonic == "txs":
            if role != "reset_init":
                report.add("CI-1.06", FAIL, where(ln), "TXS (stack pointer change) outside a reset-init file")
            else:
                report.add("CI-1.06", BLOCKED, where(ln), "reset-time stack pointer: the stack-pointer ABI is UNKNOWN")
        if ln.mnemonic in STORES:
            op = c3_asm.operand(ln.operand, source.symbols)
            target = ln.operand.split(",")[0].strip().removeprefix("a:").removeprefix("z:")
            seg = source.labels.get(target)
            if op.mode == "memory" and seg and seg.upper() not in c3_asm.RAM_SEGMENTS:
                report.add("CI-1.08", FAIL, where(ln), f"store to label {target!r} in segment {seg!r}: self-modifying "
                           "code is outside the supported static scope")


def run(contract_path=CONTRACT, root=ROOT):
    contract = json.loads(Path(contract_path).read_text(encoding="utf-8"))
    report = Report()
    if contract.get("schema") != 1:
        report.add("CI-1.00", FAIL, str(contract_path), "unknown contract schema")
        return report, contract
    bios_files = check_scope(contract, root, report)
    check_allowlist(contract, report)
    sources = {}
    for f in bios_files:
        decl = contract.get("files", {}).get(f)
        if decl is None:
            report.add("CI-1.02", FAIL, f, "bios file without a contract entry")
            continue
        sources[f] = c3_asm.parse(root / f, root)
        check_file_mpr(sources[f], decl, report)
    for extra in sorted(set(contract.get("files", {})) - set(bios_files)):
        report.add("CI-1.02", FAIL, extra, "contract entry for a file outside the bios scope")
    report.add("CI-1.08", NOT_STATIC, "bios scope", "absence of self-modifying code, of computed jumps into data, and of "
               "run-time computed mappings is not proven by a static text check (only direct stores to code labels "
               "are detected)")
    groups = contract.get("rule_groups", [])
    if "CI-1" not in groups:
        report.add("CI-1.00", FAIL, str(contract_path), "rule_groups must include CI-1")
    for group in groups:
        if group == "CI-1":
            continue
        name = RULE_MODULES.get(group)
        if name is None:
            report.add("CI-1.00", FAIL, str(contract_path), f"unknown rule group {group!r}")
            continue
        try:
            module = importlib.import_module(name)
        except ModuleNotFoundError:
            report.add("CI-1.00", FAIL, str(contract_path), f"rule group {group} is declared but {name} is missing: "
                       "a declared check must not be skipped")
            continue
        module.check(contract, sources, report)
    return report, contract


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--contract", type=Path, default=CONTRACT)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report, _ = run(args.contract, args.root)
    if args.json:
        print(json.dumps({"findings": report.findings, "counts": report.counts()}, indent=2))
    else:
        for f in report.findings:
            if f["verdict"] != OK:
                print(f"{f['verdict']:10s} {f['rule']:8s} {f['where']}: {f['message']}")
        print(f"C-3 static check: {json.dumps(report.counts(), sort_keys=True)} (never PASS; STATIC_OK is a text-level "
              "consistency result only; production allowlist UNDECIDED)")
    return 1 if report.failed() else 0


if __name__ == "__main__":
    raise SystemExit(main())
