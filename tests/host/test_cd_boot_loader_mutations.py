"""Mutation set for docs/cd-boot-loader-spec.md (#554): every mutation must make the spec tests fail.

Each mutation is applied to a copy of the spec in a temporary directory (the repo file is never written), and the
spec-reading test classes of test_cd_boot_loader_layout.py are run against that copy in-process.
IDs: O.., R01..R32 and N01..N32 are the reviewers' IDs (R06/R10/R11/R15/R16/R21/R25/R26/R28/R31/R32 were
written here from the round-6 report; the other R and N entries copy r6/mut6.py); W01..W49 are this PR's own.
"""
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("cd_boot_layout_under_mutation", HERE / "test_cd_boot_loader_layout.py")
layout = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(layout)
SPEC_CLASSES = (layout.SpecTextTests, layout.SpecTableTests, layout.NumericClaimsTests)


def _swap_rows(text, first, second):
    lines = text.splitlines(True)
    a = next(i for i, line in enumerate(lines) if line.startswith(first))
    b = next(i for i, line in enumerate(lines) if line.startswith(second))
    lines[a], lines[b] = lines[b], lines[a]
    return "".join(lines)


# (id, old, new). old must occur in the spec (first occurrence is replaced); a callable old is a whole-text edit.
MUTATIONS = [
    # Reviewer IDs (round 5): numeric claims.
    ("O03", "| 1,633 | LOCAL（ヘッダ実測と一致） |", "| 1,638 | LOCAL（ヘッダ実測と一致） |"),
    ("O04", "ゲームは INDEX 01 が INDEX 00 の 225 フレーム後", "ゲームは INDEX 01 が INDEX 00 の 150 フレーム後"),
    ("O05", "（プリギャップ 225 セクタ = 3 秒", "（プリギャップ 226 セクタ = 3 秒"),
    ("O06", "FILE 先頭のフレーム 0〜74 は", "FILE 先頭のフレーム 0〜99 は"),
    ("O07", "（LOCAL、例外 0）", "（LOCAL、例外 3）"),
    ("O08", "LBA 1,408 + k", "LBA 1,409 + k"),
    ("O09", "- `ASSUMPTION`: リポジトリ内に出典がなく、計算や仮定で置いた値。事実として扱わず、確認方法を併記する。\n", ""),
    ("O10", "1,408（= 1,633 − 225、算術）", "1,408（= 1,633 − 150、算術）"),
    ("O11", "| (0 × 60 + 3) × 75 + 0 | 225 | 算術 |", "| (0 × 60 + 3) × 75 + 0 | 226 | 算術 |"),
    ("O12", "225 フレーム = 529,200 バイト", "225 フレーム = 352,800 バイト"),
    ("O13", "INDEX 01 は 225 フレーム目", "INDEX 01 は 150 フレーム目"),
    # Reviewer IDs (round 6).
    ("R06", "| トラック 01 の範囲 | 1,408 セクタ。", "| トラック 01 の範囲 | 1,409 セクタ。"),
    ("R10", "絶対 MSF は LBA + 150 で表す", "絶対 MSF は LBA + 225 で表す"),
    ("R11", "絶対アドレス（LBA + 150）", "絶対アドレス（LBA + 225）"),
    ("R15", "`INDEX 00 00:00:00`、`INDEX 01 00:03:00`（プリギャップ", "`INDEX 00 00:00:01`、`INDEX 01 00:03:01`（プリギャップ"),
    ("R16", "| トラック 03 | `AUDIO`、`INDEX 00 00:00:00`、`INDEX 01 00:02:00` |",
     "| トラック 03 | `AUDIO`、`INDEX 00 00:00:00`、`INDEX 01 00:02:01` |"),
    ("R21", "`INDEX 01 00:03:00`（FILE 先頭から", "`INDEX 01 00:02:00`（FILE 先頭から"),
    ("R25", "1 セクタ（2048 バイト）を READ(6) で読む", "1 セクタ（2352 バイト）を READ(6) で読む"),
    ("R26", "| トラック数 | 16 |", "| トラック数 | 17 |"),
    ("R28", "| 00:02:00 = 150 フレーム |", "| 00:02:01 = 151 フレーム |"),
    ("R31", "150 ではない", "225 ではない"),
    ("R32", "3,311,616 バイト = 2352 × 1,408 セクタ", "3,313,968 バイト = 2352 × 1,409 セクタ"),
    # This PR's own set: layout, V2M policy, confidence labels.
    ("W01", "`INDEX 01` = フレーム N + 225", "`INDEX 01` = フレーム N + 150"),
    ("W02", "多 FILE の解釈。V1・V2 とは別に報告し、失敗・未対応を合格扱いにしない", "多 FILE の解釈。合格"),
    ("W03", "| V1、V2 の正常ヘッダ |", "| V1、V2、V2M の正常ヘッダ |"),
    ("W04", "基準（M4/M5）は単一 FILE の V1・V2 とする。", "基準（M4/M5）は単一 FILE の V1・V2・V2M とする。"),
    ("W05", "| LOCAL（ヘッダ実測。リポジトリに出典はなく", "| VERIFIED（ヘッダ実測。リポジトリに出典はなく"),
    ("W06", "| ASSUMPTION（INDEX 00 の位置は", "| VERIFIED（INDEX 00 の位置は"),
    ("W07", "コード長は 1〜255", "コード長は 1〜2040"),
    ("W08", "| 1,633 | LOCAL（ヘッダ実測と一致） |", "| 1,633 | VERIFIED（ヘッダ実測と一致） |"),
    ("W09", "V2M の結果は V1・V2 と別に報告し、失敗・未対応を合格扱いにしない。", "V2M の結果は V1・V2 と同じく合格とする。"),
    ("W10", "| ゲームのトラック構成（CUE） | LOCAL（メタデータ） |", "| ゲームのトラック構成（CUE） | VERIFIED（メタデータ） |"),
    ("W11", "225 フレーム: 算術（CUE は LOCAL）", "225 フレーム: VERIFIED（CUE）"),
    ("W12", "1,633 は LOCAL（ヘッダ実測）、INDEX 00", "1,633 は VERIFIED（ヘッダ実測）、INDEX 00"),
    ("W13", lambda t: _swap_rows(t, "| `BAD_LENGTH`", "| `BAD_ENTRY_OFFSET`"), None),
    ("W14", "BIN 1,408 セクタ: LOCAL（ファイルサイズ）", "BIN 1,408 セクタ: VERIFIED（ファイルサイズ）"),
    ("W15", "照合（ASSUMPTION のまま）", "照合（VERIFIED）"),
    ("W16", "INDEX 00 の絶対 LBA 1,408: ASSUMPTION（TOC", "INDEX 00 の絶対 LBA 1,408: LOCAL（TOC"),
    ("W17", "本書の基準（M4/M5）である合成メディア V1・V2 は", "本書の基準（M4/M5）である合成メディア V1・V2・V2M は"),
    ("W18", "- トラック 02 の INDEX 00 = LBA 1,408 の TOC による照合（ASSUMPTION のまま）\n", ""),
    ("W19", "| IPL は 2 セクタ | HYPOTHESIS |", "| IPL は 2 セクタ | VERIFIED |"),
    ("W20", "| トラック数 | 16 | LOCAL（CUE の記述どおり） |", "| トラック数 | 16 | VERIFIED（CUE の記述どおり） |"),
    ("W21", "その結果で V1・V2 の合否を置き換えない。", "その結果で V1・V2 の合否を置き換える。"),
    ("W22", "| 225 | 算術 |", "| 225 | VERIFIED（算術） |"),
    ("W23", "トラック 01 の前に仮想 PREGAP（CUE の `PREGAP`）がない場合に限り FILE 先頭が LBA 0 になり、INDEX 値がそのまま絶対 LBA になる（§5）",
     "FILE 先頭が LBA 0 なので INDEX 値がそのまま絶対 LBA になる"),
    ("W24", "| 二次資料 STUM（版別表）のみ | HYPOTHESIS |", "| 二次資料 STUM（版別表）のみ | VERIFIED |"),
    ("W25", "ユーザデータ（各フレームの 2048 バイトの本体）は読んでいない）", "セクタ内容は読んでいない）"),
    ("W26", "| エントリ ≥ 長さ | 21 |", "| エントリ ≥ 長さ | 22 |"),
    ("W27", "| LOCAL（ファイルサイズ）。セクタ数は 算術 |", "| VERIFIED（ファイルサイズの算術） |"),
    # Failure table, scope statements, expected results, header fields, judgment order, PREGAP condition.
    ("W28", "| 長さ 0（1 バイトなので 255 超はない） |", "| 長さ 0、または >2040 |"),
    ("W29", "## 1. ゲーム固有の事実（CUE のメタデータ、ファイルサイズ、セクタヘッダ MSF）", "## 1. ゲーム固有の事実（CUE のメタデータのみ）"),
    ("W30", "）だけで、ユーザデータは読んでいない。", "）だけを読んだ。"),
    ("W31", "フレーム 0〜299 のセクタヘッダ（各フレームの同期・ヘッダ部のうち MSF の 3 バイト）", "フレーム 0〜7920 のセクタ全体"),
    ("W32", "。ユーザデータ（各フレームの 2048 バイトの本体）は読んでいない）", "）"),
    ("W33", "| 長さ 0 | `BAD_LENGTH` |", "| 長さ 0 | `BAD_ENTRY_OFFSET` |"),
    ("W34", "別の読出し LBA になり、判定器が `BAD_MAGIC` で拒否", "別の読出し LBA になり、判定器が `BAD_CHECKSUM` で拒否"),
    ("W35", "[6] チェックサム（コード部の和の下位 8 ビット）、[7] 予約（0）", "[6] 予約（0）、[7] チェックサム（コード部の和の下位 8 ビット）"),
    ("W36", "上の表は、判定順（magic → 長さ → エントリ → チェックサム）", "上の表は、判定順（magic → エントリ → 長さ → チェックサム）"),
    ("W37", "単一 FILE では、トラック 01 の前に仮想 PREGAP（CUE の `PREGAP`、BIN に格納されない）がない場合に限り FILE 先頭が LBA 0 になり、",
     "単一 FILE では FILE 先頭が LBA 0 なので、"),
    ("W38", "ヘッダ MSF の 3 バイトが 00 00 00 で位置を示さない", "ゼロ埋めでヘッダがない"),
    ("W39", "フレーム 75〜299 のヘッダ MSF", "フレーム 75〜7920 のヘッダ MSF"),
    ("W40", "| データトラックのヘッダ magic を壊す | `BAD_MAGIC` |", "| データトラックのヘッダ magic を壊す | `BAD_LENGTH` |"),
    ("W41", "magic → 長さ（1〜255）→ エントリ（< 長さ）→ チェックサム。最初の失敗", "magic → エントリ（< 長さ）→ 長さ（1〜255）→ チェックサム。最初の失敗"),
    # Further numeric variants.
    ("W42", "INDEX 00 は FILE の 0 フレーム目", "INDEX 00 は FILE の 1 フレーム目"),
    ("W43", "| 3,311,616 ÷ 2,352 | 1,408 セクタ |", "| 3,311,616 ÷ 2,352 | 1,409 セクタ |"),
    ("W44", "18,630,192 バイト = 2352 × 7,921 セクタ", "18,630,192 バイト = 2352 × 7,922 セクタ"),
    ("W45", "| 1,408 + 150 | 誤り", "| 1,408 + 225 | 誤り"),
    ("W46", "2040（= 2048 − 8）", "2040（= 2048 − 16）"),
    ("W47", "このゲームでは INDEX 01 が INDEX 00 の 225 フレーム後", "このゲームでは INDEX 01 が INDEX 00 の 150 フレーム後"),
    ("W48", "LBA = 絶対フレーム − 150 で 1,633", "LBA = 絶対フレーム − 150 で 1,634"),
    ("W49", "フレーム 75〜299 のヘッダ MSF", "フレーム 76〜299 のヘッダ MSF"),
    # Reviewer set (round 6/7, r6/mut6.py), IDs not already above. Same old/new strings as the reviewer's script.
    ("O06a", "フレーム 0〜299 のセクタヘッダ", "フレーム 0〜99 のセクタヘッダ"),
    ("O06b", "フレーム 75〜299 のヘッダ MSF", "フレーム 75〜99 のヘッダ MSF"),
    ("R01", "1,408（= 1,633 − 225", "１,４０９（= 1,633 − 225"),
    ("R02", "| 1,633 | LOCAL（ヘッダ実測と一致）", "| １,６３４ | LOCAL（ヘッダ実測と一致）"),
    ("R03", "(23 × 75 + 58) − 150", "(23 × 75 + 59) − 150"),
    ("R04", "INDEX 01 の絶対 LBA 1,633: LOCAL", "INDEX 01 の絶対 LBA 1,634: LOCAL"),
    ("R05", "BIN 1,408 セクタ: LOCAL", "BIN 1,409 セクタ: LOCAL"),
    ("R07", "2040（= 2048 − 8）", "2041（= 2048 − 8）"),
    ("R08", "2040（= 2048 − 8）", "2041（=2048−8）"),
    ("R09", "FILE 先頭は LBA 150）", "FILE 先頭は LBA 225）"),
    ("R12", "18,630,192 バイト = 2352 × 7,921", "18,630,192 バイト = 2048 × 7,921"),
    ("R13", "3 秒 × 75 フレーム", "3 秒 × 74 フレーム"),
    ("R14", "INDEX 01 が INDEX 00 の 225 フレーム後（FILE", "INDEX 01 が INDEX 00 より 150 フレーム後（FILE"),
    ("R17", "| 1,408 + 150 |", "| 1,408 + 225 |"),
    ("R18", "INDEX 01 の絶対 LBA 1,633 は LOCAL（ヘッダ実測）、INDEX 00", "INDEX 01 の絶対 LBA 1,634 は LOCAL（ヘッダ実測）、INDEX 00"),
    ("R19", "INDEX 00 = LBA 1,408 の TOC", "INDEX 00 = LBA 1,409 の TOC"),
    ("R20", "FILE 先頭が LBA 0 になり、INDEX 値がそのまま絶対 LBA になる（§5）", "FILE 先頭が LBA 150 になり、INDEX 値がそのまま絶対 LBA になる（§5）"),
    ("R22", "225 フレーム = 529,200 バイト", "150 フレーム = 352,800 バイト"),
    ("R23", "例外 0", "例外 **3**"),
    ("R24", "各フレームの 2048 バイトの本体", "各フレームの 2352 バイトの本体"),
    ("R27", "− 150 | 0 | 算術", "− 150 | **1** | 算術"),
    ("R29", "フレーム 0〜74 はヘッダ MSF の 3 バイトが 00 00 00 で位置を示さない。LOCAL、MSF 以外は読んでいない）。フレーム 75〜299",
     "フレーム 0〜75 はヘッダ MSF の 3 バイトが 00 00 00 で位置を示さない。LOCAL、MSF 以外は読んでいない）。フレーム 76〜299"),
    ("R30", "00:23:58（絶対）。LBA = 絶対フレーム − 150 で 1,633", "00:23:59（絶対）。LBA = 絶対フレーム − 150 で 1,634"),
    ("N01", "（基準 2 変種と追加条件 1 変種）", "（基準 3 変種と追加条件 1 変種）"),
    ("N02", "| 18,630,192 ÷ 2,352 | 7,921 セクタ |", "| 18,630,192 ÷ 2,352 | 7,920 セクタ |"),
    ("N03", "(0 × 60 + 2) × 75 + 0 − 150", "(0 × 60 + 2) × 75 + 1 − 150"),
    ("N04", "`INDEX 00 00:00:00` → `INDEX 01 00:03:00` の差", "`INDEX 00 00:00:00` → `INDEX 01 00:02:00` の差"),
    ("N05", "`PREGAP 00:02:00` があると", "`PREGAP 00:03:00` があると"),
    ("N06", "フレーム 0〜299 のセクタヘッダ", "フレーム 0〜300 のセクタヘッダ"),
    ("N07", "フレーム 75〜299 のヘッダ MSF", "フレーム 75〜300 のヘッダ MSF"),
    ("N08", "FILE 先頭から 225 フレーム = 529,200", "FILE 先頭から 150 フレーム = 529,200"),
    ("N09", "225 は INDEX 00 と INDEX 01 の差（プリギャップ長）であり、150 の換算と混同しない",
     "150 は INDEX 00 と INDEX 01 の差（プリギャップ長）であり、225 の換算と混同しない"),
    ("N10", "絶対 MSF は LBA + 150 で表す", "絶対 LBA は MSF + 150 で表す"),
    ("N11", "LBA = 絶対フレーム − 150", "MSF = 絶対フレーム − 150"),
    ("N12", "絶対 LBA = 先行 FILE のフレーム数の合計 + INDEX 値", "絶対 MSF = 先行 FILE のフレーム数の合計 + INDEX 値"),
    ("N13", "= 529,200 バイト", "= 530,000 バイト"),
    ("N14", "2352 × 1,408 セクタ | LOCAL（ファイルサイズ）。セクタ数は 算術", "2352 × 1,408 セクタ | VERIFIED（ファイルサイズ）。セクタ数は 算術"),
    ("N15", "| 1,408 セクタ | LOCAL（ファイルサイズ）、算術 |", "| 1,408 セクタ | VERIFIED（ファイルサイズ）、算術 |"),
    ("N16", "。225 は 算術 |", "。225 は LOCAL |"),
    ("N17", "| 0 | 算術（`docs/cd-read-diagnostic.md` の定義） |", "| 0 | VERIFIED（`docs/cd-read-diagnostic.md` の定義） |"),
    ("N18", "INDEX 00 の 1,408 は ASSUMPTION", "INDEX 00 の 1,409 は ASSUMPTION"),
    ("N19", "| ASSUMPTION（トラック 02 の絶対位置とは独立に", "| LOCAL（トラック 02 の絶対位置とは独立に"),
    ("N20", "225 セクタ = 3 秒", "225 セクタ = 2 秒"),
    ("N21", "リードイン 2 秒", "リードイン 3 秒"),
    ("N22", "225 フレームは BIN に格納されている", "150 フレームは BIN に格納されている"),
    ("N23", "プリギャップ 225 フレームを", "プリギャップ 150 フレームを"),
    ("N24", "旧記述の 1,558", "旧記述の 1,559"),
    ("N25", "| 00:03:00 のフレーム数 |", "| 00:02:00 のフレーム数 |"),
    ("N26", "| 絶対 00:23:58 の LBA |", "| 絶対 00:23:59 の LBA |"),
    ("N27", "`AUDIO`、`INDEX 01 00:00:00`", "`AUDIO`、`INDEX 01 00:00:02`"),
    ("N28", "LBA 0 が 2048 バイトを返す", "LBA 0 が 2352 バイトを返す"),
    ("N29", "実効上限は 255", "実効上限は 256"),
    ("N30", "モードバイトは 1 とする", "モードバイトは 2 とする"),
    ("N31", "ユーザデータ（各フレームの 2048 バイトの本体）は読んでいない）\n2.", "ユーザデータ（各フレームの 2352 バイトの本体）は読んでいない）\n2."),
    ("N32", "| 絶対 00:23:58 の LBA | (23 × 75 + 58) − 150 | 1,633 |", "| 絶対 00:23:59 の LBA | (23 × 75 + 59) − 150 | 1,634 |"),
]
EXPECTED_COUNT = 126   # 71 (round 6) + O06a/O06b + 21 reviewer R IDs + N01..N32


