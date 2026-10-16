"""Host-only checks for the mixed-mode synthetic disc (data track 01 + original CD-DA track 02, Issue #5).

Verifies the CUE/BIN produced by tools/make_cd_image.py without any emulator:
track types, 2352-byte raw sizes, TOC consistency, sector counts, audio round-trip,
and negative cases (audio sector read as data, out-of-range track).

Scope: these are host image checks only. They do NOT exercise CD-DA play/pause/seek
or any emulator (Geargrafx) behaviour; that verification is BLOCKED and not claimed here.
"""
import json
from pathlib import Path
import tempfile
import re
import unittest

from tools.make_cd_image import (
    BIN_NAME, CUE_NAME, RAW_BYTES, USER_BYTES, audio_frame, build_image, sector_payload,
)

FRAMES_PER_SECOND = 75
SYNC = b"\x00" + b"\xff" * 10 + b"\x00"


def parse_cue(text):
    """Return [{'number', 'type', 'start_lba'}] from a single-FILE CUE (INDEX 01 only).

    Deliberately a minimal, independent oracle for this test. It is not the canonical
    CUE parser of PR #110 (path-based, nested dict, fail-closed on unknown keywords).
    """
    tracks = []
    for line in text.splitlines():
        m = re.match(r"\s*TRACK (\d{2}) (\S+)", line)
        if m:
            tracks.append({"number": int(m.group(1)), "type": m.group(2), "start_lba": None})
            continue
        m = re.match(r"\s*INDEX 01 (\d{2}):(\d{2}):(\d{2})", line)
        if m:
            mm, ss, ff = (int(x) for x in m.groups())
            tracks[-1]["start_lba"] = (mm * 60 + ss) * FRAMES_PER_SECOND + ff
    return tracks


def track_by_number(tracks, number):
    for track in tracks:
        if track["number"] == number:
            return track
    raise ValueError(f"track {number} not present on disc")


def track_end(tracks, index, total_sectors):
    return tracks[index + 1]["start_lba"] if index + 1 < len(tracks) else total_sectors


class MixedModeImageTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name)
        self.sectors, self.audio = 16, 8
        self.manifest = build_image(self.folder / "mixed", self.sectors, self.audio)
        self.bin = (self.folder / "mixed" / BIN_NAME).read_bytes()
        self.cue = (self.folder / "mixed" / CUE_NAME).read_text(encoding="ascii")
        self.tracks = parse_cue(self.cue)
        self.total = self.sectors + self.audio

    def tearDown(self):
        self._tmp.cleanup()

    def test_track_types_and_order(self):
        self.assertEqual([(t["number"], t["type"]) for t in self.tracks],
                         [(1, "MODE1/2352"), (2, "AUDIO")])

    def test_sector_sizes_are_2352_raw(self):
        self.assertEqual(len(self.bin), RAW_BYTES * self.total)
        self.assertEqual(self.manifest["raw_bytes_per_sector"], RAW_BYTES)
        self.assertEqual(self.manifest["sectors"], self.sectors)
        self.assertEqual(self.manifest["audio_sectors"], self.audio)

    def test_toc_consistency(self):
        self.assertEqual(track_by_number(self.tracks, 1)["start_lba"], 0)
        self.assertEqual(track_by_number(self.tracks, 2)["start_lba"], self.sectors)
        last = len(self.tracks) - 1
        self.assertEqual(track_end(self.tracks, last, self.total), self.total)
        # the audio track ends exactly at the BIN end: no gap, no overlap with the data track
        self.assertEqual(track_end(self.tracks, 0, self.total), self.sectors)

    def test_boundary_lba_maps_to_exactly_one_track_at_edge(self):
        def owners(lba):
            return [t["number"] for i, t in enumerate(self.tracks)
                    if t["start_lba"] <= lba < track_end(self.tracks, i, self.total)]
        self.assertEqual(owners(self.sectors - 1), [1])  # last data sector
        self.assertEqual(owners(self.sectors), [2])      # first audio sector
        self.assertEqual(owners(self.total - 1), [2])    # last audio sector

    def test_audio_sectors_round_trip_exactly(self):
        for i in range(self.audio):
            frame = self.bin[(self.sectors + i) * RAW_BYTES:(self.sectors + i + 1) * RAW_BYTES]
            self.assertEqual(len(frame), RAW_BYTES)
            self.assertEqual(frame, audio_frame(i), f"audio sector {i}")

    def test_audio_frames_are_nonsilent_and_distinct(self):
        frames = [audio_frame(i) for i in range(self.audio)]
        self.assertTrue(all(any(f) for f in frames))
        self.assertEqual(len(set(frames)), self.audio)

    def test_data_track_unchanged_by_audio_track(self):
        with tempfile.TemporaryDirectory() as plain:
            build_image(Path(plain), self.sectors)
            data_only = (Path(plain) / BIN_NAME).read_bytes()
        self.assertEqual(self.bin[:RAW_BYTES * self.sectors], data_only)

    def test_negative_audio_sector_is_not_mode1_data(self):
        for i in range(self.audio):
            frame = audio_frame(i)
            self.assertNotEqual(frame[:12], SYNC)
            user = frame[16:16 + USER_BYTES]
            for lba in range(self.sectors):
                self.assertNotEqual(user, sector_payload(lba))

    def test_negative_out_of_range_track_rejected(self):
        with self.assertRaises(ValueError):
            track_by_number(self.tracks, 3)
        with self.assertRaises(ValueError):
            track_by_number(self.tracks, 0)

    def test_negative_lba_past_disc_end_is_outside_every_track(self):
        past = self.total
        covered = [t for i, t in enumerate(self.tracks)
                   if t["start_lba"] <= past < track_end(self.tracks, i, self.total)]
        self.assertEqual(covered, [])

    def test_boundary_zero_audio_gives_single_data_track(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = build_image(Path(tmp), self.sectors, 0)
            cue = parse_cue((Path(tmp) / CUE_NAME).read_text(encoding="ascii"))
            self.assertEqual([t["number"] for t in cue], [1])
            self.assertEqual(manifest["audio_sectors"], 0)
            self.assertEqual((Path(tmp) / BIN_NAME).stat().st_size, RAW_BYTES * self.sectors)

    def test_boundary_audio_count_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                build_image(Path(tmp), self.sectors, 4097)
            with self.assertRaises(ValueError):
                build_image(Path(tmp), self.sectors, -1)

    def test_manifest_json_matches_mixed_files(self):
        on_disk = json.loads((self.folder / "mixed" / "disc-manifest.json").read_text())
        self.assertEqual(on_disk, self.manifest)


if __name__ == "__main__":
    unittest.main()
