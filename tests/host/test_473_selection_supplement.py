"""Issue #473 (selection supplement): consistency check for docs/473-selection-supplement-s7s8.md.

Doc-only. No emulator, BIOS or ROM is read. Checks the supplement reflects the
#653/#654 synthesis and keeps the safety gates. It is NOT evidence that any
real-core mechanism is safe for production use.
"""
import unittest
from pathlib import Path

DOCS = Path(__file__).resolve().parents[2] / "docs"
DOC = DOCS / "473-selection-supplement-s7s8.md"


class SelectionSupplement(unittest.TestCase):
    def setUp(self):
        self.text = DOC.read_text(encoding="utf-8")

    def test_s7_s8_synthesis_present_and_nonduplicative(self):
        for s in ["#653", "#654", "NOT_MEASURED", "m_mpr_buffer", "clock point",
                  "PR #701", "再実装ではない"]:
            self.assertIn(s, self.text)

    def test_a_b_c_verdicts_kept(self):
        for s in ["A 単独は UNSAFE", "B は単独不成立", "停止後状態は BLOCKED",
                  "A+D+T", "選定保留"]:
            self.assertIn(s, self.text)
        self.assertNotIn("A 単独は SAFE", self.text)

    def test_precedents_labelled_not_evidence(self):
        for s in ["#712", "#709", "先例", "transfer 安全の証拠ではない"]:
            self.assertIn(s, self.text)

    def test_comparison_table_shape(self):
        rows = [l for l in self.text.splitlines() if l.startswith("| push 前拒否")]
        self.assertEqual(len(rows), 1)
        for s in ["| pop 前拒否", "| CPU 状態維持", "| VDC 状態維持",
                  "| MPR alias 対応", "| 動的 MPR 対応", "| IRQ 対応",
                  "| デバイス副作用防止", "| free-run での適用", "| 実装規模",
                  "| 実コア証拠", "| 残存リスク"]:
            self.assertIn(s, self.text)

    def test_method_questions_answered(self):
        for s in ["A1 TransferStart", "A5 MPR alias", "A8 強制可能な検査位置",
                  "B3 push 拒否後の pop 継続防止", "B8 Geargrafx 改修範囲",
                  "C1 decode 後", "C6 step / free-run", "C8 本番組込み範囲"]:
            self.assertIn(s, self.text)

    def test_gates_and_no_production_claim(self):
        for s in ["BLOCKED のまま", "release_eligible=false", "ABI VERIFIED 0/81",
                  "DEV_CALLABLE 0", "本番安全機構は実装しない", "MERGE_RECOMMENDED"]:
            self.assertIn(s, self.text)
        self.assertNotIn("release_eligible=true", self.text)
        self.assertNotIn("production guard is enabled", self.text)

    def test_implementation_plan_split(self):
        for s in ["各 Issue = 1 Draft PR", "#367 から受け取る証拠", "#173 への統合条件",
                  "cold/warm", "debug/release 差異", "release gate 維持"]:
            self.assertIn(s, self.text)


if __name__ == "__main__":
    unittest.main()
