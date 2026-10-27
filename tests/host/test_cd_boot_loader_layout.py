"""Layout checks: docs/cd-boot-loader-spec.md numbers against a CUE sheet (#554).

The CUE parser and MSF arithmetic live in this file (not tools/). CueAnalysisConventionTests is the one
cross-check against tools/cue_analysis.py, using media written here.
- Synthetic tests always run (CRLF, multi-FILE, same-FILE, V1/V2/V2M media).
- Game tests read the real CUE only when OSS_PCE_GAME_CUE is set to its path
  (local, non-published data). Otherwise they skip with a reason.
"""
import os
import re
import sys
import tempfile
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

    def test_single_file_cue_index_values_equal_the_multi_file_contiguous_lbas(self):
        # Single FILE without a virtual PREGAP: INDEX values are absolute and match the multi-FILE CUE above.
        cue = ('FILE "s.bin" BINARY\r\n  TRACK 01 AUDIO\r\n    INDEX 01 00:00:00\r\n'
               '  TRACK 02 MODE1/2352\r\n    INDEX 00 00:18:58\r\n    INDEX 01 00:21:58\r\n')
        index = parse_cue(cue)[0]["tracks"][1]["index"]
        multi = parse_cue('FILE "a.bin" BINARY\r\n  TRACK 01 AUDIO\r\n    INDEX 01 00:00:00\r\n'
                          'FILE "b.bin" BINARY\r\n  TRACK 02 MODE1/2352\r\n'
                          '    INDEX 00 00:00:00\r\n    INDEX 01 00:03:00\r\n')
        lbas = contiguous_index_lbas(multi, {"a.bin": 1408, "b.bin": 7921})
        self.assertEqual(index, {0: lbas[(2, 0)], 1: lbas[(2, 1)]})
        self.assertEqual(index, {0: 1408, 1: 1633})
        self.assertEqual(index[1] - index[0], 225)


