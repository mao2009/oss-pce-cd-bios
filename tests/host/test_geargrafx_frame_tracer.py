"""Synthetic protocol tests for the normal-frame tracer; no Geargrafx core runs here."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration"))
import geargrafx_frame_tracer as tracer  # noqa: E402


def event(kind="sample", frame=1):
    row = {"kind": kind, "frame": frame, "master_clock": 1, "pc": 57344, "pc_page": 7, "pc_bank": 0,
           "physical_pc": 0, "pc_is_wram": False, "a": 0, "x": 0, "y": 0, "p": 4, "sp": 255,
            "mpr": [0] * 8}
    if kind == "wram":
        row.update(changed=2, first_wram=512, last_wram=513)
    if kind == "cd":
        row.update(snapshot="FRAME_SNAPSHOT", cd_event=1, command=8, lba=5, status=0)
    return row


def output(events, dropped=0, scsi_events_dropped=0, capacity=4, run=None):
    cap = {"normal_frame_execution": True, "instruction_step": False, "guest_mutation": False, "scsi_event_notification": True,
            "mmio_reads": False, "ring_capacity": capacity, "frames": 2, "disc_given": False,
           "ram_delta_summary": True,
            "run_input_requested": bool(run), "run_start_frame": 1 if run else 0, "run_hold_frames": 2 if run else 0}
    end = {"frames": 2, "events": len(events), "dropped": dropped, "scsi_events_dropped": scsi_events_dropped,
           "overflow": bool(dropped or scsi_events_dropped), "ram_deltas": ram_deltas()}
    inputs = [] if not run else ["INPUT " + json.dumps({"frame": 1, "action": "press", "run_pressed": True}),
                                 "INPUT " + json.dumps({"frame": 2, "action": "release", "run_pressed": False})]
    return "\n".join(["CAP " + json.dumps(cap), *inputs, *["EVENT " + json.dumps(row) for row in events],
                       "END " + json.dumps(end)])


def ram_deltas():
    return [
        {"region": "wram", "present": True, "availability": "PRESENT", "size": 8192,
         "bank_first": 248, "bank_last": 251, "updates": 2, "first_frame": 1, "last_frame": 2,
         "min_offset": 1, "max_offset": 8, "max_changed": 4, "scsi_correlation": "OBSERVED",
         "scsi_update_frames": [2]},
        {"region": "cdrom_ram", "present": False, "availability": "ABSENT", "updates": 0,
         "first_frame": None, "last_frame": None, "min_offset": None, "max_offset": None,
         "max_changed": 0, "scsi_correlation": "NONE_OBSERVED", "scsi_update_frames": []},
        {"region": "card_ram", "present": None, "availability": "UNKNOWN", "updates": 0,
         "first_frame": None, "last_frame": None, "min_offset": None, "max_offset": None,
         "max_changed": 0, "scsi_correlation": "UNKNOWN", "scsi_update_frames": []},
    ]


class FrameTracerProtocolTests(unittest.TestCase):
    def test_accepts_bounded_cpu_cd_memory_and_handoff_events(self):
        rows = [event(), event("cd"), event("wram"), event("handoff", 2)]
        cap, parsed, end = tracer.parse_records(output(rows))
        self.assertEqual(cap["ring_capacity"], 4)
        self.assertEqual([row["kind"] for row in parsed], ["sample", "cd", "wram", "handoff"])
        self.assertFalse(end["overflow"])

    def test_ring_overflow_is_explicit_and_retains_only_capacity(self):
        rows = [event("sample", 2), event("handoff", 2)]
        _, parsed, end = tracer.parse_records(output(rows, dropped=7, capacity=2))
        self.assertEqual(len(parsed), 2)
        self.assertEqual(end["dropped"], 7)
        self.assertTrue(end["overflow"])

    def test_core_scsi_event_loss_is_explicit_overflow(self):
        _, _, end = tracer.parse_records(output([event("cd")], scsi_events_dropped=3))
        self.assertTrue(end["overflow"])
        self.assertEqual(end["scsi_events_dropped"], 3)

    def test_ram_summary_survives_output_ring_overflow(self):
        _, _, end = tracer.parse_records(output([event("sample", 2)], dropped=99, capacity=1))
        self.assertEqual(end["ram_deltas"][0]["updates"], 2)
        self.assertEqual(end["ram_deltas"][0]["scsi_update_frames"], [2])

    def test_ram_summary_distinguishes_absent_and_unknown_regions(self):
        _, _, end = tracer.parse_records(output([event()]))
        self.assertEqual(end["ram_deltas"][1]["availability"], "ABSENT")
        self.assertEqual(end["ram_deltas"][2]["availability"], "UNKNOWN")

    def test_refuses_invalid_ram_summary_mapping_or_unavailable_updates(self):
        bad_mapping = output([event()]).replace('"bank_last": 251', '"bank_last": 250')
        bad_missing = output([event()]).replace('"updates": 0, "first_frame": null', '"updates": 1, "first_frame": null', 1)
        for text in (bad_mapping, bad_missing):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    tracer.parse_records(text)

    def test_run_schedule_uses_press_hold_release_boundaries(self):
        self.assertEqual(tracer.run_schedule(2, 3, 5), ((2, "press"), (4, "release")))
        tracer.parse_records(output([event()], run=True))
        for values in ((None, 1, 2), (2, None, 2), (2, 2, 2)):
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    tracer.run_schedule(*values)

    def test_refuses_scsi_records_without_frame_snapshot_label(self):
        row = event("cd")
        row["snapshot"] = "EVENT_TIME"
        with self.assertRaisesRegex(ValueError, "FRAME_SNAPSHOT"):
            tracer.parse_records(output([row]))

    def test_temp_paths_reject_outside_root_repo_home_and_worktrees_without_deletion(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            parent = root / "frame-tracer-jobs"
            parent.mkdir()
            worktree = root / "worktree"
            worktree.mkdir()
            outside = Path("/tmp/frame-tracer-outside")
            with mock.patch.object(tracer, "OPENCODE_TEMP_ROOT", root), mock.patch.object(tracer, "JOB_PARENT", parent):
                cases = [(outside, "not_direct_child"), (Path("/"), "prohibited_path"),
                         (Path.home(), "prohibited_path"), (tracer.ROOT, "prohibited_path"),
                         (worktree / "job", "opencode_worktree")]
                for path, reason in cases:
                    with self.subTest(path=path):
                        self.assertEqual(tracer.validate_tracked_job_dir(path), (False, reason))
            self.assertTrue(worktree.is_dir())

    def test_temp_paths_reject_symlink_missing_and_unowned_jobs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.mkdir()
            parent_link = root / "frame-tracer-jobs"
            parent_link.symlink_to(target, target_is_directory=True)
            with mock.patch.object(tracer, "OPENCODE_TEMP_ROOT", root), mock.patch.object(tracer, "JOB_PARENT", parent_link):
                self.assertEqual(tracer.validate_tracked_job_dir(parent_link / "job"), (False, "unsafe_parent"))

            parent_link.unlink()
            parent_link.mkdir()
            symlink_job = parent_link / "job"
            symlink_job.symlink_to(target, target_is_directory=True)
            with mock.patch.object(tracer, "OPENCODE_TEMP_ROOT", root), mock.patch.object(tracer, "JOB_PARENT", parent_link):
                self.assertEqual(tracer.validate_tracked_job_dir(symlink_job), (False, "unsafe_job"))
                self.assertEqual(tracer.validate_tracked_job_dir(parent_link / "missing"), (False, "missing"))
                owned = parent_link / "owned"
                owned.mkdir()
                real_lstat = tracer.os.lstat

                def unowned_lstat(path):
                    result = real_lstat(path)
                    if Path(path) == owned:
                        values = list(result)
                        values[4] += 1  # st_uid
                        return tracer.os.stat_result(values)
                    return result

                with mock.patch.object(tracer.os, "lstat", side_effect=unowned_lstat):
                    self.assertEqual(tracer.validate_tracked_job_dir(owned), (False, "not_owned"))

    def test_valid_tracked_job_is_created_and_removed_only_under_dedicated_parent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            parent = root / "frame-tracer-jobs"
            with mock.patch.object(tracer, "OPENCODE_TEMP_ROOT", root), mock.patch.object(tracer, "JOB_PARENT", parent):
                job, identity = tracer.make_tracked_job_dir()
                self.assertEqual(tracer.validate_tracked_job_dir(job, identity), (True, "ok"))
                self.assertEqual(tracer.cleanup_tracked_job_dir(job, identity), (True, "removed"))
                self.assertFalse(job.exists())

    def test_cleanup_quarantines_original_and_preserves_post_rename_replacement(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            parent = root / "frame-tracer-jobs"
            with mock.patch.object(tracer, "OPENCODE_TEMP_ROOT", root), mock.patch.object(tracer, "JOB_PARENT", parent):
                job, identity = tracer.make_tracked_job_dir()
                (job / "original").write_text("remove")
                real_rename = tracer.os.rename

                def replace_after_quarantine(source, destination, **kwargs):
                    real_rename(source, destination, **kwargs)
                    if source == job.name:
                        job.mkdir()
                        (job / "replacement").write_text("keep")

                with mock.patch.object(tracer.os, "rename", side_effect=replace_after_quarantine):
                    self.assertEqual(tracer.cleanup_tracked_job_dir(job, identity), (True, "removed"))
                self.assertEqual((job / "replacement").read_text(), "keep")

    def test_cleanup_failure_is_reported_not_suppressed(self):
        args = SimpleNamespace(frames=1, events=1, timeout=1, rom=Path("ignored"), cue=None,
                               run_start_frame=None, run_hold_frames=None)
        with mock.patch.object(tracer, "doctor", return_value={"status": "PASS"}), \
                mock.patch.object(tracer, "make_tracked_job_dir", return_value=(Path("/safe/job"), (1, 2, 3))), \
                mock.patch.object(tracer, "compile_probe", side_effect=ValueError("blocked")), \
                mock.patch.object(tracer, "cleanup_tracked_job_dir", return_value=(False, "identity_changed")):
            log = tracer.trace(args)
        self.assertEqual(log["status"], "FAIL")
        self.assertEqual(log["cleanup"], "FAILED_IDENTITY_CHANGED")
        self.assertEqual(log["reason"], "cleanup failed: identity_changed")

    @unittest.skipUnless(shutil.which("c++"), "C++ compiler unavailable")
    def test_ring_capacities_are_executed_in_cpp(self):
        source = Path(__file__).with_name("geargrafx_frame_tracer_ring_test.cpp")
        include = source.parents[1] / "integration"
        with tempfile.TemporaryDirectory() as temporary:
            for stem in ("geargrafx_frame_tracer_ring_test", "geargrafx_frame_tracer_ram_summary_test"):
                source = Path(__file__).with_name(stem + ".cpp")
                binary = Path(temporary) / stem
                subprocess.run(["c++", "-std=c++17", "-Wall", "-Wextra", "-Werror", "-I", str(include),
                                str(source), "-o", str(binary)], check=True)
                subprocess.run([str(binary)], check=True)

    def test_refuses_mutating_or_stepped_protocols_and_bad_ring_counts(self):
        cases = [
            output([event()]).replace('"guest_mutation": true', '"guest_mutation": false'),
            output([event()]).replace('"instruction_step": false', '"instruction_step": true'),
            output([event()] * 2, capacity=1),
            output([event()], dropped=1).replace('"overflow": true', '"overflow": false'),
        ]
        # First input starts mutating, then is rewritten to safe: construct the unsafe version separately.
        cases[0] = output([event()]).replace('"guest_mutation": false', '"guest_mutation": true')
        for text in cases:
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    tracer.parse_records(text)

    def test_tracer_source_has_no_guest_memory_reads_or_debugger_steps(self):
        source = (Path(__file__).resolve().parents[1] / "integration" / "geargrafx_frame_tracer.cpp").read_text()
        self.assertNotIn("memory->Read(", source)
        self.assertNotIn("SetMpr(", source)
        self.assertNotIn("RunInstruction(", source)
        self.assertNotIn("step_debugger = true", source)
        self.assertIn("GetTraceLogger()", source)
        self.assertIn("ram_deltas", source)
        self.assertNotIn("%02x", source)
        self.assertIn("core.KeyPressed(GG_CONTROLLER_1, GG_KEY_RUN)", source)
        self.assertIn("core.KeyReleased(GG_CONTROLLER_1, GG_KEY_RUN)", source)


if __name__ == "__main__":
    unittest.main()
