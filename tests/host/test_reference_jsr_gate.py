"""Host tests for the machine-readable reference-JSR execution gate (Issue #228).

Synthetic only: exercises ``tools/reference_jsr_gate.py`` (pure functions + ``--check`` CLI) and
the committed ledger. No emulator, no reference BIOS and no reference JSR are involved. The
gate must stay BLOCKED for slot $01 and every other slot; claims in spec/api_contracts.json must
not be readable as permission, and MODEL_ELIGIBLE / PASS / self-declared flags must never unlock.
"""
import copy
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import reference_jsr_gate as G

LEDGER, CONTRACTS = G.load()
HAZARD_CONTROLLED = {h: "CONTROLLED" for h in G.HAZARDS}
APPROVAL = {"reviewer": "reviewer-a", "pr": 228, "abi_status": "REVIEWED",
            "hazard_controls": HAZARD_CONTROLLED, "callee_run_evidence": ["synthetic-record-1"]}


def complete_ledger():
    ledger = copy.deepcopy(LEDGER)
    ledger["slots"]["1"]["hazard_controls"] = dict(HAZARD_CONTROLLED)
    return ledger


class ReferenceJsrGateTest(unittest.TestCase):
    def test_committed_ledger_validates_and_all_slots_blocked_without_approval(self):
        records = [G.resolve(slot, LEDGER, CONTRACTS) for slot in range(81)]
        self.assertEqual({r["execution"] for r in records}, {"BLOCKED"})
        self.assertEqual({r["jsr_count"] for r in records}, {0})
        self.assertEqual({r["release_eligible"] for r in records}, {False})
        self.assertEqual({r["runner"] for r in records}, {"NONE"})

    def test_unreviewed_slot_defaults_to_blocked_with_reason(self):
        record = G.resolve(0, LEDGER, CONTRACTS)
        self.assertEqual(record["execution"], "BLOCKED")
        self.assertIn("NO_REVIEWED_GATE_RECORD", record["reasons"])

    def test_slot_01_stays_blocked_even_with_complete_approval(self):
        # Ledger hazards H3/H4/H5 are not controlled, so no approval can open slot $01.
        record = G.resolve(1, LEDGER, CONTRACTS, APPROVAL)
        self.assertEqual((record["slot"], record["candidate_entry"]), ("$01", "0xE003"))
        self.assertEqual(record["execution"], "BLOCKED")
        for reason in ("LEDGER_H3_PARTIAL", "LEDGER_H4_UNCONTROLLED", "LEDGER_H5_UNCONTROLLED"):
            self.assertIn(reason, record["reasons"])

    def test_complete_materials_reach_materials_complete_never_allowed(self):
        record = G.resolve(1, complete_ledger(), CONTRACTS, APPROVAL)
        self.assertEqual(record["execution"], "MATERIALS_COMPLETE")
        self.assertEqual(record["reasons"], [])
        self.assertEqual(record["runner"], "NONE")
        self.assertNotIn("ALLOWED", json.dumps(record))

    def test_missing_or_unapproved_evidence_fails_closed(self):
        cases = (
            (None, "NO_INDEPENDENT_APPROVAL"),
            (["reviewer-a"], "APPROVAL_MALFORMED"),
            ({**APPROVAL, "reviewer": ""}, "APPROVAL_REVIEWER_MISSING"),
            ({**APPROVAL, "pr": True}, "APPROVAL_PR_INVALID"),
            ({**APPROVAL, "pr": 0}, "APPROVAL_PR_INVALID"),
            ({**APPROVAL, "abi_status": "UNKNOWN"}, "ABI_NOT_REVIEWED"),
            ({**APPROVAL, "hazard_controls": {"H1": "CONTROLLED"}}, "APPROVAL_HAZARD_CONTROLS_MALFORMED"),
            ({**APPROVAL, "hazard_controls": {**HAZARD_CONTROLLED, "H4": "PARTIAL"}}, "APPROVAL_H4_NOT_CONTROLLED"),
            ({**APPROVAL, "callee_run_evidence": []}, "NO_CALLEE_RUN_EVIDENCE"),
        )
        for approval, reason in cases:
            with self.subTest(reason=reason):
                record = G.resolve(1, complete_ledger(), CONTRACTS, approval)
                self.assertEqual(record["execution"], "BLOCKED")
                self.assertIn(reason, record["reasons"])

    def test_self_declared_and_model_flags_are_never_approval(self):
        for extra in ({"safety_gate_approved": True}, {"execution": "ALLOWED"},
                      {"verdict": "MODEL_ELIGIBLE"}, {"verdict": "PASS"}, {"verdict": "VERIFIED"}):
            with self.subTest(extra=extra):
                record = G.resolve(1, complete_ledger(), CONTRACTS, {**APPROVAL, **extra})
                self.assertEqual(record["execution"], "BLOCKED")
                self.assertTrue(any(r.startswith("UNRECOGNIZED_APPROVAL_FIELD_") for r in record["reasons"]))

    def test_verification_not_run_is_always_reported(self):
        contracts = copy.deepcopy(CONTRACTS)
        contracts["apis"][1]["verification"] = {"status": "OBSERVED", "executed_callee": True, "evidence": []}
        record = G.resolve(1, complete_ledger(), contracts, APPROVAL)
        self.assertEqual(record["execution"], "BLOCKED")
        self.assertIn("CALLEE_VERIFICATION_NOT_RUN", record["reasons"])

    def test_tampered_ledger_fails_closed(self):
        def mutate(path_fn):
            ledger = copy.deepcopy(LEDGER)
            path_fn(ledger)
            return ledger

        bad_ledgers = {
            "execution ALLOWED": lambda l: l["slots"]["1"].update(execution="ALLOWED"),
            "release eligible": lambda l: l["slots"]["1"].update(release_eligible=True),
            "approval inside ledger": lambda l: l["slots"]["1"].update(approval=APPROVAL),
            "entry drift": lambda l: l["slots"]["1"].update(candidate_entry="0xE00C"),
            "slot out of range": lambda l: l["slots"].update({"81": l["slots"]["1"]}),
            "non-numeric slot": lambda l: l["slots"].update({"x": l["slots"]["1"]}),
            "missing hazard": lambda l: l["slots"]["1"]["hazard_controls"].pop("H7"),
            "unknown control state": lambda l: l["slots"]["1"]["hazard_controls"].update(H1="PASS"),
            "wrong schema": lambda l: l.update(schema="x"),
            "extra top key": lambda l: l.update(default={"execution": "ALLOWED"}),
        }
        for name, change in bad_ledgers.items():
            with self.subTest(name=name), self.assertRaises(ValueError):
                G.validate_ledger(mutate(change), CONTRACTS)

    def test_claim_ledger_is_untouched_and_never_verified(self):
        for api in CONTRACTS["apis"]:
            self.assertEqual(api["verification"], G.VERIFICATION_NOT_RUN)
        self.assertNotIn("VERIFIED", json.dumps(CONTRACTS["apis"][1]["contract"]))

    def test_cli_exits_blocked_for_all_slots(self):
        result = subprocess.run([sys.executable, str(ROOT / "tools" / "reference_jsr_gate.py"), "--check"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, G.EXIT_BLOCKED)
        self.assertIn("BLOCKED: reference JSR execution gate closed for 81/81 slots; jsr_count=0", result.stdout)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
