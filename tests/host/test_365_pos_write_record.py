#!/usr/bin/env python3
"""Issue #365: host-side judgment of the recorded positive-control evidence.

The fixture tests/fixtures/365-pos-write-evidence.json holds the pos-write row
printed by the live probe (tests/integration/geargrafx_st_boundary.py, `make
st-boundary`) on the pinned core: an allowlisted RAM store at $3E50 that must
run unrefused and land. This test only re-judges that recorded row with the
probe's own classifier (check_pos). It never runs a core, never runs a BIOS,
and never fills a value that the fixture does not contain.

Scope: synthetic single-stepped guests only. Nothing here releases a gate:
slot $01 stays BLOCKED, release_eligible stays false, and no reference-BIOS
ABI or DEV ROM wiring is claimed. A debugger stop before a side effect is not
a pre-side-effect block for a free-running guest.
"""
import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "integration"))

import geargrafx_st_boundary as st  # noqa: E402
from geargrafx_reference_guard import ALLOWED_VALUE, check_pos  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "365-pos-write-evidence.json"
POS_TARGET = 0x3E50


class PosWriteRecordTests(unittest.TestCase):
    def setUp(self):
        with FIXTURE.open(encoding="utf-8") as handle:
            self.record = json.load(handle)
        self.assertEqual(len(self.record["cases"]), 1)
        self.case = self.record["cases"][0]
        self.row = self.case["row"]

    def test_provenance_is_real_core_on_synthetic_bios(self):
        self.assertEqual(self.record["observation_kind"], "REAL_CORE_EXECUTION")
        self.assertEqual(self.record["core_revision"], st.PINNED_CORE_REVISION)
        self.assertEqual(self.record["bios"], "SYNTHETIC")
        self.assertEqual(self.case["case"], "pos-write")
        self.assertEqual(self.case["observation_kind"], "REAL_CORE_EXECUTION")
        self.assertEqual(self.case["core_revision"], st.PINNED_CORE_REVISION)
        self.assertEqual(self.row.get("mode"), "pos-write")

    def test_allowlisted_store_lands_unrefused(self):
        check_pos(copy.deepcopy(self.row), POS_TARGET)
        self.assertEqual(self.row["guard_after"], ALLOWED_VALUE)
        self.assertEqual(self.row["guard_target"], POS_TARGET)

    def test_jsr_view_reports_ok_without_irq(self):
        observation = st.to_jsr_observation(copy.deepcopy(self.row))
        self.assertIsNotNone(observation)
        self.assertEqual(observation["outcome"]["status"], "ok")
        self.assertEqual(observation["provenance"]["kind"], "REAL_CORE_EXECUTION")
        self.assertEqual(observation["irq"]["status"], "UNKNOWN")

    def test_wrong_target_is_rejected(self):
        with self.assertRaises(ValueError):
            check_pos(copy.deepcopy(self.row), 0x3E51)

    def test_store_that_did_not_land_is_rejected(self):
        for tamper in ({"guard_after": self.row["guard_before"]},
                       {"guard_after": ALLOWED_VALUE ^ 0xFF},
                       {"guard_before": ALLOWED_VALUE},
                       {"status": "refused"},
                       {"detected": True},
                       {"instructions_executed": 0}):
            with self.subTest(tamper=tamper):
                row = copy.deepcopy(self.row)
                row.update(tamper)
                with self.assertRaises(ValueError):
                    check_pos(row, POS_TARGET)

    def test_missing_exit_or_status_is_rejected(self):
        row = copy.deepcopy(self.row)
        row.pop("status")
        with self.assertRaises(ValueError):
            check_pos(row, POS_TARGET)
        row = copy.deepcopy(self.row)
        row["exit"] = 1
        with self.assertRaises(ValueError):
            check_pos(row, POS_TARGET)


if __name__ == "__main__":
    unittest.main()
