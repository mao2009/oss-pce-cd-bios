"""DEV implementation ledger (spec/dev_implementations.json): consistency with the 81 slots and the DEV ROM overrides.

Host-only. Each negative case is a mutated copy of the real ledger that must produce a problem.
"""
import copy
import sys
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import dev_implementations as dev  # noqa: E402
from build_rom import load_slots, overrides  # noqa: E402

SLOTS = load_slots()
OVERRIDES = set(overrides())


def base():
    return dev.load_ledger()


def entry(ledger, slot):
    return ledger["slots"][slot]


class LedgerTests(unittest.TestCase):
    def test_real_ledger_is_consistent(self):
        self.assertEqual([], dev.validate(base(), SLOTS, OVERRIDES))

    def test_counts_separate_dev_rom_from_approval(self):
        summary = dev.summary(base(), OVERRIDES)
        self.assertEqual(12, summary["dev_rom_callable"])  # 1E/3A/3C/3D/3E/3F/40/41/42/43/44/4F
        self.assertEqual(0, summary["dev_callable_approved"])
        self.assertEqual(0, summary["abi_verified"])
        self.assertIs(False, summary["release_eligible"])

    def test_release_eligible_true_fails(self):
        ledger = base()
        ledger["release_eligible"] = True
        self.assertTrue(dev.validate(ledger, SLOTS, OVERRIDES))

    def test_duplicate_slot_id_fails(self):
        ledger = base()
        ledger["slots"][5] = copy.deepcopy(ledger["slots"][4])
        self.assertTrue(any("duplicate" in p for p in dev.validate(ledger, SLOTS, OVERRIDES)))

    def test_missing_slot_fails_not_pass(self):
        ledger = base()
        del ledger["slots"][7]
        self.assertTrue(dev.validate(ledger, SLOTS, OVERRIDES))

    def test_wrong_candidate_name_fails(self):
        ledger = base()
        entry(ledger, 0x3D)["candidate_name"] = "EX_BINBCD"
        self.assertTrue(any("candidate_name" in p for p in dev.validate(ledger, SLOTS, OVERRIDES)))

    def test_unknown_state_fails(self):
        ledger = base()
        entry(ledger, 0x3D)["state"] = "PASS"
        self.assertTrue(any("unknown state" in p for p in dev.validate(ledger, SLOTS, OVERRIDES)))

    def test_override_without_ledger_entry_fails(self):
        ledger = base()
        entry(ledger, 0x3D)["implementation"] = None
        self.assertTrue(any("lacks an implementation" in p for p in dev.validate(ledger, SLOTS, OVERRIDES)))

    def test_ledger_implementation_without_override_fails(self):
        ledger = base()
        entry(ledger, 0x10)["implementation"] = {"source": "src/overrides/slot_10.s", "license": "MIT"}
        self.assertTrue(any("claims an implementation" in p for p in dev.validate(ledger, SLOTS, OVERRIDES)))

    def test_verified_abi_is_refused(self):
        ledger = base()
        entry(ledger, 0x3D)["abi_contract"]["status"] = "VERIFIED"
        self.assertTrue(any("VERIFIED" in p for p in dev.validate(ledger, SLOTS, OVERRIDES)))

    def test_dev_callable_without_approval_fails(self):
        ledger = base()
        entry(ledger, 0x3D)["state"] = "DEV_CALLABLE"
        problems = dev.validate(ledger, SLOTS, OVERRIDES)
        self.assertTrue(any("approved_by" in p for p in problems))

    def test_dev_callable_with_approval_and_evidence_passes(self):
        ledger = base()
        slot = entry(ledger, 0x3D)
        slot["state"] = "DEV_CALLABLE"
        slot["approval"] = {"approved_by": "owner", "review_ref": "issue-324"}
        self.assertEqual([], dev.validate(ledger, SLOTS, OVERRIDES))

    def test_dev_callable_with_missing_evidence_file_fails(self):
        ledger = base()
        slot = entry(ledger, 0x3D)
        slot["state"] = "DEV_CALLABLE"
        slot["approval"] = {"approved_by": "owner", "review_ref": "issue-324"}
        slot["real_core_evidence"] = ["tests/integration/does_not_exist.py"]
        self.assertTrue(any("file missing" in p for p in dev.validate(ledger, SLOTS, OVERRIDES)))

    def test_dev_callable_without_real_core_evidence_fails(self):
        ledger = base()
        slot = entry(ledger, 0x3D)
        slot["state"] = "DEV_CALLABLE"
        slot["approval"] = {"approved_by": "owner", "review_ref": "issue-324"}
        slot["real_core_evidence"] = []
        self.assertTrue(any("needs real_core_evidence" in p for p in dev.validate(ledger, SLOTS, OVERRIDES)))

    def test_blocked_with_implementation_fails(self):
        ledger = base()
        slot = entry(ledger, 0x3D)
        slot["state"] = "BLOCKED"
        slot["reason"] = "test"
        self.assertTrue(any("BLOCKED slot must not" in p for p in dev.validate(ledger, SLOTS, OVERRIDES)))

    def test_wrong_implementation_source_fails(self):
        ledger = base()
        entry(ledger, 0x3D)["implementation"]["source"] = "src/ex_bcdbin.s"
        self.assertTrue(any("implementation source" in p for p in dev.validate(ledger, SLOTS, OVERRIDES)))


