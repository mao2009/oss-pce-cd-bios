"""Ensure catalog case cannot overstate unsupported real-core execution."""
import sys
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from reference_case_execute import validate_case_pair
from reference_bios_runner import Blocked

class ReferenceCaseExecuteTests(unittest.TestCase):
    def setUp(self):
        self.case = {
            "id": "slot-1e/candidate_nominal", "api_id": "slot-1e",
            "reviewed_abi": True, "measurement_status": "NOT_MEASURED",
            "expected": None, "timeout_instructions": 100,
            "input_fixture": {"mode": "synthetic_guest_jsr", "initial_a": 0,
                              "initial_x": 0, "initial_y": 0, "initial_sp": 255,
                              "expected_entry": "0xE05A"},
        }
        self.cfg = {
            "schema": "oss-pce-cd-bios/reference-bios-run-config/v1",
            "api_id": "slot-1e", "bios_path": "private.pce", "bios_sha256": "a"*64,
            "test_rom_path": "test.pce", "test_rom_sha256": "b"*64,
            "emulator_revision": "pinned-revision",
            "max_instructions": 100,
            "ram_write_allowlist": [{"start": 0x2000, "end": 0x2100}],
            "isolated_run": True, "reviewed_abi": True, "approved_fixture": True,
        }

    def test_explicit_reviewed_fixture_can_reach_adapter_boundary(self):
        self.assertIsNone(validate_case_pair(self.case, self.cfg))

    def test_unreviewed_blocked(self):
        with self.assertRaises(Blocked):
            validate_case_pair(dict(self.case, reviewed_abi=False), self.cfg)

    def test_other_service_blocked(self):
        with self.assertRaises(Blocked):
            validate_case_pair(dict(self.case, id="slot-03/candidate_nominal",
                                    api_id="slot-03"), self.cfg)

    def test_disallowed_variation_blocked(self):
        for change in ({"initial_a": 1}, {"expected_entry": "0xE009"},
                       {"mode": "other"}, {"initial_sp": -1}):
            case = dict(self.case, input_fixture=dict(self.case["input_fixture"], **change))
            with self.subTest(change=change), self.assertRaises(Blocked):
                validate_case_pair(case, self.cfg)

    def test_budget_and_trust_mismatch_blocked(self):
        for cfg_change in ({"max_instructions": 101}, {"approved_fixture": False},
                           {"api_id": "slot-03"}):
            with self.subTest(cfg_change=cfg_change), self.assertRaises(Blocked):
                validate_case_pair(self.case, dict(self.cfg, **cfg_change))

    def test_measured_not_promoted_to_retest(self):
        with self.assertRaises(Blocked):
            validate_case_pair(dict(self.case, measurement_status="MEASURED"), self.cfg)

if __name__ == "__main__":
    unittest.main()
