"""Host tests for the CD_FADE contract ledger (Issue #282).

These check the ledger's wording rules and the pinned field verdicts only. They
do not execute or verify the System Card CD_FADE callee.
"""
import re
import sys
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import reference_jsr_gate as gate  # noqa: E402

ALLOWED = {"SOURCE_FACT", "HYPOTHESIS", "UNKNOWN"}
FORBIDDEN = re.compile(r"(?<![A-Za-z_])(OBSERVED|VERIFIED|PASS|DEV_CALLABLE)(?![A-Za-z_])")
GATE_CLAIMS = re.compile(r"release_eligible\s*[:=]\s*true|DEV_CALLABLE`?\s*を付ける|互換(を満た|である|性あり|確認)|BLOCKED_ABI.{0,10}解除")


def section1(text):
    return text.split("## 1.")[1].split("## 2.")[0]


FIELD_ROW = re.compile(r"^\| (F\d{2}) \|")
SOURCE_ID = re.compile(r"\bS\d+\b")
# Every source S1-S10 stays PENDING (provenance), which is independent of SOURCE_FACT.
PENDING_SOURCES = {f"S{i}" for i in range(1, 11)}
# Third-party / re-implementation sources: a row resting only on these is never SOURCE_FACT.
THIRD_PARTY_SOURCES = {"S8", "S9", "S10"}
EXPECTED_COUNTS = {"SOURCE_FACT": 15, "HYPOTHESIS": 4, "UNKNOWN": 18}

# Pinned verdict per field row. A verdict change must edit this table in review.
EXPECTED = {
    "F01": "SOURCE_FACT", "F02": "UNKNOWN", "F03": "SOURCE_FACT", "F04": "SOURCE_FACT",
    "F05": "SOURCE_FACT", "F06": "SOURCE_FACT", "F07": "UNKNOWN", "F08": "HYPOTHESIS",
    "F09": "HYPOTHESIS", "F10": "UNKNOWN", "F11": "HYPOTHESIS", "F12": "UNKNOWN",
    "F13": "UNKNOWN", "F14": "SOURCE_FACT", "F15": "HYPOTHESIS", "F16": "UNKNOWN",
    "F17": "SOURCE_FACT", "F18": "UNKNOWN", "F19": "UNKNOWN", "F20": "SOURCE_FACT",
    "F21": "SOURCE_FACT", "F22": "UNKNOWN", "F23": "SOURCE_FACT", "F24": "UNKNOWN",
    "F25": "SOURCE_FACT", "F26": "SOURCE_FACT", "F27": "UNKNOWN", "F28": "UNKNOWN",
    "F29": "SOURCE_FACT", "F30": "SOURCE_FACT", "F31": "UNKNOWN", "F32": "UNKNOWN",
    "F33": "UNKNOWN", "F34": "UNKNOWN", "F35": "SOURCE_FACT", "F36": "UNKNOWN",
    "F37": "UNKNOWN",
}


def parse_fields(text):
    """Map field id -> (verdict, basis cell) for each field row."""
    rows = {}
    for line in text.splitlines():
        m = FIELD_ROW.match(line)
        if not m:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        rows[m.group(1)] = (cells[2], cells[3])
    return rows


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


def check_pins(text):
    """Return violations of the pinned verdicts, counts and PENDING-source rule."""
    rows = parse_fields(text)
    errors = []
    for fid, want in EXPECTED.items():
        if fid not in rows:
            errors.append(f"{fid}: row missing")
        elif rows[fid][0] != want:
            errors.append(f"{fid}: verdict pinned {want}, got {rows[fid][0]}")
    for fid in sorted(set(rows) - set(EXPECTED)):
        errors.append(f"{fid}: row not pinned")
    counts = Counter(verdict for verdict, _ in rows.values())
    if counts != Counter(EXPECTED_COUNTS):
        errors.append(f"verdict counts {dict(counts)} != {EXPECTED_COUNTS}")
    errors += check_source_fact(rows)
    return errors


def check_source_fact(rows):
    """SOURCE_FACT = caller-side fact in a pinned source; PENDING provenance is irrelevant.

    It needs a cited source, must not rest on third-party notes, and must not be a callee row.
    """
    errors = []
    for fid, (verdict, basis) in rows.items():
        if verdict != "SOURCE_FACT":
            continue
        cited = set(SOURCE_ID.findall(basis))
        if not cited:
            errors.append(f"{fid}: SOURCE_FACT cites no source")
        if cited & THIRD_PARTY_SOURCES:
            errors.append(f"{fid}: SOURCE_FACT cites third-party source {sorted(cited & THIRD_PARTY_SOURCES)}")
        if fid in CALLEE_ROWS:
            errors.append(f"{fid}: callee-behavior row must not be SOURCE_FACT")
    return errors