class LedgerTests3C(unittest.TestCase):
    def test_wrong_candidate_name_fails(self):
        ledger = base()
        entry(ledger, 0x3C)["candidate_name"] = "EX_BCDBIN"
        self.assertTrue(any("candidate_name" in p for p in dev.validate(ledger, SLOTS, OVERRIDES)))

    def test_unknown_state_fails(self):
        ledger = base()
        entry(ledger, 0x3C)["state"] = "PASS"
        self.assertTrue(any("unknown state" in p for p in dev.validate(ledger, SLOTS, OVERRIDES)))

    def test_override_without_ledger_entry_fails(self):
        ledger = base()
        entry(ledger, 0x3C)["implementation"] = None
        self.assertTrue(any("lacks an implementation" in p for p in dev.validate(ledger, SLOTS, OVERRIDES)))

    def test_ledger_implementation_without_override_fails(self):
        ledger = base()
        entry(ledger, 0x10)["implementation"] = {"source": "src/overrides/slot_10.s", "license": "MIT"}
        self.assertTrue(any("claims an implementation" in p for p in dev.validate(ledger, SLOTS, OVERRIDES)))

    def test_verified_abi_is_refused(self):
        ledger = base()
        entry(ledger, 0x3C)["abi_contract"]["status"] = "VERIFIED"
        self.assertTrue(any("VERIFIED" in p for p in dev.validate(ledger, SLOTS, OVERRIDES)))

    def test_dev_callable_without_approval_fails(self):
        ledger = base()
        entry(ledger, 0x3C)["state"] = "DEV_CALLABLE"
        problems = dev.validate(ledger, SLOTS, OVERRIDES)
        self.assertTrue(any("approved_by" in p for p in problems))

    def test_dev_callable_with_approval_and_evidence_passes(self):
        ledger = base()
        slot = entry(ledger, 0x3C)
        slot["state"] = "DEV_CALLABLE"
        slot["approval"] = {"approved_by": "owner", "review_ref": "issue-324"}
        self.assertEqual([], dev.validate(ledger, SLOTS, OVERRIDES))

    def test_dev_callable_with_missing_evidence_file_fails(self):
        ledger = base()
        slot = entry(ledger, 0x3C)
        slot["state"] = "DEV_CALLABLE"
        slot["approval"] = {"approved_by": "owner", "review_ref": "issue-324"}
        slot["real_core_evidence"] = ["tests/integration/does_not_exist.py"]
        self.assertTrue(any("file missing" in p for p in dev.validate(ledger, SLOTS, OVERRIDES)))

    def test_dev_callable_without_real_core_evidence_fails(self):
        ledger = base()
        slot = entry(ledger, 0x3C)
        slot["state"] = "DEV_CALLABLE"
        slot["approval"] = {"approved_by": "owner", "review_ref": "issue-324"}
        slot["real_core_evidence"] = []
        self.assertTrue(any("needs real_core_evidence" in p for p in dev.validate(ledger, SLOTS, OVERRIDES)))

    def test_blocked_with_implementation_fails(self):
        ledger = base()
        slot = entry(ledger, 0x3C)
        slot["state"] = "BLOCKED"
        slot["reason"] = "test"
        self.assertTrue(any("BLOCKED slot must not" in p for p in dev.validate(ledger, SLOTS, OVERRIDES)))

    def test_wrong_implementation_source_fails(self):
        ledger = base()
        entry(ledger, 0x3C)["implementation"]["source"] = "src/ex_bcdbin.s"
        self.assertTrue(any("implementation source" in p for p in dev.validate(ledger, SLOTS, OVERRIDES)))


