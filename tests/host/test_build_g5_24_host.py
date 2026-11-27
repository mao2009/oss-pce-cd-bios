"""Host tests for the $24 EX_DOTMOD B1-analogous experimental wiring (Issue #46).

Synthetic only, no ./rom probing. RED->GREEN: missing source/wiring fails
first; implementation passes after. Real behavior is proved by
tests/integration/test_g5_dotmod_24.py; these host tests pin the builder
contract (stub default preserved, experimental opt-in, fail-closed guards).
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# B1-analogous preservation-only scaffold bytes: PHP PHA PHX PHY PLY PLX PLA PLP RTS.
B1_BODY = bytes((0x08, 0x48, 0xDA, 0x5A, 0x7A, 0xFA, 0x68, 0x28, 0x60))


class BuildDotmod24HostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import sys
        import tempfile
        sys.path[:0] = [str(ROOT / "tools")]
        from unittest.mock import patch
        import build_rom
        cls._tmp = tempfile.TemporaryDirectory(prefix="pce-24-devrom-")
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
        src = (ROOT / "src" / "ex_dotmod.s").read_text(encoding="utf-8")
        self.assertIn(".export ex_dotmod_entry", src)
        self.assertIn('.segment "BOOT"', src)
        self.assertIn("Issue #46", src)
        self.assertIn("docs/46-ex-dotmod-contract.md", src)
        self.assertIn("B1-analogous", src)

    def test_builder_default_stub_unchanged(self):
        import sys
        import tempfile
        sys.path[:0] = [str(ROOT / "tools")]
        import build_ipl_loader_g5_2base as b2
        with tempfile.TemporaryDirectory(prefix="pce-24-stub-") as td:
            rom, syms = b2.build_ipl_loader_g5_rom(
                self.orig, Path(td), 0x109, "debug", "host-stub",
                ["-D", "IPL_PUBLIC"], track=2)
            data = rom.read_bytes()
            tab = data[3 * 0x24:3 * 0x24 + 3]
            fb = syms["api_slot_24"]
            self.assertEqual(tuple(tab), (0x4C, fb & 0xFF, (fb >> 8) & 0xFF))
            self.assertEqual(data[3 * 0x24:3 * 0x24 + 3],
                             self.normal[3 * 0x24:3 * 0x24 + 3])
            # Stub default must not link the experimental body.
            self.assertNotIn("ex_dotmod_entry", syms)
            self.assertNotIn(B1_BODY, data,
                             "stub default ROM must not contain the experimental body")
            import json
            man = json.loads((Path(td) / "manifest-host-stub-g5.json").read_text())
            self.assertEqual(man["cd_24"], "stub")
            self.assertNotIn("$24", man["experimental_slots"])
            self.assertNotIn("src/ex_dotmod.s", man["experimental_sources"])

    def test_stub_default_matches_pre_24_baseline(self):
        # Stub-default G5 ROM must be byte-for-byte identical to the pre-$24
        # baseline (same bytes as the pre-$25 baseline: the (stub, stub, stub)
        # default links proto/link/glue in the same order, so adding the
        # opt-in $24 path must not move a single byte).
        import sys
        import tempfile
        import hashlib
        sys.path[:0] = [str(ROOT / "tools")]
        import build_ipl_loader_g5_2base as b2
        with tempfile.TemporaryDirectory(prefix="pce-24-byteid-") as td:
            rom, syms = b2.build_ipl_loader_g5_rom(
                self.orig, Path(td), 0x109, "debug", "byteid-stub",
                ["-D", "IPL_PUBLIC"], track=2,
                cd_4a="stub", cd_25="stub", cd_24="stub")
            data = rom.read_bytes()
            self.assertEqual(len(data), 262144)
            self.assertNotIn("ex_dotmod_entry", syms)
            pinned = "9a7a2c00bed665a9d90030b824ad3c8e7d7a81c2eec71f824d6e8fb59ce00bc6"
            self.assertEqual(hashlib.sha256(data).hexdigest(), pinned,
                             "stub-default G5 ROM differs from pre-$24 baseline")

    def test_builder_experimental_wiring(self):
        import sys
        import tempfile
        sys.path[:0] = [str(ROOT / "tools")]
        import build_ipl_loader_g5_2base as b2
        with tempfile.TemporaryDirectory(prefix="pce-24-exp-") as td:
            rom, syms = b2.build_ipl_loader_g5_rom(
                self.orig, Path(td), 0x109, "debug", "host-exp",
                ["-D", "IPL_PUBLIC"], track=2,
                cd_4a="stub", cd_25="stub", cd_24="experimental")
            data = rom.read_bytes()
            body = syms["ex_dotmod_entry"]
            tab = data[3 * 0x24:3 * 0x24 + 3]
            self.assertEqual(tuple(tab), (0x4C, body & 0xFF, (body >> 8) & 0xFF))
            self.assertGreaterEqual(body, 0xF000)
            self.assertEqual(data[body - 0xE000:body - 0xE000 + 9], B1_BODY)
            self.assertNotEqual(B1_BODY[0], 0x60)  # not a bare RTS
            allowed = {0x08, 0x48, 0xDA, 0x5A, 0x7A, 0xFA, 0x68, 0x28, 0x60}
            for i, b in enumerate(B1_BODY):
                self.assertIn(b, allowed, f"body byte {i}: unexpected opcode")
            import json
            man = json.loads((Path(td) / "manifest-host-exp-g5.json").read_text())
            self.assertEqual(man["cd_24"], "experimental")
            self.assertIn("$24", man["experimental_slots"])
            self.assertIn("src/ex_dotmod.s", man["experimental_sources"])

    def test_builder_others_only_keep_24_stub(self):
        # Matrix corner (cd_4a/cd_25 experimental, cd_24 stub): $E06C stays on
        # the linked fallback; manifest lists $4A/$25 but not $24.
        import sys
        import tempfile
        sys.path[:0] = [str(ROOT / "tools")]
        import build_ipl_loader_g5_2base as b2
        with tempfile.TemporaryDirectory(prefix="pce-24-others-") as td:
            rom, syms = b2.build_ipl_loader_g5_rom(
                self.orig, Path(td), 0x109, "debug", "host-others",
                ["-D", "IPL_PUBLIC"], track=2,
                cd_4a="experimental", cd_25="experimental", cd_24="stub")
            data = rom.read_bytes()
            self.assertNotIn("ex_dotmod_entry", syms)
            at_24 = 3 * 0x24
            self.assertEqual(data[at_24:at_24 + 3], self.normal[at_24:at_24 + 3])
            import json
            man = json.loads((Path(td) / "manifest-host-others-g5.json").read_text())
            self.assertIn("$4A", man["experimental_slots"])
            self.assertIn("$25", man["experimental_slots"])
            self.assertNotIn("$24", man["experimental_slots"])
            self.assertEqual(man["cd_24"], "stub")

    def test_builder_combined_4a_25_24_wiring(self):
        # All three opt-ins together: $E0DE/$E06F/$E06C wired, windows are the
        # 6-window union, manifest lists all three slots.
        import sys
        import tempfile
        sys.path[:0] = [str(ROOT / "tools")]
        import build_ipl_loader_g5_2base as b2
        with tempfile.TemporaryDirectory(prefix="pce-24-both-") as td:
            rom, syms = b2.build_ipl_loader_g5_rom(
                self.orig, Path(td), 0x109, "debug", "host-both",
                ["-D", "IPL_PUBLIC"], track=2,
                cd_4a="experimental", cd_25="experimental",
                cd_24="experimental")
            data = rom.read_bytes()
            for slot, entry in ((0x4A, "ex_memopen_entry"),
                                (0x25, "ex_scrmod_entry"),
                                (0x24, "ex_dotmod_entry")):
                body = syms[entry]
                tab = data[3 * slot:3 * slot + 3]
                self.assertEqual(tuple(tab), (0x4C, body & 0xFF, (body >> 8) & 0xFF))
            import json
            man = json.loads((Path(td) / "manifest-host-both-g5.json").read_text())
            self.assertIn("$4A", man["experimental_slots"])
            self.assertIn("$25", man["experimental_slots"])
            self.assertIn("$24", man["experimental_slots"])

    def test_builder_rejects_bad_cd_24(self):
        import sys
        import tempfile
        sys.path[:0] = [str(ROOT / "tools")]
        import build_ipl_loader_g5_2base as b2
        with tempfile.TemporaryDirectory(prefix="pce-24-bad-") as td:
            with self.assertRaises(ValueError):
                b2.build_ipl_loader_g5_rom(
                    self.orig, Path(td), 0x109, "debug", "host-bad",
                    ["-D", "IPL_PUBLIC"], track=2, cd_24="sometimes")

    def test_redirect_helper_guards(self):
        import sys
        sys.path[:0] = [str(ROOT / "tools")]
        import build_experimental_bios_boot as beb
        bank = bytearray(b"\xff" * 0x2000)
        bank[3 * 0x24:3 * 0x24 + 3] = bytes((0x4C, 0x1D, 0xE2))
        body_at = 0xFA10
        bank[body_at - 0xE000:body_at - 0xE000 + 2] = bytes((0x08, 0x48))
        out = beb._redirect_slot24_to_body(bytes(bank), 0xE21D, body_at)
        self.assertEqual(tuple(out[3 * 0x24:3 * 0x24 + 3]),
                         (0x4C, body_at & 0xFF, (body_at >> 8) & 0xFF))
        bank2 = bytearray(b"\xff" * 0x2000)
        bank2[3 * 0x24:3 * 0x24 + 3] = bytes((0x4C, 0x00, 0x00))
        with self.assertRaises(ValueError):
            beb._redirect_slot24_to_body(bytes(bank2), 0xE21D, body_at)
        with self.assertRaises(ValueError):
            beb._redirect_slot24_to_body(bytes(bank), 0xE21D, 0xE100)
        bank3 = bytearray(bytes(bank))
        assert len(bank3) >= 0x2000
        bank3[body_at - 0xE000:body_at - 0xE000 + 2] = bytes((0x60, 0xEA))
        with self.assertRaises(ValueError):
            beb._redirect_slot24_to_body(bytes(bank3), 0xE21D, body_at)
        with self.assertRaises(ValueError):
            beb._redirect_slot24_to_body(bytes(bank), 0x1000, body_at)

    def test_redirect_span_boundaries(self):
        import sys
        sys.path[:0] = [str(ROOT / "tools")]
        import build_experimental_bios_boot as beb
        self.assertEqual(beb._SLOT24_BODY_LEN, 9)
        self.assertEqual(beb._SLOT24_BODY_LEN, len(B1_BODY))

        def bank_with(stub_addr, body_at, prologue=(0x08, 0x48)):
            bank = bytearray(b"\xff" * 0x2000)
            bank[3 * 0x24:3 * 0x24 + 3] = bytes((0x4C, stub_addr & 0xFF,
                                                  stub_addr >> 8))
            bank[body_at - 0xE000:body_at - 0xE000 + 2] = bytes(prologue)
            return bytes(bank)

        out = beb._redirect_slot24_to_body(bank_with(0xE21D, 0xFFED), 0xE21D, 0xFFED)
        self.assertEqual(tuple(out[3 * 0x24:3 * 0x24 + 3]), (0x4C, 0xED, 0xFF))
        for bad in (0xFFEE, 0xFFF5, 0xFFF6):
            with self.subTest(entry=hex(bad)):
                with self.assertRaises(ValueError):
                    beb._redirect_slot24_to_body(bank_with(0xE21D, bad), 0xE21D, bad)
        with self.assertRaises(ValueError):
            beb._redirect_slot24_to_body(bank_with(0xE21D, 0xFFED, (0x60, 0xEA)),
                                         0xE21D, 0xFFED)
        with self.assertRaises(ValueError):
            beb._redirect_slot24_to_body(bank_with(0xE21D, 0xEFFF), 0xE21D, 0xEFFF)

    def test_windows_superset(self):
        import sys
        sys.path[:0] = [str(ROOT / "tools")]
        import build_experimental_bios_boot as beb
        base = beb.unified_api_windows(0xE10A)
        w24 = beb.unified_api_windows_24(0xE10A)
        w4a = beb.unified_api_windows_4a(0xE10A)
        w25 = beb.unified_api_windows_25(0xE10A)
        w4a25 = beb.unified_api_windows_4a_25(0xE10A)
        wall = beb.unified_api_windows_4a_25_24(0xE10A)
        self.assertEqual(len(base), 5 + 3 + 3)
        self.assertEqual(len(w24), 5 + 3 + 3 + 3)
        self.assertTrue(base < w24)
        self.assertEqual(w24 - base, set(range(3 * 0x24, 3 * 0x24 + 3)))
        self.assertEqual(len(wall), 5 + 3 + 3 + 3 + 3 + 3)
        self.assertTrue(w4a25 < wall)
        self.assertTrue(w24 < wall)
        self.assertEqual(wall - w4a25, set(range(3 * 0x24, 3 * 0x24 + 3)))
        # Default entry point unchanged (three windows only).
        import build_ipl_loader_g5_2base as b2
        self.assertEqual(b2.api_windows_for(0xE10A), base)


if __name__ == "__main__":
    unittest.main()
