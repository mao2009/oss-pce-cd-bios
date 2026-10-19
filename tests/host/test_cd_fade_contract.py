"""Host tests for the CD_FADE contract ledger (Issue #282).

These check the ledger's wording rules and the pinned field verdicts only. They
do not execute or verify the System Card CD_FADE callee.
"""
import re
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "research" / "cd-fade-contract.md"

ALLOWED = {"SOURCE_FACT", "HYPOTHESIS", "UNKNOWN"}
FORBIDDEN = re.compile(r"(?<![A-Za-z_])(OBSERVED|VERIFIED|PASS|DEV_CALLABLE)(?![A-Za-z_])")
GATE_CLAIMS = re.compile(r"release_eligible\s*[:=]\s*true|DEV_CALLABLE`?\s*を付ける|互換(を満た|である|性あり|確認)|BLOCKED_ABI.{0,10}解除")


def section1(text):
    return text.split("## 1.")[1].split("## 2.")[0]


FIELD_ROW = re.compile(r"^\| (F\d{2}) \|")
SOURCE_ID = re.compile(r"\bS\d+\b")
# Every source S1-S10 stays PENDING (provenance), which is independent of SOURCE_FACT.
PENDING_SOURCES = {f"S{i}" for i in range(1, 11)}
# Third-party / re-implementation sources: a row resting only on these is never SOURCE_FACT.
THIRD_PARTY_SOURCES = {"S8", "S9", "S10"}
EXPECTED_COUNTS = {"SOURCE_FACT": 15, "HYPOTHESIS": 4, "UNKNOWN": 18}
SOURCE_FACT_DEF = ''

# Pinned verdict per field row. A verdict change must edit this table in review.
EXPECTED = {
    "F01": "SOURCE_FACT", "F02": "UNKNOWN", "F03": "SOURCE_FACT", "F04": "SOURCE_FACT",
    "F05": "SOURCE_FACT", "F06": "SOURCE_FACT", "F07": "UNKNOWN", "F08": "HYPOTHESIS",
    "F09": "HYPOTHESIS", "F10": "UNKNOWN", "F11": "HYPOTHESIS", "F12": "UNKNOWN",
    "F13": "UNKNOWN", "F14": "SOURCE_FACT", "F15": "HYPOTHESIS", "F16": "UNKNOWN",
    "F17": "SOURCE_FACT", "F18": "UNKNOWN", "F19": "UNKNOWN", "F20": "SOURCE_FACT",
    "F21": "SOURCE_FACT", "F22": "UNKNOWN", "F23": "SOURCE_FACT", "F24": "UNKNOWN",
    "F25": "SOURCE_FACT", "F26": "SOURCE_FACT", "F27": "UNKNOWN", "F28": "UNKNOWN",
    "F29": "SOURCE_FACT", "F30": "SOURCE_FACT", "F31": "UNKNOWN", "F32": "UNKNOWN",
    "F33": "UNKNOWN", "F34": "UNKNOWN", "F35": "SOURCE_FACT", "F36": "UNKNOWN",
    "F37": "UNKNOWN",
}


def parse_fields(text):
    """Map field id -> (verdict, basis cell) for each field row."""
    rows = {}
    for line in text.splitlines():
        m = FIELD_ROW.match(line)
        if not m:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        rows[m.group(1)] = (cells[2], cells[3])
    return rows


def check_ledger(text):
    """Return a list of wording violations in the field table."""
    errors = []
    fields = 0
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        if FORBIDDEN.search(line):
            errors.append(f"forbidden status word: {line[:60]}")
        m = FIELD_ROW.match(line)
        if not m:
            continue
        fields += 1
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        verdict = cells[2]
        if verdict not in ALLOWED:
            errors.append(f"{m.group(1)}: verdict must be exactly one of {sorted(ALLOWED)}, got {verdict!r}")
    if fields == 0:
        errors.append("no field rows found")
    return errors


def check_pins(text):
    """Return violations of the pinned verdicts, counts and PENDING-source rule."""
    rows = parse_fields(text)
    errors = []
    for fid, want in EXPECTED.items():
        if fid not in rows:
            errors.append(f"{fid}: row missing")
        elif rows[fid][0] != want:
            errors.append(f"{fid}: verdict pinned {want}, got {rows[fid][0]}")
    for fid in sorted(set(rows) - set(EXPECTED)):
        errors.append(f"{fid}: row not pinned")
    counts = Counter(verdict for verdict, _ in rows.values())
    if counts != Counter(EXPECTED_COUNTS):
        errors.append(f"verdict counts {dict(counts)} != {EXPECTED_COUNTS}")
    errors += check_source_fact(rows)
    return errors


