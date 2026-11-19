"""Host tests for the $25 EX_SCRMOD B1 experimental wiring (Issue #47).

Synthetic only, no ./rom probing. RED->GREEN: missing source/wiring fails
first; implementation passes after. Real behavior is proved by
tests/integration/test_g5_scrmod_25.py; these host tests pin the builder
contract (stub default preserved, experimental opt-in, fail-closed guards).
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# B1 preservation-only scaffold bytes: PHP PHA PHX PHY PLY PLX PLA PLP RTS.
B1_BODY = bytes((0x08, 0x48, 0xDA, 0x5A, 0x7A, 0xFA, 0x68, 0x28, 0x60))


class BuildScrmod25HostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import sys
        import tempfile
        sys.path[:0] = [str(ROOT / "tools")]
        from unittest.mock import patch
        import build_rom
        cls._tmp = tempfile.TemporaryDirectory(prefix="pce-25-devrom-")
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
        src = (ROOT / "src" / "ex_scrmod.s").read_text(encoding="utf-8")
        self.assertIn(".export ex_scrmod_entry", src)
        self.assertIn('.segment "BOOT"', src)
        self.assertIn("Issue #47", src)
        self.assertIn("docs/47-ex-scrmod-contract.md", src)
        self.assertIn("B1", src)

    def test_builder_default_stub_unchanged(self):
        import sys
        import tempfile
        sys.path[:0] = [str(ROOT / "tools")]
        import build_ipl_loader_g5_2base as b2
        with tempfile.TemporaryDirectory(prefix="pce-25-stub-") as td:
            rom, syms = b2.build_ipl_loader_g5_rom(
                self.orig, Path(td), 0x109, "debug", "host-stub",
                ["-D", "IPL_PUBLIC"], track=2)
            data = rom.read_bytes()
            tab = data[3 * 0x25:3 * 0x25 + 3]
            fb = syms["api_slot_25"]
            self.assertEqual(tuple(tab), (0x4C, fb & 0xFF, (fb >> 8) & 0xFF))
            self.assertEqual(data[3 * 0x25:3 * 0x25 + 3],
                             self.normal[3 * 0x25:3 * 0x25 + 3])
            # Stub default must not link the experimental body.
            self.assertNotIn("ex_scrmod_entry", syms)
            self.assertNotIn(B1_BODY, data,
                             "stub default ROM must not contain the experimental body")
            import json
            man = json.loads((Path(td) / "manifest-host-stub-g5.json").read_text())
            self.assertEqual(man["cd_25"], "stub")
            self.assertNotIn("$25", man["experimental_slots"])
            self.assertNotIn("src/ex_scrmod.s", man["experimental_sources"])

    def test_stub_default_matches_pre_25_baseline(self):
        # Stub-default G5 ROM must be byte-for-byte identical to the pre-$25
        # baseline (no unrelated BOOT or link-order drift). Same pinned
        # baseline as the $4A program (fa694281-era image): the (stub, stub)
        # default links proto/link/glue in the same order, so adding the
        # opt-in $25 path must not move a single byte.
        import sys
        import tempfile
        import hashlib
        sys.path[:0] = [str(ROOT / "tools")]
        import build_ipl_loader_g5_2base as b2
        with tempfile.TemporaryDirectory(prefix="pce-25-byteid-") as td:
            rom, syms = b2.build_ipl_loader_g5_rom(
                self.orig, Path(td), 0x109, "debug", "byteid-stub",
                ["-D", "IPL_PUBLIC"], track=2, cd_4a="stub", cd_25="stub")
            data = rom.read_bytes()
            self.assertEqual(len(data), 262144)
            self.assertNotIn("ex_scrmod_entry", syms)
            pinned = "9a7a2c00bed665a9d90030b824ad3c8e7d7a81c2eec71f824d6e8fb59ce00bc6"
            self.assertEqual(hashlib.sha256(data).hexdigest(), pinned,
                             "stub-default G5 ROM differs from pre-$25 baseline")

    def test_builder_experimental_wiring(self):
        import sys
        import tempfile
        sys.path[:0] = [str(ROOT / "tools")]
        import build_ipl_loader_g5_2base as b2
        with tempfile.TemporaryDirectory(prefix="pce-25-exp-") as td:
            rom, syms = b2.build_ipl_loader_g5_rom(
                self.orig, Path(td), 0x109, "debug", "host-exp",
                ["-D", "IPL_PUBLIC"], track=2, cd_4a="stub", cd_25="experimental")
            data = rom.read_bytes()
            body = syms["ex_scrmod_entry"]
            tab = data[3 * 0x25:3 * 0x25 + 3]
            self.assertEqual(tuple(tab), (0x4C, body & 0xFF, (body >> 8) & 0xFF))
            self.assertGreaterEqual(body, 0xF000)
            self.assertEqual(data[body - 0xE000:body - 0xE000 + 9], B1_BODY)
            self.assertNotEqual(B1_BODY[0], 0x60)  # not a bare RTS
            # Opcode allowlist: the B1 body is exactly the save/restore
            # stream (PHP/PHA/PHX/PHY/PLY/PLX/PLA/PLP/RTS). No TAM (no MPR
            # write), no SEI/CLI, no STA/STZ (no MMIO/RAM write), no TMA
            # (no MPR read: B1 defines no gate), no branches. The allowlist
            # pins the byte shape only; side effects are proved on the real
            # core (T05 RAM-diff, VDC AR invariance), never by this list.
            allowed = {0x08, 0x48, 0xDA, 0x5A, 0x7A, 0xFA, 0x68, 0x28, 0x60}
            for i, b in enumerate(B1_BODY):
                self.assertIn(b, allowed, f"body byte {i}: unexpected opcode")
            import json
            man = json.loads((Path(td) / "manifest-host-exp-g5.json").read_text())
            self.assertEqual(man["cd_25"], "experimental")
            self.assertIn("$25", man["experimental_slots"])
            self.assertIn("src/ex_scrmod.s", man["experimental_sources"])

    def test_builder_4a_only_keeps_25_stub(self):
        # Matrix corner (cd_4a="experimental", cd_25="stub"): $E0DE wired
        # to the $4A body while $E06F stays on the linked fallback (equal
        # to the normal DEV ROM); manifest lists $4A but not $25.
        import sys
        import tempfile
        sys.path[:0] = [str(ROOT / "tools")]
        import build_ipl_loader_g5_2base as b2
        with tempfile.TemporaryDirectory(prefix="pce-25-4aonly-") as td:
            rom, syms = b2.build_ipl_loader_g5_rom(
                self.orig, Path(td), 0x109, "debug", "host-4aonly",
                ["-D", "IPL_PUBLIC"], track=2,
                cd_4a="experimental", cd_25="stub")
            data = rom.read_bytes()
            body4a = syms["ex_memopen_entry"]
            self.assertEqual(tuple(data[3 * 0x4A:3 * 0x4A + 3]),
                             (0x4C, body4a & 0xFF, (body4a >> 8) & 0xFF))
            self.assertNotIn("ex_scrmod_entry", syms)
            at_25 = 3 * 0x25
            self.assertEqual(data[at_25:at_25 + 3], self.normal[at_25:at_25 + 3])
            import json
            man = json.loads((Path(td) / "manifest-host-4aonly-g5.json").read_text())
            self.assertIn("$4A", man["experimental_slots"])
            self.assertNotIn("$25", man["experimental_slots"])
            self.assertEqual(man["cd_25"], "stub")

    def test_builder_combined_4a_25_wiring(self):
        # Both opt-ins together: $E0DE -> $4A body and $E06F -> B1 body,
        # windows are the 5-window union, manifest lists both slots.
        import sys
        import tempfile
        sys.path[:0] = [str(ROOT / "tools")]
        import build_ipl_loader_g5_2base as b2
        with tempfile.TemporaryDirectory(prefix="pce-25-both-") as td:
            rom, syms = b2.build_ipl_loader_g5_rom(
                self.orig, Path(td), 0x109, "debug", "host-both",
                ["-D", "IPL_PUBLIC"], track=2,
                cd_4a="experimental", cd_25="experimental")
            data = rom.read_bytes()
            for slot, entry in ((0x4A, "ex_memopen_entry"),
                                (0x25, "ex_scrmod_entry")):
                body = syms[entry]
                tab = data[3 * slot:3 * slot + 3]
                self.assertEqual(tuple(tab), (0x4C, body & 0xFF, (body >> 8) & 0xFF))
            import json
            man = json.loads((Path(td) / "manifest-host-both-g5.json").read_text())
            self.assertIn("$4A", man["experimental_slots"])
            self.assertIn("$25", man["experimental_slots"])

    def test_builder_rejects_bad_cd_25(self):
        import sys
        import tempfile
        sys.path[:0] = [str(ROOT / "tools")]
        import build_ipl_loader_g5_2base as b2
        with tempfile.TemporaryDirectory(prefix="pce-25-bad-") as td:
            with self.assertRaises(ValueError):
                b2.build_ipl_loader_g5_rom(
                    self.orig, Path(td), 0x109, "debug", "host-bad",
                    ["-D", "IPL_PUBLIC"], track=2, cd_25="sometimes")

    def test_redirect_helper_guards(self):
        import sys
        sys.path[:0] = [str(ROOT / "tools")]
        import build_experimental_bios_boot as beb
        # Well-formed stub-table image redirects (4 KiB bank0 image; the
        # body check reads at the BOOT file offset like the real builder).
        bank = bytearray(b"\xff" * 0x2000)
        bank[3 * 0x25:3 * 0x25 + 3] = bytes((0x4C, 0x1D, 0xE2))
        body_at = 0xFA10
        bank[body_at - 0xE000:body_at - 0xE000 + 2] = bytes((0x08, 0x48))
        out = beb._redirect_slot25_to_body(bytes(bank), 0xE21D, body_at)
        self.assertEqual(tuple(out[3 * 0x25:3 * 0x25 + 3]),
                         (0x4C, body_at & 0xFF, (body_at >> 8) & 0xFF))
        # Wrong table bytes refuse.
        bank2 = bytearray(b"\xff" * 0x2000)
        bank2[3 * 0x25:3 * 0x25 + 3] = bytes((0x4C, 0x00, 0x00))
        with self.assertRaises(ValueError):
            beb._redirect_slot25_to_body(bytes(bank2), 0xE21D, body_at)
        # Body outside BOOT refuses.
        with self.assertRaises(ValueError):
            beb._redirect_slot25_to_body(bytes(bank), 0xE21D, 0xE100)
        # Bad prologue refuses.
        bank3 = bytearray(bytes(bank))
        assert len(bank3) >= 0x2000
        bank3[body_at - 0xE000:body_at - 0xE000 + 2] = bytes((0x60, 0xEA))
        with self.assertRaises(ValueError):
            beb._redirect_slot25_to_body(bytes(bank3), 0xE21D, body_at)
        # Stub address outside BIOS window refuses.
        with self.assertRaises(ValueError):
            beb._redirect_slot25_to_body(bytes(bank), 0x1000, body_at)

    def test_redirect_span_boundaries(self):
        # MEDIUM (review 5976263400): the 9-byte B1 span must sit entirely
        # inside BOOT (end at or before VECTORS $FFF6). Start-only checks
        # would accept $FFF5 with a valid prologue while overlapping vectors.
        import sys
        sys.path[:0] = [str(ROOT / "tools")]
        import build_experimental_bios_boot as beb
        # Body-length pin linkage: the helper span constant must match the
        # reviewed B1 bytes (change together with the structural pin).
        self.assertEqual(beb._SLOT25_BODY_LEN, 9)
        self.assertEqual(beb._SLOT25_BODY_LEN, len(B1_BODY))

        def bank_with(stub_addr, body_at, prologue=(0x08, 0x48)):
            bank = bytearray(b"\xff" * 0x2000)
            bank[3 * 0x25:3 * 0x25 + 3] = bytes((0x4C, stub_addr & 0xFF,
                                                  stub_addr >> 8))
            bank[body_at - 0xE000:body_at - 0xE000 + 2] = bytes(prologue)
            return bytes(bank)

        # $FFED + 9 == $FFF6: last acceptable span.
        out = beb._redirect_slot25_to_body(bank_with(0xE21D, 0xFFED), 0xE21D, 0xFFED)
        self.assertEqual(tuple(out[3 * 0x25:3 * 0x25 + 3]), (0x4C, 0xED, 0xFF))
        # $FFEE + 9 == $FFF7, $FFF5, $FFF6: span crosses VECTORS -> refuse.
        for bad in (0xFFEE, 0xFFF5, 0xFFF6):
            with self.subTest(entry=hex(bad)):
                with self.assertRaises(ValueError):
                    beb._redirect_slot25_to_body(bank_with(0xE21D, bad), 0xE21D, bad)
        # Bad prologue at a span-valid entry still refuses.
        with self.assertRaises(ValueError):
            beb._redirect_slot25_to_body(bank_with(0xE21D, 0xFFED, (0x60, 0xEA)),
                                         0xE21D, 0xFFED)
        # Body below BOOT refuses (span check covers the low edge too).
        with self.assertRaises(ValueError):
            beb._redirect_slot25_to_body(bank_with(0xE21D, 0xE21D), 0xE21D, 0xE100 - 1)

    def test_windows_superset(self):
        import sys
        sys.path[:0] = [str(ROOT / "tools")]
        import build_experimental_bios_boot as beb
        base = beb.unified_api_windows(0xE10A)
        w25 = beb.unified_api_windows_25(0xE10A)
        w4a = beb.unified_api_windows_4a(0xE10A)
        wboth = beb.unified_api_windows_4a_25(0xE10A)
        self.assertEqual(len(base), 5 + 3 + 3)
        self.assertEqual(len(w25), 5 + 3 + 3 + 3)
        self.assertTrue(base < w25)
        self.assertEqual(w25 - base, set(range(3 * 0x25, 3 * 0x25 + 3)))
        self.assertEqual(len(wboth), 5 + 3 + 3 + 3 + 3)
        self.assertTrue(w4a < wboth)
        self.assertTrue(w25 < wboth)
        self.assertEqual(wboth - w4a, set(range(3 * 0x25, 3 * 0x25 + 3)))
        self.assertEqual(wboth - w25, set(range(3 * 0x4A, 3 * 0x4A + 3)))
        # Default entry point unchanged (three windows only).
        import build_ipl_loader_g5_2base as b2
        self.assertEqual(b2.api_windows_for(0xE10A), base)


if __name__ == "__main__":
    unittest.main()
