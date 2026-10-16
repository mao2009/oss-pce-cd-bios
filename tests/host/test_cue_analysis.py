import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools import cue_analysis
from tools.cue_analysis import analyze, CueError, user_sector, PCE_BOOT_SIGNATURE
from tools.make_cd_image import audio_frame, build_image, raw_sector, sector_payload

CLI = Path(__file__).resolve().parents[2] / "tools" / "cue_analysis.py"
SYNC_BYTES = b"\x00" + b"\xff" * 10 + b"\x00"


class _RecordingFile:
    """Wraps a real BIN handle; records (requested, returned) per read and can cut the file short."""

    def __init__(self, f, log, cut_at=None):
        self._f, self._log, self._cut_at = f, log, cut_at

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._f.close()
        return False

    def seek(self, *args):
        return self._f.seek(*args)

    def read(self, n=-1):
        pos = self._f.tell()
        data = self._f.read(n)
        if self._cut_at is not None:  # simulate a mid-file disconnect: bytes past cut_at never arrive
            data = data[:max(0, self._cut_at - pos)]
        self._log.append((n, len(data)))
        return data


def _recording_open(log, cut_at=None):
    real_open = Path.open

    def opener(self, *args, **kwargs):
        f = real_open(self, *args, **kwargs)
        return _RecordingFile(f, log, cut_at) if self.suffix == ".bin" else f

    return mock.patch.object(Path, "open", opener)


def _run_cli(cue: Path):
    return subprocess.run([sys.executable, str(CLI), str(cue)], capture_output=True, text=True)


