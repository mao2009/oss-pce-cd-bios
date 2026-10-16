"""Host checks for the TOC / track table diagnostic on synthetic CUE/BIN images (Issue #5)."""
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest

from tools.make_cd_image import RAW_BYTES, build_image
from tools.toc_diagnostic import (
    NOT_VERIFIED, TocError, build_toc, diagnose, format_msf, main, parse_cue, parse_msf,
)

DATA_CUE = 'FILE "t.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n'


def write_case(directory: Path, cue_text: str, sectors: int) -> Path:
    (directory / "t.bin").write_bytes(bytes(sectors * RAW_BYTES))
    cue = directory / "t.cue"
    cue.write_text(cue_text, encoding="ascii")
    return cue


class TocPositiveTests(unittest.TestCase):
    def test_generated_data_only_fixture_reports_one_data_track(self):
        with tempfile.TemporaryDirectory() as tmp:
            build_image(Path(tmp), sectors=16)
            report = diagnose(Path(tmp) / "oss-pce-cd-test.cue")
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["sectors"], 16)
        self.assertEqual(report["tracks"], [{
            "track": 1, "type": "data", "lba_start": 0, "absolute_msf": "00:02:00",
            "length_sectors": 16, "pregap_sectors": 0, "pregap_declared": False}])

    def test_generated_mixed_fixture_reports_audio_track_after_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            build_image(Path(tmp), sectors=16, audio_sectors=3)
            report = diagnose(Path(tmp) / "oss-pce-cd-test.cue")
        data, audio = report["tracks"]
        self.assertEqual((data["type"], data["lba_start"], data["length_sectors"]), ("data", 0, 16))
        self.assertEqual((audio["track"], audio["type"], audio["lba_start"]), (2, "audio", 16))
        self.assertEqual(audio["absolute_msf"], "00:02:16")  # LBA 16 + 150 frames
        self.assertEqual(audio["length_sectors"], 3)
        self.assertEqual(report["sectors"], 19)

    def test_declared_pregap_is_reported_as_declared(self):
        cue = ('FILE "t.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 00 00:00:00\n'
               '    INDEX 01 00:02:00\n')
        with tempfile.TemporaryDirectory() as tmp:
            report = diagnose(write_case(Path(tmp), cue, 200))
        self.assertEqual(report["tracks"][0]["pregap_sectors"], 150)
        self.assertTrue(report["tracks"][0]["pregap_declared"])

    def test_text_and_json_both_state_not_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            build_image(Path(tmp), sectors=4)
            cue = str(Path(tmp) / "oss-pce-cd-test.cue")
            text, js = io.StringIO(), io.StringIO()
            with redirect_stdout(text):
                self.assertEqual(main([cue]), 0)
            with redirect_stdout(js):
                self.assertEqual(main([cue, "--json"]), 0)
        self.assertIn(NOT_VERIFIED, text.getvalue())
        report = json.loads(js.getvalue())
        self.assertFalse(report["evidence"]["hardware_verified"])
        self.assertFalse(report["evidence"]["emulator_verified"])


