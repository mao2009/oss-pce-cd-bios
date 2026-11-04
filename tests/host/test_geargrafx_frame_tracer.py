"""Synthetic protocol tests for the normal-frame tracer; no Geargrafx core runs here."""
import json
import os
import re
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

MISSING = object()  # sentinel: drop the END field entirely


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


def core_trace_for(end, frames=2):
    """A core_trace object consistent with the END fields, as the C++ accounting would emit it.

    One frame carries the per-frame maximum (and every lost entry); the other frames log at most
    30 entries, never more than the capacity.
    """
    capacity, retained_max, lost = end.get("core_capacity"), end.get("max_frame_core_entries"), \
        end.get("trace_events_dropped")
    if not all(type(value) is int for value in (capacity, retained_max, lost)):
        capacity, retained_max, lost = 100000, 40, 0  # negative tests: other checks reject first
    maximum = capacity + lost if lost else retained_max
    other = min(30, maximum, capacity)
    logged = maximum + other * (frames - 1)
    return {"total_logged": logged, "sequence_delta": logged, "logged_accounted": logged, "consumed": logged - lost,
            "lost": lost, "frames_accounted": frames, "discontinuity_frames": 0, "gap_frames": 1 if lost else 0,
            "max_frame_logged": maximum}


def output(events, dropped=0, trace_events_dropped=0, capacity=4, run=None, legacy=False, core_trace=None,
           cap_fields=None, **end_fields):
    cap = {"normal_frame_execution": True, "instruction_step": False, "guest_mutation": False, "scsi_event_notification": True,
            "ram_observation": True, "ram_regions": [], "cpu_event_notification": True,
           "mmio_reads": False, "ring_capacity": capacity, "frames": 2, "disc_given": False,
           "ram_delta_summary": True,
           "run_input_requested": bool(run), "run_start_frame": 1 if run else 0, "run_hold_frames": 2 if run else 0,
           "core_capacity_requested": 0, "trace_api_guest_ram_checked": True}
    end = {"frames": 2, "events": len(events), "dropped": dropped, "trace_events_dropped": trace_events_dropped,
           "overflow": bool(dropped or trace_events_dropped), "ram_deltas": ram_deltas(),
           "pushed": len(events) + dropped, "core_capacity": 100000, "max_frame_core_entries": 40,
           "max_frame_scsi_events": 3, "trace_api_guest_checks": 3, "trace_api_guest_changes": 0}
    for key, value in end_fields.items():
        if value is MISSING:
            end.pop(key, None)
        else:
            end[key] = value
    for key, value in (cap_fields or {}).items():
        if value is MISSING:
            cap.pop(key, None)
        else:
            cap[key] = value
    if core_trace is not MISSING:
        end["core_trace"] = core_trace if core_trace is not None else core_trace_for(end)
    if legacy:  # pre-PR-B tracer output: none of the PR-B fields exist
        for key in ("core_trace", "trace_api_guest_checks", "trace_api_guest_changes"):
            end.pop(key, None)
        for key in ("core_capacity_requested", "trace_api_guest_ram_checked"):
            cap.pop(key, None)
    inputs = [] if not run else ["INPUT " + json.dumps({"frame": 1, "action": "press", "run_pressed": True}),
                                 "INPUT " + json.dumps({"frame": 2, "action": "release", "run_pressed": False})]
    return "\n".join(["CAP " + json.dumps(cap), *inputs, *["EVENT " + json.dumps(row) for row in events],
                       "END " + json.dumps(end)])


def table_of(text):
    cap, _, end = tracer.parse_records(text)
    return tracer.measurements(cap, end)


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
        _, _, end = tracer.parse_records(output([event("region")], trace_events_dropped=3, core_capacity=40))
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
        text = output([event()], cap_fields={"ram_observation": False, "trace_api_guest_ram_checked": False})
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
                         "geargrafx_frame_tracer_ram_summary_test", "geargrafx_frame_tracer_core_account_test"):
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


