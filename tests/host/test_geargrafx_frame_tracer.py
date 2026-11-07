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
    if kind == "region":  # the run's first REGION event: a baseline from "unknown"
        row = {"kind": "region", "frame": frame, "event_cycle": 1, "event_pc": 8192,
               "event_bank": 248, "from_region": "unknown", "to_region": "working_ram", "trace_gap": False,
               "region_kind": "baseline"}
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
           cap_fields=None, pre_c=False, resets=(), **end_fields):
    """Synthetic tracer output: current (PR-C) format by default.

    legacy=True gives pre-PR-B output (no PR-B or PR-C fields); pre_c=True gives PR-B output (no
    PR-C fields).  resets are RESET records.
    """
    cap = {"normal_frame_execution": True, "instruction_step": False, "guest_mutation": False, "scsi_event_notification": True,
            "ram_observation": True, "ram_regions": [], "cpu_event_notification": True,
           "mmio_reads": False, "ring_capacity": capacity, "frames": 2, "disc_given": False,
           "ram_delta_summary": True,
           "run_input_requested": bool(run), "run_start_frame": 1 if run else 0, "run_hold_frames": 2 if run else 0,
           "core_capacity_requested": 0, "trace_api_guest_ram_checked": True,
           "region_gap_carry": True, "warm_reset_frame": 0}
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
    if legacy or pre_c:  # pre-PR-C tracer output: no REGION classification or warm reset
        for key in ("region_gap_carry", "warm_reset_frame"):
            cap.pop(key, None)
        events = [{key: value for key, value in row.items() if key != "region_kind"} for row in events]
    inputs = [] if not run else ["INPUT " + json.dumps({"frame": 1, "action": "press", "run_pressed": True}),
                                 "INPUT " + json.dumps({"frame": 2, "action": "release", "run_pressed": False})]
    return "\n".join(["CAP " + json.dumps(cap), *inputs, *["RESET " + json.dumps(row) for row in resets],
                      *["EVENT " + json.dumps(row) for row in events], "END " + json.dumps(end)])


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

    def test_python_and_cpp_capacity_bounds_mean_the_same_pinned_limit(self):
        """MAX_CORE_CAPACITY is the pinned core's TRACE_BUFFER_SIZE, and both sides reject anything above it.

        The C++ bound is TRACE_BUFFER_SIZE itself, so Python can only drift by being stricter or looser
        than the header; the pinned header is compared directly when the pinned source is present, and
        the real-core test runs at exactly MAX_CORE_CAPACITY.
        """
        source = (Path(__file__).resolve().parents[2] / "tests/integration/geargrafx_frame_tracer.cpp").read_text(
            encoding="utf-8")
        self.assertIn("constexpr long kMaxCoreCapacity = TRACE_BUFFER_SIZE;", source)
        self.assertIn("requested_core_capacity < 0 || requested_core_capacity > kMaxCoreCapacity", source)
        self.assertEqual(tracer.MAX_CORE_CAPACITY, 100000)
        args = dict(frames=1, events=1, timeout=1, rom=Path("ignored"), cue=None, run_start_frame=None,
                    run_hold_frames=None, observe_ram=True)
        with mock.patch.object(tracer, "doctor", return_value={"status": "BLOCKED"}):
            at_bound = tracer.trace(SimpleNamespace(core_capacity=tracer.MAX_CORE_CAPACITY, **args))
        self.assertEqual(at_bound["reason"], "doctor prerequisites")  # accepted: reached the core prerequisites
        above = tracer.trace(SimpleNamespace(core_capacity=tracer.MAX_CORE_CAPACITY + 1, **args))
        self.assertEqual((above["status"], above["evidence"]), ("FAIL", "NOT_RUN"))

    def test_pinned_trace_buffer_size_matches_when_the_pinned_source_is_present(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
        from environment import dependency_dir
        header = dependency_dir("geargrafx").with_name(dependency_dir("geargrafx").name + "-debugger") / \
            "src/trace_logger.h"
        if not header.is_file():
            self.skipTest("pinned Geargrafx source not set up (make setup); covered by the real-core bound test")
        match = re.search(r"^#define TRACE_BUFFER_SIZE (\d+)$", header.read_text(encoding="utf-8"), re.MULTILINE)
        self.assertIsNotNone(match)
        self.assertEqual(int(match.group(1)), tracer.MAX_CORE_CAPACITY)


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
        # No core-trace gap or discontinuity: every logged entry was read, so the SCSI maximum is exact.
        self.assertEqual((table["max_frame_scsi_events"]["status"], table["max_frame_scsi_events"]["value"]),
                         (tracer.MEASURED, 3))
        self.assertNotIn(tracer.SCSI_LOWER_BOUND, table)

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
        MESSAGES = {
            "pushed missing": "push accounting mismatch", "pushed too small": "push accounting mismatch",
            "pushed too large": "push accounting mismatch", "pushed bool": "push accounting mismatch",
            "pushed string": "push accounting mismatch", "dropped while not full": "dropped events while not full",
            "core_capacity missing": "invalid core trace capacity", "core_capacity zero": "invalid core trace capacity",
            "core_capacity zero, zero maxima": "invalid core trace capacity",
            "core_capacity bool": "invalid core trace capacity", "core_capacity float": "invalid core trace capacity",
            "max entries missing": "invalid per-frame core entry field",
            "max entries negative": "invalid per-frame core entry field",
            "max entries above capacity": "invalid per-frame core entry field",
            "max scsi missing": "invalid per-frame SCSI event field",
            "max scsi above entries": "invalid per-frame SCSI event field",
            "max scsi bool": "invalid per-frame SCSI event field",
        }
        self.assertEqual(set(cases), set(MESSAGES))
        for name, text in cases.items():
            with self.subTest(name):
                with self.assertRaisesRegex(ValueError, MESSAGES[name]):  # the named check, not any check
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
        self.assertEqual(table["max_frame_scsi_events"]["status"], tracer.MEASURED)
        self.assertEqual(table["core_capacity"]["status"], tracer.REPORTED_BY_CORE)

    def test_gap_frame_maximum_exceeds_capacity_and_is_not_the_clamped_value(self):
        table = table_of(self.gap_output())
        entries, drops = table["max_frame_core_entries"], table["core_trace_dropped_frames"]
        self.assertEqual((entries["value"], entries["retained_max"]), (43, 40))  # pre-clamp, above capacity 40
        self.assertEqual((drops["value"], drops["lost_entries"]), (1, 3))
        self.assertEqual(table["max_frame_scsi_events"]["status"], tracer.NOT_MEASURED)

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
        MESSAGES = {
            "frames not covered": "does not cover every frame",
            "more gap frames than accounted": "does not cover every frame",
            "lost differs from trace_events_dropped": "entry accounting mismatch",
            "consumed + lost != logged": "entry accounting mismatch",
            "lost without gap frame": "gap frames disagree with lost entries",
            "gap frame without loss": "gap frames disagree with lost entries",
            "maximum above logged total": "per-frame core maximum is inconsistent",
            "logged above frames x maximum": "per-frame core maximum is inconsistent",
            "loss larger than possible": "loss is impossible",
            "gap with maximum not above capacity": "loss is impossible",
            "above capacity without gap": "more than the core capacity without a gap",
            "retained maximum is not the clamp": "not the clamped pre-clamp maximum",
            "total_logged differs": "core trace counters disagree",
            "sequence_delta differs": "core trace counters disagree",
            "not an object": "invalid core trace accounting fields",
            "extra key": "invalid core trace accounting fields",
            "missing key": "invalid core trace accounting fields",
            "bool value": "invalid core trace accounting fields",
            "negative value": "invalid core trace accounting fields",
            "END frames differ": "invalid frame count",
            "guest checks short": "not checked around every trace API block",
            "guest checks bool": "not checked around every trace API block",
            "guest state changed": "guest state changed across tracer trace API calls",
            "guest changes bool": "not checked around every trace API block",
            "requested negative": "invalid requested core trace capacity",
            "requested above core default": "invalid requested core trace capacity",
            "requested bool": "invalid requested core trace capacity",
            "requested not applied": "differs from the requested capacity",
            "RAM check mismatch": "guest RAM check does not match",
            "RAM check not bool": "guest RAM check does not match",
            "partial: END core_trace missing": "fields incomplete",
            "partial: END trace_api_guest_checks missing": "fields incomplete",
            "partial: END trace_api_guest_changes missing": "fields incomplete",
            "partial: CAP core_capacity_requested missing": "fields incomplete",
            "partial: CAP trace_api_guest_ram_checked missing": "fields incomplete",
        }
        self.assertEqual(set(cases), set(MESSAGES))
        for name, text in cases.items():
            with self.subTest(name):
                with self.assertRaisesRegex(ValueError, MESSAGES[name]):  # the named check, not any check
                    tracer.parse_records(text)

    def test_trace_refuses_out_of_range_core_capacity_before_running(self):
        for value in (0, -1, 100001, True, "40", 40.0):
            args = SimpleNamespace(frames=1, events=1, timeout=1, rom=Path("ignored"), cue=None,
                                   run_start_frame=None, run_hold_frames=None, observe_ram=True,
                                   core_capacity=value)
            with self.subTest(value=value), mock.patch.object(tracer, "doctor", side_effect=AssertionError):
                log = tracer.trace(args)
                self.assertEqual((log["status"], log["evidence"]), ("FAIL", "NOT_RUN"))

    def run_trace(self, core_capacity, stdout, warm_reset_frame=MISSING):
        args = SimpleNamespace(frames=2, events=4, timeout=1, rom=Path("ignored"), cue=None,
                               run_start_frame=None, run_hold_frames=None, observe_ram=True)
        if core_capacity is not MISSING:
            args.core_capacity = core_capacity
        if warm_reset_frame is not MISSING:
            args.warm_reset_frame = warm_reset_frame
        job = {"status": None, "exit_code": 0, "stdout": stdout, "elapsed_s": 0.1}
        with mock.patch.object(tracer, "doctor", return_value={"status": "PASS"}), \
                mock.patch.object(tracer, "make_tracked_job_dir", return_value=(Path("/safe/job"), (1, 2, 3))), \
                mock.patch.object(tracer, "compile_probe", return_value=Path("probe")), \
                mock.patch.object(tracer.shutil, "copyfile"), \
                mock.patch.object(tracer, "run_job", return_value=job) as run_job, \
                mock.patch.object(tracer, "cleanup_tracked_job_dir", return_value=(True, "removed")):
            log = tracer.trace(args)
        return log, run_job.call_args.args[0] if run_job.called else None  # None: refused before running

    def test_trace_passes_the_requested_capacity_and_checks_it_was_applied(self):
        log, argv = self.run_trace(40, self.gap_output(cap_fields={"core_capacity_requested": 40}))
        self.assertEqual((log["status"], argv[-2], argv[-1]), ("PASS", "40", "0"))
        self.assertEqual(log["measurements"]["max_frame_core_entries"]["value"], 43)
        for missing in (None, MISSING):  # PR-A callers (no attribute) keep the core default
            log, argv = self.run_trace(missing, output([event()]))
            self.assertEqual((log["status"], argv[-2], argv[-1]), ("PASS", "0", "0"))
        log, _ = self.run_trace(40, output([event()]))  # tracer reported requested=0
        self.assertEqual((log["status"], log["evidence"]), ("FAIL", "NOT_RUN"))
        self.assertIn("requested core capacity", log["reason"])

    def test_trace_passes_the_warm_reset_and_checks_it_was_applied(self):
        log, argv = self.run_trace(None, output([event()], cap_fields=warm_cap(), resets=[WARM_RESET],
                                                ram_deltas=warm_rows()), 2)
        self.assertEqual((log["status"], argv[-1]), ("PASS", "2"))
        log, _ = self.run_trace(None, output([event()]), 2)  # tracer reported warm_reset_frame=0
        self.assertEqual((log["status"], log["evidence"]), ("FAIL", "NOT_RUN"))
        self.assertIn("requested warm reset", log["reason"])
        for value in (1, 3, 0, True, "2"):  # frames=2: only frame 2 is a valid warm reset point
            log, argv = self.run_trace(None, output([event()]), value)
            self.assertEqual((log["status"], log["evidence"], argv), ("FAIL", "NOT_RUN", None), value)

    def test_log_states_the_guest_check_and_summary_scopes(self):
        log, _ = self.run_trace(None, output([event()]))
        self.assertIn("not an observer non-interference claim", log["guest_state_check_scope"])
        self.assertIn("only 'measurements' carries a status", log["summary_scope"])

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


def region(from_region="unknown", to_region="working_ram", trace_gap=False, kind="baseline", frame=1):
    row = event("region", frame)
    row.update(from_region=from_region, to_region=to_region, trace_gap=trace_gap, region_kind=kind)
    return row


BASELINE = region()
TRANSITION = region("working_ram", "cdrom_ram", False, "transition")
AFTER_GAP = region("unknown", "working_ram", True, "after_gap", 2)


class RegionClassificationTests(unittest.TestCase):
    """A1 PR-C: trace_gap is carried to the next REGION event, so a gap never looks like a baseline."""

    def test_accepts_baseline_transitions_and_disclosed_gaps(self):
        tracer.parse_records(output([BASELINE, TRANSITION]))
        text = output([BASELINE, TRANSITION, AFTER_GAP, TRANSITION], core_capacity=40, trace_events_dropped=3,
                      core_trace=CoreTraceAccountingTests.GAP)
        _, rows, _ = tracer.parse_records(text)
        self.assertEqual([row["region_kind"] for row in rows], ["baseline", "transition", "after_gap", "transition"])

    def test_output_ring_overflow_may_drop_the_baseline(self):
        # Oldest events were dropped: the first retained REGION can be a transition.
        tracer.parse_records(output([TRANSITION], dropped=9, capacity=1))

    def test_refuses_misclassified_or_hidden_gaps(self):
        gap_trace = {"core_capacity": 40, "trace_events_dropped": 3, "core_trace": CoreTraceAccountingTests.GAP}
        cases = {
            # The PR-B defect: a second "unknown" start without trace_gap hides a loss.
            "second baseline hides a gap": (output([BASELINE, TRANSITION, region(frame=2)], **gap_trace),
                                            "baseline after the first REGION event hides a gap"),
            "gap claimed without any core-trace gap": (output([BASELINE, AFTER_GAP]),
                                                      "REGION gap without any core-trace gap"),
            "kind disagrees (transition from unknown)": (output([region(kind="transition")]),
                                                         "classification disagrees"),
            "kind disagrees (baseline with gap)": (output([region(trace_gap=True)], **gap_trace),
                                                   "classification disagrees"),
            "transition carrying trace_gap": (output([BASELINE, region("working_ram", "other", True, "transition")],
                                                     **gap_trace), "classification disagrees"),
            "unknown kind": (output([region(kind="start")]), "invalid REGION classification"),
            "kind missing": (output([{key: value for key, value in BASELINE.items() if key != "region_kind"}]),
                             "invalid REGION classification"),
            "to_region unknown": (output([region(to_region="unknown")]), "invalid REGION classification"),
            "complete ring must start from unknown": (output([TRANSITION]), "does not start from unknown"),
            "region_kind in pre-PR-C output": (output([BASELINE], pre_c=True).replace(
                '"trace_gap": false', '"trace_gap": false, "region_kind": "baseline"'), "PR-C records without"),
        }
        for name, (text, message) in cases.items():
            with self.subTest(name):
                with self.assertRaisesRegex(ValueError, message):
                    tracer.parse_records(text)

    def test_pre_pr_c_output_is_accepted_without_classification(self):
        cap, rows, _ = tracer.parse_records(output([BASELINE, TRANSITION], pre_c=True))
        self.assertNotIn("region_gap_carry", cap)
        self.assertNotIn("region_kind", rows[0])

    def test_tracer_source_carries_the_gap_across_frames(self):
        source = (Path(__file__).resolve().parents[2] / "tests/integration/geargrafx_frame_tracer.cpp").read_text(
            encoding="utf-8")
        loop = source.index("for (unsigned number = 1;")
        # Declared once before the frame loop, never reset at the start of a frame.
        self.assertLess(source.index("    bool trace_gap = false;\n"), loop)
        self.assertEqual(source.count("bool trace_gap = false;"), 1)
        self.assertIn('!from_unknown ? "transition" : event.trace_gap ? "after_gap" : "baseline"', source)


def warm_cap(frame=2):
    """CAP fields of a run that requested a warm reset before `frame`."""
    return {"warm_reset_frame": frame, "guest_mutation": "requested_warm_reset"}


def warm_rows():
    """RAM rows of a valid warm-reset run: the reset frame (2) is not SCSI-attributed."""
    rows = ram_deltas()
    rows[0]["scsi_update_frames"] = [1]
    return rows


WARM_RESET = {"frame": 2, "kind": "warm", "api": "GeargrafxCore::ResetMedia(true)", "performed": True,
              "master_clock_reset": True, "ram_preserved": "backup_ram_only"}


class WarmResetRecordTests(unittest.TestCase):
    RESET = WARM_RESET

    def test_accepts_the_requested_warm_reset(self):
        cap, _, _ = tracer.parse_records(output([event()], cap_fields=warm_cap(), resets=[self.RESET],
                                                ram_deltas=warm_rows()))
        self.assertEqual(cap["guest_mutation"], "requested_warm_reset")  # declared, not hidden as false

    def test_refuses_undeclared_or_misdeclared_guest_mutation(self):
        rows = ram_deltas()
        rows[0]["scsi_update_frames"] = [2]  # the reset frame
        cases = {
            "warm reset declared as no mutation": (
                output([event()], cap_fields={"warm_reset_frame": 2}, resets=[self.RESET]),
                "tracer safety capability missing"),
            "mutation declared without a warm reset": (
                output([event()], cap_fields={"guest_mutation": "requested_warm_reset"}),
                "tracer safety capability missing"),
            "other mutation label": (
                output([event()], cap_fields={"warm_reset_frame": 2, "guest_mutation": "reset"}, resets=[self.RESET]),
                "tracer safety capability missing"),
            "zero is not false": (output([event()], cap_fields={"guest_mutation": 0}), "tracer safety capability missing"),
            "reset frame attributed to SCSI": (
                output([event()], cap_fields=warm_cap(), resets=[self.RESET], ram_deltas=rows),
                "warm reset frame attributed as a SCSI-correlated RAM update"),
        }
        for name, (text, message) in cases.items():
            with self.subTest(name):
                with self.assertRaisesRegex(ValueError, message):
                    tracer.parse_records(text)

    def test_refuses_missing_extra_or_failed_resets(self):
        warm = warm_cap()
        cases = {
            "requested but not recorded": (output([event()], cap_fields=warm), "does not match the requested"),
            "recorded but not requested": (output([event()], resets=[self.RESET]), "does not match the requested"),
            "media not ready": (output([event()], cap_fields=warm, resets=[dict(self.RESET, performed=False)]),
                                "does not match the requested"),
            "wrong frame": (output([event()], cap_fields=warm, resets=[dict(self.RESET, frame=1)]),
                            "does not match the requested"),
            "two resets": (output([event()], cap_fields=warm, resets=[self.RESET, self.RESET]),
                           "does not match the requested"),
            "other API": (output([event()], cap_fields=warm, resets=[dict(self.RESET, api="Reset()")]),
                          "does not match the requested"),
            "RAM preservation not stated": (output([event()], cap_fields=warm,
                                                   resets=[dict(self.RESET, ram_preserved="all")]),
                                            "does not match the requested"),
            "master clock reset not stated": (output([event()], cap_fields=warm,
                                                     resets=[dict(self.RESET, master_clock_reset=False)]),
                                              "does not match the requested"),
            "frame 1": (output([event()], cap_fields=warm_cap(1)), "invalid warm reset frame"),
            "after last frame": (output([event()], cap_fields=warm_cap(3)), "invalid warm reset frame"),
            "bool frame": (output([event()], cap_fields={"warm_reset_frame": True}), "invalid warm reset frame"),
            "RESET in pre-PR-C output": (output([event()], pre_c=True, resets=[self.RESET]), "PR-C records without"),
            "partial PR-C capability": (output([event()], cap_fields={"warm_reset_frame": MISSING}),
                                        "PR-C capability fields incomplete"),
            "gap carry not claimed": (output([event()], cap_fields={"region_gap_carry": False}),
                                      "REGION gap carry capability missing"),
            "PR-C without PR-B accounting": (output([event()], legacy=True).replace(
                '"ram_delta_summary": true', '"ram_delta_summary": true, "region_gap_carry": true, '
                '"warm_reset_frame": 0'), "lacks the PR-B core trace accounting"),
        }
        for name, (text, message) in cases.items():
            with self.subTest(name):
                with self.assertRaisesRegex(ValueError, message):
                    tracer.parse_records(text)

    def test_tracer_source_uses_the_existing_warm_reset_api_once_before_the_frame(self):
        source = (Path(__file__).resolve().parents[2] / "tests/integration/geargrafx_frame_tracer.cpp").read_text(
            encoding="utf-8")
        self.assertEqual(source.count("core.ResetMedia(true);"), 1)
        # Guarded only by the requested frame, and recorded right after the call (real-core test checks it ran).
        self.assertRegex(source, r"const bool reset_this_frame = warm_reset_frame && number == \(unsigned\)warm_reset_frame;"
                                 r"\n\s*if \(reset_this_frame\) \{\n"
                                 r"(\s*//[^\n]*\n)*\s*const bool media_ready = core\.GetMedia\(\)->IsReady\(\);\n"
                                 r"\s*core\.ResetMedia\(true\);\n\s*std::printf\(\"RESET ")
        # The reset frame's RAM delta is never attributed to SCSI and never yields a HANDOFF candidate.
        self.assertIn("(frame_scsi_incomplete || reset_this_frame) && delta.changed != 0", source)
        self.assertIn("if (delta.changed && scsi_observed_this_frame && !reset_this_frame)", source)
        self.assertIn("if (!reset_this_frame && now_cpu.pc_is_wram && wram_changed &&", source)
        self.assertIn('warm_reset_frame ? "\\"requested_warm_reset\\"" : "false"', source)
        reset = source.index("core.ResetMedia(true);")
        self.assertLess(source.index("for (unsigned number = 1;"), reset)
        self.assertLess(reset, source.index("core.RunToVBlank("))


class ScsiFailClosedTests(unittest.TestCase):
    """A1 PR-C: max_frame_scsi_events is MEASURED only with no gap and no discontinuity frame."""

    BROKEN = CoreTraceAccountingTests.changed(CoreTraceAccountingTests.BASE, frames_accounted=1,
                                              discontinuity_frames=1, logged_accounted=40, consumed=40,
                                              total_logged=5, sequence_delta=999)

    def assert_lower_bound(self, table, value, reason):
        scsi, bound = table["max_frame_scsi_events"], table[tracer.SCSI_LOWER_BOUND]
        self.assertEqual(set(scsi), {"status", "reason"})
        self.assertEqual(scsi["status"], tracer.NOT_MEASURED)
        self.assertIn(reason, scsi["reason"])
        self.assertEqual((bound["status"], bound["value"], bound["bound_of"], bound["kind"]),
                         (tracer.LOWER_BOUND, value, "max_frame_scsi_events", "lower_bound"))
        self.assertNotEqual(bound["status"], tracer.MEASURED)

    def test_gap_free_run_is_measured_without_a_lower_bound_row(self):
        table = table_of(output([event()], max_frame_scsi_events=7))
        self.assertEqual(table["max_frame_scsi_events"]["status"], tracer.MEASURED)
        self.assertEqual(table["max_frame_scsi_events"]["value"], 7)
        self.assertEqual(set(table), set(tracer.MEASUREMENT_NAMES))

    def test_gap_frames_give_not_measured_and_a_separate_lower_bound(self):
        table = table_of(CoreTraceAccountingTests().gap_output(max_frame_scsi_events=2))
        self.assert_lower_bound(table, 2, "1 of 2 frame(s) lost 3 core entries")
        self.assertEqual(set(table), {*tracer.MEASUREMENT_NAMES, tracer.SCSI_LOWER_BOUND})

    def test_discontinuity_gives_not_measured_and_a_lower_bound(self):
        table = table_of(output([event()], core_trace=self.BROKEN, overflow=True))
        self.assert_lower_bound(table, 3, "discontinuous in 1 of 2")

    def test_pre_pr_b_output_never_measures_and_pr_b_output_is_classified_by_its_accounting(self):
        self.assert_lower_bound(table_of(output([event()], legacy=True)), 3, "pre-PR-B tracer output")
        # PR-B output (core_trace, no PR-C fields) with no gap is exact by the same accounting.
        self.assertEqual(table_of(output([event()], pre_c=True))["max_frame_scsi_events"]["status"], tracer.MEASURED)


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
            self.assertEqual(table["max_frame_scsi_events"]["status"], tracer.MEASURED)
        self.assertEqual((written["status"], written["evidence_valid"]), ("PASS", True))

    @staticmethod
    def gap_table():
        cap, _, end = tracer.parse_records(output([event()], core_capacity=40, trace_events_dropped=3))
        return {"status": "PASS", "measurements": tracer.measurements(cap, end)}

    def test_gap_run_keeps_the_lower_bound_separate(self):
        _, written = self.evidence(self.gap_table(), self.run_table(capacity=40))  # same capacity, no gap
        on = written["runs"]["observer_on"]
        self.assertEqual(on["max_frame_scsi_events"]["status"], tracer.NOT_MEASURED)
        self.assertEqual(on[tracer.SCSI_LOWER_BOUND]["status"], tracer.LOWER_BOUND)
        self.assertNotIn(tracer.SCSI_LOWER_BOUND, written["runs"]["observer_off"])

    def test_refuses_misclassified_scsi_rows(self):
        promoted = self.gap_table()  # gap run whose SCSI maximum is relabelled exact
        promoted["measurements"]["max_frame_scsi_events"] = {"status": tracer.MEASURED, "value": 3}
        del promoted["measurements"][tracer.SCSI_LOWER_BOUND]
        bound_measured = self.gap_table()
        bound_measured["measurements"][tracer.SCSI_LOWER_BOUND]["status"] = tracer.MEASURED
        bound_missing = self.gap_table()
        del bound_missing["measurements"][tracer.SCSI_LOWER_BOUND]
        value_kept = self.gap_table()
        value_kept["measurements"]["max_frame_scsi_events"]["value"] = 3
        extra_bound = self.run_table()
        extra_bound["measurements"][tracer.SCSI_LOWER_BOUND] = {"status": tracer.LOWER_BOUND, "value": 3}
        demoted = self.run_table()
        demoted["measurements"]["max_frame_scsi_events"] = {"status": tracer.NOT_MEASURED, "reason": "x"}
        unknown_row = self.run_table()
        unknown_row["measurements"]["scsi_events_upper_bound"] = {"status": tracer.LOWER_BOUND, "value": 1}
        # Gap runs whose SCSI rows are consistent, so only the dropped-frame count check can reject them.
        gap_no_loss = self.gap_table()
        gap_no_loss["measurements"]["core_trace_dropped_frames"]["lost_entries"] = 0
        gap_too_many = self.gap_table()
        gap_too_many["measurements"]["core_trace_dropped_frames"]["value"] = 5  # 2 frames
        cases = {
            "gap frames without lost entries": (gap_no_loss, "dropped-frame count is inconsistent"),
            "more gap frames than frames": (gap_too_many, "dropped-frame count is inconsistent"),
            "gap run promoted to MEASURED": (promoted, "disagrees with core-trace gap frames"),
            "lower bound relabelled MEASURED": (bound_measured, "not separated"),
            "lower bound missing": (bound_missing, "disagrees with core-trace gap frames"),
            "NOT_MEASURED row carries a value": (value_kept, "not separated"),
            "gap-free run with a lower bound row": (extra_bound, "disagrees with core-trace gap frames"),
            "gap-free run demoted": (demoted, "disagrees with core-trace gap frames"),
            "unknown extra row": (unknown_row, "missing or incomplete"),
        }
        for name, (on, message) in cases.items():
            with self.subTest(name):
                with self.assertRaisesRegex(ValueError, message):
                    self.evidence(on, self.run_table(capacity=on["measurements"]["core_capacity"]["value"]))

    def test_refuses_promoted_missing_or_inconsistent_tables(self):
        promoted = self.run_table()
        promoted["measurements"]["max_frame_scsi_events"] = {"status": tracer.MEASURED, "value": "3"}
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
            "legacy (pre-PR-B) table on the real core": (legacy_on, self.run_table(), "is not a measured count"),
            "dropped frames without lost entries": (bad_drops, self.run_table(), "disagrees with core-trace gap"),
            "more dropped frames than frames": (too_many_drops, self.run_table(), "disagrees with core-trace gap"),
            "measured SCSI value not a count": (promoted, self.run_table(), "max_frame_scsi_events is not a measured"),
            "configuration relabelled": (self.run_table(), relabelled, "unexpected measurement status"),
            "push mismatch": (bad_push, self.run_table(), "push accounting mismatch"),
            "quantity missing": (missing, self.run_table(), "missing or incomplete"),
            "no table": ({"status": "PASS"}, self.run_table(), "missing or incomplete"),
            "capacity differs": (self.run_table(capacity=100000), self.run_table(capacity=50000),
                                 "capacity differs between observer runs"),
        }
        for name, (on, off, message) in cases.items():
            with self.subTest(name):
                with self.assertRaisesRegex(ValueError, message):
                    self.evidence(on, off)


