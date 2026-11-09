#!/usr/bin/env python3
"""Issue #740 H4/H5 E1-E5 unified judge (HOST_FIXTURE, synthetic only).

Combines Agent A E1-E3 (h45_preblock_gate.judge_allowlist/irq) and Agent B E4-E5
(block_device_predeny.preflight/device_audit) into one fail-closed contract. Pure
stdlib over caller-supplied dicts; reads no BIOS/ROM/emulator, runs no core, never
executes a genuine callee. Before #738/#739 fixes complete the unified verdict is
structurally BLOCKED (modules missing + prereq false); even on PASS execution stays
BLOCKED with jsr_count=0 and release_eligible=false. The `prereq` dict is an
unauthenticated caller assertion, never proof of merges, reviews, CI, or safety:
a forged all-True prereq still BLOCKEDs on evidence, and PASS records carry
PREREQ_UNAUTHENTICATED_ASSERTION_NOT_PROOF. Single-module PASS is never
overall PASS. Self-declared deny_flags are never trusted: deny conditions are
recomputed from block_request/device_rows/build and cross-checked. Missing /
inconsistent / unimplemented evidence is BLOCKED, never PASS; proven side-effect is
FAIL. Pinned Geargrafx b49ae82a012eade566d72e9d61c9d95a5caa4da3 only, matching
tools/dependencies.json. Reads no emulatorsource beyond the pinned revision string.

  python3 tools/h45_unified_judge.py --check   # synthetic self-check, exits 3
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PINNED = "b49ae82a012eade566d72e9d61c9d95a5caa4da3"
PASS, FAIL, BLOCKED = "PASS", "FAIL", "BLOCKED"
EXIT_BLOCKED = 3
DENY_KEYS = ("same_value_write", "temp_write_restore", "gg_testing", "libretro",
             "block_move_outside_allowlist", "device_dma_touches_ram")
_BUILD_DICT_KEYS = frozenset(("gg_testing", "GG_TESTING", "libretro", "LIBRETRO"))
PREREQ_KEYS = ("merged_738", "merged_739", "independent_review_complete", "ci_confirmed")
TRUSTED_APPROVED_RANGE = [0x1E00, 0x4000]
DANGEROUS_OPCODES = frozenset({0x03, 0x13, 0x23, 0x20})


class GateRefused(ValueError):
    """Unified gate did not return PASS (fail closed)."""


def _ok_int(v):
    return type(v) is int


def _load():
    for base in ("", "tools."):
        try:
            import importlib

            a = importlib.import_module(base + "h45_preblock_gate")
            b = importlib.import_module(base + "block_device_predeny")
            return a, b, None
        except Exception:
            continue
    return None, None, "MODULE_MISSING"


def _pinned_ok(a, b):
    try:
        if getattr(a, "PINNED_CORE_REVISION", None) != PINNED:
            return False
        if getattr(b, "PINNED_REVISION", None) != PINNED:
            return False
        deps = json.loads((ROOT / "tools" / "dependencies.json").read_text(encoding="utf-8"))
        return deps["geargrafx"]["revision"] == PINNED
    except Exception:
        return False


def _prereq_ok(prereq):
    # Caller assertion only: True is necessary but proves nothing by itself.
    # These flags claim merges/reviews/CI that this module cannot verify: there
    # is no signature, no run-id, no harness binding. A forged all-True prereq
    # must still BLOCK on evidence checks, and even a fully passing case stays
    # synthetic HOST_FIXTURE with execution BLOCKED. REAL_CORE never passes here
    # (no trusted harness). The PASS record always carries
    # PREREQ_UNAUTHENTICATED_ASSERTION_NOT_PROOF so no consumer mistakes the
    # verdict for integration or safety proof.
    if not isinstance(prereq, dict):
        return False
    return all(prereq.get(k) is True for k in PREREQ_KEYS)


def _authenticity_ok(snap):
    """Unified-level evidence-authenticity gate, independent of A/B modules.

    Blocks forged REAL_CORE claims (run-id/halt presence proves nothing without a
    trusted harness), HOST rows carrying harness artefacts, widened allowlist
    ranges, and instruction sequences hiding 2nd+ dangerous opcodes. Returns
    (ok, reason); ok False means BLOCKED/FAIL at unified level even if a
    submodule would PASS on its own inputs.
    """
    if not isinstance(snap, dict):
        return False, "SNAPSHOT_NOT_DICT"
    prov = snap.get("provenance")
    if not isinstance(prov, dict):
        return False, "PROVENANCE_MISSING"
    kind = prov.get("kind")
    if kind == "REAL_CORE_EXECUTION":
        # No trusted harness exists in this repo: any REAL_CORE claim is
        # unproven, including fully forged run-id/halt pairs. BLOCKED.
        return False, "REAL_CORE_REQUIRES_HARNESS"
    if kind != "HOST_FIXTURE":
        return False, "PROVENANCE_UNKNOWN"
    # HOST_FIXTURE must not carry harness artefacts.
    if snap.get("run_id") is not None or prov.get("run_id") is not None:
        return False, "HOST_WITH_RUN_ID"
    if prov.get("core_revision") is not None:
        return False, "HOST_WITH_CORE_REVISION"
    if snap.get("halt_registers") is not None:
        return False, "HOST_WITH_HALT_REGISTERS"
    # Approved range is pinned; snapshot-supplied widening is rejected.
    if snap.get("approved_range") != TRUSTED_APPROVED_RANGE:
        return False, "APPROVED_RANGE_UNTRUSTED"
    # Instruction sequence must exist, match executed count/first opcode,
    # and contain no dangerous opcode (incl. 2nd+ ST/JSR).
    seq = snap.get("instruction_sequence")
    executed = snap.get("executed")
    if type(seq) is not list:
        return False, "SEQUENCE_MISSING"
    if type(executed) is not int or executed < 0:
        return False, "EXECUTED_NOT_INT"
    if len(seq) != executed:
        return False, "SEQUENCE_COUNT_MISMATCH"
    if any(type(o) is not int or not 0 <= o <= 0xFF for o in seq):
        return False, "SEQUENCE_NON_BYTE"
    if seq and seq[0] != snap.get("first_opcode"):
        return False, "SEQUENCE_FIRST_MISMATCH"
    for opcode in seq:
        if opcode in DANGEROUS_OPCODES:
            return False, "SEQUENCE_DANGEROUS_OPCODE"
    return True, "AUTH_OK"


def _mpr_ok(mpr):
    return isinstance(mpr, list) and len(mpr) == 8 and all(_ok_int(v) and 0 <= v <= 0xFF for v in mpr)


def _build_blocked(build):
    if build in ("libretro", "GG_TESTING"):
        return True
    if isinstance(build, dict):
        # Fail-closed dict contract (cf. block_device_predeny preflight build
        # keys in #739): only known keys with strict bool values are
        # interpretable; unknown keys or non-bool values prove nothing and
        # BLOCK. Only all-explicit-False (or empty) proceeds.
        for k, v in build.items():
            if k not in _BUILD_DICT_KEYS:
                return True
            if type(v) is not bool:
                return True
        return any(build.get(k) for k in ("gg_testing", "GG_TESTING", "libretro", "LIBRETRO"))
    return build != "debugger"


def clean_case():
    """Synthetic unified case that PASSES only with modules present and prereq all True."""
    rom = "a" * 64
    mpr = [0x00, 0xF8, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]
    snap = {
        "provenance": {"kind": "HOST_FIXTURE", "core_revision": None, "bios": "SYNTHETIC",
                       "real_core_claim": False, "run_id": None},
        "run_id": None, "halt_registers": None, "instruction_sequence": [],
        "jsr_count": 0, "execution": "BLOCKED", "release_eligible": False, "build": "debugger",
        "first_opcode": 0xEA, "first_opcode_screened": True, "peek_ok": True,
        "executed_before_refusal": 0, "vdc_changed": False, "halt_equal": True,
        "mmio_stores": [], "mpr": list(mpr), "mpr1_init_value": 0xF8, "mpr1_init_index": 0,
        "alias_detected": False, "irq_asserted": False, "irq_pending": False,
        "stack_writes": [], "mpr1_changed": False, "pc": 0x3E00,
        "approved_range": [0x1E00, 0x4000], "irq_status": "OBSERVED", "p_i": True,
        "budget": 100, "executed": 0, "rom_sha256": rom, "expected_sha256": rom,
        "deny_flags": {k: False for k in DENY_KEYS},
    }
    handler = 0x3F80
    irq_row = {"exit": 0, "status": "ok", "mode": "irq-entry", "vector": "IRQ2",
               "vector_addr": 0xFFF6, "vector_lo": handler & 0xFF, "vector_hi": handler >> 8,
               "execbp_hit": True, "stop_pc": handler, "handler_instructions": 0,
               "handler_writes": [], "stack_writes": [0x21FF, 0x21FE, 0x21FD],
               "pending": False, "p_i": True, "halt_equal": True}
    block = {"op": "TII", "src": 0x2000, "dst": 0x2100, "length": 8, "mpr": list(mpr),
             "window": [0x2000, 0x4000], "allowed_banks": {0xF8, 0x00},
             "build": "debugger", "same_value": False, "temp_restore": False}
    dev = [{"id": "DEV-01", "role": "device_store", "target": "VRAM", "verdict": "NOT_MAIN_RAM"},
           {"id": "DEV-04", "role": "reset_store", "target": "WRAM", "verdict": "RESET_ONLY"},
           {"id": "CPU-04", "role": "cpu_store", "target": "WRAM", "verdict": "CPU_ORIGINATED"}]
    return {"snapshot": snap, "irq": {"row": irq_row, "vector": "IRQ2", "expected_handler": handler,
            "build": "debugger"}, "block_request": block, "device_rows": dev,
            "prereq": {k: True for k in PREREQ_KEYS}}


def judge_unified(case):
    """Combine A and B fail-closed. Returns (verdict, reasons, record); PASS only if all hold."""
    if not isinstance(case, dict):
        return BLOCKED, ["CASE_NOT_DICT"], _record(BLOCKED, ["CASE_NOT_DICT"])
    snap, irq = case.get("snapshot"), case.get("irq")
    block, dev, prereq = case.get("block_request"), case.get("device_rows"), case.get("prereq")
    if not _prereq_ok(prereq):
        return BLOCKED, ["PREREQ_INCOMPLETE"], _record(BLOCKED, ["PREREQ_INCOMPLETE"])
    if not isinstance(snap, dict) or not isinstance(block, dict) or not isinstance(dev, list) or not isinstance(irq, dict):
        return BLOCKED, ["UNIMPLEMENTED_OR_MISSING"], _record(BLOCKED, ["UNIMPLEMENTED_OR_MISSING"])
    a, b, err = _load()
    if err is not None or a is None or b is None:
        return BLOCKED, ["MODULE_MISSING"], _record(BLOCKED, ["MODULE_MISSING"])
    if not _pinned_ok(a, b):
        return BLOCKED, ["PINNED_MISMATCH"], _record(BLOCKED, ["PINNED_MISMATCH"])
    if snap.get("execution") != "BLOCKED" or snap.get("release_eligible") is not False or snap.get("jsr_count") != 0:
        v = FAIL if snap.get("jsr_count") != 0 or snap.get("execution") != "BLOCKED" else BLOCKED
        return v, ["SNAPSHOT_GATE_VIOLATION"], _record(v, ["SNAPSHOT_GATE_VIOLATION"])
    if _build_blocked(snap.get("build")) or _build_blocked(block.get("build", "debugger")) or _build_blocked(irq.get("build", "debugger")):
        return BLOCKED, ["TESTING_BUILD"], _record(BLOCKED, ["TESTING_BUILD"])
    sm, bm = snap.get("mpr"), block.get("mpr")
    if not _mpr_ok(sm) or not _mpr_ok(bm if isinstance(bm, list) else list(bm) if isinstance(bm, tuple) else bm):
        return BLOCKED, ["MALFORMED_MPR"], _record(BLOCKED, ["MALFORMED_MPR"])
    if list(sm) != list(bm):
        return BLOCKED, ["MPR_INCONSISTENT"], _record(BLOCKED, ["MPR_INCONSISTENT"])
    ok, reason = _authenticity_ok(snap)
    if not ok:
        v = FAIL if reason == "SEQUENCE_DANGEROUS_OPCODE" else BLOCKED
        return v, [reason], _record(v, [reason])
    irq_status = snap.get("irq_status")
    if irq_status != "OBSERVED":
        return BLOCKED, ["IRQ_STATUS_NOT_OBSERVED"], _record(BLOCKED, ["IRQ_STATUS_NOT_OBSERVED"])
    if type(snap.get("irq_pending")) is not bool or type(snap.get("p_i")) is not bool:
        return BLOCKED, ["IRQ_PENDING_PI_MISSING"], _record(BLOCKED, ["IRQ_PENDING_PI_MISSING"])
    # Snapshot and irq.row are separate observations (no proof of same
    # observation time): require strict bools on both sides and consistency.
    # Contradictory evidence cannot PASS.
    irq_row = irq.get("row")
    if not isinstance(irq_row, dict):
        return BLOCKED, ["IRQ_ROW_MISSING"], _record(BLOCKED, ["IRQ_ROW_MISSING"])
    row_pending, row_pi = irq_row.get("pending"), irq_row.get("p_i")
    if type(row_pending) is not bool or type(row_pi) is not bool:
        return BLOCKED, ["IRQ_ROW_PENDING_PI_MISSING"], _record(BLOCKED, ["IRQ_ROW_PENDING_PI_MISSING"])
    if row_pending is not snap.get("irq_pending") or row_pi is not snap.get("p_i"):
        return BLOCKED, ["IRQ_EVIDENCE_MISMATCH"], _record(BLOCKED, ["IRQ_EVIDENCE_MISMATCH"])
    if snap.get("irq_pending") is True and snap.get("p_i") is False:
        return FAIL, ["IRQ_WOULD_BE_TAKEN"], _record(FAIL, ["IRQ_WOULD_BE_TAKEN"])
    if any(v == 0xFF for v in sm[:7]) or sm[1] != 0xF8:
        return BLOCKED, ["MPR_FF_OR_NOT_F8"], _record(BLOCKED, ["MPR_FF_OR_NOT_F8"])
    av, ad = a.judge_allowlist(snap)
    if av != PASS:
        return av, [f"A_ALLOWLIST_{av}:{ad}"], _record(av, [f"A_ALLOWLIST_{av}:{ad}"])
    try:
        iv, idet = a.judge_irq_first_instruction(irq["row"], irq["vector"], irq["expected_handler"], build=irq.get("build", "debugger"))
    except Exception as e:
        return BLOCKED, [f"IRQ_ERROR:{e}"], _record(BLOCKED, [f"IRQ_ERROR:{e}"])
    if iv != PASS:
        return iv, [f"E2_IRQ_{iv}:{idet}"], _record(iv, [f"E2_IRQ_{iv}:{idet}"])
    # Deny observations must be explicit bools: missing/None/non-bool proves
    # nothing and cannot default to safe False. Only explicit False proceeds;
    # explicit True is still refused by preflight below.
    sv, tr = block.get("same_value"), block.get("temp_restore")
    if type(sv) is not bool or type(tr) is not bool:
        return BLOCKED, ["BLOCK_DENY_OBSERVATION_MISSING"], _record(BLOCKED, ["BLOCK_DENY_OBSERVATION_MISSING"])
    try:
        bp = b.preflight(block["op"], block["src"], block["dst"], block["length"], block["mpr"],
                         block["window"], block["allowed_banks"],
                         build=_b_build(block.get("build")), same_value=sv,
                         temp_restore=tr)
        ba = b.device_audit(dev)
    except Exception as e:
        return BLOCKED, [f"B_ERROR:{e}"], _record(BLOCKED, [f"B_ERROR:{e}"])
    if bp.get("gate") != BLOCKED or bp.get("execution") != BLOCKED or bp.get("jsr_count") != 0:
        return BLOCKED, ["B_PREFLIGHT_GATE_NOT_BLOCKED"], _record(BLOCKED, ["B_PREFLIGHT_GATE_NOT_BLOCKED"])
    if bp.get("verdict") != "PERMITTED_STATIC_ONLY":
        return BLOCKED, [f"B_PREFLIGHT_{bp.get('verdict')}:{bp.get('reason')}"], _record(BLOCKED, [f"B_PREFLIGHT_{bp.get('verdict')}:{bp.get('reason')}"])
    if ba.get("gate") != BLOCKED or ba.get("execution") != BLOCKED or ba.get("jsr_count") != 0:
        return BLOCKED, ["B_AUDIT_GATE_NOT_BLOCKED"], _record(BLOCKED, ["B_AUDIT_GATE_NOT_BLOCKED"])
    if ba.get("verdict") != "DRAINED_NO_UNKNOWN":
        return BLOCKED, [f"B_AUDIT_{ba.get('verdict')}"], _record(BLOCKED, [f"B_AUDIT_{ba.get('verdict')}"])
    recomputed = {"same_value_write": sv,
                  "temp_write_restore": tr,
                  "gg_testing": False, "libretro": False,
                  "block_move_outside_allowlist": False, "device_dma_touches_ram": False}
    if snap.get("build") == "GG_TESTING":
        recomputed["gg_testing"] = True
    if snap.get("build") == "libretro":
        recomputed["libretro"] = True
    deny = snap.get("deny_flags")
    if not isinstance(deny, dict):
        return BLOCKED, ["DENY_FLAGS_MISSING"], _record(BLOCKED, ["DENY_FLAGS_MISSING"])
    for k in DENY_KEYS:
        if deny.get(k) is not False:
            return BLOCKED, [f"SELF_DECLARED_DENY_{k}"], _record(BLOCKED, [f"SELF_DECLARED_DENY_{k}"])
        if recomputed.get(k):
            return BLOCKED, [f"DENY_FLAG_MISMATCH_{k}"], _record(BLOCKED, [f"DENY_FLAG_MISMATCH_{k}"])
    if bp.get("reason") in ("dst_outside_window", "dst_bank_not_allowed", "ff_mpr", "src_read_side_effect"):
        return BLOCKED, ["BLOCK_MOVE_DENIED"], _record(BLOCKED, ["BLOCK_MOVE_DENIED"])
    return PASS, ["UNIFIED_SYNTHETIC_PASS_EXECUTION_BLOCKED",
                 "PREREQ_UNAUTHENTICATED_ASSERTION_NOT_PROOF"], _record(
                     PASS, ["UNIFIED_SYNTHETIC_PASS_EXECUTION_BLOCKED",
                            "PREREQ_UNAUTHENTICATED_ASSERTION_NOT_PROOF"])


def _b_build(build):
    if isinstance(build, dict):
        # Same interpretation as _build_blocked(): uninterpretable dicts
        # (unknown keys / non-bool values) map to a testing build so the
        # B preflight (#739) refuses even if the unified gate were bypassed.
        for k, v in build.items():
            if k not in _BUILD_DICT_KEYS or type(v) is not bool:
                return {"gg_testing": True}
        return build
    if build == "debugger":
        return {}
    if build == "libretro":
        return {"libretro": True}
    return {"gg_testing": True}


def _record(verdict, reasons):
    return {"verdict": verdict, "reasons": reasons, "execution": "BLOCKED",
            "execution_permitted": False, "jsr_count": 0, "release_eligible": False, "runner": "NONE"}


def require_unified(case):
    """Fail-closed wrapper: only PASS returns; never permits execution."""
    verdict, reasons, record = judge_unified(case)
    if verdict != PASS:
        raise GateRefused("BLOCKED: unified refused: " + ", ".join(reasons))
    return record


def _self_check():
    a, b, err = _load()
    v, r, _ = judge_unified(clean_case())
    if err is not None:
        if v != BLOCKED:
            raise ValueError(f"clean case pre-merge must be BLOCKED, got {v}:{r}")
        return "unified BLOCKED pre-merge as required; single-module PASS insufficient; execution BLOCKED"
    if v != PASS:
        raise ValueError(f"clean case with modules must PASS synthetically, got {v}:{r}")
    return "unified synthetic PASS with execution BLOCKED; single-module PASS insufficient"


def main(argv=None):
    import argparse

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--check", action="store_true")
    args = p.parse_args(argv)
    if not args.check:
        p.error("--check is required")
    try:
        s = _self_check()
    except ValueError as e:
        print(f"BLOCKED: unified self-check failed: {e}")
        return EXIT_BLOCKED
    print(f"BLOCKED: unified gate closed; jsr_count=0, release_eligible=false; {s}")
    print(json.dumps({"verdict": BLOCKED, "execution": "BLOCKED", "execution_permitted": False,
                      "jsr_count": 0, "release_eligible": False}, sort_keys=True))
    return EXIT_BLOCKED


if __name__ == "__main__":
    sys.exit(main())
