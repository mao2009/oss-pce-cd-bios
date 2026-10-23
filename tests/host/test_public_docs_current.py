"""Public docs must match the implementation ledger, the source tree and the current snapshot (Issue #507).

Fails when a README, status/roadmap/pages doc, Pages page or llms.txt keeps a stale count,
an old snapshot commit, or a PR state that has since changed.

Only CURRENT statements are checked for staleness. The single exemption is a dated record:
a Markdown bullet that starts with an ISO date (e.g. "- 2026-10-22 snapshot: 8/81 DEV.") inside a
heading whose title contains "History" or "履歴". A date or the word "historical" on an ordinary
line never exempts it. Merged-PR state is checked against a fixed list, not the live GitHub API.

Counts are derived from spec/dev_implementations.json. Every current "N/81" or "N stub" must equal
a derived value, and each public doc must state the DEV, stub, approval, ABI and JSR claims on a
labelled line. A bare "9/81" on its own does not satisfy the check.
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
# Known-merged PRs and closed issues as of 2026-10-23 (checked with `gh pr view`). Fixed list: tests stay offline.
MERGED_PRS = (475, 477, 494, 495, 502, 506)
STALE_PATTERNS = [
    r"\b8/81\b",  # any current 8/81 (the 2026-10-22 snapshot figure) is stale
    r"8/81 DEV on main", r"main alone is 8/81", r"main:? 73/81",
    r"until (Issue )?#494", r"not yet in main", r"counts? (include|counted) (it )?once merged",
]
MERGED_PR_RE = re.compile(r"(?:#|pull/)(?:%s)\b" % "|".join(str(n) for n in MERGED_PRS))
OPEN_WORDING_RE = re.compile(r"unmerged|未マージ|\bDraft\b|\bopen\b", re.IGNORECASE)
DATED_BULLET_RE = re.compile(r"^\s*[-*]\s+\**\s*20\d\d-\d\d-\d\d\b")
HISTORY_HEADING_RE = re.compile(r"^\s*#{1,6}\s.*(history|履歴)", re.IGNORECASE)
COUNT_RE = re.compile(r"\b(\d+)/81\b")
STUB_COUNT_RE = re.compile(r"\b(\d+)\s*(?:remaining\s+|other\s+|nonreturning\s+)?(?:stubs?|スタブ)", re.IGNORECASE)


def expected_counts(slots):
    """Current public counts, derived from the implementation ledger rather than typed in."""
    implemented = sum(1 for slot in slots if slot.get("implementation"))
    return {
        "dev": implemented,
        "stub": len(slots) - implemented,
        "approved": sum(1 for slot in slots if slot["approval"]["approved_by"]),
        "abi": sum(1 for slot in slots if slot["abi_contract"].get("status") == "VERIFIED"),
        "jsr": 0,  # the ledger has no per-slot JSR field; reference_jsr_gate stays fail-closed (0/81)
    }


LEDGER = json.loads((ROOT / "spec" / "dev_implementations.json").read_text(encoding="utf-8"))
EXPECTED = expected_counts(LEDGER["slots"])


def normalized_lines(text):
    """Strip HTML tags and join 'N / 81' into 'N/81' so Pages markup is read like Markdown."""
    flat = re.sub(r"<[^>]+>", "", text)
    return [re.sub(r"\s*/\s*81\b", "/81", line) for line in flat.splitlines()]


def stale_hits(text, expected=EXPECTED):
    """Return (line_no, reason) for every current line that carries a stale or wrong claim."""
    hits = []
    in_history = False
    allowed = set(expected.values())
    for number, line in enumerate(normalized_lines(text), 1):
        if re.match(r"^\s*#{1,6}\s", line):
            in_history = bool(HISTORY_HEADING_RE.match(line))
        if in_history and DATED_BULLET_RE.match(line):
            continue  # dated record under a History heading: exempt
        for pattern in STALE_PATTERNS:
            if re.search(pattern, line):
                hits.append((number, pattern))
        for match in COUNT_RE.finditer(line):
            if int(match.group(1)) not in allowed:
                hits.append((number, f"count {match.group(0)} is not derived from the ledger"))
        for match in STUB_COUNT_RE.finditer(line):
            if int(match.group(1)) != expected["stub"]:
                hits.append((number, f"stub count {match.group(1)} != ledger {expected['stub']}"))
        if MERGED_PR_RE.search(line) and OPEN_WORDING_RE.search(line):
            hits.append((number, "merged PR or closed issue described as open/unmerged/Draft"))
    return hits


def missing_claims(text, expected=EXPECTED):
    """Return the labelled claims a public doc fails to state with the ledger-derived value."""
    lines = normalized_lines(text)
    claims = {
        "DEV implementations": (r"DEV|実装", rf"(?<!\d){expected['dev']}/81\b"),
        "stubs": (r"stub|スタブ", rf"(?<!\d){expected['stub']}(?![\d/])"),
        "DEV_CALLABLE approval": (r"DEV_CALLABLE|承認", rf"(?<![\d/]){expected['approved']}(?![\d/])"),
        "reference ABI VERIFIED": (r"ABI", rf"(?<!\d){expected['abi']}/81\b"),
        "reference JSR permitted": (r"JSR", rf"(?<!\d){expected['jsr']}/81\b"),
    }
    missing = []
    for name, (label, value) in claims.items():
        if not any(re.search(label, line, re.IGNORECASE) and re.search(value, line) for line in lines):
            missing.append(name)
    return missing


class PublicDocsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ledger = LEDGER
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

    def test_every_public_doc_states_ledger_claims_and_snapshot(self):
        for name, text in self.docs.items():
            with self.subTest(doc=name):
                self.assertIn(SNAPSHOT, text, "snapshot commit is not cited")
                self.assertEqual(missing_claims(text), [], f"{name} does not state these ledger claims")

    def test_no_stale_or_wrong_claims(self):
        for name, text in self.docs.items():
            with self.subTest(doc=name):
                self.assertEqual(stale_hits(text), [], f"stale or wrong claim in {name}")

    def test_detector_flags_current_8_81_and_unmerged_wording(self):
        self.assertTrue(stale_hits("Current main: 8/81 DEV implementations."))
        self.assertTrue(stale_hits("- DEV count 8/81"))
        self.assertTrue(stale_hits("Current DEV count: 8/81."))
        self.assertTrue(stale_hits("PR #475 is unmerged and still a Draft."))
        self.assertTrue(stale_hits("[#495](https://github.com/x/pull/495) is open Draft work."))
        self.assertTrue(stale_hits("PR #506 (未マージ) wires CI."))
        self.assertTrue(stale_hits("Issue #494 is unmerged and still open."))
        self.assertTrue(stale_hits("#494 は未マージです。"))

    def test_date_or_historical_word_on_same_line_does_not_exempt(self):
        self.assertTrue(stale_hits("2026-10-22 historical note: DEV count is 8/81."))
        self.assertTrue(stale_hits("2026-10-22 historical snapshot: 8/81 DEV."))
        self.assertTrue(stale_hits("Earlier 6/81 and the 2026-10-22 8/81 report are dated history."))
        self.assertTrue(stale_hits("- 2026-10-22 historical note: DEV count is 8/81. (9/81 now)\n## History"))

    def test_dated_bullet_under_history_heading_is_exempt(self):
        text = "## Status\n\n- Current: 9/81.\n\n## History\n\n- 2026-10-22 snapshot: 8/81 DEV (historical).\n"
        self.assertEqual(stale_hits(text), [])
        self.assertTrue(stale_hits("## Status\n\n- 2026-10-22 snapshot: 8/81 DEV (historical).\n"))

    def test_current_merged_wording_is_allowed(self):
        self.assertEqual(stale_hits("- [#495] MA_MUL16S is MERGED (2026-10-22); #494 CLOSED."), [])
        self.assertEqual(stale_hits("Issue #476 remains OPEN, BLOCKED."), [])


class PublicDocsMutationTest(unittest.TestCase):
    """Mutations of a passing claim block. Each must be caught, or the check is too weak."""

    CLEAN = (
        "Status: DEV implementation count: 9/81, 72 stubs.\n"
        "owner-approved DEV_CALLABLE: 0; reference ABI VERIFIED: 0/81; reference JSR permitted: 0/81.\n"
        "Measured on `f90c16b`.\n"
    )

    def test_clean_block_passes(self):
        self.assertEqual(stale_hits(self.CLEAN), [])
        self.assertEqual(missing_claims(self.CLEAN), [])

    def test_wrong_current_dev_count_is_flagged(self):
        mutant = self.CLEAN.replace("9/81", "10/81")
        self.assertTrue(stale_hits(mutant))
        self.assertIn("DEV implementations", missing_claims(mutant))

    def test_current_dev_count_label_with_wrong_value_is_flagged(self):
        self.assertTrue(stale_hits("Current DEV count: 10/81."))

    def test_wrong_stub_count_is_flagged(self):
        self.assertTrue(stale_hits("Status: 73 stubs remain."))
        self.assertTrue(stale_hits("Status: 71 remaining stub slots."))
        self.assertEqual(stale_hits("72 remaining stub slots"), [])

    def test_bare_9_81_does_not_satisfy_claims(self):
        self.assertEqual(stale_hits("9/81"), [])
        self.assertEqual(
            set(missing_claims("9/81")),
            {"DEV implementations", "stubs", "DEV_CALLABLE approval", "reference ABI VERIFIED",
             "reference JSR permitted"},
        )

    def test_removing_abi_claim_is_flagged(self):
        mutant = self.CLEAN.replace("reference ABI VERIFIED: 0/81; ", "")
        self.assertEqual(missing_claims(mutant), ["reference ABI VERIFIED"])

    def test_removing_jsr_claim_is_flagged(self):
        mutant = self.CLEAN.replace("; reference JSR permitted: 0/81", "")
        self.assertEqual(missing_claims(mutant), ["reference JSR permitted"])

    def test_html_wrapped_count_is_read(self):
        html = "<div><span>9</span> / 81</div> DEV implemented slots <span>0</span>/81 ABI VERIFIED"
        self.assertNotIn("DEV implementations", missing_claims(html))
        self.assertTrue(stale_hits("<span>10</span> / 81 DEV implemented"))

    def test_expected_counts_follow_the_ledger_not_a_typed_constant(self):
        slots = json.loads(json.dumps(LEDGER["slots"]))
        slots[1]["implementation"] = {"source": "src/overrides/slot_XX.s"}
        expected = expected_counts(slots)
        self.assertEqual(expected["dev"], 10)
        self.assertEqual(expected["stub"], 71)
        self.assertTrue(stale_hits(self.CLEAN, expected))

    def test_approval_and_abi_status_are_read_from_ledger(self):
        slots = json.loads(json.dumps(LEDGER["slots"]))
        slots[0]["approval"]["approved_by"] = "owner"
        slots[2]["abi_contract"]["status"] = "VERIFIED"
        expected = expected_counts(slots)
        self.assertEqual(expected["approved"], 1)
        self.assertEqual(expected["abi"], 1)
        self.assertEqual(set(missing_claims(self.CLEAN, expected)), {"DEV_CALLABLE approval", "reference ABI VERIFIED"})


if __name__ == "__main__":
    unittest.main()