class CommercialSanitizationTests(unittest.TestCase):
    """Issue #635: opt-in commercial runs must never leak private inputs into tracer logs.

    These are host-only (mocked core) tests: doctor is never invoked with caller media,
    and staging errors are reported with generic reasons that carry no private filename,
    absolute path, or hash.  The local commercial JSON is inspected the same way.
    Synthetic placeholders below are not real media names.
    """

    PRIVATE_ROM = Path("/private/rom/dev.pce")
    PRIVATE_CUE = Path("/private/rom/title.cue")
    PRIVATE_BIN = "private-title-first-track.bin"

    def _args(self, frames=2, events=4):
        return SimpleNamespace(frames=frames, events=events, timeout=1, rom=self.PRIVATE_ROM,
                               cue=self.PRIVATE_CUE, run_start_frame=None, run_hold_frames=None,
                               observe_ram=True)

    def _serialized(self, log):
        return json.dumps(log, sort_keys=True)

    def _run_with_job(self, stage_disc_side=None, stage_disc_return=None, run_job=None):
        """Run tracer.trace with a real temporary job dir and mocked core; return (log, doctor_mock)."""
        with tempfile.TemporaryDirectory() as tmp:
            job = Path(tmp) / "job"
            job.mkdir()
            stage_kwargs = {}
            if stage_disc_side is not None:
                stage_kwargs["side_effect"] = stage_disc_side
            elif stage_disc_return is not None:
                stage_kwargs["return_value"] = stage_disc_return
            job_result = run_job if run_job is not None else {"status": None, "exit_code": 0,
                                                              "stdout": output([event()]), "elapsed_s": 0.1}
            with mock.patch.object(tracer, "doctor", return_value={"status": "PASS"}) as doctor, \
                    mock.patch.object(tracer, "make_tracked_job_dir", return_value=(job, (1, 2, 3))), \
                    mock.patch.object(tracer, "compile_probe", return_value=Path("probe")), \
                    mock.patch.object(tracer.shutil, "copyfile"), \
                    mock.patch.object(tracer, "stage_disc", **stage_kwargs), \
                    mock.patch.object(tracer, "run_job", return_value=job_result), \
                    mock.patch.object(tracer, "cleanup_tracked_job_dir", return_value=(True, "removed")):
                log = tracer.trace(self._args())
            return log, doctor

    def test_doctor_is_never_invoked_with_caller_media(self):
        log, doctor = self._run_with_job(stage_disc_return=["copied.cue"])
        self.assertEqual(log["status"], "PASS")
        doctor.assert_called_once_with(None, None, None)
        text = self._serialized(log)
        for secret in ("/private", "title.cue", self.PRIVATE_BIN):
            self.assertNotIn(secret, text)
        self.assertNotIn("sha256", text)

    def test_disc_staging_missing_file_is_sanitized(self):
        log, _ = self._run_with_job(
            stage_disc_side=FileNotFoundError(f"CUE data file missing: {self.PRIVATE_BIN}"))
        self.assertEqual((log["status"], log["evidence"]), ("BLOCKED", "NOT_RUN"))
        self.assertEqual(log["reason"], "disc staging input missing")
        text = self._serialized(log)
        for secret in ("/private", "title.cue", self.PRIVATE_BIN):
            self.assertNotIn(secret, text)

    def test_disc_staging_invalid_input_is_sanitized(self):
        log, _ = self._run_with_job(
            stage_disc_side=ValueError(f"unsafe CUE file name {self.PRIVATE_BIN!r}"))
        self.assertEqual((log["status"], log["evidence"]), ("FAIL", "NOT_RUN"))
        self.assertEqual(log["reason"], "invalid disc input")
        text = self._serialized(log)
        for secret in ("/private", "title.cue", self.PRIVATE_BIN):
            self.assertNotIn(secret, text)

    def test_unknown_cap_field_is_rejected_without_leak(self):
        text = output([event()], cap_fields={"private_path": "/private/rom/dev.pce",
                                             "media_name": "private-title"})
        with self.assertRaises(ValueError) as caught:
            tracer.parse_records(text)
        self.assertIn("unknown fields", str(caught.exception))
        serialized = json.dumps(str(caught.exception))
        self.assertNotIn("/private", serialized)
        self.assertNotIn("private-title", serialized)

    def test_unknown_end_and_event_fields_are_rejected(self):
        with self.assertRaises(ValueError):
            tracer.parse_records(output([event()], secret_hash="0123abcd"))
        bad_event = dict(event(), private_path="/private/rom/dev.pce",
                         arbitrary="private-title-first-track.bin")
        with self.assertRaises(ValueError):
            tracer.parse_records(output([bad_event]))

    def test_unknown_nested_fields_are_rejected(self):
        bad_cap = {"ram_regions": [{"ram": "wram", "size": 8192, "bank_first": 248,
                                    "bank_last": 251, "private_path": "/private/rom/dev.pce"}]}
        with self.assertRaises(ValueError):
            tracer.parse_records(output([event()], cap_fields=bad_cap))
        deltas = ram_deltas()
        deltas[0] = dict(deltas[0], private_title="private-title-first-track.bin")
        with self.assertRaises(ValueError):
            tracer.parse_records(output([event()], ram_deltas=deltas))

    def test_probe_build_failure_is_sanitized(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = Path(tmp) / "job"
            job.mkdir()
            with mock.patch.object(tracer, "doctor", return_value={"status": "PASS"}), \
                    mock.patch.object(tracer, "make_tracked_job_dir", return_value=(job, (1, 2, 3))), \
                    mock.patch.object(tracer, "compile_probe",
                                      side_effect=RuntimeError(f"g++ failed: /private/rom/dev.pce sha256=0123abcd")), \
                    mock.patch.object(tracer, "cleanup_tracked_job_dir", return_value=(True, "removed")):
                log = tracer.trace(self._args())
        self.assertEqual((log["status"], log["reason"]), ("BLOCKED", "probe build failed"))
        text = self._serialized(log)
        for secret in ("/private", "title.cue", self.PRIVATE_BIN, "sha256", "0123abcd"):
            self.assertNotIn(secret, text)

    def test_run_job_cannot_execute_is_sanitized(self):
        log, _ = self._run_with_job(run_job={"status": "BLOCKED",
                                             "reason": "cannot execute: /private/rom/dev.pce",
                                             "exit_code": None, "stdout": "", "elapsed_s": 0.0})
        self.assertEqual((log["status"], log["reason"]), ("BLOCKED", "cannot execute probe"))
        text = self._serialized(log)
        self.assertNotIn("/private", text)

    def test_trace_rejected_unknown_field_is_sanitized(self):
        stdout = output([event()], cap_fields={"private_path": "/private/rom/dev.pce"})
        log, _ = self._run_with_job(run_job={"status": None, "exit_code": 0, "stdout": stdout, "elapsed_s": 0.1})
        self.assertEqual((log["status"], log["reason"]), ("FAIL", "trace rejected"))
        text = self._serialized(log)
        self.assertNotIn("/private", text)


if __name__ == "__main__":
    unittest.main()
