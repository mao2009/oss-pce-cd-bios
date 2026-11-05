"""Host checks for the Issue #647 (S5) decode-time refusal / IRQ deferral driver. Synthetic only; no core is run."""
import copy
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "integration"))

import geargrafx_stack_s5 as S  # noqa: E402

M = S.M


def _snap(pc=0x1E00, mpr=(0xF8, 0xF8, 0, 0, 0, 0, 0, 0), a=0xFF):
    return dict(pc=pc, a=a, x=0x34, y=0x07, p=0x04, s=0x04, transfer_state=0, cycles=0, irq_pending=0, mpr=list(mpr))


def _row(case, refused, reason, instructions, stop_pc, stop_mpr, budget=False, decode=0, io=0):
    pair = [0, 0]
    return dict(mode=case, exit=0, refused=refused, refusal_reason=reason, instructions=instructions,
                budget_stop=budget, decode_refusals=decode, io_reads=io, io_writes=0, instructions_after_refusal=0,
                stack_bytes_changed=0, cdram_bytes_changed=0, bram_bytes_changed=0, irr_before=0, irr_after=0,
                dst=[0, 0], events=[], events_truncated=False, refusal={"kind": "instruction" if refused else "none"},
                vdc={k: list(pair) for k in ("ar", "r0", "r1", "r7", "sr", "read_buffer")},
                start=_snap(), stop=_snap(pc=stop_pc, mpr=stop_mpr),
                refusal_instruction_start=_snap(pc=stop_pc, mpr=stop_mpr))


class SweepTests(unittest.TestCase):
    def test_forbidden_iff_mpr1_bit_and_not_wram(self):
        self.assertTrue(S.tam_forbidden(0x02, 0xFF))
        self.assertTrue(S.tam_forbidden(0xFF, 0x80))
        self.assertFalse(S.tam_forbidden(0x02, 0xF8))
        self.assertFalse(S.tam_forbidden(0xFD, 0xFF))   # every MPR except MPR1
        self.assertEqual(sum(S.tam_forbidden(m, 0xFF) for m in range(256)), 128)

    def test_sweep_covers_every_mask(self):
        cases = list(S.sweep_cases())
        for method, values in S.SWEEP_METHODS.items():
            for a in values:
                self.assertEqual({m for _, mm, m, aa in cases if mm == method and aa == a}, set(range(256)))
        self.assertIn(0xF8, S.SWEEP_METHODS["AD"])
        self.assertTrue({a for _, a in S.SWEEP_EXTRA} >= {0x80, 0xF7, 0x00})

    def test_sweep_check_accepts_d_refusal_and_exact_application(self):
        start = [0xF8, 0xF8, 0, 0, 0, 0, 0, 0]
        S.check_sweep(_row("c", True, "decode_tam", 1, 0x1E00, start, decode=1), "c", "AD", 0x02, 0xFF)
        applied = [0xF8, 0xF8, 0x80, 0x80, 0, 0, 0, 0]
        S.check_sweep(_row("c", False, "none", 2, 0x1E02, applied, budget=True), "c", "AD", 0x0C, 0x80)

    def test_sweep_check_rejects_wrong_rows(self):
        start = [0xF8, 0xF8, 0, 0, 0, 0, 0, 0]
        bad = [
            (_row("c", False, "none", 2, 0x1E02, [0xF8, 0xFF, 0, 0, 0, 0, 0, 0], budget=True), 0x02, 0xFF),  # not refused
            (_row("c", True, "decode_tam", 1, 0x1E00, start, decode=1), 0x01, 0xFF),       # false positive
            (_row("c", False, "none", 2, 0x1E02, start, budget=True), 0x04, 0x80),          # MPR2 not applied
            (_row("c", True, "decode_tam", 1, 0x1E00, start, decode=1, io=1), 0x02, 0xFF),  # hardware page access
            (_row("c", True, "decode_tam", 1, 0x1E00, [0xF8, 0xFF, 0, 0, 0, 0, 0, 0], decode=1), 0x02, 0xFF),
        ]
        for row, mask, a in bad:
            with self.assertRaises(ValueError):
                S.check_sweep(row, "c", "AD", mask, a)


