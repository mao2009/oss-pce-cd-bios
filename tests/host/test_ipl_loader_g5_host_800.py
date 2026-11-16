"""Issue #800 host gates: PUBLIC loader + experimental $02/$03/$33 (commercial IPL path).

Parent #635. Synthetic only, no ./rom probing, no commercial media.
NOT a compatible System Card BIOS, NOT an ABI claim, release_eligible=False.

Part A (documents the boot-path differential, passes on current main):
  - PUBLIC loader (build_dev_ipl_loader_bios, IPL_PUBLIC) carries the normal
    18 DEV overrides (including $21/$23) and leaves $02/$03/$33 as
    nonreturning fallback stubs.
  - G5 DEV profile (build_dev_g5, PCEX path) wires $02/$03/$33 to real
    experimental bodies but boots only single-sector PCEX via XSEL selection.
  - The commercial IPL path needs the PUBLIC multi-sector INFO/BODY loader
    (IPL_SEL 16-bit, IPLSTA/IPLJMP, MPR2..6 mapping) AND the experimental
    bodies. The contract mismatches (XSEL vs IPL_SEL, link window/state
    seeding, dspoff shadow seeding, MPR/RAM init) are fail-closed BLOCKED
    until measured.

Part B (new builder, RED before tools/build_ipl_loader_g5.py exists):
  - PUBLIC loader + $02/$03/$33 real wiring in one image, reusing the
    existing patch helpers (no large new builder, no unconditional RTS).
  - Normal DEV ROM byte-identical, API-area diff exactly three windows,
    $4A stays a stub (SIMULATED control separate), fail-closed negatives.
"""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from environment import requires_cc65  # noqa: E402
from build_dev_ipl_loader_bios import build_ipl_loader_bios  # noqa: E402
from build_rom import IMAGE_BYTES, build, overrides, verify_dev_rom  # noqa: E402


ORIGINAL = ROOT / "build" / "debug" / "bios-dev" / "dev-only-not-compatible-syscard3.pce"
SEL = 0x0109  # synthetic INDEX 01 used by geargrafx_ipl_loader (265)


def _read_table_target(data: bytes, slot: int) -> int:
    at = 3 * slot
    assert data[at] == 0x4C, f"slot ${slot:02X} entry is not JMP"
    return data[at + 1] | (data[at + 2] << 8)


