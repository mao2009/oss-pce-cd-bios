"""Host checks for the Issue #653 IRQ assert/accept timing driver. Synthetic only; no core is run."""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "integration"))

import geargrafx_stack_irq_timing as S  # noqa: E402


class ModelTests(unittest.TestCase):
    def test_tam_has_no_clock_point(self):
        self.assertFalse(S.has_clock_point("tam", 1))
        self.assertEqual(S.expected("ni_tam_q1_1"), (1, "boundary", 2))   # pending before TAM: taken inside it
        self.assertEqual(S.expected("ni_tam_t_1"), (2, "boundary", 3))    # raised in TAM's cycles: after the next
        self.assertEqual(S.expected("ni_tam_q1m_1"), (None, None, 0))     # nothing to inject: NOT_MEASURED

    def test_clock_point_assert_is_taken_at_the_end_of_the_same_instruction(self):
        self.assertEqual(S.expected("ni_sta_t_1"), (1, "mid", 2))
        self.assertEqual(S.expected("ni_sta_q1m_1"), (1, "mid", 2))

    def test_transfers_take_the_irq_only_after_transfer_end(self):
        for op in S.OPS:
            for k in (1, 2, 3, 4, 5):
                self.assertEqual(S.expected(f"ni_{op}_t_{k}")[2], 7, (op, k))
            self.assertEqual(S.expected(f"ni_{op}_t_6")[2], 8)
            self.assertEqual(S.expected(f"ni_{op}v_t_3"), (3, "mid", 7))
            self.assertEqual(S.expected(f"ni_{op}_q1m_3"), (None, None, 0))

    def test_i_flag_blocks_entry(self):
        self.assertEqual(S.expected("ni_tii_t_3_i")[2], 0)
        self.assertEqual(S.expected("ni_tam_q1_1_i")[2], 0)

    def test_mutants_differ_from_the_model(self):
        for case, entry in S.MUTANTS.items():
            route = case.split(":")[1].split("-", 1)[0]
            self.assertNotEqual(S.expected(route)[2], entry, case)

    def test_observed_assert_prefers_an_earlier_clock_point(self):
        row = {"ni": {"clocks": [{"i": 3, "irr": 4}], "timeline": [{"i": 3, "irr": 0}, {"i": 4, "irr": 4}]}}
        self.assertEqual(S.observed_assert(row, 4), ("mid", 3))
        row["ni"]["clocks"] = []
        self.assertEqual(S.observed_assert(row, 4), ("boundary", 4))

    def test_cases_unique_and_not_measured_listed(self):
        cases = S.cases()
        self.assertEqual(len(cases), len(set(cases)))
        for case in S.NOT_MEASURED:
            self.assertIn(case, cases)


class WiringTests(unittest.TestCase):
    def test_make_integration_runs_the_driver(self):
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        target = re.search(r"^integration:.*$", makefile, re.M)
        recipe = []
        for line in makefile[target.end():].splitlines()[1:]:
            if not line.startswith("\t"):
                break
            recipe.append(line.strip())
        self.assertIn("$(MAKE) stack-irq-timing", recipe)

    def test_patch_observer_and_mutants(self):
        patch = (ROOT / "tests/integration/patches/geargrafx-b49ae82-stack-access-guard.patch").read_text(encoding="utf-8")
        self.assertIn("gg_verify_clock_observer(gg_verify_stack_context, master_cycles);", patch)
        self.assertIn("if (gg_verify_stack_mutation != 8)", patch)
        self.assertIn("(m_transfer_state == 0 || gg_verify_stack_mutation == 9)", patch)


if __name__ == "__main__":
    unittest.main()