class CoreTraceCapacityTests(unittest.TestCase):
    def test_core_trace_uses_core_capacity_not_the_output_ring_size(self):
        source = (Path(__file__).resolve().parents[2] / "tests/integration/geargrafx_frame_tracer.cpp").read_text(
            encoding="utf-8")
        self.assertNotIn("SetCapacity(kRingSize)", source)
        self.assertIn("trace->GetCapacity()", source)
        self.assertIn("total > core_capacity", source)


class MeasurementTableTests(unittest.TestCase):
    """PR-A: core_capacity and the output-ring dropped count.  PR-B rows are covered by CoreTraceAccountingTests."""

    def test_valid_run_classifies_each_quantity_without_estimates(self):
        cap, _, end = tracer.parse_records(output([event("sample", 2), event("handoff", 2)], dropped=7, capacity=2))
        table = tracer.measurements(cap, end)
        self.assertEqual(set(table), set(tracer.MEASUREMENT_NAMES))
        self.assertEqual(set(table), {"core_capacity", "output_ring_dropped", "max_frame_core_entries",
                                      "max_frame_scsi_events", "core_trace_dropped_frames"})
        self.assertEqual(table["core_capacity"], {
            "status": tracer.REPORTED_BY_CORE, "value": 100000, "requested": 0, "kind": "configuration",
            "source": "pinned core TraceLogger::GetCapacity()"})
        ring = table["output_ring_dropped"]
        self.assertEqual((ring["status"], ring["value"], ring["pushed"], ring["retained"], ring["ring_capacity"]),
                         (tracer.MEASURED, 7, 9, 2, 2))
        self.assertEqual(table["max_frame_scsi_events"]["status"], tracer.NOT_MEASURED)
        self.assertEqual(set(table["max_frame_scsi_events"]), {"status", "reason"})  # raw END value never promoted

    def test_configuration_is_never_labelled_measured(self):
        cap, _, end = tracer.parse_records(output([event()]))
        table = tracer.measurements(cap, end)
        self.assertNotEqual(table["core_capacity"]["status"], tracer.MEASURED)
        self.assertEqual(table["output_ring_dropped"]["value"], 0)

    def test_refuses_inconsistent_or_missing_ring_and_core_fields(self):
        one = [event()]
        cases = {
            "pushed missing": output(one, pushed=MISSING),
            "pushed too small": output(one, pushed=0),
            "pushed too large": output(one, pushed=2),
            "pushed bool": output([], pushed=False),
            "pushed string": output(one, pushed="1"),
            "dropped while not full": output(one, dropped=3, capacity=4),
            "core_capacity missing": output(one, core_capacity=MISSING),
            "core_capacity zero": output(one, core_capacity=0),
            # Isolates the capacity check: with zero maxima no other bound rejects a zero capacity.
            "core_capacity zero, zero maxima": output(one, core_capacity=0, max_frame_core_entries=0,
                                                      max_frame_scsi_events=0),
            "core_capacity bool": output(one, core_capacity=True),
            "core_capacity float": output(one, core_capacity=100000.0),
            "max entries missing": output(one, max_frame_core_entries=MISSING),
            "max entries negative": output(one, max_frame_core_entries=-1),
            "max entries above capacity": output(one, core_capacity=10, max_frame_core_entries=11),
            "max scsi missing": output(one, max_frame_scsi_events=MISSING),
            "max scsi above entries": output(one, max_frame_core_entries=2, max_frame_scsi_events=3),
            "max scsi bool": output(one, max_frame_scsi_events=True),
        }
        for name, text in cases.items():
            with self.subTest(name):
                with self.assertRaises(ValueError):
                    tracer.parse_records(text)

    def test_tracer_source_emits_the_independent_push_counter(self):
        source = (Path(__file__).resolve().parents[2] / "tests/integration/geargrafx_frame_tracer.cpp").read_text(
            encoding="utf-8")
        ring = (Path(__file__).resolve().parents[2] / "tests/integration/geargrafx_frame_tracer_ring.h").read_text(
            encoding="utf-8")
        self.assertIn('",\\"pushed\\":%llu", ring.pushed()', source)
        self.assertIn("++pushed_;", ring)
        # The counter is incremented before the retain/drop branch, so it counts every push.
        self.assertLess(ring.index("++pushed_;"), ring.index("if (size_ < capacity_)"))


