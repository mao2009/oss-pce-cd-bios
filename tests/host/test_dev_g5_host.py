"""Host gates for the G5 DEV profile ROM (Issue #798). No core needed; needs pinned cc65.

The G5 profile reuses the normal DEV build (18 overrides, byte-identical) and
the experimental triple-link wiring ($02 CD_BASE proto + $03 CD_READ link +
$33 EX_DSPOFF) behind an explicit `--profile g5-experimental` entry. It never
claims genuine ABI compatibility.
"""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.environment import requires_cc65

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from tools import build_dev_g5  # noqa: E402
from tools import build_experimental_bios_boot as exp  # noqa: E402
from tools import build_rom  # noqa: E402


def _make_chain_disc(folder, mode="debug"):
    """Single-sector synthetic disc with the standard payload embedded at INDEX01=0."""
    from tools import make_cd_image
    from tools.build_cdload_exec import USER_OFFSET, encode_sector
    from tools.build_experimental_bios_boot import build_experimental_payload
    from tools.experimental_track_selection import selection_from_cue, snapshot_media
    from tools.make_cd_image import BIN_NAME, CUE_NAME, RAW_BYTES, build_image
    media = folder / "media"
    build_image(media)
    cue0 = media / CUE_NAME
    build_dir = folder / "pay"
    code, syms = build_experimental_payload(build_dir, mode)
    raw = bytearray((media / BIN_NAME).read_bytes())
    raw[0 * RAW_BYTES + USER_OFFSET:0 * RAW_BYTES + USER_OFFSET + 2048] = encode_sector(0, code)
    (media / BIN_NAME).write_bytes(bytes(raw))
    cue = snapshot_media(cue0, folder / "mounted")
    return cue, code, syms, selection_from_cue(cue, cue)


