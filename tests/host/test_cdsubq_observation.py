"""Host checks for the Issue #261 Q-subchannel device observation judge (synthetic, no emulator).

Reply vectors are hand-computed from the MSF arithmetic (abs = LBA + 150 frames, rel = LBA - track start),
not produced by tools/cdsubq_observation.encode_reply, so the tests do not compare the code with itself.
A PASS here validates only the judge; it is not a Geargrafx or System Card CD_SUBQ result.
"""
import unittest

from tools.cdda_verify import PAUSED, PLAYING, STOPPED
from tools.cdsubq_observation import (LEADOUT_LBA, NEGATIVE, POSITIVE, TRACKS, decode_reply, encode_reply, judge,
                                      msf_frames, observe_run, unbcd)

IDLE = 1  # CdRomAudio state never started (cdrom_audio.h), not exported by cdda_verify

# Hand-computed canonical replies: state, 01, track BCD, index 01, rel MSF, abs MSF.
POS = {
    "playing_track2_start": ("00 01 02 01 00 00 00 00 02 16", PLAYING, 2, 16),
    "playing_track2_last": ("00 01 02 01 00 00 63 00 03 04", PLAYING, 2, 79),
    "paused_track2_mid": ("02 01 02 01 00 00 34 00 02 50", PAUSED, 2, 50),
    "stopped_track2_start": ("03 01 02 01 00 00 00 00 02 16", STOPPED, 2, 16),
}


def vec(text: str) -> bytes:
    return bytes.fromhex(text.replace(" ", ""))


class CdSubqObservationTests(unittest.TestCase):
    def test_medium_geometry_matches_cd_da_fixture(self):
        # Independent of tools/build_cdda constants: track 1 data LBA 0-15, track 2 audio LBA 16-79.
        self.assertEqual(TRACKS, {1: (0, 15), 2: (16, 79)})
        self.assertEqual(LEADOUT_LBA, 80)

    def test_hand_vectors_are_canonical_replies(self):
        for name, (text, state, track, lba) in POS.items():
            with self.subTest(case=name):
                self.assertEqual(encode_reply(state, track, lba), vec(text))

    def test_positive_cases_judged_positive(self):
        for name, (text, state, track, lba) in POS.items():
            with self.subTest(case=name):
                verdict, reason = judge(vec(text), state, track, lba)
                self.assertEqual(verdict, POSITIVE, reason)

    def test_decode_reports_track_index_and_time(self):
        fields = decode_reply(vec("00 01 02 01 00 00 63 00 03 04"))
        self.assertEqual(fields, {"state": PLAYING, "adr_control": 1, "track": 2, "index": 1,
                                  "rel_frames": 63, "abs_frames": 229})

    def test_boundary_negatives_rejected_with_reason(self):
        cases = [
            # reply for LBA 16 judged as LBA 17 (one frame late): rel and abs fields differ
            ("one_frame_late", POS["playing_track2_start"][0], PLAYING, 2, 17, "rel_frames 0 != 1"),
            # track 2 reply judged as the last LBA of data track 1 (LBA 15): track field differs
            ("track2_judged_track1_last", POS["playing_track2_start"][0], PLAYING, 1, 15, "track 2 != 1"),
            # last audio LBA reply judged as the lead-out LBA 80: off-track
            ("leadout_lba80", POS["playing_track2_last"][0], PLAYING, 2, 80, "outside track 2"),
            # LBA 15 just before the audio track start: off-track
            ("before_track2_lba15", POS["playing_track2_start"][0], PLAYING, 2, 15, "outside track 2"),
            ("negative_lba", POS["playing_track2_start"][0], PLAYING, 2, -1, "outside track 2"),
            ("track3_absent", POS["playing_track2_start"][0], PLAYING, 3, 100, "outside track 3"),
            # never-started state carries no Q position
            ("idle_state", POS["playing_track2_start"][0], IDLE, 2, 16, "carries no current Q position"),
            # STOPPED reply judged as PLAYING: state byte differs
            ("state_mismatch", POS["stopped_track2_start"][0], PLAYING, 2, 16, "state 3 != 0"),
        ]
        for name, text, state, track, lba, needle in cases:
            with self.subTest(case=name):
                verdict, reason = judge(vec(text), state, track, lba)
                self.assertEqual(verdict, NEGATIVE)
                self.assertIn(needle, reason)

    def test_malformed_replies_are_negative_not_raised(self):
        bad = {
            "non_bcd_track": "00 01 0A 01 00 00 00 00 02 16",   # track byte 0x0A
            "frames_75": "00 01 02 01 00 00 00 00 02 75",       # abs frames 0x75 (75 > 74)
            "short_reply": "00 01 02 01 00 00 00 00 02",        # 9 bytes
        }
        for name, text in bad.items():
            with self.subTest(case=name):
                verdict, reason = judge(vec(text), PLAYING, 2, 16)
                self.assertEqual(verdict, NEGATIVE)
                self.assertIn("malformed reply", reason)

    def test_msf_and_bcd_limits(self):
        self.assertEqual(msf_frames(bytes((0x00, 0x00, 0x74))), 74)
        with self.assertRaises(ValueError):
            msf_frames(bytes((0x00, 0x00, 0x75)))
        with self.assertRaises(ValueError):
            msf_frames(bytes((0x00, 0x60, 0x00)))   # seconds 60
        self.assertEqual(unbcd(0x99), 99)
        with self.assertRaises(ValueError):
            unbcd(0x9A)


