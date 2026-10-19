"""Host tests for the Issue #249 CD_SEARCH ($07, $E015) caller-ABI ledger.

These tests check the ledger's rules only: the verdict vocabulary, no VERIFIED / OBSERVED / PASS,
callee rows never promoted above HYPOTHESIS, UNKNOWN values never filled, HLE rows labelled as HLE,
no JSR to a table address, and the BLOCKED_ABI / release_eligible=false status. Nothing here runs a
System Card callee, and nothing here is evidence of BIOS behaviour.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "research" / "cd-search-contract.md"

FIELDS = 7  # ID | 側 | 項目 | 判定 | 値 | 出典 | 理由・確度
VERDICTS = {"SOURCE_FACT", "HYPOTHESIS", "UNKNOWN"}
FORBIDDEN_WORDS = re.compile(r"\b(VERIFIED|OBSERVED|PASS)\b")
ROW_ID = re.compile(r"[A-Z]+\d+")

# Expected verdict for every row. A row that is promoted (for example C2 to SOURCE_FACT) fails.
EXPECTED = {
    "K1": "SOURCE_FACT", "K2": "SOURCE_FACT", "K3": "HYPOTHESIS", "K4": "SOURCE_FACT",
    "K5": "SOURCE_FACT", "K6": "HYPOTHESIS", "K7": "SOURCE_FACT", "K8": "UNKNOWN",
    "H1": "SOURCE_FACT", "H2": "SOURCE_FACT", "H3": "SOURCE_FACT", "H4": "SOURCE_FACT",
    "C1": "HYPOTHESIS", "C2": "HYPOTHESIS", "C3": "HYPOTHESIS", "C4": "UNKNOWN",
    "C5": "UNKNOWN", "C6": "UNKNOWN", "C7": "UNKNOWN", "C8": "UNKNOWN",
    "C9": "UNKNOWN", "C10": "UNKNOWN", "C11": "UNKNOWN", "C12": "UNKNOWN", "C13": "UNKNOWN",
    "M1": "SOURCE_FACT", "M2": "HYPOTHESIS", "M3": "SOURCE_FACT", "M4": "HYPOTHESIS",
    "M5": "UNKNOWN",
}


def _cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _bare(value):
    return value.strip().strip("`").strip()


def parse_rows(text):
    rows = {}
    for line in text.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = _cells(line)
        if len(cells) == FIELDS and ROW_ID.fullmatch(cells[0]):
            if cells[0] in rows:
                raise AssertionError(f"duplicate row {cells[0]}")
            rows[cells[0]] = dict(zip(["id", "side", "item", "verdict", "value", "source", "reason"], cells))
    return rows


class LedgerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = DOC.read_text(encoding="utf-8")
        cls.rows = parse_rows(cls.text)

    def test_exact_row_set(self):
        self.assertEqual(set(self.rows), set(EXPECTED))

    def test_verdicts_are_pinned_and_in_vocabulary(self):
        for rid, want in EXPECTED.items():
            verdict = self.rows[rid]["verdict"]
            self.assertIn(verdict, VERDICTS, rid)
            self.assertEqual(verdict, want, f"{rid} promoted or demoted")

    def test_callee_side_never_source_fact(self):
        for rid, r in self.rows.items():
            if r["side"] == "C":
                self.assertNotEqual(r["verdict"], "SOURCE_FACT", rid)

    def test_unknown_values_are_not_filled(self):
        for rid, r in self.rows.items():
            if r["verdict"] == "UNKNOWN":
                self.assertEqual(_bare(r["value"]), "UNKNOWN", rid)

    def test_sourced_rows_cite_a_source_id(self):
        for rid, r in self.rows.items():
            if r["verdict"] in ("SOURCE_FACT", "HYPOTHESIS"):
                self.assertRegex(r["source"], r"S\d", rid)

    def test_hle_rows_are_labelled_hle(self):
        for rid, r in self.rows.items():
            if r["side"] == "H":
                self.assertIn("HLE", r["reason"], rid)

    def test_m1_source_fact_cites_huc_only(self):
        self.assertEqual(self.rows["M1"]["source"], "S1 475")
        self.assertIn("E015", self.rows["M1"]["value"])

    def test_forbidden_status_words_only_outside_table_rows(self):
        for line in self.text.splitlines():
            if FORBIDDEN_WORDS.search(line):
                self.assertFalse(line.lstrip().startswith("|"), line)

    def test_no_jsr_to_a_table_address(self):
        self.assertIsNone(re.search(r"\bJSR\s+\$[0-9A-Fa-f]{4}", self.text))

    def test_blocked_abi_and_release_gate_stay_closed(self):
        self.assertIn("BLOCKED_ABI", self.text)
        self.assertIn("release_eligible=false", self.text)
        self.assertNotIn("release_eligible=true", self.text)


if __name__ == "__main__":
    unittest.main()
