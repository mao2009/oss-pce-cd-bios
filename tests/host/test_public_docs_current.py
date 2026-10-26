"""Public docs must match the implementation ledger, the source tree and the current snapshot (Issue #507).

Fails when a README, status/roadmap/pages doc, Pages page or llms.txt keeps a stale count,
an old snapshot commit, or a PR state that has since changed.

Only CURRENT statements are checked for staleness. Two exemptions exist:
- a Markdown bullet that starts with an ISO date (e.g. "- 2026-10-22 snapshot: 8/81 DEV.") inside a
  heading whose title contains "History" or "履歴". A date or the word "historical" on an ordinary line never exempts it.
- a labelled count that equals the value a named PR had when it merged, inside a parenthesised or bracketed
  note that says "historical" and cites that PR (HISTORICAL_CLAIMS). Only the note's own count is exempt;
  a current count on the same line is still checked.

Counts are paired with their label, never matched by presence: "reference ABI VERIFIED: 9/81" is a claim
that ABI is 9, not a DEV count. Each claim is checked against a value derived from the ledger
(spec/dev_implementations.json) and the JSR gate (spec/reference_jsr_gate.json). Merged-PR state is checked
against a fixed list, not the live GitHub API.
"""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# SNAPSHOT is the fixed 2026-10-22 main snapshot cited by every public doc. EXPECTED below is derived from the
# ledger of the checked-out commit, so it describes that snapshot, not live main. Live main may have moved on;
# its state is re-verified with `gh` before a doc is updated, never inferred from this test.
SNAPSHOT = "f90c16b"  # current main snapshot cited by every public doc; update all of them together
PUBLIC_DOCS = [
    "README.md", "docs/status.md", "docs/roadmap.md", "docs/pages.md",
    "site/index.html", "site/ja/index.html", "site/llms.txt",
]
# Known-merged PRs and closed issues as of 2026-10-23 (checked with `gh pr view`). Fixed list: tests stay offline.
MERGED_PRS = (475, 477, 494, 495, 502, 506, 508, 516)
# Counts a PR or commit recorded when it was current. Exempt only on a historical line that cites that PR/commit
# with the same value. A date alone never qualifies.
HISTORICAL_CLAIMS = {"#174": {"stub": 80}, "86172e2": {"dev": 8}, "8a136ad": {"dev": 8}}
STALE_PATTERNS = [
    r"\b8/81\b",  # any current 8/81 (the 2026-10-22 snapshot figure) is stale
    r"8/81 DEV on main", r"main alone is 8/81", r"main:? 73/81",
    r"until (Issue )?#494", r"not yet in main", r"counts? (include|counted) (it )?once merged",
]
MERGED_PR_RE = re.compile(r"(?:#|pull/)(?:%s)\b" % "|".join(str(n) for n in MERGED_PRS))
OPEN_WORDING_RE = re.compile(r"unmerged|未マージ|\bDraft\b|\bopen\b", re.IGNORECASE)
DATED_BULLET_RE = re.compile(r"^\s*[-*]\s+\**\s*20\d\d-\d\d-\d\d\b")
HISTORY_HEADING_RE = re.compile(r"^\s*#{1,6}\s.*(history|履歴)", re.IGNORECASE)
HISTORICAL_WORD_RE = re.compile(r"historical|superseded|履歴", re.IGNORECASE)
# A number that is not part of a date, URL, PR/issue reference, hex id or identifier like $4F / MA_MUL16S.
VALUE_RE = re.compile(r"(?<![A-Za-z0-9_/.#$-])(\d+)(?!\d)(?!-|\.\d)(?!\s*(?:KiB|KB|MiB|MB))(\s*/\s*81(?!\d))?")

