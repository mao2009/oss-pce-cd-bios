"""Host confirmation of the uncompleted CD_BASE conditions (Issue #735).

SYNTHETIC inputs only. Nothing here runs the System Card callee, executes a
genuine JSR (jsr_count stays 0), or establishes a BIOS ABI. Every uncompleted
ledger question stays UNKNOWN when nothing was recorded and BLOCKED when no
executable callee exists; invalid LBA / out-of-range inputs are rejected with
ValueError and never yield OBSERVED/VERIFIED. The INDEX01=150 + INDEX01=0
fixtures are reused from tools/cd_base_probe.py (PR #750); they are not
reimplemented here.
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import cd_base_dev_boundary as B  # noqa: E402
import cd_base_probe as h  # noqa: E402
from cue_analysis import CueError, user_sector  # noqa: E402
from make_cd_image import sector_payload  # noqa: E402


def returned(**fields):
    return {"callee": "returned", "fixture_index01_lba": h.INDEX01_LBA, **fields}


def plain_pair(offset):
    return [{"block": 5, "read_lba": 5 + offset}, {"block": 6, "read_lba": 6 + offset}]


class PositiveJudgeStillWorksTests(unittest.TestCase):
    def test_valid_constant_offset_is_observed_not_verified(self):
        out = h.judge(returned(plain_reads=plain_pair(150)))
        self.assertEqual(out["(2)"]["status"], "OBSERVED")
        self.assertIn("TRACK_START_PLUS_BLOCK", out["(2)"]["consistent_with"])
        self.assertNotIn("VERIFIED", str(out))

    def test_both_fixtures_build_with_expected_index01(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            rep150 = h.build_fixture(folder)
            self.assertEqual(rep150["tracks"][0]["start_lba"], h.INDEX01_LBA)
            rep0 = h.build_zero_fixture(folder)
            self.assertEqual(rep0["tracks"][0]["start_lba"], 0)
            self.assertEqual(user_sector(folder / h.CUE_NAME, rep150, 155), sector_payload(155))
            self.assertEqual(user_sector(folder / h.ZERO_CUE_NAME, rep0, 5), sector_payload(5))


class NegativeOutOfRangeTests(unittest.TestCase):
    def test_out_of_range_block_rejected(self):
        for bad in (-1, 0x1000000, 2 ** 32):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    h.judge(returned(plain_reads=[{"block": bad, "read_lba": 155},
                                                 {"block": 6, "read_lba": 156}]))

    def test_out_of_range_read_lba_is_verbatim_never_verified(self):
        # read_lba is the harness-reported SCSI value: type-checked, reported
        # verbatim, and never promoted to VERIFIED. (Caller block inputs above
        # are the range-rejected side; see test_out_of_range_block_rejected.)
        out = h.judge(returned(plain_reads=[{"block": 5, "read_lba": 5 - 150},
                                             {"block": 200, "read_lba": 200 - 150}]))
        self.assertEqual(out["(2)"]["status"], "OBSERVED")
        self.assertNotIn("VERIFIED", str(out))

    def test_out_of_range_index01_rejected(self):
        for bad in (-1, 0x1000000, True, 5.0, "150"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    h.judge(returned(fixture_index01_lba=bad, plain_reads=plain_pair(150)))

    def test_non_integer_block_and_lba_rejected(self):
        good = {"block": 5, "read_lba": 155}
        for bad_block in (True, 5.0, "5", None):
            with self.subTest(bad_block=bad_block):
                with self.assertRaises(ValueError):
                    h.judge(returned(plain_reads=[{"block": bad_block, "read_lba": 155}, good]))
        for bad_lba in (True, 5.0, "155"):
            with self.subTest(bad_lba=bad_lba):
                with self.assertRaises(ValueError):
                    h.judge(returned(plain_reads=[{"block": 5, "read_lba": bad_lba}, good]))

    def test_invalid_lba_never_readable_from_fixtures(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            rep150 = h.build_fixture(folder)
            rep0 = h.build_zero_fixture(folder)
            with self.assertRaises(CueError):  # LBA 5 is virtual pregap on the 150 disc
                user_sector(folder / h.CUE_NAME, rep150, 5)
            with self.assertRaises(CueError):  # past the 16-sector data run on either disc
                user_sector(folder / h.CUE_NAME, rep150, 166)
            with self.assertRaises(CueError):
                user_sector(folder / h.ZERO_CUE_NAME, rep0, 16)

    def test_boundary_judge_rejects_bad_block_fail_closed(self):
        for bad in (-1, True, 0x1000000):
            with self.subTest(bad=bad):
                rec = B.judge({"mode": "ABS_LBA", "block": bad})
                self.assertEqual(rec["verdict"], "BLOCKED")
                self.assertEqual(rec["reason"], "INVALID_BLOCK")


class MutationUncompletedStaysTests(unittest.TestCase):
    def test_index01_zero_collapse_stays_unknown(self):
        out = h.judge(returned(fixture_index01_lba=0, plain_reads=plain_pair(0)))
        self.assertEqual(out["(2)"]["status"], "UNKNOWN")
        based = [{"inputs": {"_al": 1}, "block": 5, "read_lba": 300},
                 {"inputs": {"_al": 1}, "block": 6, "read_lba": 301}]
        out = h.judge(returned(fixture_index01_lba=0, plain_reads=plain_pair(0), cd_base_reads=based))
        self.assertEqual((out["(2)"]["status"], out["(4)"]["status"]), ("UNKNOWN", "UNKNOWN"))

    def test_null_read_and_single_block_never_observed(self):
        out = h.judge(returned(plain_reads=[{"block": 5, "read_lba": 155},
                                            {"block": 6, "read_lba": None}]))
        self.assertEqual(out["(2)"]["status"], "UNKNOWN")
        out = h.judge(returned(plain_reads=[{"block": 5, "read_lba": 155}]))
        self.assertEqual(out["(2)"]["status"], "UNKNOWN")
        inputs = {"_al": 1}
        based = [{"inputs": inputs, "block": 5, "read_lba": None},
                 {"inputs": inputs, "block": 6, "read_lba": None}]
        out = h.judge(returned(plain_reads=plain_pair(150), cd_base_reads=based))
        self.assertEqual(out["(4)"]["status"], "UNKNOWN")

    def test_non_constant_offset_stays_unknown(self):
        out = h.judge(returned(plain_reads=[{"block": 5, "read_lba": 5},
                                            {"block": 6, "read_lba": 200}]))
        self.assertEqual(out["(2)"]["status"], "UNKNOWN")

    def test_fallback_record_blocks_all_six_questions(self):
        rows = [{"pc": 0xF000, "x": 0}] + [{"pc": 0xE100, "x": 2}] * 3
        for index01 in (h.INDEX01_LBA, 0):
            with self.subTest(index01=index01):
                rec = h.record_from_api_rows(rows, 0xE100, index01=index01)
                out = h.judge(rec)
                self.assertEqual({v["status"] for v in out.values()}, {"BLOCKED"})

    def test_boundary_never_emits_observed_or_verified(self):
        cases = [
            {"mode": "ABS_LBA", "block": 1},
            {"mode": "ABS_LBA", "block": 1, "callee_observed": True},
            {"mode": "UNKNOWN_X", "block": 1, "callee_observed": True},
            "not a dict",
        ]
        for case in cases:
            with self.subTest(case=str(case)):
                verdict = B.judge(case)["verdict"]
                self.assertEqual(verdict, "BLOCKED")
                self.assertNotIn(verdict, ("OBSERVED", "VERIFIED"))

    def test_side_effects_stay_unknown_and_release_not_eligible(self):
        for claimed in (False, True):
            matrix = B.side_effect_matrix(claimed)
            self.assertTrue(all(v == "UNKNOWN" for v in matrix.values()), claimed)
        self.assertFalse(B.RELEASE_ELIGIBLE)
        self.assertFalse(B.plan_fixture(150)["release_eligible"])
        self.assertFalse(B.plan_fixture(0)["release_eligible"])


if __name__ == "__main__":
    unittest.main()
