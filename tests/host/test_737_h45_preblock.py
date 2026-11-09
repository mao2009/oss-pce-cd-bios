#!/usr/bin/env python3
"""Issue #737 H4/H5 E1-E3: host tests for tools/h45_preblock_gate.py (synthetic only).

Covers: E1 ST free-run opcode gate, E2 IRQ first-instruction gate, E3 stack/MPR allowlist,
unified allowlist + fail-closed wrapper. Every falsified/mutated row must NOT be PASS;
the unmutated clean row must PASS. No emulator, no ROM, no BIOS, no callee execution.
"""
import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import h45_preblock_gate as G  # noqa: E402


class StFreeRunTest(unittest.TestCase):
    def test_st_opcodes_refused_before_side_effect(self):
        for opcode in (0x03, 0x13, 0x23):
            with self.subTest(opcode=hex(opcode)):
                verdict, _ = G.judge_st_free_run(opcode, True, True, 0, False, True)
                self.assertEqual(verdict, G.PASS)

    def test_non_st_passes_screen(self):
        verdict, _ = G.judge_st_free_run(0xEA, True, True, 0, False, True)
        self.assertEqual(verdict, G.PASS)

    def test_unscreened_or_peek_fail_is_blocked(self):
        for opcode, peek, screened in ((0x03, True, False), (0x13, False, True), (0x23, None, True)):
            with self.subTest(opcode=hex(opcode)):
                verdict, _ = G.judge_st_free_run(opcode, peek, screened, 0, False, True)
                self.assertEqual(verdict, G.BLOCKED)

    def test_st_ran_or_vdc_changed_is_fail(self):
        verdict, _ = G.judge_st_free_run(0x03, True, True, 1, False, True)
        self.assertEqual(verdict, G.FAIL)
        verdict, _ = G.judge_st_free_run(0x13, True, True, 0, True, True)
        self.assertEqual(verdict, G.FAIL)

    def test_bool_is_not_a_byte_and_missing_halt_is_blocked(self):
        verdict, _ = G.judge_st_free_run(True, True, True, 0, False, True)
        self.assertEqual(verdict, G.BLOCKED)
        verdict, _ = G.judge_st_free_run(0x03, True, True, 0, False, None)
        self.assertEqual(verdict, G.BLOCKED)
        verdict, _ = G.judge_st_free_run(0x03, True, True, 0, False, False)
        self.assertEqual(verdict, G.FAIL)

    def test_mutations_never_pass(self):
        mutations = [
            ("disarm_bp", (0x03, True, True, 0, True, True)),
            ("remove_opcode_check_st_ran", (0x13, True, True, 1, False, True)),
            ("null_st_hook_vdc_changed", (0x23, True, True, 0, True, True)),
            ("peek_fail_step", (0x03, False, True, 0, False, True)),
            ("unscreened_first_insn", (0x13, True, False, 0, False, True)),
            ("halt_inequal", (0x23, True, True, 0, False, False)),
            ("halt_missing", (0x03, True, True, 0, False, None)),
        ]
        for name, args in mutations:
            with self.subTest(name=name):
                verdict, _ = G.judge_st_free_run(*args)
                self.assertNotEqual(verdict, G.PASS)


