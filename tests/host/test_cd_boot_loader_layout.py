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
        self.assertNotIn("プリギャップ 150", self.text)
        self.assertNotIn("INDEX 01 = 150", self.text)
        self.assertNotIn("INDEX 01 が 150", self.text)

    def test_single_file_is_the_baseline_and_multi_file_is_separate(self):
        # Owner policy: V1/V2 are single-FILE (M4/M5 baseline); multi-FILE is V2M, reported separately.
        layout = next(b for b in markdown_table_blocks(self.text) if b[0].startswith("| 変種 |"))
        rows = {row.split("|")[1].strip(): row for row in layout[2:]}
        self.assertIn("単一 FILE", rows["V1（対照）"])
        self.assertIn("単一 FILE", rows["V2（ゲームと同形）"])
        self.assertIn("225", rows["V2（ゲームと同形）"])
        self.assertIn("多 FILE", rows["V2M（追加条件）"])
        self.assertIn("合格扱いにしない", rows["V2M（追加条件）"])
        self.assertNotIn("同一 FILE の変種は作らない", self.text)

    def test_single_and_multi_file_give_the_same_absolute_lba(self):
        # Single FILE: INDEX values are absolute. Same disc as the multi-FILE CUE in the test above.
        cue = ('FILE "s.bin" BINARY\r\n  TRACK 01 AUDIO\r\n    INDEX 01 00:00:00\r\n'
               '  TRACK 02 MODE1/2352\r\n    INDEX 00 00:18:58\r\n    INDEX 01 00:21:58\r\n')
        index = parse_cue(cue)[0]["tracks"][1]["index"]
        self.assertEqual(index, {0: 1408, 1: 1633})
        self.assertEqual(index[1] - index[0], 225)

    def test_spec_header_is_8_byte_pcex_matching_the_dev_loader(self):
        # The DEV loader source (not tools/) defines the header size; the spec must agree with it.
        loader = (ROOT / "src" / "cd_load_exec_probe.s").read_text(encoding="utf-8")
        self.assertRegex(loader, r"(?m)^HDR_BYTES\s*=\s*8\b")
        self.assertIn("8 バイト", self.text)
        self.assertIn("PCEX", self.text)
        # The 16-byte OSSBOOT1 form may appear only as a separately labelled, incompatible version.
        for line in self.text.splitlines():
            if re.search(r"(?<![\d,])16 バイト", line):   # not the 3,311,616 バイト BIN size
                self.assertIn("非互換", line)

    def test_spec_labels_1633_and_1408_with_their_status(self):
        for needle in ("1,633", "1,408", "ASSUMPTION", "LOCAL"):
            self.assertIn(needle, self.text, needle)
        # 1,558 (= 1,408 + 150) may appear only as the recorded error, never as a value.
        for line in self.text.splitlines():
            if "1,558" in line:
                self.assertIn("誤り", line)


def markdown_table_blocks(text):
    """Group consecutive lines that start with '|' into blocks (each block is one table)."""
    blocks, current = [], []
    for line in text.splitlines():
        if line.lstrip().startswith("|"):
            current.append(line.strip())
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    return blocks


def cell_count(row):
    return len(row.strip().strip("|").split("|"))


class SpecTableTests(unittest.TestCase):
    """Every table row in the spec must belong to a table whose header and separator agree."""

    def setUp(self):
        self.blocks = markdown_table_blocks(SPEC.read_text(encoding="utf-8"))

    def test_every_table_block_has_header_separator_and_matching_column_counts(self):
        self.assertTrue(self.blocks)
        for block in self.blocks:
            header = block[0]
            self.assertRegex(block[1], r"^\|(\s*-+\s*\|)+$", f"no separator under: {header}")
            width = cell_count(header)
            for row in block:
                self.assertEqual(cell_count(row), width, f"column count mismatch: {row}")

    def test_failure_state_table_contains_budget_and_retry_rows(self):
        failure = next(b for b in self.blocks if b[0].startswith("| 状態 | 条件"))
        names = [row.split("|")[1].strip().strip("`") for row in failure[2:]]
        for name in ("NO_TRACK", "DATA_TIMEOUT", "PREMATURE_STATUS", "BAD_MAGIC",
                     "BAD_LENGTH", "BAD_ENTRY_OFFSET", "BAD_CHECKSUM", "BUDGET", "RETRY_EXHAUSTED"):
            self.assertIn(name, names)


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
