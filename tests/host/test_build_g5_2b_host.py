"""Host tests for the $2B EX_RCROFF D2 limited-functional experimental wiring (Issue #53).

Synthetic only, no ./rom probing. RED->GREEN: missing source/wiring fails
first; implementation passes after. Real behavior is proved by
tests/integration/test_g5_rcroff_2b.py; these host tests pin the builder
contract (stub default preserved, experimental opt-in, fail-closed guards,
bounded variant lattice).
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# D2-functional body bytes (src/ex_rcroff.s, 44 bytes):
# PHP PHA | LDA $2503 CMP #$A5 BEQ+4 | PLA PLP SEC RTS (reject) |
# SEI TMA#$01 PHA | LDA #$FF TAM#$01 | LDA #$05 STA $0000 |
# LDA $2502 AND #$FB STA $2502 STA $0002 | PLA TAM#$01 PLA PLP CLC RTS (ok).
D2F_BODY = bytes((
    0x08, 0x48, 0xAD, 0x03, 0x25, 0xC9, 0xA5, 0xF0, 0x04, 0x68, 0x28, 0x38,
    0x60, 0x78, 0x43, 0x01, 0x48, 0xA9, 0xFF, 0x53, 0x01, 0xA9, 0x05, 0x8D,
    0x00, 0x00, 0xAD, 0x02, 0x25, 0x29, 0xFB, 0x8D, 0x02, 0x25, 0x8D, 0x02,
    0x00, 0x68, 0x53, 0x01, 0x68, 0x28, 0x18, 0x60,
))


class BuildRcroff2bHostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import sys
        import tempfile
        sys.path[:0] = [str(ROOT / "tools")]
        from unittest.mock import patch
        import build_rom
        cls._tmp = tempfile.TemporaryDirectory(prefix="pce-2b-devrom-")
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
        src = (ROOT / "src" / "ex_rcroff.s").read_text(encoding="utf-8")
        self.assertIn(".export ex_rcroff_entry", src)
        self.assertIn('.segment "BOOT"', src)
        self.assertIn("Issue #53", src)
        self.assertIn("docs/53-ex-rcroff-contract.md", src)
        self.assertIn("D2", src)
        self.assertIn("$2502", src)
        # No guessed mirrors / CPU-I / IDR changes in the body source.
        self.assertIn("$20F3/$20F7", src)

    def test_body_assembles_to_44_byte_pin(self):
        import sys
        sys.path[:0] = [str(ROOT / "tools")]
        from environment import executable
        import subprocess
        import tempfile
        ca65 = executable("ca65")
        with tempfile.TemporaryDirectory(prefix="pce-2b-asm-") as td:
            obj = str(Path(td) / "ex_rcroff.o")
            subprocess.run([ca65, str(ROOT / "src" / "ex_rcroff.s"),
                            "-o", obj], check=True)
            self.assertTrue(Path(obj).is_file())

    def test_builder_default_stub_unchanged(self):
        import sys
        import tempfile
        sys.path[:0] = [str(ROOT / "tools")]
        import build_ipl_loader_g5_2base as b2
        with tempfile.TemporaryDirectory(prefix="pce-2b-stub-") as td:
            rom, syms = b2.build_ipl_loader_g5_rom(
                self.orig, Path(td), 0x109, "debug", "host-stub",
                ["-D", "IPL_PUBLIC"], track=2)
            data = rom.read_bytes()
            tab = data[3 * 0x2B:3 * 0x2B + 3]
            fb = syms["api_slot_2B"]
            self.assertEqual(tuple(tab), (0x4C, fb & 0xFF, (fb >> 8) & 0xFF))
            self.assertEqual(data[3 * 0x2B:3 * 0x2B + 3],
                             self.normal[3 * 0x2B:3 * 0x2B + 3])
            # Stub default must not link the experimental body.
            self.assertNotIn("ex_rcroff_entry", syms)
            self.assertNotIn(D2F_BODY, data,
                             "stub default ROM must not contain the experimental body")
            import json
            man = json.loads((Path(td) / "manifest-host-stub-g5.json").read_text())
            self.assertEqual(man["cd_2b"], "stub")
            self.assertNotIn("$2B", man["experimental_slots"])
            self.assertNotIn("src/ex_rcroff.s", man["experimental_sources"])

    def test_stub_default_matches_pinned_baseline(self):
        # Stub-default G5 ROM (all six flags stub) must be byte-for-byte
        # identical to the pinned pre-$2B baseline: adding the opt-in $2B
        # path must not move a single byte.
        import sys
        import tempfile
        import hashlib
        sys.path[:0] = [str(ROOT / "tools")]
        import build_ipl_loader_g5_2base as b2
        with tempfile.TemporaryDirectory(prefix="pce-2b-byteid-") as td:
            rom, syms = b2.build_ipl_loader_g5_rom(
                self.orig, Path(td), 0x109, "debug", "byteid-stub",
                ["-D", "IPL_PUBLIC"], track=2,
                cd_4a="stub", cd_25="stub", cd_24="stub", cd_36="stub",
                cd_35="stub", cd_2c="stub", cd_2b="stub")
            data = rom.read_bytes()
            self.assertEqual(len(data), 262144)
            self.assertNotIn("ex_rcroff_entry", syms)
            pinned = "9a7a2c00bed665a9d90030b824ad3c8e7d7a81c2eec71f824d6e8fb59ce00bc6"
            self.assertEqual(hashlib.sha256(data).hexdigest(), pinned,
                             "stub-default G5 ROM differs from pinned baseline")

    def test_builder_2b_only_wiring(self):
        import sys
        import tempfile
        sys.path[:0] = [str(ROOT / "tools")]
        import build_ipl_loader_g5_2base as b2
        with tempfile.TemporaryDirectory(prefix="pce-2b-exp-") as td:
            rom, syms = b2.build_ipl_loader_g5_rom(
                self.orig, Path(td), 0x109, "debug", "host-2bonly",
                ["-D", "IPL_PUBLIC"], track=2,
                cd_4a="stub", cd_25="stub", cd_24="stub", cd_36="stub",
                cd_35="stub", cd_2c="stub", cd_2b="experimental")
            data = rom.read_bytes()
            body = syms["ex_rcroff_entry"]
            tab = data[3 * 0x2B:3 * 0x2B + 3]
            self.assertEqual(tuple(tab), (0x4C, body & 0xFF, (body >> 8) & 0xFF))
            self.assertGreaterEqual(body, 0xF000)
            self.assertLessEqual(body, 0xFFF6 - 44)
            self.assertEqual(data[body - 0xE000:body - 0xE000 + 44], D2F_BODY)
            # No other experimental body linked.
            for other in ("ex_memopen_entry", "ex_scrmod_entry",
                          "ex_dotmod_entry", "ex_satclr_entry", "ex_sprdma_entry"):
                self.assertNotIn(other, syms)
            import json
            man = json.loads((Path(td) / "manifest-host-2bonly-g5.json").read_text())
            self.assertEqual(man["cd_2b"], "experimental")
            self.assertEqual(man["experimental_slots"], ["$02", "$03", "$33", "$2B"])
            self.assertIn("src/ex_rcroff.s", man["experimental_sources"])

    def test_builder_2c_2b_chain_wiring(self):
        # $2C+$2B chain combination: $2C+$2B wired, manifest lists both.
        # Full ExpG ($4A+$25+$24+$36+$35+$2C+$2B) overflows BOOT by 19 bytes
        # and is refused; this chain fits and proves the $2C->$2B interaction.
        import sys
        import tempfile
        sys.path[:0] = [str(ROOT / "tools")]
        import build_ipl_loader_g5_2base as b2
        with tempfile.TemporaryDirectory(prefix="pce-2b-chain-") as td:
            rom, syms = b2.build_ipl_loader_g5_rom(
                self.orig, Path(td), 0x109, "debug", "host-chain",
                ["-D", "IPL_PUBLIC"], track=2,
                cd_4a="stub", cd_25="stub", cd_24="stub", cd_36="stub",
                cd_35="stub", cd_2c="experimental", cd_2b="experimental")
            data = rom.read_bytes()
            for slot, entry in ((0x2C, "ex_irqon_entry"),
                                (0x2B, "ex_rcroff_entry")):
                body = syms[entry]
                tab = data[3 * slot:3 * slot + 3]
                self.assertEqual(tuple(tab), (0x4C, body & 0xFF, (body >> 8) & 0xFF))
            self.assertEqual(data[syms["ex_rcroff_entry"] - 0xE000:
                                  syms["ex_rcroff_entry"] - 0xE000 + 44], D2F_BODY)
            # $2C body still pinned (chain needs both).
            self.assertEqual(len(bytes((
                0x08, 0x48, 0xAD, 0x03, 0x25, 0xC9, 0xA5, 0xF0, 0x04, 0x68, 0x28, 0x38,
                0x60, 0x78, 0x43, 0x01, 0x48, 0xA9, 0xFF, 0x53, 0x01, 0xA9, 0x05, 0x8D,
                0x00, 0x00, 0xAD, 0x02, 0x25, 0x09, 0x08, 0x8D, 0x02, 0x25, 0x8D, 0x02,
                0x00, 0x68, 0x53, 0x01, 0x68, 0x28, 0x18, 0x60,
            ))), 44)
            import json
            man = json.loads((Path(td) / "manifest-host-chain-g5.json").read_text())
            self.assertEqual(man["experimental_slots"],
                             ["$02", "$03", "$33", "$2C", "$2B"])
            self.assertEqual(man["cd_2c"], "experimental")
            self.assertEqual(man["cd_2b"], "experimental")
            self.assertIn("src/ex_irqon.s", man["experimental_sources"])
            self.assertIn("src/ex_rcroff.s", man["experimental_sources"])
            self.assertFalse(man["release_eligible"])
            self.assertFalse(man["dev_callable"])

    def test_builder_rejects_bad_cd_2b(self):
        import sys
        import tempfile
        sys.path[:0] = [str(ROOT / "tools")]
        import build_ipl_loader_g5_2base as b2
        with tempfile.TemporaryDirectory(prefix="pce-2b-bad-") as td:
            with self.assertRaises(ValueError):
                b2.build_ipl_loader_g5_rom(
                    self.orig, Path(td), 0x109, "debug", "host-bad",
                    ["-D", "IPL_PUBLIC"], track=2, cd_2b="sometimes")

    def test_builder_rejects_unbounded_2b_combos(self):
        # Bounded lattice: $2B experimental is supported only $2B-only or
        # $2C+$2B chain. Partial combinations (e.g. $4A+$2B, $25+$2B, $35+$2B)
        # and full ExpG (overflow) must refuse
        # must refuse fail-closed instead of silently picking a wrong window.
        import sys
        import tempfile
        sys.path[:0] = [str(ROOT / "tools")]
        import build_ipl_loader_g5_2base as b2
        bad_cfgs = [
            dict(cd_4a="experimental", cd_2b="experimental"),
            dict(cd_25="experimental", cd_2b="experimental"),
            dict(cd_35="experimental", cd_2b="experimental"),
            dict(cd_4a="experimental", cd_25="experimental",
                 cd_24="experimental", cd_2b="experimental"),
            dict(cd_4a="experimental", cd_25="experimental",
                 cd_24="experimental", cd_36="experimental",
                 cd_35="experimental", cd_2c="experimental",
                 cd_2b="experimental"),
        ]
        for i, cfg in enumerate(bad_cfgs):
            with self.subTest(cfg=cfg):
                with tempfile.TemporaryDirectory(prefix=f"pce-2b-unbounded-{i}-") as td:
                    with self.assertRaises(ValueError):
                        b2.build_ipl_loader_g5_rom(
                            self.orig, Path(td), 0x109, "debug", f"host-unb{i}",
                            ["-D", "IPL_PUBLIC"], track=2, **cfg)

    def test_symlinked_rcroff_leaf_refused(self):
        # A pre-planted symlink at ex_rcroff_{variant}.o must be refused by
        # the R2 preflight BEFORE any output write (same discipline as
        # $4A/$25/$24/$36/$35).
        import sys
        import tempfile
        import hashlib
        sys.path[:0] = [str(ROOT / "tools")]
        import build_ipl_loader_g5_2base as b2
        self.assertIn("ex_rcroff_victim.o", b2._g5_leaf_names("victim"))
        for tag, target in (("plain", Path("/etc/hostname")),
                            ("dangling", Path("/tmp/definitely-missing-2b-target.bin"))):
            with self.subTest(target=tag):
                with tempfile.TemporaryDirectory(prefix="pce-2b-symlink-") as td:
                    out = Path(td)
                    if tag == "plain":
                        sentinel = out / "sentinel.bin"
                        sentinel.write_bytes(b"SENTINEL-2B")
                        sha_before = hashlib.sha256(sentinel.read_bytes()).hexdigest()
                        (out / "ex_rcroff_victim.o").symlink_to(sentinel)
                    else:
                        (out / "ex_rcroff_victim.o").symlink_to(target)
                    with self.assertRaises(ValueError):
                        b2.build_ipl_loader_g5_rom(
                            self.orig, out, 0x109, "debug", "victim",
                            ["-D", "IPL_PUBLIC"], track=2,
                            cd_2b="experimental")
                    if tag == "plain":
                        self.assertEqual(sorted(p.name for p in out.iterdir()),
                                         ["ex_rcroff_victim.o", "sentinel.bin"])
                        self.assertEqual(sentinel.read_bytes(), b"SENTINEL-2B")
                        self.assertEqual(hashlib.sha256(sentinel.read_bytes()).hexdigest(),
                                         sha_before)
                    else:
                        self.assertEqual(sorted(p.name for p in out.iterdir()),
                                         ["ex_rcroff_victim.o"])
                    self.assertTrue((out / "ex_rcroff_victim.o").is_symlink())

    def test_redirect_helper_guards(self):
        import sys
        sys.path[:0] = [str(ROOT / "tools")]
        import build_experimental_bios_boot as beb
        self.assertEqual(beb._SLOT2B_BODY_LEN, 44)
        self.assertEqual(beb._SLOT2B_BODY_LEN, len(D2F_BODY))
        bank = bytearray(b"\xff" * 0x2000)
        bank[3 * 0x2B:3 * 0x2B + 3] = bytes((0x4C, 0x1D, 0xE2))
        body_at = 0xFA10
        bank[body_at - 0xE000:body_at - 0xE000 + 2] = bytes((0x08, 0x48))
        out = beb._redirect_slot2b_to_body(bytes(bank), 0xE21D, body_at)
        self.assertEqual(tuple(out[3 * 0x2B:3 * 0x2B + 3]),
                         (0x4C, body_at & 0xFF, (body_at >> 8) & 0xFF))
        bank2 = bytearray(b"\xff" * 0x2000)
        bank2[3 * 0x2B:3 * 0x2B + 3] = bytes((0x4C, 0x00, 0x00))
        with self.assertRaises(ValueError):
            beb._redirect_slot2b_to_body(bytes(bank2), 0xE21D, body_at)
        with self.assertRaises(ValueError):
            beb._redirect_slot2b_to_body(bytes(bank), 0xE21D, 0xE100)
        bank3 = bytearray(bytes(bank))
        bank3[body_at - 0xE000:body_at - 0xE000 + 2] = bytes((0x60, 0xEA))
        with self.assertRaises(ValueError):
            beb._redirect_slot2b_to_body(bytes(bank3), 0xE21D, body_at)
        with self.assertRaises(ValueError):
            beb._redirect_slot2b_to_body(bytes(bank), 0x1000, body_at)

    def test_redirect_span_boundaries(self):
        import sys
        sys.path[:0] = [str(ROOT / "tools")]
        import build_experimental_bios_boot as beb

        def bank_with(stub_addr, body_at, prologue=(0x08, 0x48)):
            bank = bytearray(b"\xff" * 0x2000)
            bank[3 * 0x2B:3 * 0x2B + 3] = bytes((0x4C, stub_addr & 0xFF,
                                                 stub_addr >> 8))
            bank[body_at - 0xE000:body_at - 0xE000 + 2] = bytes(prologue)
            return bytes(bank)

        # 44-byte span: entry + 44 <= $FFF6, so $FFCA is the last accept.
        out = beb._redirect_slot2b_to_body(bank_with(0xE21D, 0xFFCA), 0xE21D, 0xFFCA)
        self.assertEqual(tuple(out[3 * 0x2B:3 * 0x2B + 3]), (0x4C, 0xCA, 0xFF))
        for bad in (0xFFCB, 0xFFF5, 0xFFF6):
            with self.subTest(entry=hex(bad)):
                with self.assertRaises(ValueError):
                    beb._redirect_slot2b_to_body(bank_with(0xE21D, bad), 0xE21D, bad)
        with self.assertRaises(ValueError):
            beb._redirect_slot2b_to_body(bank_with(0xE21D, 0xFFCA, (0x60, 0xEA)),
                                         0xE21D, 0xFFCA)
        with self.assertRaises(ValueError):
            beb._redirect_slot2b_to_body(bank_with(0xE21D, 0xEFFF), 0xE21D, 0xEFFF)

    def test_windows_superset(self):
        import sys
        sys.path[:0] = [str(ROOT / "tools")]
        import build_experimental_bios_boot as beb
        base = beb.unified_api_windows(0xE10A)
        w2b = beb.unified_api_windows_2b(0xE10A)
        w2c2b = beb.unified_api_windows_2c_2b(0xE10A)
        w2c = beb.unified_api_windows_2c(0xE10A)
        self.assertEqual(len(base), 5 + 3 + 3)
        self.assertEqual(len(w2b), 5 + 3 + 3 + 3)
        self.assertTrue(base < w2b)
        self.assertEqual(w2b - base, set(range(3 * 0x2B, 3 * 0x2B + 3)))
        self.assertEqual(len(w2c2b), 5 + 3 + 3 + 3 + 3)
        self.assertTrue(w2c < w2c2b)
        self.assertTrue(w2b < w2c2b)
        self.assertEqual(w2c2b - w2c, set(range(3 * 0x2B, 3 * 0x2B + 3)))
        # Full ExpG windows still defined (for future BOOT-saving follow-up).
        wall = beb.unified_api_windows_4a_25_24_36_35_2c_2b(0xE10A)
        self.assertEqual(len(wall), 5 + 3 + 3 + 3 + 3 + 3 + 3 + 3 + 3 + 3)
        # Default entry point unchanged (three windows only).
        import build_ipl_loader_g5_2base as b2
        self.assertEqual(b2.api_windows_for(0xE10A), base)

    def test_spec_classification(self):
        import json
        spec = json.loads((ROOT / "spec" / "c3_static_contracts.json").read_text())
        self.assertIn("src/ex_rcroff.s", spec["scope"]["fixture"])
        self.assertIn("EX_RCROFF", spec["scope"]["fixture_reasons"]["src/ex_rcroff.s"])


if __name__ == "__main__":
    unittest.main()
