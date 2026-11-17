"""Issue #793 host: G5 $4A SIMULATED diagnostic variant (synthetic, no commercial).

Parent #635. NOT compatible, NOT ABI, release_eligible=false.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from tools.environment import cc65_skip_reason, cc65_required, requires_cc65  # noqa: E402


@requires_cc65
class G5FourASimHostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import shutil
        import tempfile
        from build_rom import build
        cls.work = Path(tempfile.mkdtemp(prefix="pce-g54a-host-"))
        cls.addClassCleanup(shutil.rmtree, cls.work, ignore_errors=True)
        normal_path = build(cls.work / "normal", root=ROOT)
        cls.normal = normal_path.read_bytes()

    def _build(self, variant, sel=0x109, track=1, private=False):
        from build_ipl_loader_g5_4a import build_ipl_loader_g5_4a_simulated
        out = self.work / f"g5-{variant}"
        return build_ipl_loader_g5_4a_simulated(
            self.work / "normal" / "dev-only-not-compatible-syscard3.pce",
            out, sel, "debug", variant, ["-D", "IPL_PUBLIC"], track,
            private_input=private)

    def test_standard_rom_untouched_by_diagnostic(self):
        from build_ipl_loader_g5 import build_ipl_loader_g5_rom
        import hashlib
        out1 = self.work / "std-only"
        rom1, _ = build_ipl_loader_g5_rom(
            self.work / "normal" / "dev-only-not-compatible-syscard3.pce",
            out1, 0x109, "debug", "stdcheck", ["-D", "IPL_PUBLIC"], 1)
        h1 = hashlib.sha256(rom1.read_bytes()).hexdigest()
        std_rom, diag_rom, _, _ = self._build("stdcheck2")
        # Diagnostic must not overwrite the standard file of the same variant build;
        # standard SHA must be stable across rebuilds.
        out2 = self.work / "std-only2"
        rom2, _ = build_ipl_loader_g5_rom(
            self.work / "normal" / "dev-only-not-compatible-syscard3.pce",
            out2, 0x109, "debug", "stdcheck", ["-D", "IPL_PUBLIC"], 1)
        self.assertEqual(hashlib.sha256(rom2.read_bytes()).hexdigest(), h1)
        self.assertTrue(std_rom.is_file())
        self.assertTrue(diag_rom.is_file())
        self.assertNotEqual(std_rom, diag_rom)

    def test_patch_window_is_4a_only(self):
        std_rom, diag_rom, _, info = self._build("wincheck")
        std = std_rom.read_bytes()
        diag = diag_rom.read_bytes()
        diffs = [i for i, (a, b) in enumerate(zip(std, diag)) if a != b]
        self.assertEqual(len(diffs), 5)
        # Fallback offset derived from label, table entry $E0DE intact.
        self.assertEqual(info["table_entry"], "$E0DE")
        self.assertEqual(info["before"], "a24a4c3be2")
        self.assertEqual(info["after"], "60eaeaeaea")
        self.assertEqual(info["classification"][:9], "SIMULATED")
        self.assertFalse(info["dev_callable"])
        self.assertFalse(info["release_eligible"])

    def test_mismatch_blocked(self):
        from build_ipl_loader_g5_4a import build_ipl_loader_g5_4a_simulated
        # Bad track refuses before any diagnostic write.
        with self.assertRaises(ValueError):
            self._build("badtrk", track=0)
        with self.assertRaises(ValueError):
            self._build("badsel", sel=0x1FFFF)


if __name__ == "__main__":
    unittest.main()