# PR reference (any number, not just merged ones). Used to decide whether another PR
# mention falls between a merged PR and its open/unmerged/Draft wording. Only matches "#NNNN"
# form; "pull/NNNN" inside URLs (e.g. https://.../pull/495) must not count as a separator.
# Issue references (#NNNN that appear as "Issue #NNNN") are NOT PR references for the purpose of
# separating PR state clauses.
_ANY_PR_RE = re.compile(r"#\d+(?!\d)")
_ISSUE_REF_RE = re.compile(r"(?:Issue|issue|課題)\s+#\d+|#\d+\s+\([^)]+\)", re.IGNORECASE)
# Text allowed between a label and its number: short, and no sentence or clause punctuation.
GAP_RE = re.compile(r"[^.;,()\[\]<>。、；，（）]{0,30}")
# Each claim label and the claim key it asserts. Matched case-insensitively.
LABELS = [
    ("dev", re.compile(r"DEV[\s-]*(?:only\s+|ROM\s+)?(?:count|implement\w*)|DEV実装|DEV 実装", re.I)),
    ("stub", re.compile(r"stubs?\b|スタブ", re.I)),
    ("approval", re.compile(r"DEV_CALLABLE|owner[\s-]*approv\w*", re.I)),
    ("abi", re.compile(r"ABI(?![-\d]|[A-Za-z])", re.I)),  # ABI-1 is a review name, not the ABI count
    ("jsr", re.compile(r"(?<![A-Za-z_])JSR(?![A-Za-z_])", re.I)),
]
NAMES = {
    "dev": "DEV implementations", "stub": "stubs", "approval": "DEV_CALLABLE approval",
    "abi": "reference ABI VERIFIED", "jsr": "reference JSR permitted",
}


def expected_counts(slots, gate_slots):
    """Current public counts, derived from the ledger and the JSR gate rather than typed in."""
    implemented = sum(1 for slot in slots if slot.get("implementation"))
    return {
        "dev": implemented,
        "stub": len(slots) - implemented,
        "approval": sum(1 for slot in slots if slot["approval"]["approved_by"]),
        "abi": sum(1 for slot in slots if slot["abi_contract"].get("status") == "VERIFIED"),
        "jsr": sum(1 for entry in gate_slots.values() if entry["execution"] == "ALLOWED"),
    }


LEDGER = json.loads((ROOT / "spec" / "dev_implementations.json").read_text(encoding="utf-8"))
JSR_GATE = json.loads((ROOT / "spec" / "reference_jsr_gate.json").read_text(encoding="utf-8"))
EXPECTED = expected_counts(LEDGER["slots"], JSR_GATE["slots"])


def normalized_lines(text):
    """Strip HTML tags and join 'N / 81' into 'N/81' so Pages markup is read like Markdown."""
    flat = re.sub(r"<[^>]+>", "", text)
    return [re.sub(r"\s*/\s*81\b", "/81", line) for line in flat.splitlines()]


def claims_in_line(line):
    """Return (key, value, has_denominator) for each number paired with a label on the line.

    A number takes the nearest label before it in its clause (text after the last ';' or ','). If there is
    none, it takes the first label after it in its clause. A label already used by the previous number is
    not reused. Numbers with no label are not returned.
    """
    matches = list(VALUE_RE.finditer(line))
    claims, consumed = [], 0
    for index, match in enumerate(matches):
        start = max(matches[index - 1].end() if index else 0, consumed)
        before = line[start:match.start()]
        clause_start = start + max(before.rfind(";"), before.rfind(","), -1) + 1
        before_labels = [(m.end(), key) for key, rx in LABELS for m in rx.finditer(line, clause_start, match.start())]
        before_labels = [(end, key) for end, key in before_labels if GAP_RE.fullmatch(line[end:match.start()])]
        if before_labels:
            key = max(before_labels)[1]
        else:
            nxt = matches[index + 1].start() if index + 1 < len(matches) else len(line)
            clause = re.split(r"[;,]", line[match.end():nxt], maxsplit=1)[0]
            after = [(m.start(), m.end(), key) for key, rx in LABELS for m in rx.finditer(clause)]
            after = [(s, e, key) for s, e, key in after if GAP_RE.fullmatch(line[match.end():match.end() + s])]
            if not after:
                continue
            _, end, key = min(after)
            consumed = match.end() + end
        claims.append((key, int(match.group(1)), bool(match.group(2)), match.start(), match.end()))
    return claims


# A parenthesised or bracketed note, allowing one level of nesting for a Markdown link inside it.
HISTORICAL_SPAN_RE = re.compile(r"\((?:[^()]|\([^()]*\))*\)|\[[^\[\]]*\]")
# A cite is a whole PR number (#174, not #1740 or #17) or a whole 7-hex commit ID (not a longer SHA). A commit ID must
# contain a letter, so a 7-digit count such as 0000073 is never blanked and stays checked.
CITE_RE = re.compile(r"#\d+(?!\d)|(?<![0-9A-Za-z])(?=[0-9a-f]*[a-f])[0-9a-f]{7}(?![0-9A-Za-z])")


