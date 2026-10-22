"""Issue #497: host tests for the MPR1 stack-bank contract (tools/mpr1_stack_contract.py). Synthetic only.

No emulator, no ROM, no reference BIOS. Checks that the contract cannot silently widen (only $F8 allowed),
that the reset value is never trusted, that TAM and direct SetMpr are judged on separate paths, and that the
same-instruction IRQ hole stays UNMEASURED. The IRQ input is three-valued: an unobserved IRQ state
(IRQ_UNKNOWN) is missing evidence and must never become PASS. The suite does not swap the judge functions;
a permissive judge is caught by the full-value assertions (every value 0..255 and every CPU address), by the
IRQ decision table (every documented row is re-checked against the code), and by the invalid-input cases,
which were checked by hand in a temporary copy (see the PR report).
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
    """Three-valued IRQ input: True / False are observations, IRQ_UNKNOWN means "not observed"."""

    def test_same_instruction_overlap_is_unmeasured_never_pass(self):
        self.assertEqual(c.judge_irq_window(True, True, False)[0], c.UNMEASURED)
        self.assertEqual(c.IRQ_SAME_INSTRUCTION_STATUS, c.UNMEASURED)

    def test_no_overlap_passes(self):
        self.assertEqual(c.judge_irq_window(False, True, False)[0], c.PASS)
        self.assertEqual(c.judge_irq_window(True, False, False)[0], c.PASS)


class IrqUnknownStateTest(unittest.TestCase):
    """A missing IRQ observation is missing evidence, never a negative observation (never PASS)."""

    def test_unknown_constant_is_the_explicit_not_observed_value(self):
        self.assertIsNone(c.IRQ_UNKNOWN)

    def test_mpr1_change_with_unknown_irq_state_is_not_pass(self):
        for asserted, pending in ((c.IRQ_UNKNOWN, False), (False, c.IRQ_UNKNOWN),
                                   (c.IRQ_UNKNOWN, c.IRQ_UNKNOWN)):
            with self.subTest(asserted=asserted, pending=pending):
                verdict, detail = c.judge_irq_window(True, asserted, pending)
                self.assertNotEqual(verdict, c.PASS)
                self.assertEqual(verdict, c.BLOCKED)
                self.assertIn("not observed", detail)

    def test_mpr1_change_with_observed_negatives_passes(self):
        self.assertEqual(c.judge_irq_window(True, False, False)[0], c.PASS)

    def test_mpr1_change_with_observed_irq_overlap_is_unmeasured(self):
        self.assertEqual(c.judge_irq_window(True, True, False)[0], c.UNMEASURED)
        self.assertEqual(c.judge_irq_window(True, False, True)[0], c.UNMEASURED)

    def test_observed_overlap_dominates_an_unobserved_side(self):
        # The hole is confirmed by the observation, so the missing side does not change the verdict.
        self.assertEqual(c.judge_irq_window(True, True, c.IRQ_UNKNOWN)[0], c.UNMEASURED)
        self.assertEqual(c.judge_irq_window(True, c.IRQ_UNKNOWN, True)[0], c.UNMEASURED)

    def test_no_mpr1_change_keeps_the_meaningful_pass(self):
        self.assertEqual(c.judge_irq_window(False, False, False)[0], c.PASS)
        self.assertEqual(c.judge_irq_window(False, True, False)[0], c.PASS)
        self.assertEqual(c.judge_irq_window(False, False, True)[0], c.PASS)
        self.assertEqual(c.judge_irq_window(False, True, True)[0], c.PASS)

    def test_unknown_state_is_not_promoted_to_pass_even_without_an_mpr1_change(self):
        for asserted, pending in ((c.IRQ_UNKNOWN, False), (False, c.IRQ_UNKNOWN),
                                   (c.IRQ_UNKNOWN, c.IRQ_UNKNOWN)):
            with self.subTest(asserted=asserted, pending=pending):
                self.assertNotEqual(c.judge_irq_window(False, asserted, pending)[0], c.PASS)

    def test_omitted_pending_argument_is_unknown_not_false(self):
        # The default means "not observed": it must never be read as "no pending IRQ".
        verdict, _ = c.judge_irq_window(True, False)
        self.assertNotEqual(verdict, c.PASS)
        self.assertEqual(verdict, c.BLOCKED)
        verdict, _ = c.judge_irq_window(False, False)
        self.assertNotEqual(verdict, c.PASS)

    def test_invalid_irq_types_are_blocked_not_raised(self):
        for bad in ("yes", "", 1, 0, 2, -1, 0.0, 1.0, [], [True], {}, b"1", object()):
            with self.subTest(value=bad):
                for args in ((True, bad, False), (True, False, bad), (False, bad, bad)):
                    with self.subTest(args=args):
                        try:
                            verdict, detail = c.judge_irq_window(*args)
                        except Exception as error:   # the contract must return a verdict, never raise
                            self.fail("judge_irq_window%r raised %r" % (args, error))
                        self.assertEqual(verdict, c.BLOCKED)
                        self.assertNotEqual(verdict, c.PASS)

    def test_invalid_mpr1_changed_flag_is_blocked_not_raised(self):
        for bad in ("yes", 1, 0, None, [], object()):
            with self.subTest(value=bad):
                try:
                    verdict, _ = c.judge_irq_window(bad, False, False)
                except Exception as error:   # the contract must return a verdict, never raise
                    self.fail("judge_irq_window raised %r" % (error,))
                self.assertEqual(verdict, c.BLOCKED)


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

    def test_every_byte_value_is_judged_by_the_allow_table(self):
        # The same table through the TAM path: only $F8 may pass.
        for value in range(256):
            with self.subTest(value=value):
                verdict, _ = c.check_mpr1_write(c.PATH_TAM, value, 5)
                self.assertEqual(verdict, c.PASS if value == 0xF8 else c.FAIL)


class InvalidInputTest(unittest.TestCase):
    """Invalid input must fail closed with a structured verdict: never an exception, never PASS.

    The policy (docs/497-mpr1-stack-contract.md §2.1): a wrong type (str, bool, None, list, ...) is
    BLOCKED because the evidence cannot be read; a readable number that breaks the contract (out of the
    0..255 byte range, not $F8, not instruction index 0, a negative index) is FAIL.
    """

    WRONG_TYPES = (None, "F8", "", True, False, 1.0, [], {}, b"\xF8", object())
    OUT_OF_RANGE = (-1, 0x100, 0x1FF)

    def assert_verdict(self, call, expected, *args):
        """Call a judge function; it must return a verdict and never raise."""
        try:
            verdict, detail = call(*args)
        except Exception as error:   # the contract returns a verdict, it never raises
            self.fail("%s%r raised %r" % (getattr(call, "__name__", call), args, error))
        self.assertIn(verdict, (c.PASS, c.FAIL, c.BLOCKED, c.UNMEASURED))
        self.assertEqual(verdict, expected)
        return detail

    def test_stack_bank_rejects_wrong_types(self):
        for bad in self.WRONG_TYPES:
            with self.subTest(value=bad):
                self.assert_verdict(c.check_stack_bank, c.BLOCKED, bad)

    def test_stack_bank_rejects_out_of_range_numbers(self):
        for bad in self.OUT_OF_RANGE:
            with self.subTest(value=bad):
                self.assert_verdict(c.check_stack_bank, c.FAIL, bad)

    def test_stack_bank_keeps_the_meaningful_pass(self):
        self.assert_verdict(c.check_stack_bank, c.PASS, 0xF8)
        self.assert_verdict(c.check_stack_bank, c.FAIL, 0xFF)
        self.assert_verdict(c.check_stack_bank, c.FAIL, 0x00)

    def test_initial_state_rejects_wrong_types(self):
        for bad in self.WRONG_TYPES:
            with self.subTest(value=bad):
                self.assert_verdict(c.check_initial_state, c.BLOCKED, None, bad, 0)
                self.assert_verdict(c.check_initial_state, c.BLOCKED, None, 0xF8, bad)

    def test_initial_state_rejects_out_of_range_numbers(self):
        for bad in self.OUT_OF_RANGE:
            with self.subTest(value=bad):
                self.assert_verdict(c.check_initial_state, c.FAIL, None, bad, 0)
        self.assert_verdict(c.check_initial_state, c.FAIL, None, 0xF8, -1)

    def test_initial_state_keeps_the_meaningful_pass(self):
        self.assert_verdict(c.check_initial_state, c.PASS, None, 0xF8, 0)
        self.assert_verdict(c.check_initial_state, c.PASS, 0xF8, 0xF8, 0)
        self.assert_verdict(c.check_initial_state, c.PASS, 0x00, 0xF8, 0)

    def test_measured_reset_is_informational_only(self):
        # The reset value is not a contract input: an unreadable one never changes the verdict.
        for reset in self.WRONG_TYPES + (0x100, -1):
            with self.subTest(reset=reset):
                self.assert_verdict(c.check_initial_state, c.PASS, reset, 0xF8, 0)
        # A missing recorded init stays BLOCKED whatever the reset value is.
        for reset in self.WRONG_TYPES + (0xF8,):
            with self.subTest(reset=reset):
                self.assert_verdict(c.check_initial_state, c.BLOCKED, reset, None, 0)

    def test_stack_address_rejects_wrong_types_and_out_of_range(self):
        for bad in self.WRONG_TYPES:
            with self.subTest(value=bad):
                self.assert_verdict(c.check_stack_address, c.BLOCKED, bad)
        for bad in (-1, 0x1FF, 0x2200, 0x10000):
            with self.subTest(value=bad):
                self.assert_verdict(c.check_stack_address, c.FAIL, bad)

    def test_stack_address_keeps_the_meaningful_pass(self):
        self.assert_verdict(c.check_stack_address, c.PASS, 0x2100)
        self.assert_verdict(c.check_stack_address, c.PASS, 0x21FF)
        self.assert_verdict(c.check_stack_address, c.FAIL, 0x0100)
        self.assert_verdict(c.check_stack_address, c.FAIL, 0x01FF)

    def test_mpr1_write_rejects_wrong_types(self):
        for bad in self.WRONG_TYPES:
            with self.subTest(value=bad):
                self.assert_verdict(c.check_mpr1_write, c.BLOCKED, c.PATH_TAM, bad, 5)
                self.assert_verdict(c.check_mpr1_write, c.BLOCKED, c.PATH_DIRECT_SETMPR, bad, 0)
                self.assert_verdict(c.check_mpr1_write, c.BLOCKED, c.PATH_TAM, 0xF8, bad)
                self.assert_verdict(c.check_mpr1_write, c.BLOCKED, c.PATH_DIRECT_SETMPR, 0xF8, bad)

    def test_mpr1_write_rejects_out_of_range_numbers(self):
        for bad in self.OUT_OF_RANGE:
            with self.subTest(value=bad):
                self.assert_verdict(c.check_mpr1_write, c.FAIL, c.PATH_TAM, bad, 5)
                self.assert_verdict(c.check_mpr1_write, c.FAIL, c.PATH_DIRECT_SETMPR, bad, 0)
        self.assert_verdict(c.check_mpr1_write, c.FAIL, c.PATH_TAM, 0xF8, -1)

    def test_bool_index_is_not_a_valid_instruction_index(self):
        # False == 0 in Python: a bool index must never be read as instruction number 0.
        for index in (False, True):
            with self.subTest(index=index):
                verdict, _ = c.check_mpr1_write(c.PATH_DIRECT_SETMPR, 0xF8, index)
                self.assertNotEqual(verdict, c.PASS)
                self.assertEqual(verdict, c.BLOCKED)
                verdict, _ = c.check_initial_state(None, 0xF8, index)
                self.assertNotEqual(verdict, c.PASS)

    def test_mpr1_write_keeps_the_meaningful_passes(self):
        self.assert_verdict(c.check_mpr1_write, c.PASS, c.PATH_TAM, 0xF8, 5)
        self.assert_verdict(c.check_mpr1_write, c.PASS, c.PATH_DIRECT_SETMPR, 0xF8, 0)

    def test_no_invalid_input_raises_and_none_is_pass(self):
        for value in self.WRONG_TYPES + self.OUT_OF_RANGE:
            with self.subTest(value=value):
                calls = (
                    (c.check_stack_bank, (value,)),
                    (c.check_initial_state, (value, value, value)),
                    (c.check_stack_address, (value,)),
                    (c.check_mpr1_write, (c.PATH_TAM, value, value)),
                    (c.check_mpr1_write, (c.PATH_DIRECT_SETMPR, value, value)),
                    (c.check_mpr1_write, (c.PATH_RESET, value, value)),
                )
                for call, args in calls:
                    try:
                        verdict, _ = call(*args)
                    except Exception as error:   # the contract returns a verdict, it never raises
                        self.fail("%s%r raised %r" % (getattr(call, "__name__", call), args, error))
                    self.assertNotEqual(verdict, c.PASS)


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

    def test_doc_states_the_three_valued_irq_and_the_missing_evidence_rule(self):
        text = (ROOT / "docs" / "497-mpr1-stack-contract.md").read_text(encoding="utf-8")
        self.assertIn("IRQ_UNKNOWN", text)
        self.assertIn("3 値", text)
        self.assertIn("BLOCKED", text)
        self.assertIn("UNMEASURED", text)

    def test_doc_states_the_input_type_policy(self):
        text = (ROOT / "docs" / "497-mpr1-stack-contract.md").read_text(encoding="utf-8")
        self.assertIn("fail-closed", text)
        self.assertIn("bool", text)
        self.assertIn("0..255", text)
        self.assertIn("型違い", text)
        self.assertIn("範囲外", text)


class DocIrqTableTest(unittest.TestCase):
    """The IRQ decision table in the doc must match the code row by row."""

    def setUp(self):
        self.text = (ROOT / "docs" / "497-mpr1-stack-contract.md").read_text(encoding="utf-8")

    @staticmethod
    def _irq_value(cell):
        if cell == "IRQ_UNKNOWN":
            return c.IRQ_UNKNOWN
        if cell.startswith("観測 "):
            return cell[len("観測 "):] == "True"
        raise AssertionError("unknown IRQ cell %r" % (cell,))

    def test_doc_irq_table_matches_the_code(self):
        rows = [line for line in self.text.splitlines()
                if line.startswith("| あり |") or line.startswith("| なし |")]
        self.assertGreaterEqual(len(rows), 12)
        for row in rows:
            cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
            self.assertEqual(len(cells), 4, row)
            changed = {"あり": True, "なし": False}[cells[0]]
            asserted = self._irq_value(cells[1])
            pending = self._irq_value(cells[2])
            with self.subTest(row=row):
                self.assertEqual(c.judge_irq_window(changed, asserted, pending)[0], cells[3])

    def test_doc_irq_table_keeps_the_unknown_rows(self):
        for fragment in ("| あり | IRQ_UNKNOWN | 観測 False | BLOCKED |",
                         "| あり | 観測 False | IRQ_UNKNOWN | BLOCKED |",
                         "| あり | IRQ_UNKNOWN | IRQ_UNKNOWN | BLOCKED |",
                         "| なし | IRQ_UNKNOWN | 観測 False | BLOCKED |"):
            self.assertIn(fragment, self.text)


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

    def test_doc_init_index_matches_code_rule(self):
        # Read the number from the §2 rule sentence and compare it with the indices the code passes.
        matches = re.findall(r"命令番号 (\d+) で記録された `\$F8`", self.text)
        self.assertEqual(len(matches), 1)
        code_pass_indices = {i for i in range(8) if c.check_initial_state(None, c.HARNESS_INIT_MPR1, i)[0] == c.PASS}
        self.assertEqual({int(matches[0])}, code_pass_indices)

    def test_doc_other_values_row_is_fail(self):
        # The "その他の全値" row must say FAIL; a PASS there would widen the allow set without a table row.
        rows = [line for line in self.text.splitlines() if line.startswith("| その他の全値 |")]
        self.assertEqual(len(rows), 1)
        cells = [cell.strip() for cell in rows[0].strip().strip("|").split("|")]
        self.assertIn("FAIL", cells[2])
        self.assertNotIn("PASS", cells[2])


if __name__ == "__main__":
    unittest.main()
