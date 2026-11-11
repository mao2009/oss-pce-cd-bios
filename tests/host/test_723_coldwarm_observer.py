"""Issue #723: cold/warm observer + IRQ/JSR distinction (host pinning).

Synthetic only: no emulator, no ROM, no Geargrafx core, no reference
BIOS, no reference JSR, no ./rom/ input. The pinned-source order fact
and the per-stop retention / no-resume contract live in Issue #721
(docs/721-transferend-noresume.md + tests/host/test_721_*, PR #770)
and are NOT re-pinned here. This file pins only the observer shape:
separate cold/warm initial-state evidence (MPR1 UNDETERMINED ->
DIRECT_SETMPR $F8 at index 0, per #497) and the explicit IRQ-vs-JSR
entry distinction (per #709 / #635 classify_apicall + jsr_confirmed),
with fail-closed handling and no raw stack bytes in records or logs.
"""
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "integration"))

from geargrafx_api_entry import classify_apicall, jsr_confirmed  # noqa: E402


def _load_contract():
    spec = importlib.util.spec_from_file_location(
        "mpr1_stack_contract", ROOT / "tools" / "mpr1_stack_contract.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


C = _load_contract()
DOC = ROOT / "docs" / "723-coldwarm-observer.md"
STUB = 0xE245


def _call(in_table=True, slot=2, delta=2, caller=0x3006, entry=None,
          stack_check="MATCH", ret_match=True, mpr1=0xF8):
    if entry is None:
        entry = 0xE006 if in_table else STUB
    mpr = [0xF8, mpr1, 0x80, 0x81, 0x82, 0x83, 0x84, 0x00]
    return {"in_table": in_table, "slot": slot, "sp_delta": delta,
            "entry_pc": entry, "caller_pc": caller,
            "stack_check": stack_check,
            "return_matches_caller": ret_match, "mpr": mpr}


def _jsr_row():
    return _call(in_table=True, delta=2, stack_check="MATCH",
                 ret_match=True, mpr1=0xF8)


def _irq_row():
    return _call(in_table=False, delta=3, entry=0xFFF8,
                 stack_check="MISMATCH", ret_match=False, mpr1=0xF8)


def _cycle_record(cycle, measured_reset, init_value, init_index):
    return {"cycle": cycle, "measured_reset": measured_reset,
            "init_value": init_value, "init_index": init_index}


def _judge_cycle_init(record):
    return C.check_initial_state(record["measured_reset"],
                                 record["init_value"],
                                 record["init_index"])


def _judge_pair(cold_record, warm_record):
    """Both cycles need their own index-0 $F8 record. Shared reuse is BLOCKED."""
    if cold_record is warm_record:
        return C.BLOCKED, "single record reused for both cycles"
    if cold_record.get("cycle") != "cold" or warm_record.get("cycle") != "warm":
        return C.BLOCKED, "cycle labels missing or swapped"
    cold_verdict, cold_detail = _judge_cycle_init(cold_record)
    if cold_verdict != C.PASS:
        return cold_verdict, "cold: " + cold_detail
    warm_verdict, warm_detail = _judge_cycle_init(warm_record)
    if warm_verdict != C.PASS:
        return warm_verdict, "warm: " + warm_detail
    return C.PASS, "cold and warm carry separate index-0 $F8 records"


def _judge_runs(runs):
    """Minimal run-shape gate mirroring classify_commercial fail-closed order."""
    if len(runs) != 2:
        return "FAIL", "missing cold/warm runs"
    cycles = sorted(r.get("cycle") for r in runs)
    if cycles != ["cold", "warm"]:
        return "FAIL", "cold/warm pair incomplete"
    if any(r.get("reason") == "budget" or r.get("halt_confirmed") is not True
           for r in runs):
        return "BUDGET", "no confirmed halt (budget is not a halt)"
    if any(r.get("apicall_truncated") is True for r in runs):
        return "INCOMPLETE", "APICALL trace truncated"
    return "PASS", "cold+warm runs present, halts confirmed, no truncation"


ALLOWED_RECORD_KEYS = frozenset({
    "in_table", "slot", "sp_delta", "entry_pc", "caller_pc",
    "stack_check", "return_matches_caller", "mpr", "cycle",
})
FORBIDDEN_RECORD_KEYS = frozenset({
    "stack_b0", "stack_b1", "stack_word", "buffer_hex", "result_hex",
    "buffer_head_hex",
})


def _audit_no_raw_bytes(rows):
    for row in rows:
        keys = set(row)
        if any(k.endswith("_hex") for k in keys):
            return False
        if keys & FORBIDDEN_RECORD_KEYS:
            return False
        if not keys <= ALLOWED_RECORD_KEYS:
            return False
    return True


class ColdWarmInitialStateTests(unittest.TestCase):
    def test_reset_value_is_undetermined_and_never_trusted(self):
        self.assertIsNone(C.RESET_MPR1)
        # Same recorded init passes whatever the measured reset reads.
        for reset in (None, 0x00, 0xFF, 0xF8, "garbage"):
            with self.subTest(reset=reset):
                self.assertEqual(
                    C.check_initial_state(reset, 0xF8, 0)[0], C.PASS)

    def test_cold_and_warm_need_separate_index0_f8_records(self):
        cold = _cycle_record("cold", None, 0xF8, 0)
        warm = _cycle_record("warm", 0xFF, 0xF8, 0)
        self.assertEqual(_judge_pair(cold, warm)[0], C.PASS)

    def test_shared_single_record_for_both_cycles_is_blocked(self):
        shared = _cycle_record("cold", None, 0xF8, 0)
        self.assertEqual(_judge_pair(shared, shared)[0], C.BLOCKED)

    def test_swapped_cycle_labels_are_blocked(self):
        cold = _cycle_record("warm", None, 0xF8, 0)
        warm = _cycle_record("cold", None, 0xF8, 0)
        self.assertEqual(_judge_pair(cold, warm)[0], C.BLOCKED)

    def test_missing_warm_record_is_not_pass(self):
        cold = _cycle_record("cold", None, 0xF8, 0)
        warm = _cycle_record("warm", None, None, 0)
        verdict, _ = _judge_pair(cold, warm)
        self.assertNotEqual(verdict, C.PASS)
        self.assertEqual(verdict, C.BLOCKED)

    def test_init_after_first_instruction_fails_even_with_f8(self):
        for index in range(1, 8):
            with self.subTest(index=index):
                cold = _cycle_record("cold", None, 0xF8, index)
                warm = _cycle_record("warm", None, 0xF8, 0)
                self.assertEqual(_judge_pair(cold, warm)[0], C.FAIL)

    def test_recorded_init_other_than_f8_fails(self):
        cold = _cycle_record("cold", None, 0xFF, 0)
        warm = _cycle_record("warm", None, 0xF8, 0)
        self.assertEqual(_judge_pair(cold, warm)[0], C.FAIL)

    def test_unreadable_init_is_blocked_not_pass(self):
        for bad in (None, "F8", True, [0xF8], 0x1FF):
            with self.subTest(bad=bad):
                if bad is None:
                    verdict, _ = C.check_initial_state(None, None, 0)
                else:
                    verdict, _ = C.check_initial_state(None, bad, 0)
                self.assertNotEqual(verdict, C.PASS)
                if bad == 0x1FF:
                    self.assertEqual(verdict, C.FAIL)
                else:
                    self.assertEqual(verdict, C.BLOCKED)

    def test_reset_path_is_never_a_contract_input(self):
        for value in (0xF8, 0xFF, 0x00):
            with self.subTest(value=value):
                self.assertEqual(
                    C.check_mpr1_write(C.PATH_RESET, value, 0)[0], C.BLOCKED)

    def test_tam_and_direct_paths_are_distinct(self):
        self.assertEqual(C.check_mpr1_write(C.PATH_TAM, 0xF8, 5)[0], C.PASS)
        self.assertEqual(C.check_mpr1_write(C.PATH_TAM, 0xFF, 5)[0], C.FAIL)
        self.assertEqual(
            C.check_mpr1_write(C.PATH_DIRECT_SETMPR, 0xF8, 0)[0], C.PASS)
        self.assertEqual(
            C.check_mpr1_write(C.PATH_DIRECT_SETMPR, 0xF8, 1)[0], C.FAIL)


class IrqJsrDistinctionTests(unittest.TestCase):
    def test_jsr_jmp_interrupt_table_classes(self):
        self.assertEqual(classify_apicall(_call(delta=2), stub_addr=STUB), "JSR")
        self.assertEqual(classify_apicall(_call(delta=0), stub_addr=STUB), "JMP")
        self.assertEqual(
            classify_apicall(_call(delta=3), stub_addr=STUB), "INTERRUPT")
        self.assertEqual(classify_apicall(_call(delta=7), stub_addr=STUB), "OTHER")

    def test_irq_needs_vector_or_delta3_off_table(self):
        self.assertEqual(
            classify_apicall(_call(in_table=False, delta=3, entry=0xFFF8),
                             stub_addr=STUB), "IRQ")
        self.assertEqual(
            classify_apicall(_call(in_table=False, delta=0, entry=0xFFF6),
                             stub_addr=STUB), "IRQ")
        self.assertEqual(
            classify_apicall(_call(in_table=False, entry=STUB),
                             stub_addr=STUB), "DIRECT")

    def test_direct_needs_explicit_stub(self):
        moved = _call(in_table=False, entry=STUB)
        self.assertEqual(classify_apicall(moved, stub_addr=STUB), "DIRECT")
        self.assertEqual(
            classify_apicall(moved, stub_addr=0xE24F), "OTHER_DIRECT")
        with self.assertRaises(TypeError):
            classify_apicall(moved)
        self.assertEqual(
            classify_apicall(_call(in_table=False, entry=0x4000, delta=0),
                             stub_addr=STUB), "OTHER_DIRECT")

    def test_jsr_confirmed_requires_match_return_and_mpr1_f8(self):
        self.assertTrue(jsr_confirmed(_jsr_row()))
        self.assertFalse(
            jsr_confirmed(_call(stack_check="MISMATCH", ret_match=False)))
        self.assertFalse(jsr_confirmed(_call(ret_match=False)))
        self.assertFalse(jsr_confirmed(_call(mpr1=0xFF)))

    def test_jsr_success_is_not_irq_evidence(self):
        row = _jsr_row()
        self.assertEqual(classify_apicall(row, stub_addr=STUB), "JSR")
        self.assertNotEqual(classify_apicall(row, stub_addr=STUB), "IRQ")
        self.assertTrue(jsr_confirmed(row))
        # The same confirmed JSR row must not satisfy an IRQ-entry check.
        self.assertNotEqual(
            classify_apicall(row, stub_addr=STUB), "IRQ",
            "a JSR success must never be read as IRQ safety evidence")

    def test_irq_observation_is_not_jsr_evidence(self):
        row = _irq_row()
        self.assertEqual(classify_apicall(row, stub_addr=STUB), "IRQ")
        self.assertNotEqual(classify_apicall(row, stub_addr=STUB), "JSR")
        self.assertFalse(jsr_confirmed(row))


class FailClosedTests(unittest.TestCase):
    def test_mpr1_mismatch_is_not_confirmed(self):
        self.assertEqual(C.check_stack_bank(0xFF)[0], C.FAIL)
        self.assertFalse(jsr_confirmed(_call(mpr1=0xFF)))
        self.assertFalse(jsr_confirmed(_call(mpr1=0x00)))

    def test_irq_unknown_window_is_blocked_never_pass(self):
        self.assertEqual(C.judge_irq_window(True, None, None)[0], C.BLOCKED)
        self.assertEqual(C.judge_irq_window(False, None, None)[0], C.BLOCKED)
        self.assertEqual(
            C.judge_irq_window(True, C.IRQ_UNKNOWN, False)[0], C.BLOCKED)
        self.assertEqual(C.judge_irq_window(True, True, False)[0], C.UNMEASURED)
        self.assertEqual(C.judge_irq_window(True, False, False)[0], C.PASS)
        self.assertEqual(C.judge_irq_window(False, False, False)[0], C.PASS)

    def test_missing_cycle_is_fail_not_pass(self):
        only_cold = [{"cycle": "cold", "reason": "api_unimplemented",
                      "halt_confirmed": True, "apicall_truncated": False}]
        self.assertEqual(_judge_runs(only_cold)[0], "FAIL")
        self.assertEqual(_judge_runs([])[0], "FAIL")

    def test_truncated_trace_is_incomplete_not_pass(self):
        runs = [{"cycle": "cold", "reason": "api_unimplemented",
                 "halt_confirmed": True, "apicall_truncated": True},
                {"cycle": "warm", "reason": "api_unimplemented",
                 "halt_confirmed": True, "apicall_truncated": False}]
        self.assertEqual(_judge_runs(runs)[0], "INCOMPLETE")

    def test_budget_is_not_a_halt(self):
        runs = [{"cycle": "cold", "reason": "budget",
                 "halt_confirmed": False, "apicall_truncated": False},
                {"cycle": "warm", "reason": "api_unimplemented",
                 "halt_confirmed": True, "apicall_truncated": False}]
        self.assertEqual(_judge_runs(runs)[0], "BUDGET")

    def test_observer_records_carry_no_raw_bytes(self):
        rows = [dict(_jsr_row(), cycle="cold"), dict(_irq_row(), cycle="warm")]
        self.assertTrue(_audit_no_raw_bytes(rows))
        leaked = [dict(_jsr_row(), cycle="cold", stack_b0=0x12)]
        self.assertFalse(_audit_no_raw_bytes(leaked))
        dumped = [dict(_jsr_row(), cycle="cold", buffer_hex="00")]
        self.assertFalse(_audit_no_raw_bytes(dumped))

    def test_wrong_types_are_blocked(self):
        self.assertEqual(C.check_stack_bank("F8")[0], C.BLOCKED)
        self.assertEqual(C.check_stack_bank(None)[0], C.BLOCKED)
        self.assertEqual(C.check_stack_bank(True)[0], C.BLOCKED)
        self.assertEqual(
            C.check_initial_state(None, 0xF8, True)[0], C.BLOCKED)
        self.assertEqual(
            C.judge_irq_window("yes", False, False)[0], C.BLOCKED)


class LedgerLinkTests(unittest.TestCase):
    def test_ledger_exists_and_cites_721_reuse(self):
        text = DOC.read_text(encoding="utf-8")
        for snippet in ("721-transferend-noresume", "PR #770",
                        "b49ae82", "synthetic", "release_eligible=false",
                        "DIRECT_SETMPR $F8", "UNDETERMINED",
                        "classify_apicall", "jsr_confirmed"):
            self.assertIn(snippet, text)

    def test_stop_state_contract_is_not_duplicated_here(self):
        text = DOC.read_text(encoding="utf-8")
        self.assertIn("does **not** re-pin", text)
        # Dirty-stop dirt markers live in #721 only; citing the ledger by
        # name is allowed, re-tabling its per-stop dirt is not.
        for duplicated in ('changed={"transfer_state"}',
                           "REGISTERS_UNCHANGED_PC_ADVANCED"):
            self.assertNotIn(duplicated, text)
        own_source = Path(__file__).read_text(encoding="utf-8")
        self.assertNotIn("geargrafx_stack" + "_access_guard", own_source)
        self.assertNotIn("check" + "_armed", own_source)

    def test_doc_pins_separate_cycles_and_nontransfer(self):
        text = DOC.read_text(encoding="utf-8")
        for snippet in ("separate", "cold", "warm",
                        "never IRQ safety evidence",
                        "never JSR safety evidence"):
            self.assertIn(snippet, text)


class ProvenanceBoundaryTests(unittest.TestCase):
    def test_doc_states_synthetic_only_and_gates(self):
        text = DOC.read_text(encoding="utf-8")
        for snippet in ("Synthetic", "./rom/", "BLOCKED",
                        "ABI VERIFIED 0/81", "DEV_CALLABLE 0",
                        "not a compatible", "NOT_RUN"):
            self.assertIn(snippet, text)

    def test_test_file_uses_no_probe_or_commercial_path(self):
        own_source = Path(__file__).read_text(encoding="utf-8")
        # Build needles by concatenation so this audit does not itself
        # introduce the forbidden literals.
        for forbidden in ("run_" + "probe", "commercial_" + "media",
                          "LoadBiosFrom" + "Buffer", "subprocess." + "run",
                          "geargrafx_" + "smoke"):
            self.assertNotIn(forbidden, own_source)

    def test_doc_has_no_raw_stack_value_fields(self):
        text = DOC.read_text(encoding="utf-8")
        for forbidden in ('"stack_b0"', '"stack_b1"', '"stack_word"',
                           "buffer_hex", "stack_bytes_changed"):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