def cites(text):
    return set(CITE_RE.findall(text))


def blank_cites(text):
    """Blank cite tokens so the hex digits or PR digits of a citation are never read as a count."""
    return CITE_RE.sub(lambda match: " " * len(match.group(0)), text)


def historical_split(line):
    """Return (current_text, historical_notes). A note is historical only if it says historical and cites a PR/commit.

    Each note is blanked out of current_text, so its numbers are checked against HISTORICAL_CLAIMS and never
    against the current expected counts.
    """
    notes = []

    def take(match):
        body = match.group(0)[1:-1]
        if HISTORICAL_WORD_RE.search(body) and cites(body) & set(HISTORICAL_CLAIMS):
            notes.append(body)
            return " " * len(match.group(0))
        return match.group(0)

    return blank_cites(HISTORICAL_SPAN_RE.sub(take, line)), notes


def historical_value_ok(note, key, value):
    cited = cites(note)
    return any(cite in cited and HISTORICAL_CLAIMS[cite].get(key) == value for cite in HISTORICAL_CLAIMS)


def segment_containing(current, pos):
    """Return the sentence segment (split on . ; 。 ；) that contains character position `pos`.

    A comma or colon does NOT split a segment: a list such as
    "main f90c16b (A, B, C) and 72 stubs" stays in one segment so the historical reference
    covers every count in it. A period/semicolon ends a segment, which lets a line mix a
    historical main f90c16b statement with a current PR-branch statement.
    """
    start = 0
    for sep in re.finditer(r"[.。;；]", current):
        end = sep.start()
        if start <= pos < end:
            return current[start:end]
        start = sep.end()
    return current[start:]


