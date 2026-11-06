"""SOURCE_FACT safety rules for the AD_RESET ledger, on synthetic rows only.

The ledger document is withdrawn (owner ruling, research documents removed). The rules below still apply to any
ledger row: a BIOS-side row is never SOURCE_FACT, and the SOURCE_FACT definition never claims approval.
"""
import unittest


def bios_side_source_facts(rows):
    """Row ids of BIOS-side rows (suffix b) whose confidence is SOURCE_FACT. Must be empty."""
    return [i for i, c in rows if i.endswith("b") and c == "SOURCE_FACT"]


def definition_claims_approval(paragraph):
    return "APPROVED" in paragraph


class SourceFactRules(unittest.TestCase):
    def test_a_bios_side_row_marked_source_fact_is_refused(self):
        self.assertEqual(bios_side_source_facts([("S1b", "SOURCE_FACT")]), ["S1b"])

    def test_a_bios_side_row_marked_hypothesis_or_unknown_is_accepted(self):
        self.assertEqual(bios_side_source_facts([("S1b", "HYPOTHESIS"), ("S2b", "UNKNOWN")]), [])

    def test_a_caller_side_row_may_be_source_fact(self):
        self.assertEqual(bios_side_source_facts([("S3", "SOURCE_FACT")]), [])

    def test_a_definition_that_claims_approval_is_refused(self):
        self.assertTrue(definition_claims_approval("SOURCE_FACT is APPROVED for use"))

    def test_a_definition_without_an_approval_claim_is_accepted(self):
        self.assertFalse(definition_claims_approval("SOURCE_FACT is independent of provenance"))


if __name__ == "__main__":
    unittest.main()
