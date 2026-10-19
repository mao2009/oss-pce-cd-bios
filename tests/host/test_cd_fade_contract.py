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
FORBIDDEN = re.compile(r"\b(OBSERVED|VERIFIED|PASS|DEV_CALLABLE)\b")
FIELD_ROW = re.compile(r"^\| (F\d{2}) \|")
SOURCE_ID = re.compile(r"\bS\d+\b")
PENDING_SOURCES = {"S6", "S7", "S8", "S9"}
EXPECTED_COUNTS = {"SOURCE_FACT": 16, "HYPOTHESIS": 6, "UNKNOWN": 14}

# Pinned verdict per field row. A verdict change must edit this table in review.
EXPECTED = {
    "F01": "SOURCE_FACT", "F02": "UNKNOWN", "F03": "SOURCE_FACT", "F04": "SOURCE_FACT",
    "F05": "SOURCE_FACT", "F06": "HYPOTHESIS", "F07": "UNKNOWN", "F08": "HYPOTHESIS",
    "F09": "SOURCE_FACT", "F10": "HYPOTHESIS", "F11": "HYPOTHESIS", "F12": "UNKNOWN",
    "F13": "UNKNOWN", "F14": "SOURCE_FACT", "F15": "HYPOTHESIS", "F16": "UNKNOWN",
    "F17": "SOURCE_FACT", "F18": "HYPOTHESIS", "F19": "UNKNOWN", "F20": "SOURCE_FACT",
    "F21": "SOURCE_FACT", "F22": "UNKNOWN", "F23": "SOURCE_FACT", "F24": "UNKNOWN",
    "F25": "SOURCE_FACT", "F26": "SOURCE_FACT", "F27": "UNKNOWN", "F28": "UNKNOWN",
    "F29": "SOURCE_FACT", "F30": "SOURCE_FACT", "F31": "UNKNOWN", "F32": "UNKNOWN",
    "F33": "UNKNOWN", "F34": "UNKNOWN", "F35": "SOURCE_FACT", "F36": "SOURCE_FACT",
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
        self.assertIn("BLOCKED_ABI", self.text)
        self.assertIn("release_eligible=false", self.text)


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


if __name__ == "__main__":
    unittest.main()