def stale_hits(text, expected=EXPECTED):
    """Return (line_no, reason) for every current line that carries a stale or wrong claim."""
    hits = []
    in_history = False
    allowed = set(expected.values())

    # No document-wide exemption: each claim is checked individually against the ledger.
    # Historical main snapshot claims are exempt only when the surrounding context
    # explicitly references "main f90c16b" (not merely the presence of "f90c16b" anywhere).

    for number, line in enumerate(normalized_lines(text), 1):
        if re.match(r"^\s*#{1,6}\s", line):
            in_history = bool(HISTORY_HEADING_RE.match(line))
        if in_history and DATED_BULLET_RE.match(line):
            continue  # dated record under a History heading: exempt
        current, notes = historical_split(line)
        for pattern in STALE_PATTERNS:
            if re.search(pattern, current):
                hits.append((number, pattern))
        for key, value, _denom, cstart, _cend in claims_in_line(current):
            if value == expected[key]:
                continue
            if key in ('dev', 'stub'):
                # blank_cites() は "f90c16b" を7桁コミットIDとして同長空白置換するため、
                # 履歴文脈の判定は置換前の元の行 `line` で行う（位置は1対1で保たれる）。
                seg = segment_containing(line, cstart)
                # A claim is a main f90c16b historical claim only when the segment containing
                # THAT claim explicitly references "main f90c16b". A "main f90c16b" mention
                # elsewhere on the line does not by itself exempt this claim.
                if re.search(r'\bmain\s+f90c16b\b', seg, re.I):
                    # Historical main f90c16b snapshot: only dev=9/81 and stubs=72/81 are legitimate.
                    if (key == 'dev' and value == 9) or (key == 'stub' and value == 72):
                        continue  # legitimate historical main snapshot value
                    # A wrong historical value (e.g. "main f90c16b: 11/81") is flagged below.
                else:
                    # main f90c16b may be referenced elsewhere on the same line. Such a line can
                    # still state historical values (9/81 dev, 72/81 stubs), but a segment that
                    # labels the count "Current" states a current value and must match the ledger.
                    seg_is_current = bool(re.search(r'\bcurrent\b|現在', seg, re.I))
                    if (not seg_is_current) and re.search(r'\bmain\s+f90c16b\b', line, re.I):
                        if (key == 'dev' and value == 9) or (key == 'stub' and value == 72):
                            continue  # historical main value referenced elsewhere on the line
            # Non-dev/stub keys (approval, abi, jsr) are never exempted by main f90c16b, and a
            # correct historical value never exempts a wrong current value on the same line.
            hits.append((number, f"{NAMES[key]} {value} != ledger {expected[key]}"))
        for note in notes:
            for key, value, *_rest in claims_in_line(blank_cites(note)):
                if not historical_value_ok(note, key, value):
                    hits.append((number, f"historical {NAMES[key]} {value} does not match the cited PR"))
        for match in VALUE_RE.finditer(current):
            if match.group(2) and int(match.group(1)) not in allowed:
                val = int(match.group(1))
                # blank_cites() は "f90c16b" を同長空白置換するため、履歴文脈は元の行 `line` で判定する。
                seg = segment_containing(line, match.start())
                historical_ctx = bool(re.search(r'\bmain\s+f90c16b\b', seg, re.I))
                exempt = False
                if historical_ctx:
                    if val in (9, 72):
                        exempt = True  # legitimate historical main snapshot count
                else:
                    # main f90c16b may be referenced elsewhere on the same line; a segment that
                    # labels the count "Current" states a current value and must match the ledger.
                    seg_is_current = bool(re.search(r'\bcurrent\b|現在', seg, re.I))
                    if (not seg_is_current) and re.search(r'\bmain\s+f90c16b\b', line, re.I):
                        if val in (9, 72):
                            exempt = True  # historical main count referenced elsewhere on the line
                if not exempt:
                    hits.append((number, f"count {match.group(0).strip()} is not derived from the ledger"))
        # Limit state checks to text that describes each merged PR, with no other PR reference
        # between the PR mention and the open/unmerged/Draft wording. "and"/"および" does not
        # split the check: if another PR follows the conjunction, its wording is attributed to
        # that PR, not to the earlier one.
        # Issue references (#NNNN after "Issue"/"issue"/"課題", or in parens as context) do not
        # count as separating PR references.
        all_pr_positions = []
        for pm in _ANY_PR_RE.finditer(line):
            pos = pm.start()
            # Check if this is an issue reference
            before = line[max(0, pos - 25):pos]
            if re.search(r"(?:Issue|issue|課題)\s+$", before, re.IGNORECASE):
                continue
            if re.search(r"\(\s*$", before):
                continue
            # Check if in parens as context like "PR #495 (#303)"
            if re.search(r"\([^)]*$", line[:pos]):
                continue
            all_pr_positions.append(pos)
        all_pr_matches = list(MERGED_PR_RE.finditer(line))
        sentence_boundaries = ['.', ';', ':', '。', '；', '：', '\n']
        for m in all_pr_matches:
            pr_start = m.start()
            pr_end = m.end()
            found = False
            # Find sentence/segment boundaries for this PR
            # A sentence boundary is a real separator (., ;, :, etc.)
            # Find last boundary before pr_start
            last_boundary_before = -1
            for b in sentence_boundaries:
                idx = line.rfind(b, 0, pr_start)
                if idx > last_boundary_before:
                    last_boundary_before = idx
            # Find first boundary after pr_end
            first_boundary_after = len(line)
            for b in sentence_boundaries:
                idx = line.find(b, pr_end)
                if idx >= 0 and idx < first_boundary_after:
                    first_boundary_after = idx
            segment = line[last_boundary_before + 1:first_boundary_after]
            # Check if any other PR is in this segment
            other_pr_in_segment = any(
                last_boundary_before < p < first_boundary_after and p != pr_start
                for p in all_pr_positions
            )
            # Check open wording within this segment, not attributed to other PR
            if OPEN_WORDING_RE.search(segment):
                # Check if open wording is before/after another PR in same segment
                for ow_match in OPEN_WORDING_RE.finditer(segment):
                    ow_pos = last_boundary_before + 1 + ow_match.start()
                    # Check if there's another PR between this PR and the open wording
                    between_pr = any(
                        min(pr_end, ow_pos) < p < max(pr_start, ow_pos)
                        for p in all_pr_positions if p != pr_start
                    )
                    if not between_pr:
                        # Check if this open wording is in the part of segment attributed to this PR
                        # If another PR is in segment after this PR, open wording after it belongs to that PR
                        if other_pr_in_segment:
                            # Find next PR in segment
                            next_pr_in_segment = min([p for p in all_pr_positions if p > pr_start and p < first_boundary_after], default=None)
                            if next_pr_in_segment is not None and ow_pos > next_pr_in_segment:
                                continue  # belongs to next PR
                            if next_pr_in_segment is not None and pr_end < next_pr_in_segment < ow_pos:
                                continue  # belongs to next PR
                            if ow_pos > pr_end and (next_pr_in_segment is None or ow_pos < next_pr_in_segment):
                                found = True
                                break
                        else:
                            found = True
                            break
            if found:
                hits.append((number, "merged PR or closed issue described as open/unmerged/Draft"))
    return hits


