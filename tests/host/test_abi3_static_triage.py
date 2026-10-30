"""Public boundary checks for the Phase ABI-3 static triage (Issue #476; owner ruling A, Issue #324).

No per-slot projection is published. The local regeneration tool
(tools/reproduce_static_triage.py) writes outside the repository and needs the private image, so these
tests need no ROM. They check that the public files carry only management state, and that the seven
ABI-1 slots keep no detail derived from the private image.
"""
from pathlib import Path
import json
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import abi1_reference_review as review  # noqa: E402

REMOVED_PROJECTION = ROOT / "docs" / "evidence" / "reference-static-triage-81.json"
ABI1 = ROOT / "spec" / "abi1_reference_review.json"
TRACKED_SLOTS = (0x1E, 0x3C, 0x3D, 0x3F, 0x40, 0x41, 0x42)


class PublicBoundaryTests(unittest.TestCase):
    def test_the_81_slot_projection_is_not_committed(self):
        self.assertFalse(REMOVED_PROJECTION.exists())

    def test_public_evidence_files_carry_no_private_image_detail(self):
        public = [ABI1, *sorted((ROOT / "docs" / "evidence").glob("*.json"))]
        for path in public:
            with self.subTest(path=path.name):
                document = json.loads(path.read_text(encoding="utf-8"))
                # Raises Invalid for any forbidden key, at any depth.
                review._public_keys(document)

    def test_a_private_image_detail_key_is_refused_at_any_depth(self):
        for key in ("callee_instructions", "hazard_counts", "static_verdict", "static_items", "instructions_decoded"):
            with self.subTest(key=key):
                with self.assertRaises(review.Invalid):
                    review._public_keys({"apis": [{"safety": {key: 1}}]})


class TrackedSlotTests(unittest.TestCase):
    """The seven ABI-1 slots keep only management state: a static review was performed."""

    @classmethod
    def setUpClass(cls):
        cls.ledger = json.loads(ABI1.read_text(encoding="utf-8"))

    def test_seven_tracked_slots_are_the_phase_scope(self):
        slots = {int(api["api_id"].split("-")[1], 16) for api in self.ledger["apis"]}
        self.assertEqual(slots, set(TRACKED_SLOTS))

    def test_each_slot_has_only_management_state(self):
        for api in self.ledger["apis"]:
            with self.subTest(api=api["api_id"]):
                self.assertNotIn("static", api)
                self.assertEqual(api["safety"]["execution_permission"], "BLOCKED")
                self.assertNotIn("static_items", api)


class AbiStateTests(unittest.TestCase):
    def test_abi1_ledger_still_claims_no_verified_state(self):
        ledger = json.loads(ABI1.read_text(encoding="utf-8"))
        self.assertTrue(all(api["abi_state"] == "NOT_RUN" for api in ledger["apis"]))


if __name__ == "__main__":
    unittest.main()
