"""Guards docs/research/cd-pcmrd-contract.md (Issue #276).

Fails if a CD_PCMRD claim loses its label, a conflict is silently resolved,
an UNKNOWN is promoted, or the BLOCKED_ABI status is weakened. Checks the
document text only; it is not evidence about the System Card BIOS.
"""

import pathlib
import re
import unittest

DOC = pathlib.Path(__file__).resolve().parents[2] / "docs" / "research" / "cd-pcmrd-contract.md"
LABELS = ("SOURCE_FACT", "HYPOTHESIS", "UNKNOWN")
FORBIDDEN_LABELS = ("VERIFIED", "OBSERVED", "PASS")


def _claim_rows(text):
    # Section 4 rows: "| A1 | ... |" with the label in the 5th cell.
    rows = []
    for line in text.splitlines():
        cells = [c.strip() for c in line.split("|")]
        if len(cells) > 6 and cells[1].startswith("A") and cells[1][1:].isdigit():
            rows.append(cells)
    return rows


def _label(cell):
    # The label is the leading token of the label cell; words inside the
    # explanation (e.g. "SOURCE_FACT にしない") must not count.
    m = re.match(r"(SOURCE_FACT|HYPOTHESIS|UNKNOWN)(?![A-Za-z_])", cell)
    return m.group(1) if m else None


class CdPcmrdContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = DOC.read_text(encoding="utf-8")

    def test_status_stays_blocked(self):
        self.assertIn("BLOCKED_ABI", self.text)
        self.assertIn("release_eligible=false", self.text)
        self.assertIn("DEV_CALLABLE なし", self.text)
        self.assertNotIn("DEV_CALLABLE: yes", self.text)

    def test_every_claim_row_has_one_of_the_allowed_labels(self):
        rows = _claim_rows(self.text)
        self.assertGreaterEqual(len(rows), 11)
        for cells in rows:
            label_cell = cells[4]
            self.assertIn(_label(label_cell), LABELS, cells[1])
            for bad in FORBIDDEN_LABELS:
                self.assertNotIn(bad, label_cell, cells[1])

    def test_unknown_fields_are_not_promoted(self):
        # Pinned dispositions: a silent UNKNOWN -> SOURCE_FACT promotion must fail.
        expected = {
            "A1": "SOURCE_FACT", "A2": "HYPOTHESIS",
            "A3": "UNKNOWN", "A5": "UNKNOWN", "A6": "UNKNOWN", "A7": "UNKNOWN",
            "A8": "UNKNOWN", "A9": "UNKNOWN", "A10": "UNKNOWN", "A11": "UNKNOWN",
        }
        found = {cells[1]: cells[4] for cells in _claim_rows(self.text)}
        for cid, label in expected.items():
            self.assertEqual(_label(found[cid]), label, (cid, found[cid]))
        self.assertEqual(_label(found["A4"]), "UNKNOWN")  # C1 stays open: no source fact claimed for X/Y output

    def test_source_ids_match_the_evidence_rules(self):
        # A2 must not rest on the unpinned Zeograd (S5) or the HLE Hu-Go (S7) source.
        sources = {cells[1]: cells[5] for cells in _claim_rows(self.text)}
        self.assertNotIn("S6", sources["A1"])
        for sid in ("S5", "S7"):
            self.assertNotIn(sid, sources["A2"], sid)

    def test_conflicts_remain_unresolved(self):
        for cid in ("C1", "C2", "C3", "C4"):
            self.assertIn(f"**{cid}", self.text, cid)
        self.assertIn("conflict unresolved", self.text)
        self.assertIn("いずれの矛盾も、本書では解決していない", self.text)
        for cells in _claim_rows(self.text):
            if cells[1] in ("A2", "A4", "A5"):
                self.assertIn("conflict unresolved", cells[3], cells[1])

    def test_callee_and_hle_not_used_as_abi_evidence(self):
        self.assertIn("ABI の証拠ではない", self.text)
        self.assertIn("HLE", self.text)
        self.assertIn("NOT_RUN", self.text)


if __name__ == "__main__":
    unittest.main()
