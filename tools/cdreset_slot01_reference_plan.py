#!/usr/bin/env python3
"""Slot-$01 media present/absent observation plan + fail-closed execution gate (Issue #181).

Two LOCAL_ONLY observation records for the single slot-$01 / $E003 API are derived from the
Issue #180 fixtures: every observation value is UNKNOWN (nothing was executed), every
expectation stays tagged HYPOTHESIS/UNKNOWN and execution stays NOT_RUN. The gate judges
structured materials only: (a) the pinned-core $1804 pre-side-effect result (#194 measured
BLOCKED), (b) the #178/#191 hazard posture (MODEL_ELIGIBLE is eligibility, never execution
permission), (c) ST/IRQ/MPR/alias unproven flags, (d) independent-review approval evidence.
A self-declared ``safety_gate_approved`` flag is never evidence. No emulator, no reference
BIOS JSR and no ROM byte is read: ``--check`` prints NOT_RUN + BLOCKED (exit 3) for the real
materials. A synthetic complete input flips the verdict (falsifiable), and even then this
module has no execution path and never emits a permission, result or verification verdict.
"""
import argparse

import cdreset_safety_preflight as safety
from cdreset_slot01_fixture import CASES, UNKNOWN

EXIT_BLOCKED = 3  # repo BLOCKED convention (tools/cdreset_safety_preflight.py)
PLAN_SCHEMA = "oss-pce-cd-bios/cdreset-slot01-observation-plan/v1"
SLOT, ENTRY_CANDIDATE = "$01", "$E003"
PLAN_FIELDS = frozenset({"schema", "case_id", "slot", "entry_candidate", "media",
                         "observation", "expectation", "execution"})
#: Fact-only observation fields. A value other than UNKNOWN is a guess and is rejected.
OBSERVATION_FIELDS = ("caller_pc", "jsr_pc", "return_pc", "A", "X", "Y", "P", "sp", "mpr",
                      "ram_diff", "bus_state", "timeout", "core_sha256")
#: Untested ABI: expectations carry a tag and are never mixed with observation values (#10).
EXPECTATIONS = {"entry": "HYPOTHESIS", "returns": "HYPOTHESIS", "stack": "HYPOTHESIS",
                "clobbered": "UNKNOWN", "mpr": "HYPOTHESIS", "ram": "HYPOTHESIS",
                "irq": "UNKNOWN", "errors": "HYPOTHESIS", "timing": "UNKNOWN"}
EXPECTATION_TAGS = ("HYPOTHESIS", "UNKNOWN")
MMIO_BLOCKED = "blocked_side_effect_before_stop"  # #194 real-core measurement
MMIO_PROVEN = "pre_side_effect_rejection_proven"  # never observed by this module
MMIO_STATES = (MMIO_BLOCKED, "not_measured", MMIO_PROVEN)
UNPROVEN_FLAGS = ("st0_st1_st2", "irq_registers", "mpr_change", "bank_alias")
REVIEW_FIELDS = frozenset({"reviewer", "pr", "label"})
REVIEW_LABELS = ("approved",)

#: The real materials today: #194 measured $1804 pre-trapping BLOCKED, the #170 posture with
#: the UNKNOWN slot-$01 ABI, ST/IRQ unproven (#178 H4/H5) and no independent-review evidence.
#: MPR change and bank alias are covered by the #170 guard (H1/H2), so their flags are false.
CURRENT_MATERIALS = {
    "mmio_1804": MMIO_BLOCKED,
    "posture": safety.CURRENT_POSTURE,
    "abi": safety.CURRENT_ABI,
    "unproven": {"st0_st1_st2": True, "irq_registers": True, "mpr_change": False, "bank_alias": False},
    "review": None,
    "self_declared_gate": False,
}
MATERIAL_FIELDS = tuple(CURRENT_MATERIALS)


def build_plan(case):
    """One NOT_RUN observation record derived from an Issue #180 fixture case."""
    return {"schema": PLAN_SCHEMA, "case_id": case["case_id"], "slot": case["slot"],
            "entry_candidate": case["entry_candidate"], "media": case["media"],
            "observation": {field: UNKNOWN for field in OBSERVATION_FIELDS},
            "expectation": dict(EXPECTATIONS), "execution": "NOT_RUN"}


def validate_plan(record):
    """Structural errors for one observation record. A guessed observation, an untagged
    expectation, a media state without a fixture case or any API other than $01/$E003 is
    rejected instead of being reported as evidence."""
    if not isinstance(record, dict) or set(record) != PLAN_FIELDS:
        return ["invalid plan fields"]
    errors = []
    if record["schema"] != PLAN_SCHEMA:
        errors.append("unknown schema")
    if (record["slot"], record["entry_candidate"]) != (SLOT, ENTRY_CANDIDATE):
        errors.append("only the slot $01 / $E003 API may be planned; every other API stays forbidden")
    if record["media"] not in ("present", "absent"):
        errors.append("media must be present or absent")
    if not any(case["case_id"] == record["case_id"] and case["media"] == record["media"]
               for case in CASES):
        errors.append("case_id/media must match a fixture case")
    observation = record["observation"]
    if not isinstance(observation, dict) or set(observation) != set(OBSERVATION_FIELDS):
        errors.append(f"observation fields must be exactly {sorted(OBSERVATION_FIELDS)}")
    else:
        errors += [f"observation {field} must be UNKNOWN: a guess is not evidence"
                   for field, value in observation.items() if type(value) is not str or value != UNKNOWN]
    expectation = record["expectation"]
    if not isinstance(expectation, dict) or set(expectation) != set(EXPECTATIONS):
        errors.append(f"expectation fields must be exactly {sorted(EXPECTATIONS)}")
    else:
        errors += [f"expectation {field} must be tagged HYPOTHESIS or UNKNOWN"
                   for field, value in expectation.items() if value not in EXPECTATION_TAGS]
    if record["execution"] != "NOT_RUN":
        errors.append("execution must be NOT_RUN")
    return errors


