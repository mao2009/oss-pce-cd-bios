"""Host-only assertions for the reference BIOS scenario plan generator."""
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("reference_bios_scenarios", ROOT / "tools/reference_bios_scenarios.py")
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class ReferenceBiosScenarioTests(unittest.TestCase):
    def setUp(self):
        self.slots = json.loads((ROOT / "spec/api_slots.json").read_text(encoding="utf-8"))["slots"]
        self.items = module.generate(self.slots)

    def test_all_90_unique(self):
        self.assertEqual(len(self.items), 90)
        self.assertEqual(len({item["id"] for item in self.items}), 90)
        self.assertEqual(sum(x["kind"] == "main_api" for x in self.items), 81)
        self.assertEqual(sum(x["kind"] == "psg_subcommand" for x in self.items), 9)

    def test_main_slot_entry_arithmetic(self):
        for index, item in enumerate(self.items[:81]):
            self.assertEqual(item["entry_candidate"], f"0x{0xe000 + index * 3:04x}")

    def test_plans_never_claim_measurement_or_execute(self):
        for item in self.items:
            self.assertEqual(item["execution_status"], "NOT_RUN")
            self.assertEqual(item["expectation_status"], "NOT_MEASURED")
            self.assertEqual(item["abi_status"], "HYPOTHESIS")
            self.assertFalse(item["safety"]["execute_automatically"])
            self.assertTrue(item["safety"]["requires_manual_fixture_and_abi_review"])
            self.assertTrue(all(c["status"] == "DESIGN_PENDING" for c in item["scenarios"]))

    def test_duplicate_slot_rejected(self):
        bad = self.slots[:-1] + [self.slots[0]]
        with self.assertRaises(ValueError):
            module.generate(bad)

    def test_no_proprietary_bytes_or_expected_values(self):
        dumped = json.dumps(self.items)
        self.assertNotIn('"expected_bytes"', dumped)
        self.assertNotIn('"expected_registers"', dumped)
        self.assertNotIn('"verified"', dumped.lower())


if __name__ == "__main__":
    unittest.main()
