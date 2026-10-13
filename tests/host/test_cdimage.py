"""Host checks for the original, reproducible CD fixture generator (Issue #5)."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from tools.make_cd_image import (
    BIN_NAME, CUE_NAME, RAW_BYTES, USER_BYTES, build_image, raw_sector, sector_payload,
)


class CdImageTests(unittest.TestCase):
    def test_payload_is_2048_bytes_unique_and_not_constant(self):
        payloads = [sector_payload(lba) for lba in range(256)]
        self.assertTrue(all(len(p) == USER_BYTES for p in payloads))
        self.assertEqual(len(set(payloads)), 256)
        self.assertGreater(len(set(payloads[5])), 100)
        self.assertEqual(sector_payload(5)[:17], b"OSSPCE-CD-TEST-V1")
        self.assertEqual(int.from_bytes(sector_payload(5)[24:28], "little"), 5)

    def test_single_bit_difference_between_neighbours_everywhere(self):
        a, b = sector_payload(5), sector_payload(6)
        # every 64-byte window differs, so a shifted/neighbouring sector cannot match by accident
        for start in range(0, USER_BYTES, 64):
            self.assertNotEqual(a[start:start + 64], b[start:start + 64], start)

    def test_raw_frame_layout_mode1_2352(self):
        raw = raw_sector(0)
        self.assertEqual(len(raw), RAW_BYTES)
        self.assertEqual(raw[:12], b"\x00" + b"\xff" * 10 + b"\x00")
        self.assertEqual(raw[12:16], bytes((0x00, 0x02, 0x00, 0x01)))  # 00:02:00 mode 1
        self.assertEqual(raw[16:16 + USER_BYTES], sector_payload(0))
        self.assertEqual(raw_sector(75 * 60)[12:15], bytes((0x01, 0x02, 0x00)))
        self.assertEqual(raw[16 + USER_BYTES:], bytes(RAW_BYTES - 16 - USER_BYTES))

    def test_reproducible_and_manifest_matches_files(self):
        with tempfile.TemporaryDirectory() as one, tempfile.TemporaryDirectory() as two:
            first = build_image(Path(one))
            second = build_image(Path(two))
            self.assertEqual(first, second)
            self.assertEqual((Path(one) / BIN_NAME).read_bytes(), (Path(two) / BIN_NAME).read_bytes())
            data = (Path(one) / BIN_NAME).read_bytes()
            self.assertEqual(len(data), RAW_BYTES * first["sectors"])
            self.assertEqual(hashlib.sha256(data).hexdigest(), first["bin_sha256"])
            self.assertEqual(hashlib.sha256((Path(one) / CUE_NAME).read_bytes()).hexdigest(), first["cue_sha256"])
            for lba, digest in enumerate(first["user_data_sha256"]):
                frame = data[lba * RAW_BYTES:(lba + 1) * RAW_BYTES]
                self.assertEqual(hashlib.sha256(frame[16:16 + USER_BYTES]).hexdigest(), digest)
            self.assertEqual(json.loads((Path(one) / "disc-manifest.json").read_text()), first)
            self.assertIn("MODE1/2352", (Path(one) / CUE_NAME).read_text())

    def test_invalid_sizes_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            for sectors in (0, 4097):
                with self.assertRaises(ValueError):
                    build_image(Path(tmp), sectors)
        with self.assertRaises(ValueError):
            sector_payload(-1)


if __name__ == "__main__":
    unittest.main()
