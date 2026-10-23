"""Public docs must match the implementation ledger, the source tree and the current snapshot (Issue #507).

Fails when a README, status/roadmap/pages doc, Pages page or llms.txt keeps a stale count,
an old snapshot commit, or a PR state that has since changed.

Only CURRENT statements are checked for staleness. A line that carries an ISO date and an explicit
historical marker (e.g. "2026-10-22 ... historical", "superseded", "dated history") is a dated record
and is exempt. Merged-PR state is checked against a fixed list, not the live GitHub API.
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
# Known-merged PRs as of 2026-10-23 (checked with `gh pr view`). Fixed list on purpose: tests stay offline.
MERGED_PRS = (475, 477, 495, 502, 506)
STALE_PATTERNS = [
    r"\b8/81\b",  # any current 8/81 (the 2026-10-22 snapshot figure) is stale
    r"8/81 DEV on main", r"main alone is 8/81", r"main:? 73/81",
    r"until (Issue )?#494", r"not yet in main", r"counts? (include|counted) (it )?once merged",
]
MERGED_PR_RE = re.compile(r"(?:#|pull/)(?:%s)\b" % "|".join(str(n) for n in MERGED_PRS))
OPEN_WORDING_RE = re.compile(r"unmerged|未マージ|\bDraft\b|\bopen\b", re.IGNORECASE)
DATE_RE = re.compile(r"\b20\d\d-\d\d-\d\d\b")
HISTORICAL_RE = re.compile(r"historical|superseded|dated history|earlier snapshot", re.IGNORECASE)


def is_historical(line):
    return bool(DATE_RE.search(line) and HISTORICAL_RE.search(line))


def stale_hits(text):
    """Return (line_no, reason) for every current (non-historical) line that carries a stale claim."""
    hits = []
    for number, line in enumerate(text.splitlines(), 1):
        if is_historical(line):
            continue
        for pattern in STALE_PATTERNS:
            if re.search(pattern, line):
                hits.append((number, pattern))
        if MERGED_PR_RE.search(line) and OPEN_WORDING_RE.search(line):
            hits.append((number, "merged PR described as open/unmerged/Draft"))
    return hits


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
            with self.subTest(doc=name):
                self.assertEqual(stale_hits(text), [], f"stale claim in {name}")

    def test_detector_flags_reintroduced_8_81_and_unmerged_wording(self):
        self.assertTrue(stale_hits("Current main: 8/81 DEV implementations."))
        self.assertTrue(stale_hits("- DEV count 8/81"))
        self.assertTrue(stale_hits("PR #475 is unmerged and still a Draft."))
        self.assertTrue(stale_hits("[#495](https://github.com/x/pull/495) is open Draft work."))
        self.assertTrue(stale_hits("PR #506 (未マージ) wires CI."))

    def test_detector_allows_dated_history_and_current_merged_wording(self):
        self.assertEqual(stale_hits("2026-10-22 historical snapshot: 8/81 DEV."), [])
        self.assertEqual(stale_hits("Earlier 6/81, 7/81 and the 2026-10-22 8/81 reports are dated history."), [])
        self.assertEqual(stale_hits("- [#495] MA_MUL16S is MERGED (2026-10-22); #494 CLOSED."), [])
        self.assertEqual(stale_hits("Issue #476 remains OPEN, BLOCKED."), [])


if __name__ == "__main__":
    unittest.main()
