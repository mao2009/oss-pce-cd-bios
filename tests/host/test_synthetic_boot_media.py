"""Host checks for the synthetic CD boot media (wave26 W06): layout, oracle outcomes, pinned digests.

No game data, no emulator. The expected outcomes below are hand-written from docs/cd-boot-loader-spec.md section 5;
they are not derived from the generator. The oracle (tools/synthetic_boot_oracle.py) parses the CUE and BIN itself.
"""
import hashlib
import unittest

from tools import synthetic_boot_media as gen
from tools import synthetic_boot_oracle as oracle

# (layout, pregap frames). The pregap convention is UNKNOWN, so both 150 and 225 are exercised.
LAYOUTS = (("V1", None), ("V2", 150), ("V2", 225))

# Hand-written expected outcome per mutation (docs/cd-boot-loader-spec.md section 5).
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

# Expected digests of the generator output (sha256). Changing the generator requires re-pinning these.
PINNED = {
    ("V1", None): {"cue": "6047765ac7021dec12105a9bcdddb1aaf69f45a4b62db15e54f4749560a4b53b",
                   "bin": "42b65c0a08c8b22d825f443545898418f8cf55323f2dc02d654175c278c2d2c4"},
    ("V2", 150): {"cue": "f0af29630e6c88b83796681c6c3218b8d075a21555cc8326e525aff339798314",
                  "bin": "3227d71006947bb3f35365301b371150860420855086a740e1eaa603980cc756"},
    ("V2", 225): {"cue": "e03109af8148056fcb93e14687494ef9ae40fb0e50f5469a3bd80f940643d381",
                  "bin": "cdc7025c30b8f01fa7178be1c4e16df77fced4a6591b4a04a50067216d7be324"},
}

RAW = 2352
USER_OFFSET = 16
MAGIC = b"OSSBOOT1"


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
            msf = lambda frames: "%02d:%02d:%02d" % (frames // 4500, frames // 75 % 60, frames % 75)
            self.assertIn("INDEX 00 00:00:16", img.cue)
            self.assertIn("INDEX 01 " + msf(index01), img.cue)
            self.assertEqual(len(img.bin), total * RAW)

    def test_v2_default_pregap_is_unknown_and_refused(self):
        self.assertIsNone(gen.DEFAULT_V2_PREGAP_FRAMES)
        with self.assertRaises(ValueError):
            gen.build_case_image("NONE", "V2")

    def test_header_sector_sits_at_index01_and_pregap_holds_no_header(self):
        for layout, pregap in LAYOUTS:
            img = gen.build_case_image("NONE", layout, pregap)
            index01 = 16 if layout == "V1" else 16 + pregap
            user = img.bin[index01 * RAW + USER_OFFSET:][:2048]
            self.assertEqual(user[:8], MAGIC)
            for frame in range(16, index01):
                self.assertNotEqual(img.bin[frame * RAW + USER_OFFSET:][:8], MAGIC)

    def test_lba0_decoy_is_not_a_header(self):
        for layout, pregap in LAYOUTS:
            img = gen.build_case_image("NONE", layout, pregap)
            self.assertNotEqual(img.bin[USER_OFFSET:][:8], MAGIC)

    def test_pinned_digests(self):
        for layout, pregap in LAYOUTS:
            img = gen.build_case_image("NONE", layout, pregap)
            want = PINNED[(layout, pregap)]
            self.assertEqual(hashlib.sha256(img.cue.encode("ascii")).hexdigest(), want["cue"])
            self.assertEqual(hashlib.sha256(img.bin).hexdigest(), want["bin"])


class OracleTests(unittest.TestCase):
    def test_hand_built_image_ok_and_checksum_fault(self):
        # Built here, not by the generator: audio frame, then one data frame with header + 2 code bytes.
        audio = bytes(RAW)
        user = bytearray(2048)
        user[0:8] = MAGIC
        user[8:10] = (0).to_bytes(2, "little")      # entry
        user[10:12] = (2).to_bytes(2, "little")     # length
        user[12] = 3                                # checksum = 1 + 2
        user[16:18] = b"\x01\x02"
        frame = bytes(16) + bytes(user) + bytes(RAW - 16 - 2048)
        cue = 'FILE "x.bin" BINARY\n  TRACK 01 AUDIO\n    INDEX 01 00:00:00\n  TRACK 02 MODE1/2352\n    INDEX 01 00:00:01\n'
        image = audio + frame
        self.assertEqual(oracle.judge(cue, image, {"lba_offset": 0}), "OK")
        bad = bytearray(image)
        bad[RAW + USER_OFFSET + 16] ^= 0xFF
        self.assertEqual(oracle.judge(cue, bytes(bad), {"lba_offset": 0}), "BAD_CHECKSUM")

    def test_oracle_outcomes_for_every_layout_and_mutation(self):
        for layout, pregap in LAYOUTS:
            for mutation, want in EXPECTED.items():
                with self.subTest(layout=layout, pregap=pregap, mutation=mutation):
                    img = gen.build_case_image(mutation, layout, pregap)
                    self.assertEqual(oracle.judge(img.cue, img.bin, img.read_plan), want)


if __name__ == "__main__":
    unittest.main()
