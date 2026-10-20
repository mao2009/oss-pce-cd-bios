"""Host checks for the synthetic CD_DINFO (slot $0B, candidate $E021) TOC boundary fixture (Issue #235).

Synthetic only: the medium (data track 1 + audio track 2, pregap / INDEX 01 / lead-out) and every expected
value are author-defined model expectations, not measured device or System Card behaviour. No emulator
runs, no reference BIOS is read or called (reference_jsr_count = 0), and nothing under ./rom/ is touched.
Passing these tests validates only this fixture and its judge. The fixture stays BLOCKED_ABI and
release_eligible=false, and must not be wired into any build, ROM or callable entry.
"""
import hashlib
import json
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_REL = "tests/fixtures/cddinfo-slot0b-cases.json"
FIXTURE = ROOT / FIXTURE_REL
SELF_REL = "tests/host/test_cddinfo_slot0b_fixture.py"
# sha256 of the canonical JSON (sort_keys, compact separators, UTF-8). Editing the fixture requires updating it here.
FIXTURE_SHA256 = "b4ee7f069ea1ff1dbd37f43fa1bb1e5381f2573797012e714d4ab177b54ce140"
POSITIVE, NEGATIVE = "POSITIVE", "NEGATIVE"
TOP_LEVEL_KEYS = {"schema", "issue", "slot", "entry_candidate", "candidate_name", "abi_status", "release_eligible",
                  "execution", "provenance", "callee_abi", "medium", "cases"}
APPROVED_PROVENANCE = ("synthetic: author-defined medium and cases only; no System Card BIOS byte, disassembly, "
                       "retail data, or ./rom/ content; no reference callee is executed")
HEX = "0123456789abcdefABCDEF"


def canonical_sha256(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                          .encode("utf-8")).hexdigest()


def decode_bcd(text):
    """Two-digit BCD string -> track number. Raises ValueError for malformed or invalid-nibble input."""
    if type(text) is not str or len(text) != 2:
        raise ValueError("malformed BCD: wrong length")
    if any(c not in HEX for c in text):
        raise ValueError("malformed BCD: non-hex digit")
    hi, lo = int(text[0], 16), int(text[1], 16)
    if hi > 9 or lo > 9:
        raise ValueError("invalid BCD: nibble above 9")
    return hi * 10 + lo


def _track_result(medium, number):
    if number == 0:
        return NEGATIVE, "track number zero is not a track", None
    entry = next((t for t in medium["tracks"] if t["no"] == number), None)
    if entry is None:
        return NEGATIVE, "track number not on the medium", None
    return POSITIVE, "track found", {"type": entry["type"], "index01": entry["index01"], "end": entry["end"]}


def judge(medium, query, present=True):
    """Reference judge: (verdict, reason, result). Never a PASS/VERIFIED verdict; ABI stays BLOCKED_ABI."""
    if not present:
        return NEGATIVE, "no medium present", None
    kind, value = query["kind"], query["value"]
    if kind == "bcd":
        try:
            number = decode_bcd(value)
        except ValueError as exc:
            return NEGATIVE, str(exc), None
        return _track_result(medium, number)
    if kind == "track":
        if type(value) is not int:
            return NEGATIVE, "malformed: track must be an int (bool, float, str refused)", None
        return _track_result(medium, value)
    if kind == "lba":
        if type(value) is not int:
            return NEGATIVE, "non-integer LBA type (malformed)", None
        if value == medium["leadout_lba"]:
            return POSITIVE, "lead-out", {"region": "LEAD_OUT"}
        if value < 0 or value > medium["leadout_lba"]:
            return NEGATIVE, "outside the medium", None
        for t in medium["tracks"]:
            if t["pregap_start"] <= value < t["index01"]:
                return POSITIVE, "pregap", {"region": "PREGAP", "track": t["no"]}
            if t["index01"] <= value <= t["end"]:
                return POSITIVE, "INDEX 01", {"region": "INDEX01", "track": t["no"]}
    return NEGATIVE, "unknown query kind", None


def lenient_judge(medium, query, present=True):
    """Deliberately wrong judge: ignores presence, type checks and BCD validity. Negatives must catch it."""
    value = query["value"]
    if query["kind"] == "bcd":
        try:
            value = int(str(value), 16)
        except ValueError:
            return NEGATIVE
    if query["kind"] == "lba":
        return POSITIVE if isinstance(value, (int, float)) and 0 <= value <= medium["leadout_lba"] else NEGATIVE
    return POSITIVE if value in (1, 2) else NEGATIVE