class CueAnalysisTests(unittest.TestCase):
    def test_synthetic_cuebin_analysis(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            build_image(d)
            rep = analyze(d / "oss-pce-cd-test.cue")
            self.assertTrue(rep["structurally_valid"])
            self.assertEqual(rep["data_tracks"], [1])
            self.assertEqual(rep["total_frames"], 16)
            data = user_sector(d / "oss-pce-cd-test.cue", rep, 5)
            self.assertEqual(len(data), 2048)
            with self.assertRaises(CueError):
                user_sector(d / "oss-pce-cd-test.cue", rep, 16)
            with self.assertRaises(CueError):
                user_sector(d / "oss-pce-cd-test.cue", rep, -1)

    def test_invalid_cue_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "bad.cue").write_text('FILE "missing.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n', encoding='ascii')
            with self.assertRaises(CueError):
                analyze(d / "bad.cue")

    def test_multiple_tracks_single_bin(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            # Create a BIN with two data tracks - 100 sectors total
            # Track 1: sectors 0-49 (starts at 00:00:00 = LBA 0)
            # Track 2: sectors 50-99 (starts at 00:01:00 = LBA 75)
            sectors = 100
            data = b"".join(raw_sector(lba) for lba in range(sectors))
            bin_path = d / "test.bin"
            bin_path.write_bytes(data)
            cue_text = ('FILE "test.bin" BINARY\n'
                        '  TRACK 01 MODE1/2352\n'
                        '    INDEX 01 00:00:00\n'
                        '  TRACK 02 MODE1/2352\n'
                        '    INDEX 01 00:01:00\n')  # 75 frames = sector 75
            (d / "test.cue").write_text(cue_text)
            rep = analyze(d / "test.cue")
            self.assertEqual(len(rep["data_tracks"]), 2)
            self.assertEqual(rep["tracks"][0]["start_lba"], 0)
            self.assertEqual(rep["tracks"][1]["start_lba"], 75)
            self.assertEqual(rep["tracks"][0]["length_frames"], 75)
            self.assertEqual(rep["tracks"][1]["length_frames"], 25)

    def test_multiple_bin_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            # Track 1 in bin1
            data1 = b"".join(raw_sector(lba) for lba in range(10))
            (d / "track01.bin").write_bytes(data1)
            # Track 2 in bin2
            data2 = b"".join(raw_sector(lba + 10) for lba in range(10))
            (d / "track02.bin").write_bytes(data2)
            cue_text = ('FILE "track01.bin" BINARY\n'
                        '  TRACK 01 MODE1/2352\n'
                        '    INDEX 01 00:00:00\n'
                        'FILE "track02.bin" BINARY\n'
                        '  TRACK 02 MODE1/2352\n'
                        '    INDEX 01 00:00:00\n')
            (d / "test.cue").write_text(cue_text)
            rep = analyze(d / "test.cue")
            self.assertEqual(len(rep["data_tracks"]), 2)
            self.assertEqual(rep["tracks"][0]["file"], "track01.bin")
            self.assertEqual(rep["tracks"][1]["file"], "track02.bin")

    def test_index00_and_index01(self):
        """Pregap sectors are physical LBAs 0..149; INDEX 01 data starts at LBA 150 (header = LBA + 150)."""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            # 150 valid pregap sectors followed by 50 data sectors: BIN frame k is LBA k
            (d / "test.bin").write_bytes(b"".join(raw_sector(lba) for lba in range(200)))
            (d / "test.cue").write_text('FILE "test.bin" BINARY\n'
                                        '  TRACK 01 MODE1/2352\n'
                                        '    INDEX 00 00:00:00\n'
                                        '    INDEX 01 00:02:00\n')
            rep = analyze(d / "test.cue")
            track = rep["tracks"][0]
            self.assertEqual(track["index"]["0"], 0)
            self.assertEqual(track["index"]["1"], 150)
            self.assertEqual(track["pregap_frames"], 150)
            self.assertEqual(track["index01_frame"], 150)
            self.assertEqual(track["pregap_start_lba"], 0)
            self.assertEqual(track["start_lba"], 150)
            self.assertEqual(track["declared_data_lba"], 150)
            self.assertEqual(track["length_frames"], 50)
            self.assertEqual(track["pregap_sync_frames"], 150)  # valid pregap sync sectors are not errors
            self.assertEqual(rep["findings"], [])
            self.assertTrue(rep["structurally_valid"])
            self.assertEqual(user_sector(d / "test.cue", rep, 150), sector_payload(150))
            self.assertEqual(user_sector(d / "test.cue", rep, 199), sector_payload(199))
            with self.assertRaisesRegex(CueError, "pregap"):
                user_sector(d / "test.cue", rep, 0)
            with self.assertRaises(CueError):
                user_sector(d / "test.cue", rep, 200)

    def test_zero_filled_pregap_is_not_user_data(self):
        """A zero-filled pregap (no sync) is valid, and its LBAs are rejected as user data."""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            pregap = b"\x00" * (150 * 2352)
            (d / "test.bin").write_bytes(pregap + b"".join(raw_sector(150 + i) for i in range(10)))
            (d / "test.cue").write_text('FILE "test.bin" BINARY\n'
                                        '  TRACK 01 MODE1/2352\n'
                                        '    INDEX 00 00:00:00\n'
                                        '    INDEX 01 00:02:00\n')
            rep = analyze(d / "test.cue")
            self.assertEqual(rep["findings"], [])
            self.assertEqual(rep["tracks"][0]["pregap_sync_frames"], 0)
            for lba in range(150, 160):
                self.assertEqual(user_sector(d / "test.cue", rep, lba), sector_payload(lba))
            with self.assertRaises(CueError):
                user_sector(d / "test.cue", rep, 149)

    def test_pregap_sync_with_wrong_header_is_detected(self):
        """A sync sector inside the pregap whose MSF disagrees with its position is corruption."""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            frames = [bytearray(raw_sector(lba)) for lba in range(200)]
            frames[40][12:15] = b"\x00\x00\x00"  # pregap frame 40 claims MSF 00:00:00
            (d / "test.bin").write_bytes(b"".join(bytes(f) for f in frames))
            (d / "test.cue").write_text('FILE "test.bin" BINARY\n'
                                        '  TRACK 01 MODE1/2352\n'
                                        '    INDEX 00 00:00:00\n'
                                        '    INDEX 01 00:02:00\n')
            rep = analyze(d / "test.cue")
            self.assertFalse(rep["structurally_valid"])
            self.assertTrue(any("pregap sync sectors disagree" in f and "[40]" in f for f in rep["findings"]),
                            rep["findings"])

    def test_index01_data_matches_expected_payload(self):
        """INDEX 01 data matches sector_payload at its physical LBA."""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "test.bin").write_bytes(b"".join(raw_sector(lba) for lba in range(160)))
            (d / "test.cue").write_text('FILE "test.bin" BINARY\n'
                                        '  TRACK 01 MODE1/2352\n'
                                        '    INDEX 00 00:00:00\n'
                                        '    INDEX 01 00:02:00\n')
            rep = analyze(d / "test.cue")
            for lba in range(150, 160):
                self.assertEqual(user_sector(d / "test.cue", rep, lba), sector_payload(lba), f"LBA {lba} mismatch")

    def test_pregap_not_extractable_as_user_data(self):
        """Pregap LBAs and negative LBAs are rejected; pregap_frames reports the stored pregap."""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "test.bin").write_bytes(b"".join(raw_sector(lba) for lba in range(160)))
            (d / "test.cue").write_text('FILE "test.bin" BINARY\n'
                                        '  TRACK 01 MODE1/2352\n'
                                        '    INDEX 00 00:00:00\n'
                                        '    INDEX 01 00:02:00\n')
            rep = analyze(d / "test.cue")
            with self.assertRaises(CueError):
                user_sector(d / "test.cue", rep, -1)
            with self.assertRaisesRegex(CueError, "pregap"):
                user_sector(d / "test.cue", rep, 149)
            self.assertEqual(rep["tracks"][0]["pregap_frames"], 150)

    def test_no_false_positive_msf_mismatch(self):
        """Valid pregap sync sectors and data headers produce no MSF or pregap findings."""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "test.bin").write_bytes(b"".join(raw_sector(lba) for lba in range(160)))
            (d / "test.cue").write_text('FILE "test.bin" BINARY\n'
                                        '  TRACK 01 MODE1/2352\n'
                                        '    INDEX 00 00:00:00\n'
                                        '    INDEX 01 00:02:00\n')
            rep = analyze(d / "test.cue")
            msf_findings = [f for f in rep["findings"] if "MSF headers disagree" in f or "pregap sync" in f]
            self.assertEqual(msf_findings, [], f"Unexpected findings: {msf_findings}")

    def test_normal_track_without_index00(self):
        """Normal track without INDEX 00 maintains existing behavior."""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            build_image(d)  # Creates INDEX 01 at 00:00:00, no INDEX 00
            rep = analyze(d / "oss-pce-cd-test.cue")
            self.assertTrue(rep["structurally_valid"])
            self.assertEqual(rep["tracks"][0]["pregap_frames"], 0)
            self.assertEqual(rep["tracks"][0]["index01_frame"], 0)
            self.assertEqual(rep["tracks"][0]["start_lba"], 0)
            self.assertEqual(rep["tracks"][0]["length_frames"], 16)
            # Verify extraction works
            for lba in [0, 5, 15]:
                data = user_sector(d / "oss-pce-cd-test.cue", rep, lba)
                self.assertEqual(data, sector_payload(lba))

    def test_multiple_tracks_with_index00_pregap_keep_physical_lba(self):
        """Track 2 pregap (INDEX 00) advances the disc LBA: track 2 data starts at LBA 250, not 100."""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            # 350 BIN frames, every frame a valid MODE1 sector: BIN frame k == LBA k
            (d / "test.bin").write_bytes(b"".join(raw_sector(lba) for lba in range(350)))
            (d / "test.cue").write_text('FILE "test.bin" BINARY\n'
                                        '  TRACK 01 MODE1/2352\n'
                                        '    INDEX 01 00:00:00\n'
                                        '  TRACK 02 MODE1/2352\n'
                                        '    INDEX 00 00:01:25\n'
                                        '    INDEX 01 00:03:25\n')
            rep = analyze(d / "test.cue")
            self.assertEqual(rep["findings"], [])
            t1, t2 = rep["tracks"]
            self.assertEqual((t1["pregap_start_lba"], t1["start_lba"], t1["length_frames"]), (0, 0, 100))
            self.assertEqual((t2["pregap_start_lba"], t2["start_lba"], t2["length_frames"]), (100, 250, 100))
            self.assertEqual(t2["pregap_frames"], 150)
            self.assertEqual(t2["index01_frame"], 250)
            self.assertEqual(rep["total_frames"], 350)
            self.assertEqual(user_sector(d / "test.cue", rep, 99), sector_payload(99))
            with self.assertRaisesRegex(CueError, "pregap"):
                user_sector(d / "test.cue", rep, 100)
            self.assertEqual(user_sector(d / "test.cue", rep, 250), sector_payload(250))
            self.assertEqual(user_sector(d / "test.cue", rep, 349), sector_payload(349))
            with self.assertRaises(CueError):
                user_sector(d / "test.cue", rep, 350)

    def test_cue_pregap_is_virtual_and_advances_lba(self):
        """A CUE PREGAP is not stored in the BIN but still occupies disc LBAs before INDEX 01."""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            # Track 1: LBA 0..99 in BIN frames 0..99. Track 2: PREGAP 75 frames (virtual, LBA 100..174),
            # INDEX 01 at BIN frame 100 holds LBA 175 onward.
            bin_frames = [raw_sector(lba) for lba in range(100)] + [raw_sector(175 + i) for i in range(50)]
            (d / "test.bin").write_bytes(b"".join(bin_frames))
            (d / "test.cue").write_text('FILE "test.bin" BINARY\n'
                                        '  TRACK 01 MODE1/2352\n'
                                        '    INDEX 01 00:00:00\n'
                                        '  TRACK 02 MODE1/2352\n'
                                        '    PREGAP 00:01:00\n'
                                        '    INDEX 01 00:01:25\n')  # BIN frame 100 (1 s 25 frames)
            rep = analyze(d / "test.cue")
            self.assertEqual(rep["findings"], [])
            t2 = rep["tracks"][1]
            self.assertEqual((t2["pregap_start_lba"], t2["start_lba"]), (100, 175))
            self.assertEqual(user_sector(d / "test.cue", rep, 175), sector_payload(175))
            with self.assertRaisesRegex(CueError, "pregap"):
                user_sector(d / "test.cue", rep, 120)

    def test_pregap_and_index00_together_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "test.bin").write_bytes(raw_sector(0) * 200)
            (d / "test.cue").write_text('FILE "test.bin" BINARY\n'
                                        '  TRACK 01 MODE1/2352\n'
                                        '    PREGAP 00:02:00\n'
                                        '    INDEX 00 00:00:00\n'
                                        '    INDEX 01 00:02:00\n')
            with self.assertRaisesRegex(CueError, "ambiguous"):
                analyze(d / "test.cue")

    def test_multiple_bin_files_offset_calculation(self):
        """A second BIN's pregap advances the disc LBA across the FILE boundary."""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "track01.bin").write_bytes(b"".join(raw_sector(lba) for lba in range(50)))
            # Track 2 occupies disc LBA 50..249: 150 pregap frames (50..199) then 50 data frames (200..249)
            (d / "track02.bin").write_bytes(b"".join(raw_sector(lba) for lba in range(50, 250)))
            cue_text = ('FILE "track01.bin" BINARY\n'
                        '  TRACK 01 MODE1/2352\n'
                        '    INDEX 01 00:00:00\n'
                        'FILE "track02.bin" BINARY\n'
                        '  TRACK 02 MODE1/2352\n'
                        '    INDEX 00 00:00:00\n'
                        '    INDEX 01 00:02:00\n')
            (d / "test.cue").write_text(cue_text)
            rep = analyze(d / "test.cue")
            self.assertEqual(rep["findings"], [])
            self.assertEqual(len(rep["data_tracks"]), 2)
            self.assertEqual(rep["tracks"][0]["file"], "track01.bin")
            self.assertEqual(rep["tracks"][0]["start_lba"], 0)
            self.assertEqual(rep["tracks"][0]["length_frames"], 50)
            self.assertEqual(rep["tracks"][1]["file"], "track02.bin")
            self.assertEqual(rep["tracks"][1]["pregap_start_lba"], 50)
            self.assertEqual(rep["tracks"][1]["start_lba"], 200)
            self.assertEqual(rep["tracks"][1]["length_frames"], 50)
            self.assertEqual(rep["tracks"][1]["pregap_frames"], 150)
            self.assertEqual(rep["tracks"][1]["index01_frame"], 150)
            self.assertEqual(user_sector(d / "test.cue", rep, 0), sector_payload(0))
            self.assertEqual(user_sector(d / "test.cue", rep, 49), sector_payload(49))
            with self.assertRaisesRegex(CueError, "pregap"):
                user_sector(d / "test.cue", rep, 100)
            self.assertEqual(user_sector(d / "test.cue", rep, 200), sector_payload(200))
            self.assertEqual(user_sector(d / "test.cue", rep, 249), sector_payload(249))

    def test_virtual_cue_pregap_first_track(self):
        """CUE PREGAP is virtual: LBA 0..149 are not in the BIN, track 1 data starts at LBA 150 = BIN frame 0."""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            # Track 1 data: BIN frames 0..149 hold LBA 150..299. Track 2 AUDIO: BIN frames 150..249, LBA 300..399.
            bin_frames = [raw_sector(150 + k) for k in range(150)] + [audio_frame(k) for k in range(100)]
            (d / "test.bin").write_bytes(b"".join(bin_frames))
            (d / "test.cue").write_text('FILE "test.bin" BINARY\n'
                                        '  TRACK 01 MODE1/2352\n'
                                        '    PREGAP 00:02:00\n'
                                        '    INDEX 01 00:00:00\n'
                                        '  TRACK 02 AUDIO\n'
                                        '    INDEX 01 00:02:00\n')
            rep = analyze(d / "test.cue")
            self.assertEqual(rep["findings"], [])
            t1, t2 = rep["tracks"]
            self.assertEqual((t1["pregap_start_lba"], t1["start_lba"], t1["length_frames"]), (0, 150, 150))
            self.assertEqual(t1["pregap_frames"], 150)
            self.assertEqual((t2["pregap_start_lba"], t2["start_lba"], t2["length_frames"]), (300, 300, 100))
            self.assertEqual(rep["total_frames"], 400)
            self.assertEqual(user_sector(d / "test.cue", rep, 150), sector_payload(150))
            self.assertEqual(user_sector(d / "test.cue", rep, 299), sector_payload(299))
            for lba in (0, 149):
                with self.assertRaisesRegex(CueError, "pregap"):
                    user_sector(d / "test.cue", rep, lba)
            with self.assertRaises(CueError):
                user_sector(d / "test.cue", rep, 300)  # audio track: not user data

    def test_spec_example_pregap_boundaries(self):
        """INDEX 00 = 00:00:00, INDEX 01 = 00:02:00: pregap LBA 0, data LBA 150; LBA 0 and 149 rejected."""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "test.bin").write_bytes(b"".join(raw_sector(lba) for lba in range(152)))
            (d / "test.cue").write_text('FILE "test.bin" BINARY\n'
                                        '  TRACK 01 MODE1/2352\n'
                                        '    INDEX 00 00:00:00\n'
                                        '    INDEX 01 00:02:00\n')
            rep = analyze(d / "test.cue")
            track = rep["tracks"][0]
            self.assertEqual((track["pregap_start_lba"], track["start_lba"]), (0, 150))
            self.assertEqual(rep["findings"], [])
            for lba in (0, 149):
                with self.assertRaisesRegex(CueError, "pregap"):
                    user_sector(d / "test.cue", rep, lba)
            self.assertEqual(user_sector(d / "test.cue", rep, 150), sector_payload(150))

    def test_first_track_pregap_without_index00_rejected(self):
        """BIN frames before the first INDEX are not mapped to a track, so they must be rejected."""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "test.bin").write_bytes(b"".join(raw_sector(lba) for lba in range(160)))
            (d / "test.cue").write_text('FILE "test.bin" BINARY\n'
                                        '  TRACK 01 MODE1/2352\n'
                                        '    INDEX 01 00:02:00\n')
            with self.assertRaisesRegex(CueError, "not mapped"):
                analyze(d / "test.cue")

    def test_unsupported_cue_forms_rejected(self):
        """Unsupported CUE constructs fail closed instead of being guessed."""
        cases = {
            "postgap": ('  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n    POSTGAP 00:01:00\n', "POSTGAP"),
            "index02": ('  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n    INDEX 02 00:01:00\n', "INDEX"),
            "no_index01": ('  TRACK 01 MODE1/2352\n    INDEX 00 00:00:00\n', "INDEX 01 is required"),
            "index00_after_index01": ('  TRACK 01 MODE1/2352\n    INDEX 00 00:01:00\n    INDEX 01 00:00:50\n',
                                      "after INDEX 01"),
            "unordered_tracks": ('  TRACK 01 MODE1/2352\n    INDEX 01 00:01:00\n'
                                 '  TRACK 02 MODE1/2352\n    INDEX 01 00:00:30\n', "not increasing"),
        }
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "test.bin").write_bytes(b"".join(raw_sector(lba) for lba in range(200)))
            for name, (body, message) in cases.items():
                with self.subTest(case=name):
                    (d / "test.cue").write_text('FILE "test.bin" BINARY\n' + body)
                    with self.assertRaisesRegex(CueError, message):
                        analyze(d / "test.cue")

    def test_audio_track_pregap_advances_lba_and_is_not_user_data(self):
        """An AUDIO track with a BIN-stored pregap: its pregap LBAs advance the disc and are not data."""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            bin_frames = [raw_sector(lba) for lba in range(100)] + [audio_frame(k) for k in range(100)]
            (d / "test.bin").write_bytes(b"".join(bin_frames))
            (d / "test.cue").write_text('FILE "test.bin" BINARY\n'
                                        '  TRACK 01 MODE1/2352\n'
                                        '    INDEX 01 00:00:00\n'
                                        '  TRACK 02 AUDIO\n'
                                        '    INDEX 00 00:01:25\n'
                                        '    INDEX 01 00:01:50\n')  # 25 pregap frames in BIN (INDEX 00 = frame 100)
            rep = analyze(d / "test.cue")
            self.assertEqual(rep["findings"], [])
            t2 = rep["tracks"][1]
            self.assertEqual((t2["pregap_start_lba"], t2["start_lba"], t2["length_frames"]), (100, 125, 75))
            self.assertEqual(user_sector(d / "test.cue", rep, 99), sector_payload(99))
            with self.assertRaises(CueError):
                user_sector(d / "test.cue", rep, 110)

    def test_mode1_2352_sector_extraction(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            build_image(d)
            rep = analyze(d / "oss-pce-cd-test.cue")
            # Test extraction at various LBAs
            for lba in [0, 5, 10, 15]:
                data = user_sector(d / "oss-pce-cd-test.cue", rep, lba)
                self.assertEqual(len(data), 2048)
                # Verify it matches the expected payload
                expected = sector_payload(lba)
                self.assertEqual(data, expected)

    def test_data_audio_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            build_image(d, sectors=10, audio_sectors=10)
            rep = analyze(d / "oss-pce-cd-test.cue")
            self.assertEqual(rep["data_tracks"], [1])
            self.assertEqual(rep["audio_tracks"], [2])
            # Data track should work
            data = user_sector(d / "oss-pce-cd-test.cue", rep, 5)
            self.assertEqual(len(data), 2048)
            # Audio track should fail
            with self.assertRaises(CueError):
                user_sector(d / "oss-pce-cd-test.cue", rep, 10)

    def test_out_of_range_lba(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            build_image(d)
            rep = analyze(d / "oss-pce-cd-test.cue")
            with self.assertRaises(CueError):
                user_sector(d / "oss-pce-cd-test.cue", rep, 16)  # Past end
            with self.assertRaises(CueError):
                user_sector(d / "oss-pce-cd-test.cue", rep, -1)  # Negative

    def test_invalid_cue_syntax(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            # Invalid MSF
            (d / "bad.cue").write_text('FILE "test.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 99:99:99\n', encoding='ascii')
            (d / "test.bin").write_bytes(b"\x00" * 2352)
            with self.assertRaises(CueError):
                analyze(d / "bad.cue")

    def test_invalid_bin_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            # BIN not multiple of sector size
            (d / "test.bin").write_bytes(b"\x00" * 1000)
            (d / "test.cue").write_text('FILE "test.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
            with self.assertRaises(CueError):
                analyze(d / "test.cue")

    def test_unsupported_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            # MODE1/2048 uses 2048-byte sectors
            data = b"".join(sector_payload(lba) for lba in range(10))
            (d / "test.bin").write_bytes(data)
            # MODE1/2048 is not supported for user_sector
            (d / "test.cue").write_text('FILE "test.bin" BINARY\n  TRACK 01 MODE1/2048\n    INDEX 01 00:00:00\n')
            rep = analyze(d / "test.cue")
            self.assertEqual(rep["tracks"][0]["mode"], "MODE1/2048")
            with self.assertRaises(CueError):
                user_sector(d / "test.cue", rep, 0)

    def test_mode2_not_supported_for_user_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            data = b"\x00" * (2336 * 10)
            (d / "test.bin").write_bytes(data)
            (d / "test.cue").write_text('FILE "test.bin" BINARY\n  TRACK 01 MODE2/2336\n    INDEX 01 00:00:00\n')
            rep = analyze(d / "test.cue")
            self.assertEqual(rep["tracks"][0]["mode"], "MODE2/2336")
            with self.assertRaises(CueError):
                user_sector(d / "test.cue", rep, 0)

    def test_msf_lba_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            # Create a sector with wrong MSF
            bad_sector = bytearray(raw_sector(5))
            # Corrupt the MSF in the header (bytes 12-14)
            bad_sector[12:15] = b"\x00\x00\x00"
            (d / "test.bin").write_bytes(bytes(bad_sector))
            (d / "test.cue").write_text('FILE "test.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
            rep = analyze(d / "test.cue")
            # Should have findings about MSF mismatch
            self.assertTrue(any("MSF headers disagree" in f for f in rep["findings"]))

    def test_bad_msf_beyond_probe_limit_is_detected(self):
        """A wrong header after MAX_PROBE_FRAMES must still be a finding (the MSF check covers the whole track)."""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            frames = cue_analysis.MAX_PROBE_FRAMES + 60
            bad = bytearray(raw_sector(frames - 10))
            bad[12:15] = b"\x00\x00\x00"
            body = [bytes(2352)] * frames
            body[frames - 10] = bytes(bad)
            (d / "t.bin").write_bytes(b"".join(body))
            (d / "t.cue").write_text('FILE "t.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
            rep = analyze(d / "t.cue")
            self.assertFalse(rep["structurally_valid"])
            self.assertTrue(any(f"[{frames - 10}]" in f for f in rep["findings"]), rep["findings"])

    def test_track_order_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            data = b"".join(raw_sector(lba) for lba in range(10))
            (d / "test.bin").write_bytes(data)
            # INDEX 01 of track 2 before track 1
            cue_text = ('FILE "test.bin" BINARY\n'
                        '  TRACK 01 MODE1/2352\n'
                        '    INDEX 01 00:02:00\n'
                        '  TRACK 02 MODE1/2352\n'
                        '    INDEX 01 00:00:00\n')
            (d / "test.cue").write_text(cue_text)
            with self.assertRaises(CueError):
                analyze(d / "test.cue")

    def test_case_insensitive_parsing(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            build_image(d)
            # Test lower case keywords
            cue_lower = (d / "oss-pce-cd-test.cue").read_text().lower()
            (d / "test_lower.cue").write_text(cue_lower)
            rep = analyze(d / "test_lower.cue")
            self.assertTrue(rep["structurally_valid"])

    def test_filename_with_spaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            data = b"".join(raw_sector(lba) for lba in range(10))
            bin_name = "My Game.bin"
            (d / bin_name).write_bytes(data)
            cue_text = f'FILE "{bin_name}" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n'
            (d / "test.cue").write_text(cue_text)
            rep = analyze(d / "test.cue")
            self.assertEqual(rep["tracks"][0]["file"], bin_name)

    # --- Streaming / memory efficiency -------------------------------------------------

    def test_large_bin_is_streamed_in_bounded_reads(self):
        """No read request may approach the track size; the probe limit still applies."""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            frames = 7000  # > MAX_PROBE_FRAMES, ~16.5 MB; mostly zero frames keep the fixture fast
            data = b"".join(raw_sector(lba) for lba in range(16)) + bytes(2352 * (frames - 16))
            (d / "big.bin").write_bytes(data)
            (d / "big.cue").write_text('FILE "big.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
            log = []
            with _recording_open(log):
                rep = analyze(d / "big.cue")
            track = rep["tracks"][0]
            self.assertEqual(track["sha256"], hashlib.sha256(data).hexdigest())
            self.assertEqual(track["sector"]["probed_frames"], frames)  # whole track is checked, not a prefix
            self.assertEqual(track["length_frames"], frames)
            requested = [n for n, _ in log]
            self.assertLessEqual(max(requested), cue_analysis.HASH_CHUNK)
            self.assertLess(max(requested), len(data) // 8)
            self.assertTrue(all(got == want for want, got in log), "a read came back short")
            self.assertGreaterEqual(sum(got for _, got in log), len(data))

    def test_small_chunks_give_identical_results(self):
        """Tiny HASH/FRAME/AUDIO chunks must not change any report field or SHA-256."""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            build_image(d, sectors=10, audio_sectors=5)
            cue = d / "oss-pce-cd-test.cue"
            expected = analyze(cue)
            with mock.patch.object(cue_analysis, "HASH_CHUNK", 7), \
                    mock.patch.object(cue_analysis, "FRAME_CHUNK", 3), \
                    mock.patch.object(cue_analysis, "AUDIO_SCAN_FRAMES", 1):
                self.assertEqual(analyze(cue), expected)
            bin_bytes = (d / "oss-pce-cd-test.bin").read_bytes()
            self.assertEqual(expected["tracks"][0]["sha256"],
                             hashlib.sha256(bin_bytes[:10 * 2352]).hexdigest())

    def test_truncated_bin_is_detected(self):
        """A mid-file cut must raise CueError, not silently hash or probe partial data."""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "t.bin").write_bytes(b"".join(raw_sector(lba) for lba in range(10)))
            (d / "t.cue").write_text('FILE "t.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
            with _recording_open([], cut_at=5 * 2352 + 100), self.assertRaisesRegex(CueError, "short read"):
                analyze(d / "t.cue")

    def test_boot_signature_detected_with_lba(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            frames = [bytearray(raw_sector(lba)) for lba in range(4)]
            frames[2][16 + 100:16 + 100 + len(PCE_BOOT_SIGNATURE)] = PCE_BOOT_SIGNATURE
            (d / "t.bin").write_bytes(b"".join(bytes(f) for f in frames))
            (d / "t.cue").write_text('FILE "t.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
            rep = analyze(d / "t.cue")
            self.assertEqual(rep["boot_signatures"], [{"track": 1, "lba": 2, "user_offset": 100,
                                                       "signature": PCE_BOOT_SIGNATURE.decode("ascii")}])

    def test_audio_track_sync_detected_in_scan_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "data.bin").write_bytes(raw_sector(0))
            (d / "audio.bin").write_bytes(SYNC_BYTES + bytes(2352 - 12))
            (d / "t.cue").write_text('FILE "data.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n'
                                     'FILE "audio.bin" BINARY\n  TRACK 02 AUDIO\n    INDEX 01 00:00:00\n')
            rep = analyze(d / "t.cue")
            self.assertTrue(any("AUDIO track unexpectedly contains MODE sync" in f for f in rep["findings"]))


class CueCliTests(unittest.TestCase):
    def _write(self, d: Path, bin_bytes: bytes, cue_text: str) -> Path:
        (d / "t.bin").write_bytes(bin_bytes)
        (d / "t.cue").write_text(cue_text)
        return d / "t.cue"

    def test_mode1_2048_cooked_does_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            cue = self._write(Path(tmp), bytes(2048 * 4),
                              'FILE "t.bin" BINARY\n  TRACK 01 MODE1/2048\n    INDEX 01 00:00:00\n')
            res = _run_cli(cue)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertNotIn("Traceback", res.stderr)
            self.assertIn("sync/MSF not verified", res.stdout)
            self.assertNotIn("sync_frames=", res.stdout)
            self.assertIn("layout validation only", res.stdout)

    def test_mode2_2336_cooked_does_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            cue = self._write(Path(tmp), bytes(2336 * 4),
                              'FILE "t.bin" BINARY\n  TRACK 01 MODE2/2336\n    INDEX 01 00:00:00\n')
            res = _run_cli(cue)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertNotIn("Traceback", res.stderr)
            self.assertIn("sync/MSF not verified", res.stdout)
            self.assertIn("note: user_sector() supports only", res.stdout)

    def test_mode1_2352_output_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            build_image(d)
            res = _run_cli(d / "oss-pce-cd-test.cue")
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn("sync_frames=16", res.stdout)
            self.assertIn("cue_analysis: PASS structural CUE/BIN validation", res.stdout)

    def test_mode2_2352_reported_as_unsupported_for_user_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Header MSF must advance per frame (LBA 0..3 -> 00:02:00..00:02:03)
            frames = b"".join(SYNC_BYTES + bytes([0, 2, i, 2]) + bytes(2352 - 16) for i in range(4))
            cue = self._write(Path(tmp), frames,
                              'FILE "t.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n')
            res = _run_cli(cue)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn("sync_frames=4", res.stdout)
            self.assertIn("MODE2/2352 user data is not extractable", res.stdout)

    def test_audio_multi_track_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            build_image(d, sectors=10, audio_sectors=10)
            res = _run_cli(d / "oss-pce-cd-test.cue")
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn("(1 data, 1 audio)", res.stdout)
            self.assertIn("track 02 AUDIO", res.stdout)
            audio_line = next(line for line in res.stdout.splitlines() if "track 02" in line)
            self.assertNotIn("sync", audio_line)

    def test_invalid_cue_fails_with_single_error_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            cue = self._write(Path(tmp), bytes(2352), 'FILE "t.bin" BINARY\n  TRACK 01 MODE1/2352\n'
                                                      '    INDEX 01 99:99:99\n')
            res = _run_cli(cue)
            self.assertEqual(res.returncode, 1)
            self.assertTrue(res.stderr.startswith("cue_analysis: FAIL/BLOCKED:"), res.stderr)
            self.assertNotIn("Traceback", res.stderr)

    def test_invalid_bin_size_fails_with_single_error_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            cue = self._write(Path(tmp), bytes(1000), 'FILE "t.bin" BINARY\n  TRACK 01 MODE1/2352\n'
                                                      '    INDEX 01 00:00:00\n')
            res = _run_cli(cue)
            self.assertEqual(res.returncode, 1)
            self.assertIn("not a multiple of 2352", res.stderr)
            self.assertNotIn("Traceback", res.stderr)


if __name__ == '__main__':
    unittest.main()