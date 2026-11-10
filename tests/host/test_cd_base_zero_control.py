"""Host tests for the CD_BASE INDEX01=0 control fixture (Issue #735).

The control disc has no pregap: INDEX 01 is disc LBA 0, so absolute and
track-relative addressing coincide and the judge must stay UNKNOWN/BLOCKED.
SYNTHETIC inputs only; nothing about the System Card callee.
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import cd_base_probe as h  # noqa: E402
from cue_analysis import user_sector  # noqa: E402
from make_cd_image import sector_payload  # noqa: E402


class ZeroFixtureTest(unittest.TestCase):
    def test_zero_fixture_index01_is_lba0(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = h.build_zero_fixture(Path(tmp))
            track = report["tracks"][0]
            self.assertEqual(track["start_lba"], 0)
            cue = Path(tmp) / h.ZERO_CUE_NAME
            self.assertEqual(user_sector(cue, report, 5), sector_payload(5))

    def test_zero_fixture_names_differ_from_original(self):
        self.assertNotEqual(h.ZERO_CUE_NAME, h.CUE_NAME)
        self.assertNotEqual(h.ZERO_BIN_NAME, h.BIN_NAME)


class ZeroRecordTest(unittest.TestCase):
    def test_fallback_rows_with_index01_zero_become_blocked(self):
        rows = [{"pc": 0xF000, "x": 0}] + [{"pc": 0xE100, "x": 2}] * 3
        rec = h.record_from_api_rows(rows, 0xE100, index01=0)
        self.assertEqual(rec["fixture_index01_lba"], 0)
        out = h.judge(rec)
        self.assertEqual({v["status"] for v in out.values()}, {"BLOCKED"})

    def test_default_index01_is_unchanged(self):
        rows = [{"pc": 0xF000, "x": 0}] + [{"pc": 0xE100, "x": 2}] * 3
        rec = h.record_from_api_rows(rows, 0xE100)
        self.assertEqual(rec["fixture_index01_lba"], h.INDEX01_LBA)


if __name__ == "__main__":
    unittest.main()
