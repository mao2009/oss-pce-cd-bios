"""Issue #473: consistency check between the A/B/C comparison doc and the existing observation docs.

Doc-only. No emulator, BIOS or ROM is read. A failure means the comparison drifted from the recorded
observations (#470 / #481) or lost a required section. It is NOT evidence about any safety mechanism.

Approval wording is not policed by a pattern guard. All approval statements are consolidated in the fixed
section "承認事項（S0 検証方針のみ）"; the tests below check that section's structure and the gate invariants.
"""
import unittest
from pathlib import Path

DOCS = Path(__file__).resolve().parents[2] / "docs"
CMP = (DOCS / "473-stack-safety-mechanism-comparison.md").read_text(encoding="utf-8")
AUDIT = (DOCS / "367-block-transfer-audit.md").read_text(encoding="utf-8")
EXP = (DOCS / "473-caller-stack-experiment.md").read_text(encoding="utf-8")


def section(prefix):
    """Body of the '## <prefix>' / '### <prefix>' heading, up to the next heading of the same or higher level."""
    lines = CMP.splitlines()
    for i, l in enumerate(lines):
        if l.startswith("#") and l.lstrip("#").strip().startswith(prefix):
            level = len(l) - len(l.lstrip("#"))
            out = []
            for m in lines[i + 1:]:
                if m.startswith("#") and len(m) - len(m.lstrip("#")) <= level:
                    break
                out.append(m)
            return "\n".join(out)
    raise AssertionError("missing section " + prefix)


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


