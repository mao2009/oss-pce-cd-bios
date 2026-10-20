#!/usr/bin/env python3
"""Issue #361: pre-side-effect blocking plan V01-V08 on the pinned real core (synthetic inputs only).

Orchestrates EXISTING real-core probe runs (ST paths, neg/pos write guard, TAM) from
``geargrafx_st_bypass`` and ``geargrafx_reference_guard`` and maps each planned check to a verdict.
No core code is copied and no core hook is added here.

Verdicts per case:
* PASS    - measured, every planned sub-check prevented the side effect before it landed.
* BLOCKED - the ST side effect lands before any stop (the measured expectation for W05-W07), or the
            evidence is missing: the probe refused to run, emitted no observation (none, or empty), or
            timed out. Missing evidence is never a PASS.
* PARTIAL - only a measured subset of the planned sub-checks ran; the rest is listed in the detail.
            V06 (TAM) is PARTIAL because TAM is a detection-only check at the instruction boundary,
            not a block: the MPR write itself is not stopped, and other MPR paths are unaudited.
            V05 measures stores that were dropped and detected; that is a measurement, not a PREVENTED
            claim (#360 W02 stays UNKNOWN: no content-validated run id is recorded).
* NOT_RUN - no probe mode / audit exists in this PR; nothing was executed for the case.
* FAIL    - a probe run that did run and contradicted its expectation: a V05 store that landed (not dropped),
            a V05 store not detected, a V01/V02 ST store that was prevented (#360 records W05-W07 as
            prevents NO, so a prevention is a contradiction), or a V06 TAM not detected.

Scope: this script covers V01-V08 only. An overall PASS is NOT a gate release (ゲート解放ではない).
本スクリプトは PASS を出力しない（各 V-case の上限 PARTIAL。総合 PASS は本PRの構造上到達不能）。ゲート解放の経路はない。
Per case: V03/V04/V07/V08 are NOT_RUN (no probe mode or audit here); V01/V02 are BLOCKED on a measured landing;
V05/V06 are at most PARTIAL. The slot-1e gate stays BLOCKED (spec/reference_jsr_gate.json) regardless of this output.
V08 is the observation schema, not a W row. W-ids are grouped in W_MAP: RUN (a run V-case), PARTIAL (the
unrun remainder of a run V-case), NOT_RUN (mapped to a NOT_RUN V-case), UNMAPPED (no V-case at all, not run here).
W18 is an inventory row on #360 (spec/write_path_inventory.json: UNKNOWN / UNKNOWN / NOT_MEASURED, gate BLOCKED);
here it is only the unrun part of V05, and it is never reported as prevented or measured.
Verdicts are never merged: PASS, PARTIAL, NOT_RUN, BLOCKED and FAIL are printed as separate counts.

Exit status: 0 only when the overall verdict is PASS; BLOCKED and FAIL both exit 1.
Run by the parent on Linux after ``make setup``:
    python3 tests/integration/geargrafx_preside_probe.py
On Windows or WSL-from-worker the run is BLOCKED (exit 1); a refusal is never a PASS.
"""
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import geargrafx_reference_guard as guard  # noqa: E402
import geargrafx_st_bypass as st  # noqa: E402
from geargrafx_api import compile_probe  # noqa: E402

PASS, BLOCKED, PARTIAL, NOT_RUN, FAIL = "PASS", "BLOCKED", "PARTIAL", "NOT_RUN", "FAIL"
PROBE_SOURCE = "geargrafx_reference_jsr.cpp"
# The inventory's allowlisted window (#360 W16). Addresses inside it are permitted by design and are
# never planned as V05 targets: a store there lands by design, so it is not a block to test.
ALLOW_START, ALLOW_END = guard.ALLOW
# W02 targets: the three that the existing neg-write probe runs; the rest of the plan is unrun.
V05_RUN = (0x0000, 0x1FFF, 0x4000)
# Non-allowlisted page starts and ends (8 KiB pages), i.e. the planned V05 set. $2000 and $3FFF are
# inside [$2000,$4000) and are excluded.
V05_PLANNED = tuple(sorted(a for a in {p * 0x2000 for p in range(8)} | {p * 0x2000 + 0x1FFF for p in range(8)}
                           if not ALLOW_START <= a < ALLOW_END))
# Every inventory id W01-W18 (spec/write_path_inventory.json on #360) and its place in this plan.
# RUN: a V-case that executes a probe. PARTIAL: the unrun remainder of a RUN V-case (W18 = V05 minus V05_RUN,
# an inventory row on #360, UNKNOWN). NOT_RUN: mapped to a V-case that is fixed NOT_RUN.
# UNMAPPED: no V-case at all; reported here, never run.
W_MAP = {
    "W01": ("UNMAPPED", None), "W02": ("RUN", "V05"), "W03": ("RUN", "V06"),
    "W04": ("UNMAPPED", None), "W05": ("RUN", "V01"), "W06": ("RUN", "V02"),
    "W07": ("RUN", "V02"), "W08": ("UNMAPPED", None), "W09": ("UNMAPPED", None),
    "W10": ("UNMAPPED", None), "W11": ("UNMAPPED", None), "W12": ("UNMAPPED", None),
    "W13": ("UNMAPPED", None), "W14": ("NOT_RUN", "V03"), "W15": ("NOT_RUN", "V04"),
    "W16": ("UNMAPPED", None), "W17": ("NOT_RUN", "V07"), "W18": ("PARTIAL", "V05"),
}

