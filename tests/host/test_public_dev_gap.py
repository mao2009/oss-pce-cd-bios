"""Host tests for the Wave1-B PUBLIC/DEV gap differential (Issue #762).

Pure logic only: no Geargrafx core, no commercial media, no reference BIOS.
The real-core differential lives in
tests/integration/geargrafx_public_dev_gap.py (synthetic media only); this
file proves the differential table and its pure judge, including negatives
and mutations, without running a core.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "tools"), str(ROOT / "tests" / "integration")]

import geargrafx_public_dev_gap as G  # noqa: E402


def _obs(cycle, reason, halted, code, copied, ran):
    return {"cycle": cycle, "reason": reason, "halt_confirmed": halted,
            "code": code, "stage": 3, "copied": copied, "cdb": [0] * 6,
            "payload_ran": ran}


def _pair(dev_kwargs, pub_kwargs):
    dev = [_obs("cold", **dev_kwargs), _obs("warm", **dev_kwargs)]
    pub = [_obs("cold", **pub_kwargs), _obs("warm", **pub_kwargs)]
    return dev, pub


def _disc(name):
    return next(d for d in G.DIFFERENTIAL if d["disc"] == name)


class DifferentialTableTests(unittest.TestCase):
    def test_five_discs_cover_five_fields(self):
        self.assertEqual(len(G.DIFFERENTIAL), 5)
        self.assertEqual([d["disc"] for d in G.DIFFERENTIAL],
                         ["D1_mpr_raw", "D2_count_11", "D3_load_end_8800",
                          "D4_reserved_nonzero", "D5_bad_checksum_std_mpr"])
        self.assertEqual([d["dev_code"] for d in G.DIFFERENTIAL],
                         [25, 22, 23, 28, 30])

    def test_validation_order_matches_refusing_codes(self):
        # Firing order in ipl_loader_rom.s validate_info (by code position,
        # not numeric value): reserved(28) first, then count(22), load-end
        # (23), MPR(25); checksum(30) is post-copy. D2/D3/D4 codes must fire
        # before the MPR check so each isolates its own check.
        order = [28, 22, 23, 25]
        for name in ("D4_reserved_nonzero", "D2_count_11", "D3_load_end_8800"):
            self.assertLess(order.index(_disc(name)["dev_code"]),
                            order.index(25), name)

    def test_d1_is_the_only_single_field_flip(self):
        # DEV baseline (pos_n5_3000_payloadA): n=5, iplblk=2, sta/entry $3000,
        # openmode 0, no info edits, correct checksum. D1 differs only in the
        # MPR bytes 8..12 (raw indices vs $80..$87 banks); every other disc
        # differs in at least one further disc field.
        d1 = _disc("D1_mpr_raw")
        self.assertEqual(
            (d1["n"], d1["iplblk"], d1["sta"], d1["entry"], d1["info_edits"]),
            (5, 2, 0x3000, 0x3000, []))
        self.assertEqual(d1["mprs"], [0, 1, 2, 3, 4])
        for name in ("D2_count_11", "D3_load_end_8800", "D4_reserved_nonzero",
                     "D5_bad_checksum_std_mpr"):
            other = _disc(name)
            self.assertNotEqual(
                (other["n"], other["iplblk"], other["sta"], other["entry"],
                 other["info_edits"]),
                (5, 2, 0x3000, 0x3000, []),
                f"{name} must differ from the DEV baseline beyond MPR")

    def test_kinds_and_rationales(self):
        kinds = {d["disc"]: d["kind"] for d in G.DIFFERENTIAL}
        self.assertEqual(kinds,
                         {"D1_mpr_raw": "pass_gap", "D2_count_11": "pass_gap",
                          "D3_load_end_8800": "pass_gap",
                          "D4_reserved_nonzero": "pass_gap",
                          "D5_bad_checksum_std_mpr": "dual_refuse"})
        for disc in G.DIFFERENTIAL:
            self.assertTrue(disc.get("rationale"), disc["disc"])

    def test_d3_payload_origin_matches_sta(self):
        d3 = _disc("D3_load_end_8800")
        self.assertEqual(G.PAY_ORIGINS[d3["pay"]][0], d3["sta"])


class DiscCaseConstructionTests(unittest.TestCase):
    def test_dev_and_pub_share_disc_bytes(self):
        for disc in G.DIFFERENTIAL:
            dev, pub = G.disc_case(disc, "dev"), G.disc_case(disc, "pub")
            for field in ("n", "iplblk", "sta", "entry", "mprs", "openmode",
                          "info_edits", "pay"):
                self.assertEqual(dev[field], pub[field],
                                 f"{disc['disc']} field {field}")

    def test_dev_always_refuses_before_write_except_d5(self):
        for disc in G.DIFFERENTIAL:
            dev = G.disc_case(disc, "dev")
            self.assertEqual((dev["reason"], dev["code"], dev["payload_ran"]),
                             ("cd_halt_fail", disc["dev_code"], False))
            self.assertEqual(dev["copied"], 0 if disc["kind"] == "pass_gap"
                             else disc["n"])

    def test_pub_pass_gap_runs_payload(self):
        for disc in G.DIFFERENTIAL:
            if disc["kind"] != "pass_gap":
                continue
            pub = G.disc_case(disc, "pub")
            self.assertEqual(
                (pub["reason"], pub["code"], pub["copied"], pub["payload_ran"],
                 pub.get("public"), pub.get("mpr_add")),
                ("cd_halt_ok", 0, disc["n"], True, True, 0x80))

    def test_pub_d5_refuses_on_mpr(self):
        pub = G.disc_case(_disc("D5_bad_checksum_std_mpr"), "pub")
        self.assertEqual((pub["reason"], pub["code"], pub["copied"],
                          pub["payload_ran"]),
                         ("cd_halt_fail", 25, 0, False))

    def test_unknown_kind_refused(self):
        bad = dict(_disc("D1_mpr_raw"), kind="any_slot")
        with self.assertRaises(ValueError):
            G.disc_case(bad, "dev")
        with self.assertRaises(ValueError):
            G.disc_case(bad, "pub")

    def test_unknown_role_refused(self):
        with self.assertRaises(ValueError):
            G.disc_case(_disc("D1_mpr_raw"), "commercial")


class CheckGapPairPositiveTests(unittest.TestCase):
    def test_pass_gap_pairs_confirm(self):
        for name, dev_code, n in (("D1_mpr_raw", 25, 5), ("D2_count_11", 22, 11),
                                  ("D3_load_end_8800", 23, 5),
                                  ("D4_reserved_nonzero", 28, 5)):
            dev, pub = _pair(
                dict(reason="cd_halt_fail", halted=True, code=dev_code,
                     copied=0, ran=False),
                dict(reason="cd_halt_ok", halted=True, code=0,
                     copied=n, ran=True))
            self.assertTrue(G.check_gap_pair(dev, pub, _disc(name)), name)

    def test_dual_refuse_pair_confirms(self):
        dev, pub = _pair(
            dict(reason="cd_halt_fail", halted=True, code=30, copied=5,
                 ran=False),
            dict(reason="cd_halt_fail", halted=True, code=25, copied=0,
                 ran=False))
        self.assertTrue(
            G.check_gap_pair(dev, pub, _disc("D5_bad_checksum_std_mpr")))


class CheckGapPairNegativeTests(unittest.TestCase):
    def test_wrong_dev_code_fails(self):
        dev, pub = _pair(
            dict(reason="cd_halt_fail", halted=True, code=22, copied=0,
                 ran=False),
            dict(reason="cd_halt_ok", halted=True, code=0, copied=5,
                 ran=True))
        with self.assertRaises(ValueError):
            G.check_gap_pair(dev, pub, _disc("D1_mpr_raw"))

    def test_dev_payload_trace_fails(self):
        dev, pub = _pair(
            dict(reason="cd_halt_fail", halted=True, code=25, copied=0,
                 ran=True),
            dict(reason="cd_halt_ok", halted=True, code=0, copied=5,
                 ran=True))
        with self.assertRaises(ValueError):
            G.check_gap_pair(dev, pub, _disc("D1_mpr_raw"))

    def test_pub_refusal_fails_pass_gap(self):
        dev, pub = _pair(
            dict(reason="cd_halt_fail", halted=True, code=25, copied=0,
                 ran=False),
            dict(reason="cd_halt_fail", halted=True, code=25, copied=0,
                 ran=False))
        with self.assertRaises(ValueError):
            G.check_gap_pair(dev, pub, _disc("D1_mpr_raw"))

    def test_swapped_roles_fail(self):
        # A PUBLIC-shaped pair judged as DEV (and vice versa) must not pass.
        dev, pub = _pair(
            dict(reason="cd_halt_ok", halted=True, code=0, copied=5,
                 ran=True),
            dict(reason="cd_halt_fail", halted=True, code=25, copied=0,
                 ran=False))
        with self.assertRaises(ValueError):
            G.check_gap_pair(dev, pub, _disc("D1_mpr_raw"))

    def test_missing_warm_cycle_fails(self):
        dev = [_obs("cold", "cd_halt_fail", True, 25, 0, False),
               _obs("cold", "cd_halt_fail", True, 25, 0, False)]
        pub = [_obs("cold", "cd_halt_ok", True, 0, 5, True),
               _obs("warm", "cd_halt_ok", True, 0, 5, True)]
        with self.assertRaises(ValueError):
            G.check_gap_pair(dev, pub, _disc("D1_mpr_raw"))

    def test_unconfirmed_halt_fails(self):
        dev, pub = _pair(
            dict(reason="cd_halt_fail", halted=False, code=25, copied=0,
                 ran=False),
            dict(reason="cd_halt_ok", halted=True, code=0, copied=5,
                 ran=True))
        with self.assertRaises(ValueError):
            G.check_gap_pair(dev, pub, _disc("D1_mpr_raw"))


class CheckGapPairMutationTests(unittest.TestCase):
    def test_d5_obs_does_not_confirm_d1(self):
        # Mutation: dual-refuse observations claimed as a PASS gap.
        dev, pub = _pair(
            dict(reason="cd_halt_fail", halted=True, code=30, copied=5,
                 ran=False),
            dict(reason="cd_halt_fail", halted=True, code=25, copied=0,
                 ran=False))
        with self.assertRaises(ValueError):
            G.check_gap_pair(dev, pub, _disc("D1_mpr_raw"))

    def test_d1_obs_does_not_confirm_d5(self):
        dev, pub = _pair(
            dict(reason="cd_halt_fail", halted=True, code=25, copied=0,
                 ran=False),
            dict(reason="cd_halt_ok", halted=True, code=0, copied=5,
                 ran=True))
        with self.assertRaises(ValueError):
            G.check_gap_pair(dev, pub, _disc("D5_bad_checksum_std_mpr"))

    def test_unknown_kind_fails_closed(self):
        dev, pub = _pair(
            dict(reason="cd_halt_fail", halted=True, code=25, copied=0,
                 ran=False),
            dict(reason="cd_halt_ok", halted=True, code=0, copied=5,
                 ran=True))
        with self.assertRaises(ValueError):
            G.check_gap_pair(dev, pub, dict(_disc("D1_mpr_raw"),
                                            kind="pass_gap_mutant"))


class ScopeGuardTests(unittest.TestCase):
    RAW = (ROOT / "tests" / "integration"
           / "geargrafx_public_dev_gap.py").read_text(encoding="utf-8")
    # Scan code only: the module docstring states the same "no X" scope in
    # prose, which would false-positive a whole-file token scan.
    SOURCE = RAW.split('"""', 2)[2] if RAW.count('"""') >= 2 else RAW

    def test_no_commercial_media_path(self):
        for token in ("commercial_media", "commercial-auto-loader",
                      "commercial_auto_loader", "./rom/", "resolve_rom_dir",
                      "LoadBiosFromBuffer", "reference_jsr", "reference BIOS",
                      "syscard"):
            self.assertNotIn(token, self.SOURCE, token)

    def test_no_rts_chain_extension(self):
        for token in ("build_slot02_return_loader", "apply_patch",
                      "slot02ret", "slot02-33ret", "api_slot_"):
            self.assertNotIn(token, self.SOURCE, token)

    def test_no_production_wiring(self):
        for token in ("src/overrides", "overrides", "BUILD_ID"):
            self.assertNotIn(token, self.SOURCE, token)

    def test_reuses_loader_harness(self):
        self.assertIn("import geargrafx_ipl_loader as L", self.SOURCE)


if __name__ == "__main__":
    unittest.main()