def _bcd(value):
    return (value // 10) << 4 | value % 10


def _mode1_frame(lba):
    """Raw MODE1/2352 frame: sync, BCD MSF of LBA + 150, mode 1, zero user data (written here, not in tools/)."""
    msf = lba + LEAD_IN_FRAMES
    head = bytes([_bcd(msf // 4500), _bcd(msf // FPS % 60), _bcd(msf % FPS), 1])
    return b"\x00" + b"\xff" * 10 + b"\x00" + head + bytes(SECTOR_BYTES - 16)


AUDIO_N = 1408      # same track 01 length as the game, so the expected LBAs are the spec's 1,408 / 1,633
PREGAP = 225
DATA_N = 4


def write_layout(folder, variant):
    """Write V1, V2 or V2M (docs/cd-boot-loader-spec.md S5) into `folder`; return the CUE path."""
    audio = bytes(SECTOR_BYTES) * AUDIO_N          # silence, no MODE sync inside AUDIO
    gap = 0 if variant == "V1" else PREGAP
    data = b"".join(_mode1_frame(AUDIO_N + i) for i in range(gap + DATA_N))
    msf = lambda f: "%02d:%02d:%02d" % (f // 4500, f // FPS % 60, f % FPS)
    if variant == "V2M":
        (folder / "a.bin").write_bytes(audio)
        (folder / "b.bin").write_bytes(data)
        cue = ('FILE "a.bin" BINARY\n  TRACK 01 AUDIO\n    INDEX 01 00:00:00\n'
               'FILE "b.bin" BINARY\n  TRACK 02 MODE1/2352\n    INDEX 00 00:00:00\n    INDEX 01 00:03:00\n')
    else:
        (folder / "s.bin").write_bytes(audio + data)
        cue = 'FILE "s.bin" BINARY\n  TRACK 01 AUDIO\n    INDEX 01 00:00:00\n  TRACK 02 MODE1/2352\n'
        if gap:
            cue += "    INDEX 00 %s\n" % msf(AUDIO_N)
        cue += "    INDEX 01 %s\n" % msf(AUDIO_N + gap)
    (folder / "layout.cue").write_text(cue, encoding="ascii")
    return folder / "layout.cue"


class CueAnalysisConventionTests(unittest.TestCase):
    """The spec says it follows tools/cue_analysis.py; check the S5 layouts against that tool (one report per variant)."""

    def analyze(self, variant):
        sys.path.insert(0, str(ROOT))
        from tools import cue_analysis
        with tempfile.TemporaryDirectory() as tmp:
            report = cue_analysis.analyze(write_layout(Path(tmp), variant))
        self.assertEqual(report["findings"], [], variant)
        self.assertTrue(report["structurally_valid"], variant)
        return report["tracks"][1]

    def test_v1_index01_is_lba_1408(self):
        track = self.analyze("V1")
        self.assertEqual((track["pregap_start_lba"], track["start_lba"], track["pregap_frames"]), (1408, 1408, 0))

    def test_v2_index00_1408_index01_1633_pregap_225(self):
        track = self.analyze("V2")
        self.assertEqual((track["pregap_start_lba"], track["start_lba"], track["pregap_frames"]), (1408, 1633, 225))

    def test_v2m_additional_condition_index00_1408_index01_1633_pregap_225(self):
        # Separate condition (V2M); a failure here does not change the V1/V2 results above.
        track = self.analyze("V2M")
        self.assertEqual((track["pregap_start_lba"], track["start_lba"], track["pregap_frames"]), (1408, 1633, 225))
        self.assertEqual(track["index01_frame"], 225)   # FILE-relative, not absolute


class SpecTextTests(unittest.TestCase):
    def setUp(self):
        self.text = SPEC.read_text(encoding="utf-8")

    def test_spec_states_225_and_not_the_stale_150(self):
        self.assertIn("225", self.text)
        self.assertIn("00:03:00", self.text)
        self.assertNotIn("プリギャップ 150", self.text)
        self.assertNotIn("INDEX 01 = 150", self.text)
        self.assertNotIn("INDEX 01 が 150", self.text)

    def table(self, header_prefix):
        block = next(b for b in markdown_table_blocks(self.text) if b[0].startswith(header_prefix))
        return {cells(row)[0]: cells(row) for row in block[2:]}

    def test_single_file_is_the_baseline_and_multi_file_is_separate(self):
        # Owner policy: V1/V2 are single-FILE (M4/M5 baseline); multi-FILE is V2M, reported separately.
        rows = self.table("| 変種 |")
        self.assertEqual(set(rows), {"V1（対照）", "V2（ゲームと同形）", "V2M（追加条件）"})
        self.assertTrue(rows["V1（対照）"][1].startswith("単一 FILE"))
        self.assertTrue(rows["V2（ゲームと同形）"][1].startswith("単一 FILE"))
        self.assertTrue(rows["V2M（追加条件）"][1].startswith("多 FILE"))
        self.assertNotIn("同一 FILE の変種は作らない", self.text)
        baseline = [line for line in self.text.splitlines() if line.startswith("基準（M4/M5）は")]
        self.assertEqual(baseline, ["基準（M4/M5）は単一 FILE の V1・V2 とする。"
                                    "多 FILE の V2M は追加条件であり、その結果で V1・V2 の合否を置き換えない。"])
        # Every sentence that names the M4/M5 baseline lists V1・V2 and never V2M.
        mentions = [m for m in re.findall(r"基準（M4/M5）[^。]*", self.text)]
        self.assertGreaterEqual(len(mentions), 2)
        for sentence in mentions:
            self.assertIn("V1・V2", sentence)
            self.assertNotIn("V2M", sentence)
        rule_b = [line for line in self.text.splitlines() if line.startswith("- 規則 B")]
        self.assertEqual(len(rule_b), 1)
        self.assertIn("仮想 PREGAP（CUE の `PREGAP`）がない場合に限り FILE 先頭が LBA 0", rule_b[0])

    def test_pass_wording_appears_only_as_a_negation(self):
        # V2M must never be reported as passing anywhere (tables, rules, body text).
        text = self.text.replace("合格扱いにしない", "").replace("合格にしない", "")
        self.assertNotIn("合格", text)
        self.assertNotIn("PASS", text.upper())

    def test_v2_row_puts_index01_225_frames_after_index00(self):
        v2 = self.table("| 変種 |")["V2（ゲームと同形）"][1]
        n00 = re.search(r"`INDEX 00` = フレーム N(?![ +\d])", v2)
        n01 = re.findall(r"`INDEX 01` = フレーム N \+ (\d+)", v2)
        self.assertIsNotNone(n00, v2)
        self.assertEqual(n01, [str(msf_frames("00:03:00"))])
        self.assertEqual(re.findall(r"プリギャップ (\d+) フレーム", v2), ["225"])
        v1 = self.table("| 変種 |")["V1（対照）"][1]
        self.assertRegex(v1, r"`INDEX 01` = フレーム N（プリギャップなし）")

    def test_v2m_is_never_reported_as_passing(self):
        expected = self.table("| ケース | 期待 |")
        v2m_rows = [self.table("| 変種 |")["V2M（追加条件）"]] + [r for k, r in expected.items() if "V2M" in k]
        self.assertEqual(len(v2m_rows), 2)
        for row in v2m_rows:
            text = " ".join(row).replace("合格扱いにしない", "").replace("合格にしない", "")
            self.assertNotIn("合格", text)
            self.assertNotIn("PASS", text.upper())
            self.assertIn("別に報告", " ".join(row))
        for key in expected:   # V2M may not be folded into a baseline row such as "V1、V2 の正常ヘッダ"
            if not key.startswith("V2M"):
                self.assertNotIn("V2M", key)
        self.assertIn("V1、V2 の正常ヘッダ", expected)

    def test_layout_rows_keep_their_confidence_labels(self):
        facts = self.table("| 項目 | CUE から読んだ内容 | 確度 |")
        self.assertTrue(facts["トラック 02 の INDEX 01 の絶対 LBA"][2].startswith("LOCAL"))
        self.assertIn("1,633", facts["トラック 02 の INDEX 01 の絶対 LBA"][1])
        self.assertTrue(facts["トラック 02 の INDEX 00 の絶対 LBA"][2].startswith("ASSUMPTION"))
        self.assertIn("1,408", facts["トラック 02 の INDEX 00 の絶対 LBA"][1])
        self.assertTrue(facts["トラック 01 の範囲"][2].startswith("ASSUMPTION"))
        arith = self.table("| 項目 | 計算 | 結果 | 確度 |")
        self.assertTrue(arith["絶対 00:23:58 の LBA"][3].startswith("LOCAL"))
        # VERIFIED is Geargrafx-measured only (definition at the top): never on CUE, file-size or arithmetic rows.
        for row in list(facts.values()) + list(arith.values()):
            self.assertNotIn("VERIFIED", row[-1], row)
        self.assertEqual(facts["トラック 01 の BIN サイズ"][2], "LOCAL（ファイルサイズ）。セクタ数は 算術")
        self.assertEqual(facts["トラック 02 の BIN サイズ"][2], "LOCAL（ファイルサイズ）。セクタ数は 算術")
        self.assertEqual(arith["トラック 01 の BIN"][3], "LOCAL（ファイルサイズ）、算術")
        summary = self.table("| 項目 | 状態 |")
        self.assertEqual(summary["ゲームのレイアウト算術"][1],
                         "225 フレーム: 算術（CUE は LOCAL）／ BIN 1,408 セクタ: LOCAL（ファイルサイズ）／ "
                         "INDEX 01 の絶対 LBA 1,633: LOCAL（ヘッダ実測）／ "
                         "INDEX 00 の絶対 LBA 1,408: ASSUMPTION（TOC 照合まで事実ではない）")
        self.assertEqual(summary["ゲームのトラック構成（CUE）"][1], "LOCAL（メタデータ）")
        b6 = next(line for line in self.text.splitlines() if line.startswith("| B6 |"))
        self.assertIn("1,633 は LOCAL（ヘッダ実測）、INDEX 00 の 1,408 は ASSUMPTION", b6)
        self.assertIn("- トラック 02 の INDEX 00 = LBA 1,408 の TOC による照合（ASSUMPTION のまま）",
                      self.text.splitlines())

    def test_verified_appears_only_where_the_definition_allows(self):
        # VERIFIED = Geargrafx-measured DEV image only. Any other line carrying it (a relabelled LOCAL,
        # ASSUMPTION, 算術 or HYPOTHESIS value in any section) is a regression.
        allowed = ("参照 JSR ゲートは CLOSED のまま。", "- `VERIFIED`: ", "| C14 |", "| C15 |", "**合否の扱い**")
        for line in self.text.splitlines():
            if "VERIFIED" in line:
                self.assertTrue(line.startswith(allowed), line)
        for line in self.text.splitlines():
            if line.startswith("**合否の扱い**"):
                self.assertIn("VERIFIED は付けない", line)

    def test_scope_statement_matches_the_header_measurement(self):
        scope = next(line for line in self.text.splitlines() if line.startswith("1. ゲーム固有の事実"))
        self.assertIn("ヘッダ MSF", scope)
        self.assertIn("ユーザデータ", scope)
        self.assertNotIn("セクタ内容は読んでいない", self.text)

    def test_code_length_is_1_to_255(self):
        contract = self.table("| 項目 | v0 契約（合成メディア） |")
        self.assertIn("[5] 長さ（1〜255）", contract["ヘッダ"][1])
        self.assertIn("1〜255", contract["長さ"][1])
        self.assertIn("判定順", contract)
        self.assertTrue(contract["判定順"][1].startswith("magic → 長さ（1〜255）→ エントリ（< 長さ）→ チェックサム"))
        self.assertNotRegex(self.text, r"1\s*[〜~-]\s*2,?040")

    def test_spec_header_is_8_byte_pcex_matching_the_dev_loader(self):
        # The DEV loader source (not tools/) defines the header size; the spec must agree with it.
        loader = (ROOT / "src" / "cd_load_exec_probe.s").read_text(encoding="utf-8")
        self.assertRegex(loader, r"(?m)^HDR_BYTES\s*=\s*8\b")
        self.assertIn("8 バイト", self.text)
        self.assertIn("PCEX", self.text)
        # No 16-byte header anywhere (the old form has no source in the repo and was removed).
        self.assertNotRegex(self.text, r"(?<![\d,])16 バイト")   # not the 3,311,616 バイト BIN size
        self.assertNotIn("OSSBOOT1", self.text)

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


def cells(row):
    return [c.strip() for c in row.strip().strip("|").split("|")]


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
        # Exact order: the header checks follow load_check (magic, length, entry, checksum).
        self.assertEqual(names, ["NO_TRACK", "DATA_TIMEOUT", "PREMATURE_STATUS", "BAD_MAGIC",
                                 "BAD_LENGTH", "BAD_ENTRY_OFFSET", "BAD_CHECKSUM", "BUDGET", "RETRY_EXHAUSTED"])
        codes = {row.split("|")[1].strip().strip("`"): row.split("|")[3].strip() for row in failure[2:]}
        self.assertEqual([codes[n] for n in ("BAD_MAGIC", "BAD_LENGTH", "BAD_ENTRY_OFFSET", "BAD_CHECKSUM")],
                         ["20", "22", "21", "23"])


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
