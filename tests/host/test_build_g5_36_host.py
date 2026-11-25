"""Host tests for the $36 EX_SATCLR B1-analogous experimental wiring (Issue #64).

Synthetic only, no ./rom probing. RED->GREEN: missing source/wiring fails
first; implementation passes after. Real behavior is proved by
tests/integration/test_g5_satclr_36.py; these host tests pin the builder
contract (stub default preserved, experimental opt-in, fail-closed guards).
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# B1-analogous preservation-only scaffold bytes: PHP PHA PHX PHY PLY PLX PLA PLP RTS.
B1_BODY = bytes((0x08, 0x48, 0xDA, 0x5A, 0x7A, 0xFA, 0x68, 0x28, 0x60))


class BuildSatclr36HostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import sys
        import tempfile
        sys.path[:0] = [str(ROOT / "tools")]
        from unittest.mock import patch
        import build_rom
        cls._tmp = tempfile.TemporaryDirectory(prefix="pce-36-devrom-")
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
        src = (ROOT / "src" / "ex_satclr.s").read_text(encoding="utf-8")
        self.assertIn(".export ex_satclr_entry", src)
        self.assertIn('.segment "BOOT"', src)
        self.assertIn("Issue #64", src)
        self.assertIn("docs/64-ex-satclr-contract.md", src)
        self.assertIn("B1-analogous", src)

    def test_builder_default_stub_unchanged(self):
        import sys
        import tempfile
        sys.path[:0] = [str(ROOT / "tools")]
        import build_ipl_loader_g5_2base as b2
        with tempfile.TemporaryDirectory(prefix="pce-36-stub-") as td:
            rom, syms = b2.build_ipl_loader_g5_rom(
                self.orig, Path(td), 0x109, "debug", "host-stub",
                ["-D", "IPL_PUBLIC"], track=2)
            data = rom.read_bytes()
            tab = data[3 * 0x36:3 * 0x36 + 3]
            fb = syms["api_slot_36"]
            self.assertEqual(tuple(tab), (0x4C, fb & 0xFF, (fb >> 8) & 0xFF))
            self.assertEqual(data[3 * 0x36:3 * 0x36 + 3],
                             self.normal[3 * 0x36:3 * 0x36 + 3])
            # Stub default must not link the experimental body.
            self.assertNotIn("ex_satclr_entry", syms)
            self.assertNotIn(B1_BODY, data,
                             "stub default ROM must not contain the experimental body")
            import json
            man = json.loads((Path(td) / "manifest-host-stub-g5.json").read_text())
            self.assertEqual(man["cd_36"], "stub")
            self.assertNotIn("$36", man["experimental_slots"])
            self.assertNotIn("src/ex_satclr.s", man["experimental_sources"])

    def test_stub_default_matches_pre_36_baseline(self):
        # Stub-default G5 ROM must be byte-for-byte identical to the pre-$36
        # baseline (same bytes as the pre-$25 baseline: the (stub, stub, stub, stub)
        # default links proto/link/glue in the same order, so adding the
        # opt-in $36 path must not move a single byte).
        import sys
        import tempfile
        import hashlib
        sys.path[:0] = [str(ROOT / "tools")]
        import build_ipl_loader_g5_2base as b2
        with tempfile.TemporaryDirectory(prefix="pce-36-byteid-") as td:
            rom, syms = b2.build_ipl_loader_g5_rom(
                self.orig, Path(td), 0x109, "debug", "byteid-stub",
                ["-D", "IPL_PUBLIC"], track=2,
                cd_4a="stub", cd_25="stub", cd_36="stub")
            data = rom.read_bytes()
            self.assertEqual(len(data), 262144)
            self.assertNotIn("ex_satclr_entry", syms)
            pinned = "9a7a2c00bed665a9d90030b824ad3c8e7d7a81c2eec71f824d6e8fb59ce00bc6"
            self.assertEqual(hashlib.sha256(data).hexdigest(), pinned,
                             "stub-default G5 ROM differs from pre-$36 baseline")

    def test_builder_experimental_wiring(self):
        import sys
        import tempfile
        sys.path[:0] = [str(ROOT / "tools")]
        import build_ipl_loader_g5_2base as b2
        with tempfile.TemporaryDirectory(prefix="pce-36-exp-") as td:
            rom, syms = b2.build_ipl_loader_g5_rom(
                self.orig, Path(td), 0x109, "debug", "host-exp",
                ["-D", "IPL_PUBLIC"], track=2,
                cd_4a="stub", cd_25="stub", cd_36="experimental")
            data = rom.read_bytes()
            body = syms["ex_satclr_entry"]
            tab = data[3 * 0x36:3 * 0x36 + 3]
            self.assertEqual(tuple(tab), (0x4C, body & 0xFF, (body >> 8) & 0xFF))
            self.assertGreaterEqual(body, 0xF000)
            self.assertEqual(data[body - 0xE000:body - 0xE000 + 9], B1_BODY)
            self.assertNotEqual(B1_BODY[0], 0x60)  # not a bare RTS
            allowed = {0x08, 0x48, 0xDA, 0x5A, 0x7A, 0xFA, 0x68, 0x28, 0x60}
            for i, b in enumerate(B1_BODY):
                self.assertIn(b, allowed, f"body byte {i}: unexpected opcode")
            import json
            man = json.loads((Path(td) / "manifest-host-exp-g5.json").read_text())
            self.assertEqual(man["cd_36"], "experimental")
            self.assertIn("$36", man["experimental_slots"])
            self.assertIn("src/ex_satclr.s", man["experimental_sources"])

    def test_builder_others_only_keep_36_stub(self):
        # Matrix corner (cd_4a/cd_25 experimental, cd_36 stub): $E0A2 stays on
        # the linked fallback; manifest lists $4A/$25 but not $36.
        import sys
        import tempfile
        sys.path[:0] = [str(ROOT / "tools")]
        import build_ipl_loader_g5_2base as b2
        with tempfile.TemporaryDirectory(prefix="pce-36-others-") as td:
            rom, syms = b2.build_ipl_loader_g5_rom(
                self.orig, Path(td), 0x109, "debug", "host-others",
                ["-D", "IPL_PUBLIC"], track=2,
                cd_4a="experimental", cd_25="experimental", cd_36="stub")
            data = rom.read_bytes()
            self.assertNotIn("ex_satclr_entry", syms)
            at_36 = 3 * 0x36
            self.assertEqual(data[at_36:at_36 + 3], self.normal[at_36:at_36 + 3])
            import json
            man = json.loads((Path(td) / "manifest-host-others-g5.json").read_text())
            self.assertIn("$4A", man["experimental_slots"])
            self.assertIn("$25", man["experimental_slots"])
            self.assertNotIn("$36", man["experimental_slots"])
            self.assertEqual(man["cd_36"], "stub")

    def test_builder_combined_4a_25_36_wiring(self):
        # All three opt-ins together: $E0DE/$E06F/$E0A2 wired, windows are the
        # 6-window union, manifest lists all three slots.
        import sys
        import tempfile
        sys.path[:0] = [str(ROOT / "tools")]
        import build_ipl_loader_g5_2base as b2
        with tempfile.TemporaryDirectory(prefix="pce-36-both-") as td:
            rom, syms = b2.build_ipl_loader_g5_rom(
                self.orig, Path(td), 0x109, "debug", "host-both",
                ["-D", "IPL_PUBLIC"], track=2,
                cd_4a="experimental", cd_25="experimental",
                cd_36="experimental")
            data = rom.read_bytes()
            for slot, entry in ((0x4A, "ex_memopen_entry"),
                                (0x25, "ex_scrmod_entry"),
                                (0x36, "ex_satclr_entry")):
                body = syms[entry]
                tab = data[3 * slot:3 * slot + 3]
                self.assertEqual(tuple(tab), (0x4C, body & 0xFF, (body >> 8) & 0xFF))
            import json
            man = json.loads((Path(td) / "manifest-host-both-g5.json").read_text())
            self.assertIn("$4A", man["experimental_slots"])
            self.assertIn("$25", man["experimental_slots"])
            self.assertIn("$36", man["experimental_slots"])

    def test_builder_rejects_bad_cd_36(self):
        import sys
        import tempfile
        sys.path[:0] = [str(ROOT / "tools")]
        import build_ipl_loader_g5_2base as b2
        with tempfile.TemporaryDirectory(prefix="pce-36-bad-") as td:
            with self.assertRaises(ValueError):
                b2.build_ipl_loader_g5_rom(
                    self.orig, Path(td), 0x109, "debug", "host-bad",
                    ["-D", "IPL_PUBLIC"], track=2, cd_36="sometimes")

    def test_redirect_helper_guards(self):
        import sys
        sys.path[:0] = [str(ROOT / "tools")]
        import build_experimental_bios_boot as beb
        bank = bytearray(b"\xff" * 0x2000)
        bank[3 * 0x36:3 * 0x36 + 3] = bytes((0x4C, 0x1D, 0xE2))
        body_at = 0xFA10
        bank[body_at - 0xE000:body_at - 0xE000 + 2] = bytes((0x08, 0x48))
        out = beb._redirect_slot36_to_body(bytes(bank), 0xE21D, body_at)
        self.assertEqual(tuple(out[3 * 0x36:3 * 0x36 + 3]),
                         (0x4C, body_at & 0xFF, (body_at >> 8) & 0xFF))
        bank2 = bytearray(b"\xff" * 0x2000)
        bank2[3 * 0x36:3 * 0x36 + 3] = bytes((0x4C, 0x00, 0x00))
        with self.assertRaises(ValueError):
            beb._redirect_slot36_to_body(bytes(bank2), 0xE21D, body_at)
        with self.assertRaises(ValueError):
            beb._redirect_slot36_to_body(bytes(bank), 0xE21D, 0xE100)
        bank3 = bytearray(bytes(bank))
        assert len(bank3) >= 0x2000
        bank3[body_at - 0xE000:body_at - 0xE000 + 2] = bytes((0x60, 0xEA))
        with self.assertRaises(ValueError):
            beb._redirect_slot36_to_body(bytes(bank3), 0xE21D, body_at)
        with self.assertRaises(ValueError):
            beb._redirect_slot36_to_body(bytes(bank), 0x1000, body_at)

    def test_redirect_span_boundaries(self):
        import sys
        sys.path[:0] = [str(ROOT / "tools")]
        import build_experimental_bios_boot as beb
        self.assertEqual(beb._SLOT36_BODY_LEN, 9)
        self.assertEqual(beb._SLOT36_BODY_LEN, len(B1_BODY))

        def bank_with(stub_addr, body_at, prologue=(0x08, 0x48)):
            bank = bytearray(b"\xff" * 0x2000)
            bank[3 * 0x36:3 * 0x36 + 3] = bytes((0x4C, stub_addr & 0xFF,
                                                  stub_addr >> 8))
            bank[body_at - 0xE000:body_at - 0xE000 + 2] = bytes(prologue)
            return bytes(bank)

        out = beb._redirect_slot36_to_body(bank_with(0xE21D, 0xFFED), 0xE21D, 0xFFED)
        self.assertEqual(tuple(out[3 * 0x36:3 * 0x36 + 3]), (0x4C, 0xED, 0xFF))
        for bad in (0xFFEE, 0xFFF5, 0xFFF6):
            with self.subTest(entry=hex(bad)):
                with self.assertRaises(ValueError):
                    beb._redirect_slot36_to_body(bank_with(0xE21D, bad), 0xE21D, bad)
        with self.assertRaises(ValueError):
            beb._redirect_slot36_to_body(bank_with(0xE21D, 0xFFED, (0x60, 0xEA)),
                                         0xE21D, 0xFFED)
        with self.assertRaises(ValueError):
            beb._redirect_slot36_to_body(bank_with(0xE21D, 0xEFFF), 0xE21D, 0xEFFF)

    def test_windows_superset(self):
        import sys
        sys.path[:0] = [str(ROOT / "tools")]
        import build_experimental_bios_boot as beb
        base = beb.unified_api_windows(0xE10A)
        w36 = beb.unified_api_windows_36(0xE10A)
        w4a = beb.unified_api_windows_4a(0xE10A)
        w25 = beb.unified_api_windows_25(0xE10A)
        w4a25 = beb.unified_api_windows_4a_25(0xE10A)
        wall = beb.unified_api_windows_4a_25_36(0xE10A)
        self.assertEqual(len(base), 5 + 3 + 3)
        self.assertEqual(len(w36), 5 + 3 + 3 + 3)
        self.assertTrue(base < w36)
        self.assertEqual(w36 - base, set(range(3 * 0x36, 3 * 0x36 + 3)))
        self.assertEqual(len(wall), 5 + 3 + 3 + 3 + 3 + 3)
        self.assertTrue(w4a25 < wall)
        self.assertTrue(w36 < wall)
        self.assertEqual(wall - w4a25, set(range(3 * 0x36, 3 * 0x36 + 3)))
        # Default entry point unchanged (three windows only).
        import build_ipl_loader_g5_2base as b2
        self.assertEqual(b2.api_windows_for(0xE10A), base)

    def test_all_16_variants_persistent_regression(self):
        # Permanent 4-opt-in (cd_4a/cd_25/cd_24/cd_36) x stub/experimental
        # matrix: replaces one-off local runs with automated regression.
        # Bodies $25/$24/$36 share the B1 opcode row, so an unselected body
        # is NEVER proven absent by scanning ROM bytes: symbol presence,
        # routing, link set, and manifest distinguish presence from
        # coincidental opcode matches.
        import sys
        import tempfile
        import hashlib
        import itertools
        import json
        import struct
        sys.path[:0] = [str(ROOT / "tools")]
        import build_ipl_loader_g5_2base as b2
        normal = bytes(self.normal)
        slots = ((0x4A, "cd_4a", "$4A", "ex_memopen_entry", "src/ex_memopen.s", None),
                 (0x25, "cd_25", "$25", "ex_scrmod_entry", "src/ex_scrmod.s", B1_BODY),
                 (0x24, "cd_24", "$24", "ex_dotmod_entry", "src/ex_dotmod.s", B1_BODY),
                 (0x36, "cd_36", "$36", "ex_satclr_entry", "src/ex_satclr.s", B1_BODY))
        names, shas, base_sources, default_sha = set(), set(), None, None
        for combo in itertools.product(["stub", "experimental"], repeat=4):
            cfg = dict(zip(("cd_4a", "cd_25", "cd_24", "cd_36"), combo))
            variant = "m-" + "".join("e" if v == "experimental" else "s" for v in combo)
            with tempfile.TemporaryDirectory(prefix="pce-36-matrix-") as td:
                td = Path(td)
                rom, syms = b2.build_ipl_loader_g5_rom(
                    self.orig, td, 0x109, "debug", variant,
                    ["-D", "IPL_PUBLIC"], track=2, **cfg)
                data = rom.read_bytes()
                names.add(rom.name)
                shas.add(hashlib.sha256(data).hexdigest())
                with self.subTest(combo=combo):
                    self.assertEqual(len(data), 262144)
                    self.assertEqual(self.orig.read_bytes(), normal)
                    man = json.loads((td / f"manifest-{variant}-g5.json").read_text())
                    if base_sources is None:
                        base_sources = list(man["experimental_sources"])
                    for _, opt, _, _, _, _ in slots:
                        self.assertEqual(man[opt], cfg[opt])
                    self.assertEqual(man["experimental_slots"],
                                     ["$02", "$03", "$33"] + [tag for (_, _, tag, _, _, _), v in zip(slots, combo) if v == "experimental"])
                    self.assertEqual(set(man["experimental_sources"]),
                                     set(base_sources) | {src for (_, _, _, _, src, _), v in zip(slots, combo) if v == "experimental"})
                    self.assertFalse(man["release_eligible"])
                    self.assertFalse(man["dev_callable"])
                    for (slot, _, _, entry, src, pin), val in zip(slots, combo):
                        at = 3 * slot
                        if val == "experimental":
                            body = syms[entry]
                            self.assertEqual(tuple(data[at:at + 3]), (0x4C, body & 0xFF, (body >> 8) & 0xFF))
                            self.assertGreaterEqual(body, 0xF000)
                            self.assertLessEqual(body, 0xFFF6 - 9)
                            if pin is not None:
                                self.assertEqual(data[body - 0xE000:body - 0xE000 + 9], pin)
                        else:
                            self.assertNotIn(entry, syms)
                            self.assertEqual(data[at:at + 3], normal[at:at + 3])
                            self.assertNotIn(src, man["experimental_sources"])
                    # API-area discipline: the $02 fallback sequence becomes
                    # JMP proto+NOPs; $33/$03 tables become JMP to the linked
                    # bodies; selected tables become JMP to their bodies;
                    # unselected tables stay byte-identical to normal.
                    # Shared-opcode fact: every table entry is JMP (0x4C)
                    # before and after, so byte-level diffs never contain a
                    # table base offset itself. Windows are therefore pinned
                    # as exact entry values, with the byte diff pinned as
                    # subset plus per-entry target-byte presence.
                    slot02 = syms["api_slot_02"]
                    self.assertEqual(bytes(data[slot02 - 0xE000:slot02 - 0xE000 + 5]),
                                     bytes((0x4C, syms["cd_base_2base_entry"] & 0xFF,
                                            (syms["cd_base_2base_entry"] >> 8) & 0xFF, 0xEA, 0xEA)))
                    for slot, link in ((0x33, "experimental_slot_33"),
                                       (0x03, "cd_read_commercial_entry")):
                        self.assertEqual(tuple(data[3 * slot:3 * slot + 3]),
                                         (0x4C, syms[link] & 0xFF, (syms[link] >> 8) & 0xFF))
                    windows = {slot02 - 0xE000 + i for i in range(5)}
                    windows |= set(range(3 * 0x33, 3 * 0x33 + 3))
                    windows |= set(range(3 * 0x03, 3 * 0x03 + 3))
                    for (slot, _, _, _, _, _), val in zip(slots, combo):
                        if val == "experimental":
                            windows |= set(range(3 * slot, 3 * slot + 3))
                    self.assertEqual(len(windows), 11 + 3 * sum(v == "experimental" for v in combo))
                    diff = {i for i in range(0x1000) if data[i] != normal[i]}
                    self.assertLessEqual(diff, windows)
                    must_change = [slot02 - 0xE000 + i for i in range(5)]
                    must_change += [3 * s + 1 for s in (0x33, 0x03)]
                    must_change += [3 * s + 2 for s in (0x33, 0x03)]
                    for (slot, _, _, _, _, _), val in zip(slots, combo):
                        if val == "experimental":
                            must_change += [3 * slot + 1, 3 * slot + 2]
                    for at in must_change:
                        self.assertIn(at, diff)
                    # Vector area: this build's irq_stub x4 + reset (SEI entry).
                    vec = [struct.unpack("<H", data[o:o + 2])[0] for o in range(0x1FF6, 0x2000, 2)]
                    self.assertEqual(vec[:4], [syms["irq_stub"]] * 4)
                    self.assertEqual(vec[4], syms["reset"])
                    self.assertEqual(data[(syms["reset"] & 0xFFFF) - 0xE000], 0x78)
                    if all(v == "stub" for v in combo):
                        default_sha = hashlib.sha256(data).hexdigest()
        self.assertEqual(len(names), 16)
        self.assertEqual(len(shas), 16)
        self.assertEqual(default_sha,
                         "9a7a2c00bed665a9d90030b824ad3c8e7d7a81c2eec71f824d6e8fb59ce00bc6",
                         "all-stub default differs from pinned baseline")


if __name__ == "__main__":
    unittest.main()
