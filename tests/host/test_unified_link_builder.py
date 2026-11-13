"""Host tests for the triple-link $03 wiring (G5 P0 next step, Issue #787).

Proves, without cc65 or the pinned core, that the unified experimental BIOS
recipe now carries CD_BASE $02 proto + CD_READ link $03 + EX_DSPOFF $33 as
REAL processing in one image:

- slot-$03 table redirect ($E009 -> cd_read_link_entry, fail-closed),
- API-area windows cover all three patches and stay disjoint,
- manifest carries the cd_read_link native block (slot/entry/link_entry),
- harness-D model derives (0x02, 0x03, 0x33) and verifies the triple ROM,
  while the pre-triple two-block manifest still validates (backward compat).

NOT genuine ABI evidence. NOT compatibility. Real arrival/return and the
$2800 transfer run on the pinned core in
tests/integration/test_unified_link_realcore.py. No emulator here.
"""
import hashlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import build_experimental_bios_boot as U  # noqa: E402
import g5_progress_model as M  # noqa: E402

SLOT02_ADDR = 0xE10A
UNIMPL_ADDR = 0xE245
PROTO_ADDR = 0xF000
STUB33_ADDR = 0xE234
BODY33_ADDR = 0xF187
STUB03_ADDR = 0xE238
LINK_ADDR = 0xF1A0


def _bank(slot02=SLOT02_ADDR, unimpl=UNIMPL_ADDR,
          stub33=STUB33_ADDR, body33=BODY33_ADDR,
          stub03=STUB03_ADDR, link=LINK_ADDR):
    bank = bytearray(b"\xFF" * 8192)
    off = slot02 - 0xE000
    bank[off:off + 5] = bytes((0xA2, 0x02, 0x4C, unimpl & 0xFF, (unimpl >> 8) & 0xFF))
    bank[U._SLOT33_TABLE:U._SLOT33_TABLE + 3] = bytes((0x4C, stub33 & 0xFF, stub33 >> 8))
    bank[body33 - 0xE000:body33 - 0xE000 + 2] = bytes((0x08, 0x48))
    bank[U._SLOT03_TABLE:U._SLOT03_TABLE + 3] = bytes((0x4C, stub03 & 0xFF, stub03 >> 8))
    bank[link - 0xE000:link - 0xE000 + 2] = bytes((0x08, 0x48))
    return bytes(bank)


def _triple_manifest(rom_sha, **overrides):
    man = {
        "rom_name": "experimental-dev-only-not-compatible-syscard-sized.pce",
        "rom_sha256": rom_sha, "rom_bytes": M.ROM_BYTES,
        "pinned_cc65": M.PINNED_CC65, "pinned_geargrafx": M.PINNED_GEARGRAFX,
        "release_eligible": False, "dev_callable": False,
        "branch": "agent-d/triple-link-g5", "head": "d" * 40,
        "labels": {"api_slot_02": SLOT02_ADDR, "api_slot_03": STUB03_ADDR,
                   "api_slot_33": STUB33_ADDR, "api_unimplemented": UNIMPL_ADDR,
                   "cd_base_proto_entry": PROTO_ADDR, "experimental_slot_33": BODY33_ADDR,
                   "cd_read_link_entry": LINK_ADDR,
                   "slot_02_table_entry": 0xE006, "slot_03_table_entry": 0xE009,
                   "slot_33_table_entry": 0xE099},
        "symbols": {"api_unimplemented": UNIMPL_ADDR},
        "selection_contract": {"lba": 7, "media_identity": "executed-id"},
        "cd_base_proto": {"slot": "$02", "entry": "$E006", "proto_entry": "$F000",
                          "patch": {"target": "api_slot_02", "length_bytes": 5}},
        "experimental_slot_33": {"table_entry": "0xE099", "body": "$F187"},
        "cd_read_link": {"slot": "$03", "entry": "$E009", "table_entry": "0xE009",
                         "link_entry": "$F1A0"},
    }
    man.update(overrides)
    return man


