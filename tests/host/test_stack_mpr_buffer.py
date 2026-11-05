"""Host checks for the Issue #654 m_mpr_buffer / TMA #$00 driver. Synthetic only; no core is run."""
import copy
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "integration"))

import geargrafx_stack_mpr_buffer as S  # noqa: E402


def _row(case, timeline, refused=False, reason="none", stop_buf=None, **extra):
    snap = dict(pc=0x1E04, a=0xF8, x=0x34, y=0x07, p=0x04, s=0x04, transfer_state=0, cycles=0, irq_pending=0,
                mpr=[0xF8, 0xF8, 0xF8, 0x01, 0x02, 0, 0, 0])
    pair = [0, 0]
    row = dict(mode=case, exit=0, refused=refused, refusal_reason=reason, instructions=14900,
               refusal={"kind": "instruction" if refused else "none"}, io_reads=0, io_writes=0,
               instructions_after_refusal=0, stack_bytes_changed=0, cdram_bytes_changed=0, bram_bytes_changed=0,
               irr_before=0, irr_after=0, dst=[0, 0], events=[], events_truncated=False, handler_entry_instruction=0,
               vdc={k: list(pair) for k in ("ar", "r0", "r1", "r7", "sr", "read_buffer")},
               start=dict(snap), stop=dict(snap), refusal_instruction_start=dict(snap),
               mb={"active": True, "stop_buf": stop_buf if stop_buf is not None else timeline[-1]["buf"],
                   "timeline": timeline})
    row.update(extra)
    return row


TAM1 = [dict(i=1, pc=0x1E00, a=0xF8, buf=0x00, mpr1=0xF8), dict(i=2, pc=0x1E02, a=0xF8, buf=0xF8, mpr1=0xF8),
        dict(i=3, pc=0x1E04, a=0xF8, buf=0xF8, mpr1=0xF8)]


class ModelTests(unittest.TestCase):
    def test_d_keeps_the_buffer_a_and_e_show_the_tam(self):
        self.assertEqual(S.REFUSED["AD:mb_refuse-allowed"][1:], (0x02, 0x02))
        self.assertEqual(S.REFUSED["A:mb_refuse-allowed"][1:], (0x02, 0xFF))
        self.assertEqual(S.REFUSED["AE:mb_refuse-allowed"][1:], (0x02, 0xFF))
        self.assertEqual(S.REFUSED["A:mb_irqf-allowed"][0]["verdict"], S.UNSAFE)

    def test_multi_bit_tma_is_the_or(self):
        self.assertEqual(S.COMPLETED["tmam"][0][2], 0x01 | 0x02)

    def test_tam_without_bits_leaves_the_buffer(self):
        points = {i: buf for i, _, buf, _ in S.COMPLETED["tam0"]}
        self.assertEqual(points[2], points[4])

    def test_completed_check_accepts_the_model_and_rejects_a_constant_observation(self):
        S.check_completed(_row("A:mb_tam1-allowed", TAM1), "A:mb_tam1-allowed")
        broken = copy.deepcopy(TAM1)
        for e in broken:
            e["buf"] = 0
        with self.assertRaises(ValueError):
            S.check_completed(_row("A:mb_tam1-allowed", broken, stop_buf=0), "A:mb_tam1-allowed")

    def test_cases_unique_and_mutants_point_at_real_cases(self):
        cases = S.cases()
        self.assertEqual(len(cases), len(set(cases)))
        for mutant, (correct, _) in S.MUTANTS.items():
            self.assertIn(correct, cases)
            self.assertIn(mutant, cases)


class WiringTests(unittest.TestCase):
    def test_make_integration_runs_the_driver(self):
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        target = re.search(r"^integration:.*$", makefile, re.M)
        recipe = []
        for line in makefile[target.end():].splitlines()[1:]:
            if not line.startswith("\t"):
                break
            recipe.append(line.strip())
        self.assertIn("$(MAKE) stack-mpr-buffer", recipe)

    def test_patch_accessor_is_read_only(self):
        patch = (ROOT / "tests/integration/patches/geargrafx-b49ae82-stack-access-guard.patch").read_text(encoding="utf-8")
        self.assertIn("+    u8 GG_VerifyMprBuffer() const { return m_mpr_buffer; }", patch)


if __name__ == "__main__":
    unittest.main()
