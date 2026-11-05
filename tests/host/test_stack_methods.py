"""Host checks for the Issue #498 stack methods driver (method A, eight criteria).

Synthetic only: no emulator, BIOS, ROM or core. Rows are built here, independently of the probe, to pin
what the criteria accept and reject. Real-core evidence: tests/integration/geargrafx_stack_methods.py.
"""
import copy
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "integration"))

import geargrafx_stack_methods as M  # noqa: E402

PATCH = ROOT / "tests" / "integration" / "patches" / "geargrafx-b49ae82-stack-access-guard.patch"
PROBE = ROOT / "tests" / "integration" / "geargrafx_stack_access_guard.cpp"


def _snap(**fields):
    snap = dict(pc=0x1E00, a=0x5A, x=0x34, y=0x07, p=0x04, s=0x04, transfer_state=0, cycles=0, irq_pending=0,
                mpr=[0xF8, 0xFF, 0, 0, 0, 0, 0, 0])
    snap.update(fields)
    return snap


def safe_row():
    """A method A refusal before the first instruction: nothing executed, nothing changed."""
    snap = _snap()
    return dict(mode="A:pha-armed", exit=0, dst=[0, 0], refused=True, halted=True, latched=False, instructions=1,
                instructions_after_refusal=0, io_reads=0, io_writes=0, stack_bytes_changed=0,
                cdram_bytes_changed=0, bram_bytes_changed=0, irr_before=0, irr_after=0,
                refusal=dict(kind="instruction", op=255, s=4, transfer_state=0, mpr1=0xFF),
                start=copy.deepcopy(snap), refusal_instruction_start=copy.deepcopy(snap), stop=copy.deepcopy(snap),
                vdc={n: [5, 5] for n in ("ar", "r0", "r1", "r7", "sr", "read_buffer")}, events=[],
                events_truncated=False, guard_calls=0)


class CriteriaTests(unittest.TestCase):
    def test_untouched_stop_is_safe_in_scope(self):
        verdict, criteria = M.verdict(safe_row())
        self.assertEqual(verdict, M.SAFE)
        self.assertTrue(all(criteria.values()))

    def test_every_criterion_can_fail(self):
        for name, broken in M.falsified(safe_row()):
            with self.subTest(falsification=name):
                self.assertNotEqual(M.verdict(broken)[0], M.SAFE)

    def test_side_effects_are_unsafe_not_blocked(self):
        for field in ("io_writes", "io_reads", "cdram_bytes_changed", "bram_bytes_changed", "stack_bytes_changed"):
            row = safe_row()
            row[field] = 1
            if field.startswith("io_"):   # keep the row consistent: the event log carries the access too
                row["events"] = [dict(seq=0, kind="io", op=1 if field == "io_writes" else 0, address=0x2104, value=0,
                                      decision=-1, latched=False, mpr1=0xFF, instruction=1)]
            self.assertEqual(M.verdict(row)[0], M.UNSAFE, field)
        row = safe_row()
        row["dst"] = [0, 0x5A]
        self.assertEqual(M.verdict(row)[0], M.UNSAFE, "dst")
        self.assertEqual(M.verdict(row, legit_dst=True)[0], M.SAFE, "legit dst")

    def test_inconsistent_counters_are_rejected_as_malformed(self):
        row = safe_row()
        row["io_writes"] = 1   # counter without the event: integrity failure, not a verdict
        with self.assertRaises(ValueError):
            M.verdict(row)

    def test_execution_after_the_refusal_is_unsafe(self):
        row = safe_row()
        row["instructions_after_refusal"] = 1
        self.assertEqual(M.verdict(row)[0], M.UNSAFE)

    def test_state_change_without_side_effect_is_blocked(self):
        row = safe_row()
        row["stop"]["pc"] += 1   # e.g. a refused push with PC already past the instruction (method C)
        verdict, criteria = M.verdict(row)
        self.assertEqual(verdict, M.BLOCKED)
        self.assertFalse(criteria["pc_preserved"])
        self.assertFalse(criteria["resume_safe"])

    def test_stop_inside_a_block_transfer_is_not_resume_safe(self):
        row = safe_row()
        for name in ("refusal_instruction_start", "stop"):
            row[name]["transfer_state"] = 1
        verdict, criteria = M.verdict(row)
        self.assertEqual((verdict, [k for k, ok in criteria.items() if not ok]), (M.BLOCKED, ["resume_safe"]))

    def test_no_refusal_is_unknown_not_safe(self):
        row = safe_row()
        row["refused"] = False
        self.assertEqual(M.verdict(row)[0], M.UNKNOWN)

    def test_configuration_labels_are_not_read(self):
        row = safe_row()
        row.update(mode="A:pha-mut-aignore", mutation=6, guarded=False, method="C")
        self.assertEqual(M.verdict(row)[0], M.SAFE)

    def test_legit_pre_refusal_pushes_are_allowed_only_when_expected(self):
        row = safe_row()
        row["stack_bytes_changed"] = 3
        self.assertEqual(M.verdict(row)[0], M.UNSAFE)
        self.assertEqual(M.verdict(row, legit_stack_bytes=3)[0], M.SAFE)


