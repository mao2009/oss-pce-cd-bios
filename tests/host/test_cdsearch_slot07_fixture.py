"""Positive/negative/boundary host tests for the slot-$07 (CD_SEARCH, candidate $E015) synthetic fixture (Issue #250).

Synthetic only: the medium, cases and judge here are author-defined. No emulator, no reference BIOS
read, no ./rom/ access, no reference callee JSR. The fixture is data (tests/fixtures/cdsearch-slot07-cases.json)
judged by a local reference judge; a passing run validates only this fixture and its judge, never
reference CD_SEARCH behaviour. The fixture must stay ``BLOCKED_ABI`` and ``release_eligible=false``,
and must not be wired into any callable entry.
"""
import hashlib
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "cdsearch-slot07-cases.json"
# sha256 of the canonical JSON (sort_keys, compact separators, UTF-8) of the fixture data. Changing the
# fixture requires updating this value in the same change, so silent edits are caught.
FIXTURE_SHA256 = "eebc25bf74e7a4b769d7960d409bac653f0c9108395079a81c01b3d1dfeabff0"
POSITIVE, NEGATIVE = "POSITIVE", "NEGATIVE"


def canonical_sha256(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                          .encode("utf-8")).hexdigest()


def judge(medium, track, lba):
    """Reference judge for a seek to (track, lba) on the synthetic medium. Returns POSITIVE only for an
    integer LBA inside an audio track's range. Never a PASS/VERIFIED verdict; ABI stays BLOCKED_ABI."""
    if type(track) is not int or type(lba) is not int:
        return NEGATIVE  # malformed: wrong type (bool, float, str are refused)
    entry = next((t for t in medium["tracks"] if t["no"] == track), None)
    if entry is None or entry["type"] != "audio":
        return NEGATIVE  # no such track, or a data track designation
    return POSITIVE if entry["start"] <= lba <= entry["end"] else NEGATIVE


def lenient_judge(medium, track, lba):
    """Deliberately wrong judge (ignores track type and track range): the negative cases must catch it."""
    if type(track) is not int or type(lba) is not int:
        return NEGATIVE
    return POSITIVE if 0 <= lba < medium["leadout_lba"] else NEGATIVE


class CdSearchSlot07FixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cls.medium = cls.fixture["medium"]
        cls.cases = cls.fixture["cases"]

    def test_metadata_is_blocked_synthetic_and_not_releasable(self):
        f = self.fixture
        self.assertEqual(f["schema"], "oss-pce-cd-bios/cdsearch-slot07-fixture/v1")
        self.assertEqual((f["slot"], f["entry_candidate"], f["candidate_name"]), ("$07", "$E015", "CD_SEARCH"))
        self.assertEqual(f["abi_status"], "BLOCKED_ABI")
        self.assertIs(f["release_eligible"], False)
        self.assertEqual(f["execution"], "NOT_RUN")
        self.assertIs(self.medium["synthetic"], True)
        self.assertEqual(f["callee_abi"]["reference_jsr"], "NOT_CALLED")
        for key in ("arguments", "returns", "registers"):
            self.assertEqual(f["callee_abi"][key], "UNKNOWN")

    def test_fixture_hash_matches_recorded_value(self):
        self.assertEqual(canonical_sha256(self.fixture), FIXTURE_SHA256)

    def test_io_range_is_recorded_and_consistent_with_medium(self):
        io = self.fixture["io_range"]
        self.assertEqual((io["lba_min"], io["lba_max"], io["track_numbers"]), (0, 200, [1, 2]))
        self.assertEqual(self.medium["leadout_lba"], io["lba_max"])
        self.assertEqual([t["no"] for t in self.medium["tracks"]], io["track_numbers"])

    def test_every_case_matches_its_expected_verdict(self):
        for case in self.cases:
            with self.subTest(case=case["id"]):
                self.assertEqual(judge(self.medium, case["track"], case["lba"]), case["expected"])

    def test_case_mix_covers_positive_negative_and_boundary(self):
        positives = [c for c in self.cases if c["expected"] == POSITIVE]
        negatives = [c for c in self.cases if c["expected"] == NEGATIVE]
        self.assertGreaterEqual(len(positives), 3)
        self.assertGreaterEqual(len(negatives), 12)
        self.assertEqual(len({c["id"] for c in self.cases}), len(self.cases))
        # Boundary LBAs of the valid audio track are positive; one step outside each edge is negative.
        # Keyed by id: (2, 100.0) would collide with (2, 100) as a dict key.
        by_id = {c["id"]: c["expected"] for c in self.cases}
        self.assertEqual(by_id["pos_audio_start"], POSITIVE)
        self.assertEqual(by_id["pos_audio_end"], POSITIVE)
        self.assertEqual(by_id["neg_before_track"], NEGATIVE)
        self.assertEqual(by_id["neg_leadout"], NEGATIVE)

    def test_no_negative_case_is_accepted_by_the_judge(self):
        accepted = [c["id"] for c in self.cases
                    if c["expected"] == NEGATIVE and judge(self.medium, c["track"], c["lba"]) == POSITIVE]
        self.assertEqual(accepted, [])

    def test_negative_set_catches_a_lenient_judge(self):
        # False-positive guard: a judge that ignores track type/range must be rejected by at least one negative.
        caught = [c["id"] for c in self.cases
                  if c["expected"] == NEGATIVE and lenient_judge(self.medium, c["track"], c["lba"]) == POSITIVE]
        self.assertGreaterEqual(len(caught), 4)

    def test_fixture_is_not_wired_as_a_callable_entry(self):
        text = FIXTURE.read_text(encoding="utf-8")
        self.assertNotIn("DEV_CALLABLE", text)
        self.assertNotIn("VERIFIED", text)
        contracts = json.loads((ROOT / "spec" / "api_contracts.json").read_text(encoding="utf-8"))
        slot7 = next(entry for entry in contracts["apis"] if entry.get("slot") == 7)
        self.assertEqual(slot7["candidate_name"], "CD_SEARCH")
        self.assertEqual(slot7["verification"]["status"], "NOT_RUN")
        self.assertIs(slot7["verification"]["executed_callee"], False)
        self.assertEqual(slot7["mapping_status"], "HYPOTHESIS")
        for shared in ("tools", "Makefile", "spec", "docs"):
            for path in (ROOT / shared).rglob("*"):
                if path.is_file() and path.suffix in {".py", ".json", ".md", ""}:
                    self.assertNotIn("cdsearch-slot07", path.read_text(encoding="utf-8", errors="ignore"),
                                     f"fixture wired into {path.relative_to(ROOT)}")


if __name__ == "__main__":
    unittest.main()
