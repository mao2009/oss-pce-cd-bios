"""MODEL-ONLY host check of the DEV EX_BGON contract (slot $2E, Issue #56).
Not the System Card ABI.

This slot sets the HuC6270 background-blanking bit (VDC CR R05, bit 7),
preserves every other CR bit and register (A/X/Y/P with C=0), balances the
stack and touches nothing else. This host test checks a Python model of that
contract and static checks on the assembly source. It does NOT assemble or
execute src/overrides/slot_2E.s by itself.

The assembled slot_2E.s IS executed by the real-core integration test
tests/integration/geargrafx_dev_bgon.py (pinned cc65 + Geargrafx), registered in
the `dev-integration` target. Keep host model checks and real-core checks distinct.
"""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "overrides" / "slot_2E.s"
SPEC = ROOT / "spec" / "dev_implementations.json"


def cr_model(lo, hi):
    """Project-own DEV contract: set BB (bit 7), keep every other bit."""
    return (lo | 0x80, hi)


class ModelContractTests(unittest.TestCase):
    def test_bb_is_set_and_nothing_else_changes(self):
        for lo, hi in ((0x00, 0x00), (0x40, 0xA5), (0x0F, 0x5A), (0xFF, 0xFF)):
            got_lo, got_hi = cr_model(lo, hi)
            self.assertEqual(got_lo, lo | 0x80)
            self.assertEqual(got_hi, hi)
            self.assertEqual(got_lo & 0x7F, lo & 0x7F)

    def test_high_byte_is_never_touched(self):
        for hi in (0x00, 0xA5, 0x5A, 0xFF):
            self.assertEqual(cr_model(0x55, hi)[1], hi)

    def test_applying_twice_is_stable(self):
        lo, hi = cr_model(0x12, 0x34)
        self.assertEqual(cr_model(lo, hi), (lo, hi))


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
        # CR select + low access must use absolute (a:) form, never zero page.
        self.assertIn("sta a:$0000", code)
        self.assertIn("lda #$05", code)
        self.assertIn("lda a:$0002", code)
        self.assertIn("sta a:$0002", code)
        self.assertFalse(any(re.match(r"(lda|sta|stx|ldx)\s+\$000[023]\b", line) for line in code),
                         "VDC access must use the a: absolute form (zero page would hit $20xx)")
        # The high byte is never touched: no read or write of $0003 anywhere.
        self.assertFalse(any("$0003" in line for line in code))
        self.assertIn("ora #$80", code)
        self.assertEqual(code[-1], "rts")
        self.assertIn(".export api_slot_2E", SRC.read_text(encoding="utf-8"))

    def test_push_depth_matches_the_sp07_bound(self):
        code = self._code()
        pushes = [line.split()[0] for line in code if line.split()[0] in ("php", "phy", "phx", "pha")]
        # flags, Y, X, A, MPR0 = 5 simultaneous pushes (+2 JSR bytes = SP >= $07).
        self.assertEqual(len(pushes), 5)
        pulls = [line.split()[0] for line in code if line.split()[0] in ("plp", "ply", "plx", "pla")]
        self.assertEqual(len(pulls), 5)

    def test_no_zero_page_ram_stack_or_branch_side_effects(self):
        code = "\n".join(self._code()).lower()
        self.assertNotRegex(code, r"\$20[0-9a-f]{2}")
        self.assertNotRegex(code, r"\$21[0-9a-f]{2}")
        for mnemonic in ("bra", "bne", "beq", "bcc", "bcs", "jmp", "jsr", "rti",
                         "brk", "inc", "dec", "asl", "lsr", "rol", "ror"):
            self.assertNotIn(mnemonic, code.split())


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
