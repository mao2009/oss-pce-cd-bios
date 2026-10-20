"""Issue #366: IRQ2/IRQ1/TIQ entry judge (tools/irq_entry_judge.py). Synthetic rows only; no emulator, no ROM.

Checks: a debugger-build stop at the resolved handler with 0 handler instructions and page-1 stack writes is PASS;
libretro is always BLOCKED; every falsified row is rejected (mutation test) so a removed stop judgement is caught.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import irq_entry_judge as ij  # noqa: E402

HANDLERS = {"IRQ2": 0x4010, "IRQ1": 0x4020, "TIQ": 0x4030}


class IrqEntryJudgeTest(unittest.TestCase):
    def test_each_vector_passes_on_debugger_build(self):
        for vector, handler in HANDLERS.items():
            with self.subTest(vector=vector):
                verdict, detail = ij.judge_entry(ij.base_row(vector, handler), vector, handler)
                self.assertEqual(verdict, ij.PASS, detail)

    def test_libretro_build_is_always_blocked(self):
        for vector, handler in HANDLERS.items():
            with self.subTest(vector=vector):
                verdict, _ = ij.judge_entry(ij.base_row(vector, handler), vector, handler, build="libretro")
                self.assertEqual(verdict, ij.BLOCKED)

    def test_handler_comes_from_caller_not_observation(self):
        row = ij.base_row("IRQ1", HANDLERS["IRQ1"])
        verdict, _ = ij.judge_entry(row, "IRQ1", 0x4999)
        self.assertEqual(verdict, ij.FAIL)

    def test_stack_window_boundaries(self):
        handler = HANDLERS["IRQ2"]
        for address, expected in ((0x2000, ij.PASS), (0x3FFF, ij.PASS), (0x4000, ij.FAIL), (0x1FFF, ij.FAIL)):
            with self.subTest(address=hex(address)):
                row = ij.base_row("IRQ2", handler)
                row["stack_writes"] = [address, 0x21FE, 0x21FD]
                self.assertEqual(ij.judge_entry(row, "IRQ2", handler)[0], expected)

    def test_unobserved_fields_are_blocked_not_pass(self):
        handler = HANDLERS["TIQ"]
        row = ij.base_row("TIQ", handler)
        del row["stack_writes"]
        self.assertEqual(ij.judge_entry(row, "TIQ", handler)[0], ij.BLOCKED)
        row = ij.base_row("TIQ", handler)
        row["status"] = "rejected"
        self.assertEqual(ij.judge_entry(row, "TIQ", handler)[0], ij.BLOCKED)

    def test_mutation_self_check_rejects_every_falsified_row(self):
        for vector, handler in HANDLERS.items():
            with self.subTest(vector=vector):
                count = ij.mutation_self_check(ij.base_row(vector, handler), handler, vector)
                self.assertGreaterEqual(count, 10)

    def test_removed_stop_judgement_would_be_caught(self):
        # Mutation of the judge itself: skip the stop comparison. The stop-mutated row must then wrongly pass,
        # which the mutation self-check would report. This proves the stop check is load-bearing.
        handler = HANDLERS["IRQ2"]
        row = ij.base_row("IRQ2", handler)
        row["stop_pc"] = handler + 3
        self.assertEqual(ij.judge_entry(row, "IRQ2", handler)[0], ij.FAIL)
        source = Path(ij.__file__).read_text(encoding="utf-8")
        mutated = source.replace("if stop_pc != expected_handler:", "if False:")
        self.assertNotEqual(mutated, source)
        namespace = {"__name__": "mutant"}
        exec(compile(mutated, "mutant", "exec"), namespace)
        self.assertEqual(namespace["judge_entry"](row, "IRQ2", handler)[0], ij.PASS)


if __name__ == "__main__":
    unittest.main()