def validate_materials(materials):
    """Structural errors for the gate materials; missing or mistyped evidence fails closed."""
    if not isinstance(materials, dict) or set(materials) != set(MATERIAL_FIELDS):
        return [f"materials must be exactly {sorted(MATERIAL_FIELDS)}"]
    errors = []
    if materials["mmio_1804"] not in MMIO_STATES:
        errors.append(f"mmio_1804 must be one of {MMIO_STATES}")
    unproven = materials["unproven"]
    if (not isinstance(unproven, dict) or set(unproven) != set(UNPROVEN_FLAGS)
            or any(type(value) is not bool for value in unproven.values())):
        errors.append(f"unproven must be exactly {sorted(UNPROVEN_FLAGS)} as bools")
    review = materials["review"]
    if review is not None:
        if not isinstance(review, dict) or set(review) != REVIEW_FIELDS:
            errors.append("review evidence must be exactly {reviewer, pr, label} or null")
        else:
            if not isinstance(review["reviewer"], str) or not review["reviewer"].strip():
                errors.append("review evidence reviewer must be a non-empty string")
            if type(review["pr"]) is not int or review["pr"] <= 0:
                errors.append("review evidence pr must be a positive int")
            if review["label"] not in REVIEW_LABELS:
                errors.append(f"review evidence label must be one of {REVIEW_LABELS}")
    if type(materials["self_declared_gate"]) is not bool:
        errors.append("self_declared_gate must be a bool")
    errors += safety.validate_posture(materials["posture"])
    errors += safety.validate_abi(materials["abi"])
    return errors


def decide(materials):
    """Pure gate decision on validated materials.

    Returns ``(verdict, reasons)`` with one reason per unmet material, fail closed. The verdict
    is ``BLOCKED`` unless *every* material is satisfied; only then does it become
    ``MATERIALS_COMPLETE`` (the gate decision would permit an execution attempt) — which the
    real materials can never reach today, so this is a falsifiability property, not a release.
    The #191 ``MODEL_ELIGIBLE`` posture result is accepted only as material (b); it is not
    execution permission and is never echoed as a verdict here.
    """
    errors = validate_materials(materials)
    if errors:
        return "BLOCKED", [f"malformed_configuration: {error}" for error in errors]
    reasons = []
    if materials["mmio_1804"] != MMIO_PROVEN:
        detail = ("measured blocked_side_effect_before_stop on the pinned core (#194)"
                  if materials["mmio_1804"] == MMIO_BLOCKED else materials["mmio_1804"])
        reasons.append(f"mmio_1804_pre_side_effect_rejection_not_established: {detail}")
    hazard_verdict, hazard_reasons = safety.decide(materials["posture"], materials["abi"])
    if hazard_verdict != "MODEL_ELIGIBLE":
        reasons.append("hazard_posture_incomplete: " + " | ".join(hazard_reasons))
    reasons += [f"unproven_{flag}: no pre-side-effect guarantee"
                for flag in UNPROVEN_FLAGS if materials["unproven"][flag]]
    if materials["review"] is None:
        reasons.append("independent_review_missing: reviewer/PR/label approval evidence absent")
        if materials["self_declared_gate"]:
            reasons.append("self_declared_gate_not_evidence: safety_gate_approved=true alone "
                           "never unlocks slot $01")
    return ("MATERIALS_COMPLETE", []) if not reasons else ("BLOCKED", reasons)


def evaluate(records, materials):
    """Return ``(execution, verdict, reasons)``. ``execution`` is always ``NOT_RUN``: this
    module has no execution path (no emulator, no JSR, no ROM byte) and never releases one.
    Malformed records or materials fail closed to ``BLOCKED`` with each reason listed."""
    if not isinstance(records, (list, tuple)) or not records:
        errors = ["plan: at least one observation record is required"]
    else:
        errors = [f"plan[{index}]: {error}" for index, record in enumerate(records)
                  for error in validate_plan(record)]
    errors += [f"materials: {error}" for error in validate_materials(materials)]
    if errors:
        return "NOT_RUN", "BLOCKED", [f"malformed_configuration: {error}" for error in errors]
    verdict, reasons = decide(materials)
    return "NOT_RUN", verdict, reasons


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="evaluate the two built-in observation records and the real materials")
    arguments = parser.parse_args(argv)
    if not arguments.check:
        parser.error("--check is required")
    execution, verdict, reasons = evaluate([build_plan(case) for case in CASES], CURRENT_MATERIALS)
    print(f"{execution}: 2 slot $01 media present/absent observation records; no run, no ROM read")
    if verdict == "BLOCKED":
        print("BLOCKED: " + "; ".join(reasons))
    else:  # unreachable for the real materials; still fail closed, never print a permission
        print("MATERIALS_COMPLETE: every gate material satisfied; no execution path exists here")
    return EXIT_BLOCKED


if __name__ == "__main__":
    raise SystemExit(main())
