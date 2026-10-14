import json
import tempfile
import unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.cue_analysis import analyze, CueError, user_sector
from tools.make_cd_image import build_image

class CueAnalysisTests(unittest.TestCase):
    def test_synthetic_cuebin_analysis(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            build_image(d)
            rep = analyze(d / "oss-pce-cd-test.cue")
            self.assertTrue(rep["structurally_valid"])
            self.assertEqual(rep["data_tracks"], [1])
            self.assertEqual(rep["total_frames"], 16)
            data = user_sector(d / "oss-pce-cd-test.cue", rep, 5)
            self.assertEqual(len(data), 2048)
            with self.assertRaises(CueError):
                user_sector(d / "oss-pce-cd-test.cue", rep, 16)
            with self.assertRaises(CueError):
                user_sector(d / "oss-pce-cd-test.cue", rep, -1)

    def test_invalid_cue_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "bad.cue").write_text('FILE "missing.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n', encoding='ascii')
            with self.assertRaises(CueError):
                analyze(d / "bad.cue")

if __name__ == '__main__':
    unittest.main()
