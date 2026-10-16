"""Host checks for the Issue #143 minimal reset entry (original code, not a complete BIOS)."""
import json
from pathlib import Path
import tempfile
import unittest

from tools.build_rom import build
from tools.build_reset_entry import RESET_CODE, SYMBOL_OFFSETS, build_reset_entry, verify_reset_entry


class ResetEntryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.base_path = build(cls.root / "base")
        cls.base = cls.base_path.read_bytes()
        cls.rom = build_reset_entry(cls.base_path, cls.root / "debug", "debug")
        cls.release = build_reset_entry(cls.base_path, cls.root / "release", "release")
        cls.payload = cls.rom.read_bytes()

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_reproducible_and_verified(self):
        self.assertEqual(self.payload, self.release.read_bytes())
        self.assertEqual(len(self.payload), 262144)
        verify_reset_entry(self.payload, self.base)

    def test_only_sourced_init_no_guessed_state(self):
        # SEI CLD; MPR0=$FF; MPR1=$F8; LDX #$FF TXS. No CSH, no MPR2..6 writes (UNKNOWN in spec/rom-contract.json).
        self.assertTrue(RESET_CODE.startswith(bytes.fromhex("78 d8 a9ff 5301 a9f8 5302 a2ff 9a".replace(" ", ""))))
        self.assertNotIn(0xD4, RESET_CODE)
        for mask in (0x04, 0x08, 0x10, 0x20, 0x40, 0x80):
            self.assertNotIn(bytes([0x53, mask]), RESET_CODE)

    def test_vectors(self):
        reset = (0xF000).to_bytes(2, "little")
        trap = (0xF000 + SYMBOL_OFFSETS["reset_unexpected_interrupt"]).to_bytes(2, "little")
        self.assertEqual(self.payload[0x1FF6:0x1FFE], trap * 4)
        self.assertEqual(self.payload[0x1FFE:0x2000], reset)

    def test_every_byte_pinned(self):
        for offset in range(0x1000, 0x2000):
            data = bytearray(self.payload)
            data[offset] ^= 1
            with self.assertRaises(ValueError, msg=hex(offset)):
                verify_reset_entry(bytes(data), self.base)

    def test_negative_structures(self):
        cases = {"wrong mpr0": (0x1000 + 3, 0xFE), "wrong mpr1 bank": (0x1000 + 7, 0xF0),
                 "txs removed": (0x1000 + 12, 0xEA), "bad reset vector": (0x1FFE, 0x00),
                 "api table changed": (1, 0x00), "unused bank dirtied": (0x2000, 0)}
        for name, (offset, value) in cases.items():
            data = bytearray(self.payload)
            data[offset] = value if data[offset] != value else value ^ 0xFF
            with self.subTest(name), self.assertRaises(ValueError):
                verify_reset_entry(bytes(data), self.base)
        for data in (self.payload[:-1], b""):
            with self.assertRaises(ValueError):
                verify_reset_entry(data, self.base)

    def test_manifest_not_release_and_unknowns(self):
        m = json.loads((self.root / "debug/manifest.json").read_text())
        self.assertFalse(m["release_eligible"])
        self.assertEqual(m["unknown"], ["mpr2_to_mpr6_layout", "csh_speed_policy", "stack_pointer_abi",
                                        "interrupt_abi", "final_system_card_layout"])


if __name__ == "__main__":
    unittest.main()
