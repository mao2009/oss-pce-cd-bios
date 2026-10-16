"""Host-side checks for tools/cd_base_model.py (Issue #11).

Pure arithmetic only. Nothing here runs an emulator or proves the CD_BASE callee ABI.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools import cd_base_model as m


class MsfTests(unittest.TestCase):
    def test_absolute_msf_00_02_00_is_lba_0(self):
        self.assertEqual(m.absolute_msf_to_lba(0, 2, 0), 0)

    def test_msf_to_frames_and_back(self):
        self.assertEqual(m.msf_to_frames(0, 0, 1), 1)
        self.assertEqual(m.msf_to_frames(1, 0, 0), 4500)
        self.assertEqual(m.frames_to_msf(4500), (1, 0, 0))
        self.assertEqual(m.frames_to_msf(74), (0, 0, 74))

    def test_lba_to_absolute_msf(self):
        self.assertEqual(m.lba_to_absolute_msf(0), (0, 2, 0))
        self.assertEqual(m.lba_to_absolute_msf(16), (0, 2, 16))

    def test_msf_field_limits_are_rejected(self):
        for bad in [(0, 60, 0), (0, 0, 75), (-1, 0, 0), (100, 0, 0), (0, -1, 0), (0, 0, -1)]:
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    m.msf_to_frames(*bad)

    def test_absolute_msf_below_lead_in_is_rejected(self):
        with self.assertRaises(ValueError):
            m.absolute_msf_to_lba(0, 1, 74)  # LBA -1

    def test_non_integer_msf_is_rejected(self):
        for bad in [(True, 0, 0), (0.0, 0, 0), ("0", 0, 0), (None, 0, 0)]:
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    m.msf_to_frames(*bad)


class IndexBoundaryTests(unittest.TestCase):
    # Case A: virtual CUE PREGAP 00:02:00 + INDEX 01 00:00:00.
    # LBA 0..149 are not stored in the BIN; INDEX 01 (data start) is LBA 150.
    def test_virtual_pregap_boundaries(self):
        index01 = 150
        pregap_start = m.virtual_pregap_start(index01, 150)
        self.assertEqual(pregap_start, 0)
        self.assertEqual(m.region(0, index01, pregap_start, 166), "pregap")
        self.assertEqual(m.region(149, index01, pregap_start, 166), "pregap")
        self.assertEqual(m.region(150, index01, pregap_start, 166), "data")

    def test_pregap_longer_than_lba_space_start_is_rejected(self):
        with self.assertRaises(ValueError):
            m.virtual_pregap_start(10, 150)

    # Case B: INDEX 00 00:00:00 + INDEX 01 00:02:00 (pregap stored in the BIN).
    # Same disc LBAs as case A; the boundary sits at LBA 150 either way.
    def test_index00_pregap_boundaries(self):
        index00, index01 = 0, 150
        self.assertEqual(m.region(0, index01, index00, 166), "pregap")
        self.assertEqual(m.region(149, index01, index00, 166), "pregap")
        self.assertEqual(m.region(150, index01, index00, 166), "data")

    # Case C: INDEX 01 00:00:00 with no pregap (fixture layout): LBA n is BIN sector n.
    def test_no_pregap_index01_at_lba0(self):
        self.assertEqual(m.region(0, 0, None, 16), "data")
        self.assertEqual(m.region(15, 0, None, 16), "data")

    def test_region_rejects_negative_lba(self):
        with self.assertRaises(ValueError):
            m.region(-1, 0, None, 16)

    def test_index01_relative_is_signed_offset(self):
        self.assertEqual(m.index01_relative(150, 150), 0)
        self.assertEqual(m.index01_relative(149, 150), -1)
        self.assertEqual(m.index01_relative(165, 150), 15)


class TrackEndTests(unittest.TestCase):
    # Track with INDEX01=150 and length 16 covers LBA [150, 166); the end is exclusive.
    # Virtual PREGAP 150 frames -> pregap starts at LBA 0.
    def test_last_lba_inside_track_is_data(self):
        self.assertEqual(m.region(165, 150, 0, 166), "data")

    def test_end_lba_is_after_track_not_data(self):
        self.assertEqual(m.region(166, 150, 0, 166), "after_track")
        self.assertEqual(m.region(166, 150, None, 166), "after_track")

    def test_lba_far_past_end_is_after_track(self):
        self.assertEqual(m.region(0x1000, 150, 0, 166), "after_track")

    def test_track_start_is_data_and_pregap_before_it(self):
        self.assertEqual(m.region(150, 150, 0, 166), "data")
        self.assertEqual(m.region(0, 150, 0, 166), "pregap")

    def test_before_pregap_is_before_track(self):
        self.assertEqual(m.region(0, 150, 1, 166), "before_track")
        self.assertEqual(m.region(0, 150, None, 166), "before_track")

    def test_invalid_track_bounds_are_rejected(self):
        for index01, end in ((150, 150), (150, 149), (150, 0)):  # zero or negative length
            with self.subTest(index01=index01, end=end):
                with self.assertRaises(ValueError):
                    m.region(150, index01, None, end)

    def test_end_outside_24bit_is_rejected(self):
        with self.assertRaises(ValueError):
            m.region(0, 150, None, 0x1000000)
        with self.assertRaises(ValueError):
            m.region(0, 150, None, -1)


class BlockBaseTests(unittest.TestCase):
    def test_absolute_base_zero_is_identity(self):
        self.assertEqual(m.lba_to_block(16, 0), 16)

    def test_index01_base_is_relative_to_data_start(self):
        self.assertEqual(m.lba_to_block(150, 150), 0)
        self.assertEqual(m.lba_to_block(165, 150), 15)

    def test_lba_below_base_is_rejected_not_wrapped(self):
        with self.assertRaises(ValueError):
            m.lba_to_block(149, 150)

    def test_round_trip(self):
        for base in (0, 150, 0x100):
            for lba in (base, base + 1, base + 0x1234):
                with self.subTest(base=base, lba=lba):
                    self.assertEqual(m.block_to_lba(m.lba_to_block(lba, base), base), lba)


class Range24Tests(unittest.TestCase):
    def test_24bit_bounds_accepted(self):
        self.assertEqual(m.check_u24(0, "x"), 0)
        self.assertEqual(m.check_u24(0xFFFFFF, "x"), 0xFFFFFF)

    def test_24bit_out_of_range_rejected(self):
        for bad in (-1, 0x1000000, 2**32):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    m.check_u24(bad, "x")

    def test_non_integer_rejected(self):
        for bad in (True, 1.0, "1", None):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    m.check_u24(bad, "x")

    def test_block_result_out_of_24bit_is_rejected(self):
        with self.assertRaises(ValueError):
            m.block_to_lba(0x1000000, 0)  # base + block would leave the 24-bit space
        with self.assertRaises(ValueError):
            m.lba_to_block(0x1000000, 0)

    def test_read6_lba_field_is_21_bit(self):
        self.assertTrue(m.fits_read6_lba(0x1FFFFF))
        self.assertFalse(m.fits_read6_lba(0x200000))
        self.assertFalse(m.fits_read6_lba(-1))


if __name__ == "__main__":
    unittest.main()