class ExpectationTests(unittest.TestCase):
    def test_every_route_has_allowed_control_and_method_a_expectations(self):
        self.assertEqual(set(M.ALLOWED), set(M.UNGUARDED_IO))
        self.assertEqual(set(M.ALLOWED), set(M.EXPECT_A))
        for op in M.BLOCK_OPS:
            self.assertIn(op, M.ROUTES)
        for route in ("jsr", "pha", "flippha", "tamjsr", "tend", "irqtam"):   # the S2 gap paths
            self.assertIn(route, M.ROUTES)

    def test_known_method_a_outcomes_are_pinned(self):
        self.assertEqual(M.EXPECT_A["irqtam"]["verdict"], M.UNSAFE)
        self.assertEqual(M.EXPECT_A["irqtam"]["io"], [(1, 0x2104), (1, 0x2103), (1, 0x2102)])
        self.assertEqual((M.EXPECT_A["tend"]["verdict"], M.EXPECT_A["tend"]["failing"]), (M.BLOCKED, {"resume_safe"}))
        unsafe_or_blocked = {r for r, e in M.EXPECT_A.items() if e["verdict"] != M.SAFE}
        self.assertEqual(unsafe_or_blocked, {"irqtam", *M.MID_TRANSFER})

    def test_mid_transfer_cases_cover_every_block_opcode(self):
        self.assertEqual(set(M.MID_TRANSFER), {"tend", "tendtin", "tendtia", "tendtdd", "tendtai"})
        for route in M.MID_TRANSFER:
            self.assertEqual(M.EXPECT_A[route]["verdict"], M.BLOCKED, route)

    def test_real_core_mutants_cover_ignored_and_late_refusals(self):
        self.assertEqual(M.MUTANTS["mut-aignore"][1:], (M.UNSAFE, "io_prevented"))
        self.assertEqual(M.MUTANTS["mut-alate"][1:], (M.BLOCKED, "pc_preserved"))

    def test_no_route_sets_the_t_flag(self):
        """The pre-instruction snapshot moved before the T flag handling (#498); no route starts with T set."""
        text = PROBE.read_text(encoding="utf-8")
        values = {int(v, 16) for v in re.findall(r"r->p = 0x([0-9A-Fa-f]{2});", text)}
        default = re.search(r"Route d = \{\"\", \{\}, 0x[0-9A-F]+, 0x[0-9A-F]+, 0x[0-9A-F]+, 0x[0-9A-F]+, 0x([0-9A-F]+),", text)
        values.add(int(default.group(1), 16))
        self.assertTrue(values)
        self.assertTrue(all(v & 0x20 == 0 for v in values), values)

    def test_cases_are_unique(self):
        cases = list(M.cases())
        self.assertEqual(len(cases), len(set(cases)))
        self.assertTrue(all(c.split(":", 1)[0] in M.METHODS for c in cases))


class PatchAndProbeTests(unittest.TestCase):
    def test_pre_instruction_refusal_comes_before_any_state_change(self):
        diff = PATCH.read_text(encoding="utf-8").split("+++ b/src/huc6280_inline.h", 1)[1].split("--- a/", 1)[0]
        view = [line[1:] for line in diff.splitlines() if line[:1] in (" ", "+")]
        # The RunInstruction hunk starts after the debugger resets; anchor on the cycle counter reset.
        start = next(i for i, line in enumerate(view) if "m_extra_master_cycles = 0;" in line)
        body = view[start:start + 60]
        hook = next(i for i, line in enumerate(body) if "!gg_verify_pre_instruction(" in line)
        # The hunk ends at TraceCpuEvent(); upstream fetches the opcode right after it (pinned source).
        for marker in ("m_transfer_flag = IsSetFlag", "ClearFlag(FLAG_TRANSFER)", "TraceCpuEvent();"):
            self.assertLess(hook, next(i for i, line in enumerate(body) if marker in line), marker)
        refused = body[hook:hook + 8]
        self.assertTrue(any("m_memory_breakpoint_hit = true;" in line for line in refused))
        self.assertTrue(any(line.strip() == "return 0;" for line in refused))
        self.assertIn("gg_verify_stack_mutation != 6", "\n".join(body[hook - 4:hook + 8]))
        self.assertIn('#error "VERIFICATION-ONLY refusal needs the debugger build', "\n".join(body))

    def test_probe_keeps_method_c_as_the_default(self):
        text = PROBE.read_text(encoding="utf-8")
        self.assertIn('std::string method = "C", rest = mode;', text)
        self.assertIn('g_method_a = guarded && method.find(\'A\') != std::string::npos;', text)
        self.assertIn("if (gg_verify_stack_refused || g_instruction_refused", text)

    def test_make_integration_runs_the_driver(self):
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        target = re.search(r"^integration:.*$", makefile, re.M)
        recipe = []
        for line in makefile[target.end():].splitlines()[1:]:
            if not line.startswith("\t"):
                break
            recipe.append(line.strip())
        self.assertIn("$(MAKE) stack-methods", recipe)
        self.assertIn("$(MAKE) stack-access-guard", recipe)


if __name__ == "__main__":
    unittest.main()
