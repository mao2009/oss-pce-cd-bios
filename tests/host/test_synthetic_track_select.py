"""Host-only CUE selection checks for the synthetic real-core track diagnostic."""
import importlib.util
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from tools import cue_analysis
from tools import synthetic_boot_media as media
from tools.make_cd_image import CUE_NAME as READ_CUE_NAME, build_image
from tools.synthetic_track_select import NoDataTrackError, select_first_mode1_track

INTEGRATION = Path(__file__).resolve().parents[1] / "integration" / "geargrafx_synthetic_track_select.py"
SPEC = importlib.util.spec_from_file_location("geargrafx_synthetic_track_select", INTEGRATION)
track_integration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(track_integration)


class SyntheticTrackSelectionTests(unittest.TestCase):
    def write(self, mutation, layout):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        media.write_case(root, mutation, layout)
        return root / media.CUE_NAME

    def test_single_data_track_selects_index01(self):
        # A one-track CUE is separately generated here; the selection helper is
        # intentionally driven by CUE analysis rather than a guessed BIOS command.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_image(root)
            cue = root / READ_CUE_NAME
            selected = select_first_mode1_track(cue)
            self.assertEqual((selected["track"], selected["lba"]), (1, 0))

    def test_multitrack_selects_data_after_audio(self):
        cue = self.write("NONE", "V1")
        selected = select_first_mode1_track(cue)
        self.assertEqual((selected["track"], selected["lba"]), (2, media.AUDIO_FRAMES))

    def test_stored_pregap_selects_index01_not_index00(self):
        cue = self.write("NONE", "V2")
        selected = select_first_mode1_track(cue)
        track = selected["report"]["tracks"][1]
        self.assertEqual(track["pregap_frames"], media.V2_PREGAP_FRAMES)
        self.assertEqual(selected["lba"], track["start_lba"])
        self.assertNotEqual(selected["lba"], track["pregap_start_lba"])
        with self.assertRaisesRegex(cue_analysis.CueError, "pregap"):
            cue_analysis.user_sector(cue, selected["report"], track["pregap_start_lba"])

    def test_audio_only_is_rejected(self):
        cue = self.write("NO_TRACK", "V1")
        with self.assertRaises(NoDataTrackError):
            select_first_mode1_track(cue)


class SyntheticTrackSelectionClassificationTests(unittest.TestCase):
    def test_missing_artifact_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(track_integration.BlockedError):
                track_integration.require_file(Path(tmp) / "missing.pce")
        self.assertEqual(track_integration.exit_for_error(track_integration.BlockedError("BLOCKED: missing")), 3)

    def test_partial_core_observation_is_blocked(self):
        partial = {"CAP": [{}], "RUN": [{"cycle": "cold"}], "PHASE": []}
        with self.assertRaisesRegex(track_integration.BlockedError, "partial probe record"):
            track_integration.require_complete_records(partial, "test")
        with mock.patch.object(track_integration, "execute", side_effect=ValueError("probe did not complete cold and warm runs")):
            with self.assertRaisesRegex(track_integration.BlockedError, "partial or unreadable"):
                track_integration.execute_complete("probe", "rom", "cue", "labels")

    def test_completed_oracle_failure_is_fail(self):
        self.assertEqual(track_integration.exit_for_error(ValueError("cold: RAM differs from sector data")), 1)
        self.assertEqual(track_integration.exit_for_error(ValueError("unexpected Geargrafx behaviour")), 1)
