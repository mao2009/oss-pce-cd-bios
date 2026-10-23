"""Host tests for the Issue #265 synthetic track judge (tools/cdcontnts_track_judge.py).

Reuses the existing synthetic fixture (tests/fixtures/cdcontnts-track-cases.json, Issue #347) unchanged.
The judge is a local model: these tests run no emulator, read no ROM and call no reference callee.
Passing them is not evidence of CD_CONTNTS ABI compatibility; the callee verdict stays BLOCKED.
"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import cdcontnts_track_judge as judge_mod  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "cdcontnts-track-cases.json"
PINNED_SHA256 = "a53ef2c69caa30fa8a360f557720a02b6bca5fc0f9d885e1b1730a606ab1e04f"
MIXED_OK = {"synthetic": True, "leadout_lba": 300, "tracks": [
    {"no": 1, "type": "data", "start": 0, "end": 99},
    {"no": 2, "type": "audio", "start": 100, "end": 199}]}


class JudgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def _medium(self, name):
        return None if name == "none" else self.fixture["media"][name]

    def test_fixture_hash_matches_pin(self):
        self.assertEqual(judge_mod.fixture_sha256(self.fixture), PINNED_SHA256)

    def test_judge_agrees_with_every_decided_fixture_case(self):
        for case in self.fixture["cases"]:
            with self.subTest(case=case["id"]):
                verdict, detail = judge_mod.judge(self._medium(case["media"]), case["track"])
                self.assertEqual(verdict, case["expected"])
                if verdict == judge_mod.POSITIVE:
                    self.assertEqual(detail, case["attributes"])

    def test_callee_dependent_queries_are_blocked_never_positive_or_negative(self):
        for case in self.fixture["cases"]:
            with self.subTest(case=case["id"]):
                verdict, detail = judge_mod.judge(self._medium(case["media"]), case["track"], needs_callee=True)
                self.assertEqual(verdict, judge_mod.BLOCKED)
                self.assertTrue(detail.startswith("BLOCKED_ABI"))

    def test_malformed_medium_is_unknown_not_negative(self):
        bad_media = [
            {},
            {**MIXED_OK, "synthetic": False},
            {"synthetic": True, "leadout_lba": 300},
            {**MIXED_OK, "leadout_lba": "300"},
            {**MIXED_OK, "tracks": [{"no": 1, "type": "video", "start": 0, "end": 9}]},
            {**MIXED_OK, "tracks": [{"no": "1", "type": "data", "start": 0, "end": 9}]},
            ["not", "a", "medium"],
        ]
        for medium in bad_media:
            with self.subTest(medium=medium):
                self.assertEqual(judge_mod.judge(medium, 1)[0], judge_mod.UNKNOWN)

    def test_missing_medium_is_negative_and_distinct_from_unknown(self):
        self.assertEqual(judge_mod.judge(None, 1)[0], judge_mod.NEGATIVE)
        self.assertNotEqual(judge_mod.judge(None, 1)[0], judge_mod.judge({}, 1)[0])

    def test_four_verdicts_are_distinct_strings(self):
        verdicts = {judge_mod.POSITIVE, judge_mod.NEGATIVE, judge_mod.UNKNOWN, judge_mod.BLOCKED}
        self.assertEqual(len(verdicts), 4)

    def test_bool_track_is_refused_even_where_1_exists(self):
        self.assertEqual(judge_mod.judge(MIXED_OK, True)[0], judge_mod.NEGATIVE)
        self.assertEqual(judge_mod.judge(MIXED_OK, 1)[0], judge_mod.POSITIVE)

    def test_blocked_takes_precedence_over_missing_medium(self):
        self.assertEqual(judge_mod.judge(None, 1, needs_callee=True)[0], judge_mod.BLOCKED)

    def test_absent_track_is_negative_not_positive(self):
        self.assertEqual(judge_mod.judge(MIXED_OK, 3)[0], judge_mod.NEGATIVE)

    # Expected values below are hand-derived from the documented model (fixture query_model and the judge
    # docstring precedence), not copied from judge() output. Model: a medium is "synthetic" with int
    # leadout_lba, a track list, each track {no:int, type in (data, audio), start:int, end:int}, and a
    # track number identifies at most one track. Anything outside that shape is outside the model -> UNKNOWN.

    def test_track_missing_start_or_end_or_with_wrong_type_is_unknown_not_keyerror(self):
        # Track 1 exists, so the old code reached entry["start"] and raised KeyError (or returned a non-int).
        bad_tracks = [
            {"no": 1, "type": "data", "end": 99},                      # start missing
            {"no": 1, "type": "data", "start": 0},                     # end missing
            {"no": 1, "type": "data", "start": "0", "end": 99},        # start is a string
            {"no": 1, "type": "data", "start": 0, "end": None},        # end is null
            {"no": 1, "type": "data", "start": 0, "end": 99.0},        # end is a float
        ]
        for track in bad_tracks:
            medium = {**MIXED_OK, "tracks": [track]}
            with self.subTest(track=track):
                self.assertEqual(judge_mod.judge(medium, 1)[0], judge_mod.UNKNOWN)

    def test_leadout_bool_is_unknown_not_negative(self):
        # Model: leadout_lba is an int. True is a bool, so the medium is outside the model -> UNKNOWN.
        for leadout in (True, False):
            medium = {"synthetic": True, "leadout_lba": leadout, "tracks": []}
            with self.subTest(leadout=leadout):
                self.assertEqual(judge_mod.judge(medium, 1)[0], judge_mod.UNKNOWN)

    def test_bool_in_start_or_end_is_unknown(self):
        # start/end are ints in the model. True/False are bools, not ints -> medium UNKNOWN.
        for field, value in (("start", True), ("start", False), ("end", True), ("end", False)):
            track = {"no": 1, "type": "data", "start": 0, "end": 99, field: value}
            with self.subTest(field=field, value=value):
                self.assertEqual(judge_mod.judge({**MIXED_OK, "tracks": [track]}, 1)[0], judge_mod.UNKNOWN)

    def test_duplicate_track_number_on_medium_is_unknown(self):
        # Two tracks both numbered 1: the model cannot say which one a query means -> medium UNKNOWN.
        # First-match would have returned POSITIVE for the data entry; that is the finding being fixed.
        dup = {"synthetic": True, "leadout_lba": 300, "tracks": [
            {"no": 1, "type": "data", "start": 0, "end": 99},
            {"no": 1, "type": "audio", "start": 100, "end": 199}]}
        self.assertEqual(judge_mod.judge(dup, 1)[0], judge_mod.UNKNOWN)
        self.assertEqual(judge_mod.judge(dup, 2)[0], judge_mod.UNKNOWN)  # the medium is malformed as a whole

    def test_precedence_malformed_medium_beats_invalid_track(self):
        # Rule 3 (medium -> UNKNOWN) is checked before rule 4 (non-int track -> NEGATIVE).
        # Medium is malformed (leadout is a string) and the track is bool: expected UNKNOWN.
        bad_medium = {**MIXED_OK, "leadout_lba": "300"}
        self.assertEqual(judge_mod.judge(bad_medium, True)[0], judge_mod.UNKNOWN)
        self.assertEqual(judge_mod.judge(bad_medium, "1")[0], judge_mod.UNKNOWN)

    def test_precedence_invalid_track_beats_well_formed_medium_lookup(self):
        # Well-formed medium, bool track that would match a number 1 track if bool were treated as int:
        # rule 4 -> NEGATIVE, not POSITIVE.
        self.assertEqual(judge_mod.judge(MIXED_OK, True)[0], judge_mod.NEGATIVE)

    def test_precedence_no_medium_beats_malformed_medium_and_track(self):
        # Rule 2 (no medium -> NEGATIVE) is checked before rule 3; there is no medium to judge.
        self.assertEqual(judge_mod.judge(None, True)[0], judge_mod.NEGATIVE)

    def test_precedence_callee_block_beats_malformed_medium(self):
        # Rule 1 (BLOCKED) is first: a malformed medium does not turn a callee-dependent query into UNKNOWN.
        self.assertEqual(judge_mod.judge({}, 1, needs_callee=True)[0], judge_mod.BLOCKED)

    # Physical-geometry negatives (Issue #265 review LOW, rules: start>=0, start<=end, end<leadout_lba, no>=1,
    # tracks ordered by no and non-overlapping). Each expected value is hand-derived here from those rules:
    # a medium that breaks one rule is outside the model -> UNKNOWN, and UNKNOWN must never become POSITIVE.

    def test_start_after_end_is_unknown_not_positive(self):
        # Hand-derived: track 1 has start 50 and end 10, so the track runs backwards. Impossible -> UNKNOWN.
        medium = {**MIXED_OK, "tracks": [{"no": 1, "type": "data", "start": 50, "end": 10}]}
        self.assertEqual(judge_mod.judge(medium, 1)[0], judge_mod.UNKNOWN)

    def test_negative_start_is_unknown_not_positive(self):
        # Hand-derived: an LBA cannot be negative. start -1 -> UNKNOWN.
        medium = {**MIXED_OK, "tracks": [{"no": 1, "type": "data", "start": -1, "end": 99}]}
        self.assertEqual(judge_mod.judge(medium, 1)[0], judge_mod.UNKNOWN)

    def test_track_end_at_or_past_leadout_is_unknown_not_positive(self):
        # Hand-derived: leadout_lba is 300, so a track must end before 300 (the fixture's last track ends at 299).
        # end 300 (equal) and end 350 (past) are both outside the model -> UNKNOWN.
        for end in (300, 350):
            medium = {"synthetic": True, "leadout_lba": 300,
                      "tracks": [{"no": 1, "type": "data", "start": 0, "end": end}]}
            with self.subTest(end=end):
                self.assertEqual(judge_mod.judge(medium, 1)[0], judge_mod.UNKNOWN)

    def test_track_number_zero_or_negative_on_medium_is_unknown_not_positive(self):
        # Hand-derived: track numbers start at 1 (the fixture says "track number zero is not a track").
        # A medium that lists track 0 or -1 is outside the model -> UNKNOWN for every query, including the 0/-1 query.
        for no in (0, -1):
            medium = {"synthetic": True, "leadout_lba": 300,
                      "tracks": [{"no": no, "type": "data", "start": 0, "end": 99}]}
            with self.subTest(no=no):
                self.assertEqual(judge_mod.judge(medium, no)[0], judge_mod.UNKNOWN)
                self.assertEqual(judge_mod.judge(medium, 1)[0], judge_mod.UNKNOWN)

    def test_overlapping_tracks_are_unknown_not_positive(self):
        # Hand-derived: track 1 covers LBA 0..150 and track 2 starts at 100, inside track 1 -> they overlap -> UNKNOWN.
        medium = {"synthetic": True, "leadout_lba": 300, "tracks": [
            {"no": 1, "type": "data", "start": 0, "end": 150},
            {"no": 2, "type": "audio", "start": 100, "end": 199}]}
        self.assertEqual(judge_mod.judge(medium, 1)[0], judge_mod.UNKNOWN)
        self.assertEqual(judge_mod.judge(medium, 2)[0], judge_mod.UNKNOWN)

    def test_out_of_order_tracks_are_unknown_not_positive(self):
        # Hand-derived: the list has track 2 (LBA 100..199) before track 1 (LBA 0..99). Track numbers and LBAs
        # must both ascend along the list, so this order is outside the model -> UNKNOWN.
        medium = {"synthetic": True, "leadout_lba": 300, "tracks": [
            {"no": 2, "type": "audio", "start": 100, "end": 199},
            {"no": 1, "type": "data", "start": 0, "end": 99}]}
        self.assertEqual(judge_mod.judge(medium, 1)[0], judge_mod.UNKNOWN)
        self.assertEqual(judge_mod.judge(medium, 2)[0], judge_mod.UNKNOWN)

    def test_cli_prints_pinned_hash_and_agreeing_case_lines(self):
        out = subprocess.run([sys.executable, str(ROOT / "tools" / "cdcontnts_track_judge.py"), str(FIXTURE)],
                             capture_output=True, text=True, check=True).stdout.splitlines()
        self.assertEqual(out[0], f"fixture_sha256={PINNED_SHA256}")
        self.assertEqual(len(out) - 1, len(self.fixture["cases"]))
        for line in out[1:]:
            self.assertRegex(line, r"^\S+: expected=(POSITIVE|NEGATIVE) judged=(POSITIVE|NEGATIVE)$")
            expected, judged = (part.split("=")[1] for part in line.split(": ")[1].split(" "))
            self.assertEqual(expected, judged, line)


if __name__ == "__main__":
    unittest.main()
