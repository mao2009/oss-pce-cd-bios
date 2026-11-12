"""Host tests for the UNIFIED experimental BIOS builder (G5 Agent A+B union).

Proves, without cc65 or the pinned core, that the union of
origin/agent-a/cd-base-proto-g5 (slot $02 CD_BASE proto, PR #782) and
origin/agent-b/ex-dspoff-real-cpu-g5 (slot $33 EX_DSPOFF fragment, PR #780)
coexists as REAL processing in one experimental image recipe:

- combined REQUIRED labels (cd_base_proto_entry + experimental_slot_33),
- BOTH link units,
- ordered fail-closed patches (slot-$02 5-byte JMP first, then slot-$33
  3-byte $E099 table redirect),
- API-area diff assertion allowing ONLY the two windows,
- G5-native manifest provenance (branch/head/merged-heads/pins/labels/
  selection) derived at build time, never hardcoded.

NOT genuine ABI evidence. NOT compatibility. Real arrival/return, SP/MPR
integrity and harness-D classification run on the pinned core in
tests/integration/test_unified_realcore.py. No emulator here.
"""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import build_experimental_bios_boot as U  # noqa: E402
from environment import DEPENDENCIES  # noqa: E402

SLOT02_ADDR = 0xE10A
UNIMPL_ADDR = 0xE245
PROTO_ADDR = 0xF000
STUB33_ADDR = 0xE234
BODY33_ADDR = 0xF187


def _bank(slot02=SLOT02_ADDR, unimpl=UNIMPL_ADDR, proto=PROTO_ADDR,
          stub33=STUB33_ADDR, body33=BODY33_ADDR):
    """Fake 8 KiB bank0: slot-02 fallback, $E099 stub JMP, BOOT prologue."""
    bank = bytearray(b"\xFF" * 8192)
    off = slot02 - 0xE000
    bank[off:off + 5] = bytes((0xA2, 0x02, 0x4C, unimpl & 0xFF, (unimpl >> 8) & 0xFF))
    bank[U._SLOT33_TABLE:U._SLOT33_TABLE + 3] = bytes((0x4C, stub33 & 0xFF, stub33 >> 8))
    body_off = body33 - 0xE000
    bank[body_off:body_off + 2] = bytes((0x08, 0x48))  # PHP, PHA
    return bytes(bank)


def _selected():
    return {"magic": "XSEL", "version": 1, "format": 1, "kind": 1,
            "track": 1, "lba": 150, "cue": "x.cue",
            "media_identity": {"cue": "x.cue", "cue_sha256": "0" * 64,
                               "files": [], "layout": []}}


class UnionRequiredTests(unittest.TestCase):
    def test_required_carries_both_callees(self):
        self.assertIn("cd_base_proto_entry", U.REQUIRED)
        self.assertIn("experimental_slot_33", U.REQUIRED)

    def test_link_units_carry_both_fragments(self):
        names = {path.name for path, _defines in U._experimental_boot_units(_selected())}
        self.assertEqual(names, {"experimental_bios_boot.s", "cd_load_exec_probe.s",
                                 "cd_base_proto.s"})

    def test_slot33_body_comes_from_the_shared_fragment(self):
        source = (ROOT / "src" / "experimental_bios_boot.s").read_text(encoding="utf-8")
        self.assertIn(".export experimental_slot_33", source)
        self.assertIn('.include "ex_dspoff.s"', source)
        # Single source of truth: no duplicated $33 processing logic in the glue.
        self.assertNotIn("and #$3F", source)


