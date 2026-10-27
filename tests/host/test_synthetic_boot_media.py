"""Host checks for the synthetic CD boot media (wave26 W06): layout, oracle outcomes, pinned digests.

No game data, no emulator. Expected outcomes are hand-written from the DEV loader contract
(src/cd_load_exec_probe.s load_check, docs/cd-load-exec-probe.md). The spec reconstruction below is written from
the same contract and does NOT import the generator; the oracle (tools/synthetic_boot_oracle.py) parses CUE and BIN itself.
Independence is partial: spec_image and the pinned digests re-derive the same formulas as the generator (same author).
The independent layout check is tools/cue_analysis.py (from main, not written for this generator).
"""
import ast
import hashlib
import tempfile
import unittest
from pathlib import Path

from tools import cue_analysis
from tools import synthetic_boot_media as gen
from tools import synthetic_boot_oracle as oracle

# (layout, pregap frames). Single FILE baseline (M4/M5). V2 pregap = 225 = INDEX 01 00:03:00 - INDEX 00 00:00:00
# of the game CUE (docs/cd-boot-loader-spec.md S5). Multi-FILE (V2M) is a separate condition, see MultiFileTests.
LAYOUTS = (("V1", None), ("V2", 225))

# Hand-written expected outcome per mutation (docs/cd-load-exec-probe.md, loader check order).
EXPECTED = {
    "NONE": "OK",
    "BAD_MAGIC": "BAD_MAGIC",
    "BAD_CHECKSUM": "BAD_CHECKSUM",
    "BAD_LENGTH": "BAD_LENGTH",
    "BAD_ENTRY_OFFSET": "BAD_ENTRY_OFFSET",
    "NO_TRACK": "NO_TRACK",
    "DATA_TIMEOUT": "DATA_TIMEOUT",
    "PREMATURE_STATUS": "PREMATURE_STATUS",
    "WRONG_LBA": "BAD_MAGIC",
}

# sha256 of the spec reconstruction (spec_image below), not of generator output.
PINNED = {
    ("V1", None): {"cue": "6047765ac7021dec12105a9bcdddb1aaf69f45a4b62db15e54f4749560a4b53b",
                   "bin": "48ec100686bbd8d12e4f126053a60106394a62cdaa27903db74975b520a57aa8"},
    ("V2", 225): {"cue": "e03109af8148056fcb93e14687494ef9ae40fb0e50f5469a3bd80f940643d381",
                  "bin": "3b3e8c447aa5382ea2e90b9f842ee4b3e69e1f83ce5f7fdb014cc683f60ba51a"},
}

RAW = 2352
USER_OFFSET = 16
USER_BYTES = 2048
MAGIC = b"PCEX"
CODE = bytes([0xEA] * 31 + [0x60])   # the 32 bytes the spec places after the header
LOADER_SRC = Path(__file__).resolve().parents[2] / "src" / "cd_load_exec_probe.s"


# ---- Spec reconstruction (independent of tools/synthetic_boot_media.py) ----------------------------------------