class AbiVerifiedDerivedTests(unittest.TestCase):
    """abi_verified is computed from ledger rows, not hardcoded. A VERIFIED mutant must be rejected even with evidence."""

    def _evidenced_verified_mutant(self):
        ledger = base()
        slot = entry(ledger, 0x3D)
        slot["abi_contract"]["status"] = "VERIFIED"
        slot["state"] = "DEV_CALLABLE"
        slot["approval"] = {"approved_by": "owner", "review_ref": "issue-439"}
        slot["host_evidence"] = ["tests/host/test_dev_implementations.py"]
        slot["real_core_evidence"] = ["tests/host/test_dev_implementations.py"]
        return ledger

    def test_mutant_verified_row_with_evidence_is_rejected(self):
        ledger = self._evidenced_verified_mutant()
        problems = dev.validate(ledger, SLOTS, OVERRIDES)
        self.assertTrue(any("VERIFIED" in p and "slot 61" in p for p in problems), problems)

    def test_mutant_verified_row_is_counted_by_summary(self):
        ledger = self._evidenced_verified_mutant()
        self.assertEqual(1, dev.summary(ledger, OVERRIDES)["abi_verified"])

    def test_mutant_verified_row_without_evidence_is_rejected(self):
        ledger = base()
        entry(ledger, 0x3C)["abi_contract"]["status"] = "VERIFIED"
        self.assertTrue(any("VERIFIED" in p for p in dev.validate(ledger, SLOTS, OVERRIDES)))
        self.assertEqual(1, dev.summary(ledger, OVERRIDES)["abi_verified"])

    def test_real_ledger_has_zero_verified_rows(self):
        self.assertEqual(0, dev.summary(base(), OVERRIDES)["abi_verified"])


class FailClosedTests(unittest.TestCase):
    """Malformed ledgers must yield problems (never an exception, never an empty list)."""

    def test_counts_are_separate_and_current(self):
        s = dev.summary(base(), OVERRIDES)
        self.assertEqual((12, 69, 0), (s["dev_rom_callable"], s["dev_rom_unimplemented"], s["owner_approved"]))

    def test_owner_approval_is_counted_apart_from_implementation(self):
        ledger = base()
        entry(ledger, 0x10)["approval"] = {"approved_by": "owner", "review_ref": "x"}
        s = dev.summary(ledger, OVERRIDES)
        self.assertEqual((12, 1, 0), (s["dev_rom_callable"], s["owner_approved"], s["dev_callable_approved"]))

    def test_malformed_ledgers_are_rejected(self):
        for mutate in (
            lambda l: l.update(slots=[None] + l["slots"][1:]),
            lambda l: l["slots"][3].update(abi_contract=None),
            lambda l: l["slots"][3].update(abi_contract="VERIFIED"),
            lambda l: l["slots"][3].update(abi_contract={}),
            lambda l: l["slots"][0x3D].update(state="DEV_CALLABLE", approval="owner"),
            lambda l: l.update(slots={}),
            lambda l: l.pop("schema_version"),
            lambda l: l.update(schema_version=True),
            lambda l: l["slots"][0x3D].update(implementation="x"),
            lambda l: l["slots"][0x3D].update(implementation=["x"]),
            lambda l: l["slots"][0x3D].update(state="DEV_CALLABLE", approval={"approved_by": "o", "review_ref": "r"},
                                              host_evidence=5),
            lambda l: l["slots"][0x3D].update(state="DEV_CALLABLE", approval={"approved_by": "o", "review_ref": "r"},
                                              host_evidence=[None]),
            lambda l: l["slots"][0x3D].update(state="DEV_CALLABLE", approval={"approved_by": "o", "review_ref": "r"},
                                              host_evidence=["../../../etc/hostname"]),
            lambda l: l["slots"][3].update(id=[1]),
            lambda l: l["slots"][3].update(id=True),
            lambda l: l["slots"][3].update(state=[1]),
            lambda l: l["slots"][3].update(abi_contract={"status": [1]}),
            lambda l: l["slots"][0x3D]["implementation"].update(license=1),
            lambda l: l["slots"][3].update(state="BLOCKED", reason=[1]),
            lambda l: l["slots"][0x3D].update(state="DEV_CALLABLE", approval={"approved_by": 1, "review_ref": 2}),
            lambda l: l["slots"][0x3D]["implementation"].update(license=" "),
        ):
            ledger = base()
            mutate(ledger)
            self.assertTrue(dev.validate(ledger, SLOTS, OVERRIDES))
        self.assertTrue(dev.validate([], SLOTS, OVERRIDES))
        self.assertTrue(dev.validate(base(), SLOTS, {1, "a"}))

    def test_main_returns_1_when_ledger_unloadable(self):
        import io
        from contextlib import redirect_stderr
        with unittest.mock.patch.object(dev, "load_ledger", side_effect=RecursionError), redirect_stderr(io.StringIO()):
            self.assertEqual(1, dev.main())
        with unittest.mock.patch.object(dev, "validate", side_effect=RuntimeError), redirect_stderr(io.StringIO()):
            self.assertEqual(1, dev.main())

    def test_summary_never_raises_on_malformed_ledger(self):
        for bad in ([], {"slots": 5}, {"slots": [None, 1, {"state": [1], "abi_contract": 3}]}):
            self.assertEqual(0, dev.summary(bad, OVERRIDES)["abi_verified"])


if __name__ == "__main__":
    unittest.main()
