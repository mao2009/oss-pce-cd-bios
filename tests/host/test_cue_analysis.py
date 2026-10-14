import json
import tempfile
import unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.cue_analysis import analyze, CueError, user_sector
from tools.make_cd_image import build_image, raw_sector, sector_payload


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
            sectors = 20
            data = b"".join(raw_sector(lba) for lba in range(sectors))
            (d / "test.bin").write_bytes(data)
            cue_text = ('FILE "test.bin" BINARY\n'
                        '  TRACK 01 MODE1/2352\n'
                        '    INDEX 00 00:00:00\n'
                        '    INDEX 01 00:02:00\n')  # 150 frames pregap
            (d / "test.cue").write_text(cue_text)
            rep = analyze(d / "test.cue")
            self.assertEqual(rep["tracks"][0]["index"]["0"], 0)
            self.assertEqual(rep["tracks"][0]["index"]["1"], 150)
            self.assertEqual(rep["tracks"][0]["declared_data_lba"], 150)

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


if __name__ == '__main__':
    unittest.main()