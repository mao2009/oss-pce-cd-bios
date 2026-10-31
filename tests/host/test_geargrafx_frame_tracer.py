"""Synthetic protocol tests for the normal-frame tracer; no Geargrafx core runs here."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

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


def output(events, dropped=0, scsi_events_dropped=0, capacity=4):
    cap = {"normal_frame_execution": True, "instruction_step": False, "guest_mutation": False, "scsi_event_notification": True,
           "mmio_reads": False, "ring_capacity": capacity, "frames": 2, "disc_given": False}
    end = {"frames": 2, "events": len(events), "dropped": dropped, "scsi_events_dropped": scsi_events_dropped,
           "overflow": bool(dropped or scsi_events_dropped)}
    return "\n".join(["CAP " + json.dumps(cap), *["EVENT " + json.dumps(row) for row in events],
                      "END " + json.dumps(end)])


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

    def test_refuses_scsi_records_without_frame_snapshot_label(self):
        row = event("cd")
        row["snapshot"] = "EVENT_TIME"
        with self.assertRaisesRegex(ValueError, "FRAME_SNAPSHOT"):
            tracer.parse_records(output([row]))

    def test_cleanup_rejects_untracked_or_replaced_directories(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            untracked = root / "untracked"
            untracked.mkdir()
            self.assertFalse(tracer.cleanup_tracked_job_dir(untracked, (0, 0)))
            self.assertTrue(untracked.is_dir())

            tracked, identity = tracer.make_tracked_job_dir()
            moved = root / "original-job"
            tracked.rename(moved)
            tracked.mkdir()
            try:
                self.assertFalse(tracer.cleanup_tracked_job_dir(tracked, identity))
                self.assertTrue(tracked.is_dir())
                self.assertTrue(moved.is_dir())
            finally:
                shutil.rmtree(tracked)
                shutil.rmtree(moved)

    def test_cleanup_reports_missing_tracked_directory_without_deleting_anything(self):
        tracked, identity = tracer.make_tracked_job_dir()
        shutil.rmtree(tracked)
        self.assertFalse(tracer.cleanup_tracked_job_dir(tracked, identity))

    @unittest.skipUnless(shutil.which("c++"), "C++ compiler unavailable")
    def test_ring_capacities_are_executed_in_cpp(self):
        source = Path(__file__).with_name("geargrafx_frame_tracer_ring_test.cpp")
        include = source.parents[1] / "integration"
        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary) / "ring-test"
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


if __name__ == "__main__":
    unittest.main()
