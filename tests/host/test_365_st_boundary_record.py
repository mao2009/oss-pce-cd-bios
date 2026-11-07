#!/usr/bin/env python3
"""Issue #365: host-side judgment of the recorded ST0/ST1/ST2 instruction-boundary evidence.

The fixture tests/fixtures/365-st-boundary-evidence.json holds the rows printed by the existing
synthetic-ROM probe (tests/integration/geargrafx_st_boundary.py, PR #383) on the pinned core.
This test only re-judges those recorded rows with the probe's own classifiers. It never runs a core,
never runs a BIOS, and never fills a value that the fixture does not contain.
"""
import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "integration"))

import geargrafx_st_boundary as st  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "365-st-boundary-evidence.json"
EXPECTED_CASES = ("st0-entry", "st0-unguarded", "st1-unknown", "st1-unguarded", "st2-unknown", "st2-unguarded")
ARMED = ("st0-entry", "st1-unknown", "st2-unknown")


def unguarded_of(mode):
    return mode.split("-")[0] + "-unguarded"


class StBoundaryRecordTests(unittest.TestCase):
    def setUp(self):
        with FIXTURE.open(encoding="utf-8") as handle:
            self.record = json.load(handle)
        self.rows = {case["case"]: case["row"] for case in self.record["cases"]}

    def test_provenance_is_real_core_on_synthetic_bios(self):
        self.assertEqual(self.record["observation_kind"], "REAL_CORE_EXECUTION")
        self.assertEqual(self.record["core_revision"], st.PINNED_CORE_REVISION)
        self.assertEqual(self.record["bios"], "SYNTHETIC")
        self.assertEqual(tuple(sorted(self.rows)), tuple(sorted(EXPECTED_CASES)))

    def test_armed_cases_refused_before_side_effect(self):
        for mode in ARMED:
            with self.subTest(mode=mode):
                verdict = st.check_refused_historical(copy.deepcopy(self.rows[mode]), mode)
                self.assertEqual(verdict, st.REFUSED_VERDICT)

    def test_disabled_monitor_cases_land_side_effect(self):
        for mode in ARMED:
            with self.subTest(mode=unguarded_of(mode)):
                verdict = st.check_unguarded(copy.deepcopy(self.rows[unguarded_of(mode)]), unguarded_of(mode))
                self.assertEqual(verdict, st.LANDED_VERDICT)

    def test_recorded_mutations_are_rejected(self):
        for mode in ARMED:
            with self.subTest(mode=mode):
                st.mutation_test_armed(self.rows[mode], self.rows[unguarded_of(mode)], mode, historical=True)

    def test_removed_opcode_check_proxy_is_rejected(self):
        # Opcode check removed while the boundary stop stays: the ST runs, so the armed row must not verify.
        for mode in ARMED:
            with self.subTest(mode=mode):
                broken = copy.deepcopy(self.rows[unguarded_of(mode)])
                broken.update({"mode": mode, "armed": True, "boundary_check": True})
                with self.assertRaises(ValueError):
                    st.check_refused(broken, mode)

    def test_unguarded_row_cannot_pass_as_refused(self):
        for mode in ARMED:
            with self.subTest(mode=unguarded_of(mode)):
                with self.assertRaises(ValueError):
                    st.check_refused(copy.deepcopy(self.rows[unguarded_of(mode)]), mode)

    def test_mpr0_is_never_ff_in_recorded_rows(self):
        for name, row in self.rows.items():
            with self.subTest(case=name):
                self.assertEqual(row["before"]["mpr"][0], st.MPR0_NON_FF)
                self.assertEqual(row["after"]["mpr"][0], st.MPR0_NON_FF)


class StEntryCoverageTest(unittest.TestCase):
    """Entry-PC coverage for all three ST opcodes (Issue #365).

    Host-only static checks: the driver must define an armed entry refusal for
    ST0/ST1/ST2 at the guest entry PC with 0 executed instructions, and each
    entry must have a no-entry-check RED mutation whose normalized mode is the
    entry itself. The recorded fixture above stays untouched.
    """

    def test_armed_modes_include_entry_for_each_st(self):
        for kind in ("st0", "st1", "st2"):
            with self.subTest(kind=kind):
                self.assertIn(f"{kind}-entry", st.ARMED_MODES)

    def test_entry_modes_refuse_at_guest_entry_with_zero_executed(self):
        for kind in ("st0", "st1", "st2"):
            mode = f"{kind}-entry"
            with self.subTest(mode=mode):
                self.assertEqual(st.ST_EXPECTED_PC[mode], 0x3E00)
                self.assertEqual(st.ST_EXECUTED_BEFORE_REFUSAL[mode], 0)

    def test_each_entry_has_no_entry_check_mutation(self):
        for kind in ("st0", "st1", "st2"):
            mode = f"{kind}-entry"
            with self.subTest(mode=mode):
                self.assertIn(f"{kind}-entry-no-entry-check", st.MUTATIONS[mode])


if __name__ == "__main__":
    unittest.main()
