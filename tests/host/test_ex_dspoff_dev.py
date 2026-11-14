"""MODEL-ONLY host check of the DEV EX_DSPOFF fragment (slot $33, Issue #61).
Not the System Card ABI.

The fragment src/ex_dspoff.s reuses the DEV VDC CR display-family discipline of
src/overrides/slot_2E.s (EX_BGON): CR-low shadow at $2502, magic $A5 at $2503,
reserved $2504, MPR0=$FF window with SEI, AR left at 5, C=0 on ok / C=1 when the
shadow is uninitialized, balanced stack, no RAM outside owned cells. The mask
differs: AND #$3F clears BB+SB (bits 7+6) instead of setting BB. This test
checks a Python model of that contract and static checks on the assembly
source. It does NOT assemble or execute src/ex_dspoff.s, and the fragment is
NOT linked into the DEV ROM (ROM-layout ceiling: OVERRIDES ends $E7FB, 5 bytes
below the $E800 probe overlay; see docs/61-dspoff-contract.md). Keep host model
checks and real-core checks distinct.
"""
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "ex_dspoff.s"
SPEC = ROOT / "spec" / "dev_implementations.json"

sys.path.insert(0, str(ROOT / "tests" / "integration"))
import geargrafx_dev_cr as dev_cr  # noqa: E402

SET_MASK = 0x00
CLEAR_MASK = 0xC0


def dspoff_model(shadow_lo, magic):
    """Project-own DEV contract: initialized shadow -> clear BB+SB, C=0
    (SETWRT-style result convention); uninitialized shadow -> reject with C=1
    and no VDC or non-stack RAM writes (the call's own transient PHP/PHA/JSR
    stack slots are written and balanced, as documented in src/ex_dspoff.s).
    Returns (new_shadow_lo, new_magic, carry, vdc_written_or_None)."""
    if magic != 0xA5:
        return shadow_lo, magic, 1, None
    cleared = (shadow_lo & ~CLEAR_MASK) | SET_MASK
    return cleared, 0xA5, 0, cleared


class ModelContractTests(unittest.TestCase):
    def test_bb_sb_are_cleared_and_nothing_else_changes(self):
        for lo in (0x00, 0x40, 0x80, 0xC0, 0xFF, 0x4F, 0xB5):
            got_lo, got_magic, carry, written = dspoff_model(lo, 0xA5)
            self.assertEqual((got_lo, got_magic, carry, written),
                             ((lo & 0x3F, 0xA5, 0, lo & 0x3F)))
            self.assertEqual(got_lo & 0x3F, lo & 0x3F)
            self.assertEqual(got_lo & 0xC0, 0)

    def test_uninitialized_shadow_is_rejected_without_side_effects(self):
        for lo, magic in ((0x00, 0x00), (0xFF, 0x00), (0x12, 0x00),
                          (0x00, 0x5A), (0xC0, 0x00), (0xFF, 0xFF)):
            got_lo, got_magic, carry, written = dspoff_model(lo, magic)
            self.assertEqual((got_lo, got_magic, carry, written),
                             (lo, magic, 1, None))

    def test_applying_twice_is_stable(self):
        lo, _, _, _ = dspoff_model(0xF2, 0xA5)
        self.assertEqual(dspoff_model(lo, 0xA5), (lo, 0xA5, 0, lo))

    def test_desync_is_out_of_contract_not_an_error(self):
        # $A5 marks "seeded", not "matches hardware". A caller that writes CR
        # directly and re-calls gets the stale shadow applied with C=0 (no
        # error reported) -- hence the re-seed discipline.
        stale, magic, carry, written = dspoff_model(0x00, 0xA5)
        self.assertEqual((carry, written), (0, 0x00))
        stale2, _, carry2, written2 = dspoff_model(0xC0, 0xA5)
        self.assertEqual((carry2, written2), (0, 0x00))

    def test_complements_bgon_mask(self):
        # DSPOFF clears exactly the bits BGON/SPRON set: BB ($80) + SB ($40).
        for lo in (0x00, 0x3F, 0x40, 0x80, 0xC0, 0xFF):
            cleared, _, _, _ = dspoff_model(lo, 0xA5)
            self.assertEqual(cleared, lo & 0x3F)
            self.assertEqual(cleared | 0xC0, lo | 0xC0)