@requires_cc65
class DevG5ProfileHostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.folder = Path(cls.tmp.name)
        cls.base_dir = cls.folder / "base"
        cls.normal_path = build_rom.build(cls.base_dir, root=ROOT)
        cls.normal = cls.normal_path.read_bytes()
        cls.cue, cls.code, cls.syms, cls.selected = _make_chain_disc(cls.folder)
        cls.out = cls.folder / "bios-dev-g5"
        cls.g5_path = build_dev_g5.build_g5_dev_rom(cls.normal_path, cls.out, "debug",
                                                    cls.cue, cls.code, cls.syms)
        cls.g5 = cls.g5_path.read_bytes()
        cls.manifest = json.loads((cls.out / "manifest.json").read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_output_files(self):
        self.assertEqual(self.g5_path.name, build_dev_g5.G5_ROM_NAME)
        self.assertTrue((self.out / "manifest.json").is_file())
        self.assertTrue((self.out / "trace-labels.txt").is_file())
        self.assertEqual(len(self.g5), build_rom.IMAGE_BYTES)
        self.assertEqual(self.g5[build_rom.BANK_BYTES:],
                         b"\xff" * (build_rom.IMAGE_BYTES - build_rom.BANK_BYTES))

    def test_normal_dev_rom_untouched(self):
        # The profile build must not mutate its normal input, and the input
        # must still be byte-identical to an independent rebuild.
        self.assertEqual(self.normal_path.read_bytes(), self.normal)
        with tempfile.TemporaryDirectory() as tmp:
            fresh = build_rom.build(Path(tmp) / "fresh", root=ROOT)
            self.assertEqual(fresh.read_bytes(), self.normal)
        build_rom.verify_dev_rom(self.normal,
                                 manifest=self.base_dir / "manifest.json")

    def test_api_area_diff_is_exactly_three_windows(self):
        normal, g5 = self.normal, self.g5
        self.assertEqual(len(normal), len(g5))
        diff = [i for i in range(0x1000) if normal[i] != g5[i]]
        # The only allowed API-area deltas: 5-byte $02 fallback window,
        # 3-byte $33 table redirect, 3-byte $03 table redirect.
        slot02 = self.manifest["labels"]["api_slot_02"] - 0xE000
        windows = (set(range(slot02, slot02 + 5))
                   | set(range(3 * 0x33, 3 * 0x33 + 3))
                   | set(range(3 * 0x03, 3 * 0x03 + 3)))
        self.assertTrue(diff, "G5 wiring must take effect (no silent no-op)")
        self.assertTrue(set(diff) <= windows,
                        f"API-area diff outside the three windows: {[hex(i) for i in diff if i not in windows][:8]}")
        for window in (set(range(slot02, slot02 + 5)),
                       set(range(3 * 0x33, 3 * 0x33 + 3)),
                       set(range(3 * 0x03, 3 * 0x03 + 3))):
            self.assertTrue(set(diff) & window, "a wiring window took no effect")

    def test_wired_entries_are_jmp_to_boot(self):
        # Slots $03/$33: direct table JMP to the BOOT body. Slot $02 keeps the
        # documented two-stage wiring (table -> api_slot_02 -> JMP proto+NOPs).
        for slot, name in ((0x03, "cd_read_link_entry"), (0x33, "experimental_slot_33")):
            at = 3 * slot
            self.assertEqual(self.g5[at], 0x4C, f"slot ${slot:02X} entry is not JMP")
            target = self.g5[at + 1] | (self.g5[at + 2] << 8)
            self.assertEqual(target, self.manifest["labels"][name])
            self.assertGreaterEqual(target, 0xF000)
            self.assertLess(target, 0xFFF6)
        at = 3 * 0x02
        self.assertEqual(self.g5[at], 0x4C)
        stage1 = self.g5[at + 1] | (self.g5[at + 2] << 8)
        self.assertEqual(stage1, self.manifest["labels"]["api_slot_02"])
        proto = self.manifest["labels"]["cd_base_proto_entry"]
        second = bytes(self.g5[stage1 - 0xE000:stage1 - 0xE000 + 5])
        self.assertEqual(second, bytes((0x4C, proto & 0xFF, (proto >> 8) & 0xFF, 0xEA, 0xEA)))
        self.assertGreaterEqual(proto, 0xF000)
        self.assertLess(proto, 0xFFF6)

    def test_eighteen_overrides_kept(self):
        for slot in sorted(build_rom.overrides(ROOT)):
            if slot in (0x02, 0x03, 0x33):
                continue
            at = 3 * slot
            self.assertEqual(self.g5[at:at + 3], self.normal[at:at + 3],
                             f"override slot ${slot:02X} table entry changed")

    def test_vectors_and_probe_overlay(self):
        reset = int.from_bytes(self.g5[0x1FFE:0x2000], "little")
        self.assertEqual(reset, self.manifest["symbols"]["experimental_bios_reset"])
        self.assertEqual(self.g5[reset & 0x1FFF], 0x78)  # SEI first
        # $E800-$EFFF probe overlay stays padding.
        self.assertEqual(self.g5[0x0800:0x1000], b"\xff" * 0x800)

    def test_manifest_gates(self):
        manifest = self.manifest
        self.assertEqual(manifest["rom_name"], build_dev_g5.G5_ROM_NAME)
        self.assertEqual(manifest["rom_sha256"], hashlib.sha256(self.g5).hexdigest())
        self.assertEqual(manifest["rom_bytes"], build_rom.IMAGE_BYTES)
        self.assertFalse(manifest["release_eligible"])
        self.assertFalse(manifest["dev_callable"])
        self.assertIn(manifest["system_card_abi"], ("NO_CLAIM",))
        self.assertIn("g5_profile", manifest)
        profile = manifest["g5_profile"]
        self.assertEqual(profile["profile"], "g5-experimental")
        self.assertEqual(profile["normal_dev_rom_sha256"], hashlib.sha256(self.normal).hexdigest())
        self.assertEqual(profile["normal_dev_overrides"], 18)
        self.assertEqual(profile["experimental_slots"], ["$02", "$03", "$33"])
        self.assertEqual(profile["reference_jsr"], "0/81")
        self.assertEqual(profile["abi_verified"], "0/81")
        self.assertFalse(profile["release_eligible"])

    def test_rejects_cue_without_payload(self):
        from tools import make_cd_image
        fresh = self.folder / "fresh-media"
        make_cd_image.build_image(fresh)
        cue = fresh / make_cd_image.CUE_NAME
        from tools.experimental_track_selection import snapshot_media
        mounted = snapshot_media(cue, self.folder / "fresh-mounted")
        with self.assertRaisesRegex(ValueError, "payload"):
            build_dev_g5.build_g5_dev_rom(self.normal_path, self.folder / "bad1",
                                          "debug", mounted, self.code, self.syms)

    def test_rejects_output_equal_to_base_dir(self):
        with self.assertRaisesRegex(ValueError, "must not be the normal DEV ROM directory"):
            build_dev_g5.build_g5_dev_rom(self.normal_path, self.base_dir,
                                          "debug", self.cue, self.code, self.syms)
        # The normal manifest is intact (nothing was written).
        build_rom.verify_dev_rom(self.normal,
                                 manifest=self.base_dir / "manifest.json")

    def test_profile_rebuild_is_idempotent(self):
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "bios-dev-g5"
            first = build_dev_g5.run_profile(out, "debug")
            first_bytes = first.read_bytes()
            second = build_dev_g5.run_profile(out, "debug")
            self.assertEqual(second.read_bytes(), first_bytes)
            # The generated disc manifest describes the finalized media.
            disc_manifest = json.loads((out / "media" / "disc-manifest.json").read_text())
            final_bin = (out / "media" / "oss-pce-cd-test.bin").read_bytes()
            self.assertEqual(disc_manifest["bin_sha256"], hashlib.sha256(final_bin).hexdigest())
            from tools.make_cd_image import RAW_BYTES
            from tools.build_cdload_exec import USER_OFFSET
            sector0 = final_bin[0 * RAW_BYTES + USER_OFFSET:0 * RAW_BYTES + USER_OFFSET + 2048]
            self.assertEqual(disc_manifest["user_data_sha256"][0],
                             hashlib.sha256(sector0).hexdigest())

    def test_rebuild_refuses_foreign_snapshot_content(self):
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "bios-dev-g5"
            first = build_dev_g5.run_profile(out, "debug")
            first_bytes = first.read_bytes()
            # A rebuild reuses the identical snapshot (same ROM bytes).
            self.assertEqual(build_dev_g5.run_profile(out, "debug").read_bytes(), first_bytes)
            # Unknown content in the snapshot directory is refused, never deleted.
            (out / "media" / "mounted" / "user-placed.txt").write_text("do not touch")
            with self.assertRaisesRegex(ValueError, "refusing to replace unknown content"):
                build_dev_g5.run_profile(out, "debug")
            self.assertEqual((out / "media" / "mounted" / "user-placed.txt").read_text(),
                             "do not touch")

    def test_rejects_tampered_base(self):
        tampered = self.folder / "tampered-base"
        tampered.mkdir(exist_ok=True)
        bad = tampered / self.normal_path.name
        raw = bytearray(self.normal)
        raw[0x009] ^= 0xFF
        bad.write_bytes(bytes(raw))
        (tampered / "manifest.json").write_text(
            (self.base_dir / "manifest.json").read_text(encoding="utf-8"))
        with self.assertRaises(ValueError):
            build_dev_g5.build_g5_dev_rom(bad, self.folder / "bad2",
                                          "debug", self.cue, self.code, self.syms)

    def test_build_rom_profile_dev_is_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "dev"
            with patch("sys.argv", ["build_rom.py", "--mode", "debug", "--output", str(out)]):
                self.assertEqual(build_rom.main(), 0)
            rom = out / "dev-only-not-compatible-syscard3.pce"
            self.assertEqual(rom.read_bytes(), self.normal)

    def test_build_rom_profile_g5_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "bios-dev-g5"
            argv = ["build_rom.py", "--mode", "debug", "--profile", "g5-experimental",
                    "--output", str(out)]
            with patch("sys.argv", argv):
                self.assertEqual(build_rom.main(), 0)
            rom = out / build_dev_g5.G5_ROM_NAME
            data = rom.read_bytes()
            self.assertEqual(len(data), build_rom.IMAGE_BYTES)
            manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["g5_profile"]["profile"], "g5-experimental")
            # Same selection (INDEX01=0/track=1) -> same ROM bytes as the fixture build.
            self.assertEqual(data, self.g5)

    def test_build_rom_profile_g5_needs_cue_or_generates_media(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "bios-dev-g5"
            argv = ["build_rom.py", "--mode", "debug", "--profile", "g5-experimental",
                    "--output", str(out), "--cue", str(self.folder / "nope.cue")]
            with patch("sys.argv", argv):
                self.assertNotEqual(build_rom.main(), 0)

    def test_run_profile_with_prepared_cue(self):
        # A caller-prepared CUE embedding the default payload builds the same ROM.
        from tools import make_cd_image
        from tools.build_cdload_exec import USER_OFFSET, encode_sector
        from tools.build_experimental_bios_boot import build_experimental_payload
        from tools.experimental_track_selection import snapshot_media
        from tools.make_cd_image import BIN_NAME, CUE_NAME, RAW_BYTES, build_image
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            media = folder / "media"
            build_image(media)
            code, _ = build_experimental_payload(folder / "pay", "debug")
            raw = bytearray((media / BIN_NAME).read_bytes())
            raw[0 * RAW_BYTES + USER_OFFSET:0 * RAW_BYTES + USER_OFFSET + 2048] = \
                encode_sector(0, code)
            (media / BIN_NAME).write_bytes(bytes(raw))
            cue = snapshot_media(media / CUE_NAME, folder / "mounted")
            out = folder / "bios-dev-g5-cue"
            path = build_dev_g5.run_profile(out, "debug", cue)
            self.assertEqual(path.read_bytes(), self.g5)


if __name__ == "__main__":
    unittest.main()