class IrqFirstInstructionTest(unittest.TestCase):
    def test_each_vector_passes(self):
        for vector, addr in G.VECTORS.items():
            with self.subTest(vector=vector):
                row = G.clean_irq_row(vector, 0x3F80)
                self.assertEqual(G.judge_irq_first_instruction(row, vector, 0x3F80)[0], G.PASS)

    def test_libretro_blocked_and_unknown_build_blocked(self):
        row = G.clean_irq_row()
        self.assertEqual(G.judge_irq_first_instruction(row, "IRQ2", 0x3F80, build="libretro")[0], G.BLOCKED)
        self.assertEqual(G.judge_irq_first_instruction(row, "IRQ2", 0x3F80, build="unknown")[0], G.BLOCKED)

    def test_unmasked_pending_is_fail(self):
        row = G.clean_irq_row()
        row["pending"] = True
        row["p_i"] = False
        self.assertEqual(G.judge_irq_first_instruction(row, "IRQ2", 0x3F80)[0], G.FAIL)

    def test_missing_irq_observation_is_blocked(self):
        for key in ("pending", "p_i"):
            with self.subTest(missing=key):
                row = G.clean_irq_row()
                row.pop(key)
                self.assertEqual(G.judge_irq_first_instruction(row, "IRQ2", 0x3F80)[0], G.BLOCKED)

    def test_mutations_never_pass(self):
        base = G.clean_irq_row()
        mutations = [
            ("stop_pc+3", {"stop_pc": 0x3F83}),
            ("handler_instructions=1", {"handler_instructions": 1}),
            ("execbp_hit=false", {"execbp_hit": False}),
            ("vector_lo wrong", {"vector_lo": 0x81}),
            ("stack outside", {"stack_writes": [0x1FFF, 0x21FE, 0x21FD]}),
            ("handler wrote", {"handler_writes": [0x2010]}),
            ("stack len 4", {"stack_writes": [0x21FF, 0x21FE, 0x21FD, 0x21FC]}),
            ("status rejected", {"status": "rejected"}),
            ("vector mismatch", {"vector": "IRQ1"}),
            ("halt unequal", {"halt_equal": False}),
        ]
        for name, change in mutations:
            with self.subTest(name=name):
                row = copy.deepcopy(base)
                row.update(change)
                verdict, _ = G.judge_irq_first_instruction(row, "IRQ2", 0x3F80)
                self.assertNotEqual(verdict, G.PASS)


class StackMprTest(unittest.TestCase):
    def test_clean_passes(self):
        verdict, _ = G.judge_stack_mpr([0, 0xF8, 0, 0, 0, 0, 0, 0], 0xF8, 0, False, False, False, [])
        self.assertEqual(verdict, G.PASS)

    def test_ff_and_non_f8_fail(self):
        verdict, _ = G.judge_stack_mpr([0xFF, 0xF8, 0, 0, 0, 0, 0, 0], 0xF8, 0, False, False, False, [])
        self.assertEqual(verdict, G.FAIL)
        verdict, _ = G.judge_stack_mpr([0, 0xFF, 0, 0, 0, 0, 0, 0], 0xF8, 0, False, False, False, [])
        self.assertEqual(verdict, G.FAIL)
        verdict, _ = G.judge_stack_mpr([0, 0x00, 0, 0, 0, 0, 0, 0], 0xF8, 0, False, False, False, [])
        self.assertEqual(verdict, G.FAIL)

    def test_init_index_wrong_and_alias_blocked(self):
        verdict, _ = G.judge_stack_mpr([0, 0xF8, 0, 0, 0, 0, 0, 0], 0xF8, 7, False, False, False, [])
        self.assertEqual(verdict, G.FAIL)
        verdict, _ = G.judge_stack_mpr([0, 0xF8, 0, 0, 0, 0, 0, 0], 0xF8, 0, True, False, False, [])
        self.assertEqual(verdict, G.FAIL)
        verdict, _ = G.judge_stack_mpr([0, 0xF8, 0, 0, 0, 0, 0, 0], 0xF8, 0, None, False, False, [])
        self.assertEqual(verdict, G.BLOCKED)

    def test_irq_tam_hole_is_blocked(self):
        verdict, _ = G.judge_stack_mpr([0, 0xF8, 0, 0, 0, 0, 0, 0], 0xF8, 0, False, True, False, [], mpr1_changed=True)
        self.assertEqual(verdict, G.BLOCKED)
        verdict, _ = G.judge_stack_mpr([0, 0xF8, 0, 0, 0, 0, 0, 0], 0xF8, 0, False, None, False, [])
        self.assertEqual(verdict, G.BLOCKED)

    def test_stack_outside_fails(self):
        verdict, _ = G.judge_stack_mpr([0, 0xF8, 0, 0, 0, 0, 0, 0], 0xF8, 0, False, False, False, [0x0100])
        self.assertEqual(verdict, G.FAIL)

    def test_mutations_never_pass(self):
        cases = [
            ("widen_allowlist_f7", ([0, 0xF7, 0, 0, 0, 0, 0, 0], 0xF8, 0, False, False, False, [])),
            ("drop_alias_scan", ([0, 0xFF, 0, 0, 0, 0, 0, 0], 0xF8, 0, False, False, False, [])),
            ("off_by_one", ([0, 0xF9, 0, 0, 0, 0, 0, 0], 0xF8, 0, False, False, False, [])),
            ("mid_flight_setmpr", ([0, 0xF8, 0, 0, 0, 0, 0, 0], 0xF8, 1, False, False, False, [])),
            ("tam_to_ff", ([0, 0xFF, 0, 0, 0, 0, 0, 0], 0xF8, 0, False, False, False, [])),
        ]
        for name, args in cases:
            with self.subTest(name=name):
                verdict, _ = G.judge_stack_mpr(*args)
                self.assertNotEqual(verdict, G.PASS)


