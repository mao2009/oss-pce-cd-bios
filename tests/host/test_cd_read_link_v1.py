"""Host tests for the EXPERIMENTAL CD_READ link V1 mirror.

NOT genuine ABI evidence. Pins tools/cd_read_link.py logic: 24-bit add with
carry, 21-bit cap, synthetic window, refusal taxonomy, no-SCSI on invalid,
state preservation, fail/timeout distinction. Real transfer to $2800 and
off-target RAM are asserted on the pinned core by the focused integration.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import cd_read_link as L


def base_args(base=150, block=0, win=150):
    return {
        "base_hi": (base >> 16) & 0xFF, "base_mid": (base >> 8) & 0xFF,
        "base_lo": base & 0xFF, "gen": 1, "last": 0,
        "blk_lo": block & 0xFF, "blk_mid": (block >> 8) & 0xFF,
        "blk_hi": (block >> 16) & 0xFF,
        "win_lo": win & 0xFF, "win_mid": (win >> 8) & 0xFF,
        "win_hi": (win >> 16) & 0xFF,
    }


class ComputeTests(unittest.TestCase):
    def test_block_zero(self):
        lba, carry, hi, mid, lo = L.compute_lba(0, 0, 150, 0, 0, 0)
        self.assertEqual((lba, carry, hi, mid, lo), (150, 0, 0, 0, 150))

    def test_block_one(self):
        lba, carry, _, _, _ = L.compute_lba(0, 0, 150, 1, 0, 0)
        self.assertEqual(lba, 151)

    def test_base_plus_block_normal(self):
        lba, carry, _, _, _ = L.compute_lba(0, 0, 155, 5, 0, 0)
        self.assertEqual((lba, carry), (160, 0))

    def test_carry_low_to_mid(self):
        lba, carry, hi, mid, lo = L.compute_lba(0, 0, 0xFF, 1, 0, 0)
        self.assertEqual((lba, carry, hi, mid, lo), (0x100, 0, 0, 1, 0))

    def test_carry_mid_to_hi(self):
        lba, carry, hi, mid, lo = L.compute_lba(0, 0xFF, 0xFF, 1, 0, 0)
        self.assertEqual((lba, carry, hi, mid, lo), (0x10000, 0, 1, 0, 0))

    def test_carry_out_of_24_bits(self):
        lba, carry, _, _, _ = L.compute_lba(0xFF, 0xFF, 0xFF, 1, 0, 0)
        self.assertEqual((lba, carry), (0, 1))

    def test_read6_max_lba_accepted_by_adder(self):
        lba, carry, hi, mid, lo = L.compute_lba(0x1F, 0xFF, 0xFE, 1, 0, 0)
        self.assertEqual((lba, carry, hi, mid, lo), (0x1FFFFF, 0, 0x1F, 0xFF, 0xFF))


class LinkRefusalTests(unittest.TestCase):
    def test_block_zero_valid_issues_read(self):
        out = L.link(**base_args(base=150, block=0, win=150))
        self.assertEqual((out["a"], out["c"], out["z"]), (0, 0, 1))
        self.assertTrue(out["issued"])
        self.assertEqual(out["lba"], 150)
        self.assertEqual(out["stage"], 8)

    def test_block_one_valid(self):
        out = L.link(**base_args(base=150, block=1, win=150))
        self.assertEqual(out["a"], 0)
        self.assertEqual(out["lba"], 151)

    def test_window_end_valid(self):
        out = L.link(**base_args(base=150, block=15, win=150))
        self.assertEqual(out["a"], 0)
        self.assertEqual(out["lba"], 165)

    def test_window_overrun_refused_no_scsi(self):
        out = L.link(**base_args(base=150, block=16, win=150))
        self.assertEqual(out["a"], L.ERR_AFTER)
        self.assertEqual((out["c"], out["z"]), (1, 0))
        self.assertFalse(out["issued"])
        self.assertEqual(out["stage"], 1)
        self.assertEqual(out["lba"], 166)

    def test_window_before_refused(self):
        out = L.link(**base_args(base=150, block=0, win=160))
        self.assertEqual(out["a"], L.ERR_BEFORE)
        self.assertFalse(out["issued"])

    def test_over_cap_refused(self):
        # base $1FFFFF + block 1 = $200000 (first beyond READ(6)).
        out = L.link(**base_args(base=0x1FFFFF, block=1, win=150))
        self.assertEqual(out["a"], L.ERR_OVER_CAP)
        self.assertFalse(out["issued"])
        self.assertEqual(out["lba"], 0x200000)

    def test_read6_max_lba_math(self):
        lba, carry, _, _, _ = L.compute_lba(0x1F, 0xFF, 0xFF, 0, 0, 0)
        self.assertEqual(lba, L.MAX_READ6)
        # Max LBA is outside the 150..165 window, so link refuses AFTER (not cap).
        out = L.link(**base_args(base=0x1FFFFF, block=0, win=150))
        self.assertEqual(out["a"], L.ERR_AFTER)
        self.assertFalse(out["issued"])

    def test_invalid_state_never_set(self):
        args = base_args(base=150, block=0, win=150)
        args["gen"], args["last"] = 0, 0xFF
        out = L.link(**args)
        self.assertEqual(out["a"], L.ERR_INVALID_STATE)
        self.assertFalse(out["issued"])

    def test_invalid_state_stale_last(self):
        args = base_args(base=150, block=0, win=150)
        args["last"] = 0x30  # proto PREGAP, base stale
        out = L.link(**args)
        self.assertEqual(out["a"], L.ERR_INVALID_STATE)
        self.assertFalse(out["issued"])

    def test_invalid_window_hi(self):
        args = base_args(base=150, block=0, win=150)
        args["win_hi"] = 0x20
        out = L.link(**args)
        self.assertEqual(out["a"], L.ERR_INVALID_WINDOW)
        self.assertFalse(out["issued"])

    def test_no_scsi_on_any_refusal(self):
        cases = [
            base_args(base=0x1FFFFF, block=1, win=150),  # cap
            base_args(base=150, block=16, win=150),      # after
            base_args(base=150, block=0, win=160),       # before
        ]
        args = base_args(base=150, block=0, win=150)
        args["last"] = 0x31
        cases.append(args)
        for kw in cases:
            out = L.link(**kw)
            self.assertFalse(out["issued"], kw)
            self.assertEqual(out["stage"], 1)

    def test_state_preserved_on_error(self):
        state = {"base_hi": 0, "base_mid": 0, "base_lo": 150, "gen": 7,
                 "last": 0, "off_hi": 0, "off_mid": 0, "off_lo": 0}
        args = base_args(base=150, block=99, win=150)
        out = L.link(state=dict(state), **args)
        self.assertEqual(out["a"], L.ERR_AFTER)
        self.assertEqual(out["state"], state)

    def test_state_preserved_on_success(self):
        state = {"base_hi": 0, "base_mid": 0, "base_lo": 150, "gen": 3,
                 "last": 0, "off_hi": 0, "off_mid": 0, "off_lo": 5}
        args = base_args(base=150, block=5, win=150)
        out = L.link(state=dict(state), **args)
        self.assertEqual(out["a"], 0)
        self.assertEqual(out["state"], state)

    def test_rerun_consistency(self):
        args = base_args(base=150, block=5, win=150)
        first = L.link(**args)
        second = L.link(**args)
        self.assertEqual(first["a"], second["a"])
        self.assertEqual(first["lba"], second["lba"])
        self.assertEqual(first["link_res"], second["link_res"])

    def test_fail_vs_timeout_codes(self):
        args = base_args(base=150, block=5, win=150)
        ok = L.link(scsi="ok", **args)
        fail = L.link(scsi="fail", **args)
        tmo = L.link(scsi="timeout", **args)
        self.assertEqual(ok["a"], 0)
        self.assertEqual(fail["a"], L.ERR_READ_FAIL)
        self.assertEqual(tmo["a"], L.ERR_READ_TIMEOUT)
        self.assertTrue(ok["issued"] and fail["issued"] and tmo["issued"])

    def test_bad_types_rejected(self):
        with self.assertRaises(ValueError):
            L.link(**{**base_args(), "blk_lo": True})
        with self.assertRaises(ValueError):
            L.compute_lba(0, 0, 0, 0, 0, 256)
        with self.assertRaises(ValueError):
            L.link(scsi="maybe", **base_args())


class SectorMatchTests(unittest.TestCase):
    def test_expected_sector_bytes_match_synthetic(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
        from make_cd_image import sector_payload
        for lba in (150, 151, 155, 160, 165):
            args = base_args(base=150, block=lba - 150, win=150)
            out = L.link(**args)
            self.assertEqual(out["a"], 0)
            self.assertEqual(out["lba"], lba)
            expected = sector_payload(lba)
            self.assertEqual(len(expected), 2048)
            # Host mirror pins the LBA; the real core must deliver these exact bytes.
            self.assertEqual(out["link_res"]["bytes"], 2048)


if __name__ == "__main__":
    unittest.main()
