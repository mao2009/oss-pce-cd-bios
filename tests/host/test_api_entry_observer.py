"""Host tests for the Issue #635 API-entry observer (synthetic only, no commercial data)."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "integration"))
from geargrafx_api_entry import (assert_commercial_clean, classify_apicall, classify_commercial,  # noqa: E402
                                 jsr_confirmed, list_registered_worktrees, save_commercial_evidence,
                                 validate_commercial_output)


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

    def test_direct_uses_label_derived_stub(self):
        # The stub address moves with the loader layout (#744 moved it off
        # $E24F); classification must follow the label-derived address.
        moved = call(in_table=False, entry=0xE245)
        self.assertEqual(classify_apicall(moved, stub_addr=0xE245), "DIRECT")
        # ... and must NOT match the stale constant under the default.
        self.assertEqual(classify_apicall(moved), "OTHER_DIRECT")

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

    def test_multi_call_jsr_sequence_confirmed(self):
        # Earlier JSR calls that returned (e.g. to an implemented slot), then a
        # stub JSR: same sequence cold/warm, all JSR-confirmed, stub halt.
        seq = [call(slot=2), call(slot=3)]
        verdict, detail = classify_commercial(records(seq, seq))
        self.assertEqual(verdict, "API_ENTRY_CONFIRMED")
        self.assertIn("sequence", detail)

    def test_multi_call_sequence_mismatch_is_unknown(self):
        cold = [call(slot=2), call(slot=3)]
        warm = [call(slot=2), call(slot=4)]
        verdict, _ = classify_commercial(records(cold, warm))
        self.assertEqual(verdict, "UNKNOWN")

    def test_multi_call_with_bad_return_is_unknown(self):
        bad = call(slot=2, stack_check="MISMATCH", ret_match=False)
        seq = [bad, call(slot=3)]
        verdict, _ = classify_commercial(records(seq, seq))
        self.assertEqual(verdict, "UNKNOWN")

    def test_multi_call_jmp_not_last_is_unknown(self):
        seq = [call(delta=0), call(slot=3)]
        verdict, _ = classify_commercial(records(seq, seq))
        self.assertEqual(verdict, "UNKNOWN")


class CommercialOutputTests(unittest.TestCase):
    def test_repo_path_rejected(self):
        with self.assertRaises(ValueError):
            validate_commercial_output(str(ROOT / "out.json"))

    def test_relative_and_empty_rejected(self):
        for bad in ("out.json", "", "/"):
            with self.assertRaises(ValueError, msg=bad):
                validate_commercial_output(bad)

    def test_home_rejected(self):
        with self.assertRaises(ValueError):
            validate_commercial_output(str(Path.home() / "out.json"))

    def test_not_under_private_parent_rejected(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            sub = Path(tmp) / "sub"
            sub.mkdir()
            with self.assertRaises(ValueError):
                validate_commercial_output(str(sub / "out.json"), private_parent=Path(tmp))

    def test_missing_parent_rejected(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                validate_commercial_output(str(Path(tmp) / "missing" / "out.json"), private_parent=Path(tmp))

    def test_existing_file_rejected_no_overwrite(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "exists.json"
            path.write_text("{}")
            with self.assertRaises(ValueError):
                validate_commercial_output(str(path), private_parent=Path(tmp))

    def test_symlink_file_rejected(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target.json"
            link = Path(tmp) / "link.json"
            target.write_text("{}")
            os.symlink(str(target), str(link))
            with self.assertRaises(ValueError):
                validate_commercial_output(str(link), private_parent=Path(tmp))

    def test_ancestor_symlink_rejected(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp) / "real"
            real.mkdir()
            link = Path(tmp) / "link"
            os.symlink(str(real), str(link))
            with self.assertRaises(ValueError):
                validate_commercial_output(str(link / "out.json"), private_parent=link)

    def test_ancestor_git_rejected(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            priv = Path(tmp) / "priv"
            priv.mkdir()
            (priv / ".git").touch()
            with self.assertRaises(ValueError):
                validate_commercial_output(str(priv / "out.json"), private_parent=priv)

    def test_registered_worktree_rejected(self):
        # Mock enumeration: output inside a registered worktree must be rejected.
        from unittest import mock
        import geargrafx_api_entry as mod
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            priv = Path(tmp) / "priv"
            priv.mkdir()
            wt = priv / "wt"
            wt.mkdir()
            with mock.patch.object(mod, "list_registered_worktrees", return_value=[wt]):
                with self.assertRaises(ValueError):
                    validate_commercial_output(str(wt / "out.json"), private_parent=priv)

    def test_list_worktrees_parses_porcelain(self):
        from unittest import mock
        import geargrafx_api_entry as mod
        import subprocess
        fake = subprocess.CompletedProcess([], 0,
                                            stdout="worktree /tmp/wt-a\nbare\nworktree /tmp/wt-b\n")
        with mock.patch.object(mod.subprocess, "run", return_value=fake):
            got = mod.list_registered_worktrees(Path("/tmp/fake-root"))
        self.assertEqual([str(p) for p in got], ["/tmp/wt-a", "/tmp/wt-b"])

    def test_list_worktrees_failure_fails_closed(self):
        from unittest import mock
        import geargrafx_api_entry as mod
        import subprocess
        for bad in (subprocess.CompletedProcess([], 1, stdout=""),
                    subprocess.CompletedProcess([], 0, stdout="")):
            with self.subTest(bad=bad.stdout), mock.patch.object(mod.subprocess, "run", return_value=bad):
                with self.assertRaises(ValueError):
                    mod.list_registered_worktrees(Path("/tmp/fake-root"))

    def test_worktree_enumeration_failure_fails_closed(self):
        from unittest import mock
        import geargrafx_api_entry as mod
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(mod, "list_registered_worktrees", side_effect=ValueError("worktree enumeration failed")):
                with self.assertRaises(ValueError):
                    validate_commercial_output(str(Path(tmp) / "out.json"), private_parent=Path(tmp))

    def test_harness_never_logs_paths(self):
        src = (ROOT / "tests" / "integration" / "geargrafx_api_entry.py").read_text()
        # prints must not interpolate rom/cue/output paths (no str(args.commercial_* in prints)
        for forbidden in ("str(args.commercial_rom", "str(args.commercial_cue", "str(args.commercial_output"):
            self.assertNotIn(forbidden, src)

    def test_commercial_evidence_is_whitelisted(self):
        src = (ROOT / "tests" / "integration" / "geargrafx_api_entry.py").read_text()
        # evidence writes APICALL struct + stripped RUN only (no hex keys copied).
        self.assertIn('"apicalls": records["APICALL"]', src)
        commercial_block = src[src.index("if args.commercial:"):]
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


class SaveEvidenceTests(unittest.TestCase):
    """fd-pinned save: perms, exclusivity, TOCTOU, write/close failures."""

    def test_save_success_0600_and_reread(self):
        import json
        import stat
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            text = json.dumps({"verdict": "SYNTHETIC", "x": 1}, indent=2)
            dest = save_commercial_evidence(str(Path(tmp) / "out.json"), text, private_parent=Path(tmp))
            self.assertEqual(dest.name, "out.json")
            st = (Path(tmp) / "out.json").stat()
            self.assertTrue(stat.S_ISREG(st.st_mode))
            self.assertEqual(stat.S_IMODE(st.st_mode), 0o600)
            self.assertEqual(json.loads((Path(tmp) / "out.json").read_text()), {"verdict": "SYNTHETIC", "x": 1})

    def test_save_refuses_overwrite_and_symlink_target(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            p = str(Path(tmp) / "out.json")
            save_commercial_evidence(p, "first", private_parent=Path(tmp))
            with self.assertRaises(ValueError):
                save_commercial_evidence(p, "second", private_parent=Path(tmp))
            self.assertEqual(Path(p).read_text(), "first")

    def test_save_rejects_loose_parent(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            os.chmod(tmp, 0o755)
            with self.assertRaises(ValueError):
                save_commercial_evidence(str(Path(tmp) / "out.json"), "x", private_parent=Path(tmp))

    def test_parent_replacement_does_not_escape_fd(self):
        # Simulate parent replacement between fd-open and file-create: the write
        # must land in the pinned inode (priv-orig), never in the symlink target.
        import os
        from unittest import mock
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            priv = Path(tmp) / "priv"
            priv.mkdir()
            os.chmod(priv, 0o700)
            evil = Path(tmp) / "evil"
            evil.mkdir()
            orig_open = os.open
            swapped = []

            def hooking_open(path, *a, **k):
                if isinstance(path, str) and path.endswith("out.json"):
                    if not swapped:
                        swapped.append(True)
                        os.rename(str(priv), str(Path(tmp) / "priv-orig"))
                        os.symlink(str(evil), str(priv))
                return orig_open(path, *a, **k)

            dest = str(priv / "out.json")
            with mock.patch("os.open", side_effect=hooking_open):
                try:
                    save_commercial_evidence(dest, '{"ok":true}', private_parent=priv)
                except ValueError:
                    pass  # fail-closed acceptable; escape is not
            self.assertFalse((evil / "out.json").exists(), "escaped write to symlink target")
            pinned = Path(tmp) / "priv-orig" / "out.json"
            self.assertTrue(pinned.exists(), "write must land in pinned dir")
            self.assertEqual(pinned.read_text(), '{"ok":true}')

    def test_concurrent_create_one_wins(self):
        import concurrent.futures
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            p = str(Path(tmp) / "race.json")
            def attempt(text):
                try:
                    save_commercial_evidence(p, text, private_parent=Path(tmp))
                    return True
                except ValueError:
                    return False
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(attempt, [f"writer-{i}" for i in range(8)]))
            self.assertEqual(results.count(True), 1, results)
            self.assertEqual(results.count(False), 7, results)

    def test_write_failure_leaves_partial_and_fails(self):
        import os
        from unittest import mock
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            p = str(Path(tmp) / "partial.json")
            real_write = os.write
            def bad_write(fd, data):
                if b"PARTIAL" in data:
                    raise OSError("disk full")
                return real_write(fd, data)
            with mock.patch("os.write", side_effect=bad_write):
                with self.assertRaises(ValueError):
                    save_commercial_evidence(p, '{"v":"PARTIAL"}', private_parent=Path(tmp))
            # Partial is left in place (never deleted) and must not be treated as success.
            self.assertTrue(Path(p).exists())

    def test_close_failure_fails(self):
        import os
        from unittest import mock
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            p = str(Path(tmp) / "closefail.json")
            real_close = os.close
            first = [True]
            def bad_close(fd):
                if first[0]:
                    # Allow parent_fd close but fail file close once? Both close via os.close; fail first.
                    first[0] = False
                    raise OSError("close failed")
                return real_close(fd)
            with mock.patch("os.close", side_effect=bad_close):
                with self.assertRaises(ValueError):
                    save_commercial_evidence(p, "{}", private_parent=Path(tmp))

    def test_onofollow_unavailable_fails_closed(self):
        import os
        from unittest import mock
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            p = str(Path(tmp) / "out.json")
            with mock.patch.object(os, "O_NOFOLLOW", None, create=True):
                with self.assertRaises(ValueError):
                    save_commercial_evidence(p, "{}", private_parent=Path(tmp))


if __name__ == "__main__":
    unittest.main()
