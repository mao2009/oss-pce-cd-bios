"""Build-structure contracts for the experimental slot $33 wiring (Issue #61; G5 Agent B).

The normal DEV ROM keeps its nonreturning $33 stub: OVERRIDES ends at $E7FB with
5 bytes of slack below the $E800 probe-overlay window, so no
src/overrides/slot_33.s exists (see docs/61-dspoff-contract.md). The experimental
System-Card-sized DEV image instead links the project-own DEV EX_DSPOFF fragment
(src/ex_dspoff.s, single source via .include) into its BOOT region and redirects
its own $E099 table entry to that body (tools/build_experimental_bios_boot.py).

No emulator and no real-core run here; the pinned real-core execution lives in
tests/integration/test_ex_dspoff_real_core.py. NOT the reference System Card ABI:
reference mapping stays HYPOTHESIS, ABI VERIFIED stays 0/81, DEV_CALLABLE stays 0,
release_eligible stays False. Commercial media is never touched (NOT_RUN).
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests" / "integration"))

from tools.environment import requires_cc65  # noqa: E402

EXP_BOOT = ROOT / "src" / "experimental_bios_boot.s"
FRAGMENT = ROOT / "src" / "ex_dspoff.s"
SLOT = 0x33
TABLE_AT = 3 * SLOT  # file offset of the $E099 table entry (table starts at $E000)
OVERLAY_START, OVERLAY_END = 0x0800, 0x1000  # file offsets of CPU $E800-$EFFF


def _parse_labels(text):
    import re
    result = {}
    for address, name in re.findall(r"^al ([0-9A-Fa-f]{6}) \.([A-Za-z_][A-Za-z_0-9]*)$", text, re.M):
        value = int(address, 16)
        if result.setdefault(name, value) != value:
            raise ValueError(f"contradictory addresses for label {name}")
    return result


class ExperimentalSlot33SourceTests(unittest.TestCase):
    """cc65-independent source contracts: the wiring is experimental-only."""

    def test_entry_lives_in_experimental_boot_and_reuses_the_fragment(self):
        source = EXP_BOOT.read_text(encoding="utf-8")
        self.assertIn(".export experimental_slot_33", source)
        self.assertIn("experimental_slot_33:", source)
        self.assertIn('.include "ex_dspoff.s"', source)
        # Single source of truth: the processing logic is not duplicated here.
        self.assertNotIn("and #$3F", source)
        self.assertNotIn("ora #$80", source)
        for phrase in ("NOT a compatible System Card BIOS", "release_eligible=false",
                       "outside the builder's first-4KiB identity window"):
            self.assertIn(phrase, source)

    def test_fragment_export_survives_for_the_include(self):
        self.assertIn(".export ex_dspoff", FRAGMENT.read_text(encoding="utf-8"))

    def test_no_normal_rom_override_for_slot_33(self):
        from build_rom import overrides
        self.assertNotIn(SLOT, set(overrides()))
        self.assertFalse((ROOT / "src" / "overrides" / "slot_33.s").exists())

    def test_spec_ledger_still_describes_the_normal_rom(self):
        # The ledger tracks the normal DEV ROM (no $33 override there); the
        # experimental wiring is recorded in the experimental manifest instead.
        data = json.loads((ROOT / "spec" / "dev_implementations.json").read_text(encoding="utf-8"))
        entry = next(s for s in data["slots"] if s.get("candidate_name") == "EX_DSPOFF")
        self.assertIsNone(entry["implementation"])
        self.assertEqual(entry["abi_contract"]["status"], "UNKNOWN")
        self.assertEqual(entry["host_evidence"], [])
        self.assertEqual(entry["real_core_evidence"], [])
        self.assertEqual(entry["state"], "NOT_READY")
        self.assertIsNone(entry["approval"]["approved_by"])
        implemented = [s for s in data["slots"] if s.get("implementation")]
        self.assertEqual(len(implemented), 16)
        self.assertEqual(len(data["slots"]) - len(implemented), 65)

    def test_api_slot_mapping_is_unchanged(self):
        slots = json.loads((ROOT / "spec" / "api_slots.json").read_text(encoding="utf-8"))["slots"]
        entry = next(s for s in slots if s.get("id") == SLOT)
        self.assertEqual(entry["candidate_name"], "EX_DSPOFF")
        # The table layout ($E000 + 3 * slot) makes slot $33 the $E099 entry.
        self.assertEqual(0xE000 + 3 * SLOT, 0xE099)


@requires_cc65
class ExperimentalSlot33BuildTests(unittest.TestCase):
    """The wired experimental image vs the untouched normal ROM (needs pinned cc65)."""

    @classmethod
    def setUpClass(cls):
        from build_rom import build
        from build_experimental_bios_boot import (ROM_NAME, build_experimental_bios_boot,
                                                  build_experimental_payload)
        cls.tmp = Path(tempfile.mkdtemp(prefix="pce-dspoff-real-host-"))
        cls.addClassCleanup(shutil.rmtree, cls.tmp, ignore_errors=True)
        # Normal DEV ROM (must stay byte-identical to a fresh rebuild).
        cls.normal_path = build(cls.tmp / "normal", root=ROOT)
        cls.normal = cls.normal_path.read_bytes()
        # Minimal synthetic medium: the wiring must never depend on private media.
        sys.path.insert(0, str(ROOT / "tests" / "integration"))
        import make_cd_image
        from experimental_track_selection import selection_from_cue, snapshot_media
        single_dir = cls.tmp / "single"
        make_cd_image.build_image(single_dir)
        cue = single_dir / make_cd_image.CUE_NAME
        payload, _ = build_experimental_payload(cls.tmp / "payload", "debug")
        raw = bytearray((cue.parent / make_cd_image.BIN_NAME).read_bytes())
        from build_cdload_exec import USER_OFFSET, encode_sector
        raw[0 * make_cd_image.RAW_BYTES + USER_OFFSET:0 * make_cd_image.RAW_BYTES + USER_OFFSET + 2048] = encode_sector(0, payload)
        (cue.parent / make_cd_image.BIN_NAME).write_bytes(bytes(raw))
        cue = snapshot_media(cue, cls.tmp / "mounted")
        cls.cue_path = cue
        if str(cue.resolve()).startswith(str((ROOT / "rom").resolve())):
            raise ValueError("synthetic medium must not live under ./rom")
        out = cls.tmp / "exp"
        cls.exp_path = build_experimental_bios_boot(cls.normal_path, out, "debug", cue, payload)
        cls.exp = cls.exp_path.read_bytes()
        cls.labels = _parse_labels((out / "experimental-boot.lbl").read_text(encoding="utf-8"))
        cls.manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))

    def test_normal_rom_keeps_the_nonreturning_stub(self):
        stub = self.labels["api_slot_33"] if "api_slot_33" in self.labels else None
        table = bytes(self.normal[TABLE_AT:TABLE_AT + 3])
        self.assertEqual(table[0], 0x4C)  # JMP absolute
        target = table[1] | (table[2] << 8)
        self.assertEqual(bytes(self.normal[target - 0xE000:target - 0xE000 + 2]), bytes((0xA2, SLOT)))
        self.assertEqual(self.normal[target - 0xE000 + 2], 0x4C)  # LDX #$33; JMP handler
        if stub is not None:
            self.assertEqual(target, stub)

    def test_normal_rom_probe_overlay_stays_padding(self):
        self.assertEqual(self.normal[OVERLAY_START:OVERLAY_END], b"\xff" * 0x800)

    def test_experimental_table_redirects_only_documented_windows(self):
        # UNION adaptation (G5 Agent A+B, issue #783): the union image wires
        # BOTH slot $33 (this test's redirect) and slot $02 (Agent-A 5-byte
        # JMP-proto patch). The fail-closed invariant is unchanged in spirit:
        # the API-area diff vs the normal DEV ROM is EXACTLY the two
        # documented windows, nothing else. Agent B's source and PR are
        # untouched; only this branch's expectation follows the union image.
        body = self.labels["experimental_slot_33"]
        self.assertTrue(0xF000 <= body < 0xFFF6, f"body ${body:04X} outside BOOT")
        table = bytes(self.exp[TABLE_AT:TABLE_AT + 3])
        self.assertEqual(table, bytes((0x4C, body & 0xFF, body >> 8)))
        slot02 = self.labels["api_slot_02"]
        proto = self.labels["cd_base_proto_entry"]
        masked = bytearray(self.exp[:0x1000])
        masked[TABLE_AT:TABLE_AT + 3] = self.normal[TABLE_AT:TABLE_AT + 3]
        masked[slot02 - 0xE000:slot02 - 0xE000 + 5] = \
            self.normal[slot02 - 0xE000:slot02 - 0xE000 + 5]
        self.assertEqual(bytes(masked), self.normal[:0x1000])
        # The second window is the union's documented slot-$02 JMP+NOP pad.
        self.assertEqual(bytes(self.exp[slot02 - 0xE000:slot02 - 0xE000 + 5]),
                         bytes((0x4C, proto & 0xFF, (proto >> 8) & 0xFF, 0xEA, 0xEA)))

    def test_experimental_body_matches_the_fragment_prologue(self):
        body = self.labels["experimental_slot_33"]
        off = body - 0xE000
        self.assertEqual(bytes(self.exp[off:off + 2]), bytes((0x08, 0x48)))  # PHP, PHA
        self.assertEqual(self.labels.get("ex_dspoff"), body)

    def test_experimental_manifest_records_the_wiring_and_gates(self):
        slot33 = self.manifest["experimental_slot_33"]
        body = self.labels["experimental_slot_33"]
        self.assertEqual(slot33["table_entry"], "0xE099")
        self.assertEqual(slot33["body"], f"{body:#06x}")
        self.assertIn("NOT the reference ABI", slot33["contract"])
        self.assertFalse(self.manifest["release_eligible"])
        self.assertEqual(self.manifest["system_card_abi"], "NO_CLAIM")
        self.assertFalse(self.manifest["dev_callable"])
        self.assertFalse(self.manifest["private_media_used"])
        self.assertEqual(self.manifest["symbols"]["experimental_slot_33"], body)

    def test_experimental_overlay_window_stays_padding(self):
        self.assertEqual(self.exp[OVERLAY_START:OVERLAY_END], b"\xff" * 0x800)

    def test_commercial_media_is_not_run(self):
        # Public tests stay reproducible without private media: the only medium
        # consumed is the synthetic cue built above (./rom is never read).
        self.assertTrue(str(self.cue_path.resolve()).startswith(str(self.tmp.resolve())))
        self.assertFalse(str(self.cue_path.resolve()).startswith(str((ROOT / "rom").resolve())))


if __name__ == "__main__":
    unittest.main()
