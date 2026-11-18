"""Host tests for the $4A EX_MEMOPEN experimental wiring (Issue #84).

Synthetic only, no ./rom probing. RED->GREEN: missing source/wiring fails
first; implementation passes after. Real behavior is proved by
tests/integration/test_g5_memopen_4a.py; these host tests pin the builder
contract (stub default preserved, experimental opt-in, fail-closed guards).
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class BuildMemopen4AHostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import sys
        import tempfile
        sys.path[:0] = [str(ROOT / "tools")]
        from unittest.mock import patch
        import build_rom
        cls._tmp = tempfile.TemporaryDirectory(prefix="pce-4a-devrom-")
        out = Path(cls._tmp.name) / "dev"
        with patch("sys.argv", ["build_rom.py", "--mode", "debug",
                                "--output", str(out)]):
            assert build_rom.main() == 0
        cls.orig = out / "dev-only-not-compatible-syscard3.pce"
        assert cls.orig.is_file()
        cls.normal = cls.orig.read_bytes()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_sources_exist(self):
        src = (ROOT / "src" / "ex_memopen.s").read_text(encoding="utf-8")
        self.assertIn(".export ex_memopen_entry", src)
        self.assertIn('.segment "BOOT"', src)
        self.assertIn("Issue #84", src)
        self.assertIn("docs/84-4a-contract.md", src)

    def test_builder_default_stub_unchanged(self):
        import sys
        import tempfile
        sys.path[:0] = [str(ROOT / "tools")]
        import build_ipl_loader_g5_2base as b2
        with tempfile.TemporaryDirectory(prefix="pce-4a-stub-") as td:
            rom, syms = b2.build_ipl_loader_g5_rom(
                self.orig, Path(td), 0x109, "debug", "host-stub",
                ["-D", "IPL_PUBLIC"], track=2)
            data = rom.read_bytes()
            tab = data[3 * 0x4A:3 * 0x4A + 3]
            fb = syms["api_slot_4A"]
            self.assertEqual(tuple(tab), (0x4C, fb & 0xFF, (fb >> 8) & 0xFF))
            self.assertEqual(data[3 * 0x4A:3 * 0x4A + 3],
                             self.normal[3 * 0x4A:3 * 0x4A + 3])
            import json
            man = json.loads((Path(td) / "manifest-host-stub-g5.json").read_text())
            self.assertEqual(man["cd_4a"], "stub")
            self.assertNotIn("$4A", man["experimental_slots"])
            self.assertNotIn("src/ex_memopen.s", man["experimental_sources"])

    def test_builder_experimental_wiring(self):
        import sys
        import tempfile
        sys.path[:0] = [str(ROOT / "tools")]
        import build_ipl_loader_g5_2base as b2
        with tempfile.TemporaryDirectory(prefix="pce-4a-exp-") as td:
            rom, syms = b2.build_ipl_loader_g5_rom(
                self.orig, Path(td), 0x109, "debug", "host-exp",
                ["-D", "IPL_PUBLIC"], track=2, cd_4a="experimental")
            data = rom.read_bytes()
            body = syms["ex_memopen_entry"]
            tab = data[3 * 0x4A:3 * 0x4A + 3]
            self.assertEqual(tuple(tab), (0x4C, body & 0xFF, (body >> 8) & 0xFF))
            self.assertGreaterEqual(body, 0xF000)
            self.assertEqual(data[body - 0xE000:body - 0xE000 + 2], bytes((0x08, 0x48)))
            # Full 21-byte body pinned here as well (host-fast duplicate of
            # the real-core structural pin): any silent body edit fails.
            want = bytes((0x08, 0x48, 0xDA, 0x5A, 0x43, 0x02, 0xC9, 0xF8,
                            0xD0, 0x05, 0x7A, 0xFA, 0x68, 0x28, 0x60,
                            0x7A, 0xFA, 0x68, 0x28, 0x38, 0x60))
            self.assertEqual(data[body - 0xE000:body - 0xE000 + 21], want)
            # No memory-write, no MPR-write, no control-transfer opcodes in
            # the body: the byte set is exactly the allowlisted instruction
            # stream (PHP/PHA/PHX/PHY/TMA/CMP/BNE/PLY/PLX/PLA/PLP/SEC/RTS).
            allowed = {0x08, 0x48, 0xDA, 0x5A, 0x43, 0x02, 0xC9, 0xF8,
                       0xD0, 0x05, 0x7A, 0xFA, 0x68, 0x28, 0x60, 0x38}
            for i, b in enumerate(want):
                self.assertIn(b, allowed, f"body byte {i}: unexpected opcode")
            import json
            man = json.loads((Path(td) / "manifest-host-exp-g5.json").read_text())
            self.assertEqual(man["cd_4a"], "experimental")
            self.assertIn("$4A", man["experimental_slots"])
            self.assertIn("src/ex_memopen.s", man["experimental_sources"])

    def test_builder_rejects_bad_cd_4a(self):
        import sys
        import tempfile
        sys.path[:0] = [str(ROOT / "tools")]
        import build_ipl_loader_g5_2base as b2
        with tempfile.TemporaryDirectory(prefix="pce-4a-bad-") as td:
            with self.assertRaises(ValueError):
                b2.build_ipl_loader_g5_rom(
                    self.orig, Path(td), 0x109, "debug", "host-bad",
                    ["-D", "IPL_PUBLIC"], track=2, cd_4a="sometimes")

    def test_redirect_helper_guards(self):
        import sys
        sys.path[:0] = [str(ROOT / "tools")]
        import build_experimental_bios_boot as beb
        # Well-formed stub-table image redirects (4 KiB bank0 image; the
        # body check reads at the BOOT file offset like the real builder).
        bank = bytearray(b"\xff" * 0x2000)
        bank[3 * 0x4A:3 * 0x4A + 3] = bytes((0x4C, 0x1D, 0xE2))
        body_at = 0xFA10
        bank[body_at - 0xE000:body_at - 0xE000 + 2] = bytes((0x08, 0x48))
        out = beb._redirect_slot4a_to_body(bytes(bank), 0xE21D, body_at)
        self.assertEqual(tuple(out[3 * 0x4A:3 * 0x4A + 3]),
                         (0x4C, body_at & 0xFF, (body_at >> 8) & 0xFF))
        # Wrong table bytes refuse.
        bank2 = bytearray(b"\xff" * 0x2000)
        bank2[3 * 0x4A:3 * 0x4A + 3] = bytes((0x4C, 0x00, 0x00))
        with self.assertRaises(ValueError):
            beb._redirect_slot4a_to_body(bytes(bank2), 0xE21D, body_at)
        # Body outside BOOT refuses.
        with self.assertRaises(ValueError):
            beb._redirect_slot4a_to_body(bytes(bank), 0xE21D, 0xE100)
        # Bad prologue refuses.
        bank3 = bytearray(bytes(bank))
        assert len(bank3) >= 0x2000
        bank3[body_at - 0xE000:body_at - 0xE000 + 2] = bytes((0x60, 0xEA))
        with self.assertRaises(ValueError):
            beb._redirect_slot4a_to_body(bytes(bank3), 0xE21D, body_at)
        # Stub address outside BIOS window refuses.
        with self.assertRaises(ValueError):
            beb._redirect_slot4a_to_body(bytes(bank), 0x1000, body_at)

    def test_windows_superset(self):
        import sys
        sys.path[:0] = [str(ROOT / "tools")]
        import build_experimental_bios_boot as beb
        base = beb.unified_api_windows(0xE10A)
        ext = beb.unified_api_windows_4a(0xE10A)
        self.assertEqual(len(base), 5 + 3 + 3)
        self.assertEqual(len(ext), 5 + 3 + 3 + 3)
        self.assertTrue(base < ext)
        self.assertEqual(ext - base, set(range(3 * 0x4A, 3 * 0x4A + 3)))
        # Default entry point unchanged (three windows only).
        sys.path[:0] = [str(ROOT / "tools")]
        import build_ipl_loader_g5_2base as b2
        self.assertEqual(b2.api_windows_for(0xE10A), base)


if __name__ == "__main__":
    unittest.main()
