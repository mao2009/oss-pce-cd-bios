"""Host checks for the Issue #500 A / C / A+C comparison driver. Synthetic only; no core is run."""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "integration"))

import geargrafx_stack_methods_compare as X  # noqa: E402

M = X.M


class ExpectationTests(unittest.TestCase):
    def test_every_method_covers_every_route(self):
        for method in X.METHODS:
            self.assertEqual(set(X.EXPECT[method]), set(M.ROUTES), method)
            self.assertIn(method, X.BANK_EXPECT)

    def test_method_c_alone_never_claims_a_consistent_stop(self):
        for route, expect in X.EXPECT_C.items():
            self.assertEqual(expect["verdict"], X.BLOCKED, route)
            self.assertIn("resume_safe", expect["failing"], route)
            self.assertEqual(expect["kind"], "stack", route)
        self.assertEqual(X.BANK_EXPECT["C"]["verdict"], X.BLOCKED)

    def test_a_plus_c_differs_from_a_only_on_irq_in_tam(self):
        differ = {r for r in M.ROUTES if X.EXPECT_AC[r]["verdict"] != M.EXPECT_A[r]["verdict"]}
        self.assertEqual(differ, {"irqtam"})
        self.assertEqual(M.EXPECT_A["irqtam"]["verdict"], X.UNSAFE)
        self.assertEqual(X.EXPECT_AC["irqtam"]["verdict"], X.BLOCKED)

    def test_a_plus_c_is_never_unsafe_and_keeps_two_blocked_routes(self):
        verdicts = {r: e["verdict"] for r, e in X.EXPECT_AC.items()}
        self.assertNotIn(X.UNSAFE, verdicts.values())
        self.assertEqual({r for r, v in verdicts.items() if v == X.BLOCKED}, {"irqtam", *M.MID_TRANSFER})

    def test_mutants_cover_both_methods(self):
        self.assertEqual(X.MUTANTS["AC:pha-mut-aignore"][:2], (X.BLOCKED, "pc_preserved"))   # C catches A's miss
        self.assertEqual(X.MUTANTS["AC:irqtam-mut-late16"][:2], (X.UNSAFE, "io_prevented"))  # broken C on A's hole
        self.assertEqual(X.MUTANTS["C:pha-mut-ignore"][:2], (X.UNSAFE, "io_prevented"))

    def test_mutant_evidence_checks_need_real_observations(self):
        caught = X.MUTANTS["AC:pha-mut-aignore"][2]
        row = dict(guard_refusals=1, io_reads=0, io_writes=0, instructions_after_refusal=0, instruction_refused=True,
                   events=[dict(kind="stack", decision=0)])
        self.assertTrue(caught(row))
        for broken in (dict(row, guard_refusals=0), dict(row, events=[]), dict(row, io_writes=1),
                       dict(row, instructions_after_refusal=2), dict(row, instruction_refused=False)):
            self.assertFalse(caught(broken))

    def test_cases_are_unique_and_method_prefixed(self):
        cases = list(X.cases())
        self.assertEqual(len(cases), len(set(cases)))
        self.assertTrue(all(c.split(":", 1)[0] in X.METHODS for c in cases))

    def test_make_integration_runs_the_driver(self):
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        target = re.search(r"^integration:.*$", makefile, re.M)
        recipe = []
        for line in makefile[target.end():].splitlines()[1:]:
            if not line.startswith("\t"):
                break
            recipe.append(line.strip())
        self.assertIn("$(MAKE) stack-methods-compare", recipe)


if __name__ == "__main__":
    unittest.main()
