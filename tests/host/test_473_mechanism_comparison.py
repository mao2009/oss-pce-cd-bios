"""Issue #473: consistency check between the A/B/C comparison doc and the existing observation docs.

Doc-only. No emulator, BIOS or ROM is read. A failure means the comparison drifted from the recorded
observations (#470 / #481) or lost a required section. It is NOT evidence about any safety mechanism.
"""
import unittest
from pathlib import Path

import re

# ---- approval guard: allowlist of sentence units, not negation scoring. Keep GUARD_ALLOWED in sync with the doc. ----
# Every sentence-level unit of the doc (split at newline, table cell, 。 ； ;) that contains an approval word
# must be an exact GUARD_ALLOWED entry after normalisation. Anything else fails, negated or not.
# Negation is never scored: "not approved" / "approved but not B" are both simply unlisted sentences.
# Bare nouns that are common technical terms (許可バンク, 未決定) are matched only in claim forms (許可した, 決定する).
_APPROVAL_WORD = re.compile(
    r"(?i)approv|grant|sign-?\s?off|signed off|authori[sz]|endors|go-?\s?ahead|green-?lit|accept|agree|consent"
    r"|承認|承諾|了承|認可|同意|合意|容認|賛成|認め"
    r"|許可(?:した|する|され|を得|を与|を受|して)|採用(?:した|する|され|を得|を与|を受)|決定(?:した|する|され|を得|を与|を受)"
    r"|\bOK\b|ＯＫ"
)
_UNIT_SPLIT = re.compile(r"[\n。；;]")
GUARD_ALLOWED = [
    "承認の範囲は、2026-10-22 のオーナー判断 S0 による検証方針 9 項目のみである（第 12.1 節・第 12.6 節）",
    "正式承認なし",
    "同コメントは本番の安全機構の正式承認ではないと明記している",
    "「方式 A/B/C のいずれかについて、オーナーが明示的に承認する",
    "S0 は検証方針であり、安全機構の方式の正式承認の記録はない",
    "「オーナーからチャットで明示的な同意を得た対象は『今後の設計・実コア検証を進める方針』であり、安全機構の完成・本番適用・リリースの承認ではない",
    "本番の安全機構の正式承認ではない",
    "12.6 承認の記録（検証方針のみ）",
    "承認の記録は、2026-10-22 の S0 による検証方針のみである（12.1）",
    "承認の対象：12.9 の推奨 9 項目を、今後の設計と実コア検証を進める方針として扱うこと",
    "承認の対象外：安全機構の完成、本番適用、リリース、#459 / #367 のゲート解除、VERIFIED / DEV_CALLABLE は付与しない",
    "12.7 未承認項目（すべて未決定）",
    "「A を採用する",
    "「オーナー判断：呼出し側で、有効な RAM スタックを保証する契約を採用する",
    "2026-10-22 の S0 により、この 9 項目について検証方針の承認が記録されている",
    "検証方針として承認（S0）",
]


def _normalise(unit):
    unit = re.sub(r"[*`]", "", unit).strip()
    return re.sub(r"^[#\-\s]+", "", unit).strip()


def units(text):
    out = []
    for line in text.split("\n"):
        for cell in line.split("|"):
            for part in _UNIT_SPLIT.split(cell):
                n = _normalise(part)
                if n:
                    out.append(n)
    return out


def approval_violations(text):
    """Return the approval-bearing units of text that are not exact GUARD_ALLOWED entries."""
    return [u for u in units(text) if _APPROVAL_WORD.search(u) and u not in GUARD_ALLOWED]


