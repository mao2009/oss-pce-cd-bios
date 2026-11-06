"""Issue #188: structure check of the non-executing CD_READ mode-2 fixture (no reference BIOS, no emulator run)."""
import json
import unittest

from tools import cd_read_contract as crc
from tools import reference_jsr_gate as G
from tools.build_rom import FIRST_ENTRY

FIXTURE = crc.ROOT / "tests/fixtures/cd-read-mode2-single-sector.json"
DOC = crc.ROOT / "docs/188-cd-read-mode-safety.md"
UNKNOWN = "UNKNOWN"


class Mode2FixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = FIXTURE.read_text(encoding="utf-8")
        cls.fx = json.loads(cls.text)
        cls.examples = {e["id"]: e for e in crc.RAM_MODE_EXAMPLES}

    def test_fixture_declares_no_execution_and_no_verified_claim(self):
        fx = self.fx
        self.assertEqual(fx["status"], "NON_EXECUTING_FIXTURE")
        self.assertFalse(fx["executes_reference_bios"])
        self.assertEqual(fx["callee_real_core_runs"], crc.CALLEE_REAL_CORE_RUNS)
        self.assertEqual(fx["mode"]["dh"], crc.RAM_MODE)
        self.assertEqual(fx["fixture_sectors"], crc.FIXTURE_SECTORS)
        self.assertNotIn("VERIFIED", self.text)

    def test_expected_state_template_is_unknown_and_maps_to_contract_items(self):
        known = {i["id"] for i in crc.RAM_MODE_ITEMS} | {c["id"] for c in crc.TRANSPORT_CLAIMS}
        for name, slot in self.fx["expected_state_template"].items():
            with self.subTest(field=name):
                self.assertEqual(slot["value"], UNKNOWN)
                self.assertIn(slot["item"], known)

    def test_cases_mirror_contract_examples_and_callee_stays_unknown(self):
        ids = [c["id"] for c in self.fx["cases"]]
        self.assertEqual(len(ids), len(set(ids)))
        for case in self.fx["cases"]:
            with self.subTest(case=case["id"]):
                self.assertEqual(case["callee_expect"], UNKNOWN)
                ex = self.examples[case["ram_mode_example"]]
                self.assertEqual(case["ram_mode_example"], case["id"])
                self.assertEqual((case["lba"], case["count"], case["polarity"]),
                                 (ex["lba"], ex["count"], ex["polarity"]))
                if case["transport_expect"] == "measured":
                    self.assertIsNotNone(ex["measured_by"])
                    self.assertEqual(crc.transport_outcome(crc.FIXTURE_SECTORS, case["lba"], case["count"]),
                                     ex["expect"])
                else:
                    self.assertEqual(case["transport_expect"], UNKNOWN)
                    self.assertIsNone(ex["measured_by"])

    def test_count_boundaries_list_only_count_1_as_in_scope(self):
        status = {b["count"]: b["status"] for b in self.fx["count_boundaries"]}
        self.assertEqual(status[1], "IN_SCOPE")
        for count in (0, 2, 3, 4, 256):
            with self.subTest(count=count):
                self.assertEqual(status[count], UNKNOWN)

    def test_unlisted_counts_are_refused_by_the_transport_model(self):
        for count in (0, 4, 256):
            with self.subTest(count=count), self.assertRaises(ValueError):
                crc.transport_outcome(crc.FIXTURE_SECTORS, 5, count)

    def test_gate_record_for_slot3_is_blocked_and_never_permitted(self):
        ledger, contracts = G.load()
        rec = G.resolve(self.fx["gate"]["slot"], ledger, contracts)
        self.assertEqual(rec["execution"], self.fx["gate"]["expected_execution"])
        self.assertIn(self.fx["gate"]["expected_reason"], rec["reasons"])
        # The entry is the DEV ROM table rule for the slot, computed here and not stored in the fixture.
        self.assertEqual(rec["candidate_entry"], f"0x{FIRST_ENTRY + 3 * self.fx['gate']['slot']:04X}")
        self.assertFalse(rec["execution_permitted"])
        self.assertEqual(rec["jsr_count"], 0)

    def test_safety_doc_lists_every_case(self):
        text = DOC.read_text(encoding="utf-8")
        for case in self.fx["cases"]:
            with self.subTest(case=case["id"]):
                self.assertRegex(text, rf"\|\s*{case['id']}\s*\|")
        self.assertIn("BLOCKED", text)


if __name__ == "__main__":
    unittest.main()
