"""Host checks for the independent #4 startup diagnostic, not a production BIOS."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from tools.build_rom import build
from tools.build_startup import (
    STARTUP_CODE, SYMBOL_OFFSETS, build_startup, parse_symbols, verify_startup,
)


class StartupBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.original = build(cls.root / "original")
        cls.debug_path = build_startup(cls.original, cls.root / "startup", "debug")
        cls.release_path = build_startup(cls.original, cls.root / "release", "release")
        cls.base = cls.original.read_bytes()
        cls.payload = cls.debug_path.read_bytes()

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_real_assembled_startup_image_and_reproducible_modes(self):
        self.assertEqual(self.payload, self.release_path.read_bytes())
        self.assertEqual(len(self.payload), 262144)
        self.assertNotEqual(self.base, self.payload)
        self.assertEqual(verify_startup(self.payload, self.base), hashlib.sha256(self.payload).hexdigest())
        self.assertEqual(self.payload[:0x1000], self.base[:0x1000])

    def test_symbol_positions_and_vectors(self):
        symbols = parse_symbols(self.root / "startup/startup.lbl")
        self.assertEqual(symbols, {name: 0xf000 + pos for name, pos in SYMBOL_OFFSETS.items()})
        self.assertEqual(self.payload[0x1000:0x1000 + len(STARTUP_CODE)], STARTUP_CODE)
        irq = symbols["startup_unexpected_interrupt"].to_bytes(2, "little")
        self.assertEqual(self.payload[0x1ff6:0x1ffe], irq * 4)
        self.assertEqual(self.payload[0x1ffe:0x2000], b"\x00\xf0")

    def test_duplicate_ld65_names_are_safe_but_conflicting_addresses_fail(self):
        labels = self.root / "startup/startup.lbl"
        lines = labels.read_text().splitlines()
        matching = [line for line in lines if line.endswith(" .startup_entry") and line.startswith("al ")]
        self.assertGreaterEqual(len(matching), 1)
        with tempfile.TemporaryDirectory() as tmp:
            duplicate = Path(tmp) / "duplicate.lbl"
            duplicate.write_text(labels.read_text() + matching[0] + "\n")
            self.assertEqual(parse_symbols(duplicate)["startup_entry"], 0xf000)
            conflicting = Path(tmp) / "conflicting.lbl"
            conflicting.write_text(labels.read_text() + "al 00F001 .startup_entry\n")
            with self.assertRaisesRegex(ValueError, "contradictory"):
                parse_symbols(conflicting)

    def test_corruptions_fail_closed(self):
        for offset in (0, 0x1000, 0x1000 + 9, 0x1000 + SYMBOL_OFFSETS["startup_marker_write"],
                       0x1000 + len(STARTUP_CODE), 0x1ff6, 0x1ffe, 0x2000):
            with self.subTest(offset=offset):
                data = bytearray(self.payload)
                data[offset] ^= 1
                with self.assertRaises(ValueError):
                    verify_startup(data, self.base)
        with self.assertRaises(ValueError):
            verify_startup(self.payload[:-1], self.base)

    def test_every_byte_of_startup_code_vectors_and_padding_is_pinned(self):
        for offset in range(0x1000, 0x2000):  # any single-bit change in $F000-$FFFF must be rejected
            data = bytearray(self.payload)
            data[offset] ^= 0x01
            with self.assertRaises(ValueError, msg=f"offset {offset:#x}"):
                verify_startup(bytes(data), self.base)

    def test_targeted_semantic_corruptions(self):
        code = 0x1000
        cases = {
            "sei removed": (code, 0xEA),
            "mpr5 bad bank": (code + 24, 0x04),
            "mpr7 not reset to bank 0": (code + 32, 0x01),
            "stack pointer changed": (code + 36, 0xFE),
            "txs removed": (code + 37, 0xEA),
            "marker byte changed": (code + SYMBOL_OFFSETS["startup_marker_write"] + 1, 0x00),
            "cd state byte set to disc-dependent value": (code + SYMBOL_OFFSETS["startup_cd_state"] + 1, 0x01),
            "reset vector points at API table": (0x1ffe, 0x00),
            "irq vector points at reset": (0x1ff6, 0x00),
            "extra instruction after checkpoint": (code + len(STARTUP_CODE), 0x60),  # RTS: false success
            "unused bank dirtied": (0x2000, 0x00),
            "api table entry changed": (1, 0x00),
            "api fallback changed": (0x103, 0x00),
        }
        for name, (offset, value) in cases.items():
            data = bytearray(self.payload)
            if data[offset] == value:
                data[offset] ^= 0xFF  # make sure it really is a mutation
            else:
                data[offset] = value
            with self.subTest(name), self.assertRaises(ValueError):
                verify_startup(bytes(data), self.base)

    def test_invalid_base_rom_or_size_is_rejected(self):
        broken_base = bytearray(self.base)
        broken_base[0] = 0x60  # RTS where the table's first JMP must be
        with self.assertRaises(ValueError):
            verify_startup(self.payload, bytes(broken_base))
        for data in (self.payload + bytes(1), self.payload[:0x2000], b""):
            with self.assertRaises(ValueError):
                verify_startup(data, self.base)

    def test_manifest_enforces_diagnostic_only(self):
        manifest = json.loads((self.root / "startup/manifest.json").read_text())
        self.assertFalse(manifest["release_eligible"])
        self.assertEqual(manifest["api_implementation"], "NONE")
        self.assertEqual(manifest["final_system_card_layout"], "UNKNOWN")
        self.assertEqual(manifest["cd_state_value"], 0)
        self.assertFalse(manifest["cd_disc_presence_read"])
        self.assertEqual(manifest["startup_symbols"], {name: 0xf000 + pos for name, pos in SYMBOL_OFFSETS.items()})

    def test_bad_mode_refused(self):
        with self.assertRaises(ValueError):
            build_startup(self.original, self.root / "invalid-mode", "unsupported")


if __name__ == "__main__":
    unittest.main()
