"""Issue #692: host tests for the API-stub halt classifier (no emulator).

Fail-closed rules on fabricated records, checks on the real spec and ledger data, and a strict override-name check.
Expectation-breaking mutations are run by the integration harness (geargrafx_api_unimplemented.py --mutation).
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from api_halt_classify import (API_NOT_CONFIRMED, API_PC_MISMATCH, API_SLOT_MISMATCH,  # noqa: E402
                               API_UNIMPLEMENTED_HALT, BUDGET, CD_HALT_FAIL, CD_HALT_OK, OTHER,
                               OVERRIDES, SPEC_SLOTS, STUB_LABEL, all_slots, classify, implemented_slots)

STUB = 0xE24F
FIRST_IMPLEMENTED = 0x1E


def run(reason, confirmed, pc, x):
    return {"reason": reason, "halt_confirmed": confirmed, "pc": pc, "x": x}


class ClassifyTests(unittest.TestCase):
    def test_confirmed_stub_with_expected_slot(self):
        r = classify(run(STUB_LABEL, True, STUB, 0x02), stub_address=STUB, expected_slot=0x02)
        self.assertEqual(r["state"], API_UNIMPLEMENTED_HALT)
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

    def test_existing_halt_labels_keep_their_states(self):
        self.assertEqual(classify(run("cd_halt_fail", True, 0x1234, 0), stub_address=STUB)["state"], CD_HALT_FAIL)
        self.assertEqual(classify(run("cd_halt_ok", True, 0x1234, 0), stub_address=STUB)["state"], CD_HALT_OK)

    def test_success_named_reasons_without_confirmation_are_not_success(self):
        self.assertEqual(classify(run("cd_halt_ok", False, STUB, 2), stub_address=STUB)["state"], OTHER)
        self.assertEqual(classify(run("cd_halt_fail", False, STUB, 2), stub_address=STUB)["state"], OTHER)

    def test_stub_pc_is_required_even_without_an_expected_slot(self):
        r = classify(run(STUB_LABEL, True, STUB + 3, 0x02), stub_address=STUB)
        self.assertEqual(r["state"], API_PC_MISMATCH)

    def test_no_halt_state_name_says_success(self):
        for name in (API_UNIMPLEMENTED_HALT, API_SLOT_MISMATCH, API_PC_MISMATCH, API_NOT_CONFIRMED, BUDGET,
                     CD_HALT_FAIL, CD_HALT_OK, OTHER):
            self.assertNotIn("SUCCESS", name)
            self.assertNotIn("PASS", name)


class SpecAndLedgerData(unittest.TestCase):
    def test_spec_has_81_slots_with_ids_exactly_0_to_80(self):
        ids = sorted(int(e["id"]) for e in json.loads(SPEC_SLOTS.read_text(encoding="utf-8"))["slots"])
        self.assertEqual(ids, list(range(81)))
        self.assertEqual(all_slots(), set(range(81)))

    def test_slot_02_is_cd_base_in_the_spec_and_has_no_override(self):
        entries = {int(e["id"]): e for e in json.loads(SPEC_SLOTS.read_text(encoding="utf-8"))["slots"]}
        self.assertEqual(entries[0x02]["candidate_name"], "CD_BASE")
        self.assertNotIn(0x02, implemented_slots())
        self.assertFalse((OVERRIDES / "slot_02.s").exists())

    def test_every_override_file_defines_its_own_slot_symbol(self):
        # content binding, not a file-existence check: the file must name the symbol of its own slot
        for slot in implemented_slots():
            text = (OVERRIDES / f"slot_{slot:02X}.s").read_text(encoding="ascii", errors="replace")
            self.assertIn(f"api_slot_{slot:02X}", text, f"slot {slot:02X}")

    def test_boundary_on_the_real_ledger(self):
        impl = implemented_slots()
        self.assertEqual(min(impl), FIRST_IMPLEMENTED)
        self.assertNotIn(FIRST_IMPLEMENTED - 1, impl)
        self.assertIn(FIRST_IMPLEMENTED, impl)

    def test_a_non_slot_override_name_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "slot_1E.s").write_text("", encoding="ascii")
            Path(tmp, "helper.s").write_text("", encoding="ascii")
            with self.assertRaises(ValueError):
                implemented_slots(Path(tmp))

    def test_a_lowercase_override_name_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "slot_1e.s").write_text("", encoding="ascii")
            with self.assertRaises(ValueError):
                implemented_slots(Path(tmp))


if __name__ == "__main__":
    unittest.main()
