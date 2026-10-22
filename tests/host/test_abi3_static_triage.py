"""Host checks for the Phase ABI-3 static triage projection (Issue #476).

The projection is the committed output of tools/reference_rom_static_audit.py on the local payload.
These tests need no ROM: they check the committed file's shape, its jump table, and that its seven
previously reviewed slots reproduce the committed ABI-1 static ledger exactly. The ROM itself is never
read here; regenerating the file is tools/reproduce_static_triage.py (local only).
"""
from pathlib import Path
import json
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]
PROJECTION = ROOT / "spec" / "reference_static_triage_81.json"
ABI1 = ROOT / "spec" / "abi1_reference_review.json"
TRACKED_SLOTS = (0x1E, 0x3C, 0x3D, 0x3F, 0x40, 0x41, 0x42)


class ProjectionShapeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(PROJECTION.read_text(encoding="utf-8"))

    def test_schema_and_scope_are_static_only(self):
        self.assertEqual(self.data["schema"], "oss-pce-cd-bios/reference-rom-static-audit/v1")
        self.assertEqual(self.data["scope"], "STATIC_PROFILE_ONLY")
        self.assertEqual(self.data["execution_permission"], "NOT_PRODUCED_BY_THIS_TOOL")

    def test_all_81_slots_present_once_in_order(self):
        self.assertEqual([api["slot"] for api in self.data["apis"]], list(range(81)))

    def test_jump_table_is_81_distinct_jmp_abs_entries(self):
        table = self.data["jump_table"]
        self.assertEqual(table["entry_count"], 81)
        self.assertEqual(table["distinct_targets"], 81)
        self.assertTrue(table["all_entries_are_jmp_abs"])
        self.assertTrue(table["all_targets_distinct"])

    def test_payload_provenance_is_recorded_without_header(self):
        prov = self.data["provenance"]
        self.assertEqual(prov["image_bytes"], 262144)
        self.assertEqual(prov["header_bytes"], 0)
        self.assertEqual(prov["trust"], "LOCAL_ONLY_NOT_VERIFIED")

    def test_projection_contains_no_long_hex_runs(self):
        # ROM bytes would appear as long hex strings; the projection carries only counts and booleans.
        self.assertIsNone(re.search(r"[0-9A-Fa-f]{32,}", PROJECTION.read_text(encoding="utf-8")))


class TrackedConsistencyTests(unittest.TestCase):
    """The seven APIs reviewed in ABI-1 must reproduce their committed static facts exactly."""

    @classmethod
    def setUpClass(cls):
        cls.projection = json.loads(PROJECTION.read_text(encoding="utf-8"))
        cls.ledger = json.loads(ABI1.read_text(encoding="utf-8"))

    def test_seven_tracked_slots_match_abi1_static_ledger(self):
        by_slot = {api["slot"]: api for api in self.projection["apis"]}
        ledger = {int(api["api_id"].split("-")[1], 16): api for api in self.ledger["apis"]}
        self.assertEqual(set(ledger), set(TRACKED_SLOTS))
        for slot in TRACKED_SLOTS:
            with self.subTest(slot=hex(slot)):
                mine, theirs = by_slot[slot], ledger[slot]
                self.assertEqual(mine["candidate_entry"], theirs["candidate_entry"])
                static = theirs["static"]
                callee = mine["callee"]
                self.assertEqual(callee["instructions_decoded"], static["callee_instructions"])
                self.assertEqual(callee["hazard_counts"], static["hazard_counts"])
                self.assertEqual(callee["max_stack_bytes"], static["max_stack_bytes"])
                self.assertEqual(callee["non_returning_states"], static["non_returning_states"])
                self.assertEqual(callee["register_swap_ops"], static["register_swap_ops"])
                self.assertEqual(callee["returns_observed"], static["return_kinds"])
                self.assertEqual(callee["store_target_classes"], static["store_target_classes"])
                self.assertEqual(callee["unresolved"], static["unresolved"])
                self.assertEqual(callee["zero_page_operand_stores"], static["zero_page_operand_stores"])


class AbiStateTests(unittest.TestCase):
    def test_abi1_ledger_still_claims_no_verified_state(self):
        ledger = json.loads(ABI1.read_text(encoding="utf-8"))
        self.assertTrue(all(api["abi_state"] == "NOT_RUN" for api in ledger["apis"]))


if __name__ == "__main__":
    unittest.main()
