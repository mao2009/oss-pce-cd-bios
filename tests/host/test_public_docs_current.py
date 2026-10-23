"""Public docs must match the implementation ledger, the source tree and the current snapshot (Issue #507).

Fails when a README, status/roadmap/pages doc, Pages page or llms.txt keeps a stale count,
an old snapshot commit, or a PR state that has since changed.
"""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = "f90c16b"  # current main snapshot cited by every public doc; update all of them together
PUBLIC_DOCS = [
    "README.md", "docs/status.md", "docs/roadmap.md", "docs/pages.md",
    "site/index.html", "site/ja/index.html", "site/llms.txt",
]
STALE_PATTERNS = [
    r"main:? 8/81", r"8/81 DEV on main", r"main alone is 8/81", r"main:? 73/81",
    r"until (Issue )?#494", r"not yet in main", r"counts? (include|counted) (it )?once merged",
    r"#47[57][^\n]{0,80}\bDraft\b",
]


class PublicDocsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ledger = json.loads((ROOT / "spec" / "dev_implementations.json").read_text(encoding="utf-8"))
        cls.docs = {name: (ROOT / name).read_text(encoding="utf-8") for name in PUBLIC_DOCS}

    def test_ledger_counts_match_source_overrides(self):
        slots = self.ledger["slots"]
        implemented = {slot["id"] for slot in slots if slot.get("implementation")}
        source = {int(path.stem.split("_", 1)[1], 16) for path in (ROOT / "src" / "overrides").glob("slot_*.s")}
        self.assertEqual(len(slots), 81)
        self.assertEqual(len(implemented), 9)
        self.assertEqual(len(slots) - len(implemented), 72)
        self.assertEqual(source, implemented, "src/overrides must match the implemented ledger slots")
        self.assertEqual(sum(1 for slot in slots if slot["approval"]["approved_by"]), 0)
        self.assertIs(self.ledger["release_eligible"], False)

    def test_every_public_doc_cites_current_snapshot_and_counts(self):
        for name, text in self.docs.items():
            with self.subTest(doc=name):
                self.assertIn(SNAPSHOT, text, "snapshot commit is not cited")
                self.assertIn("9/81", text)
                self.assertIn("72", text)
                self.assertIn("0/81", text, "reference ABI VERIFIED count must be stated")

    def test_no_stale_claims(self):
        for name, text in self.docs.items():
            for pattern in STALE_PATTERNS:
                with self.subTest(doc=name, pattern=pattern):
                    self.assertIsNone(re.search(pattern, text, re.MULTILINE), f"stale claim in {name}")


if __name__ == "__main__":
    unittest.main()
