"""Host tests for the machine-readable reference-JSR execution gate (Issue #228).

Synthetic only: exercises ``tools/reference_jsr_gate.py`` (pure functions + ``--check`` CLI) and
the committed ledger. No emulator, no reference BIOS and no reference JSR are involved. The
gate must stay BLOCKED for slot $01 and every other slot; claims in spec/api_contracts.json must
not be readable as permission, and MODEL_ELIGIBLE / PASS / self-declared flags must never unlock.
Approval input is unauthenticated: it is not authorisation and must never produce ALLOWED.
"""
import copy
import json
import shutil
import subprocess
import sys
import tempfile
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

    def test_complete_materials_reach_unauthenticated_state_never_allowed(self):
        record = G.resolve(1, complete_ledger(), CONTRACTS, APPROVAL)
        self.assertEqual(record["execution"], "MATERIALS_COMPLETE")
        self.assertEqual(record["reasons"], [])
        self.assertEqual(record["runner"], "NONE")
        self.assertNotIn("ALLOWED", json.dumps(record))

    def test_permission_accepts_only_the_exact_allowed_value(self):
        # Fail-closed: every non-exact value is refused, including case/whitespace variants.
        for value in ("BLOCKED", "MATERIALS_COMPLETE", "", " ALLOWED", "ALLOWED ", "\tALLOWED",
                      "allowed", "Allowed", "ALLOW", "PASS", "VERIFIED", "MODEL_ELIGIBLE", None, True, 1):
            with self.subTest(value=value):
                self.assertFalse(G.is_execution_permitted(value))
        self.assertTrue(G.is_execution_permitted("ALLOWED"))

    def test_self_declared_full_approval_is_materials_complete_never_permitted(self):
        # A reviewer-only or fully self-declared approval cannot reach permission on any slot.
        for approval in ({"reviewer": "x", "pr": 228}, {**APPROVAL, "reviewer": "self"}):
            with self.subTest(approval=approval):
                record = G.resolve(1, complete_ledger(), CONTRACTS, approval)
                self.assertFalse(G.is_execution_permitted(record["execution"]))
                self.assertIs(record["execution_permitted"], False)
        record = G.resolve(1, complete_ledger(), CONTRACTS, APPROVAL)
        self.assertEqual(record["execution"], "MATERIALS_COMPLETE")
        self.assertIs(record["execution_permitted"], False)
        self.assertFalse(G.is_execution_permitted(record["execution"]))

    def test_materials_complete_meaning_is_documented(self):
        # MATERIALS_COMPLETE is not renamed; its meaning per gate lives in docs/cd-reset-contract.md.
        doc = (ROOT / "docs" / "cd-reset-contract.md").read_text(encoding="utf-8")
        for name in ("MATERIALS_COMPLETE", "MODEL_ELIGIBLE"):
            self.assertIn(f"| `{name}` |", doc)
        rows = [l for l in doc.splitlines() if "`tools/reference_jsr_gate.py`" in l]
        self.assertEqual(len(rows), 1)
        for keyword in ("未認証", "NONE", "jsr_count=0", "NOT_RUN"):
            self.assertIn(keyword, rows[0])

    def test_fake_approval_on_every_slot_never_grants_execution(self):
        # A well-formed but unauthenticated approval must not read as permission: no ALLOWED,
        # no runner, no JSR, no release eligibility, on any slot.
        ledger = complete_ledger()
        for slot in range(len(CONTRACTS["apis"])):
            with self.subTest(slot=slot):
                record = G.resolve(slot, ledger, CONTRACTS, APPROVAL)
                self.assertNotEqual(record["execution"], "ALLOWED")
                self.assertEqual((record["runner"], record["jsr_count"], record["release_eligible"]),
                                 ("NONE", 0, False))
        self.assertEqual(G.resolve(1, LEDGER, CONTRACTS, APPROVAL)["execution"], "BLOCKED")

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

    def test_recorded_callee_verification_is_reported_and_blocked(self):
        contracts = copy.deepcopy(CONTRACTS)
        contracts["apis"][1]["verification"] = {"status": "OBSERVED", "executed_callee": True, "evidence": []}
        record = G.resolve(1, complete_ledger(), contracts, APPROVAL)
        self.assertEqual(record["execution"], "BLOCKED")
        self.assertIn("CALLEE_VERIFICATION_RECORDED", record["reasons"])
        self.assertNotIn("CALLEE_VERIFICATION_NOT_RUN", record["reasons"])

    def test_not_run_callee_verification_emits_no_verification_reason(self):
        record = G.resolve(1, complete_ledger(), CONTRACTS, APPROVAL)
        self.assertFalse([r for r in record["reasons"] if r.startswith("CALLEE_VERIFICATION")])

    def test_duplicate_ledger_key_is_rejected_not_last_wins(self):
        blocked = LEDGER["slots"]["1"]
        all_controlled = {**blocked, "hazard_controls": dict(HAZARD_CONTROLLED)}
        text = ('{"schema": "%s", "claim_ledger": "%s", "slots": {"1": %s, "1": %s}}'
                % (LEDGER["schema"], LEDGER["claim_ledger"], json.dumps(blocked), json.dumps(all_controlled)))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.json"
            path.write_text(text, encoding="utf-8")
            with self.assertRaises(ValueError):
                G.load(path, ROOT / "spec" / "api_contracts.json")

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
            "leading-zero slot": lambda l: l["slots"].update({"01": l["slots"]["1"]}),
            "non-ascii digit slot": lambda l: l["slots"].update({"١": l["slots"]["1"]}),
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

    def test_cli_fails_closed_with_exit_3_on_unusable_ledger(self):
        broken = {
            "missing ledger": None,
            "malformed JSON": "{not json",
            "duplicate key": ('{"schema": "%s", "claim_ledger": "spec/api_contracts.json", "slots": {}, '
                              '"slots": {"1": {"execution": "ALLOWED"}}}' % LEDGER["schema"]),
        }
        for name, text in broken.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                tree = Path(tmp)
                (tree / "tools").mkdir()
                (tree / "spec").mkdir()
                shutil.copy(ROOT / "tools" / "reference_jsr_gate.py", tree / "tools")
                shutil.copy(ROOT / "spec" / "api_contracts.json", tree / "spec")
                if text is not None:
                    (tree / "spec" / "reference_jsr_gate.json").write_text(text, encoding="utf-8")
                result = subprocess.run([sys.executable, str(tree / "tools" / "reference_jsr_gate.py"), "--check"],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, G.EXIT_BLOCKED)
                self.assertTrue(result.stdout.startswith("BLOCKED: gate ledger unusable"), result.stdout)
                self.assertNotIn("Traceback", result.stderr)

    def test_cli_never_returns_zero_even_if_a_record_reports_allowed(self):
        # Negative: the real data is all BLOCKED, so also force resolve() to report an allowed
        # value and an unexpected one. main() must still return a nonzero code, never 0.
        import contextlib
        import io
        from unittest import mock
        real_resolve = G.resolve
        for value in ("ALLOWED", "", None, "MATERIALS_COMPLETE"):
            with self.subTest(value=value):
                def forced(slot, ledger, contracts, approval=None, _value=value):
                    return dict(real_resolve(slot, ledger, contracts, approval), execution=_value)
                with mock.patch.object(G, "resolve", forced), contextlib.redirect_stdout(io.StringIO()):
                    code = G.main(["--check"])
                self.assertEqual(code, G.EXIT_BLOCKED)
                self.assertNotEqual(code, 0)

    def test_cli_exits_blocked_for_all_slots(self):
        result = subprocess.run([sys.executable, str(ROOT / "tools" / "reference_jsr_gate.py"), "--check"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, G.EXIT_BLOCKED)
        self.assertIn("BLOCKED: reference JSR execution gate closed for 81/81 slots; jsr_count=0", result.stdout)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