def _spec_tests_pass(spec_path):
    saved = layout.SPEC
    layout.SPEC = spec_path
    try:
        suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(c) for c in SPEC_CLASSES)
        return unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite).wasSuccessful()
    finally:
        layout.SPEC = saved


class SpecMutationTests(unittest.TestCase):
    def setUp(self):
        self.original = layout.SPEC.read_text(encoding="utf-8")
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "cd-boot-loader-spec.md"

    def tearDown(self):
        self.tmp.cleanup()

    def test_ids_are_unique(self):
        ids = [m[0] for m in MUTATIONS]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(ids), EXPECTED_COUNT)

    def test_unmutated_copy_passes(self):
        self.path.write_text(self.original, encoding="utf-8")
        self.assertTrue(_spec_tests_pass(self.path))

    def test_every_mutation_is_caught(self):
        for mid, old, new in MUTATIONS:
            with self.subTest(mutation=mid):
                if callable(old):
                    mutated = old(self.original)
                else:
                    self.assertIn(old, self.original, "%s no longer applies to the spec" % mid)
                    mutated = self.original.replace(old, new, 1)
                self.assertNotEqual(mutated, self.original)
                self.path.write_text(mutated, encoding="utf-8")
                self.assertFalse(_spec_tests_pass(self.path), "%s was not caught" % mid)
        self.assertEqual(layout.SPEC.read_text(encoding="utf-8"), self.original)   # repo spec untouched


if __name__ == "__main__":
    unittest.main(verbosity=2)