class Structure(unittest.TestCase):
    """Doc-only structure checks (#473). A failure means a required part was lost."""

    def test_numbered_sections_present(self):
        for h in ["2. pinned コアのソース読み", "3. 方式の定義", "4. 方式 A", "5. 方式 B", "6. 方式 C",
                  "7. 比較表", "8. 方式 A の穴", "9. 方式 C の条件", "10. 検証条件", "11. Issue 分割案",
                  "12. オーナー判断事項", "13. 未検証"]:
            self.assertTrue(section(h) != "", h)

    def test_approval_section_is_fixed_and_before_section_1(self):
        head = CMP.split("## 1.")[0]
        self.assertIn("## 承認事項（S0 検証方針のみ）", head)
        self.assertIn("開発側の提案である", head)
        self.assertIn("承認事項は、冒頭の「承認事項（S0 検証方針のみ）」節に集約", head)

    def test_approval_section_scope_is_nine_items_only(self):
        s = section("承認事項")
        self.assertIn("検証方針として承認済み", s)
        self.assertIn("9 項目のみ", s)
        items = [l for l in s.splitlines() if l[:2] in [f"{n}." for n in range(1, 10)]]
        self.assertEqual(len(items), 9)
        self.assertIn("S0 の原文には無い本書の補足", s)

    def test_approval_section_not_approved_items(self):
        s = section("承認されていないもの")
        for k in ["本番用の安全機構", "実機での安全性", "純正 ABI の VERIFIED", "DEV_CALLABLE",
                  "参照 JSR の実行許可", "release_eligible"]:
            self.assertIn(k, s)
        for k in ["付与しない", "変更しない"]:
            self.assertIn(k, s)

    def test_approval_section_gates_and_measurement_split(self):
        g = section("ゲートの状態")
        self.assertIn("#459 / #367", g)
        self.assertIn("BLOCKED のまま維持", g)
        m = section("実測・ソース読み・未測定の区別")
        for k in ["**実測**", "**ソース読み**", "**未測定（UNMEASURED）**", "NOT_RUN"]:
            self.assertIn(k, m)

    def test_proposal_is_not_owner_approval(self):
        s121 = section("12.1")
        self.assertIn("区別できない", s121)
        self.assertIn("本人かは未確認", s121)
        self.assertNotIn("額面どおり", CMP)
        self.assertNotIn("確認できた明示的なオーナー判断", CMP)
        self.assertIn("NOT_APPLICABLE", section("12.2"))
        self.assertIn("承認事項（S0 検証方針のみ）", section("12.6"))
        r8 = [l for l in section("12.9").splitlines() if l.startswith("| 8 |")]
        self.assertEqual(len(r8), 1)
        self.assertIn("維持", r8[0])
        self.assertIn("BLOCKED", r8[0])
        self.assertNotIn("NMI は、pinned コアに受理経路が無い（第 2.3 節、ソース読み）ので対象外とする。", CMP)
        self.assertIn("開発側の分析として扱う", s121)

    def test_verification_order_a_then_c_then_both(self):
        s = section("3. 方式の定義")
        i = [s.index(k) for k in ["1. 方式 A の実コア検証", "2. 方式 C を A の補完", "3. A と C の併用"]]
        self.assertEqual(i, sorted(i))

    def test_b_is_not_a_standalone_candidate(self):
        self.assertIn("B は単独候補から外す", section("3. 方式の定義"))
        self.assertIn("単独候補から外す", CMP)
        issue = section("11. Issue 分割案")
        self.assertNotIn("方式 B の実コア検証", issue)
        self.assertIn("B 単独の検証 Issue は案に含めない", issue)
        self.assertIn("単独では採らない", section("7. 比較表"))

    def test_irq_in_tam_hole_is_unmeasured_with_negative_test(self):
        s = section("8. 方式 A の穴")
        self.assertIn("UNMEASURED", s)
        for k in ["N-IRQ-TAM", "TAM #$02", "IRQ1 が assert 済み", "IRQ 受理の stack 書込み", "VDC の状態",
                  "A/X/Y/P/S/PC", "MPR0〜7", "拒否前の値のまま保たれたか"]:
            self.assertIn(k, s)
        for line in s.splitlines():
            if "PASS" in line:
                self.assertTrue("書かない" in line or "PASS には" in line, line)
        a = section("4. 方式 A")
        self.assertIn("UNMEASURED", a)
        self.assertIn("同じ `RunInstruction`", a)

    def test_source_reading_cites_functions_and_labels_it(self):
        s = section("2. pinned コアのソース読み")
        for k in ["StackPush16", "StackPop16", "HandleIRQ", "CheckIRQs", "RunInstruction", "SetMprTAM",
                  "OPCodes_TransferStart", "OPCodes_TransferEnd", "OPCode0x20", "OPCode0x48", "OPCode0x08",
                  "OPCodes_Subroutine", "OPCode0x60", "OPCode0x40", "huc6280_inline.h", "NMI の受理経路が無い",
                  ]:
            self.assertIn(k, s)
        self.assertIn("実行による確認ではない", CMP)
        self.assertIn("実在する", section("2.2"))
        self.assertIn("実コアで測っていない", section("2.2"))

    def test_c_conditions_list_targets_and_leftover_side_effects(self):
        t = section("2.1")
        for k in ["JSR", "PHA", "PHP", "BSR", "RTS", "RTI", "IRQ 入口", "TransferStart", "TransferEnd"]:
            self.assertIn("| " + k + " |", t)
        rows = [l.split("|")[1].strip() for l in section("9.2").splitlines() if l.startswith("| ")]
        for r in ["PC", "A / X / Y", "P", "S", "MPR", "VDC", "WRAM", "宛先データ", "IRQ 保留状態"]:
            self.assertIn(r, rows)
        self.assertIn("UNMEASURED", section("9. 方式 C の条件"))

    def test_owner_decision_structure(self):
        for h in ["12.1", "12.2 推奨案", "12.3 理由", "12.4 代替案", "12.5 未測定", "12.6 承認の記録",
                  "12.7 未承認項目", "12.8 次に必要な Issue", "12.9 本資料の 9 項目"]:
            self.assertNotEqual(section(h), "", h)
        rows = [l for l in section("12.9").splitlines() if l.startswith("| ") and l.split("|")[1].strip().isdigit()]
        self.assertEqual(len(rows), 9)
        for r in rows:
            n = r.split("|")[1].strip()
            if n != "8":
                self.assertIn("S0 検証方針の範囲（承認事項節）", r)
            else:
                self.assertIn("維持", r)
        for r in rows:
            if r.split("|")[1].strip() in ("5", "6"):
                self.assertIn("S0 の原文外の制約（制約強化方向）", r)
        rec = section("12.2")
        for k in ["方式 A を先に", "WRAM $F8 のみ", "ゲストの実行を打ち切る", "検証コアのパッチと製品実装は分ける",
                  "NMI", "BLOCKED を維持"]:
            self.assertIn(k, rec)

    def test_owner_records_are_quoted_not_overstated(self):
        s = section("12.1")
        self.assertIn("呼出し側で、有効な RAM スタックを保証する契約を採用する", s)
        self.assertIn("本番の安全機構の正式承認ではない", s)
        self.assertIn("暫定候補", s)
        self.assertIn("コメントなし", s)
        self.assertIn("S0", s)
        self.assertIn("検証方針として記録する", s)
        self.assertIn("安全機構の完成・本番適用・リリースの承認ではない", s)
        self.assertIn("投稿者の区別もできない", s)

    def test_forbidden_claims_absent(self):
        for bad in ["参照 JSR ゲートを解除", "release_eligible=true", "VERIFIED を付与する", "安全が実証", "実証された"]:
            self.assertNotIn(bad, CMP)
        self.assertIn("参照 JSR ゲートも変更しない", CMP)
        self.assertIn("本書の範囲外で、行わない", section("12.7"))


if __name__ == "__main__":
    unittest.main()
