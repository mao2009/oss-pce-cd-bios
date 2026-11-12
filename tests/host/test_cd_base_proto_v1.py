"""Host targeted tests for the EXPERIMENTAL CD_BASE proto V1 mirror.

NOT genuine ABI evidence. Pins the guest logic of src/cd_base_proto.s via
tools/cd_base_proto.py: normal/abnormal/boundary, state update/preservation,
MPR/RAM contract notes (host-checked as pure state, real MPR/RAM asserted on
the pinned core by the focused integration).
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import cd_base_proto as P


class ProtoV1Tests(unittest.TestCase):
    def test_normal_data_start(self):
        out = P.proto_v1_full({"al": 1, "ah": 0, "bl": 0, "bh": 0x80,
                               "cl": 150 & 0xFF, "ch": 0, "dl": 0, "dh": 0},
                              sel_track=1, index01=150)
        self.assertEqual(out["a"], 0)
        self.assertEqual(out["c"], 0)
        self.assertEqual(out["z"], 1)
        st = out["state"]
        self.assertEqual((st["base_hi"], st["base_mid"], st["base_lo"]), (0, 0, 150))
        self.assertEqual((st["off_hi"], st["off_mid"], st["off_lo"]), (0, 0, 0))
        self.assertEqual(st["gen"], 1)
        self.assertEqual(st["last"], 0)

    def test_normal_data_offset(self):
        out = P.proto_v1_full([1, 0, 0, 0x80, 155, 0, 0, 0], sel_track=1, index01=150)
        self.assertEqual(out["a"], 0)
        st = out["state"]
        self.assertEqual((st["off_hi"], st["off_mid"], st["off_lo"]), (0, 0, 5))

    def test_normal_index01_zero_control(self):
        # INDEX01=0: absolute and track-relative coincide; base 0 is DATA.
        out = P.proto_v1_full([1, 0, 0, 0x80, 0, 0, 0, 0], sel_track=1, index01=0)
        self.assertEqual(out["a"], 0)
        self.assertEqual(out["state"]["gen"], 1)

    def test_boundary_last_pregap_vs_first_data(self):
        # INDEX01=150, pregap 0..149, data from 150.
        pre = P.proto_v1_full([1, 0, 0, 0x80, 149, 0, 0, 0], sel_track=1, index01=150)
        self.assertEqual(pre["a"], P.ERR_PREGAP)
        first = P.proto_v1_full([1, 0, 0, 0x80, 150, 0, 0, 0], sel_track=1, index01=150)
        self.assertEqual(first["a"], 0)

    def test_boundary_pregap_start(self):
        out = P.proto_v1_full([1, 0, 0, 0x80, 0, 0, 0, 0], sel_track=1, index01=150)
        self.assertEqual(out["a"], P.ERR_PREGAP)

    def test_negative_before_when_no_pregap(self):
        # INDEX01=100 (<150): no virtual pregap; base 50 < 100 is BEFORE.
        out = P.proto_v1_full([1, 0, 0, 0x80, 50, 0, 0, 0], sel_track=1, index01=100)
        self.assertEqual(out["a"], P.ERR_BEFORE)

    def test_negative_track(self):
        for bad in (0, 2, 100):
            out = P.proto_v1_full([bad, 0, 0, 0x80, 150, 0, 0, 0], sel_track=1, index01=150)
            self.assertEqual(out["a"], P.ERR_TRACK, f"track={bad}")

    def test_negative_flags(self):
        out = P.proto_v1_full([1, 0, 0, 0x00, 150, 0, 0, 0], sel_track=1, index01=150)
        self.assertEqual(out["a"], P.ERR_FLAGS)

    def test_negative_reserved(self):
        for key in ("ah", "bl", "dh"):
            inp = {"al": 1, "ah": 0, "bl": 0, "bh": 0x80,
                   "cl": 150, "ch": 0, "dl": 0, "dh": 0}
            inp[key] = 1
            out = P.proto_v1_full(inp, sel_track=1, index01=150)
            self.assertEqual(out["a"], P.ERR_RSVD, f"reserved {key}")

    def test_negative_over_21bit(self):
        # $200000 is first LBA beyond READ(6); $1FFFFF is last accepted.
        ok = P.proto_v1_full([1, 0, 0, 0x80, 0xFF, 0xFF, 0x1F, 0], sel_track=1, index01=150)
        self.assertEqual(ok["a"], 0)
        bad = P.proto_v1_full([1, 0, 0, 0x80, 0x00, 0x00, 0x20, 0], sel_track=1, index01=150)
        self.assertEqual(bad["a"], P.ERR_RANGE)

    def test_state_preserved_on_error(self):
        good = P.proto_v1_full([1, 0, 0, 0x80, 150, 0, 0, 0], sel_track=1, index01=150)
        st = good["state"]
        bad = P.proto_v1_full([1, 0, 0, 0x80, 5, 0, 0, 0], sel_track=1, index01=150, state=st)
        self.assertEqual(bad["a"], P.ERR_PREGAP)
        self.assertEqual((bad["state"]["base_hi"], bad["state"]["base_mid"], bad["state"]["base_lo"]),
                         (0, 0, 150))
        self.assertEqual(bad["state"]["gen"], st["gen"])
        self.assertEqual(bad["state"]["last"], P.ERR_PREGAP)

    def test_generation_wraps(self):
        st = {"base_hi": 0, "base_mid": 0, "base_lo": 150, "gen": 0xFF,
              "last": 0, "off_hi": 0, "off_mid": 0, "off_lo": 0}
        out = P.proto_v1_full([1, 0, 0, 0x80, 151, 0, 0, 0], sel_track=1, index01=150, state=st)
        self.assertEqual(out["a"], 0)
        self.assertEqual(out["state"]["gen"], 0)

    def test_bad_types_rejected(self):
        with self.assertRaises(ValueError):
            P.proto_v1_full([1, 0, 0, 0x80, True, 0, 0, 0], sel_track=1, index01=150)
        with self.assertRaises(ValueError):
            P.proto_v1(1, 0, 0, 0x80, 150, 0, 0, 1, 0x1FFFFF + 1, None)


if __name__ == "__main__":
    unittest.main()
