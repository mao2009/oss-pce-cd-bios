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
import os
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
        dsp = (ROOT / "src" / "ex_cr_family.s").read_text(encoding="utf-8")
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
        from build_ipl_loader_g5 import build_ipl_loader_g5_rom  # noqa: E402
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

    def test_release_mode_keeps_loader_labels(self):
        # Major review fix: the loader/proto/link/glue objects always carry
        # -g so `reset`/`irq_stub`/etc. survive in the -Ln file in release
        # mode too (ROM bytes unchanged, only labels). Without this the
        # release build raises missing-symbol errors.
        from build_ipl_loader_g5 import build_ipl_loader_g5_rom as build_g5  # noqa: E402
        rom, syms = build_g5(ORIGINAL, self.out / "g5rel", SEL, "release", "t800rel")
        data = rom.read_bytes()
        self.assertEqual(len(data), IMAGE_BYTES)
        for name in ("reset", "irq_stub", "cd_halt_fail",
                     "cd_unexpected_interrupt", "api_unimplemented",
                     "cd_base_proto_entry", "cd_read_link_entry",
                     "experimental_slot_33"):
            self.assertIn(name, syms, f"release symbols missing {name}")
        # Release image still wires the three windows (no silent no-op).
        self.assertEqual(data[3 * 0x03], 0x4C)
        self.assertEqual(data[3 * 0x33], 0x4C)

    def test_public_defines_forms_classified(self):
        # `-D IPL_PUBLIC=1` still selects PUBLIC behavior in ca65, so the
        # whitelist normalizes it to the canonical PUBLIC argv; the manifest
        # classifies exactly the normalized argv (MEDIUM-1: no substring
        # guessing, so ["-I", "IPL_PUBLIC"] can never read as PUBLIC).
        from build_ipl_loader_g5 import _normalize_defines  # noqa: E402
        self.assertEqual(_normalize_defines(None), (["-D", "IPL_PUBLIC"], True))
        self.assertEqual(_normalize_defines([]), ([], False))
        self.assertEqual(_normalize_defines(["-D", "IPL_PUBLIC"]),
                         (["-D", "IPL_PUBLIC"], True))
        self.assertEqual(_normalize_defines(["-D", "IPL_PUBLIC=1"]),
                         (["-D", "IPL_PUBLIC"], True))
        self.assertEqual(_normalize_defines(("-D", "IPL_PUBLIC")),
                         (["-D", "IPL_PUBLIC"], True))
        for bad in (["-I", "IPL_PUBLIC"], ["-D", "IPL_PUBLIC", "-l", "/tmp/x.lst"],
                    ["-o", "/tmp/evil.o"], ["--create-dep", "/tmp/x.d"],
                    ["-I", "/tmp/inc"], ["-D", "IPL_SEL=1234"],
                    ["-D", "EXP_SELECTED_TRACK=02"], ["-D", "FOO"],
                    ["-D"], "IPL_PUBLIC", 0, True, [b"-D"], [["-D"]]):
            with self.subTest(defines=repr(bad)):
                with self.assertRaises(ValueError):
                    _normalize_defines(bad)

    def test_defines_injection_refused_before_any_write(self):
        # MAJOR-1: extra ca65 options must never reach the assembler. Each
        # refused input leaves no output directory and no sentinel behind.
        # Everything lives in disposable temp dirs (never the real tree).
        from build_ipl_loader_g5 import build_ipl_loader_g5_rom as build_g5  # noqa: E402
        import shutil as _shutil
        bad_list = [["-D", "IPL_PUBLIC", "-l", "/tmp/x.lst"],
                    ["-o", "/tmp/evil.o"],
                    ["--create-dep", "/tmp/x.d"],
                    ["-I", "IPL_PUBLIC"],
                    ["-I", "/tmp/inc"],
                    ["-D", "IPL_SEL=1234"],
                    ["-D", "EXP_SELECTED_TRACK=02"],
                    ["-D", "EXP_SELECTED_LBA_LO=09"],
                    ["-D", "FOO"],
                    ["-D", "IPL_PUBLIC", "-D", "IPL_PUBLIC"]]
        for i, bad in enumerate(bad_list):
            with self.subTest(defines=repr(bad)):
                scratch = Path(tempfile.mkdtemp(prefix="pce-800-defrej-"))
                self.addCleanup(_shutil.rmtree, str(scratch), ignore_errors=True)
                out = scratch / "out"
                sentinel = scratch / "sentinel.bin"
                sentinel.write_bytes(b"SENTINEL-800-defines")
                with self.assertRaises(ValueError):
                    build_g5(ORIGINAL, out, SEL, "debug", f"defrej{i:02d}", bad)
                self.assertFalse(out.exists(),
                                 f"refused defines {bad!r} must not create output")
                self.assertEqual(sentinel.read_bytes(), b"SENTINEL-800-defines")

    def test_normalized_public_rom_bytes_identical_and_manifest_public(self):
        # The value form must assemble exactly the canonical PUBLIC image
        # and classify as PUBLIC (MEDIUM-1: manifest matches the real build).
        from build_ipl_loader_g5 import build_ipl_loader_g5_rom as build_g5  # noqa: E402
        out1 = self.out / "norm-a"
        out2 = self.out / "norm-b"
        rom1, _ = build_g5(ORIGINAL, out1, SEL, "debug", "normpub",
                           ["-D", "IPL_PUBLIC"])
        rom2, _ = build_g5(ORIGINAL, out2, SEL, "debug", "normpub",
                           ["-D", "IPL_PUBLIC=1"])
        self.assertEqual(rom1.read_bytes(), rom2.read_bytes())
        for out in (out1, out2):
            manifest = json.loads((out / "manifest-normpub-g5.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["ipl_format"], "public")
            self.assertIn("PUBLIC-IPL-FORMAT", manifest["status"])
            self.assertEqual(manifest["defines"], ["-D", "IPL_PUBLIC"])

    def test_dev_build_manifest_matches_real_build(self):
        # defines=[] keeps the existing DEV contract and classifies as DEV.
        from build_ipl_loader_g5 import build_ipl_loader_g5_rom as build_g5  # noqa: E402
        out = self.out / "norm-dev"
        rom, _ = build_g5(ORIGINAL, out, SEL, "debug", "normdev", [])
        self.assertEqual(len(rom.read_bytes()), IMAGE_BYTES)
        manifest = json.loads((out / "manifest-normdev-g5.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["ipl_format"], "dev")
        self.assertIn("DEV-IPL-FORMAT", manifest["status"])
        self.assertEqual(manifest["defines"], [])

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


@requires_cc65
class IplLoaderG5OutputSafetyTest(unittest.TestCase):
    """R1-R3 + MEDIUM variant: output-symlink / leaf-symlink / normal-dir / variant guards.

    Follow-up to Issue #800 (PR #801 review): mirrors the PR #799 output-safety
    policy (lstat without following, refuse before any write, realpath
    same-directory, no ancestor checks, documented TOCTOU limit). Every test
    uses an isolated fake normal DEV ROM in a temp dir; the real build tree
    and ./rom are never touched (commercial media: NOT_RUN, no probing).
    """

    VARIANT = "t800"

    @classmethod
    def setUpClass(cls):
        from build_ipl_loader_g5 import build_ipl_loader_g5_rom  # noqa: E402
        # staticmethod: a plain function stored as a class attribute would
        # become a bound method via self.build (shifting every argument).
        cls.build = staticmethod(build_ipl_loader_g5_rom)
        tmp = tempfile.TemporaryDirectory(prefix="pce-800-g5safe-")
        cls.addClassCleanup(tmp.cleanup)
        cls.work = Path(tmp.name)
        cls.fake_normal_dir = cls.work / "fakenormal"
        cls.fake_base = build(cls.fake_normal_dir, root=ROOT)
        cls.fake_normal = cls.fake_base.read_bytes()

    def _sentinel_dir(self, name):
        ext = self.work / name
        ext.mkdir(exist_ok=True)
        sentinel = ext / "sentinel.bin"
        sentinel.write_bytes(b"SENTINEL-800:" + name.encode() + b":" + b"x" * 64)
        return ext, sentinel

    def _assert_untouched(self, ext, sentinel):
        self.assertTrue(sentinel.is_file())
        self.assertEqual(sentinel.read_bytes(),
                         b"SENTINEL-800:" + ext.name.encode() + b":" + b"x" * 64)

    def test_output_symlink_refused(self):
        ext, sentinel = self._sentinel_dir("ext-out")
        before_children = sorted(p.name for p in ext.iterdir())
        link = self.work / "outlink"
        link.symlink_to(ext, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.build(self.fake_base, link, SEL, "debug", self.VARIANT)
        self._assert_untouched(ext, sentinel)
        self.assertEqual(sorted(p.name for p in ext.iterdir()), before_children)

    def test_output_dangling_symlink_refused(self):
        ext, sentinel = self._sentinel_dir("ext-dangle")
        before_children = sorted(p.name for p in ext.iterdir())
        link = self.work / "outdangle"
        link.symlink_to(ext / "missing-target", target_is_directory=True)
        self.assertFalse(link.exists())
        self.assertTrue(os.path.lexists(link))
        with self.assertRaises(ValueError):
            self.build(self.fake_base, link, SEL, "debug", self.VARIANT)
        self._assert_untouched(ext, sentinel)
        self.assertEqual(sorted(p.name for p in ext.iterdir()), before_children)

    def test_output_plain_file_refused(self):
        blocker = self.work / "planfile"
        blocker.write_bytes(b"BLOCKER")
        with self.assertRaises((ValueError, OSError)):
            self.build(self.fake_base, blocker, SEL, "debug", self.VARIANT)
        self.assertEqual(blocker.read_bytes(), b"BLOCKER")

    def test_pce_leaf_symlink_refused(self):
        ext, sentinel = self._sentinel_dir("ext-pce")
        out = self.work / "leaf-pce"
        out.mkdir()
        (out / f"dev-ipl-loader-g5-not-compatible-{self.VARIANT}.pce").symlink_to(sentinel)
        with self.assertRaises(ValueError):
            self.build(self.fake_base, out, SEL, "debug", self.VARIANT)
        self._assert_untouched(ext, sentinel)

    def test_manifest_and_trace_leaf_symlinks_refused(self):
        for leaf in (f"manifest-{self.VARIANT}-g5.json",
                     f"trace-labels-{self.VARIANT}-g5.txt"):
            with self.subTest(leaf=leaf):
                ext, sentinel = self._sentinel_dir(f"ext-{leaf[:12]}")
                out = self.work / f"leaf-{leaf[:12]}"
                out.mkdir()
                (out / leaf).symlink_to(sentinel)
                with self.assertRaises(ValueError):
                    self.build(self.fake_base, out, SEL, "debug", self.VARIANT)
                self._assert_untouched(ext, sentinel)

    def test_intermediate_leaf_symlinks_refused(self):
        leaves = (f"ipl_loader_{self.VARIANT}.o",
                  f"bank0-{self.VARIANT}-g5.bin",
                  f"ipl-loader-g5-{self.VARIANT}.lbl",
                  f"cd_base_proto_{self.VARIANT}.o",
                  "api_table.s",
                  "api_table.o")
        for i, leaf in enumerate(leaves):
            with self.subTest(leaf=leaf):
                ext, sentinel = self._sentinel_dir(f"ext-leafi-{i:02d}")
                out = self.work / f"leaf-i-{i:02d}"
                out.mkdir()
                (out / leaf).symlink_to(sentinel)
                with self.assertRaises(ValueError):
                    self.build(self.fake_base, out, SEL, "debug", self.VARIANT)
                self._assert_untouched(ext, sentinel)

    def _snapshot_dir_files(self, directory):
        return {p.name: p.read_bytes() for p in sorted(directory.iterdir())
                if p.is_file()}

    def test_normal_dev_dir_output_refused(self):
        before = self._snapshot_dir_files(self.fake_normal_dir)
        self.assertIn(self.fake_base.name, before)
        with self.assertRaises(ValueError):
            self.build(self.fake_base, self.fake_normal_dir, SEL, "debug", self.VARIANT)
        self.assertEqual(self._snapshot_dir_files(self.fake_normal_dir), before)

    def test_normal_dev_dir_spellings_refused(self):
        before = self._snapshot_dir_files(self.fake_normal_dir)
        spellings = [
            self.fake_normal_dir / "." / ".." / self.fake_normal_dir.name,
            self.fake_normal_dir / "sub" / "..",
        ]
        alias = self.work / "normalias"
        alias.symlink_to(self.fake_normal_dir, target_is_directory=True)
        spellings.append(alias)
        for spelling in spellings:
            with self.subTest(spelling=str(spelling)):
                with self.assertRaises(ValueError):
                    self.build(self.fake_base, spelling, SEL, "debug", self.VARIANT)
        self.assertEqual(self._snapshot_dir_files(self.fake_normal_dir), before)
        self.assertTrue(alias.is_symlink())

    def _mock_canonical_dir(self, name):
        # Isolated stand-in for ROOT/build/<mode>/bios-dev. The real tree is
        # never touched: only the lookup is mocked, never the filesystem.
        canon = self.work / name / "build" / "debug" / "bios-dev"
        canon.mkdir(parents=True)
        (canon / "sentinel.txt").write_bytes(b"CANONICAL-SENTINEL")
        return canon

    def test_canonical_dev_dir_output_refused(self):
        # MAJOR-1: a temp copy used as input must not re-open the canonical
        # normal DEV tree. Mock only the canonical-dir lookup.
        from unittest import mock
        canon = self._mock_canonical_dir("canon")
        before = self._snapshot_dir_files(canon)
        with mock.patch("build_ipl_loader_g5._canonical_dev_dir",
                         return_value=canon, create=True):
            with self.assertRaises(ValueError):
                self.build(self.fake_base, canon, SEL, "debug", self.VARIANT)
        self.assertEqual(self._snapshot_dir_files(canon), before)
        self.assertEqual((canon / "sentinel.txt").read_bytes(), b"CANONICAL-SENTINEL")

    def test_canonical_dev_dir_spellings_refused(self):
        # MAJOR-1 spellings: relative path, ".", ".." and symlink alias
        # of the canonical dir are refused without creating anything.
        from unittest import mock
        canon = self._mock_canonical_dir("canonspell")
        before = self._snapshot_dir_files(canon)
        rel = Path(os.path.relpath(str(canon), str(Path.cwd())))
        spellings = [
            rel,
            canon / "." / ".." / canon.name,
            canon / "sub" / "..",
        ]
        alias = self.work / "canonalias"
        alias.symlink_to(canon, target_is_directory=True)
        spellings.append(alias)
        with mock.patch("build_ipl_loader_g5._canonical_dev_dir",
                         return_value=canon, create=True):
            for spelling in spellings:
                with self.subTest(spelling=str(spelling)):
                    with self.assertRaises(ValueError):
                        self.build(self.fake_base, spelling, SEL, "debug", self.VARIANT)
        self.assertEqual(self._snapshot_dir_files(canon), before)
        self.assertEqual((canon / "sentinel.txt").read_bytes(), b"CANONICAL-SENTINEL")
        self.assertTrue(alias.is_symlink())

    def test_foreign_part_like_files_preserved(self):
        # MEDIUM-2: only this run's own temp files may be cleaned. A foreign
        # file (or link) that merely matches the part pattern must survive
        # a successful build byte-identical.
        out = self.work / "foreign-part"
        out.mkdir()
        foreign = out / f"dev-ipl-loader-g5-not-compatible-{self.VARIANT}.pce.part-foreign.tmp"
        foreign.write_bytes(b"FOREIGN-NOT-OURS")
        ext, sentinel = self._sentinel_dir("ext-partlink")
        link = out / f"manifest-{self.VARIANT}-g5.json.part-link.tmp"
        link.symlink_to(sentinel)
        rom, _ = self.build(self.fake_base, out, SEL, "debug", self.VARIANT)
        self.assertTrue(rom.is_file())
        self.assertEqual(foreign.read_bytes(), b"FOREIGN-NOT-OURS")
        self.assertTrue(link.is_symlink())
        self._assert_untouched(ext, sentinel)

    def test_unsafe_variants_refused_before_any_write(self):
        bad = ["../outside", "a/b", "a\\b", "/tmp/outside.pce",
               "C:\\outside", "C:/outside", "\\\\server\\share",
               "", ".", "..", "bad\x00name", "manifest.json",
               "trace-labels.txt", "x" * 65, "a b", ".hidden"]
        for i, variant in enumerate(bad):
            with self.subTest(variant=repr(variant)):
                out = self.work / f"variant-{i:02d}"
                with self.assertRaises(ValueError):
                    self.build(self.fake_base, out, SEL, "debug", variant)
                self.assertFalse(out.exists(),
                                 f"refused variant {variant!r} must not create output")

    def test_safe_variants_still_usable(self):
        for variant in ("main", "t800", "t800rel"):
            with self.subTest(variant=variant):
                out = self.work / f"ok-{variant}"
                rom, _syms = self.build(self.fake_base, out, SEL, "debug", variant)
                self.assertTrue(rom.is_file())
                self.assertEqual(len(rom.read_bytes()), IMAGE_BYTES)

    def test_rebuild_in_place_is_stable(self):
        out = self.work / "stable"
        rom1, _ = self.build(self.fake_base, out, SEL, "debug", self.VARIANT)
        sha1 = hashlib.sha256(rom1.read_bytes()).hexdigest()
        rom2, _ = self.build(self.fake_base, out, SEL, "debug", self.VARIANT)
        self.assertEqual(rom2.read_bytes(), rom1.read_bytes())
        self.assertEqual(hashlib.sha256(rom2.read_bytes()).hexdigest(), sha1)

    def test_failed_verification_leaves_no_rom(self):
        # MEDIUM-2/MEDIUM-1: the final image is verified in memory before any
        # disk write. Corrupt the RESET symbol (a check that runs AFTER the
        # ROM write in the old implementation) and prove that no .pce,
        # manifest or trace labels are published (RED on code that writes
        # the ROM first and verifies afterwards).
        from unittest import mock
        from build_dev_ipl_loader_bios import parse_labels as real_parse  # noqa: E402

        def bad_parse(labels_path, names):
            syms = dict(real_parse(labels_path, names))
            syms["reset"] = 0xE000
            return syms

        out = self.work / "romfail-fresh"
        with mock.patch("build_ipl_loader_g5.parse_labels", side_effect=bad_parse):
            with self.assertRaises(ValueError):
                self.build(self.fake_base, out, SEL, "debug", self.VARIANT)
        for leaf in (f"dev-ipl-loader-g5-not-compatible-{self.VARIANT}.pce",
                     f"manifest-{self.VARIANT}-g5.json",
                     f"trace-labels-{self.VARIANT}-g5.txt"):
            self.assertFalse((out / leaf).exists(),
                             f"{leaf} must not be published when verification fails")
        self.assertEqual(self._part_files(out), [])

    def test_failed_verification_preserves_existing_rom(self):
        # MEDIUM-2 (+MEDIUM-1 injection): a failed rebuild must not destroy
        # the previous good ROM/manifest/trace labels. The corrupted RESET
        # symbol fails the vector/RESET contract check -- after the ROM
        # write in the old implementation, before any write in the new one.
        from unittest import mock
        from build_dev_ipl_loader_bios import parse_labels as real_parse  # noqa: E402

        def bad_parse(labels_path, names):
            syms = dict(real_parse(labels_path, names))
            syms["reset"] = 0xE000
            return syms

        out = self.work / "romfail-keep"
        rom1, _ = self.build(self.fake_base, out, SEL, "debug", self.VARIANT)
        self.assertTrue(rom1.is_file())
        finals = self._final_names()
        before = {name: (out / name).read_bytes() for name in finals}
        with mock.patch("build_ipl_loader_g5.parse_labels", side_effect=bad_parse):
            with self.assertRaises(ValueError):
                self.build(self.fake_base, out, SEL, "debug", self.VARIANT)
        for name in finals:
            self.assertEqual((out / name).read_bytes(), before[name],
                             f"{name} must survive a failed rebuild")
        self.assertEqual(self._part_files(out), [])

    def _final_names(self):
        return (f"dev-ipl-loader-g5-not-compatible-{self.VARIANT}.pce",
                f"manifest-{self.VARIANT}-g5.json",
                f"trace-labels-{self.VARIANT}-g5.txt")

    def _part_files(self, out):
        return sorted(p.name for p in out.iterdir()
                      if ".part-" in p.name and p.name.endswith(".tmp"))

    def test_final_publish_atomic_on_replace_failure(self):
        # A mid-publish failure (os.replace raising, e.g. ENOSPC/permission)
        # must leave the previous finals intact and no temp files behind.
        from unittest import mock
        out = self.work / "atomic-keep"
        rom1, _ = self.build(self.fake_base, out, SEL, "debug", self.VARIANT)
        self.assertTrue(rom1.is_file())
        finals = self._final_names()
        before = {name: (out / name).read_bytes() for name in finals}
        with mock.patch.object(os, "replace", side_effect=OSError("ENOSPC")):
            with self.assertRaises(OSError):
                self.build(self.fake_base, out, SEL, "debug", self.VARIANT)
        for name in finals:
            self.assertEqual((out / name).read_bytes(), before[name],
                             f"{name} must survive a failed publish")
        self.assertEqual(self._part_files(out), [])

    def test_final_publish_atomic_on_fresh_dir_replace_failure(self):
        # Same as above but with no previous files: nothing is published.
        from unittest import mock
        out = self.work / "atomic-fresh"
        with mock.patch.object(os, "replace", side_effect=OSError("ENOSPC")):
            with self.assertRaises(OSError):
                self.build(self.fake_base, out, SEL, "debug", self.VARIANT)
        for name in self._final_names():
            self.assertFalse((out / name).exists(),
                             f"{name} must not be published when replace fails")
        self.assertEqual(self._part_files(out), [])

    def test_tmp_cleaned_on_write_failure(self):
        # A failure during the temp-file write (fsync raising) must unlink
        # the partial temp file and preserve the previous finals.
        from unittest import mock
        out = self.work / "atomic-write-fail"
        rom1, _ = self.build(self.fake_base, out, SEL, "debug", self.VARIANT)
        self.assertTrue(rom1.is_file())
        finals = self._final_names()
        before = {name: (out / name).read_bytes() for name in finals}
        with mock.patch.object(os, "fsync", side_effect=OSError("EIO")):
            with self.assertRaises(OSError):
                self.build(self.fake_base, out, SEL, "debug", self.VARIANT)
        for name in finals:
            self.assertEqual((out / name).read_bytes(), before[name],
                             f"{name} must survive a failed temp write")
        self.assertEqual(self._part_files(out), [])


if __name__ == "__main__":
    unittest.main()
