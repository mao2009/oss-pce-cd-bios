"""Issue #473: consistency check for docs/473-production-safety-mechanism-design.md only.

Doc-only. No emulator, BIOS or ROM is read. This test checks that the design doc reflects the
#647/#648 verification results and keeps the safety gates. It is NOT evidence that any real-core
mechanism is safe.
"""
import unittest
from pathlib import Path

DOCS = Path(__file__).resolve().parents[2] / "docs"
DESIGN = (DOCS / "473-production-safety-mechanism-design.md").read_text(encoding="utf-8")


class ProductionDesign(unittest.TestCase):
    def test_647_648_reflected(self):
        for s in ["#647", "#648", "SAFE_IN_SCOPE", "UNSAFE", "BLOCKED",
                  "decode-time", "IRQ deferral", "pre-start", "mid-transfer"]:
            self.assertIn(s, DESIGN)

    def test_a_alone_not_claimed_safe(self):
        self.assertIn("A 単独は UNSAFE", DESIGN)

    def test_ac_not_claimed_safe(self):
        # A+C stops side effects but the stopped state is BLOCKED, not SAFE.
        self.assertIn("stopped state is **BLOCKED**", DESIGN)
        self.assertNotIn("A+C is SAFE", DESIGN)

    def test_d_e_limits_stated(self):
        for s in ["PC/MPR state and IRQ timing differ", "検証対象範囲内", "UNKNOWN"]:
            self.assertIn(s, DESIGN)

    def test_owner_not_approved_and_gates_kept(self):
        for s in ["Owner 未承認", "BLOCKED", "release_eligible", "ABI VERIFIED 0/81",
                  "DEV_CALLABLE 0", "0/81", "再開は禁止"]:
            self.assertIn(s, DESIGN)

    def test_per_method_post_stop_expectations(self):
        # §5 must not demand one identical post-stop state for every method.
        self.assertIn("同一の停止後状態を全方式に一律に要求しない", DESIGN)
        for s in ["A 単独", "A+C", "A+D", "A+E", "転送開始前", "転送途中",
                  "TAM 実行前", "TAM 実行後", "PC/MPR", "BLOCKED (再開禁止)", "再開は禁止"]:
            self.assertIn(s, DESIGN)

    def test_mid_transfer_partial_execution_blocked(self):
        self.assertIn("部分実行済み", DESIGN)
        self.assertIn("巻き戻されない", DESIGN)

    def test_not_claimed_implemented(self):
        self.assertIn("No implementation", DESIGN)
        self.assertIn("実装済みの安全機構は存在しない", DESIGN)
        self.assertNotIn("production guard is enabled", DESIGN)


if __name__ == "__main__":
    unittest.main()
