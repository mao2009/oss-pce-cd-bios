"""Host-only CUE selection checks for the synthetic real-core track diagnostic."""
import tempfile
import unittest
from pathlib import Path

from tools import cue_analysis
from tools import synthetic_boot_media as media
from tools.make_cd_image import CUE_NAME as READ_CUE_NAME, build_image
from tools.synthetic_track_select import NoDataTrackError, select_first_mode1_track


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