# (case id, inventory row, kind, parameters, reason when not runnable)
CASES = (
    ("V01", "W05 ST0 address register", "st", ("st0",), None),
    ("V02", "W06/W07 ST1/ST2 data registers", "st", ("st1", "st2"), None),
    ("V03", "W14 interrupt entry", "none", (),
     "no probe mode runs a CLI/IRQ entry; a pre-handler hook does not exist on the pinned core"),
    ("V04", "W15 block-transfer instructions", "none", (),
     "no probe mode executes TII/TIN/TDD/TIA/TAI; mid-instruction stop behaviour unmeasured"),
    ("V05", "W02 measured subset (W18 remainder unrun)", "neg", V05_PLANNED, None),
    ("V06", "W03 MPR changes", "tam", (), None),
    ("V07", "W17 device-origin RAM writes", "none", (),
     "core-source audit not performed in this PR (pinned source is outside the repository)"),
    ("V08", "observation schema (IRQ UNKNOWN or OBSERVED)", "none", (),
     "tools/jsr_observation.py (from #360) defines the schema only; no real-core observation is recorded here"),
)


class MissingEvidence(Exception):
    """A probe run gave no usable observation (refused, no output, timeout). Judged BLOCKED."""


def measure(probe, bios, cue, mode, target):
    """Run one real-core probe mode. Any run that did not produce an observation is MissingEvidence."""
    try:
        row = guard.run_mode(probe, bios, cue, mode, target)
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        raise MissingEvidence(str(error)) from error
    if row.get("exit", 0) != 0:
        raise MissingEvidence(f"{mode} ${target:04X} probe refused to run (exit {row.get('exit')})")
    # A run that prints nothing (exit 0 and no fields) is no observation at all, not a contradiction.
    if not {key for key in row if key != "exit"}:
        raise MissingEvidence(f"{mode} ${target:04X} probe emitted an empty observation")
    return row


def run_case(case, probe, bios, cue):
    """Return (case id, verdict, detail). Calls the real probe only for runnable kinds."""
    case_id, label, kind, params, reason = case
    try:
        if kind == "st":
            verdicts = [st.check_st(measure(probe, bios, cue, mode, 0), mode) for mode in params]
            if set(verdicts) != {st.BLOCKED_VERDICT}:
                raise ValueError(f"unexpected ST verdicts {verdicts}")
            return case_id, BLOCKED, f"{label}: side effect lands before the stop (measured)"
        if kind == "neg":
            for target in V05_RUN:
                guard.check_neg(measure(probe, bios, cue, "neg-write", target), target)
            missing = [f"${t:04X}" for t in V05_PLANNED if t not in V05_RUN]
            return case_id, PARTIAL, (f"{label}: store dropped and detected at {len(V05_RUN)} targets "
                                      "(measured only; no PREVENTED claim, #360 W02 stays UNKNOWN); "
                                      f"unrun W18 remainder {', '.join(missing)}")
        if kind == "tam":
            guard.check_tam(measure(probe, bios, cue, "tam", 0))
            return case_id, PARTIAL, (f"{label}: TAM detected before the next instruction "
                                      "(検出のみ、遮断ではない); other MPR paths unaudited")
    except MissingEvidence as error:
        return case_id, BLOCKED, f"{label}: no evidence ({error})"
    except ValueError as error:
        return case_id, FAIL, f"{label}: {error}"
    return case_id, NOT_RUN, f"{label}: {reason}"


def overall(verdicts):
    """PASS only when every case is PASS; anything else keeps the gate BLOCKED."""
    return PASS if verdicts and all(v == PASS for v in verdicts) else BLOCKED


def main():
    if sys.platform != "linux":  # pinned Geargrafx core is a Linux .so; never skip silently
        print(f"BLOCKED: real-core probe is Linux-pinned (platform {sys.platform}); not PASS")
        print("OVERALL BLOCKED (release_eligible=false; V01-V08 のみ; ゲート解放ではない)")
        return 1
    with tempfile.TemporaryDirectory(prefix="pce-preside-") as tmp:
        directory = Path(tmp)
        bios, cue = guard.synthetic_media(directory)
        probe = compile_probe(directory, source_name=PROBE_SOURCE, target_name="preside-probe")
        results = [run_case(case, probe, bios, cue) for case in CASES]
    for case_id, verdict, detail in results:
        print(f"{case_id} {verdict}: {detail}")
    for group in ("RUN", "PARTIAL", "NOT_RUN", "UNMAPPED"):
        members = [f"{w}->{c}" if c else w for w, (g, c) in W_MAP.items() if g == group]
        print(f"W-MAP {group}: {', '.join(members) or '-'}")
    print("COUNTS " + " ".join(
        f"{v}={sum(1 for _, x, _ in results if x == v)}" for v in (PASS, PARTIAL, NOT_RUN, BLOCKED, FAIL)))
    verdict = overall([v for _, v, _ in results])
    print(f"OVERALL {verdict} (release_eligible=false; V01-V08 のみ; ゲート解放ではない)")
    return 0 if verdict == PASS else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        print(f"BLOCKED pre-side-effect probe error: {error}", file=sys.stderr)
        raise SystemExit(1) from error