def check_source_fact(rows):
    """SOURCE_FACT = caller-side fact in a pinned source; PENDING provenance is irrelevant.

    It needs a cited source, must not rest on third-party notes, and must not be a callee row.
    """
    errors = []
    for fid, (verdict, basis) in rows.items():
        if verdict != "SOURCE_FACT":
            continue
        cited = set(SOURCE_ID.findall(basis))
        if not cited:
            errors.append(f"{fid}: SOURCE_FACT cites no source")
        if cited & THIRD_PARTY_SOURCES:
            errors.append(f"{fid}: SOURCE_FACT cites third-party source {sorted(cited & THIRD_PARTY_SOURCES)}")
        if fid in CALLEE_ROWS:
            errors.append(f"{fid}: callee-behavior row must not be SOURCE_FACT")
    return errors


# Callee (BIOS-side) rows: unverified behavior, never SOURCE_FACT.
CALLEE_ROWS = ["F02", "F07", "F10", "F12", "F13", "F16", "F18", "F19", "F22", "F24",
               "F27", "F28", "F31", "F32", "F33", "F34", "F36", "F37"]


def source_rows(text):
    """Return the section-3 source table rows (S1..S10)."""
    return [l for l in text.splitlines() if re.match(r"^\| S\d+ \|", l)]


def check_sources(text):
    """Every source row must be PENDING and not APPROVED (this PR records no approval)."""
    errors = []
    for row in source_rows(text):
        sid = SOURCE_ID.search(row).group(0)
        if "APPROVED" in row:
            errors.append(f"{sid}: source is APPROVED without owner record")
        elif "PENDING" not in row:
            errors.append(f"{sid}: source row does not state PENDING")
    return errors


def promote(text, fid, verdict):
    """Return text with the verdict cell of one field row replaced."""
    return re.sub(rf"^(\| {fid} \|[^|]*\| )[A-Z_]+( \|)", rf"\g<1>{verdict}\2",
                  text, count=1, flags=re.M)


class LedgerRulesTest(unittest.TestCase):
    def test_rule_rejects_forbidden_status_words(self):
        self.assertTrue(check_ledger("| F90 | x | OBSERVED | S1 | y |"))
        self.assertTrue(check_ledger("| F90 | x | SOURCE_FACT | PASS | y |"))

    def test_rule_rejects_mixed_or_promoted_verdicts(self):
        self.assertTrue(check_ledger("| F90 | x | UNKNOWN（SOURCE_FACT） | — | y |"))
        self.assertTrue(check_ledger("| F90 | x | HYPOTHESIS, UNKNOWN | — | y |"))

    def test_rule_accepts_single_verdict(self):
        self.assertEqual(check_ledger("| F90 | x | UNKNOWN | — | y |"), [])


class CdFadeLedgerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = DOC.read_text(encoding="utf-8")

    def test_ledger_field_verdicts_are_single_and_no_forbidden_words(self):
        self.assertEqual(check_ledger(self.text), [])

    def test_field_verdicts_match_pins_and_counts(self):
        self.assertEqual(check_pins(self.text), [])

    def test_release_gate_stays_closed(self):
        s1 = section1(self.text)
        self.assertIn("BLOCKED_ABI を維持", s1)
        self.assertIn("`release_eligible=false`。`DEV_CALLABLE` は付けない", s1)
        self.assertIsNone(GATE_CLAIMS.search(self.text))

    def test_source_fact_definition_is_unified_text(self):
        self.assertIn(SOURCE_FACT_DEF, self.text)
        self.assertNotIn("APPROVED（人間が裁定済み", self.text)

    def test_gate_mutations_are_detected(self):
        for old, new in [
            ("BLOCKED_ABI を維持", "BLOCKED_ABI を解除"),
            ("`DEV_CALLABLE` は付けない", "`DEV_CALLABLE` を付ける"),
            ("`release_eligible=false`。", "`release_eligible=false`。release_eligible: true に変更。"),
            ("BIOS 側の振る舞いに対する実行証拠はない", "BIOS 互換を満たす"),
        ]:
            with self.subTest(new=new):
                m = self.text.replace(old, new, 1)
                self.assertNotEqual(m, self.text)
                self.assertTrue(GATE_CLAIMS.search(m) or "BLOCKED_ABI を維持" not in section1(m))

    def test_forbidden_words_adjacent_to_japanese_are_detected(self):
        self.assertTrue(check_ledger("| F90 | x | UNKNOWN | 実機PASS | y |"))
        self.assertTrue(check_ledger("| F90 | x | UNKNOWN | VERIFIEDの定義 | y |"))


class PromotionRegressionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = DOC.read_text(encoding="utf-8")

    def assert_promotion_rejected(self, fid, verdict):
        mutated = promote(self.text, fid, verdict)
        self.assertNotEqual(mutated, self.text)
        # The single-verdict wording rule alone accepts the promotion ...
        self.assertEqual(check_ledger(mutated), [])
        # ... so the pinned verdicts must reject it.
        self.assertTrue(check_pins(mutated), f"{fid} -> {verdict} was not rejected")

    def test_f13_unknown_to_source_fact_is_rejected(self):
        self.assert_promotion_rejected("F13", "SOURCE_FACT")

    def test_f02_unknown_to_hypothesis_is_rejected(self):
        self.assert_promotion_rejected("F02", "HYPOTHESIS")

    def test_f18_hypothesis_to_source_fact_is_rejected(self):
        self.assert_promotion_rejected("F18", "SOURCE_FACT")

    def test_pending_source_does_not_force_downgrade(self):
        rows = parse_fields(self.text)
        # F01 is SOURCE_FACT while citing PENDING S1: provenance is independent.
        self.assertEqual(rows["F01"][0], "SOURCE_FACT")
        self.assertIn("S1", rows["F01"][1])
        self.assertEqual(check_pins(self.text), [])
        for fid, want in EXPECTED.items():
            if want == "SOURCE_FACT":
                with self.subTest(fid=fid):
                    self.assert_promotion_rejected(fid, "HYPOTHESIS")

    def test_callee_claim_is_not_source_fact(self):
        for fid in ("F10", "F18", "F37", "F13"):
            with self.subTest(fid=fid):
                self.assert_promotion_rejected(fid, "SOURCE_FACT")
                errors = check_source_fact(parse_fields(promote(self.text, fid, "SOURCE_FACT")))
                self.assertIn(f"{fid}: callee-behavior row must not be SOURCE_FACT", errors)

    def test_third_party_only_source_fact_is_rejected(self):
        self.assertIn("F10: SOURCE_FACT cites third-party source ['S8']",
                      check_source_fact({"F10": ("SOURCE_FACT", "S8 332")}))
        self.assertTrue(check_source_fact({"F90": ("SOURCE_FACT", "—")}))

    def test_f10_f18_unknown_and_f08_split(self):
        rows = parse_fields(self.text)
        self.assertEqual(rows["F10"][0], "UNKNOWN")
        self.assertEqual(rows["F18"][0], "UNKNOWN")
        self.assertEqual(rows["F08"][0], "HYPOTHESIS")  # (a) cited document's description
        self.assertEqual(rows["F37"][0], "UNKNOWN")  # (b) actual BIOS behavior
        self.assertNotIn("S8", rows["F08"][1])

    def test_every_field_promotion_to_source_fact_is_rejected(self):
        for fid, want in EXPECTED.items():
            if want == "SOURCE_FACT":
                continue
            with self.subTest(fid=fid):
                self.assert_promotion_rejected(fid, "SOURCE_FACT")

    def test_every_unknown_promotion_is_rejected(self):
        for fid, want in EXPECTED.items():
            if want == "UNKNOWN":
                with self.subTest(fid=fid):
                    self.assert_promotion_rejected(fid, "HYPOTHESIS")

    def test_callee_rows_are_not_source_fact(self):
        rows = parse_fields(self.text)
        for fid in CALLEE_ROWS:
            self.assertIn(rows[fid][0], {"HYPOTHESIS", "UNKNOWN"}, fid)

    def test_all_sources_stay_pending(self):
        self.assertEqual(len(source_rows(self.text)), 10)
        self.assertEqual(check_sources(self.text), [])

    def test_source_approval_mutation_is_detected_for_every_source(self):
        for sid in sorted(PENDING_SOURCES):
            with self.subTest(sid=sid):
                mutated = re.sub(rf"^(\| {sid} \|.*)PENDING", r"\g<1>APPROVED", self.text,
                                 count=1, flags=re.M)
                self.assertNotEqual(mutated, self.text)
                self.assertTrue(check_sources(mutated))

    def test_source_pending_removal_is_detected(self):
        mutated = re.sub(r"^(\| S2 \|.*)PENDING（S1 と同じ）", r"\g<1>同上", self.text,
                         count=1, flags=re.M)
        self.assertNotEqual(mutated, self.text)
        self.assertTrue(check_sources(mutated))

    def test_release_gate_wording_is_negative_only(self):
        self.assertNotRegex(self.text, r"(BLOCKED_ABI.{0,10}解除|release_eligible=true)")

    def test_ledger_declares_counts(self):
        self.assertIn("SOURCE_FACT 15、HYPOTHESIS 4、UNKNOWN 18", self.text)


if __name__ == "__main__":
    unittest.main()
