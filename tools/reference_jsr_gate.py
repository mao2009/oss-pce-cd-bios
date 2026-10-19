#!/usr/bin/env python3
"""Machine-readable reference-JSR execution gate, kept separate from claim status (Issue #228).

Three concepts stay apart: the *claim* (contract status in spec/api_contracts.json, never
VERIFIED), the *verification* (executed_callee / NOT_RUN in the same ledger) and the *execution
gate* (this module plus spec/reference_jsr_gate.json). Every slot defaults to BLOCKED. A ledger
entry can only restate BLOCKED with release_eligible=false and per-hazard control status; it
cannot unlock anything. An external approval record can at most reach MATERIALS_COMPLETE, which
still grants no execution because this repository has no reference-JSR runner (runner NONE).
MODEL_ELIGIBLE (#178/#191), host PASS and self-declared flags are never accepted as approval.
Synthetic inputs only: no emulator, no BIOS, no ROM byte is read.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER_PATH = ROOT / "spec" / "reference_jsr_gate.json"
CLAIM_LEDGER = "spec/api_contracts.json"
SCHEMA = "oss-pce-cd-bios/reference-jsr-gate/v1"
EXIT_BLOCKED = 3  # repo BLOCKED convention
HAZARDS = tuple(f"H{i}" for i in range(1, 8))  # same hazard ids as tools/cdreset_safety_preflight.py
CONTROL_STATES = ("CONTROLLED", "PARTIAL", "UNCONTROLLED")
ENTRY_KEYS = frozenset({"candidate_entry", "execution", "release_eligible", "hazard_controls"})
APPROVAL_KEYS = frozenset({"reviewer", "pr", "abi_status", "hazard_controls", "callee_run_evidence"})
VERIFICATION_NOT_RUN = {"status": "NOT_RUN", "executed_callee": False, "evidence": []}


def validate_ledger(ledger, contracts):
    """Fail closed (ValueError) on any ledger that could carry an unlock or drift from the claims."""
    def require(ok, message):
        if not ok:
            raise ValueError(message)

    require(isinstance(ledger, dict) and set(ledger) == {"schema", "claim_ledger", "slots"}, "ledger keys")
    require(ledger["schema"] == SCHEMA and ledger["claim_ledger"] == CLAIM_LEDGER, "ledger schema/ref")
    require(isinstance(ledger["slots"], dict), "slots must be an object")
    apis = contracts["apis"]
    for key, entry in ledger["slots"].items():
        require(key.isdigit() and 0 <= int(key) < len(apis), f"slot key {key!r} out of range")
        slot = int(key)
        require(isinstance(entry, dict) and set(entry) == ENTRY_KEYS, f"slot {key}: entry keys")
        require(entry["execution"] == "BLOCKED" and entry["release_eligible"] is False,
                f"slot {key}: ledger may only restate BLOCKED / release_eligible=false")
        require(entry["candidate_entry"] == apis[slot]["candidate_entry"] == f"0x{0xE000 + 3 * slot:04X}",
                f"slot {key}: candidate entry drift from claim ledger")
        controls = entry["hazard_controls"]
        require(isinstance(controls, dict) and set(controls) == set(HAZARDS), f"slot {key}: hazard ids")
        require(all(v in CONTROL_STATES for v in controls.values()), f"slot {key}: control state")


def resolve(slot, ledger, contracts, approval=None):
    """Return the execution-gate record for one slot. Never returns ALLOWED."""
    reasons = []
    entry = ledger["slots"].get(str(slot))
    if entry is None:
        reasons.append("NO_REVIEWED_GATE_RECORD")
    else:
        reasons += [f"LEDGER_{h}_{entry['hazard_controls'][h]}" for h in HAZARDS
                    if entry["hazard_controls"][h] != "CONTROLLED"]
    api = contracts["apis"][slot]
    if api["verification"] != VERIFICATION_NOT_RUN:
        reasons.append("CALLEE_VERIFICATION_NOT_RUN")
    if approval is None:
        reasons.append("NO_INDEPENDENT_APPROVAL")
    elif not isinstance(approval, dict):
        reasons.append("APPROVAL_MALFORMED")
    else:
        reasons += [f"UNRECOGNIZED_APPROVAL_FIELD_{k}" for k in sorted(set(approval) - APPROVAL_KEYS)]
        reviewer, pr = approval.get("reviewer"), approval.get("pr")
        if not (isinstance(reviewer, str) and reviewer):
            reasons.append("APPROVAL_REVIEWER_MISSING")
        if not (type(pr) is int and pr > 0):
            reasons.append("APPROVAL_PR_INVALID")
        if approval.get("abi_status") != "REVIEWED":
            reasons.append("ABI_NOT_REVIEWED")
        controls = approval.get("hazard_controls")
        if not (isinstance(controls, dict) and set(controls) == set(HAZARDS)):
            reasons.append("APPROVAL_HAZARD_CONTROLS_MALFORMED")
        else:
            reasons += [f"APPROVAL_{h}_NOT_CONTROLLED" for h in HAZARDS if controls[h] != "CONTROLLED"]
        evidence = approval.get("callee_run_evidence")
        if not (isinstance(evidence, list) and evidence):
            reasons.append("NO_CALLEE_RUN_EVIDENCE")
    return {
        "slot": f"${slot:02X}",
        "candidate_entry": api["candidate_entry"],
        "execution": "BLOCKED" if reasons else "MATERIALS_COMPLETE",
        "runner": "NONE",
        "jsr_count": 0,
        "release_eligible": False,
        "reasons": reasons,
    }


def load(ledger_path=LEDGER_PATH, contracts_path=ROOT / "spec" / "api_contracts.json"):
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    contracts = json.loads(contracts_path.read_text(encoding="utf-8"))
    validate_ledger(ledger, contracts)
    return ledger, contracts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="evaluate every slot with no approval")
    arguments = parser.parse_args(argv)
    if not arguments.check:
        parser.error("--check is required")
    try:
        ledger, contracts = load()
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(f"BLOCKED: gate ledger unusable: {error}")
        return EXIT_BLOCKED
    records = [resolve(slot, ledger, contracts) for slot in range(len(contracts["apis"]))]
    blocked = sum(record["execution"] == "BLOCKED" for record in records)
    print(f"BLOCKED: reference JSR execution gate closed for {blocked}/{len(records)} slots; jsr_count=0")
    print(json.dumps({"slot_01": records[1], "blocked": blocked, "total": len(records)},
                     ensure_ascii=False, indent=2))
    return EXIT_BLOCKED if blocked else 0


if __name__ == "__main__":
    sys.exit(main())