# Callee (BIOS-side) rows: unverified behavior, never SOURCE_FACT.
CALLEE_ROWS = ["F02", "F07", "F10", "F12", "F13", "F16", "F18", "F19", "F22", "F24",
               "F27", "F28", "F31", "F32", "F33", "F34", "F36", "F37"]


def source_rows(text):
    """Return the section-3 source table rows (S1..S10)."""
    return [l for l in text.splitlines() if re.match(r"^\| S\d+ \|", l)]


def check_sources(text):
    """Every source row must be PENDING and not APPROVED (this PR records no approval)."""
    errors = []
    for row in source_rows(text):
        sid = SOURCE_ID.search(row).group(0)
        if "APPROVED" in row:
            errors.append(f"{sid}: source is APPROVED without owner record")
        elif "PENDING" not in row:
            errors.append(f"{sid}: source row does not state PENDING")
    return errors


def promote(text, fid, verdict):
    """Return text with the verdict cell of one field row replaced."""
    return re.sub(rf"^(\| {fid} \|[^|]*\| )[A-Z_]+( \|)", rf"\g<1>{verdict}\2",
                  text, count=1, flags=re.M)


class LedgerRulesTest(unittest.TestCase):
    def test_rule_rejects_forbidden_status_words(self):
        self.assertTrue(check_ledger("| F90 | x | OBSERVED | S1 | y |"))
        self.assertTrue(check_ledger("| F90 | x | SOURCE_FACT | PASS | y |"))

    def test_rule_rejects_mixed_or_promoted_verdicts(self):
        self.assertTrue(check_ledger("| F90 | x | UNKNOWN（SOURCE_FACT） | — | y |"))
        self.assertTrue(check_ledger("| F90 | x | HYPOTHESIS, UNKNOWN | — | y |"))

    def test_rule_accepts_single_verdict(self):
        self.assertEqual(check_ledger("| F90 | x | UNKNOWN | — | y |"), [])


class RestoredRuleChecks(unittest.TestCase):
    """The fade-contract rule checks, restored after the document was withdrawn: run on synthetic input only."""

    def test_a_callee_row_marked_source_fact_is_refused(self):
        self.assertTrue(check_source_fact({"F02": ("SOURCE_FACT", "S1 12")}))

    def test_a_source_fact_without_a_citation_is_refused(self):
        self.assertTrue(check_source_fact({"F40": ("SOURCE_FACT", "—")}))

    def test_a_source_fact_citing_a_third_party_source_is_refused(self):
        third = sorted(THIRD_PARTY_SOURCES)[0]
        self.assertTrue(check_source_fact({"F40": ("SOURCE_FACT", f"{third} 1")}))

    def test_a_caller_side_source_fact_with_a_primary_citation_is_accepted(self):
        self.assertEqual(check_source_fact({"F40": ("SOURCE_FACT", "S1 12")}), [])

    def test_a_hypothesis_or_unknown_row_is_never_checked_as_source_fact(self):
        self.assertEqual(check_source_fact({"F02": ("HYPOTHESIS", "—"), "F40": ("UNKNOWN", "—")}), [])

    def test_a_source_row_marked_approved_is_refused(self):
        self.assertTrue(check_sources("| S1 | synthetic | APPROVED |"))

    def test_a_source_row_without_pending_is_refused(self):
        self.assertTrue(check_sources("| S1 | synthetic | none |"))

    def test_a_pending_source_row_is_accepted(self):
        self.assertEqual(check_sources("| S1 | synthetic | PENDING |"), [])

    def test_the_pin_check_reports_a_ledger_with_no_pinned_rows(self):
        # The pinned table belongs to the withdrawn document; an empty ledger must still be reported, never passed.
        self.assertTrue(check_pins(""))


class RealGateState(unittest.TestCase):
    """Slot 15: the release state is checked on the real gate, since the research document is withdrawn."""

    def test_the_real_gate_keeps_the_slot_blocked_and_not_released(self):
        ledger, contracts = gate.load()
        record = gate.resolve(15, ledger, contracts)
        self.assertEqual(record["execution"], "BLOCKED")
        self.assertIs(record["release_eligible"], False)


if __name__ == "__main__":
    unittest.main()
