import collections
import pathlib
import re
import unittest

DOC = pathlib.Path(__file__).resolve().parents[2] / "docs/research/ad-reset-contract.md"
# #317/#321 と同一の SOURCE_FACT 定義（docs/research/ad-reset-contract.md 11-12 行）
CANONICAL = "- `SOURCE_FACT`: SOURCE_FACT = a caller-side fact written in a pinned primary source. It is independent of provenance PENDING. It does NOT indicate BIOS callee behavior.\n  （訳: 固定版の一次出典に書かれた呼出側の事実。出典の provenance が PENDING かどうかとは独立。BIOS 呼出先の振る舞いを示さない。）出典が第三者の記述のみ、または再実装・開発者の主張である場合は `HYPOTHESIS` とする。ライセンス上の利用可否、呼出先の実測、実装可能性は別の軸であり、SOURCE_FACT はそのいずれも示さない。"


def read():
    return DOC.read_text(encoding="utf-8").replace("\r\n", "\n")


def rows():
    """§4 の表を (id, 確度) の一覧にする。"""
    sec = read().split("## 4. ", 1)[1].split("\n## ", 1)[0]
    out = []
    for line in sec.splitlines():
        c = [x.strip() for x in line.strip().strip("|").split("|")]
        if re.fullmatch(r"\d+[ab]?", c[0]) and len(c) >= 4:
            out.append((c[0], c[3]))
    return out


class AdResetLedger(unittest.TestCase):
    """限界: BIOS 側の判別は行 id の接尾辞 b のみ。接尾辞のない BIOS 側の行（2、6、9、17、18）は検査しない。"""

    def test_canonical_definition_present(self):
        self.assertIn(CANONICAL, read())

    def test_source_fact_paragraph_has_no_approved_condition(self):
        para = read().split("- `SOURCE_FACT`:", 1)[1].split("\n- `HYPOTHESIS`", 1)[0]
        self.assertNotIn("APPROVED", para)

    def test_row_counts_match_doc(self):
        r = rows()
        self.assertEqual(len(r), 31)
        self.assertEqual(
            collections.Counter(c for _, c in r),
            {"SOURCE_FACT": 10, "HYPOTHESIS": 5, "UNKNOWN": 16},
        )

    def test_no_bios_side_row_is_source_fact(self):
        bad = [i for i, c in rows() if i.endswith("b") and c == "SOURCE_FACT"]
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