@requires_cc65
class BootPathDifferentialTest(unittest.TestCase):
    """Part A: PUBLIC vs G5-PCEX boot-path contract differences (existing code)."""

    @classmethod
    def setUpClass(cls):
        if not ORIGINAL.is_file():
            raise unittest.SkipTest("needs make dev-rom output (CI build step)")
        tmp = tempfile.TemporaryDirectory(prefix="pce-800-diff-")
        cls.addClassCleanup(tmp.cleanup)
        cls.out = Path(tmp.name)
        cls.base = build(cls.out / "base-check", root=ROOT)
        cls.normal = cls.base.read_bytes()
        rom, syms = build_ipl_loader_bios(
            ORIGINAL, cls.out / "public-probe", SEL, "debug", "probe800",
            ["-D", "IPL_PUBLIC"])
        cls.public_rom = rom.read_bytes()
        cls.public_syms = syms
        cls.public_path = rom

    def test_normal_dev_has_18_overrides_including_21_23(self):
        ov = overrides(ROOT)
        self.assertEqual(len(ov), 18)
        for slot in (0x21, 0x23):
            self.assertIn(slot, ov, f"EX slot ${slot:02X} must be in normal DEV 18")

    def test_public_loader_keeps_18_overrides(self):
        # PUBLIC loader reuses assemble_api_objects: $21/$23 table targets
        # must equal the normal DEV ROM's (real DEV APIs callable on IPL path).
        for slot in sorted(overrides(ROOT)):
            with self.subTest(slot=hex(slot)):
                self.assertEqual(_read_table_target(self.public_rom, slot),
                                 _read_table_target(self.normal, slot))

    def test_public_loader_leaves_02_03_33_as_fallback_stubs(self):
        # Pre-integration baseline: $02/$03/$33 are NOT wired to real bodies.
        # Each table entry points at its own LDX #$slot fallback (5 bytes).
        for slot in (0x02, 0x03, 0x33):
            with self.subTest(slot=hex(slot)):
                target = _read_table_target(self.public_rom, slot)
                self.assertGreaterEqual(target, 0xE100)
                off = target - 0xE000
                self.assertEqual(self.public_rom[off:off + 2], bytes((0xA2, slot)))
                # And the normal DEV ROM agrees (same stub layout).
                ntarget = _read_table_target(self.normal, slot)
                self.assertEqual(self.public_rom[off:off + 5],
                                 self.normal[ntarget - 0xE000:ntarget - 0xE000 + 5])

    def test_public_loader_reset_is_ipl_loader_not_pcex(self):
        # PUBLIC path RESET validates INFO/BODY (multi-sector), not PCEX+XSEL.
        # The reset symbol exists, starts with SEI, and differs from the
        # experimental_bios_reset entry (different boot contract).
        self.assertIn("reset", self.public_syms)
        reset = self.public_syms["reset"]
        self.assertGreaterEqual(reset, 0xF000)
        off = reset - 0xE000
        self.assertEqual(self.public_rom[off], 0x78)  # SEI first
        # INFO signature present in the linked image (PUBLIC contract).
        self.assertIn(b"PC Engine CD-ROM SYSTEM", self.public_rom)

    def test_proto_needs_xsel_while_loader_embeds_ipl_sel(self):
        # Contract mismatch documented (not bypassed): cd_base_proto.s reads
        # experimental_selection+7 (track) and +8/+9/+10 (HI/MID/LO), while
        # the PUBLIC loader embeds a 16-bit IPL_SEL and validates INFO/BODY.
        # The new builder must bind both to the same synthetic INDEX 01 and
        # refuse any mismatch (fail-closed), never silently reinterpret.
        import re
        proto = (ROOT / "src" / "cd_base_proto.s").read_text(encoding="utf-8")
        self.assertIn("experimental_selection+7", proto)
        self.assertIn("experimental_selection+8", proto)
        loader = (ROOT / "tests" / "integration" / "ipl_loader_rom.s").read_text(
            encoding="utf-8")
        self.assertIn("IPL_SEL", loader)
        self.assertIn("validate_info", loader)

    def test_link_and_dspoff_need_explicit_seeding(self):
        # cd_read_link needs $2400 proto state + $20F2-$20F7 window/block;
        # ex_dspoff needs $2502/$2503 shadow seeded ($A5 marker). The PUBLIC
        # loader does not seed them (loader owns $2200/$2F00/$3000+ only).
        link = (ROOT / "src" / "cd_read_link.s").read_text(encoding="utf-8")
        self.assertIn("$2400", link)
        self.assertIn("INVALID_STATE", link)
        dsp = (ROOT / "src" / "ex_dspoff.s").read_text(encoding="utf-8")
        self.assertIn("$2502", dsp)
        self.assertIn("$2503", dsp)
        loader = (ROOT / "tests" / "integration" / "ipl_loader_rom.s").read_text(
            encoding="utf-8")
        self.assertNotIn("$2502", loader)
        # $2400 appears in loader only as zero-page temps, not proto state.
        self.assertNotIn("ST_LAST", loader)

    def test_4a_stays_stub_in_public_loader(self):
        # $4A (KEY_BIOS/EX_MEMOPEN) has no override; its table entry must stay
        # a fallback stub. Immediate-RTS is a separate SIMULATED control
        # (tools/build_slot02_return_loader.py), never wired here.
        target = _read_table_target(self.public_rom, 0x4A)
        off = target - 0xE000
        self.assertEqual(self.public_rom[off:off + 2], bytes((0xA2, 0x4A)))


