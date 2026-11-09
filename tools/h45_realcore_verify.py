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
    if not isinstance(verdict, str):
        return False
    low = verdict.lower()
    if "landed" in low or "unsafe" in low:
        return False
    return "refus" in low or "fail_closed" in low or "failed_closed" in low


# Pinned #742 blockpre contract (mirrors
# tests/integration/geargrafx_block_preblock.py ARMED_MODES + MUTATIONS).
# Armed 7, mutation 14, positive control via probe exit + summary (not EVIDENCE).
BLOCKPRE_ARMED = frozenset({
    "tii-entry", "tdd-unknown", "tin-unknown-far", "tia-wrap-forbidden",
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
BLOCKPRE_HWPAGE_ARMED = frozenset({"tii-hwpage-fetch-blocked"})
BLOCKPRE_EXPECTED_EVIDENCE = BLOCKPRE_ARMED | BLOCKPRE_MUTATIONS


def _check_blockpre_cases(cases, probe_ok=False, summary_ok=False):
    """Validate pinned #742 blockpre transcript. Returns (verdict, reasons, halt).

    Exact-case contract (no substring classification): EVIDENCE must contain
    exactly the 7 armed + 14 mutation cases, no more, no fewer. Missing ->
    BLOCKED, duplicate is rejected in _parse_evidence, unknown -> BLOCKED
    (never auto-accepted). Positive control is via probe exit + summary
    (pos-write has no EVIDENCE line); without both it is BLOCKED. Partial
    verification never returns PASS, so verify() can never map it to
    VERIFIED_PREBLOCK. ST probe behavior is untouched (see _check_st_cases).
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
            if verdict != "hardware_page_fetch_failed_closed_before_step":
                return FAIL, [f"HWPAGE_EXPECTED_BLOCKED:{case}:{verdict}"], {}
            mut_ok += 1
        elif case in BLOCKPRE_ARMED:
            if case in BLOCKPRE_HWPAGE_ARMED:
                if verdict != "hardware_page_fetch_failed_closed_before_step":
                    return FAIL, [f"ARMED_HWPAGE_NOT_BLOCKED:{case}:{verdict}"], {}
            else:
                if not _is_refused_verdict(verdict):
                    if isinstance(verdict, str) and "landed" in verdict.lower():
                        return FAIL, [f"ARMED_SIDE_EFFECT:{case}"], {}
                    return BLOCKED, [f"ARMED_VERDICT_UNKNOWN:{case}:{verdict}"], {}
            if row.get("vdc_sr_before") != row.get("vdc_sr_after"):
                return FAIL, [f"ARMED_VDC_CHANGED:{case}"], {}
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
    if armed_ok != len(BLOCKPRE_ARMED):
        return BLOCKED, [f"ARMED_COUNT_{armed_ok}_EXPECTED_{len(BLOCKPRE_ARMED)}"], {}
    if mut_ok != len(BLOCKPRE_MUTATIONS):
        return BLOCKED, [f"MUT_COUNT_{mut_ok}_EXPECTED_{len(BLOCKPRE_MUTATIONS)}"], {}
    if not (probe_ok and summary_ok):
        return BLOCKED, ["NO_POS_CONTROL"], {}
    reasons.append("POS_CONTROL_BY_PROBE_EXIT")
    reasons = reasons + [f"ARMED_{armed_ok}_MUT_{mut_ok}_POS_1"]
    return PASS, reasons, halt


def _check_st_cases(cases, probe_ok=False, summary_ok=False, always_blocked=()):
    """Validate st/blockpre boundary transcripts. Returns (verdict, reasons, summary).

    Armed-vs-control contract (probe classifiers are authoritative for counts):
    armed runs must carry a refused-family verdict with VDC SR unchanged and a
    recorded stop PC; mutations must carry a landed verdict (sensitivity proof).
    Executed-instruction counts vary by family (entry/setup steps) and are
    recorded, not gated here.
    """
    reasons = []
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
        if case == "pos-write" or case.startswith("pos-"):
            if not (isinstance(verdict, str) and "landed" in verdict.lower()) and verdict not in ("PASS", "pass"):
                # Positive control must have run unrefused; probe exit code
                # already gates this - unknown verdict strings stay BLOCKED.
                return BLOCKED, [f"POS_CONTROL_NOT_LANDED:{case}:{verdict}"], {}
            pos_ok += 1
            continue
        is_mut = any(s in case for s in ("-unguarded", "-no-entry-check", "-no-recheck",
                                         "-wrong-opcode", "-knownpc-only", "-resume-allowed",
                                         "-unsafe-read", "-peek-fail-step", "-no-hazard-latch"))
        if case in always_blocked:
            # Fail-closed fetch does not depend on the monitor: even unguarded,
            # no step is issued at an unpeekable PC. Expect the blocked verdict.
            if verdict != "hardware_page_fetch_failed_closed_before_step":
                return FAIL, [f"HWPAGE_EXPECTED_BLOCKED:{case}:{verdict}"], {}
            mut_ok += 1
        elif is_mut:
            # Sensitivity: mutation must make the effect observable (not refused).
            if _is_refused_verdict(verdict):
                return FAIL, [f"MUTATION_REFUSED:{case}"], {}
            if not (isinstance(verdict, str) and "landed" in verdict.lower()):
                return BLOCKED, [f"MUTATION_INCONCLUSIVE:{case}:{verdict}"], {}
            mut_ok += 1
        else:
            if not _is_refused_verdict(verdict):
                if isinstance(verdict, str) and "landed" in verdict.lower():
                    return FAIL, [f"ARMED_SIDE_EFFECT:{case}"], {}
                return BLOCKED, [f"ARMED_VERDICT_UNKNOWN:{case}:{verdict}"], {}
            if row.get("vdc_sr_before") != row.get("vdc_sr_after"):
                return FAIL, [f"ARMED_VDC_CHANGED:{case}"], {}
            if not isinstance(row.get("stop_pc"), int):
                return BLOCKED, [f"ARMED_STOP_PC_MISSING:{case}"], {}
            armed_ok += 1
        halt[case] = {"stop_pc": row.get("stop_pc"),
                      "executed": row.get("instructions_executed"),
                      "sr_before": row.get("vdc_sr_before"),
                      "sr_after": row.get("vdc_sr_after"),
                      "status": row.get("status")}
    if armed_ok == 0:
        return BLOCKED, ["NO_ARMED_CASES"], {}
    if mut_ok == 0:
        return BLOCKED, ["NO_MUTATION_CASES"], {}
    if pos_ok == 0:
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
        if row.get("stop_pc") != row.get("expected_end_pc"):
            return FAIL, [f"STOP_PC_MISMATCH:{case}"], {}
        if not isinstance(row.get("stop_pc"), int):
            return BLOCKED, [f"STOP_PC_MISSING:{case}"], {}
        before = row.get("before") if isinstance(row.get("before"), dict) else {}
        after = row.get("after") if isinstance(row.get("after"), dict) else {}
        if before.get("mpr") != after.get("mpr"):
            return FAIL, [f"MPR_CHANGED:{case}"], {}
        if row.get("sp") != [4, 4]:
            return FAIL, [f"STACK_UNBALANCED:{case}"], {}
        if [row.get("a_after"), row.get("x_after"), row.get("y_after")] != [0x12, 0x34, 0x07]:
            return FAIL, [f"AXY_NOT_RESTORED:{case}"], {}
        if case.endswith("-armed"):
            if verdict in BLOCK_ARMED_VERDICTS:
                if BLOCK_ARMED_VERDICTS[verdict] != variant:
                    return BLOCKED, [f"ARMED_VARIANT_MISMATCH:{case}"], {}
                if verdict == "per_byte_refusal_continues_and_restores":
                    if row.get("guard_refusals", 0) < 1:
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
