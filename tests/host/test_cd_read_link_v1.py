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

    def test_24bit_wrap_reports_wrapped_lba(self):
        # LINK_RES LBA spec (OVER_CAP via carry): low 24 bits of base+block.
        # base $FFFFFF + block 1 = $1000000 -> carry, wrapped $000000, $62.
        out = L.link(**base_args(base=0xFFFFFF, block=1, win=150))
        self.assertEqual(out["a"], L.ERR_OVER_CAP)
        self.assertEqual((out["c"], out["z"]), (1, 0))
        self.assertFalse(out["issued"])
        self.assertEqual(out["stage"], 1)
        self.assertEqual(out["lba"], 0)
        self.assertEqual((out["lba_hi"], out["lba_mid"], out["lba_lo"]),
                         (0, 0, 0))
        lr = out["link_res"]
        self.assertEqual(lr, {"code": L.ERR_OVER_CAP, "stage": 1,
                              "bytes": 0, "scsi_status": 0, "msg": 0,
                              "last_bus": 0,
                              "lba_hi": 0, "lba_mid": 0, "lba_lo": 0})

    def test_24bit_wrap_plus_two_reports_wrapped_lba_one(self):
        out = L.link(**base_args(base=0xFFFFFF, block=2, win=150))
        self.assertEqual(out["a"], L.ERR_OVER_CAP)
        self.assertFalse(out["issued"])
        self.assertEqual(out["lba"], 1)
        self.assertEqual((out["lba_hi"], out["lba_mid"], out["lba_lo"]),
                         (0, 0, 1))
        self.assertEqual((out["link_res"]["lba_hi"],
                          out["link_res"]["lba_mid"],
                          out["link_res"]["lba_lo"]), (0, 0, 1))

    def test_normal_read_then_wrap_refuse_same_execution(self):
        # Sequential calls model one guest execution: a valid READ must not
        # leak its LBA into the following 24-bit wrap refusal (stale-LR guard).
        first = L.link(**base_args(base=150, block=0, win=150))
        self.assertEqual(first["a"], 0)
        self.assertTrue(first["issued"])
        self.assertEqual(first["lba"], 150)
        self.assertEqual((first["lba_hi"], first["lba_mid"], first["lba_lo"]),
                         (0, 0, 150))
        self.assertEqual(first["link_res"]["bytes"], 2048)
        second = L.link(**base_args(base=0xFFFFFF, block=1, win=150))
        self.assertEqual(second["a"], L.ERR_OVER_CAP)
        self.assertEqual((second["c"], second["z"]), (1, 0))
        self.assertFalse(second["issued"])
        self.assertEqual(second["stage"], 1)
        self.assertEqual(second["lba"], 0)
        self.assertEqual((second["lba_hi"], second["lba_mid"],
                          second["lba_lo"]), (0, 0, 0))
        lr = second["link_res"]
        self.assertEqual(lr["code"], L.ERR_OVER_CAP)
        self.assertEqual(lr["stage"], 1)
        self.assertEqual(lr["bytes"], 0)
        self.assertEqual(lr["scsi_status"], 0)
        self.assertEqual(lr["msg"], 0)
        self.assertEqual(lr["last_bus"], 0)
        self.assertEqual((lr["lba_hi"], lr["lba_mid"], lr["lba_lo"]),
                         (0, 0, 0))

    def test_no_scsi_on_any_refusal(self):
        cases = [
            base_args(base=0x1FFFFF, block=1, win=150),  # cap (HI>=0x20)
            base_args(base=0xFFFFFF, block=1, win=150),  # cap (24-bit wrap)
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
        pre = L.link(scsi="premature", **args)
        tail = L.link(scsi="tail_bad", **args)
        tmo = L.link(scsi="timeout", **args)
        self.assertEqual(ok["a"], 0)
        self.assertEqual(pre["a"], L.ERR_READ_FAIL)
        self.assertEqual(tail["a"], L.ERR_READ_FAIL)
        self.assertEqual(tmo["a"], L.ERR_READ_TIMEOUT)
        self.assertTrue(ok["issued"] and pre["issued"]
                        and tail["issued"] and tmo["issued"])

    def test_unreachable_fail_mode_removed(self):
        # Review 5872704142: a single scsi="fail" returning stage 6 +
        # bytes 0 + scsi_status 2 is unreachable in the guest (no guest path
        # produces that combination). It must be rejected, not mirrored.
        with self.assertRaises(ValueError):
            L.link(scsi="fail", **base_args(base=150, block=5, win=150))

    def test_premature_matches_observed_guest(self):
        # Guest ground truth (pinned Geargrafx b49ae82a, off-disc LBA 150 on
        # the INDEX01=0 synthetic disc, cold AND warm): the DATA_IN wait sees
        # early STATUS, takes @premature -> code $70, stage 5, 0 bytes,
        # scsi_status 0 (STATUS byte never read), msg 0, last_bus $D8.
        # Observed LINK_RES: 70 05 00 00 00 00 D8 00 00 96.
        out = L.link(scsi="premature", **base_args(base=150, block=0, win=150))
        self.assertEqual((out["a"], out["c"], out["z"]), (0x70, 1, 0))
        self.assertTrue(out["issued"])
        self.assertEqual(out["stage"], 5)
        self.assertEqual(out["lba"], 150)
        lr = out["link_res"]
        self.assertEqual(lr, {"code": 0x70, "stage": 5, "bytes": 0,
                              "scsi_status": 0, "msg": 0, "last_bus": 0xD8,
                              "lba_hi": 0, "lba_mid": 0, "lba_lo": 150})
        self.assertEqual([lr["code"], lr["stage"], lr["bytes"] & 0xFF,
                          (lr["bytes"] >> 8) & 0xFF, lr["scsi_status"],
                          lr["msg"], lr["last_bus"], lr["lba_hi"],
                          lr["lba_mid"], lr["lba_lo"]],
                         [0x70, 5, 0, 0, 0, 0, 0xD8, 0, 0, 150])

    def test_premature_partial_bytes(self):
        # STATUS arriving mid-transfer: stage stays 5, bytes = count so far,
        # scsi_status still 0 (STATUS byte unread), last_bus still $D8.
        out = L.link(scsi="premature", partial=1000,
                     **base_args(base=150, block=5, win=150))
        self.assertEqual(out["a"], L.ERR_READ_FAIL)
        self.assertEqual(out["stage"], 5)
        lr = out["link_res"]
        self.assertEqual(lr["bytes"], 1000)
        self.assertEqual(lr["scsi_status"], 0)
        self.assertEqual(lr["msg"], 0)
        self.assertEqual(lr["last_bus"], 0xD8)
        self.assertEqual((lr["lba_hi"], lr["lba_mid"], lr["lba_lo"]),
                         (0, 0, 155))
        with self.assertRaises(ValueError):
            L.link(scsi="premature", partial=2048,
                   **base_args(base=150, block=5, win=150))

    def test_tail_bad_full_transfer(self):
        # Guest @tail_bad: 2048 bytes already moved, final FREE observed,
        # then SCSI|MSG != 0 -> code $70, stage 8, bytes 2048, last_bus $00.
        out = L.link(scsi="tail_bad", **base_args(base=150, block=5, win=150))
        self.assertEqual((out["a"], out["c"], out["z"]), (0x70, 1, 0))
        self.assertTrue(out["issued"])
        self.assertEqual(out["stage"], 8)
        lr = out["link_res"]
        self.assertEqual(lr, {"code": 0x70, "stage": 8, "bytes": 2048,
                              "scsi_status": 2, "msg": 0, "last_bus": 0x00,
                              "lba_hi": 0, "lba_mid": 0, "lba_lo": 155})

    def test_tail_bad_msg_only(self):
        out = L.link(scsi="tail_bad", scsi_status=0, msg=1,
                     **base_args(base=150, block=5, win=150))
        self.assertEqual(out["a"], L.ERR_READ_FAIL)
        self.assertEqual(out["stage"], 8)
        self.assertEqual(out["link_res"]["bytes"], 2048)
        self.assertEqual(out["link_res"]["scsi_status"], 0)
        self.assertEqual(out["link_res"]["msg"], 1)

    def test_tail_bad_good_status_rejected(self):
        # scsi_status 0 + msg 0 takes the guest success path ($00), so the
        # fault mirror must refuse to label it a failure.
        with self.assertRaises(ValueError):
            L.link(scsi="tail_bad", scsi_status=0, msg=0,
                   **base_args(base=150, block=5, win=150))

    def test_timeout_default_reachable(self):
        # Default timeout triple: DATA_IN wait expires with the drive stuck in
        # COMMAND phase (stage 5, 0 bytes, last_bus $D0) -- a reachable guest
        # @data timeout (bus shows neither DATA_IN nor STATUS, so @premature
        # does not trigger). code $71.
        out = L.link(scsi="timeout", **base_args(base=150, block=5, win=150))
        self.assertEqual((out["a"], out["c"], out["z"]), (0x71, 1, 0))
        self.assertTrue(out["issued"])
        self.assertEqual(out["stage"], 5)
        lr = out["link_res"]
        self.assertEqual(lr, {"code": 0x71, "stage": 5, "bytes": 0,
                              "scsi_status": 0, "msg": 0, "last_bus": 0xD0,
                              "lba_hi": 0, "lba_mid": 0, "lba_lo": 155})

    def test_timeout_sites_guest_flow(self):
        # Every accepted (stage, bytes) pair is a guest timeout site:
        # 2/3/4 pre-data (0 bytes), 5 data wait (partial), 6/7/8 tail (2048).
        base = base_args(base=150, block=5, win=150)
        for stage, nbytes in ((2, 0), (3, 0), (4, 0), (5, 100),
                              (6, 2048), (7, 2048), (8, 2048)):
            out = L.link(scsi="timeout", timeout_stage=stage,
                         timeout_bytes=nbytes, timeout_last=0xD0, **base)
            self.assertEqual(out["a"], L.ERR_READ_TIMEOUT, (stage, nbytes))
            self.assertEqual(out["stage"], stage, (stage, nbytes))
            self.assertEqual(out["link_res"]["stage"], stage)
            self.assertEqual(out["link_res"]["bytes"], nbytes)
            self.assertEqual(out["link_res"]["last_bus"], 0xD0)
            self.assertEqual(out["link_res"]["scsi_status"], 0)

    def test_timeout_unreachable_combos_rejected(self):
        base = base_args(base=150, block=5, win=150)
        # Stage 5 never holds 2048 bytes (full transfer advances to stage 6);
        # tail stages never hold 0 bytes; pre-data stages never hold partial.
        for stage, nbytes in ((5, 2048), (8, 0), (6, 100), (2, 10), (9, 0)):
            with self.assertRaises(ValueError, msg=(stage, nbytes)):
                L.link(scsi="timeout", timeout_stage=stage,
                       timeout_bytes=nbytes, **base)

    def test_invalid_state_nonzero_base_block_zeroes_lba(self):
        args = base_args(base=0x123456, block=0x789ABC, win=150)
        args["last"] = 0x31
        out = L.link(**args)
        self.assertEqual(out["a"], L.ERR_INVALID_STATE)
        self.assertFalse(out["issued"])
        self.assertEqual(out["lba"], 0)
        self.assertEqual((out["lba_hi"], out["lba_mid"], out["lba_lo"]), (0, 0, 0))
        self.assertEqual(out["stage"], 1)
        self.assertEqual(out["link_res"]["code"], L.ERR_INVALID_STATE)
        self.assertEqual(out["link_res"]["stage"], 1)
        self.assertEqual(out["link_res"]["bytes"], 0)
        self.assertEqual(out["link_res"]["scsi_status"], 0)
        self.assertEqual(out["link_res"]["msg"], 0)
        self.assertEqual(out["link_res"]["last_bus"], 0)
        self.assertEqual((out["link_res"]["lba_hi"], out["link_res"]["lba_mid"],
                          out["link_res"]["lba_lo"]), (0, 0, 0))

    def test_refusal_paths_verify_all_link_res_fields(self):
        # WINDOW
        args = base_args(base=150, block=0, win=150)
        args["win_hi"] = 0x20
        out = L.link(**args)
        self.assertEqual(out["a"], L.ERR_INVALID_WINDOW)
        self.assertFalse(out["issued"])
        self.assertEqual(out["stage"], 1)
        self.assertEqual(out["link_res"]["stage"], 1)
        self.assertEqual(out["link_res"]["bytes"], 0)
        self.assertEqual(out["link_res"]["scsi_status"], 0)
        self.assertEqual(out["link_res"]["msg"], 0)
        self.assertEqual(out["link_res"]["last_bus"], 0)
        # CAP
        out = L.link(**base_args(base=0x1FFFFF, block=1, win=150))
        self.assertEqual(out["a"], L.ERR_OVER_CAP)
        self.assertEqual(out["link_res"]["code"], L.ERR_OVER_CAP)
        # BEFORE
        out = L.link(**base_args(base=150, block=0, win=160))
        self.assertEqual(out["a"], L.ERR_BEFORE)
        self.assertEqual(out["link_res"]["code"], L.ERR_BEFORE)
        # AFTER
        out = L.link(**base_args(base=150, block=16, win=150))
        self.assertEqual(out["a"], L.ERR_AFTER)
        self.assertEqual(out["link_res"]["code"], L.ERR_AFTER)
        self.assertEqual(out["link_res"]["bytes"], 0)

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
