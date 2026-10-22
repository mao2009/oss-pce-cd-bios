"""Issue #473: consistency check between the A/B/C comparison doc and the existing observation docs.

Doc-only. No emulator, BIOS or ROM is read. A failure means the comparison drifted from the recorded
observations (#470 / #481) or lost a required section. It is NOT evidence about any safety mechanism.
"""
import unittest
from pathlib import Path

DOCS = Path(__file__).resolve().parents[2] / "docs"
CMP = (DOCS / "473-stack-safety-mechanism-comparison.md").read_text(encoding="utf-8")
AUDIT = (DOCS / "367-block-transfer-audit.md").read_text(encoding="utf-8")
EXP = (DOCS / "473-caller-stack-experiment.md").read_text(encoding="utf-8")

class Comparison(unittest.TestCase):
    def test_quoted_figures_match_sources(self):
        # Exact figures that are spelled identically in both documents.
        for s in ["`$12`", "`$34`", "`$07`", "`$BE`", "`$EF`", "`$05`", "`0xBEEF`", "$5A"]:
            self.assertIn(s, AUDIT)
        for s in ["`$12/$34/$07`", "`$BE/$EF/$05`", "`0xBEEF`", "$5A"]:
            self.assertIn(s, CMP)
        for s in ["AR 0→7", "R[7] 0→564"]:
            self.assertIn(s, CMP)
            self.assertIn(s, AUDIT)
        self.assertIn("30 ケース", CMP)
        self.assertIn("30 ケース", AUDIT)
        self.assertNotIn("検出する前に、ガードが", CMP)  # O6 once had a self-contradicting sentence
        self.assertIn("UNMEASURED", CMP)
        self.assertIn("`$05`→`$00`", CMP)
        for s in ["$E019", "80 ステップ"]:
            self.assertIn(s, CMP)
            self.assertIn(s, EXP)

    def test_required_sections_and_negative_kept(self):
        for s in ["オーナー判断事項", "Issue 分割案", "MPR1=$FF", "S0", "S7", "A / B / C"]:
            self.assertIn(s, CMP)
        # Each verification condition must be its own table row (a bare "PC" elsewhere is not enough).
        rows = [l.split("|")[1].strip() for l in CMP.splitlines() if l.startswith("| ")]
        for r in ["PC", "A / X / Y", "S", "P", "IRQ 線・保留状態", "MPR0〜7", "VDC", "宛先バイト", "WRAM のスタック画像"]:
            self.assertIn(r, rows)

    def test_gates_stay_blocked_and_no_new_claims(self):
        self.assertIn("BLOCKED のまま", CMP)
        for bad in ["ゲートを解除する", "解除済み", "解除可能", "MERGE_RECOMMENDED"]:
            self.assertNotIn(bad, CMP)
        self.assertIn("release_eligible=false", CMP)
        self.assertIn("本番用安全ガードは有効化しない", CMP)
        # The only mention allowed is the statement that they are not granted.
        for word in ["VERIFIED", "DEV_CALLABLE"]:
            for line in CMP.splitlines():
                if word in line:
                    self.assertIn("付与しない", line)

    def test_sources_still_block(self):
        self.assertIn("BLOCKED", AUDIT)
        self.assertIn("本番用安全機構ではない", AUDIT)


if __name__ == "__main__":
    unittest.main()
