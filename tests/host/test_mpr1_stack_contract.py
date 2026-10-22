"""Issue #497: host tests for the MPR1 stack-bank contract (tools/mpr1_stack_contract.py). Synthetic only.

No emulator, no ROM, no reference BIOS. Checks that the contract cannot silently widen (only $F8 allowed),
that the reset value is never trusted, that TAM and direct SetMpr are judged on separate paths, and that the
same-instruction IRQ hole stays UNMEASURED. The mutation check fails if a permissive judge is substituted.
"""
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load_contract():
    spec = importlib.util.spec_from_file_location(
        "mpr1_stack_contract", ROOT / "tools" / "mpr1_stack_contract.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


c = load_contract()


class StackBankTableTest(unittest.TestCase):
    def test_only_wram_f8_is_allowed(self):
        self.assertEqual(c.ALLOWED_STACK_MPR1, frozenset({0xF8}))
        for value in range(256):
            with self.subTest(value=value):
                verdict, _ = c.check_stack_bank(value)
                self.assertEqual(verdict, c.PASS if value == 0xF8 else c.FAIL)

    def test_io_page_is_rejected(self):
        verdict, detail = c.check_stack_bank(0xFF)
        self.assertEqual(verdict, c.FAIL)
        self.assertIn("I/O", detail)

    def test_missing_evidence_is_blocked_not_pass(self):
        self.assertEqual(c.check_stack_bank(None)[0], c.BLOCKED)


class InitialStateTest(unittest.TestCase):
    def test_reset_value_is_undetermined(self):
        self.assertIsNone(c.RESET_MPR1)

    def test_no_recorded_init_is_blocked_even_if_reset_is_f8(self):
        self.assertEqual(c.check_initial_state(0xF8, None)[0], c.BLOCKED)
        self.assertEqual(c.check_initial_state(None, None)[0], c.BLOCKED)

    def test_recorded_init_of_f8_passes_regardless_of_reset(self):
        for reset in (None, 0x00, 0xFF, 0xF8):
            with self.subTest(reset=reset):
                self.assertEqual(c.check_initial_state(reset, 0xF8)[0], c.PASS)

    def test_recorded_init_other_than_f8_fails(self):
        self.assertEqual(c.check_initial_state(0xF8, 0xFF)[0], c.FAIL)


class PathDistinctionTest(unittest.TestCase):
    def test_reset_path_is_never_pass(self):
        for value in range(256):
            with self.subTest(value=value):
                self.assertNotEqual(c.check_mpr1_write(c.PATH_RESET, value, 0)[0], c.PASS)

    def test_tam_is_judged_by_bank_table(self):
        self.assertEqual(c.check_mpr1_write(c.PATH_TAM, 0xF8, 5)[0], c.PASS)
        self.assertEqual(c.check_mpr1_write(c.PATH_TAM, 0xFF, 5)[0], c.FAIL)

    def test_direct_setmpr_mid_run_fails_even_with_f8(self):
        self.assertEqual(c.check_mpr1_write(c.PATH_DIRECT_SETMPR, 0xF8, 1)[0], c.FAIL)

    def test_direct_setmpr_init_only_at_index_zero(self):
        self.assertEqual(c.check_mpr1_write(c.PATH_DIRECT_SETMPR, 0xF8, 0)[0], c.PASS)
        self.assertEqual(c.check_mpr1_write(c.PATH_DIRECT_SETMPR, 0xFF, 0)[0], c.FAIL)

    def test_unknown_path_is_blocked(self):
        self.assertEqual(c.check_mpr1_write("SOMETHING", 0xF8, 0)[0], c.BLOCKED)


class IrqWindowTest(unittest.TestCase):
    def test_same_instruction_overlap_is_unmeasured_never_pass(self):
        self.assertEqual(c.judge_irq_window(True, True)[0], c.UNMEASURED)
        self.assertEqual(c.IRQ_SAME_INSTRUCTION_STATUS, c.UNMEASURED)

    def test_no_overlap_passes(self):
        self.assertEqual(c.judge_irq_window(False, True)[0], c.PASS)
        self.assertEqual(c.judge_irq_window(True, False)[0], c.PASS)


class MutationCheckTest(unittest.TestCase):
    """A permissive bank table (allowing any value) must be caught by the table tests above."""

    def test_every_non_f8_value_is_a_fail_not_a_pass(self):
        for value in range(256):
            if value == 0xF8:
                continue
            with self.subTest(value=value):
                self.assertNotEqual(c.check_stack_bank(value)[0], c.PASS)


class DocConsistencyTest(unittest.TestCase):
    def test_contract_doc_states_the_same_allow_and_hole(self):
        text = (ROOT / "docs" / "497-mpr1-stack-contract.md").read_text(encoding="utf-8")
        self.assertIn("$F8", text)
        self.assertIn("UNMEASURED", text)


if __name__ == "__main__":
    unittest.main()
