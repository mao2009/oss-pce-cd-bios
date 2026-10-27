"""Host checks for the synthetic CD boot media (wave26 W06): layout, oracle outcomes, pinned digests.

No game data, no emulator. Expected outcomes are hand-written from the DEV loader contract
(src/cd_load_exec_probe.s load_check, docs/cd-load-exec-probe.md). The spec reconstruction below is written from
the same contract and does NOT import the generator; the oracle (tools/synthetic_boot_oracle.py) parses CUE and BIN itself.
"""
import ast
import hashlib
import unittest
from pathlib import Path

from tools import synthetic_boot_media as gen
from tools import synthetic_boot_oracle as oracle

# (layout, pregap frames). The pregap convention is UNKNOWN, so both 150 and 225 are exercised.
LAYOUTS = (("V1", None), ("V2", 150), ("V2", 225))

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
                   "bin": "973bdb9f317fdd1818a48862a1b35d9efc25510148a71cf2eee9e2357f04a1cd"},
    ("V2", 150): {"cue": "f0af29630e6c88b83796681c6c3218b8d075a21555cc8326e525aff339798314",
                  "bin": "10506681cf35b1ca7346b96f981c76d27fac8233bab641342b7de9b107988811"},
    ("V2", 225): {"cue": "e03109af8148056fcb93e14687494ef9ae40fb0e50f5469a3bd80f940643d381",
                  "bin": "05fca0381d5ab00c528885a04b757073f4e417377e00ca8a1ebde99b09f69225"},
}

RAW = 2352
USER_OFFSET = 16
USER_BYTES = 2048
MAGIC = b"PCEX"
CODE = bytes([0xEA] * 31 + [0x60])   # the 32 bytes the spec places after the header


# ---- Spec reconstruction (independent of tools/synthetic_boot_media.py) ----------------------------------------

def _spec_msf(frames):
    return "%02d:%02d:%02d" % (frames // 4500, frames // 75 % 60, frames % 75)


def _spec_filler(tag, lba):
    out, counter = b"", 0
    while len(out) < USER_BYTES:
        out += hashlib.sha256(tag + lba.to_bytes(4, "little") + counter.to_bytes(4, "little")).digest()
        counter += 1
    return out[:USER_BYTES]


def _spec_frame(user):
    # 16-byte raw header (sync 00 FF*10 00, 4 header bytes zero), 2048 user bytes, zero tail
    return b"\x00" + b"\xff" * 10 + b"\x00" + bytes(4) + user + bytes(RAW - USER_OFFSET - USER_BYTES)


def spec_image(pregap, header_user=None):
    """CUE and BIN for the NONE case. pregap None = V1 (no INDEX 00), an int = V2 with that many pregap frames."""
    gap = 0 if pregap is None else pregap
    index00 = 16
    index01 = 16 + gap
    frames = [(hashlib.sha256(b"synthetic-audio" + i.to_bytes(4, "little")).digest() * 74)[:RAW] for i in range(16)]
    frames += [_spec_frame(bytes(USER_BYTES)) for _ in range(gap)]
    if header_user is None:
        header_user = MAGIC + bytes([0, len(CODE), sum(CODE) & 0xFF, 0]) + CODE
        header_user += _spec_filler(b"synthetic-data", index01)[len(header_user):]
    frames.append(_spec_frame(header_user))
    frames += [_spec_frame(_spec_filler(b"synthetic-data", index01 + i)) for i in range(1, 4)]
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
                img = gen.build_case_image("NONE", layout, pregap)
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

    def test_v2_pregap_150_and_225_frame_positions(self):
        for pregap, index01, total in ((150, 166, 170), (225, 241, 245)):
            img = gen.build_case_image("NONE", "V2", pregap)
            self.assertIn("INDEX 00 00:00:16", img.cue)
            self.assertIn("INDEX 01 " + _spec_msf(index01), img.cue)
            self.assertEqual(len(img.bin), total * RAW)

    def test_v2_225_pregap_is_225_frames_from_index00_to_index01(self):
        cue = gen.build_case_image("NONE", "V2", 225).cue
        frames = {}
        for line in cue.splitlines():
            words = line.split()
            if len(words) == 3 and words[0] == "INDEX":
                m, s, f = (int(part) for part in words[2].split(":"))
                frames[words[1]] = (m * 60 + s) * 75 + f
        self.assertEqual(frames["01"] - frames["00"], 225)

    def test_v2_default_pregap_is_unknown_and_refused(self):
        self.assertIsNone(gen.DEFAULT_V2_PREGAP_FRAMES)
        with self.assertRaises(ValueError):
            gen.build_case_image("NONE", "V2")

    def test_header_sector_sits_at_index01_and_pregap_holds_no_header(self):
        for layout, pregap in LAYOUTS:
            img = gen.build_case_image("NONE", layout, pregap)
            index01 = 16 if layout == "V1" else 16 + pregap
            user = img.bin[index01 * RAW + USER_OFFSET:][:USER_BYTES]
            self.assertEqual(user[:4], MAGIC)
            for frame in range(16, index01):
                self.assertNotEqual(img.bin[frame * RAW + USER_OFFSET:][:4], MAGIC)

    def test_lba0_decoy_is_not_a_header(self):
        for layout, pregap in LAYOUTS:
            img = gen.build_case_image("NONE", layout, pregap)
            self.assertNotEqual(img.bin[USER_OFFSET:][:4], MAGIC)


class OracleTests(unittest.TestCase):
    def test_oracle_outcomes_for_every_layout_and_mutation(self):
        for layout, pregap in LAYOUTS:
            for mutation, want in EXPECTED.items():
                with self.subTest(layout=layout, pregap=pregap, mutation=mutation):
                    img = gen.build_case_image(mutation, layout, pregap)
                    self.assertEqual(oracle.judge(img.cue, img.bin, img.read_plan), want)

    def test_wrong_lba_is_detected_for_every_layout(self):
        for layout, pregap in LAYOUTS:
            gap = 0 if pregap is None else pregap
            img = gen.build_case_image("NONE", layout, pregap)
            with self.subTest(layout=layout, pregap=pregap):
                self.assertEqual(oracle.judge(img.cue, img.bin, {"lba_offset": 0}), "OK")
                # one sector early, one late, and INDEX 00 (first pregap frame, V2 only) all hold no header
                for offset in (-1, 1) + ((-gap,) if gap else ()):
                    self.assertEqual(oracle.judge(img.cue, img.bin, {"lba_offset": offset}), "BAD_MAGIC",
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
                self.assertEqual(oracle.judge(cue, bin_bytes, {"lba_offset": 0}), want[name])


if __name__ == "__main__":
    unittest.main()
