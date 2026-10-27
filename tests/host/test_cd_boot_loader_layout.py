"""Layout checks: docs/cd-boot-loader-spec.md numbers against a CUE sheet (#554).

Independent of tools/: the CUE parser and MSF arithmetic live in this file.
- Synthetic tests always run (CRLF, multi-FILE, same-FILE).
- Game tests read the real CUE only when OSS_PCE_GAME_CUE is set to its path
  (local, non-published data). Otherwise they skip with a reason.
"""
import os
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "docs" / "cd-boot-loader-spec.md"
SECTOR_BYTES = 2352
FPS = 75
LEAD_IN_FRAMES = 150  # absolute MSF = LBA + 150 (docs/cd-read-diagnostic.md)


def msf_frames(text):
    mm, ss, ff = (int(x) for x in text.split(":"))
    return (mm * 60 + ss) * FPS + ff


def parse_cue(text):
    """Return [{'file': name, 'tracks': [{'no', 'mode', 'index': {n: frames}}]}]. CRLF tolerant."""
    files = []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("FILE "):
            m = re.match(r'FILE "(.+)" BINARY$', line)
            files.append({"file": m.group(1), "tracks": []})
        elif line.startswith("TRACK "):
            _, no, mode = line.split(None, 2)
            files[-1]["tracks"].append({"no": int(no), "mode": mode, "index": {}})
        elif line.startswith("INDEX "):
            _, no, msf = line.split()
            files[-1]["tracks"][-1]["index"][int(no)] = msf_frames(msf)
    return files


def contiguous_index_lbas(files, sectors_per_file):
    """(track, index) -> LBA, assuming FILEs are laid out back to back from LBA 0.

    This contiguity is an ASSUMPTION (the CUE does not state disc positions). Used only to
    check the spec's ASSUMPTION row, never as a fact.
    """
    out, base = {}, 0
    for f in files:
        for t in f["tracks"]:
            for n, frames in t["index"].items():
                out[(t["no"], n)] = base + frames
        base += sectors_per_file[f["file"]]
    return out


class SyntheticLayoutTests(unittest.TestCase):
    def test_msf_arithmetic(self):
        self.assertEqual(msf_frames("00:03:00"), 225)
        self.assertEqual(msf_frames("00:02:00"), 150)
        self.assertEqual(msf_frames("00:02:00") - LEAD_IN_FRAMES, 0)  # absolute 00:02:00 is LBA 0
        self.assertEqual(msf_frames("00:23:58") - LEAD_IN_FRAMES, 1633)

    def test_multi_file_index_is_relative_to_its_file_and_crlf_is_tolerated(self):
        cue = ('CATALOG 0000000000000\r\n'
               'FILE "a.bin" BINARY\r\n  TRACK 01 AUDIO\r\n    INDEX 01 00:00:00\r\n'
               'FILE "b.bin" BINARY\r\n  TRACK 02 MODE1/2352\r\n'
               '    INDEX 00 00:00:00\r\n    INDEX 01 00:03:00\r\n')
        files = parse_cue(cue)
        self.assertEqual(files[1]["tracks"][0]["index"], {0: 0, 1: 225})
        lbas = contiguous_index_lbas(files, {"a.bin": 1408, "b.bin": 7921})
        self.assertEqual(lbas[(2, 0)], 1408)
        self.assertEqual(lbas[(2, 1)], 1633)

    def test_same_file_index_is_relative_to_the_shared_file(self):
        cue = ('FILE "one.bin" BINARY\r\n  TRACK 01 AUDIO\r\n    INDEX 01 00:00:00\r\n'
               '  TRACK 02 AUDIO\r\n    INDEX 01 00:02:00\r\n')
        files = parse_cue(cue)
        # One FILE: track 2 INDEX 01 is file frame 150, not an offset from track 2's start.
        self.assertEqual(files[0]["tracks"][1]["index"][1], 150)
        self.assertEqual(len(files), 1)


class SpecTextTests(unittest.TestCase):
    def setUp(self):
        self.text = SPEC.read_text(encoding="utf-8")

    def test_spec_states_225_and_not_the_stale_150(self):
        self.assertIn("225", self.text)
        self.assertIn("00:03:00", self.text)
        self.assertNotIn("プリギャップ 150 セクタ", self.text)
        self.assertNotIn("INDEX 01 = 150", self.text)
        self.assertNotIn("INDEX 01 が 150", self.text)

    def test_spec_labels_1633_and_1408_with_their_status(self):
        for needle in ("1,633", "1,408", "ASSUMPTION", "LOCAL"):
            self.assertIn(needle, self.text, needle)
        # 1,558 (= 1,408 + 150) may appear only as the recorded error, never as a value.
        for line in self.text.splitlines():
            if "1,558" in line:
                self.assertIn("誤り", line)


@unittest.skipUnless(os.environ.get("OSS_PCE_GAME_CUE"), "set OSS_PCE_GAME_CUE to the local game CUE path")
class GameCueTests(unittest.TestCase):
    def setUp(self):
        self.cue = Path(os.environ["OSS_PCE_GAME_CUE"])
        self.files = parse_cue(self.cue.read_bytes().decode("ascii"))
        self.sectors = {}
        for f in self.files:
            size = os.path.getsize(self.cue.parent / f["file"])
            self.assertEqual(size % SECTOR_BYTES, 0, f["file"])
            self.sectors[f["file"]] = size // SECTOR_BYTES

    def track(self, no):
        return next(t for f in self.files for t in f["tracks"] if t["no"] == no)

    def test_data_track_index_and_sizes(self):
        data = [t for f in self.files for t in f["tracks"] if t["mode"].startswith("MODE")]
        self.assertEqual([t["no"] for t in data], [2])
        self.assertEqual(self.track(2)["mode"], "MODE1/2352")
        self.assertEqual(self.track(2)["index"], {0: 0, 1: 225})
        self.assertEqual(self.track(3)["index"][1], 150)  # track 3's own pregap, unrelated to track 2
        first_file = self.files[0]["file"]
        self.assertEqual(self.sectors[first_file], 1408)
        self.assertEqual(self.sectors[self.files[1]["file"]], 7921)

    def test_index01_absolute_lba_is_1633_under_contiguity(self):
        lbas = contiguous_index_lbas(self.files, self.sectors)
        self.assertEqual(lbas[(2, 1)], 1633)  # contiguity is ASSUMPTION; see spec 1.1 and section 8


if __name__ == "__main__":
    unittest.main(verbosity=2)
