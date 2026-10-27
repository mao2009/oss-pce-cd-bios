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
        facts = self.table("| 項目 | 読んだ内容（CUE、ファイルサイズ、ヘッダ MSF） | 確度 |")
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
        # Every scope statement (opening list, S1 heading, target line) must state the same read range:
        # CUE, file sizes, and only the 3 MSF bytes of track 02 frames 0-299; no user data.
        lines = self.text.splitlines()
        opening = [l for l in lines if l.startswith("1. ゲーム固有の事実")]
        heading = [l for l in lines if l.startswith("## 1. ")]
        target = [l for l in lines if l.startswith("対象: ")]
        self.assertEqual(opening, ["1. ゲーム固有の事実（CUE のメタデータ、BIN のファイルサイズ、トラック 02 の BIN の"
                                   "一部セクタのヘッダ MSF のみ。ユーザデータ（各フレームの 2048 バイトの本体）は読んでいない）"])
        self.assertEqual(heading, ["## 1. ゲーム固有の事実（CUE のメタデータ、ファイルサイズ、セクタヘッダ MSF）"])
        self.assertEqual(len(target), 1)
        self.assertTrue(target[0].endswith(
            "読んだのは CUE の本文、各 BIN のファイルサイズ、トラック 02 の BIN のフレーム 0〜299 のセクタヘッダ"
            "（各フレームの同期・ヘッダ部のうち MSF の 3 バイト）だけで、ユーザデータは読んでいない。"), target[0])
        self.assertNotIn("セクタ内容は読んでいない", self.text)
        self.assertNotIn("ゼロ埋め", self.text)   # would claim more than the MSF bytes that were read
        # Measurement rows may not cite frames outside the stated 0-299 range.
        for frames in re.findall(r"フレーム (\d+)〜(\d+)", self.text.split("## 2.")[0]):
            self.assertLessEqual(int(frames[1]), 299, frames)

    def test_header_range_agrees_with_the_failure_table_and_expected_results(self):
        contract = self.table("| 項目 | v0 契約（合成メディア） |")
        self.assertEqual(contract["ヘッダ"][1],
                         "8 バイト（開発用ローダ `src/cd_load_exec_probe.s` の `HDR_BYTES = 8`、生成器 "
                         "`tools/build_cdload_exec.py` の `HDR_BYTES = 8`）。[0:4] magic `PCEX`、[4] エントリオフセット、"
                         "[5] 長さ（1〜255）、[6] チェックサム（コード部の和の下位 8 ビット）、[7] 予約（0）。"
                         "コードは [8:] から長さ分")
        self.assertEqual(contract["判定順"][1],
                         "magic → 長さ（1〜255）→ エントリ（< 長さ）→ チェックサム。最初の失敗で停止し、以降は見ない")
        low, high = re.search(r"\[5\] 長さ（(\d+)〜(\d+)）", contract["ヘッダ"][1]).groups()
        failure = {cells(r)[0].strip("`"): cells(r) for r in next(
            b for b in markdown_table_blocks(self.text) if b[0].startswith("| 状態 | 条件"))[2:]}
        self.assertEqual(failure["BAD_LENGTH"][1], "長さ 0（1 バイトなので 255 超はない）")
        self.assertEqual((low, high), ("1", "255"))
        self.assertIn(high, failure["BAD_LENGTH"][1])
        self.assertEqual(failure["BAD_ENTRY_OFFSET"][1], "エントリ ≥ 長さ")
        self.assertEqual(failure["BAD_CHECKSUM"][1], "チェックサム不一致")
        self.assertEqual(failure["BAD_MAGIC"][1], "magic 不一致（LBA 0 を読んでも同じ）")
        self.assertIn("上の表は、判定順（magic → 長さ → エントリ → チェックサム）に並べた行の後に、"
                      "`BUDGET` と `RETRY_EXHAUSTED` を続けている。", self.text.splitlines())
        expected = {k: v[1] for k, v in self.table("| ケース | 期待 |").items()}
        self.assertEqual(expected["データトラックのヘッダ magic を壊す"], "`BAD_MAGIC`")
        self.assertEqual(expected["チェックサムを壊す"], "`BAD_CHECKSUM`")
        self.assertEqual(expected["エントリ = 長さ"], "`BAD_ENTRY_OFFSET`")
        self.assertEqual(expected["長さ 0"], "`BAD_LENGTH`")
        self.assertEqual(expected["データトラックを除去（オーディオのみ）"], "`NO_TRACK`")
        self.assertEqual(expected["DATA-IN 途中で中断"], "`DATA_TIMEOUT`")
        self.assertEqual(expected["V2 で絶対 LBA と INDEX 01 相対を取り違える実装"],
                         "別の読出し LBA になり、判定器が `BAD_MAGIC` で拒否")
        self.assertTrue(expected["読出し LBA を 0（トラック 01 AUDIO の先頭）にする実装"].startswith(
            "ホスト判定器では `BAD_MAGIC`"))

    def test_section5_states_the_no_virtual_pregap_condition(self):
        self.assertIn("INDEX の値は FILE 先頭からのフレーム数である。単一 FILE では、トラック 01 の前に仮想 PREGAP"
                      "（CUE の `PREGAP`、BIN に格納されない）がない場合に限り FILE 先頭が LBA 0 になり、"
                      "INDEX 値がそのまま絶対 LBA になる（`tools/cue_analysis.py` の `analyze` の説明。"
                      "`PREGAP 00:02:00` があると FILE 先頭は LBA 150）。V1・V2 は仮想 PREGAP を使わない。"
                      "多 FILE では、絶対 LBA = 先行 FILE のフレーム数の合計 + INDEX 値（連続配置の仮定。"
                      "`tools/cue_analysis.py` と同じ規約）。", self.text.splitlines())

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


