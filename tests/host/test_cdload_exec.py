"""Host checks for the load-and-execute experiment (Issues #14 and #5): build, sector layout, labels. No emulator."""
import hashlib
from pathlib import Path
import tempfile
import unittest

from tools.build_cdload_exec import (EXEC_ADDRESS, HDR_BYTES, SECTOR_BYTES, USER_OFFSET, build_cdload_exec,
                                     build_exec_disc, encode_sector, parse_labels)
from tools.build_rom import build
from tools.make_cd_image import BIN_NAME, RAW_BYTES, sector_payload


class CdLoadExecBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.original = build(cls.root / "original")
        cls.debug_path = build_cdload_exec(cls.original, cls.root / "debug", "debug")
        cls.release_path = build_cdload_exec(cls.original, cls.root / "release", "release")
        cls.payload = cls.debug_path.read_bytes()
        cls.code = (cls.root / "debug/payload.bin").read_bytes()
        cls.symbols = parse_labels(cls.root / "debug/cdload.lbl")
        cls.payload_symbols = parse_labels(cls.root / "debug/payload.lbl")

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_debug_and_release_identical_and_api_table_untouched(self):
        self.assertEqual(self.payload, self.release_path.read_bytes())
        self.assertEqual(len(self.payload), 262144)
        self.assertEqual(self.payload[:0x1000], self.original.read_bytes()[:0x1000])

    def test_payload_starts_at_exec_address_and_fits_the_sector(self):
        self.assertEqual(self.payload_symbols["payload_entry"], EXEC_ADDRESS)
        self.assertTrue(EXEC_ADDRESS <= self.payload_symbols["payload_done"] < EXEC_ADDRESS + len(self.code))
        self.assertTrue(1 <= len(self.code) <= 255)
        self.assertLessEqual(HDR_BYTES + len(self.code), SECTOR_BYTES)

    def test_stage0_return_label_and_halt_labels_are_in_rom(self):
        self.assertEqual(self.symbols["cd_entry"], 0xF000)
        for name in ("cd_payload_return", "cd_halt_ok", "cd_halt_fail", "cd_buffer_ready", "cd_data_done"):
            self.assertGreaterEqual(self.symbols[name], 0xE000)

    def test_sector_header_and_tail_layout(self):
        sector = encode_sector(5, self.code)
        self.assertEqual(sector[:4], b"PCEX")
        self.assertEqual(sector[4], 0)
        self.assertEqual(sector[5], len(self.code))
        self.assertEqual(sector[6], sum(self.code) & 0xFF)
        self.assertEqual(sector[HDR_BYTES:HDR_BYTES + len(self.code)], self.code)
        self.assertEqual(sector[HDR_BYTES + len(self.code):], sector_payload(5)[HDR_BYTES + len(self.code):])

    def test_disc_carries_the_sector_at_requested_lba(self):
        cue = build_exec_disc(self.root / "disc", 5, self.code)
        raw = (self.root / "disc" / BIN_NAME).read_bytes()
        at = 5 * RAW_BYTES + USER_OFFSET
        self.assertEqual(raw[at:at + SECTOR_BYTES], encode_sector(5, self.code))
        self.assertEqual(raw[at - 16:at - 16 + 12], b"\x00" + b"\xff" * 10 + b"\x00")  # sync pattern intact
        self.assertTrue(cue.exists())

    def test_payload_size_limits_are_enforced(self):
        for code in (b"", bytes(256)):
            with self.assertRaises(ValueError):
                encode_sector(5, code)

    def test_tampered_sector_byte_changes_only_its_bytes(self):
        clean = encode_sector(5, self.code)
        damaged = bytearray(clean)
        damaged[HDR_BYTES] ^= 1
        self.assertEqual(sum(a != b for a, b in zip(clean, damaged)), 1)
        self.assertNotEqual(hashlib.sha256(clean).hexdigest(), hashlib.sha256(bytes(damaged)).hexdigest())


if __name__ == "__main__":
    unittest.main()
