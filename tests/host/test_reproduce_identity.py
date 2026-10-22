"""describe_image separates headered/headerless images and SHA-256 from CRC32 (Issue #476). Synthetic bytes only."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
from reproduce_static_triage import describe_image  # noqa: E402


class DescribeImage(unittest.TestCase):
    def test_headerless_and_headered_share_payload_facts(self):
        body = bytes(range(256)) * 1024
        a, b = describe_image(body), describe_image(b"\0" * 512 + body)
        self.assertEqual((a["header_bytes"], b["header_bytes"]), (0, 512))
        self.assertEqual(b["header_kind"], "headered")
        self.assertEqual(a["sha256_payload"], b["sha256_payload"])
        self.assertEqual(a["crc32_payload"], b["crc32_payload"])
        self.assertNotEqual(a["sha256_whole_file"], b["sha256_whole_file"])

    def test_odd_size_unsupported(self):
        self.assertEqual(describe_image(b"x" * 100)["header_kind"], "UNSUPPORTED_SIZE")


if __name__ == "__main__":
    unittest.main()