def _triple_rom(api_slot_02=SLOT02_ADDR, proto=PROTO_ADDR,
                body33=BODY33_ADDR, link=LINK_ADDR):
    data = bytearray(M.ROM_BYTES)
    data[0x02 * 3:0x02 * 3 + 3] = bytes((0x4C, api_slot_02 & 0xFF, (api_slot_02 >> 8) & 0xFF))
    data[api_slot_02 - 0xE000:api_slot_02 - 0xE000 + 5] = bytes(
        (0x4C, proto & 0xFF, (proto >> 8) & 0xFF, 0xEA, 0xEA))
    data[proto - 0xE000] = 0x08  # PHP: real proto body start
    data[0x33 * 3:0x33 * 3 + 3] = bytes((0x4C, body33 & 0xFF, (body33 >> 8) & 0xFF))
    data[body33 - 0xE000:body33 - 0xE000 + 2] = bytes((0x08, 0x48))
    data[0x03 * 3:0x03 * 3 + 3] = bytes((0x4C, link & 0xFF, (link >> 8) & 0xFF))
    data[link - 0xE000:link - 0xE000 + 2] = bytes((0x08, 0x48))  # PHP, PHA: link prologue
    return bytes(data)


class Slot03RedirectTests(unittest.TestCase):
    def test_table_constant_is_e009(self):
        self.assertEqual(U._SLOT03_TABLE, 3 * 0x03)
        self.assertEqual(0xE000 + U._SLOT03_TABLE, 0xE009)

    def test_patch_labels_cover_stub_and_link(self):
        self.assertEqual(U._SLOT03_PATCH_LABELS, ("api_slot_03", "cd_read_link_entry"))

    def test_required_carries_link_entry(self):
        self.assertIn("cd_read_link_entry", U.REQUIRED)

    def test_redirect_points_table_at_link(self):
        bank = _bank()
        out = U._redirect_slot03_to_link(bank, STUB03_ADDR, LINK_ADDR)
        self.assertEqual(bytes(out[U._SLOT03_TABLE:U._SLOT03_TABLE + 3]),
                         bytes((0x4C, LINK_ADDR & 0xFF, LINK_ADDR >> 8)))
        diffs = [i for i in range(len(bank)) if bank[i] != out[i]]
        self.assertEqual(diffs, [U._SLOT03_TABLE + 1, U._SLOT03_TABLE + 2])
        self.assertTrue(set(diffs) <= U.unified_api_windows(SLOT02_ADDR))

    def test_redirect_refuses_unexpected_table(self):
        bank = bytearray(_bank())
        bank[U._SLOT03_TABLE + 1] = 0x00
        with self.assertRaises(ValueError):
            U._redirect_slot03_to_link(bytes(bank), STUB03_ADDR, LINK_ADDR)

    def test_redirect_refuses_stub_outside_fallback(self):
        with self.assertRaises(ValueError):
            U._redirect_slot03_to_link(_bank(), 0xD000, LINK_ADDR)

    def test_redirect_refuses_link_outside_boot(self):
        with self.assertRaises(ValueError):
            U._redirect_slot03_to_link(_bank(), STUB03_ADDR, 0xE500)

    def test_redirect_refuses_link_without_prologue(self):
        bank = bytearray(_bank())
        bank[LINK_ADDR - 0xE000] = 0xEA
        with self.assertRaises(ValueError):
            U._redirect_slot03_to_link(bytes(bank), STUB03_ADDR, LINK_ADDR)

    def test_patch_order_is_documented(self):
        source = (ROOT / "tools" / "build_experimental_bios_boot.py").read_text(encoding="utf-8")
        self.assertIn("slot-$02 first, then slot-$33, then slot-$03", source)
        self.assertIn("_redirect_slot03_to_link", source)
        self.assertIn("src/cd_read_link.s", source)


class TripleWindowsTests(unittest.TestCase):
    def test_windows_cover_all_three(self):
        windows = U.unified_api_windows(SLOT02_ADDR)
        self.assertEqual(len(windows), 5 + 3 + 3)
        self.assertTrue(set(range(U._SLOT03_TABLE, U._SLOT03_TABLE + 3)) <= windows)
        self.assertTrue(set(range(U._SLOT33_TABLE, U._SLOT33_TABLE + 3)) <= windows)

    def test_full_ordered_union(self):
        original = _bank()
        patched = U._patch_slot02_to_proto(original, SLOT02_ADDR, UNIMPL_ADDR, PROTO_ADDR)
        patched = U._redirect_slot33_to_body(patched, STUB33_ADDR, BODY33_ADDR)
        patched = U._redirect_slot03_to_link(patched, STUB03_ADDR, LINK_ADDR)
        allowed = U.unified_api_windows(SLOT02_ADDR)
        api_diff = [i for i in range(0x1000) if patched[i] != original[i]]
        self.assertTrue(set(api_diff) <= allowed)
        for window in (set(range(SLOT02_ADDR - 0xE000, SLOT02_ADDR - 0xE000 + 5)),
                       set(range(U._SLOT33_TABLE, U._SLOT33_TABLE + 3)),
                       set(range(U._SLOT03_TABLE, U._SLOT03_TABLE + 3))):
            self.assertTrue(set(api_diff) & window)


