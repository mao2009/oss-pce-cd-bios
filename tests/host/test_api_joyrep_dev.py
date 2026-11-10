"""MODEL-ONLY host check of the DEV EX_JOYREP contract (slot $22, Issue #44). Not the System Card ABI.

This host test checks a Python model of the documented contract (plain RTS: every
register, flag, stack, MPR and RAM cell is preserved, no I/O) and static checks on
the assembly source. It does NOT assemble or execute src/overrides/slot_22.s by itself.

The assembled slot_22.s IS executed by the real-core integration test
tests/integration/geargrafx_dev_joyrep.py (pinned cc65 + Geargrafx), which is registered in
the `dev-integration` target. Keep host model checks and real-core checks distinct.
"""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "overrides" / "slot_22.s"
SPEC = ROOT / "spec" / "dev_implementations.json"


class DevJoyrepModel:
    """Model of the slot: a plain RTS. Guest state in, identical guest state out."""

    @staticmethod
    def call(state):
        return dict(state)


class ModelContractTests(unittest.TestCase):
    def test_every_register_flag_stack_and_ram_cell_is_preserved(self):
        before = {"a": 0x12, "x": 0x34, "y": 0x56, "p": 0xB4, "sp": 0x06,
                  "mpr": [0x00, 0xF8, 0x01, 0x02, 0x03, 0x04, 0x05, 0x00],
                  "ram": bytes(range(256))}
        self.assertEqual(DevJoyrepModel.call(before), before)

    def test_boundary_register_values_are_preserved(self):
        for regs in ({"a": 0x00, "x": 0x00, "y": 0x00, "p": 0x00, "sp": 0x02},
                     {"a": 0xFF, "x": 0xFF, "y": 0xFF, "p": 0xFF, "sp": 0xFF}):
            state = dict(regs, mpr=list(range(8)), ram=bytes(256))
            self.assertEqual(DevJoyrepModel.call(state), state)

    def test_no_output_or_error_signal_exists(self):
        self.assertIsNone(DevJoyrepModel.call({}).get("error"))
        self.assertIsNone(DevJoyrepModel.call({}).get("output"))


class AssemblyStaticTests(unittest.TestCase):
    """The source must be exactly one RTS: no reads, writes, branches, jumps or stack use."""

    def _code_lines(self):
        lines = []
        for line in SRC.read_text(encoding="utf-8").splitlines():
            code = line.split(";", 1)[0].strip()
            if not code or code.startswith(".") or code.startswith("api_slot_22:"):
                continue
            lines.append(code)
        return lines

    def test_body_is_a_single_rts(self):
        self.assertEqual(self._code_lines(), ["rts"])

    def test_no_memory_operand_or_io_or_stack_mnemonic(self):
        text = "\n".join(self._code_lines()).lower()
        self.assertNotRegex(text, r"\$")
        for mnemonic in ("lda", "sta", "ldx", "stx", "ldy", "sty", "stz",
                         "pha", "pla", "php", "plp", "phx", "phy", "plx", "ply",
                         "sei", "cli", "jmp", "jsr", "bra", "rti",
                         "tam", "tma", "tia", "tai", "tii", "tin", "tdd"):
            self.assertNotIn(mnemonic, text.split())

    def test_export_and_segment(self):
        text = SRC.read_text(encoding="utf-8")
        self.assertIn('.export api_slot_22', text)
        self.assertIn('api_slot_22:', text)
        self.assertIn('.segment "OVERRIDES"', text)


class SpecContractConsistencyTests(unittest.TestCase):
    """spec/dev_implementations.json must agree with the implementation."""

    def _entry(self):
        data = json.loads(SPEC.read_text(encoding="utf-8"))
        return next(s for s in data["slots"] if s.get("candidate_name") == "EX_JOYREP")

    def test_implementation_source_matches(self):
        entry = self._entry()
        self.assertEqual(entry["implementation"]["source"], "src/overrides/slot_22.s")
        self.assertTrue((ROOT / entry["implementation"]["source"]).is_file())
        self.assertEqual(entry["abi_contract"]["status"], "PROJECT_SPEC_ONLY")

    def test_evidence_lists_both_harnesses(self):
        entry = self._entry()
        self.assertIn("tests/host/test_api_joyrep_dev.py", entry["host_evidence"])
        self.assertIn("tests/integration/geargrafx_dev_joyrep.py", entry["real_core_evidence"])

    def test_not_ready_without_owner_approval(self):
        entry = self._entry()
        self.assertEqual(entry["state"], "NOT_READY")
        self.assertIsNone(entry["approval"]["approved_by"])

    def test_abi_contract_note_names_plain_rts_and_no_side_effects(self):
        entry = self._entry()
        note = entry["abi_contract"]["note"]
        self.assertIn("RTS", note)
        self.assertNotIn("UNKNOWN", note.split(";", 1)[0] if ";" in note else note)


if __name__ == "__main__":
    unittest.main()