def missing_claims(text, expected=EXPECTED):
    """Return the labelled claims a public doc fails to state with the ledger-derived value.

    The expected values are always the current PR-branch values (DEV 11/81, stubs 70/81).
    A historical main f90c16b heading does NOT switch the whole document to 9/81 and 72 stubs:
    each claim is judged individually by stale_hits().
    """
    found = {key for line in normalized_lines(text) for key, value, *_rest in claims_in_line(line)
             if value == expected[key]}
    return [NAMES[key] for key in NAMES if key not in found]


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
        self.assertEqual(len(implemented), 11)
        self.assertEqual(len(slots) - len(implemented), 70)
        self.assertEqual(source, implemented, "src/overrides must match the implemented ledger slots")
        self.assertEqual(sum(1 for slot in slots if slot["approval"]["approved_by"]), 0)
        self.assertIs(self.ledger["release_eligible"], False)

    def test_jsr_gate_allows_nothing(self):
        self.assertEqual(EXPECTED["jsr"], 0)

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
        self.assertTrue(stale_hits("PR #508 is unmerged and still a Draft."))
        self.assertTrue(stale_hits("PR #516 is open Draft work."))

    def test_date_or_historical_word_on_same_line_does_not_exempt(self):
        self.assertTrue(stale_hits("2026-10-22 historical note: DEV count is 8/81."))
        self.assertTrue(stale_hits("2026-10-22 historical snapshot: 8/81 DEV."))
        self.assertTrue(stale_hits("Earlier 6/81 and the 2026-10-22 8/81 report are dated history."))
        self.assertTrue(stale_hits("- 2026-10-22 historical note: DEV count is 8/81. (9/81 now)\n## History"))

    def test_dated_bullet_under_history_heading_is_exempt(self):
        text = "## Status\n\n- Current: 11/81.\n\n## History\n\n- 2026-10-22 snapshot: 9/81 DEV (historical).\n"
        self.assertEqual(stale_hits(text), [])
        self.assertTrue(stale_hits("## Status\n\n- 2026-10-22 snapshot: 9/81 DEV (historical).\n"))

    def test_historical_pr_claim_is_exempt_only_with_its_value(self):
        roadmap = ("- [x] one callable EX_GETVER override and stub slots (historical [#174]"
                   "(https://github.com/x/pull/174): 80 fail-closed nonreturning stubs as merged)")
        self.assertEqual(stale_hits(roadmap), [])
        self.assertTrue(stale_hits(roadmap.replace("80 fail-closed", "73 fail-closed")))
        self.assertTrue(stale_hits(roadmap.replace("(historical [#174]", "(as merged [#174]")))

    def test_historical_note_exempts_only_its_own_count(self):
        self.assertEqual(stale_hits("Current stubs: 70 (historical #174: 80 stubs)"), [])
        self.assertEqual(stale_hits("Current DEV implementations: 11/81 (historical 8a136ad: 8 DEV implementations)"), [])
        self.assertTrue(stale_hits("Current stubs: 80 (historical #174)"))
        self.assertTrue(stale_hits("Current stubs: 80 (historical #174: 80 stubs)"))
        self.assertTrue(stale_hits("Current stubs: 70 (historical #174: 73 stubs)"))
        self.assertTrue(stale_hits("Current DEV implementations: 9/81 (historical #174: 80 stubs)"))
        self.assertTrue(stale_hits("Current stubs: 69; historical #174: 80 stubs"))

    def test_history_pr_cite_must_be_the_whole_number(self):
        # #1740 is not #174: a note citing #1740 must not borrow #174's historical value.
        self.assertTrue(stale_hits("Current stubs: 69 (historical #1740: 80 stubs)"))
        self.assertTrue(stale_hits("Current stubs: 69 (historical #17: 80 stubs)"))
        self.assertEqual(stale_hits("Current stubs: 70 (historical #174: 80 stubs)"), [])

    def test_history_commit_cite_must_be_the_whole_id(self):
        # 8a136ad0 is not 8a136ad (and 86172e25 is not 86172e2): a longer SHA must not borrow the prefix's value.
        self.assertTrue(stale_hits("Current DEV implementations: 11/81 (historical 8a136ad0: 8 DEV implementations)"))
        self.assertTrue(stale_hits("Current DEV implementations: 11/81 (historical 86172e25: 8 DEV implementations)"))

    def test_history_commit_id_is_exempt_as_a_full_token(self):
        # The hex digits of a cited commit ID are not a count of the note.
        self.assertEqual(
            stale_hits("Current DEV implementations: 11/81 (historical 86172e2: 8 DEV implementations)"), [])
        self.assertTrue(stale_hits("Current DEV implementations: 9/81 (historical 86172e2: 8 DEV implementations)"))

    def test_seven_digit_count_is_not_a_commit_cite(self):
        # A 7-digit count is not a commit ID: it must still be checked as a current count.
        self.assertTrue(stale_hits("Stubs: 0000073 remain"))
        self.assertTrue(stale_hits("Stubs: 1234567"))

    def test_current_merged_wording_is_allowed(self):
        self.assertEqual(stale_hits("- [#495] MA_MUL16S is MERGED (2026-10-22); #494 CLOSED."), [])
        self.assertEqual(stale_hits("Issue #476 remains OPEN, BLOCKED."), [])
        self.assertEqual(stale_hits("- [#508] and [#516] are MERGED test-only PRs; #494 CLOSED."), [])

    def test_multi_pr_merged_and_open_in_same_line(self):
        # Separate PRs with separate states: each clause judged independently → PASS.
        self.assertEqual(stale_hits("PR #495 merged; PR #512 remains open Draft."), [])
        self.assertEqual(stale_hits("PR #495 merged. PR #512 remains open Draft."), [])
        self.assertEqual(stale_hits("PR #495 merged and PR #512 remains open Draft."), [])
        self.assertEqual(stale_hits("PR #495 merged および PR #512 remains open Draft."), [])
        # Sentence boundaries must not leak state across PRs
        self.assertEqual(stale_hits("PR #512 is open; PR #495 merged."), [])
        self.assertEqual(stale_hits("PR #512 is a Draft. PR #495 merged."), [])
        self.assertEqual(stale_hits("PR #495 merged. Open issues are welcome."), [])
        self.assertEqual(stale_hits("Open issues are welcome. PR #495 merged."), [])
        self.assertEqual(stale_hits("PR #495 merged; Issue #303 is open."), [])

    def test_multi_pr_merged_mislabelled_as_open(self):
        # A merged PR described as open/unmerged/Draft in its own clause → FAIL.
        self.assertTrue(stale_hits("PR #495 remains open Draft."))
        self.assertTrue(stale_hits("PR #495 is unmerged."))
        self.assertTrue(stale_hits("PR #495 は未マージです。"))
        # "and"/"および" does not hide open wording for the same PR.
        self.assertTrue(stale_hits("PR #495 merged and remains a Draft."))
        self.assertTrue(stale_hits("PR #495 merged and is still open."))
        self.assertTrue(stale_hits("PR #495 merged および Draft のままです。"))
        # Mixed: #495 open is FAIL even though #512 open Draft is correct (not merged).
        self.assertTrue(stale_hits("PR #495 remains open Draft; PR #512 remains open Draft."))
        # Markdown/HTML link with "and" hiding open wording → FAIL.
        self.assertTrue(stale_hits("[#495](https://github.com/x/pull/495) merged and remains a Draft."))
        self.assertTrue(stale_hits("<a href=\"pull/495\">#495</a> merged and remains a Draft."))
        # Issue reference should not hide PR state for the same PR
        self.assertTrue(stale_hits("PR #495 (#303) remains open Draft."))
        self.assertTrue(stale_hits("PR #495 references Issue #303 and remains open Draft."))

    def test_multi_pr_markdown_and_html_links(self):
        # Markdown links around PR numbers must not break clause separation.
        self.assertEqual(stale_hits("[#495](https://github.com/mao2009/oss-pce-cd-bios/pull/495) merged; "
                                    "[#512](https://github.com/mao2009/oss-pce-cd-bios/pull/512) remains open Draft."), [])
        self.assertEqual(stale_hits("[#495](pull/495) merged and [#512](pull/512) remains open Draft."), [])
        # HTML anchor links.
        self.assertEqual(stale_hits('<a href="pull/495">#495</a> merged; '
                                    '<a href="pull/512">#512</a> remains open Draft.'), [])


