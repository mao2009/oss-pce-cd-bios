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
from tools.make_cd_image import build_image, raw_sector, sector_payload

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
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            # Pregap (INDEX 00) + data (INDEX 01)
            # BIN frame 150 (INDEX 01) should correspond to disc LBA 0
            # So BIN frames 150+ should contain raw_sector(0), raw_sector(1), ...
            pregap_frames = 150
            user_frames = 50
            total_frames = pregap_frames + user_frames
            # Pregap data (zeros)
            pregap_data = b"\x00" * (pregap_frames * 2352)
            # User data: raw_sector(disc_lba) where disc_lba = bin_frame - 150
            user_data = b"".join(raw_sector(lba) for lba in range(user_frames))
            (d / "test.bin").write_bytes(pregap_data + user_data)
            cue_text = ('FILE "test.bin" BINARY\n'
                        '  TRACK 01 MODE1/2352\n'
                        '    INDEX 00 00:00:00\n'
                        '    INDEX 01 00:02:00\n')  # 150 frames pregap
            (d / "test.cue").write_text(cue_text)
            rep = analyze(d / "test.cue")
            self.assertEqual(rep["tracks"][0]["index"]["0"], 0)
            self.assertEqual(rep["tracks"][0]["index"]["1"], 150)
            self.assertEqual(rep["tracks"][0]["pregap_frames"], 150)
            self.assertEqual(rep["tracks"][0]["index01_frame"], 150)
            self.assertEqual(rep["tracks"][0]["declared_data_lba"], 0)  # INDEX 01 -> disc LBA 0
            self.assertEqual(rep["tracks"][0]["start_lba"], 0)
            self.assertEqual(rep["tracks"][0]["length_frames"], 50)  # 200 - 150 = 50 user data frames
            # User sector at LBA 0 should come from BIN frame 150
            user_data_extracted = user_sector(d / "test.cue", rep, 0)
            self.assertEqual(len(user_data_extracted), 2048)
            expected = sector_payload(0)
            self.assertEqual(user_data_extracted, expected)

    def test_index01_data_matches_expected_payload(self):
        """INDEX 01 position data matches expected sector_payload."""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            pregap_frames = 150
            user_frames = 10
            pregap_data = b"\x00" * (pregap_frames * 2352)
            user_data = b"".join(raw_sector(lba) for lba in range(user_frames))
            (d / "test.bin").write_bytes(pregap_data + user_data)
            cue_text = ('FILE "test.bin" BINARY\n'
                        '  TRACK 01 MODE1/2352\n'
                        '    INDEX 00 00:00:00\n'
                        '    INDEX 01 00:02:00\n')
            (d / "test.cue").write_text(cue_text)
            rep = analyze(d / "test.cue")
            # Test multiple LBAs
            for lba in range(user_frames):
                extracted = user_sector(d / "test.cue", rep, lba)
                expected = sector_payload(lba)
                self.assertEqual(extracted, expected, f"LBA {lba} mismatch")

    def test_pregap_not_extractable_as_user_data(self):
        """Pregap (before INDEX 01) cannot be extracted as user data."""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            pregap_frames = 150
            user_frames = 10
            pregap_data = b"\x00" * (pregap_frames * 2352)
            user_data = b"".join(raw_sector(lba) for lba in range(user_frames))
            (d / "test.bin").write_bytes(pregap_data + user_data)
            cue_text = ('FILE "test.bin" BINARY\n'
                        '  TRACK 01 MODE1/2352\n'
                        '    INDEX 00 00:00:00\n'
                        '    INDEX 01 00:02:00\n')
            (d / "test.cue").write_text(cue_text)
            rep = analyze(d / "test.cue")
            # LBA -1 should fail (negative)
            with self.assertRaises(CueError):
                user_sector(d / "test.cue", rep, -1)
            # LBA 0 is first user data sector (BIN frame 150)
            # There is no way to access pregap frames via user_sector since they map to negative LBAs
            # Verify track reports correct pregap_frames
            self.assertEqual(rep["tracks"][0]["pregap_frames"], 150)

    def test_no_false_positive_msf_mismatch(self):
        """MSF mismatch false positives should not occur with valid INDEX 00/01."""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            pregap_frames = 150
            user_frames = 10
            pregap_data = b"\x00" * (pregap_frames * 2352)
            user_data = b"".join(raw_sector(lba) for lba in range(user_frames))
            (d / "test.bin").write_bytes(pregap_data + user_data)
            cue_text = ('FILE "test.bin" BINARY\n'
                        '  TRACK 01 MODE1/2352\n'
                        '    INDEX 00 00:00:00\n'
                        '    INDEX 01 00:02:00\n')
            (d / "test.cue").write_text(cue_text)
            rep = analyze(d / "test.cue")
            # Should have no MSF mismatch findings
            msf_findings = [f for f in rep["findings"] if "MSF headers disagree" in f]
            self.assertEqual(len(msf_findings), 0, f"Unexpected MSF findings: {msf_findings}")

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

    def test_multiple_tracks_with_index01_no_boundary_cross(self):
        """Multiple tracks with INDEX 01 don't cross boundaries."""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            # Track 1: 100 user frames (no pregap), Track 2: 100 user frames with 150 pregap
            # Total BIN: 100 + 150 + 100 = 350 frames
            track1_frames = 100
            pregap2_frames = 150
            track2_user_frames = 100
            track1_data = b"".join(raw_sector(lba) for lba in range(track1_frames))
            pregap2_data = b"\x00" * (pregap2_frames * 2352)
            track2_data = b"".join(raw_sector(lba + track1_frames) for lba in range(track2_user_frames))
            (d / "test.bin").write_bytes(track1_data + pregap2_data + track2_data)
            # BIN frame offsets in MSF format (MM:SS:FF):
            # Frame 0 = 00:00:00
            # Frame 100 = 00:01:25 (100 frames = 1 sec 25 frames)
            # Frame 250 = 00:03:25 (250 frames = 3 sec 25 frames)
            cue_text = ('FILE "test.bin" BINARY\n'
                        '  TRACK 01 MODE1/2352\n'
                        '    INDEX 01 00:00:00\n'
                        '  TRACK 02 MODE1/2352\n'
                        '    INDEX 00 00:01:25\n'
                        '    INDEX 01 00:03:25\n')
            (d / "test.cue").write_text(cue_text)
            rep = analyze(d / "test.cue")
            self.assertEqual(len(rep["data_tracks"]), 2)
            self.assertEqual(rep["tracks"][0]["start_lba"], 0)
            self.assertEqual(rep["tracks"][0]["length_frames"], 100)
            self.assertEqual(rep["tracks"][1]["start_lba"], 100)
            self.assertEqual(rep["tracks"][1]["length_frames"], 100)
            self.assertEqual(rep["tracks"][1]["pregap_frames"], 150)
            self.assertEqual(rep["tracks"][1]["index01_frame"], 250)
            # Test extraction at boundaries
            # Track 1 last sector (LBA 99)
            data = user_sector(d / "test.cue", rep, 99)
            self.assertEqual(data, sector_payload(99))
            # Track 2 first sector (LBA 100)
            data = user_sector(d / "test.cue", rep, 100)
            self.assertEqual(data, sector_payload(100))
            # Track 2 last sector (LBA 199)
            data = user_sector(d / "test.cue", rep, 199)
            self.assertEqual(data, sector_payload(199))
            # LBA 200 should fail (past end)
            with self.assertRaises(CueError):
                user_sector(d / "test.cue", rep, 200)

    def test_multiple_bin_files_offset_calculation(self):
        """Multiple BIN files with correct offset calculation."""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            # Track 1 in bin1: 50 user frames
            track1_data = b"".join(raw_sector(lba) for lba in range(50))
            (d / "track01.bin").write_bytes(track1_data)
            # Track 2 in bin2: 150 pregap + 50 user frames
            pregap_data = b"\x00" * (150 * 2352)
            track2_data = b"".join(raw_sector(lba + 50) for lba in range(50))
            (d / "track02.bin").write_bytes(pregap_data + track2_data)
            cue_text = ('FILE "track01.bin" BINARY\n'
                        '  TRACK 01 MODE1/2352\n'
                        '    INDEX 01 00:00:00\n'
                        'FILE "track02.bin" BINARY\n'
                        '  TRACK 02 MODE1/2352\n'
                        '    INDEX 00 00:00:00\n'
                        '    INDEX 01 00:02:00\n')
            (d / "test.cue").write_text(cue_text)
            rep = analyze(d / "test.cue")
            self.assertEqual(len(rep["data_tracks"]), 2)
            self.assertEqual(rep["tracks"][0]["file"], "track01.bin")
            self.assertEqual(rep["tracks"][0]["start_lba"], 0)
            self.assertEqual(rep["tracks"][0]["length_frames"], 50)
            self.assertEqual(rep["tracks"][1]["file"], "track02.bin")
            self.assertEqual(rep["tracks"][1]["start_lba"], 50)
            self.assertEqual(rep["tracks"][1]["length_frames"], 50)
            self.assertEqual(rep["tracks"][1]["pregap_frames"], 150)
            self.assertEqual(rep["tracks"][1]["index01_frame"], 150)
            # Test extraction
            data = user_sector(d / "test.cue", rep, 0)
            self.assertEqual(data, sector_payload(0))
            data = user_sector(d / "test.cue", rep, 49)
            self.assertEqual(data, sector_payload(49))
            data = user_sector(d / "test.cue", rep, 50)
            self.assertEqual(data, sector_payload(50))
            data = user_sector(d / "test.cue", rep, 99)
            self.assertEqual(data, sector_payload(99))

    def test_pregap_postgap(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            # 400 sectors: 150 pregap + 100 data + 150 audio
            sectors = 400
            data = b"".join(raw_sector(lba) for lba in range(sectors))
            (d / "test.bin").write_bytes(data)
            cue_text = ('FILE "test.bin" BINARY\n'
                        '  TRACK 01 MODE1/2352\n'
                        '    PREGAP 00:02:00\n'
                        '    INDEX 01 00:02:00\n'
                        '  TRACK 02 AUDIO\n'
                        '    POSTGAP 00:01:00\n'
                        '    INDEX 01 00:04:00\n')
            (d / "test.cue").write_text(cue_text)
            rep = analyze(d / "test.cue")
            self.assertEqual(rep["tracks"][0]["pregap"], 150)
            self.assertEqual(rep["tracks"][1]["postgap"], 75)
            # Track 1 starts at INDEX 01 (150), track 2 starts at INDEX 01 (300)
            self.assertEqual(rep["tracks"][0]["length_frames"], 150)
            self.assertEqual(rep["tracks"][1]["length_frames"], 100)

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
            self.assertEqual(track["sector"]["probed_frames"], cue_analysis.MAX_PROBE_FRAMES)
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
            frame = SYNC_BYTES + bytes([0, 2, 0, 2]) + bytes(2352 - 16)
            cue = self._write(Path(tmp), frame * 4,
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