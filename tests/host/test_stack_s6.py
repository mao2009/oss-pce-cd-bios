"""Host checks for the Issue #648 (S6) block transfer driver. Synthetic only; no core is run."""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "integration"))

import geargrafx_stack_s6 as S  # noqa: E402

M = S.M


class ExpectationTests(unittest.TestCase):
    def test_all_five_block_opcodes(self):
        self.assertEqual(set(S.OPS), {"tii", "tin", "tdd", "tia", "tai"})
        self.assertEqual(set(S.FULL), set(S.OPS))
        self.assertEqual(set(S.PARTIAL), set(S.OPS))
        for op in S.OPS:
            self.assertLessEqual(S.PARTIAL[op], S.FULL[op], op)

    def test_mid_transfer_is_never_safe(self):
        for op in S.OPS:
            for kind in S.MID_KINDS:
                e = S.mid_expect(op, kind)
                self.assertEqual(e["verdict"], S.BLOCKED, (op, kind))
                self.assertIn("resume_safe", e["failing"])
                self.assertEqual(e["transfer_state"], 1 if kind == "e" else 2)
                self.assertGreater(e["wram"], 0)   # bytes already written are expected, not hidden
        self.assertEqual(S.SELF_MID_EXPECT["verdict"], S.BLOCKED)
        self.assertEqual(S.SELF_MID_EXPECT["reason"], "transfer_opcode_changed")

    def test_pre_start_refusal_changes_nothing(self):
        e = S.PRESTART_EXPECT
        self.assertEqual((e["verdict"], e["failing"], e["index"], e["wram"]), (S.SAFE, set(), 1, 0))
        self.assertEqual(S.SELF_EXPECT["reason"], "transfer_self")
        self.assertEqual(set(S.CROSS_IO), set(S.OPS))   # page-crossing fixture for every opcode

    def test_irq_sources(self):
        self.assertEqual(S.IRQ_KINDS, {"q": 0x02, "q2": 0x01, "qt": 0x04})

    def test_every_mutant_must_be_unsafe_and_visible(self):
        self.assertEqual(len([c for c in S.MUTANTS if c.endswith("mut-tresume")]), 6)
        self.assertEqual(len([c for c in S.MUTANTS if c.endswith("mut-tnomid")]), 5)
        self.assertIn("AT:xtiic-mut-tfirst", S.MUTANTS)
        self.assertIn("AT:xtddc-mut-tseq", S.MUTANTS)
        self.assertEqual(len(S.FALSE_POSITIVE_MUTANTS), 3)
        resumed = S.MUTANTS["AT:xtiim-mut-tresume"]
        self.assertTrue(resumed(dict(t_resumed=True, instructions_after_refusal=5, io_after_refusal=2)))
        self.assertFalse(resumed(dict(t_resumed=True, instructions_after_refusal=0, io_after_refusal=0)))

    def test_cases_are_unique_and_cover_regression(self):
        cases = S.cases()
        self.assertEqual(len(cases), len(set(cases)))
        for route in M.ROUTES:
            self.assertIn(f"AT:{route}-armed", cases)
            self.assertIn(f"AT:{route}-allowed", cases)


class CheckTests(unittest.TestCase):
    def _row(self, **extra):
        snap = dict(pc=0x1E00, a=0x5A, x=0x34, y=0x07, p=0x04, s=0x01, transfer_state=2, cycles=0, irq_pending=0,
                    mpr=[0xF8] * 4 + [0] * 4)
        pair = [0, 0]
        row = dict(mode="AT:xtiim-armed", exit=0, refused=True, refusal_reason="transfer_mpr_changed", instructions=4,
                   refusal={"kind": "instruction"}, io_reads=0, io_writes=0, io_after_refusal=0,
                   instructions_after_refusal=0, stack_bytes_changed=3, cdram_bytes_changed=0, bram_bytes_changed=0,
                   irr_before=0, irr_after=0, dst=[0, 0], events=[], events_truncated=False, wram_bytes_changed=2,
                   t_refusals=1, t_at_refusal=[0x1E82, 0x4002, 2, 0], t_stop=[0x1E82, 0x4002, 2, 0],
                   vdc={k: list(pair) for k in ("ar", "r0", "r1", "r7", "sr", "read_buffer")},
                   start=dict(snap, transfer_state=0, s=0x04), stop=dict(snap), refusal_instruction_start=dict(snap))
        row.update(extra)
        return row

    def test_mid_transfer_row_is_blocked(self):
        got, failing = S.check_refused(self._row(), "AT:xtiim-armed", S.mid_expect("tii", "m"))
        self.assertEqual((got, failing), (S.BLOCKED, ["resume_safe"]))

    def test_hidden_or_moved_bytes_are_rejected(self):
        for broken in (self._row(wram_bytes_changed=0), self._row(t_stop=[0x1E83, 0x4003, 1, 0]),
                       self._row(refusal_reason="mpr1"), self._row(io_writes=1)):
            with self.assertRaises(ValueError):
                S.check_refused(broken, "AT:xtiim-armed", S.mid_expect("tii", "m"))


class WiringTests(unittest.TestCase):
    def test_make_integration_runs_s6(self):
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        target = re.search(r"^integration:.*$", makefile, re.M)
        recipe = []
        for line in makefile[target.end():].splitlines()[1:]:
            if not line.startswith("\t"):
                break
            recipe.append(line.strip())
        self.assertIn("$(MAKE) stack-s6", recipe)

    def test_patch_adds_only_read_only_transfer_accessors(self):
        patch = (ROOT / "tests/integration/patches/geargrafx-b49ae82-stack-access-guard.patch").read_text(encoding="utf-8")
        for name in ("Source", "Dest", "Length", "Count"):
            self.assertRegex(patch, rf"u32 GG_VerifyTransfer{name}\(\) const \{{ return m_transfer_\w+; \}}")


if __name__ == "__main__":
    unittest.main()
