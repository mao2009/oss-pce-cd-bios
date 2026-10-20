"""Host checks for the slot-$0A (CD_SUBQ, candidate $E01E) synthetic Q-subchannel fixture (Issue #260).

Synthetic only: the medium, cases and judge are author-defined. No emulator, no reference BIOS read,
no ./rom/ access, no reference callee JSR. The fixture (tests/fixtures/cdsubq-slot0a-cases.json) stays
BLOCKED_ABI with release_eligible=false, and the caller ABI stays UNKNOWN. A passing run validates only
this fixture and its judge, never reference CD_SUBQ behaviour or the Q buffer layout.
"""
import copy
import hashlib
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "cdsubq-slot0a-cases.json"
# sha256 of the canonical JSON (sort_keys, compact separators, UTF-8) of the fixture data.
FIXTURE_SHA256 = "5c5a6bce787668f3b54e53e3a7ad2dfd60e7d1254adb032124c8865dc8af2fcd"
POSITIVE, NEGATIVE = "POSITIVE", "NEGATIVE"
TOP_LEVEL_KEYS = {"schema", "issue", "parent_issue", "slot", "entry_candidate", "candidate_name", "abi_status",
                  "release_eligible", "execution", "provenance", "callee_abi", "medium", "io_range", "cases"}
FORBIDDEN_WORDS = ("VERIFIED", "OBSERVED", "DEV_CALLABLE", "CALLABLE", "PASS")


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def judge(case, medium):
    """Local reference judge: a Q position is valid only while PLAYING, inside the medium, on the track."""
    if case["playback_state"] != "PLAYING":
        return NEGATIVE
    lba, track = case["lba"], case["track"]
    if not (_is_int(lba) and _is_int(track)) or lba < 0 or lba >= medium["leadout_lba"]:
        return NEGATIVE
    by_no = {t["no"]: t for t in medium["tracks"]}
    t = by_no.get(track)
    if t is None:
        return NEGATIVE
    return POSITIVE if t["start"] <= lba <= t["end"] else NEGATIVE


class CdSubqSlot0aFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = FIXTURE.read_text(encoding="utf-8")
        cls.data = json.loads(cls.text)

    def test_fail_closed_status(self):
        d = self.data
        self.assertEqual(set(d), TOP_LEVEL_KEYS)
        self.assertEqual(d["abi_status"], "BLOCKED_ABI")
        self.assertIs(d["release_eligible"], False)
        self.assertEqual(d["execution"], "NOT_RUN")
        self.assertEqual(d["callee_abi"], {
            "arguments": "UNKNOWN", "returns": "UNKNOWN", "registers": "UNKNOWN",
            "q_buffer_layout": "UNKNOWN", "reference_jsr": "NOT_CALLED"})
        for word in FORBIDDEN_WORDS:
            self.assertNotIn(word, self.text, f"forbidden status word in fixture: {word}")

    def test_fixture_hash_pinned(self):
        canon = json.dumps(self.data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        self.assertEqual(hashlib.sha256(canon.encode("utf-8")).hexdigest(), FIXTURE_SHA256)

    def test_expected_matches_judge(self):
        for case in self.data["cases"]:
            with self.subTest(case=case["id"]):
                self.assertEqual(judge(case, self.data["medium"]), case["expected"])

    def test_boundary_and_polarity_coverage(self):
        cases = {c["id"]: c for c in self.data["cases"]}
        positives = {c["id"] for c in self.data["cases"] if c["expected"] == POSITIVE}
        self.assertEqual(positives, {"pos_track1_start", "pos_track1_end", "pos_track2_start",
                                     "pos_track2_middle", "pos_track2_end"})
        for required in ("neg_not_started", "neg_stopped_track2", "neg_leadout", "neg_before_track2",
                         "neg_track_mismatch", "neg_no_such_track", "neg_bool_track"):
            self.assertIn(required, cases)
            self.assertEqual(cases[required]["expected"], NEGATIVE)

    def test_judge_rejects_mutated_expectations(self):
        # The judge must catch a flipped expectation, so a wrong fixture cannot pass silently.
        medium = self.data["medium"]
        mutated = copy.deepcopy(self.data["cases"][0])
        mutated["expected"] = NEGATIVE
        self.assertEqual(judge(mutated, medium), POSITIVE)
        leadout = copy.deepcopy(self.data["cases"][7])
        leadout["expected"] = POSITIVE
        self.assertEqual(judge(leadout, medium), NEGATIVE)


if __name__ == "__main__":
    unittest.main()