# The LOCAL game facts the spec records (S1). Derivations start from these; GameCueTests checks them against the
# real CUE when OSS_PCE_GAME_CUE is set.
GAME_CUE = {
    "tracks": 16,
    "t01": ("00:00:00", "00:00:00"),          # track 01 has only INDEX 01; [1] is INDEX 01
    "t02": ("00:00:00", "00:03:00"),          # (INDEX 00, INDEX 01)
    "t03": ("00:00:00", "00:02:00"),
    "t01_bin": 3311616,
    "t02_bin": 18630192,
    "t02_index01_msf": "00:23:58",            # header MSF of the INDEX 01 sector (LOCAL)
}


def _num(text):
    return int(text.replace(",", ""))


def _eval_formula(text):
    """Evaluate a spec formula such as '(23 × 75 + 58) − 150' or '3,311,616 ÷ 2,352' (integers only)."""
    expr = text.replace(",", "").replace("×", "*").replace("−", "-").replace("÷", "/")
    if not re.fullmatch(r"[0-9 ()+\-*/]+", expr, re.ASCII):
        return None
    value = eval(expr, {"__builtins__": {}})   # digits and operators only (checked above)
    return int(value) if value == int(value) else value


class NumericClaimsTests(unittest.TestCase):
    """Every number in the layout claims is re-derived here, row by row, from first principles.

    Inputs: the CUE INDEX values (00:00:00, 00:03:00), the measured header MSF 00:23:58 (LOCAL), the BIN sizes
    (LOCAL) and 2352 bytes per raw frame. Everything else (225, 1,633, 1,408, 529,200 ...) must follow from them.
    """

    PREGAP = msf_frames(GAME_CUE["t02"][1]) - msf_frames(GAME_CUE["t02"][0])    # 225
    INDEX01_LBA = msf_frames(GAME_CUE["t02_index01_msf"]) - LEAD_IN_FRAMES       # 1633
    INDEX00_LBA = INDEX01_LBA - PREGAP                             # 1408

    def setUp(self):
        self.text = SPEC.read_text(encoding="utf-8")
        self.lines = self.text.splitlines()
        blocks = markdown_table_blocks(self.text)
        self.facts = {cells(r)[0]: cells(r) for r in next(b for b in blocks if b[0].startswith("| 項目 | 読んだ内容"))[2:]}
        self.arith = {cells(r)[0]: cells(r) for r in next(b for b in blocks if b[0].startswith("| 項目 | 計算 |"))[2:]}

    def test_derived_constants(self):
        self.assertEqual((self.PREGAP, self.INDEX01_LBA, self.INDEX00_LBA), (225, 1633, 1408))

    def test_every_inline_difference_or_sum_is_correct(self):
        # Class check: any "A（= B − C" or "A（= B + C" anywhere in the spec must hold.
        claims = re.findall(r"([\d,]+)（= ([\d,]+) ([+−]) ([\d,]+)", self.text)
        self.assertGreaterEqual(len(claims), 2)
        for a, b, op, c in claims:
            self.assertEqual(_num(a), _num(b) - _num(c) if op == "−" else _num(b) + _num(c), (a, b, op, c))

    def test_every_frames_to_bytes_and_seconds_claim_is_correct(self):
        products = re.findall(r"([\d,]+) バイト = 2352 × ([\d,]+) セクタ", self.text)
        self.assertEqual(len(products), 2)
        for size, sectors in products:
            self.assertEqual(_num(size), 2352 * _num(sectors))
        byte_claims = re.findall(r"(\d+) フレーム = ([\d,]+) バイト", self.text)
        self.assertGreaterEqual(len(byte_claims), 1)
        for frames, size in byte_claims:
            self.assertEqual((int(frames), _num(size)), (self.PREGAP, self.PREGAP * SECTOR_BYTES))
        seconds = re.findall(r"([\d,]+) セクタ = (\d+) 秒 × 75 フレーム", self.text)
        self.assertEqual(len(seconds), 1)
        for sectors, secs in seconds:
            self.assertEqual(_num(sectors), int(secs) * FPS)
            self.assertEqual(_num(sectors), self.PREGAP)

    def test_every_pregap_distance_is_225(self):
        # Class check: "INDEX 00 の N フレーム後" (S1 note, B6) and rule A's frame positions.
        after = re.findall(r"INDEX 00 の (\d+) フレーム後", self.text)
        self.assertGreaterEqual(len(after), 2)
        self.assertEqual(set(after), {str(self.PREGAP)})
        rule_a = re.findall(r"INDEX 00 は FILE の (\d+) フレーム目、INDEX 01 は (\d+) フレーム目", self.text)
        self.assertEqual(rule_a, [("0", str(self.PREGAP))])
        index_row = self.facts["トラック 02 の INDEX"][1]
        i00, i01 = re.findall(r"`INDEX 0([01]) (\d\d:\d\d:\d\d)`", index_row)
        gap = re.findall(r"プリギャップ (\d+) セクタ", index_row)
        self.assertEqual(gap, [str(msf_frames(i01[1]) - msf_frames(i00[1]))])

    def test_arithmetic_table_formulas_and_results(self):
        want = {"00:03:00 のフレーム数": self.PREGAP, "00:02:00 の絶対 LBA": 0,
                "絶対 00:23:58 の LBA": self.INDEX01_LBA, "トラック 01 の BIN": self.INDEX00_LBA, "トラック 02 の BIN": 7921}
        for key, value in want.items():
            calc, result = self.arith[key][1], self.arith[key][2]
            with self.subTest(row=key):
                self.assertEqual(_eval_formula(calc), value, calc)
                self.assertEqual(_num(re.match(r"[\d,]+", result).group()), value, result)
        old = next(k for k in self.arith if k.startswith("旧記述の"))
        self.assertEqual(_num(re.search(r"([\d,]+)", old).group(1)), _eval_formula(self.arith[old][1]))
        self.assertEqual(_eval_formula(self.arith[old][1]), self.INDEX00_LBA + LEAD_IN_FRAMES)   # the 150 confusion
        track3 = self.arith["トラック 03 の INDEX 00→01"][1]
        m = re.fullmatch(r"(\d\d:\d\d:\d\d) = (\d+) フレーム", track3)
        self.assertEqual(msf_frames(m.group(1)), int(m.group(2)))

    def test_track02_lba_rows(self):
        i01 = self.facts["トラック 02 の INDEX 01 の絶対 LBA"][1]
        m = re.search(r"ヘッダ MSF は (\d\d:\d\d:\d\d)（絶対）。LBA = 絶対フレーム − 150 で ([\d,]+)", i01)
        self.assertEqual(msf_frames(m.group(1)) - LEAD_IN_FRAMES, _num(m.group(2)))
        self.assertEqual(_num(m.group(2)), self.INDEX01_LBA)
        i00 = self.facts["トラック 02 の INDEX 00 の絶対 LBA"][1]
        head = re.match(r"([\d,]+)（= ([\d,]+) − ([\d,]+)、算術）", i00)
        self.assertEqual(tuple(_num(g) for g in head.groups()), (self.INDEX00_LBA, self.INDEX01_LBA, self.PREGAP))
        self.assertEqual([_num(x) for x in re.findall(r"LBA ([\d,]+) \+ k", i00)], [self.INDEX00_LBA])
        # The zero-MSF range and the matched range partition the stated read range 0-299 (S1 target line).
        zero = re.findall(r"フレーム 0〜(\d+) はヘッダ MSF", i00)
        matched = re.findall(r"フレーム (\d+)〜(\d+) のヘッダ MSF", i00)
        self.assertEqual(len(zero), 1)
        self.assertEqual(len(matched), 1)
        self.assertEqual(int(matched[0][0]), int(zero[0]) + 1)
        self.assertEqual(matched[0][1], "299")
        self.assertEqual(zero[0], "74")
        self.assertEqual(re.findall(r"例外 (\d+)", i00), ["0"])

    def test_cue_rows_equal_the_derivation_inputs(self):
        # The spec's CUE facts must equal the constants the derivation uses (GAME_CUE), not only be self-consistent.
        g = GAME_CUE
        self.assertEqual(self.facts["トラック数"][1], str(g["tracks"]))
        self.assertEqual(self.facts["トラック 01"][1], "`AUDIO`、`INDEX 01 %s`" % g["t01"][1])
        index_row = self.facts["トラック 02 の INDEX"][1]
        self.assertTrue(index_row.startswith("`INDEX 00 %s`、`INDEX 01 %s`（" % g["t02"]), index_row)
        self.assertEqual(self.facts["トラック 03"][1], "`AUDIO`、`INDEX 00 %s`、`INDEX 01 %s`" % g["t03"])
        # Direction of the 150/225 sentence: the rejected value is 150, the stated pregap is 225.
        self.assertEqual(re.findall(r"(\d+) ではない", index_row), [str(msf_frames(g["t03"][1]))])
        self.assertNotEqual(msf_frames(g["t03"][1]), self.PREGAP)
        # Track 03: CUE row, S1.1 row and 150 frames agree.
        t03 = self.arith["トラック 03 の INDEX 00→01"][1]
        self.assertEqual(t03, "%s = %d フレーム" % (g["t03"][1], msf_frames(g["t03"][1]) - msf_frames(g["t03"][0])))
        self.assertEqual(msf_frames(g["t03"][1]), 150)
        # Track 01 BIN: bytes = 2352 x 1,408 = INDEX 00 LBA (contiguity), in S1, S1.1 and the range row.
        t01_sectors = g["t01_bin"] // SECTOR_BYTES
        self.assertEqual(g["t01_bin"] % SECTOR_BYTES, 0)
        self.assertEqual(t01_sectors, self.INDEX00_LBA)
        self.assertEqual(self.facts["トラック 01 の BIN サイズ"][1], "{:,} バイト = 2352 × {:,} セクタ".format(g["t01_bin"], t01_sectors))
        self.assertEqual(self.arith["トラック 01 の BIN"][1], "{:,} ÷ 2,352".format(g["t01_bin"]))
        self.assertTrue(self.facts["トラック 01 の範囲"][1].startswith("{:,} セクタ。".format(t01_sectors)))
        t02_sectors = g["t02_bin"] // SECTOR_BYTES
        self.assertEqual(self.facts["トラック 02 の BIN サイズ"][1], "{:,} バイト = 2352 × {:,} セクタ（割り切れる）".format(g["t02_bin"], t02_sectors))
        self.assertEqual(self.arith["トラック 02 の BIN"][1], "{:,} ÷ 2,352".format(g["t02_bin"]))
        self.assertIn("ヘッダ MSF は %s（絶対）" % g["t02_index01_msf"], self.facts["トラック 02 の INDEX 01 の絶対 LBA"][1])

    def test_msf_is_lba_plus_150_everywhere(self):
        relation = re.findall(r"LBA \+ (\d+)", self.text)
        self.assertGreaterEqual(len(relation), 2)   # S1 note and S5 sector header rule
        self.assertEqual(set(relation), {str(LEAD_IN_FRAMES)})

    def test_v2m_inline_index_values_give_225_frames(self):
        v2m = next(cells(r) for b in markdown_table_blocks(self.text) if b[0].startswith("| 変種 |")
                   for r in b[2:] if cells(r)[0] == "V2M（追加条件）")[1]
        values = dict(re.findall(r"`INDEX 0([01]) (\d\d:\d\d:\d\d)`", v2m))
        self.assertEqual(values, {"0": GAME_CUE["t02"][0], "1": GAME_CUE["t02"][1]})
        self.assertEqual(msf_frames(values["1"]) - msf_frames(values["0"]), self.PREGAP)
        self.assertEqual(re.findall(r"FILE 先頭から (\d+) フレーム", v2m), [str(self.PREGAP)])

    def test_read_unit_is_the_dev_loader_sector_size(self):
        loader = (ROOT / "src" / "cd_load_exec_probe.s").read_text(encoding="utf-8")
        size = int(re.search(r"(?m)^SECTOR_BYTES\s*=\s*(\d+)", loader).group(1))
        self.assertEqual(size, 2048)
        contract = {cells(r)[0]: cells(r) for b in markdown_table_blocks(self.text)
                    if b[0].startswith("| 項目 | v0 契約") for r in b[2:]}
        self.assertEqual(re.findall(r"1 セクタ（(\d+) バイト）を READ\(6\) で読む", contract["読出し単位・方法"][1]), [str(size)])
        self.assertIn("2040（= %d − 8）" % size, contract["長さ"][1])

    def test_every_confidence_label_used_is_defined(self):
        defined = {m.group(1): m.group(2) for m in (re.match(r"- `([^`]+)`: (.+)", l) for l in self.lines) if m}
        for label in ("VERIFIED", "HYPOTHESIS", "LOCAL", "ASSUMPTION", "算術", "UNKNOWN"):
            used = sum(label in cell for b in markdown_table_blocks(self.text) for r in b[2:] for cell in cells(r))
            with self.subTest(label=label):
                self.assertGreater(used, 0)
                self.assertIn(label, defined)
                self.assertGreater(len(defined[label]), 10)


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

    def test_game_cue_equals_the_recorded_facts(self):
        # GAME_CUE (the derivation inputs) must match the real CUE and BIN sizes.
        tracks = [t for f in self.files for t in f["tracks"]]
        self.assertEqual(len(tracks), GAME_CUE["tracks"])
        for no, key in ((2, "t02"), (3, "t03")):
            self.assertEqual(self.track(no)["index"], {0: msf_frames(GAME_CUE[key][0]), 1: msf_frames(GAME_CUE[key][1])})
        self.assertEqual(self.track(1)["index"], {1: msf_frames(GAME_CUE["t01"][1])})
        self.assertEqual(self.sectors[self.files[0]["file"]] * SECTOR_BYTES, GAME_CUE["t01_bin"])
        self.assertEqual(self.sectors[self.files[1]["file"]] * SECTOR_BYTES, GAME_CUE["t02_bin"])

    def test_index01_absolute_lba_is_1633_under_contiguity(self):
        lbas = contiguous_index_lbas(self.files, self.sectors)
        self.assertEqual(lbas[(2, 1)], 1633)  # contiguity is ASSUMPTION; see spec 1.1 and section 8


if __name__ == "__main__":
    unittest.main(verbosity=2)