class CdDinfoSlot0bFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cls.medium = cls.fixture["medium"]
        cls.cases = cls.fixture["cases"]

    def test_metadata_is_blocked_synthetic_and_not_releasable(self):
        f = self.fixture
        self.assertEqual(f["schema"], "oss-pce-cd-bios/cddinfo-slot0b-fixture/v1")
        self.assertEqual((f["issue"], f["slot"], f["entry_candidate"], f["candidate_name"]),
                         (235, "$0B", "$E021", "CD_DINFO"))
        self.assertEqual(f["abi_status"], "BLOCKED_ABI")
        self.assertIs(f["release_eligible"], False)
        self.assertEqual(f["execution"], "NOT_RUN")
        self.assertIs(self.medium["synthetic"], True)
        self.assertEqual(f["callee_abi"], {"arguments": "UNKNOWN", "returns": "UNKNOWN", "registers": "UNKNOWN",
                                           "reference_jsr": "NOT_CALLED", "reference_jsr_count": 0})

    def test_fixture_hash_and_provenance_are_pinned(self):
        self.assertEqual(canonical_sha256(self.fixture), FIXTURE_SHA256)
        self.assertEqual(set(self.fixture), TOP_LEVEL_KEYS)
        self.assertEqual(self.fixture["provenance"], APPROVED_PROVENANCE)

    def test_medium_boundaries_are_contiguous(self):
        d, a = self.medium["tracks"]
        self.assertEqual((d["no"], d["type"], a["no"], a["type"]), (1, "data", 2, "audio"))
        self.assertEqual((d["pregap_start"], d["index01"], d["end"]), (0, 150, 299))
        self.assertEqual((a["pregap_start"], a["index01"], a["end"]), (300, 450, 599))
        self.assertEqual(a["pregap_start"], d["end"] + 1)
        self.assertEqual(self.medium["leadout_lba"], a["end"] + 1)

    def test_every_case_matches_its_expected_verdict_and_result(self):
        for case in self.cases:
            with self.subTest(case=case["id"]):
                verdict, reason, result = judge(self.medium, case, case.get("present", True))
                self.assertEqual(verdict, case["expected"])
                if verdict == POSITIVE:
                    self.assertEqual(result, case["result"])
                else:
                    self.assertIsNone(result)
                    self.assertTrue(reason)

    def test_case_mix_covers_positive_negative_and_boundaries(self):
        ids = [c["id"] for c in self.cases]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertGreaterEqual(sum(c["expected"] == POSITIVE for c in self.cases), 5)
        self.assertGreaterEqual(sum(c["expected"] == NEGATIVE for c in self.cases), 12)
        kinds = {c["kind"] for c in self.cases}
        self.assertEqual(kinds, {"track", "bcd", "lba"})
        by_id = {c["id"]: c["expected"] for c in self.cases}
        for boundary in ("pos_lba_pregap1_end", "pos_lba_index01_1", "pos_lba_track1_end", "pos_lba_leadout"):
            self.assertEqual(by_id[boundary], POSITIVE)
        for boundary in ("neg_lba_past_leadout", "neg_track_zero", "neg_bcd_10_no_such", "neg_nomedium_track"):
            self.assertEqual(by_id[boundary], NEGATIVE)

    def test_no_negative_case_is_accepted_and_lenient_judge_is_caught(self):
        accepted = [c["id"] for c in self.cases if c["expected"] == NEGATIVE
                    and judge(self.medium, c, c.get("present", True))[0] == POSITIVE]
        self.assertEqual(accepted, [])
        caught = [c["id"] for c in self.cases if c["expected"] == NEGATIVE
                  and lenient_judge(self.medium, c, c.get("present", True)) == POSITIVE]
        self.assertGreaterEqual(len(caught), 4)

    def test_slot_0b_contract_row_is_not_run_and_not_executed(self):
        contracts = json.loads((ROOT / "spec" / "api_contracts.json").read_text(encoding="utf-8"))
        row = next(e for e in contracts["apis"] if e.get("slot") == 11)
        self.assertEqual(row["candidate_name"], "CD_DINFO")
        self.assertEqual(row["candidate_entry"], "0xE021")
        self.assertEqual(row["mapping_status"], "HYPOTHESIS")
        self.assertEqual(row["verification"], {"status": "NOT_RUN", "executed_callee": False, "evidence": []})

    def test_fixture_is_not_wired_into_build_rom_or_callable_entries(self):
        self.assertNotIn("DEV_CALLABLE", FIXTURE.read_text(encoding="utf-8"))
        out = subprocess.run(["git", "-C", str(ROOT), "grep", "-l", "-F", "cddinfo-slot0b", "--", "."],
                             capture_output=True, text=True)
        self.assertIn(out.returncode, (0, 1))
        hits = {p for p in out.stdout.splitlines()
                if p not in (FIXTURE_REL, SELF_REL) and not p.startswith("docs/") and not p.endswith(".md")}
        self.assertEqual(sorted(hits), [], "fixture referenced outside tests/docs")


if __name__ == "__main__":
    unittest.main()
