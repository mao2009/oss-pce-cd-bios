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
# No source is APPROVED yet; every source S1-S10 is PENDING until the owner records approval.
PENDING_SOURCES = {f"S{i}" for i in range(1, 11)}
EXPECTED_COUNTS = {"SOURCE_FACT": 0, "HYPOTHESIS": 21, "UNKNOWN": 15}

# Pinned verdict per field row. A verdict change must edit this table in review.
EXPECTED = {
    "F01": "HYPOTHESIS", "F02": "UNKNOWN", "F03": "HYPOTHESIS", "F04": "HYPOTHESIS",
    "F05": "HYPOTHESIS", "F06": "HYPOTHESIS", "F07": "UNKNOWN", "F08": "HYPOTHESIS",
    "F09": "HYPOTHESIS", "F10": "HYPOTHESIS", "F11": "HYPOTHESIS", "F12": "UNKNOWN",
    "F13": "UNKNOWN", "F14": "HYPOTHESIS", "F15": "HYPOTHESIS", "F16": "UNKNOWN",
    "F17": "HYPOTHESIS", "F18": "HYPOTHESIS", "F19": "UNKNOWN", "F20": "HYPOTHESIS",
    "F21": "HYPOTHESIS", "F22": "UNKNOWN", "F23": "HYPOTHESIS", "F24": "UNKNOWN",
    "F25": "HYPOTHESIS", "F26": "HYPOTHESIS", "F27": "UNKNOWN", "F28": "UNKNOWN",
    "F29": "HYPOTHESIS", "F30": "HYPOTHESIS", "F31": "UNKNOWN", "F32": "UNKNOWN",
    "F33": "UNKNOWN", "F34": "UNKNOWN", "F35": "HYPOTHESIS", "F36": "UNKNOWN",
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
    for fid, (verdict, basis) in rows.items():
        bad = set(SOURCE_ID.findall(basis)) & PENDING_SOURCES
        if verdict == "SOURCE_FACT" and bad:
            errors.append(f"{fid}: SOURCE_FACT cites PENDING source {sorted(bad)}")
    return errors


# Callee (BIOS-side) rows: unverified behavior, never SOURCE_FACT.
CALLEE_ROWS = ["F02", "F07", "F10", "F12", "F13", "F16", "F18", "F19", "F22", "F24",
               "F27", "F28", "F31", "F32", "F33", "F34", "F36"]


def source_rows(text):
    """Return the section-3 source table rows (S1..S10)."""
    return [l for l in text.splitlines() if re.match(r"^\| S\d+ \|", l)]


def check_sources(text):
    """Every source row must be PENDING and not APPROVED; SOURCE_FACT needs an APPROVED source."""
    errors = []
    rows = source_rows(text)
    approved = set()
    for row in rows:
        sid = SOURCE_ID.search(row).group(0)
        if "APPROVED" in row:
            approved.add(sid)
            errors.append(f"{sid}: source is APPROVED without owner record")
        elif "PENDING" not in row:
            errors.append(f"{sid}: source row does not state PENDING")
    for fid, (verdict, basis) in parse_fields(text).items():
        if verdict == "SOURCE_FACT" and not (set(SOURCE_ID.findall(basis)) & approved):
            errors.append(f"{fid}: SOURCE_FACT cites no APPROVED source")
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

    def test_source_fact_definition_requires_approved_source(self):
        self.assertIn("APPROVED（人間が裁定済みで、承認者と日付がある）出典", self.text)

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

    def test_source_fact_on_pending_source_is_rejected(self):
        mutated = promote(self.text, "F01", "SOURCE_FACT")
        errors = check_pins(mutated)
        self.assertIn("F01: SOURCE_FACT cites PENDING source ['S1']", errors)

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

    def test_source_fact_without_approved_source_is_rejected(self):
        for basis_fid in ("F01", "F13", "F36"):
            mutated = promote(self.text, basis_fid, "SOURCE_FACT")
            self.assertTrue(any("APPROVED" in e for e in check_sources(mutated)), basis_fid)

    def test_release_gate_wording_is_negative_only(self):
        self.assertNotRegex(self.text, r"(BLOCKED_ABI.{0,10}解除|release_eligible=true)")

    def test_ledger_declares_zero_source_fact(self):
        self.assertIn("SOURCE_FACT は 0 件", self.text)
        self.assertIn("SOURCE_FACT 0、HYPOTHESIS 21、UNKNOWN 15", self.text)


if __name__ == "__main__":
    unittest.main()