def _hit(state, lba, name="cdda_cmd_done", x=0):
    return {"name": name, "x": x, "audio": {"state": state, "current_lba": lba}}


def _points():
    """Checkpoint shape of cdda_verify.checkpoints(): {command index: (cmd hit, wait hit or None)}."""
    return {
        0: (_hit(IDLE, 0, x=0), None),
        1: (_hit(PLAYING, 16, x=1), _hit(PLAYING, 20, "cdda_wait_done")),
        2: (_hit(PLAYING, 30, x=2), None),
        3: (_hit(PAUSED, 30, x=3), _hit(PAUSED, 30, "cdda_wait_done")),
        4: (_hit(PAUSED, 30, x=4), None),
        5: (_hit(STOPPED, 30, x=5), _hit(STOPPED, 30, "cdda_wait_done")),
        6: (_hit(STOPPED, 30, x=6), None),
    }


def _run(q_replies):
    result = bytearray(0xB0)
    for offset, text in q_replies.items():
        result[offset:offset + 10] = vec(text)
    return {"result_hex": result.hex()}


class ObserveRunTests(unittest.TestCase):
    GOOD = {0x80: "00 01 02 01 00 00 10 00 02 26",     # PLAYING LBA 26 (window 20..30)
            0x90: "02 01 02 01 00 00 14 00 02 30",     # PAUSED LBA 30 (window 30..30)
            0xA0: "03 01 02 01 00 00 14 00 02 30"}     # STOPPED LBA 30 (window 30..30)

    def test_positive_run_records_lba_and_boundaries(self):
        observed = observe_run(_run(self.GOOD), _points())
        self.assertEqual({k: v["lba"] for k, v in observed.items()}, {2: 26, 4: 30, 6: 30})
        for record in observed.values():
            self.assertEqual(record["track"], 2)
            self.assertEqual(record["boundary_before"]["verdict"], NEGATIVE)
            self.assertEqual(record["boundary_after"]["verdict"], NEGATIVE)
        self.assertEqual(observed[2]["window"], [20, 30])

    def test_reply_outside_window_is_rejected(self):
        bad = dict(self.GOOD)
        bad[0x80] = "00 01 02 01 00 00 15 00 02 31"   # PLAYING LBA 31: one past the window 20..30
        with self.assertRaisesRegex(ValueError, r"matches \[\] in LBA 20\.\.30"):
            observe_run(_run(bad), _points())

    def test_malformed_run_reply_raises(self):
        bad = dict(self.GOOD)
        bad[0xA0] = "03 01 02 01 00 00 14 00 02 3A"   # non-BCD abs frame: no LBA may match
        with self.assertRaisesRegex(ValueError, r"matches \[\] in LBA 30\.\.30"):
            observe_run(_run(bad), _points())

    def test_edge_window_boundary_is_compared_by_fields(self):
        # Window starts at LBA 16 (track start): LBA 15 is off-track, so its rejection must come from the fields.
        points = _points()
        points[1] = (points[1][0], _hit(PLAYING, 16, "cdda_wait_done"))
        edge = dict(self.GOOD)
        edge[0x80] = "00 01 02 01 00 00 00 00 02 16"   # PLAYING LBA 16
        observed = observe_run(_run(edge), points)
        self.assertEqual(observed[2]["lba"], 16)
        self.assertIn("rel_frames 0 != -1", observed[2]["boundary_before"]["reason"])


if __name__ == "__main__":
    unittest.main()
