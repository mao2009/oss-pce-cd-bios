#!/usr/bin/env python3
"""H4/H5 real-core verification harness (synthetic only, pinned Geargrafx).

Spawns the existing synthetic real-core probes itself, captures their
machine-readable EVIDENCE lines, and validates armed-vs-control outcomes.
Reads no genuine BIOS/ROM, runs no genuine callee, makes no gate change:
reference_jsr_gate.json stays BLOCKED, release_eligible stays false.

Verdicts:
  VERIFIED_PREBLOCK  - every armed case refused before side effect AND every
                       mutation landed (sensitivity) AND positive control
                       landed. Synthetic single-step scope only.
  FAIL               - a prohibited side effect landed in an armed run.
  BLOCKED            - evidence missing/unobservable, env unavailable, or any
                       check inconclusive. Never PASS on unproven data.

Usage:
  python3 tools/h45_realcore_verify.py --probe st --output DIR
  Probes: st | mmio | block | all (default: st)
  Transcript JSON is written to DIR (keep outside tracked paths).
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PINNED = "b49ae82a012eade566d72e9d61c9d95a5caa4da3"

PROBES = {
    "st": "tests/integration/geargrafx_st_boundary.py",
    "mmio": "tests/integration/geargrafx_mmio1804_preblock.py",
    "block": "tests/integration/geargrafx_block_midrefusal.py",
    "blockpre": "tests/integration/geargrafx_block_preblock.py",
}

PASS, FAIL, BLOCKED = "PASS", "FAIL", "BLOCKED"


def _run_probe(script, timeout):
    env = dict(os.environ)
    start = time.time()
    proc = subprocess.run(
        [sys.executable, str(ROOT / script)], cwd=str(ROOT),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, timeout=timeout, env=env)
    return proc, time.time() - start


def _parse_evidence(stdout):
    cases = {}
    for line in stdout.splitlines():
        if not line.startswith("EVIDENCE "):
            continue
        try:
            item = json.loads(line[len("EVIDENCE "):])
        except json.JSONDecodeError:
            return None, "EVIDENCE_JSON_MALFORMED"
        case = item.get("case")
        if not isinstance(case, str):
            return None, "EVIDENCE_CASE_MISSING"
        if case in cases:
            return None, f"DUPLICATE_CASE:{case}"
        cases[case] = item
    if not cases:
        return None, "NO_EVIDENCE_LINES"
    return cases, "OK"


def _is_refused_verdict(verdict):
    # Blockpre exact pins only. Substring matching ("landed" in verdict)
    # would accept forgeries such as "not_landed" as a landed mutation or
    # "refused_spoof" as a refusal, so both directions use exact equality
    # against the probe-emitted strings (geargrafx_block_preblock.py:
    # REFUSED_VERDICT / HWPAGE_BLOCKED_VERDICT / LANDED_VERDICT).
    return verdict in (BLOCKPRE_REFUSED_VERDICT, BLOCKPRE_HWPAGE_VERDICT)


def _st_is_refused_verdict(verdict):
    # ST-path legacy classification (unchanged): the ST probe additionally
    # emits HAZARD_VERDICT ("unsafe_fetch_side_effect_landed_mutation_killed")
    # for hazard mutations, which the ST contract counts as landed. Kept
    # separate so the blockpre exact pins above are not weakened.
    if not isinstance(verdict, str):
        return False
    low = verdict.lower()
    if "landed" in low or "unsafe" in low:
        return False
    return "refus" in low or "fail_closed" in low or "failed_closed" in low


# Pinned #742 blockpre contract (mirrors
# tests/integration/geargrafx_block_preblock.py ARMED_MODES + MUTATIONS).
# Armed 7, mutation 14, positive control via probe exit + summary (not EVIDENCE).
BLOCKPRE_ARMED = frozenset({    "tii-entry", "tdd-unknown", "tin-unknown-far", "tia-wrap-forbidden",
    "tai-mpr2-unknown", "tii-hwpage-fetch-blocked", "block-irq-handler",
})
BLOCKPRE_MUTATIONS = frozenset({
    "tii-entry-unguarded", "tii-entry-no-entry-check", "tii-entry-resume-allowed",
    "tdd-unknown-unguarded", "tdd-unknown-resume-allowed",
    "tin-unknown-far-unguarded", "tin-unknown-far-knownpc-only",
    "tia-wrap-forbidden-unguarded", "tia-wrap-forbidden-knownpc-only",
    "tai-mpr2-unknown-unguarded", "tai-mpr2-unknown-no-recheck",
    "tai-mpr2-unknown-wrong-opcode",
    "tii-hwpage-fetch-blocked-unguarded",
    "block-irq-handler-unguarded",
})
BLOCKPRE_ALWAYS_BLOCKED = frozenset({"tii-hwpage-fetch-blocked-unguarded"})
BLOCKPRE_ALWAYS_BLOCKED_BASE = {
    "tii-hwpage-fetch-blocked-unguarded": "tii-hwpage-fetch-blocked",
}
BLOCKPRE_HWPAGE_ARMED = frozenset({"tii-hwpage-fetch-blocked"})
BLOCKPRE_EXPECTED_EVIDENCE = BLOCKPRE_ARMED | BLOCKPRE_MUTATIONS

# Pinned per-case safety contract, grounded in the measured probe plan
# (tests/integration/geargrafx_block_preblock.py BLOCK_EXPECTED_PC,
# BLOCK_EXECUTED_BEFORE_REFUSAL, BLOCK_OP; the C++ probe's deterministic
# synthetic environment: MPR1=$F8, rest $00, guest TAM changes MPR2 only).
BLOCKPRE_EXPECTED_PC = {
    "tii-entry": 0x3E00,
    "tdd-unknown": 0x3E10,
    "tin-unknown-far": 0x3E20,
    "tia-wrap-forbidden": 0x3E04,
    "tai-mpr2-unknown": 0x4010,
    "tii-hwpage-fetch-blocked": 0x4000,
    "block-irq-handler": 0x3F80,
}
BLOCKPRE_EXPECTED_EXECUTED = {
    "tii-entry": 0,
    "tdd-unknown": 1,
    "tin-unknown-far": 1,
    "tia-wrap-forbidden": 2,
    "tai-mpr2-unknown": 3,
    "tii-hwpage-fetch-blocked": 3,
    "block-irq-handler": 2,
}
BLOCKPRE_EXPECTED_OP = {
    "tii-entry": 0x73,
    "tdd-unknown": 0xC3,
    "tin-unknown-far": 0xD3,
    "tia-wrap-forbidden": 0xE3,
    "tai-mpr2-unknown": 0xF3,
    "tii-hwpage-fetch-blocked": 0x73,
    "block-irq-handler": 0x73,
}
_BLOCKPRE_MPR_INIT = [0x00, 0xF8, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]
BLOCKPRE_MPR_BEFORE = {
    "tii-entry": list(_BLOCKPRE_MPR_INIT),
    "tdd-unknown": list(_BLOCKPRE_MPR_INIT),
    "tin-unknown-far": list(_BLOCKPRE_MPR_INIT),
    "tia-wrap-forbidden": list(_BLOCKPRE_MPR_INIT),
    "tai-mpr2-unknown": list(_BLOCKPRE_MPR_INIT),
    "tii-hwpage-fetch-blocked": list(_BLOCKPRE_MPR_INIT),
    "block-irq-handler": list(_BLOCKPRE_MPR_INIT),
}
BLOCKPRE_MPR_AFTER = {
    "tii-entry": list(_BLOCKPRE_MPR_INIT),
    "tdd-unknown": list(_BLOCKPRE_MPR_INIT),
    "tin-unknown-far": list(_BLOCKPRE_MPR_INIT),
    "tia-wrap-forbidden": [0x00, 0xF8, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x00],
    "tai-mpr2-unknown": [0x00, 0xF8, 0xF8, 0x00, 0x00, 0x00, 0x00, 0x00],
    "tii-hwpage-fetch-blocked": [0x00, 0xF8, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x00],
    "block-irq-handler": list(_BLOCKPRE_MPR_INIT),
}
BLOCKPRE_IRQ_VECTOR = 0x3F80
BLOCKPRE_STACK_CELLS = frozenset({0x21FF, 0x21FE, 0x21FD})
BLOCKPRE_RESUME_ATTEMPTS = 3
# Measured tia-wrap-forbidden transfer (pinned core, preset AR=5/SR=0x3F):
# the 4-byte VDC transfer moves AR 5 -> 2, and the only WRAM delta is the
# single stack cell 0x21FE 0x00 -> 0xFF. An unrelated RAM change is rejected.
# Provenance note: the guest performs no stack operation (LDA/TAM/TIA/BRA)
# and SP never moves, so no guest instruction explains this byte; it is
# repeatable on the pinned core (identical in every tia-wrap run, including
# knownpc-only) and treated as part of the measured transfer signature, not
# as a proved mechanism. Re-check against fresh probe output if the pinned
# core revision ever changes.
BLOCKPRE_TIA_WRAP_VDC_AR_BEFORE = 5
BLOCKPRE_TIA_WRAP_VDC_AR_AFTER = 2
BLOCKPRE_TIA_WRAP_RAM_TRACE = [(0x21FE, 0x00, 0xFF)]
# Expected landed transfer trace per base mode: (address, before, after).
# before comes from the linear fixture (dst $3E90-$3E93 preset to $CC, the
# rest of $3E40-$3E9F filled with F(addr) = (0x20 + addr - 0x3E40) & 0xFF);
# after is the transferred pattern byte. A landed run must contain every
# triple (an IRQ run may add the three architectural stack pushes).
BLOCKPRE_LANDED_TRACE = {
    "tii-entry": [((0x3E90 + i), 0xCC, value) for i, value in enumerate((0x60, 0x61, 0x62, 0x63))],
    "tdd-unknown": [(0x3E90, 0xCC, 0x60), (0x3E8F, 0x6F, 0x5F),
                    (0x3E8E, 0x6E, 0x5E), (0x3E8D, 0x6D, 0x5D)],
    "tin-unknown-far": [(0x3E90, 0xCC, 0x63)],
    "tai-mpr2-unknown": [(0x3E90, 0xCC, 0x60), (0x3E91, 0xCC, 0x61),
                         (0x3E92, 0xCC, 0x60), (0x3E93, 0xCC, 0x61)],
    "block-irq-handler": [((0x3E90 + i), 0xCC, value) for i, value in enumerate((0x60, 0x61, 0x62, 0x63))],
}
# Exact probe-emitted verdict/status/reason strings (no substring matching).
BLOCKPRE_REFUSED_VERDICT = "refused_at_instruction_boundary_before_side_effect"
BLOCKPRE_HWPAGE_VERDICT = "hardware_page_fetch_failed_closed_before_step"
BLOCKPRE_LANDED_VERDICT = "side_effect_landed_monitor_disabled"
BLOCKPRE_ARMED_STATUS = "refused"
BLOCKPRE_ARMED_REASON = "block_opcode_at_instruction_boundary"
BLOCKPRE_HWPAGE_STATUS = "fetch_blocked"
BLOCKPRE_HWPAGE_REASON = "unsafe_opcode_fetch_fail_closed"
BLOCKPRE_HWPAGE_FETCH_PAGE = 0x4000 >> 13  # unpeekable page at the $4000 entry
BLOCKPRE_HWPAGE_FETCH_MPR = 0xFF  # guest TAMs MPR2 to $FF before the $4000 fetch


def _blockpre_mutation_base(mut):
    """Map a mutation case id to its armed base (longest-prefix match)."""
    for base in sorted(BLOCKPRE_ARMED, key=len, reverse=True):
        if mut.startswith(base + "-"):
            return base
    return None


def _blockpre_bad_int(row, name, case):
    value = row.get(name)
    if type(value) is not int:
        return f"ARMED_FIELD_MISSING:{case}:{name}"
    return None


def _blockpre_check_mpr(row, case):
    """Full 8-byte MPR before/after vs the measured plan (missing -> BLOCKED)."""
    before = row.get("before") if isinstance(row.get("before"), dict) else None
    after = row.get("after") if isinstance(row.get("after"), dict) else None
    before_mpr = before.get("mpr") if isinstance(before, dict) else None
    after_mpr = after.get("mpr") if isinstance(after, dict) else None
    for label, mpr in (("before", before_mpr), ("after", after_mpr)):
        if type(mpr) is not list or len(mpr) != 8:
            return "BLOCKED", f"ARMED_MPR_MISSING:{case}:{label}"
        for value in mpr:
            if type(value) is not int or not 0 <= value <= 0xFF:
                return "BLOCKED", f"ARMED_MPR_MISSING:{case}:{label}"
    if before_mpr != BLOCKPRE_MPR_BEFORE[case]:
        return "FAIL", f"ARMED_MPR_BEFORE_CHANGED:{case}"
    if after_mpr != BLOCKPRE_MPR_AFTER[case]:
        return "FAIL", f"ARMED_MPR_CHANGED:{case}"
    return None, None


def _blockpre_check_ram(row, case):
    """Net-diff re-check (never trust net-empty alone: truncation/count gated).

    Missing/wrong-type evidence -> BLOCKED (cannot verify). Truncated diffs are
    never accepted as complete. Any non-stack WRAM delta -> FAIL.
    """
    diffs = row.get("ram_diff")
    if not isinstance(diffs, list):
        return "BLOCKED", f"ARMED_RAM_DIFF_MISSING:{case}"
    truncated = row.get("ram_diff_truncated")
    if type(truncated) is not bool:
        return "BLOCKED", f"ARMED_RAM_DIFF_TRUNCATED_MISSING:{case}"
    if truncated:
        return "BLOCKED", f"ARMED_RAM_DIFF_TRUNCATED:{case}"
    count = row.get("ram_diff_count")
    if type(count) is not int:
        return "BLOCKED", f"ARMED_RAM_DIFF_COUNT_MISSING:{case}"
    if count != len(diffs):
        return "FAIL", f"ARMED_RAM_DIFF_COUNT_MISMATCH:{case}"
    if case == "block-irq-handler":
        if row.get("only_stack_net_diffs") is not True:
            return "FAIL", f"ARMED_IRQ_NOT_STACK_ONLY:{case}"
        for entry in diffs:
            if not isinstance(entry, dict):
                return "BLOCKED", f"ARMED_RAM_DIFF_MALFORMED:{case}"
            address, before, after = entry.get("address"), entry.get("before"), entry.get("after")
            if type(address) is not int or not 0 <= address <= 0xFFFF:
                return "BLOCKED", f"ARMED_RAM_DIFF_MALFORMED:{case}"
            if type(before) is not int or type(after) is not int:
                return "BLOCKED", f"ARMED_RAM_DIFF_MALFORMED:{case}"
            if address not in BLOCKPRE_STACK_CELLS:
                return "FAIL", f"ARMED_RAM_NON_STACK_DELTA:{case}:{address:#x}"
            if before == after:
                return "FAIL", f"ARMED_RAM_DIFF_NOT_A_CHANGE:{case}:{address:#x}"
    elif diffs:
        return "FAIL", f"ARMED_RAM_CHANGED:{case}"
    return None, None


def _blockpre_check_resume(row, case):
    """Resume flatness: 3/3 refusals, no execution or RAM advance."""
    for name in ("resume_attempts", "resume_refused"):
        bad = _blockpre_bad_int(row, name, case)
        if bad is not None:
            return "BLOCKED", bad
    if row["resume_attempts"] != BLOCKPRE_RESUME_ATTEMPTS or \
            row["resume_refused"] != BLOCKPRE_RESUME_ATTEMPTS:
        return "FAIL", f"ARMED_RESUME_NOT_REFUSED:{case}"
    for name in ("executed_at_refusal", "executed_after_resume"):
        bad = _blockpre_bad_int(row, name, case)
        if bad is not None:
            return "BLOCKED", bad
    if row["executed_at_refusal"] != BLOCKPRE_EXPECTED_EXECUTED[case]:
        return "FAIL", f"ARMED_EXECUTED_AT_REFUSAL_MISMATCH:{case}"
    if row["executed_after_resume"] != BLOCKPRE_EXPECTED_EXECUTED[case]:
        return "FAIL", f"ARMED_EXECUTED_AFTER_RESUME:{case}"
    if row.get("ram_unchanged_during_resume") is not True:
        if type(row.get("ram_unchanged_during_resume")) is not bool:
            return "BLOCKED", f"ARMED_RESUME_RAM_FLAG_MISSING:{case}"
        return "FAIL", f"ARMED_RAM_CHANGED_DURING_RESUME:{case}"
    return None, None


def _blockpre_check_fetch_operand(row, case):
    """Fetch/operand evidence: safe peek of the expected opcode, 7 bytes complete."""
    expected_op = BLOCKPRE_EXPECTED_OP[case]
    for name in ("refused_opcode", "fetch_opcode"):
        bad = _blockpre_bad_int(row, name, case)
        if bad is not None:
            return "BLOCKED", bad
    if row["refused_opcode"] != expected_op:
        return "FAIL", f"ARMED_REFUSED_OPCODE_MISMATCH:{case}"
    if row["fetch_opcode"] != expected_op:
        return "FAIL", f"ARMED_FETCH_OPCODE_MISMATCH:{case}"
    if row.get("fetch_safe") is not True:
        if type(row.get("fetch_safe")) is not bool:
            return "BLOCKED", f"ARMED_FETCH_SAFE_MISSING:{case}"
        return "FAIL", f"ARMED_FETCH_UNSAFE:{case}"
    if row.get("fetch_failed") is not False:
        if type(row.get("fetch_failed")) is not bool:
            return "BLOCKED", f"ARMED_FETCH_FAILED_MISSING:{case}"
        return "FAIL", f"ARMED_FETCH_FAILED_SET:{case}"
    operands = row.get("operand_bytes")
    if not isinstance(operands, list) or len(operands) != 7:
        return "BLOCKED", f"ARMED_OPERAND_EVIDENCE_MISSING:{case}"
    for value in operands:
        if type(value) is not int or not 0 <= value <= 0xFF:
            return "BLOCKED", f"ARMED_OPERAND_EVIDENCE_MISSING:{case}"
    if operands[0] != expected_op:
        return "FAIL", f"ARMED_OPERAND_OPCODE_MISMATCH:{case}"
    if row.get("operand_complete") is not True:
        if type(row.get("operand_complete")) is not bool:
            return "BLOCKED", f"ARMED_OPERAND_COMPLETE_MISSING:{case}"
        return "FAIL", f"ARMED_OPERAND_INCOMPLETE:{case}"
    return None, None


def _blockpre_check_irq(row, case):
    """IRQ acceptance evidence: only the handler family may accept an IRQ."""
    accepted = row.get("irq_accepted")
    if type(accepted) is not bool:
        return "BLOCKED", f"ARMED_IRQ_FLAG_MISSING:{case}"
    if case == "block-irq-handler":
        if accepted is not True:
            return "FAIL", f"ARMED_IRQ_NOT_ACCEPTED:{case}"
        # The vector bytes must actually have been read (side-effect-free
        # TryPeek of $FFF6/$FFF7): a claimed vector without a successful read
        # is not acceptance evidence, even if the value looks right.
        if not isinstance(row.get("vector_read"), bool):
            return "BLOCKED", f"ARMED_VECTOR_READ_MISSING:{case}"
        if row["vector_read"] is not True:
            return "FAIL", f"ARMED_VECTOR_READ_FAILED:{case}"
        if row.get("observed_vector") != BLOCKPRE_IRQ_VECTOR:
            if type(row.get("observed_vector")) is not int:
                return "BLOCKED", f"ARMED_IRQ_VECTOR_MISSING:{case}"
            return "FAIL", f"ARMED_IRQ_VECTOR_MISMATCH:{case}"
        if row.get("handler_steps") != 0:
            if type(row.get("handler_steps")) is not int:
                return "BLOCKED", f"ARMED_HANDLER_STEPS_MISSING:{case}"
            return "FAIL", f"ARMED_HANDLER_STEPPED:{case}"
    elif accepted is True:
        return "FAIL", f"ARMED_UNEXPECTED_IRQ:{case}"
    # Handler-step consistency for non-handler families: no handler ran, so
    # the counter must read exactly 0 (missing/wrong-type -> BLOCKED).
    steps = row.get("handler_steps")
    if type(steps) is not int:
        return "BLOCKED", f"ARMED_HANDLER_STEPS_MISSING:{case}"
    if steps != 0:
        return "FAIL", f"ARMED_HANDLER_STEPPED:{case}"
    return None, None


def _blockpre_check_hwpage_row(row, base, case, expect_armed):
    """Fail-closed fetch row re-check (hwpage armed or always-blocked mutation).

    Pins come from the base mode (a "-unguarded" mutation id carries no pins
    of its own); reason labels name the base family. The case id pins the row
    mode, and expect_armed pins the row armed flag (True for the armed case,
    False for the -unguarded mutation). Status/reason/fetch flags are pinned
    to exact probe values (status "fetch_blocked", fetch_blocked True,
    fetch_safe False, fetch_failed True). Missing/wrong-type
    evidence -> BLOCKED; observed side effect or inconsistency -> FAIL.
    """
    if not isinstance(row.get("mode"), str):
        return "BLOCKED", f"ARMED_MODE_MISSING:{base}"
    if row["mode"] != case:
        return "FAIL", f"ARMED_MODE_MISMATCH:{base}"
    if not isinstance(row.get("armed"), bool):
        return "BLOCKED", f"ARMED_FLAG_MISSING:{base}"
    if row["armed"] is not expect_armed:
        return "FAIL", f"ARMED_FLAG_MISMATCH:{base}"
    if not isinstance(row.get("status"), str):
        return "BLOCKED", f"ARMED_HWPAGE_STATUS_MISSING:{base}"
    if row["status"] != BLOCKPRE_HWPAGE_STATUS:
        return "FAIL", f"ARMED_HWPAGE_STATUS_MISMATCH:{base}"
    if not isinstance(row.get("reason"), str):
        return "BLOCKED", f"ARMED_HWPAGE_REASON_MISSING:{base}"
    if row["reason"] != BLOCKPRE_HWPAGE_REASON:
        return "FAIL", f"ARMED_HWPAGE_REASON_MISMATCH:{base}"
    # Fail-closed rows never refuse an opcode: the fetch fails before any
    # boundary decision, so refused stays False, no opcode is captured, no
    # operand capture completes, and the peek path is recorded.
    if not isinstance(row.get("refused"), bool):
        return "BLOCKED", f"ARMED_HWPAGE_REFUSED_FLAG_MISSING:{base}"
    if row["refused"] is not False:
        return "FAIL", f"ARMED_HWPAGE_REFUSED_FLAG_MISMATCH:{base}"
    bad = _blockpre_bad_int(row, "refused_opcode", base)
    if bad is not None:
        return "BLOCKED", bad
    if row["refused_opcode"] != 0:
        return "FAIL", f"ARMED_HWPAGE_REFUSED_OPCODE_MISMATCH:{base}"
    operands = row.get("operand_bytes")
    if not isinstance(operands, list):
        return "BLOCKED", f"ARMED_HWPAGE_OPERAND_MISSING:{base}"
    if operands != []:
        return "FAIL", f"ARMED_HWPAGE_OPERAND_MISMATCH:{base}"
    if not isinstance(row.get("operand_complete"), bool):
        return "BLOCKED", f"ARMED_HWPAGE_OPERAND_COMPLETE_MISSING:{base}"
    if row["operand_complete"] is not False:
        return "FAIL", f"ARMED_HWPAGE_OPERAND_COMPLETE_MISMATCH:{base}"
    if not isinstance(row.get("fetch_via"), str):
        return "BLOCKED", f"ARMED_HWPAGE_FETCH_VIA_MISSING:{base}"
    if row["fetch_via"] != "peek":
        return "FAIL", f"ARMED_HWPAGE_FETCH_VIA_MISMATCH:{base}"
    for name, want in (("fetch_blocked", True), ("fetch_safe", False),
                       ("fetch_failed", True)):
        if not isinstance(row.get(name), bool):
            return "BLOCKED", f"ARMED_FETCH_FLAG_MISSING:{base}:{name}"
        if row[name] is not want:
            return "FAIL", f"ARMED_FETCH_FLAG_MISMATCH:{base}:{name}"
    # Fail-closed fetch targeting: nothing was peekable at the $4000 entry
    # (opcode 0, unpeekable $FF bank), so no device byte could have landed.
    if not isinstance(row.get("fetch_unsafe_bank"), bool):
        return "BLOCKED", f"ARMED_FETCH_UNSAFE_BANK_MISSING:{base}"
    if row["fetch_unsafe_bank"] is not True:
        return "FAIL", f"ARMED_FETCH_UNSAFE_BANK_MISMATCH:{base}"
    bad = _blockpre_bad_int(row, "fetch_opcode", base)
    if bad is not None:
        return "BLOCKED", bad
    if row["fetch_opcode"] != 0:
        return "FAIL", f"ARMED_FETCH_OPCODE_MISMATCH:{base}"
    bad = _blockpre_bad_int(row, "fetch_page", base)
    if bad is not None:
        return "BLOCKED", bad
    if row["fetch_page"] != BLOCKPRE_HWPAGE_FETCH_PAGE:
        return "FAIL", f"ARMED_FETCH_PAGE_MISMATCH:{base}"
    bad = _blockpre_bad_int(row, "fetch_mpr", base)
    if bad is not None:
        return "BLOCKED", bad
    if row["fetch_mpr"] != BLOCKPRE_HWPAGE_FETCH_MPR:
        return "FAIL", f"ARMED_FETCH_MPR_MISMATCH:{base}"
    for name in ("vdc_r_before", "vdc_r_after"):
        bad = _blockpre_bad_int(row, name, base)
        if bad is not None:
            return "BLOCKED", bad
    if row["vdc_r_before"] != row["vdc_r_after"]:
        return "FAIL", f"ARMED_HWPAGE_VDC_CHANGED:{base}:r"
    expected_pc = BLOCKPRE_EXPECTED_PC[base]
    bad = _blockpre_bad_int(row, "stop_pc", base)
    if bad is not None:
        return "BLOCKED", bad
    if row["stop_pc"] != expected_pc:
        return "FAIL", f"ARMED_STOP_PC_MISMATCH:{base}"
    bad = _blockpre_bad_int(row, "instructions_executed", base)
    if bad is not None:
        return "BLOCKED", bad
    if row["instructions_executed"] != BLOCKPRE_EXPECTED_EXECUTED[base]:
        return "FAIL", f"ARMED_EXECUTED_MISMATCH:{base}"
    for name, sentinel in (("vdc_ar_before", 5), ("vdc_ar_after", 5),
                           ("vdc_sr_before", 0x3F), ("vdc_sr_after", 0x3F)):
        bad = _blockpre_bad_int(row, name, base)
        if bad is not None:
            return "BLOCKED", bad
        if row[name] != sentinel:
            return "FAIL", f"ARMED_HWPAGE_VDC_CHANGED:{base}:{name}"
    rb_before, rb_after = row.get("vdc_read_buffer_before"), row.get("vdc_read_buffer_after")
    if type(rb_before) is not int or type(rb_after) is not int:
        return "BLOCKED", f"ARMED_READ_BUFFER_MISSING:{base}"
    if rb_before != rb_after:
        return "FAIL", f"ARMED_READ_BUFFER_CHANGED:{base}"
    for helper in (_blockpre_check_ram, _blockpre_check_mpr,
                   _blockpre_check_resume, _blockpre_check_irq):
        status, reason = helper(row, base)
        if status is not None:
            return status, reason
    return None, None


def _blockpre_check_landed_trace(row, base, case, expected=None):
    """Landed transfer content: every expected triple present, well-formed.

    Non-empty is not enough: stack-only diffs, wrong destinations, forged
    values, truncated lists, and count mismatches never count as a landed
    transfer. The tia-wrap family lands on the VDC instead of RAM (expected
    is then the measured legitimate stack-cell delta, not a transfer triple).
    """
    if expected is None:
        expected = BLOCKPRE_LANDED_TRACE[base]
    diffs = row.get("ram_diff")
    if not isinstance(diffs, list):
        return "BLOCKED", f"MUTATION_TRACE_MALFORMED:{case}"
    if not diffs:
        return "FAIL", f"MUTATION_NO_TRACE:{case}"
    truncated = row.get("ram_diff_truncated")
    if type(truncated) is not bool:
        return "BLOCKED", f"MUTATION_TRACE_TRUNCATED_MISSING:{case}"
    if truncated:
        return "BLOCKED", f"MUTATION_TRACE_TRUNCATED:{case}"
    count = row.get("ram_diff_count")
    if type(count) is not int:
        return "BLOCKED", f"MUTATION_TRACE_COUNT_MISSING:{case}"
    if count != len(diffs):
        return "FAIL", f"MUTATION_TRACE_COUNT_MISMATCH:{case}"
    seen = set()
    for entry in diffs:
        if not isinstance(entry, dict):
            return "BLOCKED", f"MUTATION_TRACE_MALFORMED:{case}"
        address, before, after = entry.get("address"), entry.get("before"), entry.get("after")
        if type(address) is not int or not 0x2000 <= address <= 0x3FFF:
            return "BLOCKED", f"MUTATION_TRACE_MALFORMED:{case}"
        if type(before) is not int or not 0 <= before <= 0xFF:
            return "BLOCKED", f"MUTATION_TRACE_MALFORMED:{case}"
        if type(after) is not int or not 0 <= after <= 0xFF:
            return "BLOCKED", f"MUTATION_TRACE_MALFORMED:{case}"
        if before == after:
            return "FAIL", f"MUTATION_TRACE_NOT_A_CHANGE:{case}:{address:#x}"
        seen.add((address, before, after))
    for triple in expected:
        if triple not in seen:
            return "FAIL", f"MUTATION_TRACE_MISSING:{case}:{triple[0]:#x}"
    if base == "tia-wrap-forbidden" and len(diffs) != len(expected):
        # The measured tia-wrap transfer leaves exactly the one legitimate
        # stack-cell delta; any extra RAM change is unrelated to the transfer.
        return "FAIL", f"MUTATION_TRACE_EXTRA:{case}"
    return None, None


def _blockpre_check_landed_vdc(row, base, case):
    """TIA VDC evidence: exact measured contract, not mere inequality.

    Measured on the pinned core (the wrap-forbidden family presets AR=5 and
    SR=0x3F before the run): the 4-byte VDC transfer moves AR 5 -> 2, SR
    stays 0x3F, R and the read buffer are unchanged. A forged AR value or a
    missing sentinel fails closed.
    """
    for name, want in (("vdc_ar_before", 5), ("vdc_ar_after", 2),
                       ("vdc_sr_before", 0x3F), ("vdc_sr_after", 0x3F)):
        bad = _blockpre_bad_int(row, name, case)
        if bad is not None:
            return "BLOCKED", bad
        if row[name] != want:
            return "FAIL", f"MUTATION_VDC_MISMATCH:{case}:{name}"
    for name in ("vdc_r_before", "vdc_r_after",
                 "vdc_read_buffer_before", "vdc_read_buffer_after"):
        bad = _blockpre_bad_int(row, name, case)
        if bad is not None:
            return "BLOCKED", bad
    if row["vdc_r_before"] != row["vdc_r_after"]:
        return "FAIL", f"MUTATION_VDC_R_CHANGED:{case}"
    if row["vdc_read_buffer_before"] != row["vdc_read_buffer_after"]:
        return "FAIL", f"MUTATION_VDC_READ_BUFFER_CHANGED:{case}"
    return None, None


def _check_blockpre_cases(cases, probe_ok=False, summary_ok=False):
    """Validate pinned #742 blockpre transcript. Returns (verdict, reasons, halt).

    Exact-case contract (no substring classification): EVIDENCE must contain
    exactly the 7 armed + 14 mutation cases, no more, no fewer. Missing ->
    BLOCKED, duplicate is rejected in _parse_evidence, unknown -> BLOCKED
    (never auto-accepted). Positive control is via probe exit + summary
    (pos-write has no EVIDENCE line); without both it is BLOCKED. Partial
    verification never returns PASS, so verify() can never map it to
    VERIFIED_PREBLOCK. ST probe behavior is untouched (see _check_st_cases).

    Armed rows are independently re-judged from raw fields, never trusted via
    the probe "verdict" string alone: expected stop/refused PC, opcode,
    instruction count, fetch/operand evidence, resume flatness, net RAM diff
    (truncation and count gated, non-stack deltas rejected), full 8-byte MPR
    before/after, and IRQ acceptance are all pinned per case ID. Missing or
    wrong-typed evidence -> BLOCKED; an observed side effect or an internal
    inconsistency -> FAIL.
    """
    reasons = []
    if set(cases) != BLOCKPRE_EXPECTED_EVIDENCE:
        missing = sorted(BLOCKPRE_EXPECTED_EVIDENCE - set(cases))
        extra = sorted(set(cases) - BLOCKPRE_EXPECTED_EVIDENCE)
        detail = []
        if missing:
            detail.append("MISSING:" + ",".join(missing))
        if extra:
            detail.append("UNKNOWN:" + ",".join(extra))
        return BLOCKED, [f"BLOCKPRE_CASE_SET_MISMATCH:{';'.join(detail)}"], {}
    armed_ok = mut_ok = 0
    halt = {}
    for case in sorted(cases):
        item = cases[case]
        row = item.get("row") if isinstance(item.get("row"), dict) else None
        if item.get("core_revision") != PINNED or item.get("bios") != "SYNTHETIC":
            return FAIL, [f"PROVENANCE_MISMATCH:{case}"], {}
        if item.get("observation_kind") != "REAL_CORE_EXECUTION":
            return BLOCKED, [f"OBSERVATION_KIND:{case}"], {}
        if row is None:
            return BLOCKED, [f"ROW_MISSING:{case}"], {}
        verdict = item.get("verdict")
        if case in BLOCKPRE_ALWAYS_BLOCKED:
            if verdict != BLOCKPRE_HWPAGE_VERDICT:
                return FAIL, [f"HWPAGE_EXPECTED_BLOCKED:{case}:{verdict}"], {}
            status, reason = _blockpre_check_hwpage_row(
                row, BLOCKPRE_ALWAYS_BLOCKED_BASE[case], case, False)
            if status is not None:
                return status, [reason], {}
            mut_ok += 1
        elif case in BLOCKPRE_ARMED:
            if case in BLOCKPRE_HWPAGE_ARMED:
                if verdict != BLOCKPRE_HWPAGE_VERDICT:
                    return FAIL, [f"ARMED_HWPAGE_NOT_BLOCKED:{case}:{verdict}"], {}
                status, reason = _blockpre_check_hwpage_row(row, case, case, True)
                if status is not None:
                    return status, [reason], {}
            else:
                # Exact verdict pin: the probe emits exactly one refusal
                # string for armed runs. A rewritten "success" verdict or a
                # lookalike ("not_landed", "refused_spoof") never passes.
                if verdict != BLOCKPRE_REFUSED_VERDICT:
                    return FAIL, [f"ARMED_VERDICT_MISMATCH:{case}:{verdict}"], {}
                # Independent re-judgment of the armed raw row: pin the mode,
                # armed/refused flags, status/reason, and the expected
                # PC/opcode/instruction count per case ID instead of trusting
                # the probe's verdict string alone. Missing/wrong-type ->
                # BLOCKED (cannot verify); observed side effect or
                # inconsistency -> FAIL.
                if not isinstance(row.get("mode"), str):
                    return BLOCKED, [f"ARMED_MODE_MISSING:{case}"], {}
                if row["mode"] != case:
                    return FAIL, [f"ARMED_MODE_MISMATCH:{case}"], {}
                if not isinstance(row.get("armed"), bool):
                    return BLOCKED, [f"ARMED_FLAG_MISSING:{case}"], {}
                if row["armed"] is not True:
                    return FAIL, [f"ARMED_FLAG_MISMATCH:{case}"], {}
                if not isinstance(row.get("refused"), bool):
                    return BLOCKED, [f"ARMED_REFUSED_FLAG_MISSING:{case}"], {}
                if row["refused"] is not True:
                    return FAIL, [f"ARMED_REFUSED_FLAG_MISMATCH:{case}"], {}
                if not isinstance(row.get("status"), str):
                    return BLOCKED, [f"ARMED_STATUS_MISSING:{case}"], {}
                if row["status"] != BLOCKPRE_ARMED_STATUS:
                    return FAIL, [f"ARMED_STATUS_MISMATCH:{case}"], {}
                if not isinstance(row.get("reason"), str):
                    return BLOCKED, [f"ARMED_REASON_MISSING:{case}"], {}
                if row["reason"] != BLOCKPRE_ARMED_REASON:
                    return FAIL, [f"ARMED_REASON_MISMATCH:{case}"], {}
                if row.get("fetch_blocked") is not False:
                    if not isinstance(row.get("fetch_blocked"), bool):
                        return BLOCKED, [f"ARMED_FETCH_FLAG_MISSING:{case}:fetch_blocked"], {}
                    return FAIL, [f"ARMED_FETCH_FLAG_MISMATCH:{case}:fetch_blocked"], {}
                expected_pc = BLOCKPRE_EXPECTED_PC[case]
                for name in ("refused_pc", "stop_pc"):
                    bad = _blockpre_bad_int(row, name, case)
                    if bad is not None:
                        return BLOCKED, [bad], {}
                if row["refused_pc"] != expected_pc:
                    return FAIL, [f"ARMED_REFUSED_PC_MISMATCH:{case}"], {}
                if row["stop_pc"] != expected_pc:
                    return FAIL, [f"ARMED_STOP_PC_MISMATCH:{case}"], {}
                bad = _blockpre_bad_int(row, "instructions_executed", case)
                if bad is not None:
                    return BLOCKED, [bad], {}
                if row["instructions_executed"] != BLOCKPRE_EXPECTED_EXECUTED[case]:
                    return FAIL, [f"ARMED_EXECUTED_MISMATCH:{case}"], {}
                for helper in (_blockpre_check_fetch_operand, _blockpre_check_resume,
                               _blockpre_check_ram, _blockpre_check_mpr, _blockpre_check_irq):
                    status, reason = helper(row, case)
                    if status is not None:
                        return status, [reason], {}
            for name in ("vdc_ar_before", "vdc_ar_after", "vdc_sr_before",
                         "vdc_sr_after", "vdc_r_before", "vdc_r_after"):
                bad = _blockpre_bad_int(row, name, case)
                if bad is not None:
                    return BLOCKED, [bad], {}
            if row["vdc_ar_before"] != row["vdc_ar_after"]:
                return FAIL, [f"ARMED_VDC_CHANGED:{case}:ar"], {}
            if row["vdc_sr_before"] != row["vdc_sr_after"]:
                return FAIL, [f"ARMED_VDC_CHANGED:{case}:sr"], {}
            if row["vdc_r_before"] != row["vdc_r_after"]:
                return FAIL, [f"ARMED_VDC_CHANGED:{case}:r"], {}
            rb_before, rb_after = row.get("vdc_read_buffer_before"), row.get("vdc_read_buffer_after")
            if type(rb_before) is not int or type(rb_after) is not int:
                return BLOCKED, [f"ARMED_READ_BUFFER_MISSING:{case}"], {}
            if rb_before != rb_after:
                return FAIL, [f"ARMED_READ_BUFFER_CHANGED:{case}"], {}
            if not isinstance(row.get("stop_pc"), int):
                return BLOCKED, [f"ARMED_STOP_PC_MISSING:{case}"], {}
            armed_ok += 1
        elif case in BLOCKPRE_MUTATIONS:
            if _is_refused_verdict(verdict):
                return FAIL, [f"MUTATION_REFUSED:{case}"], {}
            # Exact landed pin: "not_landed" and other lookalikes must not
            # count as a landed sensitivity proof.
            if verdict != BLOCKPRE_LANDED_VERDICT:
                return BLOCKED, [f"MUTATION_INCONCLUSIVE:{case}:{verdict}"], {}
            # Independent re-judgment of the landed raw row (not verdict-only):
            # the run must identify itself, show a real refusal-latch state,
            # execute past the armed boundary, and leave an observable trace.
            base = _blockpre_mutation_base(case)
            if base is None:
                return BLOCKED, [f"MUTATION_NO_BASE:{case}"], {}
            if not isinstance(row.get("mode"), str):
                return BLOCKED, [f"MUTATION_MODE_MISSING:{case}"], {}
            if row["mode"] != case:
                return FAIL, [f"MUTATION_MODE_MISMATCH:{case}"], {}
            if not isinstance(row.get("refused"), bool):
                return BLOCKED, [f"MUTATION_REFUSED_FLAG_MISSING:{case}"], {}
            want_refused = case.endswith("-resume-allowed")
            if row["refused"] is not want_refused:
                return FAIL, [f"MUTATION_REFUSED_FLAG_MISMATCH:{case}"], {}
            # Latch state: only the pure -unguarded runs execute unmonitored;
            # every bypass variant runs with the monitor armed. Landed runs
            # halt cleanly (the guest ends in a self-loop), never as refused
            # or fetch-blocked.
            want_armed = not case.endswith("-unguarded")
            if not isinstance(row.get("armed"), bool):
                return BLOCKED, [f"MUTATION_ARMED_FLAG_MISSING:{case}"], {}
            if row["armed"] is not want_armed:
                return FAIL, [f"MUTATION_ARMED_FLAG_MISMATCH:{case}"], {}
            if not isinstance(row.get("status"), str):
                return BLOCKED, [f"MUTATION_STATUS_MISSING:{case}"], {}
            if row["status"] != "ok":
                return FAIL, [f"MUTATION_STATUS_MISMATCH:{case}"], {}
            bad = _blockpre_bad_int(row, "instructions_executed", case)
            if bad is not None:
                return BLOCKED, [bad], {}
            if row["instructions_executed"] <= BLOCKPRE_EXPECTED_EXECUTED[base]:
                return FAIL, [f"MUTATION_NO_PROGRESS:{case}"], {}
            if case.endswith("-resume-allowed"):
                # The latch holds at first: initial refusal at the expected PC
                # with an exact stop at PC+7, then 3 unrefused resume steps
                # that advance the count by exactly 3.
                for name in ("refused_pc", "stop_pc"):
                    bad = _blockpre_bad_int(row, name, case)
                    if bad is not None:
                        return BLOCKED, [bad], {}
                if row["refused_pc"] != BLOCKPRE_EXPECTED_PC[base]:
                    return FAIL, [f"MUTATION_REFUSED_PC_MISMATCH:{case}"], {}
                if row["stop_pc"] != BLOCKPRE_EXPECTED_PC[base] + 7:
                    return FAIL, [f"MUTATION_STOP_PC_MISMATCH:{case}"], {}
                for name in ("resume_refused", "resume_attempts",
                             "executed_at_refusal", "executed_after_resume"):
                    bad = _blockpre_bad_int(row, name, case)
                    if bad is not None:
                        return BLOCKED, [bad], {}
                if row["resume_refused"] != 0:
                    return FAIL, [f"MUTATION_RESUME_HELD:{case}"], {}
                if row["resume_attempts"] != BLOCKPRE_RESUME_ATTEMPTS:
                    return FAIL, [f"MUTATION_RESUME_COUNT_MISMATCH:{case}"], {}
                if row["executed_at_refusal"] != row["instructions_executed"] - BLOCKPRE_RESUME_ATTEMPTS:
                    return FAIL, [f"MUTATION_EXECUTED_AT_REFUSAL_MISMATCH:{case}"], {}
                if row["executed_after_resume"] != row["instructions_executed"]:
                    return FAIL, [f"MUTATION_EXECUTED_AFTER_RESUME_MISMATCH:{case}"], {}
            if base == "tia-wrap-forbidden":
                status, reason = _blockpre_check_landed_vdc(row, base, case)
                if status is not None:
                    return status, [reason], {}
                status, reason = _blockpre_check_landed_trace(
                    row, base, case, BLOCKPRE_TIA_WRAP_RAM_TRACE)
            else:
                status, reason = _blockpre_check_landed_trace(row, base, case)
            if status is not None:
                return status, [reason], {}
            mut_ok += 1
        else:
            return BLOCKED, [f"UNKNOWN_CASE:{case}"], {}
        halt[case] = {"stop_pc": row.get("stop_pc"),
                      "executed": row.get("instructions_executed"),
                      "sr_before": row.get("vdc_sr_before"),
                      "sr_after": row.get("vdc_sr_after"),
                      "status": row.get("status")}
    if armed_ok != len(BLOCKPRE_ARMED):
        return BLOCKED, [f"ARMED_COUNT_{armed_ok}_EXPECTED_{len(BLOCKPRE_ARMED)}"], {}
    if mut_ok != len(BLOCKPRE_MUTATIONS):
        return BLOCKED, [f"MUT_COUNT_{mut_ok}_EXPECTED_{len(BLOCKPRE_MUTATIONS)}"], {}
    if not (probe_ok and summary_ok):
        return BLOCKED, ["NO_POS_CONTROL"], {}
    reasons.append("POS_CONTROL_BY_PROBE_EXIT")
    reasons = reasons + [f"ARMED_{armed_ok}_MUT_{mut_ok}_POS_1"]
    return PASS, reasons, halt


# Pinned #365 ST case set, grounded in the measured probe plan
# (tests/integration/geargrafx_st_boundary.py ARMED_MODES with 17 entries and
# MUTATIONS with 22 runs; HWPAGE_MODE/BBR3_TAKEN_MODE are the two dynamic
# hardware-page entries). Positive control is via probe exit + summary
# (pos-write has no EVIDENCE line), exactly like the blockpre contract.
ST_ARMED = frozenset({
    "st0-entry", "st1-entry", "st2-entry",
    "st1-unknown", "st2-unknown",
    "st1-branch-unknown",
    "st0-jsr-rts",
    "st1-page-boundary",
    "st0-rom-page0",
    "st1-unknown-far",
    "st2-mpr2-unknown",
    "st1-unused-bank",
    "st0-bbr3-hwpage-skip",
    "irq2-handler-st0-survey",
    "irq2-core-stop-survey",
    "st0-hwpage-fetch-blocked",
    "st0-bbr3-hwpage-taken",
})
ST_HWPAGE_ARMED = frozenset({
    "st0-hwpage-fetch-blocked",
    "st0-bbr3-hwpage-taken",
})
ST_MUTATIONS = frozenset({
    "st0-entry-no-entry-check",
    "st1-entry-no-entry-check",
    "st2-entry-no-entry-check",
    "st1-unknown-unguarded",
    "st2-unknown-unguarded",
    "st1-branch-unknown-resume-allowed",
    "st0-jsr-rts-knownpc-only",
    "st1-page-boundary-knownpc-only",
    "st0-rom-page0-knownpc-only",
    "st1-unknown-far-knownpc-only",
    "st2-mpr2-unknown-no-recheck",
    "st2-mpr2-unknown-wrong-opcode",
    "st0-hwpage-fetch-blocked-unsafe-read",
    "st0-hwpage-fetch-blocked-peek-fail-step",
    "st0-hwpage-fetch-blocked-no-hazard-latch",
    "st0-bbr3-hwpage-taken-unsafe-read",
    "st0-bbr3-hwpage-taken-peek-fail-step",
    "st0-bbr3-hwpage-taken-no-hazard-latch",
    "st0-bbr3-hwpage-skip-knownpc-only",
    "st1-unused-bank-knownpc-only",
    "irq2-handler-st0-survey-knownpc-only",
    "irq2-core-stop-survey-knownpc-only",
})
ST_EXPECTED_EVIDENCE = ST_ARMED | ST_MUTATIONS


def _check_st_cases(cases, probe_ok=False, summary_ok=False):
    """Validate st boundary transcripts. Returns (verdict, reasons, summary).

    Exact-case contract (no substring classification): EVIDENCE must contain
    exactly the 17 armed + 22 mutation cases, no more, no fewer. Missing ->
    BLOCKED, duplicate is rejected in _parse_evidence, unknown -> BLOCKED
    (never auto-accepted: a foreign probe's case IDs can never sneak in, and a
    partial 1-armed+1-mutation transcript can never PASS). Case roles come only
    from exact set membership: fail-closed hardware-page entries, refused armed
    runs (VDC SR unchanged, recorded stop PC), and landed mutations
    (sensitivity proof, hazard shapes included). Positive control is via probe
    exit + summary only; without both it is BLOCKED. Partial verification
    never returns PASS, so verify() can never map it to VERIFIED_PREBLOCK.

    Executed-instruction counts vary by family (entry/setup steps) and are
    recorded, not gated here.
    """
    reasons = []
    if set(cases) != ST_EXPECTED_EVIDENCE:
        missing = sorted(ST_EXPECTED_EVIDENCE - set(cases))
        extra = sorted(set(cases) - ST_EXPECTED_EVIDENCE)
        detail = []
        if missing:
            detail.append("MISSING:" + ",".join(missing))
        if extra:
            detail.append("UNKNOWN:" + ",".join(extra))
        return BLOCKED, [f"ST_CASE_SET_MISMATCH:{';'.join(detail)}"], {}
    armed_ok = mut_ok = pos_ok = 0
    halt = {}
    for case, item in sorted(cases.items()):
        row = item.get("row") if isinstance(item.get("row"), dict) else None
        if item.get("core_revision") != PINNED or item.get("bios") != "SYNTHETIC":
            return FAIL, [f"PROVENANCE_MISMATCH:{case}"], {}
        if item.get("observation_kind") != "REAL_CORE_EXECUTION":
            return BLOCKED, [f"OBSERVATION_KIND:{case}"], {}
        if row is None:
            return BLOCKED, [f"ROW_MISSING:{case}"], {}
        verdict = item.get("verdict")
        if case in ST_HWPAGE_ARMED:
            # Fail-closed fetch does not depend on the monitor: no step is
            # issued at an unpeekable PC. Expect the blocked verdict, plus the
            # raw-row invariants (unchanged VDC SR, recorded stop PC); a
            # side-effecting row with the right verdict string must not PASS.
            if verdict != "hardware_page_fetch_failed_closed_before_step":
                return FAIL, [f"HWPAGE_EXPECTED_BLOCKED:{case}:{verdict}"], {}
            for name in ("vdc_sr_before", "vdc_sr_after", "stop_pc"):
                if type(row.get(name)) is not int:
                    return BLOCKED, [f"ARMED_FIELD_MISSING:{case}:{name}"], {}
            if row["vdc_sr_before"] != row["vdc_sr_after"]:
                return FAIL, [f"ARMED_VDC_CHANGED:{case}"], {}
            armed_ok += 1
        elif case in ST_ARMED:
            if not _st_is_refused_verdict(verdict):
                if isinstance(verdict, str) and "landed" in verdict.lower():
                    return FAIL, [f"ARMED_SIDE_EFFECT:{case}"], {}
                return BLOCKED, [f"ARMED_VERDICT_UNKNOWN:{case}:{verdict}"], {}
            for name in ("vdc_sr_before", "vdc_sr_after", "stop_pc"):
                if type(row.get(name)) is not int:
                    return BLOCKED, [f"ARMED_FIELD_MISSING:{case}:{name}"], {}
            if row["vdc_sr_before"] != row["vdc_sr_after"]:
                return FAIL, [f"ARMED_VDC_CHANGED:{case}"], {}
            armed_ok += 1
        elif case in ST_MUTATIONS:
            # Sensitivity: mutation must make the effect observable (not refused).
            if _st_is_refused_verdict(verdict):
                return FAIL, [f"MUTATION_REFUSED:{case}"], {}
            if not (isinstance(verdict, str) and "landed" in verdict.lower()):
                return BLOCKED, [f"MUTATION_INCONCLUSIVE:{case}:{verdict}"], {}
            mut_ok += 1
        else:
            return BLOCKED, [f"UNKNOWN_CASE:{case}"], {}
        halt[case] = {"stop_pc": row.get("stop_pc"),
                      "executed": row.get("instructions_executed"),
                      "sr_before": row.get("vdc_sr_before"),
                      "sr_after": row.get("vdc_sr_after"),
                      "status": row.get("status")}
    if armed_ok != len(ST_ARMED):
        return BLOCKED, [f"ARMED_COUNT_{armed_ok}_EXPECTED_{len(ST_ARMED)}"], {}
    if mut_ok != len(ST_MUTATIONS):
        return BLOCKED, [f"MUT_COUNT_{mut_ok}_EXPECTED_{len(ST_MUTATIONS)}"], {}
    # The st probe asserts the positive control internally (check_pos
    # raises on failure -> nonzero exit, already gated above) and reports
    # it only in the human summary, not as an EVIDENCE line. Accept it iff
    # the probe exited 0 with the summary present; label the evidence
    # strength explicitly - this is weaker than per-case EVIDENCE.
    if probe_ok and summary_ok:
        reasons.append("POS_CONTROL_BY_PROBE_EXIT")
        pos_ok = 1
    else:
        return BLOCKED, ["NO_POS_CONTROL"], {}
    reasons = reasons + [f"ARMED_{armed_ok}_MUT_{mut_ok}_POS_{pos_ok}"]
    return PASS, reasons, halt


def _check_mmio_summary(stdout):
    """Validate the mmio1804 probe PASS summary independently of its classifier.

    The probe emits no EVIDENCE lines; the harness re-checks the exact printed
    contract values (RED/GREEN verdicts, stop PC, executed count, $1804
    readback, positive-control landing). Any deviation is BLOCKED, never PASS.
    """
    reasons = []
    if "PASS issue-173 $1804 preblock" not in stdout:
        return BLOCKED, ["MMIO_NO_PASS_LINE"], {}
    if "RED=blocked_side_effect_before_stop" not in stdout:
        return BLOCKED, ["MMIO_RED_MISMATCH"], {}
    if "GREEN=preblocked_before_side_effect" not in stdout:
        return BLOCKED, ["MMIO_GREEN_MISMATCH"], {}
    for token in ("stop_pc=0x3e02", "instructions_executed=1", "$1804 stayed 0x00",
                  "control store landed", "slot $01 BLOCKED"):
        if token not in stdout:
            return BLOCKED, [f"MMIO_SUMMARY_MISMATCH:{token}"], {}
    if "LIMIT:" not in stdout or "known code" not in stdout:
        return BLOCKED, ["MMIO_LIMIT_MISSING"], {}
    reasons.append("MMIO_KNOWN_PC_ONLY")
    halt = {"stop_pc": 0x3E02, "executed": 1, "mmio_rst": 0x00}
    return PASS, reasons, halt


BLOCK_ARMED_VERDICTS = {
    "per_byte_refusal_continues_and_restores": "cross",
    "refusal_skips_source_read": "srcreadff",
}
BLOCK_UNGUARDED_VERDICTS = {
    "forbidden_bytes_land_without_hook": "cross",
    "source_read_side_effect_lands_without_hook": "srcread",
    "forbidden_source_read_and_write_land_without_hook": "srcreadff",
}
BLOCK_LIMIT_VERDICTS = {
    "source_read_side_effect_lands_on_allowed_transfer": "srcread",
}


def _check_block_cases(cases):
    """Validate block-midrefusal EVIDENCE plus raw-row re-checks.

    Armed refusal facts are recorded, but the overall verdict stays BLOCKED:
    a per-byte refusal is not a whole-instruction safe stop
    (mid_instruction_stop BLOCKED_NOT_SAFE_TO_RESUME), and the srcread-armed
    case proves a source-read side effect the guard cannot prevent. The
    harness re-checks stop PC, MPR stability, S balance and A/X/Y restore
    from the raw row instead of trusting the probe classifier alone.
    """
    reasons = []
    halt = {}
    armed = mut = 0
    limit_hit = False
    for case, item in sorted(cases.items()):
        row = item.get("row") if isinstance(item.get("row"), dict) else None
        if item.get("core_revision") != PINNED or item.get("bios") != "SYNTHETIC":
            return FAIL, [f"PROVENANCE_MISMATCH:{case}"], {}
        if item.get("observation_kind") != "REAL_CORE_EXECUTION":
            return BLOCKED, [f"OBSERVATION_KIND:{case}"], {}
        if row is None:
            return BLOCKED, [f"ROW_MISSING:{case}"], {}
        verdict = item.get("verdict")
        variant = case.split("-")[1] if "-" in case else ""
        # Independent raw-row re-checks (not the probe classifier).
        # Missing/wrong-type evidence -> BLOCKED (cannot verify); an observed
        # side effect or inconsistency -> FAIL.
        stop_pc, expected_end = row.get("stop_pc"), row.get("expected_end_pc")
        if type(stop_pc) is not int or type(expected_end) is not int:
            return BLOCKED, [f"STOP_PC_MISSING:{case}"], {}
        if stop_pc != expected_end:
            return FAIL, [f"STOP_PC_MISMATCH:{case}"], {}
        before = row.get("before") if isinstance(row.get("before"), dict) else {}
        after = row.get("after") if isinstance(row.get("after"), dict) else {}
        before_mpr, after_mpr = before.get("mpr"), after.get("mpr")
        if not isinstance(before_mpr, list) or not isinstance(after_mpr, list):
            return BLOCKED, [f"MPR_MISSING:{case}"], {}
        if before_mpr != after_mpr:
            return FAIL, [f"MPR_CHANGED:{case}"], {}
        sp = row.get("sp")
        if not isinstance(sp, list):
            return BLOCKED, [f"STACK_MISSING:{case}"], {}
        if sp != [4, 4]:
            return FAIL, [f"STACK_UNBALANCED:{case}"], {}
        axy = [row.get("a_after"), row.get("x_after"), row.get("y_after")]
        if any(type(value) is not int for value in axy):
            return BLOCKED, [f"AXY_MISSING:{case}"], {}
        if axy != [0x12, 0x34, 0x07]:
            return FAIL, [f"AXY_NOT_RESTORED:{case}"], {}
        if case.endswith("-armed"):
            if verdict in BLOCK_ARMED_VERDICTS:
                if BLOCK_ARMED_VERDICTS[verdict] != variant:
                    return BLOCKED, [f"ARMED_VARIANT_MISMATCH:{case}"], {}
                if verdict == "per_byte_refusal_continues_and_restores":
                    refusals = row.get("guard_refusals", 0)
                    if type(refusals) is not int:
                        return BLOCKED, [f"ARMED_REFUSAL_COUNT_MISSING:{case}"], {}
                    if refusals < 1:
                        return BLOCKED, [f"ARMED_NO_REFUSAL:{case}"], {}
                armed += 1
            elif verdict in BLOCK_LIMIT_VERDICTS:
                # srcread-armed: the source-read side effect lands even when
                # armed. Documented LIMIT - never counts as pre-block proof.
                limit_hit = True
            else:
                return BLOCKED, [f"ARMED_VERDICT_UNKNOWN:{case}:{verdict}"], {}
        else:
            if verdict not in BLOCK_UNGUARDED_VERDICTS:
                return BLOCKED, [f"UNGUARDED_VERDICT_UNKNOWN:{case}:{verdict}"], {}
            mut += 1
        halt[case] = {"stop_pc": row.get("stop_pc"), "verdict": verdict,
                      "refusals": row.get("guard_refusals"),
                      "mpr_stable": before.get("mpr") == after.get("mpr")}
    if armed == 0:
        return BLOCKED, ["NO_ARMED_CASES"], {}
    if mut == 0:
        return BLOCKED, ["NO_UNGUARDED_CASES"], {}
    reasons.append(f"ARMED_{armed}_UNGUARDED_{mut}")
    if limit_hit:
        reasons.append("SRCREAD_LIMIT_DOCUMENTED")
    reasons.append("MID_INSTRUCTION_STOP_BLOCKED_NOT_SAFE_TO_RESUME")
    return BLOCKED, reasons, halt


def verify(probe, timeout, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = f"h45-{int(time.time())}-{os.getpid()}"
    script = PROBES[probe]
    try:
        proc, elapsed = _run_probe(script, timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"verdict": BLOCKED, "reasons": [f"SPAWN_FAILED:{error}"],
                "run_id": run_id, "probe": probe}
    if proc.returncode != 0:
        return {"verdict": FAIL, "reasons": ["PROBE_EXIT_NONZERO"],
                "run_id": run_id, "probe": probe,
                "stderr_tail": proc.stderr[-2000:]}
    if probe == "mmio":
        # The mmio probe emits no EVIDENCE lines; its contract is the exact
        # PASS summary plus exit 0 (its internal classifier + mutation suite
        # already ran). The harness re-checks the summary tokens above.
        verdict, reasons, halt = _check_mmio_summary(proc.stdout)
        scope = "SYNTHETIC_KNOWN_PC_ONLY_NO_GATE_CHANGE"
        cases = {}
    elif probe == "blockpre":
        cases, detail = _parse_evidence(proc.stdout)
        if cases is None:
            return {"verdict": BLOCKED, "reasons": [detail],
                    "run_id": run_id, "probe": probe}
        summary_ok = ("positive control:" in proc.stdout
                      and "PASS issue-742" in proc.stdout)
        verdict, reasons, halt = _check_blockpre_cases(
            cases, probe_ok=True, summary_ok=summary_ok)
        scope = "SYNTHETIC_SINGLE_STEP_ONLY_NO_GATE_CHANGE"
    elif probe == "st":
        cases, detail = _parse_evidence(proc.stdout)
        if cases is None:
            return {"verdict": BLOCKED, "reasons": [detail],
                    "run_id": run_id, "probe": probe}
        summary_ok = ("positive control:" in proc.stdout
                      and "PASS issue-365" in proc.stdout)
        verdict, reasons, halt = _check_st_cases(cases, probe_ok=True, summary_ok=summary_ok)
        scope = "SYNTHETIC_SINGLE_STEP_ONLY_NO_GATE_CHANGE"
    else:
        cases, detail = _parse_evidence(proc.stdout)
        if cases is None:
            return {"verdict": BLOCKED, "reasons": [detail],
                    "run_id": run_id, "probe": probe}
        verdict, reasons, halt = _check_block_cases(cases)
        scope = "SYNTHETIC_MID_TRANSFER_NO_SAFE_STOP_NO_GATE_CHANGE"
    record = {"verdict": ("VERIFIED_PREBLOCK" if verdict == PASS else verdict),
              "reasons": reasons, "run_id": run_id, "probe": probe,
              "elapsed_s": round(elapsed, 1), "cases": len(cases), "halt": halt,
              "execution": "BLOCKED", "execution_permitted": False,
              "jsr_count": 0, "release_eligible": False,
              "scope": scope}
    (output_dir / f"{probe}-{run_id}.json").write_text(
        json.dumps(record, indent=1, sort_keys=True), encoding="utf-8")
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", choices=[*PROBES, "all"], default="st")
    parser.add_argument("--output", required=True)
    parser.add_argument("--timeout", type=int, default=1500)
    args = parser.parse_args(argv)
    probes = list(PROBES) if args.probe == "all" else [args.probe]
    results = {}
    for name in probes:
        record = verify(name, args.timeout, args.output)
        results[name] = record
        print(json.dumps({k: record.get(k) for k in
                          ("verdict", "reasons", "probe", "cases", "elapsed_s")},
                         sort_keys=True))
    worst = FAIL if any(r["verdict"] == FAIL for r in results.values()) else \
        BLOCKED if any(r["verdict"] == BLOCKED for r in results.values()) else PASS
    if worst != PASS:
        print(f"H4/H5 real-core verify: {worst} (see transcript; gate unchanged)", file=sys.stderr)
        return 1 if worst == FAIL else 3
    print("H4/H5 real-core verify: VERIFIED_PREBLOCK synthetic single-step only; gate unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
