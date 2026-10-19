"""Host tests for the CD_FADE contract ledger (Issue #282).

These check the ledger's wording rules only. They do not execute or verify the
System Card CD_FADE callee.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "research" / "cd-fade-contract.md"

ALLOWED = {"SOURCE_FACT", "HYPOTHESIS", "UNKNOWN"}
FORBIDDEN = re.compile(r"\b(OBSERVED|VERIFIED|PASS|DEV_CALLABLE)\b")
FIELD_ROW = re.compile(r"^\| (F\d{2}) \|")


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

    def test_release_gate_stays_closed(self):
        self.assertIn("BLOCKED_ABI", self.text)
        self.assertIn("release_eligible=false", self.text)


if __name__ == "__main__":
    unittest.main()
