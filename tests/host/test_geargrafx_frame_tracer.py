"""Synthetic protocol tests for the normal-frame tracer; no Geargrafx core runs here."""
import json
import os
from pathlib import Path
import shutil
import stat
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
    if kind == "ram":
        row.update(ram="wram", size=8192, changed=2, first_offset=512, last_offset=513,
                   bank_first=248, bank_last=251)
    if kind == "cd":
        row.update(snapshot="FRAME_SNAPSHOT", cd_event=1, command=8, lba=5, status=0)
    if kind == "region":
        row = {"kind": "region", "frame": frame, "event_cycle": 1, "event_pc": 8192,
               "event_bank": 248, "from_region": "other", "to_region": "working_ram", "trace_gap": False}
    return row


def output(events, dropped=0, trace_events_dropped=0, capacity=4, run=None):
    cap = {"normal_frame_execution": True, "instruction_step": False, "guest_mutation": False, "scsi_event_notification": True,
            "ram_observation": True, "ram_regions": [], "cpu_event_notification": True,
           "mmio_reads": False, "ring_capacity": capacity, "frames": 2, "disc_given": False,
           "ram_delta_summary": True,
           "run_input_requested": bool(run), "run_start_frame": 1 if run else 0, "run_hold_frames": 2 if run else 0}
    end = {"frames": 2, "events": len(events), "dropped": dropped, "trace_events_dropped": trace_events_dropped,
           "overflow": bool(dropped or trace_events_dropped), "ram_deltas": ram_deltas()}
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
        rows = [event(), event("cd"), event("ram"), event("handoff", 2)]
        cap, parsed, end = tracer.parse_records(output(rows))
        self.assertEqual(cap["ring_capacity"], 4)
        self.assertEqual([row["kind"] for row in parsed], ["sample", "cd", "ram", "handoff"])
        self.assertFalse(end["overflow"])

    def test_ring_overflow_is_explicit_and_retains_only_capacity(self):
        rows = [event("sample", 2), event("handoff", 2)]
        _, parsed, end = tracer.parse_records(output(rows, dropped=7, capacity=2))
        self.assertEqual(len(parsed), 2)
        self.assertEqual(end["dropped"], 7)
        self.assertTrue(end["overflow"])

    def test_core_trace_loss_is_explicit_overflow(self):
        _, _, end = tracer.parse_records(output([event("region")], trace_events_dropped=3))
        self.assertTrue(end["overflow"])
        self.assertEqual(end["trace_events_dropped"], 3)

    def test_accepts_event_time_region_metadata_without_cpu_snapshot(self):
        _, rows, _ = tracer.parse_records(output([event("region")]))
        self.assertEqual(rows[0]["to_region"], "working_ram")

    def test_refuses_bad_event_time_region_metadata(self):
        row = event("region")
        row["event_bank"] = 256
        with self.assertRaisesRegex(ValueError, "region transition"):
            tracer.parse_records(output([row]))

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

    def test_accepts_observer_off_without_ram_events(self):
        text = output([event()]).replace('"ram_observation": true', '"ram_observation": false')
        cap, events, _ = tracer.parse_records(text, require_ram_observation=False)
        self.assertFalse(cap["ram_observation"])
        self.assertEqual([row["kind"] for row in events], ["sample"])

    def test_accepts_only_confirmed_ram_mappings_and_metadata_ranges(self):
        for ram, size, first, last in (("wram", 8192, 248, 251), ("cdrom_ram", 65536, 128, 135),
                                       ("card_ram", 32768, 64, 95), ("card_ram", 196608, 104, 127)):
            with self.subTest(ram=ram, size=size):
                row = event("ram")
                row.update(ram=ram, size=size, bank_first=first, bank_last=last)
                tracer.parse_records(output([row]))
        row = event("ram")
        row.update(ram="card_ram", size=32768, bank_first=64, bank_last=67)
        with self.assertRaisesRegex(ValueError, "unconfirmed RAM bank mapping"):
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
                                run_start_frame=None, run_hold_frames=None, observe_ram=True)
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
        include = Path(__file__).resolve().parents[1] / "integration"
        with tempfile.TemporaryDirectory() as temporary:
            for stem in ("geargrafx_frame_tracer_ring_test", "geargrafx_frame_tracer_ram_test",
                         "geargrafx_frame_tracer_ram_summary_test"):
                source = Path(__file__).with_name(stem + ".cpp")
                binary = Path(temporary) / stem
                subprocess.run(["c++", "-std=c++17", "-Wall", "-Wextra", "-Werror", "-I", str(include),
                                str(source), "-o", str(binary)], check=True)
                subprocess.run([str(binary)], check=True)

    @unittest.skipUnless(shutil.which("c++"), "C++ compiler unavailable")
    def test_confirmed_trace_cpu_bank_regions_are_executed_in_cpp(self):
        source = Path(__file__).with_name("geargrafx_trace_cpu_region_test.cpp")
        include = source.parents[1] / "integration"
        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary) / "region-test"
            subprocess.run(["c++", "-std=c++11", "-Wall", "-Wextra", "-Werror", "-I", str(include),
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
        self.assertIn("GetWorkingRAM()", source)
        self.assertIn("GetCDROMRAM()", source)
        self.assertIn("GetCardRAM()", source)
        self.assertNotIn("%02x", source)
        self.assertIn("ram_deltas", source)
        self.assertIn("TRACE_FLAG_CPU", source)
        self.assertIn("entry.cpu.bank", source)
        self.assertNotIn("entry.cpu.opcodes", source)
        self.assertNotIn("entry.cpu.name", source)
        self.assertIn("core.KeyPressed(GG_CONTROLLER_1, GG_KEY_RUN)", source)
        self.assertIn("core.KeyReleased(GG_CONTROLLER_1, GG_KEY_RUN)", source)


def _state_row(frame, wram_changed=0, pc=0x100):
    return {"frame": frame, "master_clock": frame * 100, "pc": pc, "pc_bank": 0xF8, "a": 0, "x": 0, "y": 0,
            "p": 0, "sp": 0x80, "mpr": [0xF8, 0xF9, 0xFA, 0xFB, 0xF7, 0xF8, 0, 0], "cd_phase": 0,
            "cd_signals": 0, "cd_load_sector": 0, "cd_load_count": 0, "cd_irq_active": 0, "cd_irq_enabled": 0,
            "ram_hash": "0" * 16, "ram_changed": {"wram": wram_changed, "cdrom_ram": 0, "card_ram": 0}}


def _scsi_row(frame, events=0, incomplete=False):
    return {"frame": frame, "events": events, "incomplete": incomplete}


def _index(rows):
    """Frame-keyed mapping, as parse_frame_records returns it."""
    return {row["frame"]: row for row in rows}


def _stdout(mode, states, scsis):
    states = list(states.values()) if isinstance(states, dict) else states
    scsis = list(scsis.values()) if isinstance(scsis, dict) else scsis
    cap = {"trace_mode": mode, "cpu_event_notification": mode == "full", "ram_observation": True}
    lines = ["CAP " + json.dumps(cap)]
    lines += ["STATE " + json.dumps(row) for row in states]
    lines += ["SCSI " + json.dumps(row) for row in scsis]
    return "\n".join(lines) + "\n"


def _ram_rows(wram_updates=0, first=None, last=None):
    wram = {"region": "wram", "present": True, "availability": "PRESENT", "size": 0x2000, "bank_first": 0xF8,
            "bank_last": 0xFB, "updates": wram_updates, "first_frame": first, "last_frame": last,
            "min_offset": 0 if wram_updates else None, "max_offset": 7 if wram_updates else None,
            "max_changed": 1 if wram_updates else 0, "scsi_correlation": "NONE_OBSERVED", "scsi_update_frames": []}
    absent = [{"region": name, "present": False, "availability": "ABSENT", "updates": 0, "first_frame": None,
               "last_frame": None, "min_offset": None, "max_offset": None, "max_changed": 0,
               "scsi_correlation": "NONE_OBSERVED", "scsi_update_frames": []} for name in ("cdrom_ram", "card_ram")]
    return [wram] + absent


def _wram(rows):
    return next(row for row in rows if row["region"] == "wram")


class DualRunCorrelationTests(unittest.TestCase):
    FRAMES = 4

    def _states(self, changed_frames, pc=0x100):
        return _index([_state_row(f, wram_changed=1 if f in changed_frames else 0, pc=pc)
                       for f in range(1, self.FRAMES + 1)])

    def test_parse_requires_matching_mode_and_every_frame(self):
        states = list(self._states({1, 3}).values())
        scsi = [_scsi_row(f) for f in range(1, self.FRAMES + 1)]
        tracer.parse_frame_records(_stdout("scsi", states, scsi), "scsi", self.FRAMES)
        with self.assertRaises(ValueError):
            tracer.parse_frame_records(_stdout("scsi", states, scsi), "full", self.FRAMES)
        with self.assertRaises(ValueError):
            tracer.parse_frame_records(_stdout("scsi", states[:-1], scsi), "scsi", self.FRAMES)

    def test_compare_detects_state_and_scsi_count_mismatch(self):
        scsi = _index([_scsi_row(f) for f in range(1, self.FRAMES + 1)])
        ok, _ = tracer.compare_runs(self._states({1}), scsi, self._states({1}), scsi, self.FRAMES)
        self.assertTrue(ok)
        ok, reason = tracer.compare_runs(self._states({1}), scsi, self._states({1}, pc=0x200), scsi, self.FRAMES)
        self.assertFalse(ok)
        self.assertIn("frame 1", reason)
        scsi_b = dict(scsi)
        scsi_b[1] = _scsi_row(1, events=2)
        ok, _ = tracer.compare_runs(self._states({1}), scsi, self._states({1}), scsi_b, self.FRAMES)
        self.assertFalse(ok)

    def test_control_report_names_first_mismatch_and_blocks_on_failed_runs(self):
        scsi = [_scsi_row(f) for f in range(1, self.FRAMES + 1)]
        runs = {
            "A1": {"status": None, "exit_code": 0, "elapsed_s": 1.0, "stdout": _stdout("full", self._states({1}), scsi)},
            "A2": {"status": None, "exit_code": 0, "elapsed_s": 1.0, "stdout": _stdout("full", self._states({1}), scsi)},
            "B1": {"status": None, "exit_code": 0, "elapsed_s": 1.0, "stdout": _stdout("scsi", self._states({1}), scsi)},
            "B2": {"status": None, "exit_code": 0, "elapsed_s": 1.0,
                   "stdout": _stdout("scsi", self._states({1}, pc=0x200), scsi)},
        }
        report = tracer.control_experiment_report(runs, self.FRAMES)
        self.assertEqual(report["status"], "DIAGNOSTIC")
        self.assertEqual(report["pairs"]["A1_vs_A2"], "MATCH")
        self.assertEqual(report["pairs"]["B1_vs_B2"]["frame"], 1)
        self.assertIn("field pc run A=256 run B=512", report["pairs"]["B1_vs_B2"]["difference"])
        runs["B2"]["exit_code"] = 1
        self.assertEqual(tracer.control_experiment_report(runs, self.FRAMES)["status"], "BLOCKED")

    def test_state_mismatch_reports_first_differing_field_and_both_values(self):
        scsi = [_scsi_row(f) for f in range(1, self.FRAMES + 1)]
        ok, reason = tracer.compare_runs(self._states({1}), _index(scsi), self._states({1}, pc=0x200),
                                         _index(scsi), self.FRAMES)
        self.assertFalse(ok)
        self.assertIn("frame 1", reason)
        self.assertIn("field pc run A=256 run B=512", reason)
        mpr_b = _state_row(1)
        mpr_b["mpr"][2] = 0x11
        ok, reason = tracer.compare_runs({1: _state_row(1)}, {1: _scsi_row(1)}, {1: mpr_b}, {1: _scsi_row(1)}, 1)
        self.assertFalse(ok)
        self.assertIn("field mpr[2] run A=250 run B=17", reason)
        digest_b = _state_row(1)
        digest_b["ram_hash"] = "f" * 16
        ok, reason = tracer.compare_runs({1: _state_row(1)}, {1: _scsi_row(1)}, {1: digest_b}, {1: _scsi_row(1)}, 1)
        self.assertFalse(ok)
        self.assertIn("field ram_hash run A=0000000000000000 run B=ffffffffffffffff", reason)

    def test_complete_scsi_on_changed_frame_is_observed_and_unchanged_frames_are_ignored(self):
        scsi_b = _index([_scsi_row(1, events=1), _scsi_row(2), _scsi_row(3), _scsi_row(4)])
        rows = _ram_rows(2, 1, 3)
        tracer.apply_scsi_correlation(rows, self._states({1, 3}), scsi_b, self.FRAMES)
        self.assertEqual(_wram(rows)["scsi_correlation"], "OBSERVED")
        self.assertEqual(_wram(rows)["scsi_update_frames"], [1])

    def test_incomplete_scsi_on_a_changed_frame_is_unknown_not_observed(self):
        scsi_b = _index([_scsi_row(1, events=1), _scsi_row(2), _scsi_row(3, incomplete=True), _scsi_row(4)])
        rows = _ram_rows(2, 1, 3)
        tracer.apply_scsi_correlation(rows, self._states({1, 3}), scsi_b, self.FRAMES)
        self.assertEqual(_wram(rows)["scsi_correlation"], "UNKNOWN")
        self.assertEqual(_wram(rows)["scsi_update_frames"], [])

    def test_incomplete_scsi_on_an_unchanged_frame_does_not_make_the_region_unknown(self):
        scsi_b = _index([_scsi_row(1, events=1), _scsi_row(2, incomplete=True), _scsi_row(3), _scsi_row(4)])
        rows = _ram_rows(2, 1, 3)
        tracer.apply_scsi_correlation(rows, self._states({1, 3}), scsi_b, self.FRAMES)
        self.assertEqual(_wram(rows)["scsi_correlation"], "OBSERVED")

    def test_no_scsi_notification_is_never_promoted_to_observed(self):
        scsi_b = _index([_scsi_row(f) for f in range(1, self.FRAMES + 1)])
        rows = _ram_rows(2, 1, 3)
        tracer.apply_scsi_correlation(rows, self._states({1, 3}), scsi_b, self.FRAMES)
        self.assertEqual(_wram(rows)["scsi_correlation"], "NONE_OBSERVED")

    def test_evicted_run_a_scsi_count_does_not_block_a_complete_run_b(self):
        # Run A shares its bounded buffer with CPU entries: its zero count is an incomplete
        # observation, not disagreement.  The correlation is still taken from complete Run B.
        scsi_a = [_scsi_row(1, events=0, incomplete=True), _scsi_row(2), _scsi_row(3), _scsi_row(4)]
        scsi_b = [_scsi_row(1, events=1), _scsi_row(2), _scsi_row(3), _scsi_row(4)]
        end = {"ram_deltas": _ram_rows(2, 1, 3)}
        basis = tracer.compare_and_correlate(_stdout("full", self._states({1, 3}), scsi_a),
                                             _stdout("scsi", self._states({1, 3}), scsi_b), end, self.FRAMES)
        self.assertEqual(basis["status"], "PASS")
        self.assertEqual(_wram(end["ram_deltas"])["scsi_correlation"], "OBSERVED")
        self.assertEqual(_wram(end["ram_deltas"])["scsi_update_frames"], [1])

    def test_state_mismatch_marks_every_present_region_unknown(self):
        scsi = [_scsi_row(f, events=1) for f in range(1, self.FRAMES + 1)]
        end = {"ram_deltas": _ram_rows(2, 1, 3)}
        basis = tracer.compare_and_correlate(_stdout("full", self._states({1, 3}), scsi),
                                             _stdout("scsi", self._states({1, 3}, pc=0x200), scsi), end, self.FRAMES)
        self.assertEqual(basis["status"], "UNKNOWN")
        self.assertEqual(_wram(end["ram_deltas"])["scsi_correlation"], "UNKNOWN")

    def test_matching_runs_end_to_end_report_observed_with_run_b_frames(self):
        scsi_a = [_scsi_row(1, events=1), _scsi_row(2), _scsi_row(3), _scsi_row(4)]
        scsi_b = [_scsi_row(1, events=1), _scsi_row(2), _scsi_row(3), _scsi_row(4)]
        end = {"ram_deltas": _ram_rows(2, 1, 3)}
        basis = tracer.compare_and_correlate(_stdout("full", self._states({1, 3}), scsi_a),
                                             _stdout("scsi", self._states({1, 3}), scsi_b), end, self.FRAMES)
        self.assertEqual(basis["status"], "PASS")
        self.assertEqual(_wram(end["ram_deltas"])["scsi_correlation"], "OBSERVED")

    def test_cumulative_frames_keep_the_full_ram_accounting(self):
        frames = 300
        states = [_state_row(f, wram_changed=1) for f in range(1, frames + 1)]
        scsi = [_scsi_row(f, events=1 if f % 10 == 0 else 0) for f in range(1, frames + 1)]
        rows = _ram_rows(frames, 1, frames)
        tracer.apply_scsi_correlation(rows, {f: states[f - 1] for f in range(1, frames + 1)},
                                      {f: scsi[f - 1] for f in range(1, frames + 1)}, frames)
        self.assertEqual(_wram(rows)["scsi_correlation"], "OBSERVED")
        self.assertEqual(_wram(rows)["scsi_update_frames"], list(range(10, frames + 1, 10)))
        self.assertEqual(_wram(rows)["updates"], frames)


@unittest.skipUnless(hasattr(os, "getuid"), "POSIX ownership and permission modes are required")
class PrivateTempRootTests(unittest.TestCase):
    def test_missing_root_is_created_private_and_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "opencode"
            with mock.patch.object(tracer, "OPENCODE_TEMP_ROOT", root):
                self.assertEqual(tracer.ensure_private_root(), root)
                status = os.lstat(root)
                self.assertTrue(stat.S_ISDIR(status.st_mode))
                self.assertEqual(stat.S_IMODE(status.st_mode) & 0o077, 0)
                self.assertEqual(tracer.ensure_private_root(), root)

    def test_symlink_root_is_refused_without_following_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target"
            target.mkdir(mode=0o700)
            root = Path(tmp) / "opencode"
            root.symlink_to(target, target_is_directory=True)
            with mock.patch.object(tracer, "OPENCODE_TEMP_ROOT", root):
                with self.assertRaises(RuntimeError):
                    tracer.ensure_private_root()

    def test_group_or_other_accessible_root_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "opencode"
            root.mkdir(mode=0o700)
            os.chmod(root, 0o755)
            with mock.patch.object(tracer, "OPENCODE_TEMP_ROOT", root):
                with self.assertRaises(RuntimeError):
                    tracer.ensure_private_root()

    def test_regular_file_at_root_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "opencode"
            root.write_text("not a directory", encoding="utf-8")
            with mock.patch.object(tracer, "OPENCODE_TEMP_ROOT", root):
                with self.assertRaises(RuntimeError):
                    tracer.ensure_private_root()

    def test_job_is_created_without_a_preexisting_root_and_cleanup_is_identity_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "opencode"
            parent = root / "frame-tracer-jobs"
            with mock.patch.object(tracer, "OPENCODE_TEMP_ROOT", root), \
                    mock.patch.object(tracer, "JOB_PARENT", parent):
                path, identity = tracer.make_tracked_job_dir()
                self.assertTrue(path.is_dir())
                self.assertEqual(path.parent, parent)
                ok, reason = tracer.cleanup_tracked_job_dir(path, identity)
                self.assertTrue(ok, reason)
                self.assertFalse(path.exists())

    def test_cd_diagnostic_no_longer_hardcodes_the_shared_root(self):
        source = (Path(__file__).resolve().parents[2] / "tests/integration/geargrafx_frame_tracer_cd.py").read_text(
            encoding="utf-8")
        self.assertNotIn('dir="/tmp/opencode"', source)
        self.assertIn("tracer.ensure_private_root()", source)


if __name__ == "__main__":
    unittest.main()