class PublicDocsMutationTest(unittest.TestCase):
    """Mutations of a passing claim block. Each must be caught, or the check is too weak."""

    CLEAN = (
        "Status: DEV implementation count: 11/81, 70 stubs.\n"
        "owner-approved DEV_CALLABLE: 0; reference ABI VERIFIED: 0/81; reference JSR permitted: 0/81.\n"
        "Measured on PR branch (Issue #78 adds MA_SQRT slot $44).\n"
    )

    def test_clean_block_passes(self):
        self.assertEqual(stale_hits(self.CLEAN), [])
        self.assertEqual(missing_claims(self.CLEAN), [])

    def test_wrong_current_dev_count_is_flagged(self):
        mutant = self.CLEAN.replace("11/81", "12/81")
        self.assertTrue(stale_hits(mutant))
        self.assertIn("DEV implementations", missing_claims(mutant))

    def test_current_dev_count_label_with_wrong_value_is_flagged(self):
        self.assertTrue(stale_hits("Current DEV count: 12/81."))

    def test_dev_implementations_label_with_zero_is_flagged(self):
        self.assertTrue(stale_hits("DEV implementations: 0/81"))
        self.assertIn("DEV implementations", missing_claims("DEV implementations: 0/81"))

    def test_reference_abi_verified_with_dev_count_is_flagged(self):
        mutant = self.CLEAN.replace("reference ABI VERIFIED: 0/81", "reference ABI VERIFIED: 9/81")
        self.assertTrue(stale_hits(mutant))
        self.assertIn("reference ABI VERIFIED", missing_claims(mutant))

    def test_one_correct_claim_does_not_excuse_a_wrong_one(self):
        mutant = self.CLEAN + "DEV implementations 11/81; reference ABI VERIFIED 9/81.\n"
        self.assertTrue(stale_hits(mutant))

    def test_wrong_stub_count_is_flagged(self):
        self.assertTrue(stale_hits("Status: 73 stubs remain."))
        self.assertTrue(stale_hits("Stubs: 73 remain"))
        self.assertTrue(stale_hits("Status: 69 remaining stub slots."))
        self.assertTrue(stale_hits("80 fail-closed nonreturning stubs"))
        self.assertTrue(stale_hits("72 remaining stub slots"))
        self.assertTrue(stale_hits("Stubs: 72 remain"))

    def test_bare_9_81_does_not_satisfy_claims(self):
        self.assertEqual(stale_hits("11/81"), [])
        self.assertEqual(
            set(missing_claims("11/81")),
            {"DEV implementations", "stubs", "DEV_CALLABLE approval", "reference ABI VERIFIED",
             "reference JSR permitted"},
        )

    def test_approval_must_be_the_value_of_its_label(self):
        self.assertTrue(stale_hits("owner-approved DEV_CALLABLE: 1"))
        self.assertTrue(stale_hits("DEV_CALLABLE approved count: 2"))
        self.assertIn("DEV_CALLABLE approval", missing_claims("DEV_CALLABLE pending (0 of 81 probes)"))

    def test_removing_abi_claim_is_flagged(self):
        mutant = self.CLEAN.replace("reference ABI VERIFIED: 0/81; ", "")
        self.assertEqual(missing_claims(mutant), ["reference ABI VERIFIED"])

    def test_removing_jsr_claim_is_flagged(self):
        mutant = self.CLEAN.replace("; reference JSR permitted: 0/81", "")
        self.assertEqual(missing_claims(mutant), ["reference JSR permitted"])

    def test_jsr_allowed_count_follows_the_gate(self):
        gate = json.loads(json.dumps(JSR_GATE["slots"]))
        gate["1"]["execution"] = "ALLOWED"
        expected = expected_counts(LEDGER["slots"], gate)
        self.assertEqual(expected["jsr"], 1)
        self.assertTrue(stale_hits(self.CLEAN, expected))

    def test_html_wrapped_count_is_read(self):
        html = "<div><span>11</span> / 81</div> DEV implemented slots <span>0</span>/81 ABI VERIFIED"
        self.assertNotIn("DEV implementations", missing_claims(html))
        self.assertTrue(stale_hits("<span>12</span> / 81 DEV implemented"))

    def test_expected_counts_follow_the_ledger_not_a_typed_constant(self):
        slots = json.loads(json.dumps(LEDGER["slots"]))
        slots[1]["implementation"] = {"source": "src/overrides/slot_XX.s"}
        expected = expected_counts(slots, JSR_GATE["slots"])
        self.assertEqual(expected["dev"], 12)
        self.assertEqual(expected["stub"], 69)
        self.assertTrue(stale_hits(self.CLEAN, expected))

    def test_approval_and_abi_status_are_read_from_ledger(self):
        slots = json.loads(json.dumps(LEDGER["slots"]))
        slots[0]["approval"]["approved_by"] = "owner"
        slots[2]["abi_contract"]["status"] = "VERIFIED"
        expected = expected_counts(slots, JSR_GATE["slots"])
        self.assertEqual(expected["approval"], 1)
        self.assertEqual(expected["abi"], 1)
        self.assertEqual(set(missing_claims(self.CLEAN, expected)), {"DEV_CALLABLE approval", "reference ABI VERIFIED"})

    def test_main_historical_value_does_not_exempt_wrong_current_same_line(self):
        # 同じ行に main f90c16b があっても、不正な現在値は免除しない（MAJOR-1 の必須負例）。
        self.assertTrue(stale_hits("Current DEV implementations: 12/81; main f90c16b: 9/81"))
        self.assertTrue(stale_hits("Current stubs: 69; main f90c16b: 72 stubs"))
        self.assertTrue(stale_hits("Current DEV implementations: 9/81; main f90c16b: 9/81"))

    def test_main_historical_wrong_value_is_flagged(self):
        # main f90c16b の履歴値自体が間違っていればフラグ（MAJOR-1 の必須負例）。
        self.assertTrue(stale_hits("main f90c16b: 12/81 DEV implementations"))
        self.assertTrue(stale_hits("main f90c16b: 69 stubs"))

    def test_legitimate_main_historical_claims_are_not_false_positive(self):
        # 正当な main f90c16b 履歴記述は誤検出しない。
        self.assertEqual(stale_hits("main f90c16b: 9/81 DEV implementations and 72 stubs."), [])
        self.assertEqual(stale_hits("Main f90c16b has 9 DEV implementations and 72 stubs; PR branch has 11/81 and 70 stubs."), [])

    def test_history_heading_and_current_values_same_doc(self):
        # 履歴見出しと現在値が同じ文書に存在するケース：現在値は ledger と一致しなければならず、
        # 歴史値(9/81 dev, 72/81 stubs)のみが履歴文脈で免除される。
        text = ("## History\n- 2026-10-22 snapshot: 9/81 DEV (main f90c16b).\n\n"
                "Current DEV implementations: 11/81, 70 stubs.\n"
                "owner-approved DEV_CALLABLE: 0; reference ABI VERIFIED: 0/81; reference JSR permitted: 0/81.")
        self.assertEqual(stale_hits(text), [])
        self.assertTrue(stale_hits(text.replace("11/81, 70 stubs", "12/81, 69 stubs")))

    def test_multiple_current_values_same_line(self):
        # 複数の現在値が同じ行に存在するケース：それぞれ個別に判定する。
        self.assertTrue(stale_hits("Current DEV implementations: 12/81; stubs: 69."))
        self.assertTrue(stale_hits("Current DEV implementations: 12/81; current stubs: 69; main f90c16b: 9/81."))
        self.assertEqual(stale_hits("Current DEV implementations: 11/81; current stubs: 70; main f90c16b: 9/81 and 72 stubs."), [])


if __name__ == "__main__":
    unittest.main()
