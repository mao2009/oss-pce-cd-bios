"""Host tests for the EXPERIMENTAL CD_READ commercial first-mode mirror.

NOT genuine ABI evidence. Pins tools/cd_read_commercial.py logic: commercial
block at $20FC-$20FE (HI/MID/LO) with 24-bit carry, 21-bit cap, INDEX01 lower
bound with NO synthetic window end, first-mode-only guards (_dh=0, size 2048,
dest $2800), refusal taxonomy, no-$1800 on invalid, $20F2 independence,
fail/timeout distinction. Real transfer to $2800 and off-target RAM are asserted
on the pinned core by the focused integration test.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import cd_read_commercial as C


def base_args(base=0x109, block=0, win=0x109, mode=0,
              size_lo=0, size_hi=8, dst_lo=0, dst_hi=0x28, last=0):
    return {
        "base_hi": (base >> 16) & 0xFF, "base_mid": (base >> 8) & 0xFF,
        "base_lo": base & 0xFF, "gen": 1, "last": last,
        "cblk_hi": (block >> 16) & 0xFF, "cblk_mid": (block >> 8) & 0xFF,
        "cblk_lo": block & 0xFF,
        "win_lo": win & 0xFF, "win_mid": (win >> 8) & 0xFF,
        "win_hi": (win >> 16) & 0xFF,
        "mode": mode, "size_lo": size_lo, "size_hi": size_hi,
        "dst_lo": dst_lo, "dst_hi": dst_hi,
    }


class ComputeTests(unittest.TestCase):
    def test_rel_hi(self):
        lba, carry, hi, mid, lo = C.compute_lba(0, 0, 0, 1, 0, 0)
        self.assertEqual((lba, carry, hi, mid, lo), (0x10000, 0, 1, 0, 0))

    def test_rel_mid(self):
        lba, carry, hi, mid, lo = C.compute_lba(0, 0, 0, 0, 1, 0)
        self.assertEqual((lba, carry, hi, mid, lo), (0x100, 0, 0, 1, 0))

    def test_rel_lo(self):
        lba, carry, hi, mid, lo = C.compute_lba(0, 0, 0, 0, 0, 5)
        self.assertEqual((lba, carry, hi, mid, lo), (5, 0, 0, 0, 5))

    def test_plus_recbase0(self):
        lba, carry, _, _, _ = C.compute_lba(0, 1, 9, 0, 0, 1)
        self.assertEqual((lba, carry), (0x10A, 0))

    def test_carry_24bit_reports_wrapped(self):
        lba, carry, hi, mid, lo = C.compute_lba(0xFF, 0xFF, 0xFF, 0, 0, 1)
        self.assertEqual((lba, carry, hi, mid, lo), (0, 1, 0, 0, 0))


class RefusalTests(unittest.TestCase):
    def test_block_zero_valid(self):
        out = C.link(**base_args(base=0x109, block=0, win=0x109))
        self.assertEqual((out["a"], out["c"], out["z"]), (0, 0, 1))
        self.assertTrue(out["issued"])
        self.assertEqual(out["lba"], 0x109)
        self.assertEqual(out["stage"], 8)

    def test_window_end_plus16_no_after(self):
        # Link V1 reports $64 here; commercial first-mode accepts (no window end).
        out = C.link(**base_args(base=0x109, block=16, win=0x109))
        self.assertEqual(out["a"], 0)
        self.assertTrue(out["issued"])
        self.assertEqual(out["lba"], 0x119)

    def test_after_never_returned(self):
        for block in (0, 1, 15, 16, 17, 100, 0xFFFF):
            out = C.link(**base_args(base=0x109, block=block, win=0x109))
            self.assertNotEqual(out["a"], C.ERR_AFTER,
                                f"block {block:#x} returned reserved $64")

    def test_before_pregap_guard(self):
        out = C.link(**base_args(base=0, block=5, win=0x109))
        self.assertEqual(out["a"], C.ERR_BEFORE)
        self.assertFalse(out["issued"])
        self.assertEqual(out["stage"], 1)

    def test_21bit_cap(self):
        out = C.link(**base_args(base=0x1FFFFF, block=0, win=0x109))
        # base itself is in range; LBA == $1FFFFF is the last valid address.
        self.assertEqual(out["a"], 0)
        out = C.link(**base_args(base=0x1FFFFF, block=1, win=0x109))
        self.assertEqual(out["a"], C.ERR_OVER_CAP)
        self.assertFalse(out["issued"])


class FirstModeGuardTests(unittest.TestCase):
    def test_mode_refuse_nonzero(self):
        for mode in (1, 2, 3, 0xFE, 0xFF, 6):
            out = C.link(**base_args(mode=mode))
            self.assertEqual(out["a"], C.ERR_BAD_MODE, f"mode {mode:#x}")
            self.assertFalse(out["issued"])

    def test_size_limits(self):
        for size_lo, size_hi in ((1, 8), (0, 7), (0, 0), (0xFF, 0xFF), (0, 0x10)):
            out = C.link(**base_args(size_lo=size_lo, size_hi=size_hi))
            self.assertEqual(out["a"], C.ERR_BAD_SIZE,
                             f"size {size_hi:02x}{size_lo:02x}")
            self.assertFalse(out["issued"])

    def test_dest_bounds(self):
        for dst_lo, dst_hi in ((0, 0x20), (0, 0x30), (0, 0x38),
                               (1, 0x28), (0, 0x27), (0, 0x29),
                               (0xFF, 0xFF), (0, 0x00)):
            out = C.link(**base_args(dst_lo=dst_lo, dst_hi=dst_hi))
            self.assertEqual(out["a"], C.ERR_BAD_DEST,
                             f"dest {dst_hi:02x}{dst_lo:02x}")
            self.assertFalse(out["issued"])

    def test_20f2_independence(self):
        want = C.link(**base_args(base=0x109, block=3, win=0x109))
        for ignored in (0, 1, 0x7F, 0xFF, 0x1234, -1, 1 << 24):
            out = C.link(**base_args(base=0x109, block=3, win=0x109),
                         blk_lo_ignored=ignored)
            self.assertEqual((out["a"], out["lba"], out["issued"]),
                             (want["a"], want["lba"], want["issued"]),
                             f"$20F2 shadow {ignored!r} changed the result")

    def test_invalid_state_and_window(self):
        out = C.link(**base_args(last=0xFF))
        self.assertEqual(out["a"], C.ERR_INVALID_STATE)
        self.assertEqual((out["lhi"], out["lmid"], out["llo"]), (0, 0, 0))
        out = C.link(**base_args(win=0x200000))
        self.assertEqual(out["a"], C.ERR_INVALID_WINDOW)
        self.assertFalse(out["issued"])


class DriveOutcomeTests(unittest.TestCase):
    def test_premature_fail(self):
        out = C.link(**base_args(), scsi="premature", partial=100)
        self.assertEqual((out["a"], out["stage"], out["bytes"]),
                         (C.ERR_READ_FAIL, 5, 100))
        self.assertEqual(out["scsi_status"], 0)
        self.assertTrue(out["issued"])

    def test_tail_bad_fail(self):
        out = C.link(**base_args(), scsi="tail_bad", scsi_status=2, msg=0)
        self.assertEqual((out["a"], out["stage"], out["bytes"]),
                         (C.ERR_READ_FAIL, 8, 2048))

    def test_timeout_sites(self):
        out = C.link(**base_args(), scsi="timeout", timeout_stage=3,
                     timeout_bytes=0)
        self.assertEqual((out["a"], out["stage"], out["bytes"]),
                         (C.ERR_READ_TIMEOUT, 3, 0))
        out = C.link(**base_args(), scsi="timeout", timeout_stage=5,
                     timeout_bytes=77)
        self.assertEqual((out["a"], out["stage"], out["bytes"]),
                         (C.ERR_READ_TIMEOUT, 5, 77))


if __name__ == "__main__":
    unittest.main()