class TocNegativeTests(unittest.TestCase):
    def assertRejected(self, cue_text, sectors=16, pattern=None):
        with tempfile.TemporaryDirectory() as tmp:
            cue = write_case(Path(tmp), cue_text, sectors)
            with self.assertRaises(TocError) as context:
                diagnose(cue)
        if pattern:
            self.assertIn(pattern, str(context.exception))

    def test_no_tracks(self):
        self.assertRejected('FILE "t.bin" BINARY\n', pattern="no tracks")

    def test_track_without_index01(self):
        self.assertRejected('FILE "t.bin" BINARY\n  TRACK 01 MODE1/2352\n', pattern="INDEX 01")

    def test_malformed_msf(self):
        for bad in ("0:00:00", "00:00", "aa:00:00", "00:00:00:00"):
            with self.subTest(bad=bad):
                self.assertRejected(DATA_CUE.replace("00:00:00", bad), pattern="malformed MSF")

    def test_msf_field_out_of_range(self):
        self.assertRejected(DATA_CUE.replace("00:00:00", "00:60:00"), pattern="out of range")
        self.assertRejected(DATA_CUE.replace("00:00:00", "00:00:75"), pattern="out of range")

    def test_track_number_out_of_range(self):
        self.assertRejected(DATA_CUE.replace("TRACK 01", "TRACK 00"), pattern="out of range")
        self.assertRejected(DATA_CUE.replace("TRACK 01", "TRACK 100"), pattern="malformed TRACK")

    def test_track_numbers_must_be_consecutive(self):
        cue = DATA_CUE + '  TRACK 03 AUDIO\n    INDEX 01 00:00:08\n'
        self.assertRejected(cue, pattern="consecutive")

    def test_unsupported_track_type_and_directive(self):
        self.assertRejected(DATA_CUE.replace("MODE1/2352", "MODE1/2048"), pattern="unsupported track type")
        self.assertRejected(DATA_CUE + "  REM hello\n", pattern="unsupported CUE directive")

    def test_index_before_track_and_duplicate_index(self):
        self.assertRejected('FILE "t.bin" BINARY\n    INDEX 01 00:00:00\n', pattern="before any TRACK")
        self.assertRejected(DATA_CUE + "    INDEX 01 00:00:01\n", pattern="duplicate INDEX")

    def test_track_starts_beyond_bin(self):
        self.assertRejected(DATA_CUE.replace("00:00:00", "00:00:16"), sectors=16, pattern="beyond")

    def test_audio_index_not_after_previous_track(self):
        cue = DATA_CUE + "  TRACK 02 AUDIO\n    INDEX 01 00:00:00\n"
        self.assertRejected(cue, pattern="not after the previous")

    def test_bin_size_not_sector_multiple(self):
        with tempfile.TemporaryDirectory() as tmp:
            cue = Path(tmp) / "t.cue"
            cue.write_text(DATA_CUE, encoding="ascii")
            (Path(tmp) / "t.bin").write_bytes(b"\x00" * (RAW_BYTES + 1))
            with self.assertRaises(TocError):
                diagnose(cue)

    def test_missing_bin_and_unsafe_file_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            cue = Path(tmp) / "t.cue"
            cue.write_text(DATA_CUE, encoding="ascii")
            with self.assertRaises(TocError):
                diagnose(cue)
        self.assertRejected(DATA_CUE.replace('"t.bin"', '"../t.bin"'), pattern="same directory")

    def test_two_files_are_refused(self):
        self.assertRejected('FILE "t.bin" BINARY\nFILE "t.bin" BINARY\n' + DATA_CUE.split("\n", 1)[1],
                            pattern="exactly one FILE")

    def test_cli_reports_failure_with_exit_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            cue = write_case(Path(tmp), 'FILE "t.bin" BINARY\n', 16)
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(main([str(cue), "--json"]), 1)
        report = json.loads(out.getvalue())
        self.assertEqual(report["status"], "FAIL")
        self.assertIn("no tracks", report["error"])


class TocBoundaryTests(unittest.TestCase):
    def test_msf_edges(self):
        self.assertEqual(parse_msf("00:00:00"), 0)
        self.assertEqual(parse_msf("00:00:74"), 74)
        self.assertEqual(parse_msf("00:01:00"), 75)
        self.assertEqual(parse_msf("99:59:74"), (99 * 60 + 59) * 75 + 74)
        self.assertEqual(format_msf(150), "00:02:00")
        with self.assertRaises(TocError):
            parse_msf("00:59:75")

    def test_last_sector_is_valid_and_one_past_is_not(self):
        cue = parse_cue(DATA_CUE.replace("00:00:00", "00:00:15"))
        rows = build_toc(cue, 16 * RAW_BYTES)
        self.assertEqual((rows[0]["lba_start"], rows[0]["length_sectors"]), (15, 1))
        with self.assertRaises(TocError):
            build_toc(parse_cue(DATA_CUE.replace("00:00:00", "00:00:16")), 16 * RAW_BYTES)

    def test_track_99_is_accepted(self):
        cue = parse_cue(DATA_CUE + "".join(f"  TRACK {n:02d} AUDIO\n    INDEX 01 {format_msf(n - 1)}\n"
                                           for n in range(2, 100)))
        self.assertEqual(cue["tracks"][-1]["number"], 99)
        self.assertEqual(len(cue["tracks"]), 99)


if __name__ == "__main__":
    unittest.main()
