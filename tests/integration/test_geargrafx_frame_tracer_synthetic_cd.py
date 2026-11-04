"""Pinned-core, original synthetic-CD coverage for the frame RAM summary.

This runs the DEV-only internal CD diagnostic, not a System Card BIOS or a
commercial title.  The retained event ring is intentionally tiny: the test
checks that cumulative RAM metadata remains available after event overflow.
"""
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_dev_boot_internal import ROM_NAME, build_dev_boot_internal  # noqa: E402
from build_rom import main as build_dev_rom  # noqa: E402
from make_cd_image import CUE_NAME, build_image  # noqa: E402
import geargrafx_frame_tracer as tracer  # noqa: E402


class FrameTracerSyntheticCdRealCoreTests(unittest.TestCase):
    """Real Geargrafx test with reproducible media only; no guest bytes escape."""

    @classmethod
    def setUpClass(cls):
        # `make integration-test` follows `make build`, but this opt-in ROM has
        # its own explicit DEV-only builder and remains outside normal artifacts.
        previous = sys.argv[:]
        try:
            sys.argv = ["build_rom.py", "--mode", "debug"]
            build_dev_rom()
        finally:
            sys.argv = previous
        base = ROOT / "build/debug/bios-dev/dev-only-not-compatible-syscard3.pce"
        cls.rom = build_dev_boot_internal(base, ROOT / "build/debug/dev-boot-internal", "debug")

    def test_cumulative_deltas_survive_event_overflow_on_synthetic_cd(self):
        with tracer.tempfile.TemporaryDirectory(prefix="frame-synthetic-cd-") as temporary:
            media = Path(temporary) / "media"
            approved_root = Path(temporary) / "frame-tracer-root"
            approved_root.mkdir(mode=0o700)
            build_image(media)
            # CI does not provide the local /tmp/opencode root used by the
            # standalone diagnostic.  Give this test a freshly-created,
            # private root instead; tracer validation and identity-checked
            # cleanup remain enabled for its job beneath this root.
            with mock.patch.object(tracer, "OPENCODE_TEMP_ROOT", approved_root), \
                    mock.patch.object(tracer, "JOB_PARENT", approved_root / "frame-tracer-jobs"):
                result = tracer.trace(SimpleNamespace(
                    frames=120, events=1, timeout=120.0, rom=self.rom, cue=media / CUE_NAME,
                    run_start_frame=None, run_hold_frames=None, observe_ram=True,
                ))

        self.assertEqual(result["status"], "PASS", result)
        self.assertEqual(result["evidence"], "REAL_GEARGRAFX_CORE")
        self.assertEqual(result["cleanup"], "REMOVED")
        summary = result["summary"]
        self.assertTrue(summary["overflow"])
        self.assertGreater(summary["dropped"], 0)
        self.assertEqual(summary["events"], 1)
        # Measurement table: the output-ring dropped count is a real count checked by an independent
        # push counter; core_capacity is the core-reported configuration, not a usage measurement.
        table = result["measurements"]
        ring = table["output_ring_dropped"]
        self.assertEqual(ring["status"], tracer.MEASURED)
        self.assertEqual(ring["value"], summary["dropped"])
        self.assertEqual(ring["retained"], 1)
        self.assertEqual(ring["ring_capacity"], 1)
        self.assertEqual(ring["pushed"], summary["pushed"])
        self.assertEqual(ring["pushed"], ring["retained"] + ring["value"])
        self.assertGreaterEqual(ring["pushed"], 120)  # one SAMPLE is pushed for every one of the 120 frames
        capacity = table["core_capacity"]
        self.assertEqual(capacity["status"], tracer.REPORTED_BY_CORE)
        self.assertEqual(capacity["kind"], "configuration")
        self.assertIs(type(capacity["value"]), int)
        self.assertGreater(capacity["value"], 0)
        self.assertEqual(capacity["value"], summary["core_capacity"])
        for name in ("max_frame_core_entries", "max_frame_scsi_events", "core_trace_dropped_frames"):
            self.assertEqual(table[name]["status"], tracer.NOT_MEASURED, name)
            self.assertNotIn("value", table[name], name)
        rows = {row["region"]: row for row in summary["ram_deltas"]}
        # The synthetic read changes WRAM and has observed SCSI-frame correlation.
        self.assertGreater(rows["wram"]["updates"], 0)
        self.assertEqual(rows["wram"]["scsi_correlation"], "OBSERVED")
        self.assertTrue(rows["wram"]["scsi_update_frames"])
        # These mapped core regions are present but unchanged in this diagnostic.
        for region in ("cdrom_ram", "card_ram"):
            self.assertEqual(rows[region]["availability"], "PRESENT")
            self.assertEqual(rows[region]["updates"], 0)
            self.assertIsNone(rows[region]["first_frame"])
            self.assertEqual(rows[region]["scsi_correlation"], "NONE_OBSERVED")


if __name__ == "__main__":
    unittest.main()
