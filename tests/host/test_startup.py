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

    def test_manifest_enforces_diagnostic_only(self):
        manifest = json.loads((self.root / "startup/manifest.json").read_text())
        self.assertFalse(manifest["release_eligible"])
        self.assertEqual(manifest["api_implementation"], "NONE")
        self.assertEqual(manifest["final_system_card_layout"], "UNKNOWN")
        self.assertEqual(manifest["startup_symbols"], {name: 0xf000 + pos for name, pos in SYMBOL_OFFSETS.items()})

    def test_bad_mode_refused(self):
        with self.assertRaises(ValueError):
            build_startup(self.original, self.root / "invalid-mode", "unsupported")


if __name__ == "__main__":
    unittest.main()
