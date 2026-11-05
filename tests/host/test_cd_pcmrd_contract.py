"""Claim-label rules for the CD_PCMRD (slot $0E) contract, on synthetic rows and on the real gate.

The research document is withdrawn (owner ruling). The rules below still apply to any claim table: every claim has
one allowed label, a forbidden label is never used, and an UNKNOWN is never promoted to SOURCE_FACT. The slot's
release state is checked on the real gate.
"""
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import reference_jsr_gate as gate  # noqa: E402

LABELS = ("SOURCE_FACT", "HYPOTHESIS", "UNKNOWN")
FORBIDDEN_LABELS = ("VERIFIED", "OBSERVED", "PASS")
PCMRD_SLOT = 14  # the contract ledger's CD_PCMRD slot


def label_of(cell):
    """The leading label token of a cell; words inside an explanation do not count."""
    m = re.match(r"(SOURCE_FACT|HYPOTHESIS|UNKNOWN)(?![A-Za-z_])", cell)
    return m.group(1) if m else None


def label_violations(rows):
    """rows: (claim_id, label_cell). Returns a list of problems; empty means every label is allowed."""
    problems = []
    for cid, cell in rows:
        if label_of(cell) not in LABELS:
            problems.append(f"{cid}: label not allowed")
        for bad in FORBIDDEN_LABELS:
            if bad in cell:
                problems.append(f"{cid}: forbidden label {bad}")
    return problems


def promotion_violations(before, after):
    """before/after: claim_id -> label. An UNKNOWN that becomes SOURCE_FACT with no new source is a problem."""
    return [cid for cid, label in before.items()
            if label == "UNKNOWN" and after.get(cid) == "SOURCE_FACT"]


class ClaimLabelRules(unittest.TestCase):
    def test_allowed_labels_pass(self):
        self.assertEqual(label_violations([("A1", "SOURCE_FACT"), ("A2", "HYPOTHESIS"), ("A3", "UNKNOWN")]), [])

    def test_a_label_with_an_explanation_still_counts_by_its_leading_token(self):
        self.assertEqual(label_violations([("A4", "UNKNOWN（SOURCE_FACT にしない）")]), [])

    def test_a_forbidden_label_is_refused(self):
        for bad in ("VERIFIED", "OBSERVED", "PASS"):
            with self.subTest(bad=bad):
                self.assertTrue(label_violations([("A5", bad)]))

    def test_an_unknown_promoted_to_source_fact_is_refused(self):
        self.assertEqual(promotion_violations({"A6": "UNKNOWN"}, {"A6": "SOURCE_FACT"}), ["A6"])

    def test_an_unknown_that_stays_unknown_is_accepted(self):
        self.assertEqual(promotion_violations({"A6": "UNKNOWN"}, {"A6": "UNKNOWN"}), [])


class PcmrdGateState(unittest.TestCase):
    def test_the_real_gate_keeps_the_pcmrd_slot_blocked_and_not_released(self):
        ledger, contracts = gate.load()
        record = gate.resolve(PCMRD_SLOT, ledger, contracts)
        self.assertEqual(record["execution"], "BLOCKED")
        self.assertIs(record["release_eligible"], False)
        self.assertIs(record["execution_permitted"], False)


if __name__ == "__main__":
    unittest.main()