@requires_cc65
class IplLoaderG5BuilderTest(unittest.TestCase):
    """Part B: PUBLIC loader + experimental $02/$03/$33 (RED before builder)."""

    @classmethod
    def setUpClass(cls):
        if not ORIGINAL.is_file():
            raise unittest.SkipTest("needs make dev-rom output (CI build step)")
        try:
            from build_ipl_loader_g5 import build_ipl_loader_g5_rom  # noqa: E402
        except ImportError as error:
            raise unittest.SkipTest(f"RED: new builder missing: {error}")
        tmp = tempfile.TemporaryDirectory(prefix="pce-800-g5-")
        cls.addClassCleanup(tmp.cleanup)
        cls.out = Path(tmp.name)
        cls.base = build(cls.out / "base", root=ROOT)
        cls.normal = cls.base.read_bytes()
        cls.build = build_ipl_loader_g5_rom
        rom, syms = cls.build(ORIGINAL, cls.out / "g5", SEL, "debug", "t800")
        cls.rom_path = rom
        cls.g5 = rom.read_bytes()
        cls.syms = syms
        cls.manifest = json.loads(
            (cls.out / "g5" / "manifest-t800-g5.json").read_text(encoding="utf-8"))

    def test_output_files(self):
        self.assertTrue(self.rom_path.name.endswith(".pce"))
        self.assertEqual(len(self.g5), IMAGE_BYTES)
        # Bank0 layout: first 8 KiB + 248 KiB FF padding (same as other loaders).
        self.assertEqual(self.g5[8192:], b"\xff" * (IMAGE_BYTES - 8192))

    def test_normal_dev_rom_untouched(self):
        # The profile build must not mutate its normal input: verify the
        # captured bytes and rebuild byte-identical.
        verify_dev_rom(self.normal)
        with tempfile.TemporaryDirectory() as tmp:
            fresh = build(Path(tmp) / "fresh", root=ROOT)
            self.assertEqual(fresh.read_bytes(), self.normal)

    def test_api_area_diff_is_exactly_three_windows(self):
        from build_ipl_loader_g5 import api_windows_for  # noqa: E402
        normal = self.normal
        # Rebuild the baseline PUBLIC loader with identical SEL/variant to get
        # the pre-patch bytes (same pipeline, no experimental wiring).
        rom0, _ = build_ipl_loader_bios(
            ORIGINAL, self.out / "baseline", SEL, "debug", "t800")
        base = rom0.read_bytes()
        windows = api_windows_for(self.manifest["labels"]["api_slot_02"])
        diff = [i for i in range(0x1000) if base[i] != self.g5[i]]
        self.assertTrue(diff, "experimental wiring must take effect (no silent no-op)")
        self.assertTrue(set(diff) <= windows,
                        f"API-area diff outside three windows: {[hex(i) for i in diff if i not in windows][:8]}")
        for name in ("slot02", "slot33", "slot03"):
            window = {"slot02": set(range(self.manifest["labels"]["api_slot_02"] - 0xE000,
                                          self.manifest["labels"]["api_slot_02"] - 0xE000 + 5)),
                      "slot33": set(range(3 * 0x33, 3 * 0x33 + 3)),
                      "slot03": set(range(3 * 0x03, 3 * 0x03 + 3))}[name]
            self.assertTrue(set(diff) & window, f"{name} window took no effect")

    def test_wired_entries_are_jmp_to_boot(self):
        for slot, key in ((0x03, "cd_read_link_entry"),
                          (0x33, "experimental_slot_33")):
            with self.subTest(slot=hex(slot)):
                at = 3 * slot
                self.assertEqual(self.g5[at], 0x4C)
                target = self.g5[at + 1] | (self.g5[at + 2] << 8)
                self.assertEqual(target, self.manifest["labels"][key])
                self.assertGreaterEqual(target, 0xF000)
        # Slot $02 keeps the documented two-stage wiring.
        at = 3 * 0x02
        self.assertEqual(self.g5[at], 0x4C)
        stage1 = self.g5[at + 1] | (self.g5[at + 2] << 8)
        self.assertEqual(stage1, self.manifest["labels"]["api_slot_02"])
        proto = self.manifest["labels"]["cd_base_proto_entry"]
        second = bytes(self.g5[stage1 - 0xE000:stage1 - 0xE000 + 5])
        self.assertEqual(second, bytes((0x4C, proto & 0xFF, (proto >> 8) & 0xFF, 0xEA, 0xEA)))

    def test_eighteen_overrides_kept_and_4a_stub(self):
        for slot in sorted(overrides(ROOT)):
            if slot in (0x02, 0x03, 0x33):
                continue
            with self.subTest(slot=hex(slot)):
                self.assertEqual(self.g5[3 * slot:3 * slot + 3],
                                 self.normal[3 * slot:3 * slot + 3])
        # $4A must NOT be patched (SIMULATED control stays separate).
        target = _read_table_target(self.g5, 0x4A)
        off = target - 0xE000
        self.assertEqual(self.g5[off:off + 2], bytes((0xA2, 0x4A)))

    def test_manifest_gates(self):
        m = self.manifest
        self.assertEqual(m["ipl_format"], "public")
        self.assertEqual(m["ipl_sel_lba"], SEL)
        self.assertFalse(m["release_eligible"])
        self.assertFalse(m["dev_callable"])
        self.assertEqual(m["system_card_abi"], "NO_CLAIM")
        self.assertEqual(m["experimental_slots"], ["$02", "$03", "$33"])
        self.assertEqual(m["reference_jsr"], "0/81")
        self.assertEqual(m["abi_verified"], "0/81")
        self.assertFalse(m["private_media_used"])

    def test_rejects_bad_sel_before_any_write(self):
        from build_ipl_loader_g5 import build_ipl_loader_g5_rom as build_g5  # noqa: E402
        for bad in (-1, 0x10000, True, "265", None):
            with self.subTest(bad=repr(bad)):
                with self.assertRaisesRegex(ValueError, "IPL_SEL"):
                    build_g5(ORIGINAL, self.out / "bad", bad, "debug", "bad")

    def test_rejects_tampered_base(self):
        from build_ipl_loader_g5 import build_ipl_loader_g5_rom as build_g5  # noqa: E402
        tamper_dir = self.out / "tampered"
        tamper_dir.mkdir(exist_ok=True)
        bad = tamper_dir / ORIGINAL.name
        raw = bytearray(self.normal)
        raw[0x009] ^= 0xFF
        bad.write_bytes(bytes(raw))
        (tamper_dir / "manifest.json").write_text(
            (self.out / "base" / "manifest.json").read_text(encoding="utf-8")
            if (self.out / "base" / "manifest.json").exists()
            else json.dumps({"rom_sha256": hashlib.sha256(self.normal).hexdigest()}))
        with self.assertRaises(ValueError):
            build_g5(bad, self.out / "bad2", SEL, "debug", "bad2")


if __name__ == "__main__":
    unittest.main()
