"""Deterministic host checks for the CD read-ahead A/B harness (no emulator, no WSL)."""
import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

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


class BuildParallelismTests(unittest.TestCase):
    def test_default_is_two_jobs_even_on_large_hosts(self):
        self.assertEqual(cdra.DEFAULT_BUILD_JOBS, 2)
        self.assertEqual(cdra.validate_build_jobs(cdra.DEFAULT_BUILD_JOBS), 2)

    def test_explicit_single_and_max_jobs_are_allowed(self):
        self.assertEqual(cdra.validate_build_jobs(1), 1)
        self.assertEqual(cdra.validate_build_jobs(cdra.MAX_BUILD_JOBS), 4)

    def test_invalid_job_counts_fail_before_compilation(self):
        for value in (0, -1, 5, 100, True, "2", 2.5):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "build-jobs"):
                cdra.validate_build_jobs(value)

    def test_build_variant_passes_bounded_jobs_to_make(self):
        setup = SimpleNamespace(debugger_overrides=lambda _: ["CXXFLAGS=-O2 " + cdra.DEFINE])
        with patch.object(cdra, "_run") as invoke:
            cdra.build_variant(Path("/synthetic/geargrafx"), True, setup, build_jobs=1)
            cmd = invoke.call_args.args[0]
            self.assertIn("-j1", cmd)
            self.assertNotIn("-j4", cmd)

    def test_build_variant_rejects_out_of_range_without_running_make(self):
        setup = SimpleNamespace(debugger_overrides=lambda _: ["CXXFLAGS=-O2 " + cdra.DEFINE])
        with patch.object(cdra, "_run") as invoke:
            with self.assertRaisesRegex(ValueError, "build-jobs"):
                cdra.build_variant(Path("/synthetic/geargrafx"), False, setup, build_jobs=20)
            invoke.assert_not_called()


if __name__ == "__main__":
    unittest.main()