class AllowlistTest(unittest.TestCase):
    def test_clean_snapshot_passes_but_never_permits(self):
        snapshot = G.clean_snapshot()
        verdict, _ = G.judge_allowlist(snapshot)
        self.assertEqual(verdict, G.PASS)
        record = G.require_preblock(snapshot)
        self.assertEqual((record["execution"], record["execution_permitted"], record["jsr_count"], record["release_eligible"]),
                         ("BLOCKED", False, 0, False))

    def test_genuine_callee_and_gate_lift_fail(self):
        snapshot = G.clean_snapshot()
        snapshot["jsr_count"] = 1
        self.assertEqual(G.judge_allowlist(snapshot)[0], G.FAIL)
        snapshot = G.clean_snapshot()
        snapshot["execution"] = "ALLOWED"
        self.assertEqual(G.judge_allowlist(snapshot)[0], G.FAIL)
        snapshot = G.clean_snapshot()
        snapshot["release_eligible"] = True
        self.assertEqual(G.judge_allowlist(snapshot)[0], G.FAIL)
        with self.assertRaises(G.GateRefused):
            G.require_preblock(snapshot)

    def test_st_and_forbidden_mmio_are_blocked(self):
        for opcode in (0x03, 0x13, 0x23):
            with self.subTest(opcode=hex(opcode)):
                snapshot = G.clean_snapshot()
                snapshot["first_opcode"] = opcode
                verdict, _ = G.judge_allowlist(snapshot)
                self.assertEqual(verdict, G.BLOCKED)
        for mmio in (0x1802, 0x1804, 0x1402, 0x1403):
            with self.subTest(mmio=hex(mmio)):
                snapshot = G.clean_snapshot()
                snapshot["mmio_stores"] = [mmio]
                self.assertEqual(G.judge_allowlist(snapshot)[0], G.BLOCKED)

    def test_irq_unknown_and_libretro_blocked(self):
        snapshot = G.clean_snapshot()
        snapshot["irq_status"] = "UNKNOWN"
        self.assertEqual(G.judge_allowlist(snapshot)[0], G.BLOCKED)
        snapshot = G.clean_snapshot()
        snapshot["build"] = "libretro"
        self.assertEqual(G.judge_allowlist(snapshot)[0], G.BLOCKED)
        snapshot = G.clean_snapshot()
        snapshot["build"] = "GG_TESTING"
        self.assertEqual(G.judge_allowlist(snapshot)[0], G.BLOCKED)

    def test_hash_budget_pc_and_deny_flags(self):
        snapshot = G.clean_snapshot()
        snapshot["expected_sha256"] = "b" * 64
        self.assertEqual(G.judge_allowlist(snapshot)[0], G.BLOCKED)
        snapshot = G.clean_snapshot()
        snapshot["budget"] = 1000001
        self.assertEqual(G.judge_allowlist(snapshot)[0], G.BLOCKED)
        snapshot = G.clean_snapshot()
        snapshot["pc"] = 0x5000
        self.assertEqual(G.judge_allowlist(snapshot)[0], G.FAIL)
        for key in G.DENY_FLAG_KEYS:
            with self.subTest(deny=key):
                snapshot = G.clean_snapshot()
                snapshot["deny_flags"][key] = True
                self.assertEqual(G.judge_allowlist(snapshot)[0], G.BLOCKED)

    def test_real_core_provenance_must_be_pinned(self):
        snapshot = G.clean_snapshot()
        snapshot["provenance"] = {"kind": "REAL_CORE_EXECUTION", "core_revision": "deadbeef",
                                  "bios": "SYNTHETIC", "real_core_claim": True}
        self.assertEqual(G.judge_allowlist(snapshot)[0], G.BLOCKED)
        snapshot = G.clean_snapshot()
        snapshot["provenance"] = {"kind": "REAL_CORE_EXECUTION", "core_revision": G.PINNED_CORE_REVISION,
                                  "bios": "SYNTHETIC", "real_core_claim": True}
        self.assertEqual(G.judge_allowlist(snapshot)[0], G.PASS)

    def test_mutation_suite_no_false_pass(self):
        base = G.clean_snapshot()
        mutations = [
            ("st_opcode", {"first_opcode": 0x03}),
            ("mmio_1802", {"mmio_stores": [0x1802]}),
            ("mpr_ff", {"mpr": [0xFF, 0xF8, 0, 0, 0, 0, 0, 0]}),
            ("mpr1_not_f8", {"mpr": [0, 0x00, 0, 0, 0, 0, 0, 0]}),
            ("alias_true", {"alias_detected": True}),
            ("irq_unknown", {"irq_status": "UNKNOWN"}),
            ("libretro", {"build": "libretro"}),
            ("hash_mismatch", {"expected_sha256": "b" * 64}),
            ("budget_over", {"executed": 101}),
            ("pc_outside", {"pc": 0x3FFF if False else 0x4000}),
            ("jsr_count_1", {"jsr_count": 1}),
            ("execution_allowed", {"execution": "ALLOWED"}),
            ("release_true", {"release_eligible": True}),
            ("peek_fail", {"peek_ok": False}),
            ("unscreened", {"first_opcode_screened": False}),
            ("deny_same_value", {"deny_flags": {**{k: False for k in G.DENY_FLAG_KEYS}, "same_value_write": True}}),
        ]
        self.assertGreaterEqual(len(mutations), 13)
        for name, change in mutations:
            with self.subTest(name=name):
                snapshot = copy.deepcopy(base)
                snapshot.update(change)
                verdict, _ = G.judge_allowlist(snapshot)
                self.assertNotEqual(verdict, G.PASS, f"mutation {name} was accepted as PASS")

    def test_removed_opcode_check_would_be_caught(self):
        source = Path(G.__file__).read_text(encoding="utf-8")
        self.assertIn("ST_OPCODES", source)
        mutated = source.replace("first_opcode in ST_OPCODES", "False")
        self.assertNotEqual(mutated, source)
        namespace = {"__name__": "mutant"}
        exec(compile(mutated, "mutant", "exec"), namespace)
        verdict, _ = G.judge_st_free_run(0x03, True, True, 1, False, True)
        self.assertEqual(verdict, G.FAIL)
        mutant_verdict, _ = namespace["judge_st_free_run"](0x03, True, True, 1, False, True)
        self.assertEqual(mutant_verdict, namespace["PASS"])


if __name__ == "__main__":
    unittest.main()
