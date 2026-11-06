"""Issue #692: host tests for the API-stub halt classifier (no emulator).

Covers the fail-closed rules and the ledger derivation from src/overrides.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from api_halt_classify import (API_NOT_CONFIRMED, API_PC_MISMATCH, API_SLOT_MISMATCH,  # noqa: E402
                               API_UNIMPLEMENTED_CONFIRMED, BUDGET, CD_HALT_FAIL, CD_HALT_OK, OTHER,
                               STUB_LABEL, classify, implemented_slots, unimplemented_slots)

STUB = 0xE24F


def run(reason, confirmed, pc, x):
    return {"reason": reason, "halt_confirmed": confirmed, "pc": pc, "x": x}


class ClassifyTests(unittest.TestCase):
    def test_confirmed_stub_with_expected_slot(self):
        r = classify(run(STUB_LABEL, True, STUB, 0x02), stub_address=STUB, expected_slot=0x02)
        self.assertEqual(r["state"], API_UNIMPLEMENTED_CONFIRMED)
        self.assertEqual(r["slot"], 2)

    def test_wrong_slot_is_fail_closed(self):
        r = classify(run(STUB_LABEL, True, STUB, 0x03), stub_address=STUB, expected_slot=0x02)
        self.assertEqual(r["state"], API_SLOT_MISMATCH)

    def test_wrong_pc_is_fail_closed(self):
        r = classify(run(STUB_LABEL, True, STUB + 1, 0x02), stub_address=STUB, expected_slot=0x02)
        self.assertEqual(r["state"], API_PC_MISMATCH)

    def test_label_without_confirmation_is_fail_closed(self):
        r = classify(run(STUB_LABEL, False, STUB, 0x02), stub_address=STUB, expected_slot=0x02)
        self.assertEqual(r["state"], API_NOT_CONFIRMED)

    def test_budget_is_never_the_stub(self):
        r = classify(run("budget", False, STUB, 0x02), stub_address=STUB, expected_slot=0x02)
        self.assertEqual(r["state"], BUDGET)

    def test_budget_claiming_confirmation_is_inconsistent(self):
        r = classify(run("budget", True, STUB, 0x02), stub_address=STUB, expected_slot=0x02)
        self.assertEqual(r["state"], OTHER)

    def test_cd_halt_fail_and_ok_are_reported_as_themselves(self):
        self.assertEqual(classify(run("cd_halt_fail", True, 0x1234, 0), stub_address=STUB)["state"], CD_HALT_FAIL)
        self.assertEqual(classify(run("cd_halt_ok", True, 0x1234, 0), stub_address=STUB)["state"], CD_HALT_OK)

    def test_no_expected_slot_still_requires_the_stub_pc(self):
        r = classify(run(STUB_LABEL, True, STUB + 3, 0x02), stub_address=STUB)
        self.assertEqual(r["state"], API_PC_MISMATCH)

    def test_no_state_is_a_pass_word(self):
        states = {classify(run(reason, c, STUB, 2), stub_address=STUB, expected_slot=2)["state"]
                  for reason in ("budget", "cd_halt_ok", "cd_halt_fail", STUB_LABEL, "other")
                  for c in (True, False)}
        self.assertFalse(any("PASS" in s for s in states))


class LedgerTests(unittest.TestCase):
    def test_ledger_is_read_from_override_files(self):
        impl = implemented_slots()
        unimpl = unimplemented_slots()
        self.assertTrue(impl)
        self.assertEqual(impl | unimpl, set(range(81)))
        self.assertFalse(impl & unimpl)

    def test_slot_02_is_unimplemented(self):
        self.assertIn(0x02, unimplemented_slots())

    def test_boundary_is_derived_not_hardcoded(self):
        impl = implemented_slots()
        first = min(impl)
        self.assertIn(first - 1, unimplemented_slots())
        self.assertIn(first, impl)


if __name__ == "__main__":
    unittest.main()