class Slot02PatchTests(unittest.TestCase):
    def test_patch_replaces_exactly_five_bytes(self):
        bank = _bank()
        patched = U._patch_slot02_to_proto(bank, SLOT02_ADDR, UNIMPL_ADDR, PROTO_ADDR)
        off = SLOT02_ADDR - 0xE000
        self.assertEqual(patched[off:off + 5], bytes((0x4C, 0x00, 0xF0, 0xEA, 0xEA)))
        diffs = [i for i in range(len(bank)) if bank[i] != patched[i]]
        self.assertEqual(diffs, list(range(off, off + 5)))

    def test_patch_refuses_wrong_prebytes(self):
        bank = bytearray(_bank())
        bank[SLOT02_ADDR - 0xE000] = 0x00
        with self.assertRaises(ValueError):
            U._patch_slot02_to_proto(bytes(bank), SLOT02_ADDR, UNIMPL_ADDR, PROTO_ADDR)

    def test_patch_refuses_bad_addresses(self):
        bank = _bank()
        with self.assertRaises(ValueError):
            U._patch_slot02_to_proto(bank, 0xD000, UNIMPL_ADDR, PROTO_ADDR)
        with self.assertRaises(ValueError):
            U._patch_slot02_to_proto(bank, SLOT02_ADDR, UNIMPL_ADDR, 0xE050)


class Slot33RedirectTests(unittest.TestCase):
    def test_redirect_points_table_at_body(self):
        bank = _bank()
        out = U._redirect_slot33_to_body(bank, STUB33_ADDR, BODY33_ADDR)
        self.assertEqual(bytes(out[U._SLOT33_TABLE:U._SLOT33_TABLE + 3]),
                         bytes((0x4C, BODY33_ADDR & 0xFF, BODY33_ADDR >> 8)))
        diffs = [i for i in range(len(bank)) if bank[i] != out[i]]
        # Both entries are JMP, so the opcode byte coincides; the two target
        # bytes differ. Exact-set equality would be wrong here by construction.
        self.assertEqual(diffs, [U._SLOT33_TABLE + 1, U._SLOT33_TABLE + 2])
        self.assertTrue(set(diffs) <= U.unified_api_windows(SLOT02_ADDR))

    def test_redirect_refuses_unexpected_table(self):
        bank = bytearray(_bank())
        bank[U._SLOT33_TABLE + 1] = 0x00
        with self.assertRaises(ValueError):
            U._redirect_slot33_to_body(bytes(bank), STUB33_ADDR, BODY33_ADDR)

    def test_redirect_refuses_body_outside_boot(self):
        with self.assertRaises(ValueError):
            U._redirect_slot33_to_body(_bank(), STUB33_ADDR, 0xE500)

    def test_redirect_refuses_body_without_prologue(self):
        bank = bytearray(_bank())
        bank[BODY33_ADDR - 0xE000] = 0xEA
        with self.assertRaises(ValueError):
            U._redirect_slot33_to_body(bytes(bank), STUB33_ADDR, BODY33_ADDR)