GUARD_MUST_FAIL = [
    "owner approval was granted",
    "the owner granted approval",
    "The owner approves method A.",
    "Owner sign-off received for A.",
    "This is not final. The owner has approved method A.",
    "Method A is not approved; owner has approved B.",
    "Method A is no longer approved.",
    "Approved by the owner.",
    "This was approved by the owner.",
    "The change was authorised by the owner.",
    "オーナーは方式 A を承認した。",
    "方式 A は承認されている。",
    "承認済み。ただし実装は終わっていない",
    "承認済みの項目が多く、実装は終わっていない",
    "オーナーの承認を得た。",
    "方式 A はオーナーが了承した。",
    "| 方式 A | 承認済み |",
    "安全機構の正式承認を得ている。",
    "本番承認が完了した。",
    "検証方針を承認した。",
    "正式承認。",
    "The owner approved the verification policy.",
    # Coordinator review (MEDIUM): verb/predicate forms and word-order variants that used to slip through.
    "オーナーは方式 A を承認。",
    "オーナー承認あり",
    "オーナーが方式 A を承認しました",
    "The owner has signed off on A.",
    "The owner approve A.",
    "Owner approval exists for A.",
    "Approval was granted for A.",
    "The owner approves A and B.",
    "The owner did approve A.",
    "Owner approval for the safety mechanism is granted.",
    "方式 A の承認あり。",
    "オーナーが安全機構を承認済みとした。",
    # Re-review (MEDIUM-1): a negation that is not in the claim's own window must not deny it.
    "The owner approved A, not B.",
    "The owner approved A without conditions.",
    "Owner approval was granted with no conditions.",
    "The owner signed off on A; no safety mechanism is approved.",
    "The owner signed off on A; the safety mechanism is still open, and no safety mechanism is approved.",
    # The parenthetical "安全機構は未承認" denies the safety mechanism, not the owner approval; the approval
    # clause is judged on its own, so these must still fail.
    "オーナーは方式 A を承認した（安全機構は未承認）。",
    "オーナーは方式 A を承認した（安全性は確認されていない）。",
    "オーナー承認あり（正式承認なし）",
    "The owner did not approve A but approved B.",
    "The owner approved A but did not approve B.",
    "方式 A は正式承認されていないがオーナーが承認した。",
    "Owner approval is not granted for the safety mechanism.",
    "No owner has approved A.",
    "Nobody has approved A.",
    "Owner approval was never given.",
    "オーナー承認はない。",
    "承認は得られていない。",
    # Re-review (MEDIUM-1): synonyms of approval, in claim forms.
    "オーナーは方式 A に同意した。",
    "The owner accepted A.",
    "The owner agreed to A.",
    "The owner consented to A.",
    "The owner go ahead for A.",
    "The owner has green-lit A.",
    "オーナーは方式 A を許可した。",
    "オーナーは方式 A を採用した。",
    "方式 A を採用する。",
    "オーナーは方式 A に賛成した。",
    "オーナーと方式 A について合意した。",
    "オーナーは方式 A を容認した。",
    "オーナーは方式 A を認めた。",
    "オーナーは方式 A を決定した。",
    "The owner did not accept A.",
    # Re-review: the release condition is exempt only as the exact quote (see the PASS list), never bare.
    "オーナーが明示的に承認する。",
    # Re-review (MEDIUM-2): synonyms and conjugations.
    "方式 A の承認が得られた。",
    "方式 A の承認を与えた。",
    "方式 A の承認を取得した。",
    "方式 A の承認をいただいた。",
    "オーナーは方式 A を承認（S0 判断）した。",
    "The owner gave the go-ahead for A.",
    "The owner endorsed A.",
]
DOCS = Path(__file__).resolve().parents[2] / "docs"
CMP = (DOCS / "473-stack-safety-mechanism-comparison.md").read_text(encoding="utf-8")


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


