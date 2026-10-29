"""Deterministic host checks for the CD read-ahead A/B harness (no emulator, no WSL)."""
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location("cdra_compare", ROOT / "tests/integration/geargrafx_cdra_compare.py")
cdra = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cdra)

CONTROL = "lba5x1"
POSITIVE = ("lba100x3", "lba200x3")


class FirstDivergenceTests(unittest.TestCase):
    def setUp(self):
        self.series = [("HIT", {"name": "a", "step": 1}), ("PHASE", {"step": 5, "scsi_phase": 4}), ("RUN", {"step": 9})]

    def test_time_series_reports_first_differing_index_not_later_ones(self):
        other = [self.series[0], ("PHASE", {"step": 6, "scsi_phase": 4}), ("RUN", {"step": 10})]
        self.assertEqual(cdra.first_divergence(self.series, other),
                         {"index": 1, "tag": "PHASE", "fields_differ": ["step"]})

    def test_identical_series_has_no_divergence(self):
        self.assertIsNone(cdra.first_divergence(self.series, list(self.series)))

    def test_length_mismatch_reports_length_at_shorter_end(self):
        self.assertEqual(cdra.first_divergence(self.series, self.series[:2]),
                         {"index": 2, "tag": "LENGTH", "fields_differ": []})

    def test_field_present_only_in_later_row_is_reported(self):
        other = [self.series[0], ("PHASE", {"step": 5, "scsi_phase": 4, "extra": 1}), self.series[2]]
        self.assertEqual(cdra.first_divergence(self.series, other)["fields_differ"], ["extra"])

    def test_tag_mismatch_is_reported_as_tag(self):
        other = [("RUN", {"name": "a", "step": 1})] + self.series[1:]
        self.assertEqual(cdra.first_divergence(self.series, other)["fields_differ"], ["tag"])


class GuestViewTests(unittest.TestCase):
    def test_keeps_emission_order_and_drops_buffer_hex(self):
        records = {"EVENTS": [("RUN", {"step": 3, "buffer_hex": "aa", "result_hex": "bb", "reason": "r"}),
                              ("HIT", {"name": "x", "buffer_head_hex": "cc"})]}
        self.assertEqual(cdra.guest_view(records), [["RUN", {"step": 3, "reason": "r"}], ["HIT", {"name": "x"}]])


class ReproducibilityVerdictTests(unittest.TestCase):
    GUEST = [["HIT", {"name": "x"}], ["RUN", {"step": 3}]]

    def test_vfs_statistics_do_not_change_the_verdict(self):
        runs = [{"guest": self.GUEST, "metrics": {"vfs_prefetch_reads": 0, "vfs_demand_bytes": 10}},
                {"guest": self.GUEST, "metrics": {"vfs_prefetch_reads": 3, "vfs_demand_bytes": 99}}]
        self.assertTrue(cdra.reproducibility_verdict(runs))

    def test_guest_difference_fails_the_verdict(self):
        runs = [{"guest": self.GUEST, "metrics": {}},
                {"guest": [["HIT", {"name": "x"}], ["RUN", {"step": 4}]], "metrics": {}}]
        self.assertFalse(cdra.reproducibility_verdict(runs))


class ReadAheadExerciseGateTests(unittest.TestCase):
    def test_control_and_positive_pass_when_positive_prefetches_and_control_does_not(self):
        on = {CONTROL: 0, "lba100x3": 0, "lba200x3": 2}
        off = {CONTROL: 0, "lba100x3": 0, "lba200x3": 0}
        self.assertEqual(cdra.readahead_gate(on, off, CONTROL, POSITIVE), [])

    def test_control_that_prefetches_is_invalid_as_a_control(self):
        on = {CONTROL: 1, "lba100x3": 0, "lba200x3": 2}
        off = {CONTROL: 0, "lba100x3": 0, "lba200x3": 0}
        self.assertEqual(len(cdra.readahead_gate(on, off, CONTROL, POSITIVE)), 1)

    def test_positive_cases_must_exercise_read_ahead(self):
        on = {CONTROL: 0, "lba100x3": 0, "lba200x3": 0}
        off = {CONTROL: 0, "lba100x3": 0, "lba200x3": 0}
        self.assertEqual(len(cdra.readahead_gate(on, off, CONTROL, POSITIVE)), 1)

    def test_off_worker_reads_fail_in_any_scenario(self):
        on = {CONTROL: 0, "lba100x3": 0, "lba200x3": 2}
        off = {CONTROL: 0, "lba100x3": 1, "lba200x3": 0}
        self.assertEqual(len(cdra.readahead_gate(on, off, CONTROL, POSITIVE)), 1)


if __name__ == "__main__":
    unittest.main()