class CoreTraceAccountingTests(unittest.TestCase):
    """A1 PR-B: pre-clamp per-frame core counts and core-trace gap frames, from END core_trace."""

    BASE = core_trace_for({"core_capacity": 100000, "max_frame_core_entries": 40, "trace_events_dropped": 0})
    # Gap run: capacity 40, one frame logged 43 (3 lost), the other 30.
    GAP = core_trace_for({"core_capacity": 40, "max_frame_core_entries": 40, "trace_events_dropped": 3})

    @staticmethod
    def changed(base, **fields):
        row = dict(base)
        for key, value in fields.items():
            if value is MISSING:
                row.pop(key)
            else:
                row[key] = value
        return row

    def gap_output(self, core_trace=None, **fields):
        fields.setdefault("core_capacity", 40)
        fields.setdefault("trace_events_dropped", 3)
        return output([event()], core_trace=core_trace or self.GAP, **fields)

    def test_valid_run_measures_pre_clamp_maximum_and_gap_frames(self):
        table = table_of(output([event()]))
        entries, drops = table["max_frame_core_entries"], table["core_trace_dropped_frames"]
        self.assertEqual((entries["status"], entries["value"], entries["retained_max"], entries["frames"]),
                         (tracer.MEASURED, 40, 40, 2))
        self.assertEqual((drops["status"], drops["value"], drops["lost_entries"], drops["frames"]),
                         (tracer.MEASURED, 0, 0, 2))
        self.assertEqual(table["max_frame_scsi_events"]["status"], tracer.NOT_MEASURED)
        self.assertEqual(table["core_capacity"]["status"], tracer.REPORTED_BY_CORE)

    def test_gap_frame_maximum_exceeds_capacity_and_is_not_the_clamped_value(self):
        table = table_of(self.gap_output())
        entries, drops = table["max_frame_core_entries"], table["core_trace_dropped_frames"]
        self.assertEqual((entries["value"], entries["retained_max"]), (43, 40))  # pre-clamp, above capacity 40
        self.assertEqual((drops["value"], drops["lost_entries"]), (1, 3))
        self.assertEqual(table["max_frame_scsi_events"]["status"], tracer.NOT_MEASURED)  # still PR-C

    def test_requested_capacity_is_configuration_not_measurement(self):
        table = table_of(self.gap_output(cap_fields={"core_capacity_requested": 40}))
        self.assertEqual(table["core_capacity"]["status"], tracer.REPORTED_BY_CORE)
        self.assertEqual((table["core_capacity"]["value"], table["core_capacity"]["requested"]), (40, 40))

    def test_legacy_output_is_accepted_but_never_measured(self):
        table = table_of(output([event()], legacy=True))
        for name, reason in tracer.LEGACY_NOT_MEASURED_REASONS.items():
            self.assertEqual(table[name], {"status": tracer.NOT_MEASURED, "reason": reason})
        self.assertNotIn("requested", table["core_capacity"])
        self.assertEqual(table["output_ring_dropped"]["status"], tracer.MEASURED)

    def test_discontinuous_counters_keep_quantities_not_measured(self):
        broken = self.changed(self.BASE, frames_accounted=1, discontinuity_frames=1, logged_accounted=40,
                              consumed=40, total_logged=5, sequence_delta=999)
        table = table_of(output([event()], core_trace=broken, overflow=True))
        # An unknown loss is never presented as no loss: without overflow the output is rejected.
        with self.assertRaisesRegex(ValueError, "invalid overflow accounting"):
            tracer.parse_records(output([event()], core_trace=broken, overflow=False))
        for name in tracer.CORE_TRACE_MEASURED:
            self.assertEqual(table[name]["status"], tracer.NOT_MEASURED, name)
            self.assertNotIn("value", table[name])
            self.assertIn("discontinuous in 1 of 2", table[name]["reason"])

    def test_no_not_measured_row_carries_a_value(self):
        for text in (output([event()]), self.gap_output(), output([event()], legacy=True)):
            for name, row in table_of(text).items():
                if row["status"] == tracer.NOT_MEASURED:
                    self.assertEqual(set(row), {"status", "reason"}, name)

    def test_refuses_inconsistent_core_accounting(self):
        base, gap = self.BASE, self.GAP
        cases = {
            "frames not covered": output([event()], core_trace=self.changed(base, frames_accounted=3)),
            "more gap frames than accounted": self.gap_output(self.changed(gap, gap_frames=3)),
            "lost differs from trace_events_dropped": self.gap_output(self.changed(gap, lost=2, consumed=71)),
            "consumed + lost != logged": output([event()], core_trace=self.changed(base, consumed=69)),
            "lost without gap frame": output([event()], trace_events_dropped=3,
                                             core_trace=self.changed(base, lost=3, consumed=67)),
            "gap frame without loss": output([event()], core_trace=self.changed(base, gap_frames=1)),
            "maximum above logged total": output([event()], max_frame_core_entries=71,
                                                 core_trace=self.changed(base, max_frame_logged=71)),
            "logged above frames x maximum": output([event()], core_trace=self.changed(
                base, logged_accounted=81, consumed=81, total_logged=81, sequence_delta=81)),
            "loss larger than possible": self.gap_output(self.changed(gap, lost=4, consumed=69),
                                                         trace_events_dropped=4),
            "gap with maximum not above capacity": self.gap_output(self.changed(gap, max_frame_logged=40)),
            "above capacity without gap": output([event()], core_capacity=40, core_trace=self.changed(
                base, max_frame_logged=41, logged_accounted=71, consumed=71, total_logged=71, sequence_delta=71)),
            "retained maximum is not the clamp": output([event()], max_frame_core_entries=39, core_trace=base),
            "total_logged differs": output([event()], core_trace=self.changed(base, total_logged=71)),
            "sequence_delta differs": output([event()], core_trace=self.changed(base, sequence_delta=69)),
            "not an object": output([event()], core_trace=[]),
            "extra key": output([event()], core_trace=self.changed(base, extra=0)),
            "missing key": output([event()], core_trace=self.changed(base, lost=MISSING)),
            "bool value": output([event()], core_trace=self.changed(base, gap_frames=False)),
            "negative value": output([event()], core_trace=self.changed(base, discontinuity_frames=-1,
                                                                        frames_accounted=3)),
            "END frames differ": output([event()], frames=3),
            "guest checks short": output([event()], trace_api_guest_checks=2),
            "guest checks bool": output([event()], trace_api_guest_checks=True),
            "guest state changed": output([event()], trace_api_guest_changes=1),
            "guest changes bool": output([event()], trace_api_guest_changes=False),
            "requested negative": output([event()], cap_fields={"core_capacity_requested": -1}),
            "requested above core default": output([event()], cap_fields={"core_capacity_requested": 100001},
                                                   core_capacity=100001),
            "requested bool": output([event()], cap_fields={"core_capacity_requested": False}),
            "requested not applied": output([event()], cap_fields={"core_capacity_requested": 50}),
            "RAM check mismatch": output([event()], cap_fields={"trace_api_guest_ram_checked": False}),
            "RAM check not bool": output([event()], cap_fields={"trace_api_guest_ram_checked": 1}),
        }
        for key in ("core_trace", "trace_api_guest_checks", "trace_api_guest_changes"):
            cases[f"partial: END {key} missing"] = output([event()], core_trace=MISSING if key == "core_trace"
                                                          else None, **({} if key == "core_trace" else {key: MISSING}))
        for key in ("core_capacity_requested", "trace_api_guest_ram_checked"):
            cases[f"partial: CAP {key} missing"] = output([event()], cap_fields={key: MISSING})
        for name, text in cases.items():
            with self.subTest(name):
                with self.assertRaises(ValueError):
                    tracer.parse_records(text)

    def test_trace_refuses_out_of_range_core_capacity_before_running(self):
        for value in (0, -1, 100001, True, "40", 40.0):
            args = SimpleNamespace(frames=1, events=1, timeout=1, rom=Path("ignored"), cue=None,
                                   run_start_frame=None, run_hold_frames=None, observe_ram=True,
                                   core_capacity=value)
            with self.subTest(value=value), mock.patch.object(tracer, "doctor", side_effect=AssertionError):
                log = tracer.trace(args)
                self.assertEqual((log["status"], log["evidence"]), ("FAIL", "NOT_RUN"))

    def run_trace(self, core_capacity, stdout):
        args = SimpleNamespace(frames=2, events=4, timeout=1, rom=Path("ignored"), cue=None,
                               run_start_frame=None, run_hold_frames=None, observe_ram=True)
        if core_capacity is not MISSING:
            args.core_capacity = core_capacity
        job = {"status": None, "exit_code": 0, "stdout": stdout, "elapsed_s": 0.1}
        with mock.patch.object(tracer, "doctor", return_value={"status": "PASS"}), \
                mock.patch.object(tracer, "make_tracked_job_dir", return_value=(Path("/safe/job"), (1, 2, 3))), \
                mock.patch.object(tracer, "compile_probe", return_value=Path("probe")), \
                mock.patch.object(tracer.shutil, "copyfile"), \
                mock.patch.object(tracer, "run_job", return_value=job) as run_job, \
                mock.patch.object(tracer, "cleanup_tracked_job_dir", return_value=(True, "removed")):
            log = tracer.trace(args)
        return log, run_job.call_args.args[0]

    def test_trace_passes_the_requested_capacity_and_checks_it_was_applied(self):
        log, argv = self.run_trace(40, self.gap_output(cap_fields={"core_capacity_requested": 40}))
        self.assertEqual((log["status"], argv[-1]), ("PASS", "40"))
        self.assertEqual(log["measurements"]["max_frame_core_entries"]["value"], 43)
        for missing in (None, MISSING):  # PR-A callers (no attribute) keep the core default
            log, argv = self.run_trace(missing, output([event()]))
            self.assertEqual((log["status"], argv[-1]), ("PASS", "0"))
        log, _ = self.run_trace(40, output([event()]))  # tracer reported requested=0
        self.assertEqual((log["status"], log["evidence"]), ("FAIL", "NOT_RUN"))
        self.assertIn("requested core capacity", log["reason"])

    def test_tracer_source_accounts_core_counters_and_checks_guest_state(self):
        source = (Path(__file__).resolve().parents[2] / "tests/integration/geargrafx_frame_tracer.cpp").read_text(
            encoding="utf-8")
        for needle in ("trace->GetSequence()", "trace->GetCount()", "core_account.account(",
                       "FrameTracerCoreAccount::valid_start(core_start)", "if (!core_frame.continuous)",
                       "++consumed_trace;", "trace->SetCapacity((u32)requested_core_capacity)",
                       "(ring.dropped() || dropped_trace || core_account.discontinuous_frames())"):
            self.assertIn(needle, source)
        # Both guest-state comparisons are real (not short-circuited) and each is counted.
        for label in ("before_setup", "before_trace"):
            check = (r"\+\+guest_checks;\n\s*if \(!same_guest\(" + label +
                     r", guest_fingerprint\(core, cpu, memory, scsi, cdrom, rams\)\)\) \+\+guest_changes;")
            self.assertEqual(len(re.findall(check, source)), 1, label)
        # SetCapacity is only reached when a capacity is requested, before frame 1.
        self.assertIn("if (requested_core_capacity && !trace->SetCapacity(", source)
        self.assertLess(source.index("SetCapacity("), source.index("for (unsigned number = 1;"))