def _spec_msf(frames):
    return "%02d:%02d:%02d" % (frames // 4500, frames // 75 % 60, frames % 75)


def _spec_filler(tag, lba):
    out, counter = b"", 0
    while len(out) < USER_BYTES:
        out += hashlib.sha256(tag + lba.to_bytes(4, "little") + counter.to_bytes(4, "little")).digest()
        counter += 1
    return out[:USER_BYTES]


def _spec_frame(user, lba):
    # 16-byte raw header (sync 00 FF*10 00, BCD MSF of LBA + 150, mode 1), 2048 user bytes, zero tail
    m, s, f = (int(x) for x in _spec_msf(lba + 150).split(":"))
    head = bytes(int(str(v), 16) for v in (m, s, f)) + b"\x01"   # decimal digits read as hex = BCD
    return b"\x00" + b"\xff" * 10 + b"\x00" + head + user + bytes(RAW - USER_OFFSET - USER_BYTES)


def spec_image(pregap, header_user=None):
    """CUE and BIN for the NONE case. pregap None = V1 (no INDEX 00), an int = V2 with that many pregap frames."""
    gap = 0 if pregap is None else pregap
    index00 = 16
    index01 = 16 + gap
    frames = [(hashlib.sha256(b"synthetic-audio" + i.to_bytes(4, "little")).digest() * 74)[:RAW] for i in range(16)]
    frames += [_spec_frame(bytes(USER_BYTES), index00 + i) for i in range(gap)]
    if header_user is None:
        header_user = MAGIC + bytes([0, len(CODE), sum(CODE) & 0xFF, 0]) + CODE
        header_user += _spec_filler(b"synthetic-data", index01)[len(header_user):]
    frames.append(_spec_frame(header_user, index01))
    frames += [_spec_frame(_spec_filler(b"synthetic-data", index01 + i), index01 + i) for i in range(1, 4)]
    lines = ['FILE "synthetic-boot.bin" BINARY', "  TRACK 01 AUDIO", "    INDEX 01 00:00:00", "  TRACK 02 MODE1/2352"]
    if pregap is not None:
        lines.append("    INDEX 00 " + _spec_msf(index00))
    lines.append("    INDEX 01 " + _spec_msf(index01))
    return "\n".join(lines) + "\n", b"".join(frames)


# ---- Tests -----------------------------------------------------------------------------------------------------

class SpecReconstructionTests(unittest.TestCase):
    def test_generator_bytes_equal_spec_reconstruction(self):
        for layout, pregap in LAYOUTS:
            with self.subTest(layout=layout, pregap=pregap):
                img = gen.build_case_image("NONE", layout)
                cue, data = spec_image(None if layout == "V1" else pregap)
                self.assertEqual(img.cue, cue)
                self.assertEqual(img.bin, data)

    def test_pinned_digests(self):
        for layout, pregap in LAYOUTS:
            cue, data = spec_image(None if layout == "V1" else pregap)
            want = PINNED[(layout, pregap)]
            self.assertEqual(hashlib.sha256(cue.encode("ascii")).hexdigest(), want["cue"])
            self.assertEqual(hashlib.sha256(data).hexdigest(), want["bin"])

    def test_oracle_does_not_import_the_generator(self):
        tree = ast.parse(Path(oracle.__file__).read_text(encoding="utf-8"))
        imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        imported |= {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        self.assertFalse(any("synthetic_boot_media" in name for name in imported), imported)


class LayoutTests(unittest.TestCase):
    def test_v1_cue_and_bin_size(self):
        img = gen.build_case_image("NONE", "V1")
        self.assertEqual(img.cue, 'FILE "synthetic-boot.bin" BINARY\n'
                                  '  TRACK 01 AUDIO\n    INDEX 01 00:00:00\n'
                                  '  TRACK 02 MODE1/2352\n    INDEX 01 00:00:16\n')
        self.assertEqual(len(img.bin), 20 * RAW)

    def test_v2_225_frame_positions(self):
        img = gen.build_case_image("NONE", "V2")
        self.assertIn("INDEX 00 00:00:16", img.cue)
        self.assertIn("INDEX 01 00:03:16", img.cue)   # 16 + 225 = 241 frames
        self.assertEqual(len(img.bin), 245 * RAW)

    def test_v2_225_pregap_is_225_frames_from_index00_to_index01(self):
        cue = gen.build_case_image("NONE", "V2").cue
        frames = {}
        for line in cue.splitlines():
            words = line.split()
            if len(words) == 3 and words[0] == "INDEX":
                m, s, f = (int(part) for part in words[2].split(":"))
                frames[words[1]] = (m * 60 + s) * 75 + f
        self.assertEqual(frames["01"] - frames["00"], 225)

    def test_v2_pregap_is_225_not_150(self):
        self.assertEqual(gen.V2_PREGAP_FRAMES, 225)

    def test_cue_analysis_agrees_on_lba_and_sector_headers(self):
        # tools/cue_analysis.py (main, not written for this generator) checks sync, BCD MSF = LBA + 150 and mode.
        for layout, index00, index01 in (("V1", 16, 16), ("V2", 16, 241)):
            with self.subTest(layout=layout), tempfile.TemporaryDirectory() as tmp:
                gen.write_case(Path(tmp), "NONE", layout)
                report = cue_analysis.analyze(Path(tmp) / gen.CUE_NAME)
                self.assertEqual(report["findings"], [])
                self.assertTrue(report["structurally_valid"])
                track = report["tracks"][1]
                self.assertEqual((track["pregap_start_lba"], track["start_lba"]), (index00, index01))
                self.assertEqual(track["pregap_frames"], index01 - index00)

    def test_header_sector_sits_at_index01_and_pregap_holds_no_header(self):
        for layout, pregap in LAYOUTS:
            img = gen.build_case_image("NONE", layout)
            index01 = 16 if layout == "V1" else 16 + pregap
            user = img.bin[index01 * RAW + USER_OFFSET:][:USER_BYTES]
            self.assertEqual(user[:4], MAGIC)
            for frame in range(16, index01):
                self.assertNotEqual(img.bin[frame * RAW + USER_OFFSET:][:4], MAGIC)

    def test_lba0_decoy_is_not_a_header(self):
        for layout, pregap in LAYOUTS:
            img = gen.build_case_image("NONE", layout)
            self.assertNotEqual(img.bin[USER_OFFSET:][:4], MAGIC)


class OracleTests(unittest.TestCase):
    def test_oracle_outcomes_for_every_layout_and_mutation(self):
        for layout, pregap in LAYOUTS:
            for mutation, want in EXPECTED.items():
                with self.subTest(layout=layout, pregap=pregap, mutation=mutation):
                    img = gen.build_case_image(mutation, layout)
                    self.assertEqual(oracle.judge(img.cue, img.bin, img.read_plan), want)

    def test_wrong_lba_is_detected_for_every_layout(self):
        for layout, pregap in LAYOUTS:
            gap = 0 if pregap is None else pregap
            img = gen.build_case_image("NONE", layout)
            with self.subTest(layout=layout, pregap=pregap):
                self.assertEqual(oracle.judge(img.cue, img.bin, {"lba_offset": 0, "abort_in_data": False}), "OK")
                # one sector early, one late, and INDEX 00 (first pregap frame, V2 only) all hold no header
                for offset in (-1, 1) + ((-gap,) if gap else ()):
                    self.assertEqual(oracle.judge(img.cue, img.bin, {"lba_offset": offset, "abort_in_data": False}), "BAD_MAGIC",
                                     msg="offset %d" % offset)

    def test_abnormal_pcex_headers_are_refused(self):
        cue, _ = spec_image(None)
        audio_and_frame = lambda user: spec_image(None, header_user=user)[1]
        good = MAGIC + bytes([0, 2, 3, 0]) + b"\x01\x02"
        pad = lambda head: head + bytes(USER_BYTES - len(head))
        cases = {
            "OK": pad(good),
            "old OSSBOOT1 magic": pad(b"OSSBOOT1" + bytes([0, 2, 3, 0]) + b"\x01\x02"),
            "length 0": pad(MAGIC + bytes([0, 0, 0, 0])),
            "entry equals length": pad(MAGIC + bytes([2, 2, 3, 0]) + b"\x01\x02"),
            "checksum off by one": pad(MAGIC + bytes([0, 2, 4, 0]) + b"\x01\x02"),
        }
        want = {"OK": "OK", "old OSSBOOT1 magic": "BAD_MAGIC", "length 0": "BAD_LENGTH",
                "entry equals length": "BAD_ENTRY_OFFSET", "checksum off by one": "BAD_CHECKSUM"}
        for name, user in cases.items():
            with self.subTest(case=name):
                bin_bytes = audio_and_frame(user)
                self.assertEqual(oracle.judge(cue, bin_bytes, {"lba_offset": 0, "abort_in_data": False}), want[name])


class MultiFileTests(unittest.TestCase):
    """V2M (multi-FILE) is a separate condition: the oracle does not evaluate it, so it must never come back OK."""

    def test_multi_file_cue_is_not_evaluated(self):
        _, data = spec_image(None)
        cue = ('FILE "a.bin" BINARY\n  TRACK 01 AUDIO\n    INDEX 01 00:00:00\n'
               'FILE "b.bin" BINARY\n  TRACK 02 MODE1/2352\n    INDEX 00 00:00:00\n    INDEX 01 00:03:00\n')
        self.assertEqual(oracle.judge(cue, data, {"lba_offset": 0, "abort_in_data": False}), "UNSUPPORTED_MULTI_FILE")

    def test_cue_without_file_has_its_own_status(self):
        cue, data = spec_image(None)
        no_file = "\n".join(line for line in cue.splitlines() if not line.startswith("FILE")) + "\n"
        self.assertEqual(oracle.judge(no_file, data, {"lba_offset": 0, "abort_in_data": False}), "NO_FILE")


class SectorHeaderTests(unittest.TestCase):
    """The oracle alone must reject a data sector whose raw header is wrong (sync, BCD MSF = LBA + 150, mode 1)."""

    def tampered(self, frame, byte, value):
        cue, data = spec_image(225)
        data = bytearray(data)
        data[frame * RAW + byte] = value
        return oracle.judge(cue, bytes(data), {"lba_offset": 0, "abort_in_data": False})

    def test_untampered_v1_and_v2_are_ok(self):
        for pregap in (None, 225):
            cue, data = spec_image(pregap)
            self.assertEqual(oracle.judge(cue, data, {"lba_offset": 0, "abort_in_data": False}), "OK")

    def test_tampered_msf_mode_or_sync_is_rejected(self):
        # frame 241 = V2 INDEX 01 (header sector), 16 = first pregap frame (INDEX 00), 244 = last data frame
        for frame in (16, 120, 241, 244):
            for name, byte, value in (("sync", 0, 0x01), ("sync", 11, 0xFF), ("minute", 12, 0x01),
                                      ("second", 13, 0x00), ("frame", 14, 0x00), ("bcd", 14, 0x1A),
                                      ("mode", 15, 0x02), ("mode", 15, 0x00)):
                with self.subTest(frame=frame, field=name, byte=byte):
                    self.assertEqual(self.tampered(frame, byte, value), "BAD_SECTOR_HEADER")

    def test_audio_frames_are_not_header_checked(self):
        self.assertEqual(self.tampered(0, 12, 0x99), "OK")


class FailClosedInputTests(unittest.TestCase):
    """CUE/BIN inconsistencies and malformed input return a non-OK status; judge() never raises."""

    def setUp(self):
        self.cue, self.data = spec_image(225)
        self.plan = {"lba_offset": 0, "abort_in_data": False}

    def judge(self, cue=None, data=None, plan="default"):
        return oracle.judge(self.cue if cue is None else cue, self.data if data is None else data,
                            self.plan if plan == "default" else plan)

    def test_file_type_must_be_binary(self):
        self.assertEqual(self.judge(cue=self.cue.replace(" BINARY", " WAVE")), "BAD_CUE")
        self.assertEqual(self.judge(cue=self.cue.replace(" BINARY", "")), "BAD_CUE")

    def test_bin_length_must_be_a_multiple_of_2352(self):
        self.assertEqual(self.judge(data=self.data + b"\x00"), "BAD_BIN_SIZE")
        self.assertEqual(self.judge(data=self.data[:-1]), "BAD_BIN_SIZE")

    def test_bin_shorter_than_the_cue_is_rejected(self):
        self.assertEqual(self.judge(data=self.data[:240 * RAW]), "BAD_CUE")   # ends before INDEX 01 (241)

    def test_index00_after_index01_is_rejected(self):
        cue = self.cue.replace("INDEX 00 00:00:16", "INDEX 00 00:03:20")
        self.assertEqual(self.judge(cue=cue), "BAD_CUE")

    def test_out_of_range_or_malformed_msf_is_rejected(self):
        for msf in ("00:60:00", "00:03:75", "-0:03:16", "00:03", "0:03:16", "00:03:1x"):
            with self.subTest(msf=msf):
                self.assertEqual(self.judge(cue=self.cue.replace("INDEX 01 00:03:16", "INDEX 01 " + msf)), "BAD_CUE")

    def test_malformed_lines_return_bad_cue_not_an_exception(self):
        for bad in ("  TRACK\n", "  TRACK xx MODE1/2352\n", "    INDEX 01\n", "    INDEX 02 00:00:00\n",
                    "    INDEX 01 00:03:16\n", "  PREGAP 00:02:00\n"):
            with self.subTest(line=bad):
                self.assertEqual(self.judge(cue=self.cue + bad), "BAD_CUE")
        self.assertEqual(self.judge(cue="    INDEX 01 00:00:00\n" + self.cue), "BAD_CUE")   # INDEX before TRACK

    def test_non_ascii_digits_are_rejected(self):
        # "١" is ARABIC-INDIC DIGIT ONE, "²" is SUPERSCRIPT TWO: \d without re.ASCII / isdigit() accept them.
        self.assertEqual(self.judge(cue=self.cue.replace("INDEX 01 00:03:16", "INDEX 01 00:03:١6")), "BAD_CUE")
        self.assertEqual(self.judge(cue=self.cue.replace("TRACK 02", "TRACK 0²")), "BAD_CUE")

    def test_track_numbers_must_be_sequential_from_01(self):
        self.assertEqual(self.judge(cue=self.cue.replace("TRACK 02", "TRACK 03")), "BAD_CUE")
        self.assertEqual(self.judge(cue=self.cue.replace("TRACK 01", "TRACK 00")), "BAD_CUE")

    def test_postgap_is_rejected_like_pregap(self):
        self.assertEqual(self.judge(cue=self.cue + "  POSTGAP 00:02:00\n"), "BAD_CUE")

    def test_read_plan_must_name_abort_in_data(self):
        self.assertEqual(self.judge(plan={"lba_offset": 0}), "BAD_READ_PLAN")
        self.assertEqual(self.judge(plan={"lba_offset": 0, "abort_in_data": False}), "OK")

    def test_wrong_input_types_return_a_status_not_an_exception(self):
        for cue in (None, 0, self.cue.encode("ascii")):
            with self.subTest(cue=type(cue).__name__):
                self.assertEqual(oracle.judge(cue, self.data, self.plan), "BAD_CUE")
        for data in (0, "x" * RAW, [0] * RAW):
            with self.subTest(bin=type(data).__name__):
                self.assertEqual(oracle.judge(self.cue, data, self.plan), "BAD_BIN_SIZE")
        self.assertEqual(oracle.judge(self.cue, None, self.plan), "BAD_BIN_SIZE")
        for plan in (0, "plan", [("lba_offset", 0)]):
            with self.subTest(plan=type(plan).__name__):
                self.assertEqual(oracle.judge(self.cue, self.data, plan), "BAD_READ_PLAN")

    def test_read_plan_must_carry_an_integer_lba_offset(self):
        for plan in ({}, {"lba_offset": None}, {"lba_offset": "0"}, {"lba_offset": True}, None,
                     {"lba_offset": 0, "abort_in_data": "no"}):
            with self.subTest(plan=plan):
                self.assertEqual(self.judge(plan=plan), "BAD_READ_PLAN")


class LoaderHeaderTests(unittest.TestCase):
    """PCEX bytes against the DEV loader source, read as text. No generator import, no build_cdload_exec import."""

    def setUp(self):
        self.src = LOADER_SRC.read_text(encoding="ascii")

    def test_loader_declares_8_byte_pcex_header_with_fixed_field_offsets(self):
        self.assertRegex(self.src, r"(?m)^HDR_BYTES\s*=\s*8\b")
        self.assertRegex(self.src, r'(?m)^magic:\n\s*\.byte "PCEX"\s*$')
        for offset in ("BUF+4", "BUF+5", "BUF+6"):   # entry, length, checksum
            self.assertIn(offset, self.src)

    def test_generated_header_bytes_match_the_loader_layout(self):
        for layout, pregap in LAYOUTS:
            with self.subTest(layout=layout, pregap=pregap):
                img = gen.build_case_image("NONE", layout)
                lba = oracle.parse_tracks(img.cue)[1]["index01"]   # data track INDEX 01, read by the oracle's parser
                user = img.bin[lba * RAW + USER_OFFSET:][:USER_BYTES]
                self.assertEqual(user[0:4], MAGIC)              # magic
                self.assertEqual(user[4], 0)                    # entry offset
                self.assertEqual(user[5], len(CODE))            # length, 1..255
                self.assertEqual(user[6], sum(CODE) & 0xFF)     # checksum over the code bytes
                self.assertEqual(user[7], 0)                    # reserved
                self.assertEqual(user[8:8 + len(CODE)], CODE)   # code starts at offset 8


if __name__ == "__main__":
    unittest.main()