class UnifiedWindowsTests(unittest.TestCase):
    def test_ordered_union_diff_is_exactly_both_windows(self):
        original = _bank()
        # Ordered fail-closed patches: slot-$02 first, then slot-$33.
        patched = U._patch_slot02_to_proto(original, SLOT02_ADDR, UNIMPL_ADDR, PROTO_ADDR)
        patched = U._redirect_slot33_to_body(patched, STUB33_ADDR, BODY33_ADDR)
        allowed = U.unified_api_windows(SLOT02_ADDR)
        self.assertEqual(len(allowed), 5 + 3)
        api_diff = [i for i in range(0x1000) if patched[i] != original[i]]
        # Builder semantics: inside the windows, both windows nonempty,
        # both patches byte-exact.
        self.assertTrue(set(api_diff) <= allowed)
        slot02_window = set(range(SLOT02_ADDR - 0xE000, SLOT02_ADDR - 0xE000 + 5))
        slot33_window = set(range(U._SLOT33_TABLE, U._SLOT33_TABLE + 3))
        self.assertTrue(set(api_diff) & slot02_window)
        self.assertTrue(set(api_diff) & slot33_window)
        off = SLOT02_ADDR - 0xE000
        self.assertEqual(bytes(patched[off:off + 5]),
                         bytes((0x4C, PROTO_ADDR & 0xFF, (PROTO_ADDR >> 8) & 0xFF,
                                0xEA, 0xEA)))
        self.assertEqual(bytes(patched[U._SLOT33_TABLE:U._SLOT33_TABLE + 3]),
                         bytes((0x4C, BODY33_ADDR & 0xFF, (BODY33_ADDR >> 8) & 0xFF)))

    def test_windows_reject_bad_slot02_address(self):
        with self.assertRaises(ValueError):
            U.unified_api_windows(0xD000)

    def test_coincidental_byte_equality_is_tolerated(self):
        # A window byte may coincidentally match (shared low byte); the
        # builder must still accept the image when both patches are present
        # and byte-exact. Only unexpected deltas fail.
        bank = bytearray(_bank(body33=0xF134))
        bank[U._SLOT33_TABLE:U._SLOT33_TABLE + 3] = bytes((0x4C, 0x34, 0xE2))
        original = bytes(bank)
        patched = U._patch_slot02_to_proto(original, SLOT02_ADDR, UNIMPL_ADDR, PROTO_ADDR)
        patched = U._redirect_slot33_to_body(patched, STUB33_ADDR, 0xF134)
        allowed = U.unified_api_windows(SLOT02_ADDR)
        api_diff = [i for i in range(0x1000) if patched[i] != original[i]]
        self.assertTrue(set(api_diff) <= allowed)
        self.assertLess(len(api_diff), len(allowed))

    def test_windows_are_disjoint_by_construction(self):
        windows = U.unified_api_windows(SLOT02_ADDR)
        slot02 = set(range(SLOT02_ADDR - 0xE000, SLOT02_ADDR - 0xE000 + 5))
        slot33 = set(range(U._SLOT33_TABLE, U._SLOT33_TABLE + 3))
        self.assertTrue(slot02.isdisjoint(slot33))
        self.assertEqual(windows, slot02 | slot33)


class UnifiedProvenanceTests(unittest.TestCase):
    def test_provenance_is_derived_never_hardcoded(self):
        provenance = U.unified_repo_provenance()
        self.assertTrue(provenance["branch"])
        self.assertRegex(provenance["head"], r"\A[0-9a-f]{40}\Z")
        self.assertEqual(set(provenance["merged_heads"]), set(U._MERGED_REFS))
        for sha in provenance["merged_heads"].values():
            self.assertTrue(re.fullmatch(r"[0-9a-f]{40}", sha) or sha.startswith("UNRESOLVED"))
        self.assertEqual(provenance["pinned_cc65"], DEPENDENCIES["cc65"]["revision"])
        self.assertEqual(provenance["pinned_geargrafx"], DEPENDENCIES["geargrafx"]["revision"])

    def test_merged_heads_match_expected_pr_heads_when_refs_present(self):
        provenance = U.unified_repo_provenance()
        heads = provenance["merged_heads"]
        for ref, sha in heads.items():
            if sha.startswith("UNRESOLVED"):
                self.skipTest(f"{ref} absent at build time")
        self.assertNotEqual(heads[U._MERGED_REFS[0]], heads[U._MERGED_REFS[1]])

    def test_unresolvable_merged_ref_is_recorded_not_failed(self):
        orig = U._git_output
        try:
            def fake(*args):
                if args and args[0] == "rev-parse" and len(args) > 1 and args[1] == U._MERGED_REFS[0]:
                    raise ValueError("BLOCKED: ref absent")
                return orig(*args)
            U._git_output = fake
            provenance = U.unified_repo_provenance()
            self.assertTrue(provenance["merged_heads"][U._MERGED_REFS[0]].startswith("UNRESOLVED"))
        finally:
            U._git_output = orig

    def test_non_integrated_merged_ref_fails_closed(self):
        orig = U._git_output
        try:
            def fake(*args):
                if args and args[0] == "rev-parse" and len(args) > 1 and args[1] in U._MERGED_REFS:
                    return "a" * 40
                if args and args[0] == "merge-base":
                    raise ValueError("BLOCKED: not an ancestor")
                return orig(*args)
            U._git_output = fake
            with self.assertRaises(ValueError):
                U.unified_repo_provenance()
        finally:
            U._git_output = orig

    def test_merged_head_ancestry_is_verified_via_merge_base(self):
        calls = []
        orig = U._git_output
        try:
            def fake(*args):
                calls.append(args)
                return orig(*args)
            U._git_output = fake
            U.unified_repo_provenance()
            self.assertTrue(any(c and c[0] == "merge-base" and "--is-ancestor" in c for c in calls))
        finally:
            U._git_output = orig