class MeasurementEvidenceTests(unittest.TestCase):
    """CD wrapper: the counts-only table is checked before anything is written or printed."""

    @staticmethod
    def run_table(dropped=5, retained=1, capacity=100000):
        cap, _, end = tracer.parse_records(output([event()] * retained, dropped=dropped, capacity=retained,
                                                  core_capacity=capacity))
        return {"status": "PASS", "measurements": tracer.measurements(cap, end)}

    def evidence(self, on, off):
        import geargrafx_frame_tracer_cd as cd
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(cd, "ROOT", Path(tmp)):
            result = cd.measurement_evidence("debug", on, off)
            written = json.loads((Path(tmp) / "build/debug/frame-tracer-cd/frame-tracer-measurements.json").read_text())
        return result, written

    def test_writes_counts_only_table_for_both_runs(self):
        result, written = self.evidence(self.run_table(), self.run_table(dropped=0))
        self.assertEqual(result, written)
        self.assertEqual(written["core_capacity"]["value"], 100000)
        self.assertEqual(written["runs"]["observer_on"]["output_ring_dropped"]["value"], 5)
        self.assertEqual(written["runs"]["observer_off"]["output_ring_dropped"]["value"], 0)
        self.assertEqual(written["guest_bytes"], "NONE")
        for table in written["runs"].values():  # the five quantities only: no events, traces or guest data
            self.assertEqual(set(table), set(tracer.MEASUREMENT_NAMES))
            self.assertEqual(table["max_frame_core_entries"]["status"], tracer.MEASURED)
            self.assertEqual(table["core_trace_dropped_frames"]["status"], tracer.MEASURED)
            self.assertEqual(table["max_frame_scsi_events"]["status"], tracer.NOT_MEASURED)

    def test_refuses_promoted_missing_or_inconsistent_tables(self):
        promoted = self.run_table()
        promoted["measurements"]["max_frame_scsi_events"] = {"status": tracer.MEASURED, "value": 3}
        relabelled = self.run_table()
        relabelled["measurements"]["core_capacity"]["status"] = tracer.MEASURED
        bad_push = self.run_table()
        bad_push["measurements"]["output_ring_dropped"]["pushed"] += 1
        missing = self.run_table()
        del missing["measurements"]["core_trace_dropped_frames"]
        legacy_on = {"status": "PASS", "measurements": table_of(output([event()], legacy=True))}
        bad_drops = self.run_table()
        bad_drops["measurements"]["core_trace_dropped_frames"]["value"] = 3  # three gap frames, zero lost entries
        too_many_drops = self.run_table()
        too_many_drops["measurements"]["core_trace_dropped_frames"].update(value=5, lost_entries=9)  # 2 frames
        cases = {
            "legacy (pre-PR-B) table on the real core": (legacy_on, self.run_table()),
            "dropped frames without lost entries": (bad_drops, self.run_table()),
            "more dropped frames than frames": (too_many_drops, self.run_table()),
            "NOT_MEASURED promoted": (promoted, self.run_table()),
            "configuration relabelled": (self.run_table(), relabelled),
            "push mismatch": (bad_push, self.run_table()),
            "quantity missing": (missing, self.run_table()),
            "no table": ({"status": "PASS"}, self.run_table()),
            "capacity differs": (self.run_table(capacity=100000), self.run_table(capacity=50000)),
        }
        for name, (on, off) in cases.items():
            with self.subTest(name):
                with self.assertRaises(ValueError):
                    self.evidence(on, off)


if __name__ == "__main__":
    unittest.main()
