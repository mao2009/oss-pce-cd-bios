"""Host tests for MAJOR-1: dh-inclusive reserved-byte error ordering (G5 review).

EXPERIMENTAL CD_BASE proto V1 only. NOT genuine ABI evidence. Pins the HuC6280
guest inspection order (src/cd_base_proto.s): track -> flags -> reserved
($20F9/$20FA/$20FF incl. dh) -> range -> DATA/PREGAP/BEFORE, with base/gen/off
preserved on ANY error (only `last` updated). Reference JSR 0/81, ABI VERIFIED
0/81, DEV_CALLABLE 0, release_eligible=False.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import cd_base_proto as P


def fresh_state():
    return {"base_hi": 0, "base_mid": 0, "base_lo": 0, "gen": 0,
            "last": 0xFF, "off_hi": 0, "off_mid": 0, "off_lo": 0}


class DhOrderingTests(unittest.TestCase):
    def test_dh_invalid_everything_else_valid_state_preserved(self):
        st = {"base_hi": 0, "base_mid": 0, "base_lo": 150, "gen": 7,
              "last": 0, "off_hi": 0, "off_mid": 0, "off_lo": 0}
        before = dict(st)
        out = P.proto_v1_full({"al": 1, "ah": 0, "bl": 0, "bh": 0x80,
                               "cl": 155, "ch": 0, "dl": 0, "dh": 1},
                              sel_track=1, index01=150, state=st)
        self.assertEqual(out["a"], P.ERR_RSVD)
        self.assertEqual(out["c"], 1)
        self.assertEqual(out["z"], 0)
        for k in ("base_hi", "base_mid", "base_lo", "gen",
                  "off_hi", "off_mid", "off_lo"):
            self.assertEqual(out["state"][k], before[k], f"state.{k} mutated")
        self.assertEqual(out["state"]["last"], P.ERR_RSVD)

    def test_dh_invalid_track_invalid_track_wins(self):
        out = P.proto_v1_full([9, 0, 0, 0x80, 150, 0, 0, 1],
                              sel_track=1, index01=150, state=fresh_state())
        self.assertEqual(out["a"], P.ERR_TRACK)

    def test_dh_invalid_flags_invalid_flags_win(self):
        out = P.proto_v1_full([1, 0, 0, 0x00, 150, 0, 0, 1],
                              sel_track=1, index01=150, state=fresh_state())
        self.assertEqual(out["a"], P.ERR_FLAGS)

    def test_dh_invalid_range_invalid_rsvd_wins(self):
        # base $200000 is beyond READ(6), but reserved (dh) is checked first.
        out = P.proto_v1_full([1, 0, 0, 0x80, 0x00, 0x00, 0x20, 1],
                              sel_track=1, index01=150, state=fresh_state())
        self.assertEqual(out["a"], P.ERR_RSVD)

    def test_preexisting_state_invariant_on_any_error(self):
        st = {"base_hi": 0, "base_mid": 1, "base_lo": 2, "gen": 41,
              "last": 0, "off_hi": 0, "off_mid": 0, "off_lo": 3}
        cases = [
            [9, 0, 0, 0x80, 150, 0, 0, 0],   # track
            [1, 0, 0, 0x00, 150, 0, 0, 0],   # flags
            [1, 1, 0, 0x80, 150, 0, 0, 0],   # rsvd ah
            [1, 0, 0, 0x80, 150, 0, 0, 9],   # rsvd dh
            [1, 0, 0, 0x80, 0, 0, 0x20, 0],  # range
            [1, 0, 0, 0x80, 5, 0, 0, 0],     # pregap
        ]
        for inp in cases:
            with self.subTest(inputs=inp):
                out = P.proto_v1_full(list(inp), sel_track=1, index01=150,
                                      state=dict(st))
                self.assertNotEqual(out["a"], 0)
                for k in ("base_hi", "base_mid", "base_lo", "gen",
                          "off_hi", "off_mid", "off_lo"):
                    self.assertEqual(out["state"][k], st[k], f"state.{k}")
                self.assertEqual(out["state"]["last"], out["a"])

    def test_valid_input_unchanged_result(self):
        # Pre-fix valid-path behavior, byte-identical expectations.
        out = P.proto_v1_full({"al": 1, "ah": 0, "bl": 0, "bh": 0x80,
                               "cl": 155, "ch": 0, "dl": 0, "dh": 0},
                              sel_track=1, index01=150)
        self.assertEqual((out["a"], out["c"], out["z"]), (0, 0, 1))
        self.assertEqual(out["state"],
                         {"base_hi": 0, "base_mid": 0, "base_lo": 155, "gen": 1,
                          "last": 0, "off_hi": 0, "off_mid": 0, "off_lo": 5})
        # Valid path must agree with the inner core directly.
        core = P.proto_v1(1, 0, 0, 0x80, 155, 0, 0, 1, 150, None)
        self.assertEqual(out, core)


if __name__ == "__main__":
    unittest.main()
