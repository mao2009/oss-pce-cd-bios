"""docs/cd-read-contract.md section 2.1 must state the effective status that tools/cd_read_contract.py returns."""
import re
import unittest
from pathlib import Path

from tools.cd_read_contract import TRANSPORT_CLAIMS, compat_status, effective_status

DOC = Path(__file__).resolve().parents[2] / "docs" / "cd-read-contract.md"


def _section_2_1() -> str:
    text = DOC.read_text(encoding="utf-8")
    start = text.index("### 2.1 ")
    return text[start:text.index("### 2.2 ", start)]


class CdReadStateTableTest(unittest.TestCase):
    def test_effective_column_matches_code_without_bundle(self):
        section = _section_2_1()
        rows = dict(re.findall(r"^\| (T\d) \| VERIFIED \| (\w+) \|", section, flags=re.M))
        self.assertEqual(set(rows), {"T1", "T2", "T3", "T4"})
        code = effective_status(None, None)
        for cid, doc_value in rows.items():
            self.assertEqual(doc_value, code[cid], cid)
            self.assertEqual(doc_value, "BLOCKED", cid)

    def test_no_not_verified_term_in_state_section(self):
        self.assertNotIn("NOT_VERIFIED", _section_2_1())

    def test_compat_status_never_returns_verified(self):
        self.assertNotIn("VERIFIED", compat_status(None, None).values())
        self.assertEqual(set(compat_status(None, None).values()), {"BLOCKED", "HYPOTHESIS", "UNKNOWN"})
        self.assertEqual(len(TRANSPORT_CLAIMS), len(compat_status(None, None)))


if __name__ == "__main__":
    unittest.main()
