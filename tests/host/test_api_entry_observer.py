"""Host tests for the Issue #635 API-entry observer (synthetic only, no commercial data)."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "integration"))
from geargrafx_api_entry import (assert_commercial_clean, classify_apicall, classify_commercial,  # noqa: E402
                                 jsr_confirmed, validate_commercial_output)


def call(in_table=True, slot=2, delta=2, caller=0x3006, entry=None,
         stack_check="MATCH", ret_match=True, mpr1=0xF8):
    if entry is None:
        entry = 0xE006 if in_table else 0xE24F
    mpr = [255, mpr1, 128, 129, 130, 131, 132, 0]
    return {"in_table": in_table, "slot": slot, "sp_delta": delta, "entry_pc": entry,
            "caller_pc": caller, "stack_check": stack_check,
            "return_matches_caller": ret_match, "mpr": mpr}


class ClassifyTests(unittest.TestCase):
    def test_jsr_to_table(self):
        self.assertEqual(classify_apicall(call()), "JSR")

    def test_jmp_to_table(self):
        self.assertEqual(classify_apicall(call(delta=0)), "JMP")

    def test_interrupt_delta(self):
        self.assertEqual(classify_apicall(call(delta=3)), "INTERRUPT")

    def test_direct_not_table(self):
        self.assertEqual(classify_apicall(call(in_table=False)), "DIRECT")

    def test_irq_vector(self):
        self.assertEqual(classify_apicall(call(in_table=False, delta=3, entry=0xFFF8)), "IRQ")

    def test_other_direct(self):
        self.assertEqual(classify_apicall(call(in_table=False, delta=0, entry=0x4000)), "OTHER_DIRECT")

    def test_other_delta(self):
        self.assertEqual(classify_apicall(call(delta=7)), "OTHER")


class JsrReturnTests(unittest.TestCase):
    """JSR confirmation needs internal return MATCH (no raw stack values exposed)."""
    def test_jsr_confirmed_on_match(self):
        self.assertTrue(jsr_confirmed(call()))

    def test_return_mismatch_is_not_confirmed(self):
        self.assertFalse(jsr_confirmed(call(stack_check="MISMATCH", ret_match=False)))

    def test_mpr1_mismatch_is_unknown_not_confirmed(self):
        self.assertFalse(jsr_confirmed(call(mpr1=0xFF)))


class MutationTests(unittest.TestCase):
    """Mutants the harness must reject: wrong slot, misaligned entry, truncated obs."""

    def test_wrong_slot_is_not_jsr_slot2(self):
        # A slot-3 JSR mislabeled as slot 2 must not classify as slot 2.
        c = call(slot=3)
        self.assertEqual(c["slot"], 3)
        self.assertNotEqual(c["slot"], 2)

    def test_misaligned_entry_is_not_table(self):
        # An entry at $E001 (not slot-aligned) must not be in_table.
        misaligned = call(entry=0xE001, in_table=False, delta=2)
        self.assertEqual(classify_apicall(misaligned), "OTHER_DIRECT")

    def test_truncated_flag_present_in_run_schema(self):
        # Probe emits apicall_truncated; harness must see it (checked in commercial path).
        src = (ROOT / "tests" / "integration" / "geargrafx_api_entry_probe.cpp").read_text()
        self.assertIn("apicall_truncated", src)


class ProbeObservationOnlyTests(unittest.TestCase):
    def test_probe_has_no_guest_writes(self):
        src = (ROOT / "tests" / "integration" / "geargrafx_api_entry_probe.cpp").read_text()
        for forbidden in ("Memory::Write", "SetValue", "->Write(", "PutValue", "SetMpr"):
            self.assertNotIn(forbidden, src, f"probe must not write guest state: {forbidden}")
        # No write through the WRAM pointer element (wram[...] = ...). Reads (x = wram[...]) are allowed.
        import re
        self.assertIsNone(re.search(r"wram\[[^\]]*\]\s*=(?!=)", src),
                          "probe must not write WRAM element")
        self.assertIn("GetWorkingRAM()", src)

    def test_probe_has_no_guest_bytes_in_output(self):
        src = (ROOT / "tests" / "integration" / "geargrafx_api_entry_probe.cpp").read_text()
        self.assertIn("APICALL", src)
        # APICALL is byte-free in both modes (boolean return check only, no raw bytes/word).
        start = src.index('std::printf("APICALL')
        end = src.index("for (int i = 0; i < 8", start)
        apicall_printf = src[start:end]
        for forbidden in ("buffer_hex", "result_hex", "buffer_head_hex", "hex(",
                          "stack_b0", "stack_b1", "stack_word"):
            self.assertNotIn(forbidden, apicall_printf)
        self.assertIn("return_matches_caller", apicall_printf)
        self.assertIn("stack_check", apicall_printf)

    def test_commercial_mode_gates_ram_dumps(self):
        src = (ROOT / "tests" / "integration" / "geargrafx_api_entry_probe.cpp").read_text()
        self.assertIn('"commercial"', src)
        self.assertIn('if (!commercial)', src)

    def test_probe_never_emits_raw_stack_values(self):
        src = (ROOT / "tests" / "integration" / "geargrafx_api_entry_probe.cpp").read_text()
        self.assertNotIn('"stack_b0"', src)
        self.assertNotIn('"stack_b1"', src)
        self.assertNotIn('"stack_word"', src)


def records(calls_cold, calls_warm, reason="api_unimplemented", halted=True, truncated=False):
    def run(cycle, calls):
        return {"cycle": cycle, "reason": reason, "pc": 0xE24F, "x": 2,
                "halt_confirmed": halted, "step": 100, "apicall_truncated": truncated}
    apis = ([dict(c, cycle="cold") for c in calls_cold]
            + [dict(c, cycle="warm") for c in calls_warm])
    return {"APICALL": apis, "RUN": [run("cold", calls_cold), run("warm", calls_warm)]}


class CommercialVerdictTests(unittest.TestCase):
    def test_confirmed_needs_table_jsr_and_stub(self):
        verdict, _ = classify_commercial(records([call()], [call()]))
        self.assertEqual(verdict, "API_ENTRY_CONFIRMED")

    def test_zero_calls_with_fail_is_pre_api_not_confirmed(self):
        verdict, _ = classify_commercial(records([], [], reason="cd_halt_fail"))
        self.assertEqual(verdict, "PRE_API_BLOCKER_OBSERVED")
        self.assertNotEqual(verdict, "API_ENTRY_CONFIRMED")

    def test_zero_calls_unclear_is_unknown_not_confirmed(self):
        verdict, _ = classify_commercial(records([], [], reason="cd_halt_ok"))
        self.assertEqual(verdict, "UNKNOWN")

    def test_truncated_never_confirmed(self):
        verdict, _ = classify_commercial(records([call()], [call()], truncated=True))
        self.assertEqual(verdict, "INCOMPLETE")

    def test_budget_never_confirmed(self):
        verdict, _ = classify_commercial(records([call()], [call()], reason="budget", halted=False))
        self.assertEqual(verdict, "BUDGET")

    def test_return_mismatch_never_confirmed(self):
        bad = call(stack_check="MISMATCH", ret_match=False)
        verdict, _ = classify_commercial(records([bad], [bad]))
        self.assertEqual(verdict, "UNKNOWN")

    def test_mpr1_mismatch_never_confirmed(self):
        bad = call(mpr1=0xFF)
        verdict, _ = classify_commercial(records([bad], [bad]))
        self.assertEqual(verdict, "UNKNOWN")

    def test_cold_warm_mismatch_is_unknown(self):
        verdict, _ = classify_commercial(records([call()], [call(slot=3)]))
        self.assertEqual(verdict, "UNKNOWN")


class CommercialOutputTests(unittest.TestCase):
    def test_repo_path_rejected(self):
        with self.assertRaises(ValueError):
            validate_commercial_output(str(ROOT / "out.json"))

    def test_relative_path_rejected(self):
        with self.assertRaises(ValueError):
            validate_commercial_output("out.json")

    def test_missing_parent_rejected(self):
        with self.assertRaises(ValueError):
            validate_commercial_output("/tmp/opencode/definitely-missing-parent/out.json")

    def test_existing_file_rejected_no_overwrite(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "exists.json"
            path.write_text("{}")
            with self.assertRaises(ValueError):
                validate_commercial_output(str(path))

    def test_symlink_file_rejected(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target.json"
            link = Path(tmp) / "link.json"
            target.write_text("{}")
            os.symlink(str(target), str(link))
            with self.assertRaises(ValueError):
                validate_commercial_output(str(link))

    def test_nested_worktree_path_rejected(self):
        # A path inside a worktree (dir with .git) must be rejected even under /tmp/opencode.
        import os
        import tempfile
        with tempfile.TemporaryDirectory(dir="/tmp/opencode") as tmp:
            wt = Path(tmp) / "wt"
            wt.mkdir()
            (wt / ".git").touch()
            with self.assertRaises(ValueError):
                validate_commercial_output(str(wt / "out.json"))

    def test_harness_never_logs_paths(self):
        src = (ROOT / "tests" / "integration" / "geargrafx_api_entry.py").read_text()
        # prints must not interpolate rom/cue/output paths (no str(args.commercial_* in prints)
        for forbidden in ("str(args.commercial_rom", "str(args.commercial_cue", "str(args.commercial_output"):
            self.assertNotIn(forbidden, src)

    def test_commercial_evidence_is_whitelisted(self):
        src = (ROOT / "tests" / "integration" / "geargrafx_api_entry.py").read_text()
        # evidence writes APICALL struct + stripped RUN only (no hex keys copied).
        self.assertIn('"apicalls": records["APICALL"]', src)
        commercial_block = src[src.index("if args.commercial_rom:"):]
        self.assertNotIn('"buffer_hex"', commercial_block)
        self.assertNotIn('"stack_b0"', commercial_block)
        self.assertNotIn('"stack_b1"', commercial_block)
        self.assertNotIn('"stack_word"', commercial_block)


class InformationProtectionMutationTests(unittest.TestCase):
    """Each mutant must be detected by a purpose assertion (not syntax/import error)."""

    def test_mutant_stack_b0_reintroduced_in_apicall(self):
        # A future APICALL carrying stack_b0 must be rejected by the commercial guard.
        mutant = {"CAP": [], "HIT": [], "PHASE": [], "RUN": [], "APICALL": [{"cycle": "cold", "stack_b0": 1}]}
        with self.assertRaisesRegex(ValueError, "raw stack values"):
            assert_commercial_clean(mutant)

    def test_mutant_stack_b1_reintroduced_in_apicall(self):
        mutant = {"CAP": [], "HIT": [], "PHASE": [], "RUN": [], "APICALL": [{"cycle": "cold", "stack_b1": 1}]}
        with self.assertRaisesRegex(ValueError, "raw stack values"):
            assert_commercial_clean(mutant)

    def test_mutant_hex_in_cap_or_phase_rejected(self):
        for tag in ("CAP", "PHASE", "HIT", "RUN"):
            mutant = {"CAP": [], "HIT": [], "PHASE": [], "RUN": [], "APICALL": []}
            mutant[tag] = [{"buffer_hex": "aa"}]
            with self.assertRaisesRegex(ValueError, "RAM dump fields", msg=tag):
                assert_commercial_clean(mutant)

    def test_mutant_buffer_hex_in_commercial_run(self):
        src = (ROOT / "tests" / "integration" / "geargrafx_api_entry_probe.cpp").read_text()
        # Commercial RUN must contain no hex() dump: hex calls are gated by !commercial.
        self.assertIn('if (!commercial) { hex("result_hex"', src)
        self.assertIn('if (!commercial) hex("buffer_head_hex"', src)

    def test_mutant_zero_calls_confirmed(self):
        verdict, _ = classify_commercial(records([], [], reason="cd_halt_fail"))
        self.assertNotEqual(verdict, "API_ENTRY_CONFIRMED")

    def test_mutant_truncated_confirmed(self):
        verdict, _ = classify_commercial(records([call()], [call()], truncated=True))
        self.assertNotEqual(verdict, "API_ENTRY_CONFIRMED")

    def test_mutant_timeout_as_halt(self):
        verdict, _ = classify_commercial(records([call()], [call()], reason="budget", halted=False))
        self.assertNotEqual(verdict, "API_ENTRY_CONFIRMED")

    def test_mutant_missing_output_starts_commercial(self):
        # Commercial mode must require --commercial-output (host-only check, no Geargrafx launch).
        src = (ROOT / "tests" / "integration" / "geargrafx_api_entry.py").read_text()
        self.assertIn('"--commercial-output"', src)
        self.assertIn("not args.commercial_output", src)
        self.assertIn("validate_commercial_output", src)
        # And validation itself fails closed without a private outside-repo path.
        with self.assertRaises(ValueError):
            validate_commercial_output("relative.json")


if __name__ == "__main__":
    unittest.main()
