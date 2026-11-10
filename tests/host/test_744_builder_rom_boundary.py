"""Issue #745 LOW: 256KiB DEV-BIOS boundary builds (needs cc65 + make dev-rom).

For each boundary INDEX 01 (0x0000, 0x0109, 0xFFF0, 0xFFFE, 0xFFFF),
tools/build_dev_ipl_loader_bios.build_ipl_loader_bios must assemble and link
a 262144-byte DEV loader BIOS: manifest carries the exact SEL, the info-LBA
carry chain (LDA #0 / ADC #0 / STA $03) is present in bank 0, and the images
differ across SELs (proving the embedding varies).

This is a BUILD verification only. It does not prove a real-core media read
at any LBA: INDEX 01 = 0xFFFF still reads info sector 0x010000, whose real-core
read on synthetic media is UNKNOWN. No commercial media, no private input.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from environment import requires_cc65  # noqa: E402
from build_dev_ipl_loader_bios import build_ipl_loader_bios, IMAGE_BYTES  # noqa: E402

ORIGINAL = ROOT / "build" / "debug" / "bios-dev" / "dev-only-not-compatible-syscard3.pce"
SELS = (0x0000, 0x0109, 0xFFF0, 0xFFFE, 0xFFFF)


@requires_cc65
class BuilderRomBoundaryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not ORIGINAL.is_file():
            raise unittest.SkipTest("needs make dev-rom output (CI build step)")
        tmp = tempfile.TemporaryDirectory(prefix="pce-sel-boundary-")
        cls.addClassCleanup(tmp.cleanup)
        cls.out = Path(tmp.name)
        cls.roms = {}
        for sel in SELS:
            rom, _symbols = build_ipl_loader_bios(
                ORIGINAL, cls.out / f"sel_{sel:04x}", sel, "debug",
                f"sel{sel:04x}")
            cls.roms[sel] = rom.read_bytes()

    def test_images_are_256kib(self):
        for sel, data in self.roms.items():
            with self.subTest(sel=hex(sel)):
                self.assertEqual(len(data), IMAGE_BYTES)
                self.assertEqual(len(data), 262144)

    def test_manifest_carries_exact_sel(self):
        for sel in SELS:
            with self.subTest(sel=hex(sel)):
                manifest = json.loads(
                    (self.out / f"sel_{sel:04x}" / f"manifest-sel{sel:04x}.json")
                    .read_text(encoding="utf-8"))
                self.assertEqual(manifest["ipl_sel_lba"], sel)
                self.assertIs(manifest["private_media_used"], False)
                self.assertNotIn("ipl_sel_lba_redacted", manifest)

    def test_embedding_varies_and_carry_chain_present(self):
        bank0 = {sel: data[:0x2000] for sel, data in self.roms.items()}
        images = list(bank0.values())
        for i in range(len(images)):
            for j in range(i + 1, len(images)):
                self.assertNotEqual(images[i], images[j],
                                    "SEL embedding must differ across boundary values")
        for sel, code in bank0.items():
            with self.subTest(sel=hex(sel)):
                # Reset path: LDA #0 / ADC #0 / STA lbahi($03) carries INDEX01+1
                # out of 16 bits (0xFFFF -> 0x010000); the old truncation
                # (LDA #0 / STA $03) must be absent.
                self.assertIn(bytes([0x69, 0x00, 0x85, 0x03]), code)
                self.assertNotIn(bytes([0xA9, 0x00, 0x85, 0x03]), code)

    def test_out_of_range_sel_never_builds(self):
        for bad in (-1, 0x10000):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    build_ipl_loader_bios(ORIGINAL, self.out / "bad", bad)


if __name__ == "__main__":
    unittest.main()