class ExpectationTests(unittest.TestCase):
    def test_method_a_alone_stays_unsafe_on_irq_in_tam_with_i_clear(self):
        for route, (_, i_flag) in S.IRQ_ROUTES.items():
            want = S.irq_expect("A", route)["verdict"]
            self.assertEqual(want, S.SAFE if i_flag else S.UNSAFE, route)

    def test_d_and_e_expect_safe_on_every_irq_source_and_i_flag(self):
        self.assertEqual({s for s, _ in S.IRQ_ROUTES.values()}, {"IRQ1", "IRQ2", "TIQ"})
        self.assertEqual({i for _, i in S.IRQ_ROUTES.values()}, {0, 1})
        for method in ("AD", "AE", *S.WITH_C):
            for route in S.IRQ_ROUTES:
                e = S.irq_expect(method, route)
                self.assertEqual((e["verdict"], e["failing"]), (S.SAFE, set()), (method, route))

    def test_d_keeps_mpr1_and_e_shows_the_applied_tam(self):
        self.assertEqual(S.irq_expect("AD", "irqtam")["mpr1"], 0xF8)
        self.assertEqual(S.irq_expect("AE", "irqtam")["mpr1"], 0xFF)   # the executed TAM is not hidden

    def test_mid_transfer_rows_stay_blocked(self):
        for method in ("AD", "AE"):
            for route in M.MID_TRANSFER:
                self.assertEqual(S.EXPECT[method][route]["verdict"], S.BLOCKED, (method, route))

    def test_only_tam_routes_change_against_method_a(self):
        for method, changed in (("AD", {"irqtam", "tamjsr"}), ("AE", {"irqtam"})):
            differ = {r for r in M.ROUTES if S.EXPECT[method][r] != M.EXPECT_A[r]}
            self.assertEqual(differ, changed, method)

    def test_mutants_are_expected_unsafe_and_visible(self):
        for case, (want, _) in S.MUTANTS.items():
            self.assertEqual(want, S.UNSAFE, case)
        self.assertEqual(set(S.SWEEP_MUTANTS), {"AD:tam01ff-mut-dbit", "AD:tam02ff-mut-dbit"})

    def test_cases_are_unique(self):
        cases = list(S.cases())
        self.assertEqual(len(cases), len(set(cases)))
        self.assertIn("AD:tamcode-armed", cases)


class ComparisonTests(unittest.TestCase):
    def test_comparison_reads_mpr_change_from_rows(self):
        base = _row("AD:irqtam-armed", True, "decode_tam", 1, 0x1E00, [0xF8, 0xF8, 0, 0, 0, 0, 0, 0], decode=1)
        base.update(irr_before=2, irr_after=2, handler_entry_instruction=0)
        rows = {}
        for method in ("AD", "AE"):
            for route, (_, i_flag) in S.IRQ_ROUTES.items():
                row = copy.deepcopy(base)
                if method == "AE":
                    row["stop"]["mpr"][1] = 0xFF
                rows[f"{method}:{route}-armed"] = row
        out = S.comparison(rows)
        self.assertTrue(out["AD"]["3_mpr_unchanged_from_run_start"])
        self.assertFalse(out["AE"]["3_mpr_unchanged_from_run_start"])
        self.assertEqual((out["AD"]["7_handler_entry_instruction_in_allowed_run"],
                          out["AE"]["7_handler_entry_instruction_in_allowed_run"]), (2, 3))


class WiringTests(unittest.TestCase):
    def _integration_recipe(self):
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        target = re.search(r"^integration:.*$", makefile, re.M)
        recipe = []
        for line in makefile[target.end():].splitlines()[1:]:
            if not line.startswith("\t"):
                break
            recipe.append(line.strip())
        return recipe

    def test_make_integration_runs_s5_and_the_481_contract(self):
        recipe = self._integration_recipe()
        self.assertIn("$(MAKE) stack-s5", recipe)
        self.assertIn("$(MAKE) caller-stack-contract", recipe)

    def test_patch_defers_only_through_the_verification_flag(self):
        patch = (ROOT / "tests/integration/patches/geargrafx-b49ae82-stack-access-guard.patch").read_text(encoding="utf-8")
        self.assertIn("!gg_verify_defer_irq", patch)
        self.assertIn("bool gg_verify_defer_irq = false;", patch)


if __name__ == "__main__":
    unittest.main()