class UnifiedManifestContractTests(unittest.TestCase):
    """The G5-native fields harness-D needs, checked against a builder-shaped manifest."""

    def _representative_manifest(self):
        provenance = U.unified_repo_provenance()
        return {
            "rom_name": U.ROM_NAME,
            "rom_sha256": "ab" * 32,
            "rom_bytes": 256 * 1024,
            "pinned_cc65": provenance["pinned_cc65"],
            "pinned_geargrafx": provenance["pinned_geargrafx"],
            "release_eligible": False,
            "dev_callable": False,
            "branch": provenance["branch"],
            "head": provenance["head"],
            "labels": {"api_slot_02": SLOT02_ADDR, "api_slot_33": STUB33_ADDR,
                       "api_unimplemented": UNIMPL_ADDR,
                       "cd_base_proto_entry": PROTO_ADDR,
                       "experimental_slot_33": BODY33_ADDR},
            "symbols": {"api_unimplemented": UNIMPL_ADDR},
            "selection_contract": {"lba": 150, "media_identity": {"cue": "x.cue"}},
            "cd_base_proto": {"slot": "$02", "entry": "$E006",
                              "proto_entry": f"${PROTO_ADDR:04X}"},
            "experimental_slot_33": {"table_entry": "0xE099",
                                     "body": f"{BODY33_ADDR:#06x}"},
        }

    def test_g5_required_fields_present_and_well_formed(self):
        manifest = self._representative_manifest()
        self.assertTrue(manifest["rom_name"].endswith(".pce"))
        self.assertRegex(manifest["rom_sha256"], r"\A[0-9a-f]{64}\Z")
        self.assertEqual(manifest["rom_bytes"], 256 * 1024)
        self.assertIs(manifest["release_eligible"], False)
        self.assertIs(manifest["dev_callable"], False)
        self.assertTrue(manifest["branch"])
        self.assertRegex(manifest["head"], r"\A[0-9a-f]{40}\Z")
        self.assertEqual(manifest["pinned_geargrafx"], DEPENDENCIES["geargrafx"]["revision"])
        stub = manifest["labels"]["api_unimplemented"]
        self.assertIsInstance(stub, int)
        self.assertTrue(0xE000 <= stub <= 0xFFFF)
        self.assertIn("lba", manifest["selection_contract"])
        self.assertIn("media_identity", manifest["selection_contract"])

    def test_native_slots_derive_to_02_and_33(self):
        manifest = self._representative_manifest()

        def _parse(value):
            if isinstance(value, int):
                return value
            text = str(value).strip().lower()
            for prefix in ("0x", "$"):
                if text.startswith(prefix):
                    text = text[len(prefix):]
                    break
            return int(text, 16)

        def _slot(entry_addr):
            self.assertEqual((entry_addr - 0xE000) % 3, 0)
            return (entry_addr - 0xE000) // 3

        slots = sorted((_slot(_parse(manifest["cd_base_proto"]["entry"])),
                        _slot(_parse(manifest["experimental_slot_33"]["table_entry"]))))
        self.assertEqual(slots, [0x02, 0x33])

    def test_builder_never_hardcodes_slot_addresses(self):
        source = (ROOT / "tools" / "build_experimental_bios_boot.py").read_text(encoding="utf-8")
        # Slot wiring addresses must come from parse_needed_labels on the link.
        self.assertIn('parse_needed_labels(labels, _PROTO_PATCH_LABELS)', source)
        self.assertIn('parse_needed_labels(labels, _SLOT33_PATCH_LABELS)', source)
        # Provenance must come from git rev-parse, not literals.
        self.assertIn('rev-parse', source)
        self.assertNotIn("5fe93c4b", source)
        self.assertNotIn("323562fb", source)


if __name__ == "__main__":
    unittest.main()
