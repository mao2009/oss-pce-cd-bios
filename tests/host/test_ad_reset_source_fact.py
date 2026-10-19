import pathlib
import unittest

DOC = pathlib.Path(__file__).resolve().parents[2] / "docs/research/ad-reset-contract.md"
# #317/#321 と同一の SOURCE_FACT 定義（docs/research/ad-reset-contract.md 11-12 行）
CANONICAL = "- `SOURCE_FACT`: SOURCE_FACT = a caller-side fact written in a pinned primary source. It is independent of provenance PENDING. It does NOT indicate BIOS callee behavior.\n  （訳: 固定版の一次出典に書かれた呼出側の事実。出典の provenance が PENDING かどうかとは独立。BIOS 呼出先の振る舞いを示さない。）出典が第三者の記述のみ、または再実装・開発者の主張である場合は `HYPOTHESIS` とする。ライセンス上の利用可否、呼出先の実測、実装可能性は別の軸であり、SOURCE_FACT はそのいずれも示さない。"


class AdResetSourceFactDefinition(unittest.TestCase):
    def test_canonical_definition_present_and_no_approved_gate(self):
        text = DOC.read_text(encoding="utf-8").replace(chr(13) + chr(10), chr(10))
        self.assertIn(CANONICAL, text)
        self.assertNotIn("APPROVED", CANONICAL)  # 旧ルール: SOURCE_FACT に APPROVED を要求


if __name__ == "__main__":
    unittest.main()