class RamDiffScopeTests(unittest.TestCase):
    """The loaded/cold RAM diff owns exactly STORE, shadow, sentinel and the
    used stack slots -- the shared geargrafx_dev_cr oracle with the DSPOFF
    masks (clear $C0, set $00). An unused stack-page byte changing must be
    reported, while the deepest allowed slot ($21(SP-4)) must pass."""

    HALT = 0xF000
    A, X, Y, P, SP = 0x12, 0x34, 0x56, 0x34, 0xFF

    def _rows(self, loaded_mut=None):
        cold = bytearray(0x2000)
        base = dev_cr.STORE - dev_cr.WRAM_BASE
        cold[base:base + 5] = bytes([self.A, self.P, self.X, self.Y, self.SP])
        cold[base + 7] = 0x00  # STORE_MPR0 == caller MPR0
        cold[dev_cr.SHADOW_LO - dev_cr.WRAM_BASE] = 0x30
        cold[dev_cr.SHADOW_MAGIC - dev_cr.WRAM_BASE] = dev_cr.SHADOW_VALID
        for i in range(dev_cr.SENTINEL_LEN):
            cold[dev_cr.SENTINEL - dev_cr.WRAM_BASE + i] = dev_cr.sentinel_byte(i)
        for i in range(self.SP - 4, self.SP + 1):
            cold[0x100 + i] = 0xAA
        loaded = bytearray(cold)
        for i in range(8):
            loaded[base + i] = 0x00
        loaded[dev_cr.SHADOW_LO - dev_cr.WRAM_BASE] = 0x00
        loaded[dev_cr.SHADOW_MAGIC - dev_cr.WRAM_BASE] = 0x00
        for i in range(dev_cr.SENTINEL_LEN):
            loaded[dev_cr.SENTINEL - dev_cr.WRAM_BASE + i] = 0x00
        for i in range(self.SP - 4, self.SP + 1):
            loaded[0x100 + i] = 0x00
        if loaded_mut is not None:
            addr, value = loaded_mut
            loaded[addr - dev_cr.WRAM_BASE] = value
        cold_row = {"phase": "cold", "pc": self.HALT, "sp": self.SP,
                    "mpr": [0x00, 0xF8, 0, 0, 0, 0, 0, 0],
                    "vdc_ar": 5, "vdc_cr": 0x0030,
                    "work_ram_hex": bytes(cold).hex()}
        loaded_row = {"phase": "loaded", "work_ram_hex": bytes(loaded).hex()}
        return [loaded_row, cold_row]

    def _check(self, rows, seed_shadow=True):
        return dev_cr.check("synthetic", rows, self.HALT, self.A, self.X,
                            self.Y, self.P, self.SP, 0xF0, 0x00,
                            SET_MASK, CLEAR_MASK, seed_shadow=seed_shadow)

    def test_clean_diff_passes(self):
        self._check(self._rows())

    def test_reject_path_clean_diff_passes(self):
        # Unseeded run against the zero pattern: CR stays, shadow untouched.
        # The stored P carries C=1 (reject), folded with N/Z from A.
        cold = bytearray(0x2000)
        base = dev_cr.STORE - dev_cr.WRAM_BASE
        cold[base:base + 5] = bytes([self.A, self.P | 0x01, self.X, self.Y, self.SP])
        cold[base + 7] = 0x00
        cold[dev_cr.SHADOW_LO - dev_cr.WRAM_BASE] = 0xFF
        cold[dev_cr.SHADOW_MAGIC - dev_cr.WRAM_BASE] = 0x00
        for i in range(dev_cr.SENTINEL_LEN):
            cold[dev_cr.SENTINEL - dev_cr.WRAM_BASE + i] = dev_cr.sentinel_byte(i)
        for i in range(self.SP - 3, self.SP + 1):
            cold[0x100 + i] = 0xAA
        loaded = bytearray(cold)
        for i in range(8):
            loaded[base + i] = 0x00
        loaded[dev_cr.SHADOW_LO - dev_cr.WRAM_BASE] = 0x00
        loaded[dev_cr.SHADOW_MAGIC - dev_cr.WRAM_BASE] = 0x00
        for i in range(dev_cr.SENTINEL_LEN):
            loaded[dev_cr.SENTINEL - dev_cr.WRAM_BASE + i] = 0x00
        for i in range(self.SP - 3, self.SP + 1):
            loaded[0x100 + i] = 0x00
        rows = [{"phase": "loaded", "work_ram_hex": bytes(loaded).hex()},
                {"phase": "cold", "pc": self.HALT, "sp": self.SP,
                 "mpr": [0x00, 0xF8, 0, 0, 0, 0, 0, 0],
                 "vdc_ar": 5, "vdc_cr": 0x0000,
                 "work_ram_hex": bytes(cold).hex()}]
        dev_cr.check("synthetic", rows, self.HALT, self.A, self.X, self.Y,
                     self.P, self.SP, 0x00, 0x00, SET_MASK, CLEAR_MASK,
                     seed_shadow=False)

    def test_unused_stack_page_write_is_reported(self):
        with self.assertRaisesRegex(ValueError, r"out-of-contract RAM write.*\$2100"):
            self._check(self._rows(loaded_mut=(0x2100, 0xFF)))

    def test_deepest_allowed_slot_passes(self):
        self._check(self._rows(loaded_mut=(0x21FB, 0xFF)))


