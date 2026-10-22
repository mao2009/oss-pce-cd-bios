"""Issue #497: host tests for the MPR1 stack-bank contract (tools/mpr1_stack_contract.py). Synthetic only.

No emulator, no ROM, no reference BIOS. Checks that the contract cannot silently widen (only $F8 allowed),
that the reset value is never trusted, that TAM and direct SetMpr are judged on separate paths, and that the
same-instruction IRQ hole stays UNMEASURED. The suite does not swap the judge functions; a permissive judge is
caught by the full-value assertions (every value 0..255 and every CPU address), which were checked by hand in a
temporary copy (see the PR report).
"""
import importlib.util
import re
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
        self.assertEqual(c.check_initial_state(0xF8, None, 0)[0], c.BLOCKED)
        self.assertEqual(c.check_initial_state(None, None, 0)[0], c.BLOCKED)

    def test_recorded_init_of_f8_passes_regardless_of_reset(self):
        for reset in (None, 0x00, 0xFF, 0xF8):
            with self.subTest(reset=reset):
                self.assertEqual(c.check_initial_state(reset, 0xF8, 0)[0], c.PASS)

    def test_recorded_init_other_than_f8_fails(self):
        self.assertEqual(c.check_initial_state(0xF8, 0xFF, 0)[0], c.FAIL)

    def test_init_at_index_zero_passes_and_index_seven_fails(self):
        self.assertEqual(c.check_initial_state(None, 0xF8, 0)[0], c.PASS)
        self.assertEqual(c.check_initial_state(None, 0xF8, 7)[0], c.FAIL)

    def test_init_recorded_after_guest_instruction_is_not_pass(self):
        for index in range(1, 8):
            with self.subTest(index=index):
                self.assertNotEqual(c.check_initial_state(None, 0xF8, index)[0], c.PASS)


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


class PendingIrqTest(unittest.TestCase):
    """IRQ already pending at the instruction boundary, then a TAM, then HandleIRQ pushes to $21xx."""

    def test_pending_at_start_with_tam_change_is_unmeasured_not_pass(self):
        verdict, _ = c.judge_irq_window(True, False, irq_pending_at_start=True)
        self.assertEqual(verdict, c.UNMEASURED)

    def test_pending_and_newly_asserted_with_change_is_unmeasured(self):
        verdict, _ = c.judge_irq_window(True, True, irq_pending_at_start=True)
        self.assertEqual(verdict, c.UNMEASURED)

    def test_pending_without_mpr1_change_passes(self):
        self.assertEqual(c.judge_irq_window(False, False, irq_pending_at_start=True)[0], c.PASS)

    def test_no_irq_at_all_passes(self):
        self.assertEqual(c.judge_irq_window(True, False, irq_pending_at_start=False)[0], c.PASS)


class StackAddressNotationTest(unittest.TestCase):
    def test_only_2100_21ff_is_cpu_stack_page(self):
        for address in range(0x10000):
            with self.subTest(address=hex(address)):
                verdict, _ = c.check_stack_address(address)
                self.assertEqual(verdict, c.PASS if 0x2100 <= address <= 0x21FF else c.FAIL)

    def test_test_label_0100_is_not_a_pass_for_cpu_stack(self):
        for address in (0x0100, 0x01FC, 0x01FF):
            with self.subTest(address=hex(address)):
                verdict, detail = c.check_stack_address(address)
                self.assertEqual(verdict, c.FAIL)
                self.assertIn("test label", detail)

    def test_2100_judged_on_its_own_not_by_the_0100_label(self):
        self.assertEqual(c.check_stack_address(0x21FC)[0], c.PASS)
        self.assertEqual(c.check_stack_address(0x01FC)[0], c.FAIL)

    def test_missing_address_is_blocked(self):
        self.assertEqual(c.check_stack_address(None)[0], c.BLOCKED)


class NonAllowedValuesFailTest(unittest.TestCase):
    """Every value other than $F8 must be FAIL. A bank table that allows any extra value breaks this."""

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

    def test_doc_names_both_stack_notations_and_the_pending_case(self):
        text = (ROOT / "docs" / "497-mpr1-stack-contract.md").read_text(encoding="utf-8")
        self.assertIn("$2100", text)
        self.assertIn("$21FF", text)
        self.assertIn("テストラベル", text)
        self.assertIn("pending", text)


def doc_bank_verdicts(text):
    """Map each bank value in the doc's section 1 table (rows like | `$F8` | ... | verdict |) to PASS/FAIL."""
    verdicts = {}
    for line in text.splitlines():
        if not line.startswith("| `$"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        match = re.fullmatch(r"`\$([0-9A-F]{2})`", cells[0])
        if match and len(cells) >= 3:
            verdicts[int(match.group(1), 16)] = "PASS" if "PASS" in cells[2] else "FAIL"
    return verdicts


class DocCodeNumbersTest(unittest.TestCase):
    """The numbers the doc states must match the code: allowed bank, denied banks, CPU stack range, init index."""

    def setUp(self):
        self.text = (ROOT / "docs" / "497-mpr1-stack-contract.md").read_text(encoding="utf-8")

    def test_doc_pass_bank_is_the_code_allow_set(self):
        verdicts = doc_bank_verdicts(self.text)
        doc_allowed = {value for value, verdict in verdicts.items() if verdict == "PASS"}
        self.assertEqual(doc_allowed, set(c.ALLOWED_STACK_MPR1))

    def test_doc_denied_banks_are_denied_in_code(self):
        verdicts = doc_bank_verdicts(self.text)
        for value, verdict in verdicts.items():
            with self.subTest(value=hex(value)):
                self.assertEqual(c.check_stack_bank(value)[0] == c.PASS, verdict == "PASS")

    def test_doc_cpu_stack_range_matches_code(self):
        # Checked on the notation table row itself, not on any occurrence of the range elsewhere in the doc.
        low, high = c.CPU_STACK_PAGE
        rows = [line for line in self.text.splitlines() if line.startswith("| CPU スタックページ |")]
        self.assertEqual(len(rows), 1)
        cells = [cell.strip() for cell in rows[0].strip().strip("|").split("|")]
        self.assertEqual(cells[1], "`$%04X`〜`$%04X`" % (low, high))

    def test_doc_init_index_zero_matches_code(self):
        # The exact §2 rule sentence; checked by phrase, not by the bare words "命令番号 0".
        self.assertIn("命令番号 0 で記録された `$F8`", self.text)
        self.assertEqual(c.check_initial_state(None, c.HARNESS_INIT_MPR1, 0)[0], c.PASS)
        self.assertNotEqual(c.check_initial_state(None, c.HARNESS_INIT_MPR1, 1)[0], c.PASS)


if __name__ == "__main__":
    unittest.main()
