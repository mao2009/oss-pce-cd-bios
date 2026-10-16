"""Host model of expected CD-DA transport behaviour for Issue #15 (no emulator, no core).

The state machine below is OUR MODEL of the expected core behaviour (STOPPED / PLAYING /
PAUSED / ENDED). It is NOT a verified description of the System Card CD_PLAY / CD_PAUSE /
CD_SEARCH, whose arguments and return values are UNKNOWN (see docs/cdda-unresolved-facts.md).
Track geometry comes from the synthetic Mixed Mode fixture of #113 (tools/make_cd_image.py).
"""
import tempfile
import unittest
from pathlib import Path

from tools.make_cd_image import CUE_NAME, build_image

from test_cdimage_mixed import parse_cue, track_by_number, track_end


class CddaModel:
    """Expected transport behaviour, modelled on LBA positions taken from a parsed CUE."""

    def __init__(self, tracks, total_sectors):
        self.tracks = tracks
        self.total = total_sectors
        self.state = "STOPPED"
        self.pos = None
        self.track = None

    def _audio(self, number):
        track = track_by_number(self.tracks, number)  # raises ValueError for unknown tracks
        if track["type"] != "AUDIO":
            raise ValueError(f"track {number} is not CD-DA")
        return track

    def play(self, number):
        track = self._audio(number)
        self.state, self.track, self.pos = "PLAYING", number, track["start_lba"]

    def tick(self, sectors=1):
        """Advance playback. Only PLAYING moves; reaching the track end sets ENDED."""
        if sectors < 0:
            raise ValueError(f"tick requires a non-negative sector count, got {sectors}")
        if self.state != "PLAYING":
            return
        index = next(i for i, t in enumerate(self.tracks) if t["number"] == self.track)
        end = track_end(self.tracks, index, self.total)
        self.pos = min(self.pos + sectors, end)
        if self.pos == end:
            self.state = "ENDED"

    def pause(self):
        if self.state != "PLAYING":
            raise ValueError(f"pause requires PLAYING, got {self.state}")
        self.state = "PAUSED"

    def resume(self):
        if self.state != "PAUSED":
            raise ValueError(f"resume requires PAUSED, got {self.state}")
        self.state = "PLAYING"

    def stop(self):
        self.state, self.pos, self.track = "STOPPED", None, None


class CddaStateModelTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        folder = Path(self._tmp.name) / "mixed"
        self.sectors, self.audio = 16, 8
        build_image(folder, self.sectors, self.audio)
        self.tracks = parse_cue((folder / CUE_NAME).read_text(encoding="ascii"))
        self.total = self.sectors + self.audio
        self.model = CddaModel(self.tracks, self.total)
        self.audio_start = self.sectors  # track 02 INDEX 01

    def tearDown(self):
        self._tmp.cleanup()

    def test_classification_matches_cue_types(self):
        kinds = {t["number"]: t["type"] == "AUDIO" for t in self.tracks}
        self.assertEqual(kinds, {1: False, 2: True})

    def test_play_data_track_rejected(self):
        with self.assertRaises(ValueError):
            self.model.play(1)
        self.assertEqual(self.model.state, "STOPPED")

    def test_play_unknown_track_rejected(self):
        with self.assertRaises(ValueError):
            self.model.play(3)

    def test_play_starts_at_track_start_and_ends_exactly_at_disc_end(self):
        self.model.play(2)
        self.assertEqual((self.model.state, self.model.pos), ("PLAYING", self.audio_start))
        self.model.tick(self.audio - 1)
        self.assertEqual((self.model.state, self.model.pos), ("PLAYING", self.total - 1))
        self.model.tick(1)
        self.assertEqual((self.model.state, self.model.pos), ("ENDED", self.total))

    def test_end_of_track_does_not_run_into_data_track(self):
        self.model.play(2)
        self.model.tick(1000)
        self.assertEqual(self.model.pos, self.total)
        self.assertNotIn(self.model.pos, range(0, self.sectors))

    def test_tick_zero_keeps_position_and_state(self):
        self.model.play(2)
        self.model.tick(0)
        self.assertEqual((self.model.state, self.model.pos), ("PLAYING", self.audio_start))

    def test_tick_negative_rejected_in_every_state(self):
        with self.assertRaises(ValueError):
            self.model.tick(-1)  # STOPPED
        self.assertEqual((self.model.state, self.model.pos), ("STOPPED", None))
        self.model.play(2)
        with self.assertRaises(ValueError):
            self.model.tick(-1)  # PLAYING
        self.assertEqual((self.model.state, self.model.pos), ("PLAYING", self.audio_start))
        self.model.pause()
        with self.assertRaises(ValueError):
            self.model.tick(-self.audio)  # PAUSED
        self.assertEqual((self.model.state, self.model.pos), ("PAUSED", self.audio_start))

    def test_tick_huge_value_clamps_to_disc_end(self):
        self.model.play(2)
        self.model.tick(2 ** 40)
        self.assertEqual((self.model.state, self.model.pos), ("ENDED", self.total))

    def test_pause_keeps_position_and_resume_continues(self):
        self.model.play(2)
        self.model.tick(3)
        self.model.pause()
        self.model.tick(100)  # ignored while PAUSED
        self.assertEqual((self.model.state, self.model.pos), ("PAUSED", self.audio_start + 3))
        self.model.resume()
        self.model.tick(self.audio - 3)
        self.assertEqual((self.model.state, self.model.pos), ("ENDED", self.total))

    def test_pause_resume_does_not_change_total_sectors_played(self):
        uninterrupted = CddaModel(self.tracks, self.total)
        uninterrupted.play(2)
        uninterrupted.tick(self.audio)
        self.model.play(2)
        for _ in range(self.audio - 1):
            self.model.tick(1)
            self.model.pause()
            self.model.resume()
        self.model.tick(1)
        self.assertEqual((self.model.state, self.model.pos),
                         (uninterrupted.state, uninterrupted.pos))

    def test_invalid_transitions_rejected(self):
        with self.assertRaises(ValueError):
            self.model.pause()   # from STOPPED
        with self.assertRaises(ValueError):
            self.model.resume()  # from STOPPED
        self.model.play(2)
        with self.assertRaises(ValueError):
            self.model.resume()  # from PLAYING
        self.model.pause()
        with self.assertRaises(ValueError):
            self.model.pause()   # from PAUSED

    def test_stop_then_play_restarts_from_track_start(self):
        self.model.play(2)
        self.model.tick(4)
        self.model.stop()
        self.assertEqual((self.model.state, self.model.pos), ("STOPPED", None))
        with self.assertRaises(ValueError):
            self.model.resume()  # no position to resume from
        self.model.play(2)
        self.assertEqual(self.model.pos, self.audio_start)

    def test_ended_cannot_resume_but_can_replay(self):
        self.model.play(2)
        self.model.tick(self.audio)
        with self.assertRaises(ValueError):
            self.model.resume()
        self.model.play(2)
        self.assertEqual((self.model.state, self.model.pos), ("PLAYING", self.audio_start))

    def test_disc_without_audio_has_nothing_playable(self):
        with tempfile.TemporaryDirectory() as tmp:
            build_image(Path(tmp), self.sectors, 0)
            tracks = parse_cue((Path(tmp) / CUE_NAME).read_text(encoding="ascii"))
        model = CddaModel(tracks, self.sectors)
        for number in (1, 2):
            with self.assertRaises(ValueError):
                model.play(number)


if __name__ == "__main__":
    unittest.main()