class TripleModelTests(unittest.TestCase):
    def test_derive_native_slots_triple(self):
        man = _triple_manifest("0" * 64)
        self.assertEqual(M.derive_native_slots(man), (0x02, 0x03, 0x33))

    def test_validate_triple_manifest(self):
        man = _triple_manifest("a" * 64)
        checked = M.validate_experimental_manifest(man)
        self.assertEqual(checked["slots"], (0x02, 0x03, 0x33))
        self.assertTrue(checked["native"])

    def test_two_block_manifest_still_validates(self):
        man = _triple_manifest("b" * 64)
        del man["cd_read_link"]
        checked = M.validate_experimental_manifest(man)
        self.assertEqual(checked["slots"], (0x02, 0x33))

    def test_slot_entry_mismatch_blocked(self):
        man = _triple_manifest("c" * 64)
        man["cd_read_link"] = {"slot": "$03", "entry": "$E099", "table_entry": "0xE009",
                               "link_entry": "$F1A0"}
        with self.assertRaises(ValueError) as cm:
            M.derive_native_slots(man)
        self.assertIn("BLOCKED", str(cm.exception))

    def test_verify_triple_positive(self):
        rom = _triple_rom()
        man = _triple_manifest(hashlib.sha256(rom).hexdigest())
        M.verify_d_rom_bytes(rom, man)
        M.verify_d_rom_slot_targets(rom, man)  # no raise

    def test_wrong_link_dest_rejected(self):
        rom = bytearray(_triple_rom())
        rom[0x03 * 3:0x03 * 3 + 3] = bytes((0x4C, 0x00, 0xE2))
        rom = bytes(rom)
        man = _triple_manifest(hashlib.sha256(rom).hexdigest())
        with self.assertRaises(ValueError) as cm:
            M.verify_d_rom_slot_targets(rom, man)
        self.assertIn("$03", str(cm.exception))

    def test_link_fake_rts_rejected(self):
        rom = bytearray(_triple_rom())
        rom[LINK_ADDR - 0xE000] = 0x60  # link body is RTS: not real processing
        rom = bytes(rom)
        man = _triple_manifest(hashlib.sha256(rom).hexdigest())
        with self.assertRaises(ValueError) as cm:
            M.verify_d_rom_slot_targets(rom, man)
        self.assertIn("fake RTS", str(cm.exception))

    def test_link_equals_stub_rejected(self):
        # Table and manifest agree on the stub itself: the wiring claims the
        # unimplemented halt as real processing -> BLOCKED as stub equality.
        rom = bytearray(_triple_rom())
        rom[0x03 * 3:0x03 * 3 + 3] = bytes((0x4C, UNIMPL_ADDR & 0xFF, UNIMPL_ADDR >> 8))
        rom = bytes(rom)
        man = _triple_manifest(hashlib.sha256(rom).hexdigest(),
                               cd_read_link={"slot": "$03", "entry": "$E009",
                                             "table_entry": "0xE009",
                                             "link_entry": "$%04X" % UNIMPL_ADDR})
        man["labels"]["cd_read_link_entry"] = UNIMPL_ADDR
        with self.assertRaises(ValueError) as cm:
            M.verify_d_rom_slot_targets(rom, man)
        self.assertIn("unimplemented stub", str(cm.exception))

    def test_missing_link_entry_blocked(self):
        rom = _triple_rom()
        man = _triple_manifest(hashlib.sha256(rom).hexdigest())
        del man["cd_read_link"]["link_entry"]
        del man["labels"]["cd_read_link_entry"]
        with self.assertRaises(ValueError) as cm:
            M.verify_d_rom_slot_targets(rom, man)
        self.assertIn("BLOCKED", str(cm.exception))

    def test_manifest_self_claim_alone_never_passes(self):
        # Declared wiring + matching SHA but a one-stage-wrong ROM (table
        # straight past the two-stage $02 stub) must NOT verify.
        rom = bytearray(_triple_rom())
        rom[0x02 * 3:0x02 * 3 + 3] = bytes((0x4C, 0x00, 0xF0))  # table -> proto directly
        rom = bytes(rom)
        man = _triple_manifest(hashlib.sha256(rom).hexdigest())
        with self.assertRaises(ValueError):
            M.verify_d_rom_slot_targets(rom, man)


if __name__ == "__main__":
    unittest.main()
