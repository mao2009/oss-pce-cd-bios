"""Host tests for the CD_BASE measurement harness judge (Issue #11).

Records below are SYNTHETIC inputs to the judge. They test the judge's rules only and say
nothing about what the System Card CD_BASE / CD_READ callee does.
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import cd_base_probe as h  # noqa: E402
from cue_analysis import CueError, user_sector  # noqa: E402
from make_cd_image import sector_payload  # noqa: E402


def record(**fields):
    return {"callee": "returned", "fixture_index01_lba": h.INDEX01_LBA, **fields}


class FixtureTest(unittest.TestCase):
    def test_fixture_index01_is_150_and_pregap_is_not_stored(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = h.build_fixture(Path(tmp))
            track = report["tracks"][0]
            self.assertEqual((track["start_lba"], track["pregap_frames"], track["length_frames"]), (150, 150, 16))
            cue = Path(tmp) / h.CUE_NAME
            self.assertEqual(user_sector(cue, report, 155), sector_payload(155))
            with self.assertRaises(CueError):
                user_sector(cue, report, 5)  # LBA 5 is in the virtual pregap: not readable


class JudgeTest(unittest.TestCase):
    def test_no_callee_blocks_every_question(self):
        for rec in (None, {"callee": "nonreturning", "callee_reason": "stub"}):
            out = h.judge(rec)
            self.assertEqual({v["status"] for v in out.values()}, {"BLOCKED"})

    def test_returned_but_nothing_recorded_stays_unknown(self):
        self.assertEqual({v["status"] for v in h.judge(record()).values()}, {"UNKNOWN"})

    def test_plain_reads_give_candidates_never_a_verdict(self):
        cases = {0: "ABS_LBA", 150: "TRACK_START_PLUS_BLOCK", -150: "MSF_FRAMES_FROM_00_00_00"}
        for offset, name in cases.items():
            out = h.judge(record(plain_reads=[{"block": 5, "read_lba": 5 + offset},
                                              {"block": 200, "read_lba": 200 + offset}]))
            self.assertEqual(out["(2)"]["status"], "OBSERVED")
            self.assertEqual(out["(2)"]["consistent_with"], [name, f"BASE_PLUS_BLOCK(base={offset})"])
            self.assertNotIn("VERIFIED", str(out))

    def test_index01_zero_cannot_separate(self):
        out = h.judge(record(fixture_index01_lba=0, plain_reads=[{"block": 5, "read_lba": 5},
                                                                 {"block": 6, "read_lba": 6}]))
        self.assertEqual(out["(2)"]["status"], "UNKNOWN")

    def test_single_block_or_non_constant_offset_is_unknown(self):
        self.assertEqual(h.judge(record(plain_reads=[{"block": 5, "read_lba": 5}]))["(2)"]["status"], "UNKNOWN")
        out = h.judge(record(plain_reads=[{"block": 5, "read_lba": 5}, {"block": 6, "read_lba": 200}]))
        self.assertEqual(out["(2)"]["status"], "UNKNOWN")

    def test_input_sensitivity_and_relation_to_cd_read(self):
        base = {"_al": 1, "_bh": 0x80, "_cl": 2}
        rec = record(plain_reads=[{"block": 5, "read_lba": 155}, {"block": 6, "read_lba": 156}],
                     cd_base_reads=[{"inputs": inputs, "block": block, "read_lba": offset + block}
                                    for inputs, offset in ((base, 295), ({**base, "_al": 2}, 395),
                                                           ({**base, "_cl": 3}, 295))
                                    for block in (5, 6)])
        out = h.judge(rec)
        self.assertEqual(out["(1)"]["bytes_that_change_the_read"], ["_al"])
        self.assertEqual(out["(4)"]["status"], "OBSERVED")
        self.assertEqual([(g["offset"], g["changes_offset"]) for g in out["(4)"]["per_inputs"]],
                         [(295, True), (395, True), (295, True)])

    def test_relation_needs_two_blocks_per_input_set(self):
        # MAJOR-A: each input set read only one block, so no input set shows a constant offset.
        out = h.judge(record(plain_reads=[{"block": 5, "read_lba": 155}, {"block": 6, "read_lba": 156}],
                             cd_base_reads=[{"inputs": {"_al": 1}, "block": 5, "read_lba": 155},
                                            {"inputs": {"_al": 2}, "block": 6, "read_lba": 156}]))
        self.assertEqual(out["(4)"]["status"], "UNKNOWN")

    def test_relation_needs_constant_offset_per_input_set(self):
        # MAJOR-A: [300@5, 500@6] under one input set has no constant offset.
        out = h.judge(record(plain_reads=[{"block": 5, "read_lba": 155}, {"block": 6, "read_lba": 156}],
                             cd_base_reads=[{"inputs": {"_al": 1}, "block": 5, "read_lba": 300},
                                            {"inputs": {"_al": 1}, "block": 6, "read_lba": 500}]))
        self.assertEqual(out["(4)"]["status"], "UNKNOWN")
        self.assertIn("no constant offset", out["(4)"]["reason"])

    def test_null_read_is_not_input_sensitivity_evidence(self):
        # MINOR-B
        out = h.judge(record(cd_base_reads=[{"inputs": {"_al": 1}, "block": 5, "read_lba": None},
                                            {"inputs": {"_al": 2}, "block": 5, "read_lba": 300}]))
        self.assertEqual(out["(1)"]["status"], "UNKNOWN")

    def test_wrong_types_are_rejected(self):
        # MINOR-C
        plain = [{"block": 5, "read_lba": 155}, {"block": 6, "read_lba": 156}]
        for bad in (record(fixture_index01_lba="150", plain_reads=plain),
                    record(plain_reads=[{"block": True, "read_lba": 155}, plain[1]]),
                    record(plain_reads=[{"block": 5.0, "read_lba": 155}, plain[1]]),
                    record(plain_reads=[{"block": 5, "read_lba": 155.0}, plain[1]])):
            with self.assertRaises(ValueError):
                h.judge(bad)

    def test_null_read_rows_do_not_count_as_blocks(self):
        # MAJOR-1: only one block was actually read, so (2) must not be decided.
        out = h.judge(record(plain_reads=[{"block": 5, "read_lba": 155}, {"block": 6, "read_lba": None}]))
        self.assertEqual(out["(2)"]["status"], "UNKNOWN")

    def test_relation_needs_two_cd_base_blocks_read(self):
        # MAJOR-2: CD_BASE-side reads all null, or only one block read -> (4) UNKNOWN.
        plain = [{"block": 5, "read_lba": 155}, {"block": 6, "read_lba": 156}]
        inputs = {"_al": 1}
        for based in ([{"inputs": inputs, "block": 5, "read_lba": None}, {"inputs": inputs, "block": 6, "read_lba": None}],
                      [{"inputs": inputs, "block": 5, "read_lba": 300}, {"inputs": inputs, "block": 6, "read_lba": None}]):
            self.assertEqual(h.judge(record(plain_reads=plain, cd_base_reads=based))["(4)"]["status"], "UNKNOWN")

    def test_relation_unknown_when_index01_zero(self):
        # MAJOR-2: (2) UNKNOWN on an INDEX 01 = 0 disc implies (4) UNKNOWN.
        out = h.judge(record(fixture_index01_lba=0,
                             plain_reads=[{"block": 5, "read_lba": 5}, {"block": 6, "read_lba": 6}],
                             cd_base_reads=[{"inputs": {"_al": 1}, "block": 5, "read_lba": 300},
                                            {"inputs": {"_al": 1}, "block": 6, "read_lba": 301}]))
        self.assertEqual((out["(2)"]["status"], out["(4)"]["status"]), ("UNKNOWN", "UNKNOWN"))

    def test_side_effects_and_returns_are_reported_verbatim(self):
        rec = record(side_effects={"mpr_before": [0xff, 0xf8, 1, 2, 3, 4, 5, 0], "mpr_after": [0xff, 0xf8, 1, 9, 3, 4, 5, 0],
                                   "ram_base": 0x2000, "ram_before_hex": "0000", "ram_after_hex": "0001"},
                     returns=[{"case": "no-disc", "a": 1, "x": 0, "y": 0, "p": 0}])
        out = h.judge(rec)
        self.assertEqual((out["(5)"]["mpr_changed"], out["(5)"]["ram_changed"]), ([3], [0x2001]))
        self.assertEqual(out["(6)"]["returns"][0]["case"], "no-disc")

    def test_api_rows_for_fallback_become_blocked_record(self):
        rows = [{"pc": 0xF000, "x": 0}] + [{"pc": 0xE100, "x": 2}] * 3
        self.assertEqual(h.judge(h.record_from_api_rows(rows, 0xE100))["(1)"]["status"], "BLOCKED")
        with self.assertRaises(ValueError):
            h.record_from_api_rows([{"pc": 0}, {"pc": 0xE200, "x": 2}], 0xE100)
        for empty in ([], [{"pc": 0xF000, "x": 0}]):  # MINOR-3: no post-entry rows is not evidence
            with self.assertRaises(ValueError):
                h.record_from_api_rows(empty, 0xE100)


if __name__ == "__main__":
    unittest.main()
