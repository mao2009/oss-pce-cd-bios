"""MODEL-ONLY host check of the DEV EX_BGON contract (slot $2E, Issue #56).
Not the System Card ABI.

This slot sets the HuC6270 background-blanking bit (VDC CR R05, bit 7),
preserves every other CR bit and register (A/X/Y/P; C=0 on ok, C=1 when the
shadow is uninitialized), balances the stack and touches nothing else.
Because the pinned core returns stale data for CR port reads (MAJOR-1), the
routine owns CR low in the work-RAM shadow at $2502 (magic $A5 at $2503)
instead of reading the port. This host test checks a Python model of that
contract and static checks on the assembly source. It does NOT assemble or
execute src/overrides/slot_2E.s by itself.

The assembled slot_2E.s IS executed by the real-core integration test
tests/integration/geargrafx_dev_bgon.py (pinned cc65 + Geargrafx), registered in
the `dev-integration` target and judged against the independent vdc_cr probe
observation. Keep host model checks and real-core checks distinct.
"""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "overrides" / "slot_2E.s"
SPEC = ROOT / "spec" / "dev_implementations.json"


def cr_model(shadow_lo, magic):
    """Project-own DEV contract: initialized shadow -> set BB, C=0 (SETWRT-style
    result convention); uninitialized shadow -> reject with C=1 and no writes.
    Returns (new_shadow_lo, new_magic, carry, vdc_written_or_None)."""
    if magic != 0xA5:
        return shadow_lo, magic, 1, None
    return shadow_lo | 0x80, 0xA5, 0, shadow_lo | 0x80


class ModelContractTests(unittest.TestCase):
    def test_bb_is_set_and_nothing_changes(self):
        for lo in (0x00, 0x40, 0x0F, 0xFF):
            got_lo, got_magic, carry, written = cr_model(lo, 0xA5)
            self.assertEqual((got_lo, got_magic, carry, written), (lo | 0x80, 0xA5, 0, lo | 0x80))
            self.assertEqual(got_lo & 0x7F, lo & 0x7F)

    def test_uninitialized_shadow_is_rejected_without_side_effects(self):
        for lo, magic in ((0x00, 0x00), (0xFF, 0x00), (0x12, 0x00), (0x00, 0x5A)):
            got_lo, got_magic, carry, written = cr_model(lo, magic)
            self.assertEqual((got_lo, got_magic, carry, written), (lo, magic, 1, None))

    def test_applying_twice_is_stable(self):
        lo, _, _, _ = cr_model(0x12, 0xA5)
        self.assertEqual(cr_model(lo, 0xA5), (lo, 0xA5, 0, lo))


class AssemblyStaticTests(unittest.TestCase):
    def _code(self):
        lines = []
        for line in SRC.read_text(encoding="utf-8").splitlines():
            code = line.split(";", 1)[0]
            if not code.strip() or code.strip().startswith((".", "api_slot_2E:")):
                continue
            lines.append(code.strip())
        return lines

    def test_window_discipline_and_mask(self):
        code = self._code()
        text = "\n".join(code)
        self.assertIn("sei", code)
        self.assertIn("tam #$01", code)
        self.assertIn("tma #$01", code)
        # CR select + low write must use absolute (a:) form, never zero page.
        self.assertIn("sta a:$0000", code)
        self.assertIn("lda #$05", code)
        self.assertIn("sta a:$0002", code)
        # MEDIUM-1: AR is selected once (R05) and left there; no restore select.
        self.assertEqual(sum(1 for line in code if "sta a:$0000" in line), 1)
        self.assertFalse(any(re.match(r"(lda|sta|stx|ldx)\s+\$000[023]\b", line) for line in code),
                         "VDC access must use the a: absolute form (zero page would hit $20xx)")
        # MAJOR-1: the port is never READ (pinned core returns stale data); the
        # shadow is. No read of $0002/$0003 anywhere; no write of $0003 either.
        self.assertFalse(any(re.match(r"lda\s+a:\$000[23]\b", line) for line in code))
        self.assertFalse(any("$0003" in line for line in code))
        # Shadow protocol: magic check, BB set, publish-then-write ordering.
        self.assertIn("lda $2502", code)
        self.assertIn("lda $2503", code)
        self.assertIn("cmp #$A5", code)
        self.assertIn("sta $2502", code)
        self.assertIn("ora #$80", code)
        self.assertEqual(code.count("sta a:$0002"), 1)
        self.assertEqual(code[-1], "rts")
        self.assertIn(".export api_slot_2E", SRC.read_text(encoding="utf-8"))

    def test_push_depth_matches_the_sp05_bound(self):
        code = self._code()
        # X/Y are never written, so only flags, A and MPR0 are saved: 3 pushes
        # (+2 JSR bytes = SP >= $05). No phy/phx/ply/plx may appear.
        pushes = [line.split()[0] for line in code if line.split()[0] in ("php", "phy", "phx", "pha")]
        self.assertEqual(pushes, ["php", "pha", "pha"])
        pulls = [line.split()[0] for line in code if line.split()[0] in ("plp", "ply", "plx", "pla")]
        self.assertEqual(pulls, ["pla", "plp", "pla", "pla", "plp"])

    def test_no_zero_page_ram_stack_or_branch_side_effects(self):
        code = "\n".join(self._code()).lower()
        self.assertNotRegex(code, r"\$20[0-9a-f]{2}")
        self.assertNotRegex(code, r"\$21[0-9a-f]{2}")
        for mnemonic in ("bra", "bne", "bcc", "bcs", "jmp", "jsr", "rti",
                         "brk", "inc", "dec", "asl", "lsr", "rol", "ror"):
            self.assertNotIn(mnemonic, code.split())
        # Exactly one branch: the shadow-validity beq before the MPR0 window.
        self.assertEqual(sum(1 for tok in code.split() if tok == "beq"), 1)


class SpecContractConsistencyTests(unittest.TestCase):
    def _entry(self):
        data = json.loads(SPEC.read_text(encoding="utf-8"))
        return next(s for s in data["slots"] if s.get("candidate_name") == "EX_BGON")

    def test_ledger_entry_matches(self):
        entry = self._entry()
        self.assertEqual(entry["implementation"]["source"], "src/overrides/slot_2E.s")
        self.assertTrue((ROOT / entry["implementation"]["source"]).is_file())
        self.assertEqual(entry["abi_contract"]["status"], "PROJECT_SPEC_ONLY")
        self.assertEqual(entry["host_evidence"], ["tests/host/test_api_cr_dev.py"])
        self.assertEqual(entry["real_core_evidence"], ["tests/integration/geargrafx_dev_bgon.py"])
        self.assertEqual(entry["state"], "NOT_READY")
        self.assertIsNone(entry["approval"]["approved_by"])

    def test_sibling_display_slots_remain_stubs(self):
        data = json.loads(SPEC.read_text(encoding="utf-8"))
        by_name = {s["candidate_name"]: s for s in data["slots"]}
        for name in ("EX_BGOFF", "EX_SPRON", "EX_SPROFF", "EX_DSPON", "EX_DSPOFF"):
            with self.subTest(name=name):
                self.assertIsNone(by_name[name]["implementation"])
                self.assertEqual(by_name[name]["abi_contract"]["status"], "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