class AssemblyStaticTests(unittest.TestCase):
    def _code(self):
        lines = []
        for line in SRC.read_text(encoding="utf-8").splitlines():
            code = line.split(";", 1)[0]
            if not code.strip() or code.strip().startswith((".", "ex_dspoff:")):
                continue
            lines.append(code.strip())
        return lines

    def test_window_discipline_and_mask(self):
        code = self._code()
        self.assertIn("sei", code)
        self.assertIn("tam #$01", code)
        self.assertIn("tma #$01", code)
        self.assertIn("sta a:$0000", code)
        self.assertIn("lda #$05", code)
        self.assertIn("sta a:$0002", code)
        self.assertEqual(sum(1 for line in code if "sta a:$0000" in line), 1)
        self.assertFalse(any(re.match(r"(lda|sta|stx|ldx)\s+\$000[023]\b", line) for line in code),
                         "VDC access must use the a: absolute form (zero page would hit $20xx)")
        self.assertFalse(any(re.match(r"lda\s+a:\$000[23]\b", line) for line in code))
        self.assertFalse(any("$0003" in line for line in code))
        self.assertIn("lda $2502", code)
        self.assertIn("lda $2503", code)
        self.assertIn("cmp #$A5", code)
        self.assertIn("sta $2502", code)
        # DSPOFF clears BB+SB; it must AND #$3F exactly once and never ORA.
        self.assertIn("and #$3F", code)
        self.assertEqual(sum(1 for line in code if "and #$3F" in line), 1)
        self.assertFalse(any("ora " in line for line in code),
                         "DSPOFF must clear bits (AND), never set them (ORA)")
        self.assertEqual(code.count("sta a:$0002"), 1)
        self.assertEqual(code[-1], "rts")
        self.assertIn(".export ex_dspoff", SRC.read_text(encoding="utf-8"))

    def test_push_depth_matches_the_sp05_bound(self):
        code = self._code()
        pushes = [line.split()[0] for line in code if line.split()[0] in ("php", "phy", "phx", "pha")]
        self.assertEqual(pushes, ["php", "pha", "pha"])
        pulls = [line.split()[0] for line in code if line.split()[0] in ("plp", "ply", "plx", "pla")]
        self.assertEqual(pulls, ["pla", "plp", "pla", "pla", "plp"])

    def test_sync_discipline_is_documented(self):
        src = SRC.read_text(encoding="utf-8")
        for phrase in ("initialization marker", "re-seed $2502/$2503",
                       "out of contract", "slot $3A",
                       "generally callable ONLY",
                       "caller_SP-4", "balanced before RTS"):
            self.assertIn(phrase, src)

    def test_no_zero_page_ram_stack_or_branch_side_effects(self):
        code = "\n".join(self._code()).lower()
        self.assertNotRegex(code, r"\$20[0-9a-f]{2}")
        self.assertNotRegex(code, r"\$21[0-9a-f]{2}")
        for mnemonic in ("bra", "bne", "bcc", "bcs", "jmp", "jsr", "rti",
                         "brk", "inc", "dec", "asl", "lsr", "rol", "ror"):
            self.assertNotIn(mnemonic, code.split())
        self.assertEqual(sum(1 for tok in code.split() if tok == "beq"), 1)

    def test_fragment_is_not_linked_into_the_dev_rom(self):
        # The ceiling guard: no src/overrides/slot_33.s may exist on this
        # branch, so tools/build_rom.py must not list $33 as overridden.
        sys.path.insert(0, str(ROOT / "tools"))
        from build_rom import overrides
        self.assertNotIn(0x33, set(overrides()))
        self.assertFalse((ROOT / "src" / "overrides" / "slot_33.s").exists())


class SpecContractConsistencyTests(unittest.TestCase):
    def _entry(self):
        data = json.loads(SPEC.read_text(encoding="utf-8"))
        return next(s for s in data["slots"] if s.get("candidate_name") == "EX_DSPOFF")

    def test_ledger_entry_stays_unwired(self):
        entry = self._entry()
        self.assertIsNone(entry["implementation"])
        self.assertEqual(entry["abi_contract"]["status"], "UNKNOWN")
        self.assertEqual(entry["host_evidence"], [])
        self.assertEqual(entry["real_core_evidence"], [])
        self.assertEqual(entry["state"], "NOT_READY")
        self.assertIsNone(entry["approval"]["approved_by"])

    def test_implementation_counts_match_spec(self):
        data = json.loads(SPEC.read_text(encoding="utf-8"))
        implemented = [s for s in data["slots"] if s.get("implementation")]
        self.assertEqual(len(implemented), 18)
        self.assertEqual(len(data["slots"]) - len(implemented), 63)


if __name__ == "__main__":
    unittest.main()