class Structure(unittest.TestCase):
    """Added for the owner-decision restructure (#473). Doc-only; failure means a required part was lost."""

    def test_numbered_sections_present(self):
        for h in ["2. pinned コアのソース読み", "3. 方式の定義", "4. 方式 A", "5. 方式 B", "6. 方式 C",
                  "7. 比較表", "8. 方式 A の穴", "9. 方式 C の条件", "10. 検証条件", "11. Issue 分割案",
                  "12. オーナー判断事項", "13. 未検証"]:
            self.assertTrue(section(h) != "", h)

    def test_proposal_is_not_owner_approval(self):
        head = CMP.split("## 1.")[0]
        self.assertIn("開発側の提案である", head)
        self.assertIn("検証方針 9 項目のみ", head)
        self.assertIn("正式承認なし", head)
        # Clause-level guard (see approval_violations): no approval claim without a same-clause negation.
        self.assertEqual(approval_violations(CMP), [])
        s121 = section("12.1")
        self.assertIn("区別できない", s121)
        self.assertIn("本人かは未確認", s121)
        self.assertNotIn("額面どおり", CMP)
        self.assertNotIn("確認できた明示的なオーナー判断", CMP)
        self.assertIn("本人かは未確認", section("12.1"))
        self.assertIn("NOT_APPLICABLE", section("12.2"))
        s6 = section("12.6")
        self.assertIn("検証方針のみ", s6)
        self.assertIn("正式承認ではない", s6)
        self.assertIn("VERIFIED / DEV_CALLABLE は付与しない", s6)
        r8 = [l for l in section("12.9").splitlines() if l.startswith("| 8 |")]
        self.assertEqual(len(r8), 1)
        self.assertIn("維持", r8[0])
        self.assertIn("BLOCKED", r8[0])
        self.assertNotIn("NMI は、pinned コアに受理経路が無い（第 2.3 節、ソース読み）ので対象外とする。", CMP)
        self.assertIn("開発側の分析として扱う", s121)
        # An 'approved' item may only appear to say that there is none.
        for line in CMP.splitlines():
            if "承認済み" in line and not line.startswith("#"):
                self.assertTrue(any(k in line for k in ["なし", "ない"]), line)
            if "オーナーが承認した" in line:
                self.assertIn("見つからない", line)

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
        for h in ["12.1", "12.2 推奨案", "12.3 理由", "12.4 代替案", "12.5 未測定", "12.6 承認の記録（検証方針のみ）",
                  "12.7 未承認項目", "12.8 次に必要な Issue", "12.9 本資料の 9 項目"]:
            self.assertNotEqual(section(h), "", h)
        rows = [l for l in section("12.9").splitlines() if l.startswith("| ") and l.split("|")[1].strip().isdigit()]
        self.assertEqual(len(rows), 9)
        for r in rows:
            if r.split("|")[1].strip() != "8":
                # S0 approved these 9 items as a verification policy only; formal decisions stay open.
                self.assertIn("検証方針として承認（S0）", r)
            else:
                self.assertIn("維持", r)
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
        # S0 record (2026-10-22): scope is the verification policy, never the formal safety-mechanism approval.
        self.assertIn("S0", s)
        self.assertIn("検証方針として記録する", s)
        self.assertIn("安全機構の完成・本番適用・リリースの承認ではない", s)
        self.assertIn("投稿者の区別もできない", s)

    def test_forbidden_claims_absent(self):
        for bad in ["参照 JSR ゲートを解除", "release_eligible=true", "VERIFIED を付与する", "安全が実証", "実証された"]:
            self.assertNotIn(bad, CMP)
        self.assertIn("参照 JSR ゲートも変更しない", CMP)
        self.assertIn("本書の範囲外で、行わない", section("12.7"))


class GuardSelfTest(unittest.TestCase):
    """The allowlist guard itself must not silently weaken."""

    def test_must_fail(self):
        # Not on the allowlist, so they fail whether or not they contain a negation.
        for s in GUARD_MUST_FAIL:
            self.assertTrue(approval_violations(s), s)

    def test_allowlisted_units_pass(self):
        for s in GUARD_ALLOWED:
            self.assertEqual(approval_violations(s), [], s)

    def test_allowlist_has_no_stale_entries(self):
        # Every allowlisted sentence must occur verbatim (normalised) in the doc, so the list cannot drift.
        doc_units = units(CMP)
        for s in GUARD_ALLOWED:
            self.assertIn(s, doc_units, s)

    def test_mutation_against_doc(self):
        # Appending any sentence outside the allowlist must make the guard fire; the doc itself is clean.
        self.assertEqual(approval_violations(CMP), [])
        for s in GUARD_MUST_FAIL:
            self.assertTrue(approval_violations(CMP + chr(10) + s), s)


if __name__ == "__main__":
    unittest.main()
