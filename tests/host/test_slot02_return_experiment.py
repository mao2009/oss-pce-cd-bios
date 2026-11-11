"""Host tests for the Issue #756 slot-$02 returning experiment.

Hermetic and synthetic-only: no Geargrafx core, no commercial media, no
reference BIOS. The cc65-gated test proves the assembled stub is RTS-first;
everything else is pure logic (patch accounting, manifest flags, SIMULATED
classification, production-wiring guards).
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "tools"), str(ROOT / "tests" / "integration")]

from build_rom import overrides  # noqa: E402
from build_slot02_return_loader import (  # noqa: E402
    PATCH_LEN, RTS, _clean_variant_files, apply_patch, experiment_manifest,
    patch_preview, resolve_defines,
)
from environment import requires_cc65  # noqa: E402
import geargrafx_slot02_return as S  # noqa: E402


def _call(cycle="cold", caller=0x3014, entry=0xE006, table=True, slot=2,
          sdelta=2, before=0x80, after=0x7E, a=0xAA, check="MATCH", ret=True):
    return {"cycle": cycle, "caller_pc": caller, "entry_pc": entry, "in_table": table,
            "slot": slot, "sp_before": before, "sp_after": after, "sp_delta": sdelta,
            "caller_a": a, "caller_x": 0x11, "caller_y": 0x22, "caller_p": 5,
            "stack_check": check, "return_matches_caller": ret,
            "mpr": [255, 248, 128, 129, 130, 131, 132, 0]}


def _run(cycle="cold", reason="cd_halt_ok", pc=0x3000, x=0, halted=True, step=100, trunc=False):
    return {"cycle": cycle, "reason": reason, "pc": pc, "x": x,
            "halt_confirmed": halted, "step": step, "apicall_truncated": trunc}


def _records(calls, runs):
    return {"CAP": [], "HIT": [], "PHASE": [], "RUN": runs, "APICALL": calls}


class ProductionWiringGuardTests(unittest.TestCase):
    def test_no_slot_02_override_on_disk(self):
        self.assertNotIn(0x02, overrides())
        self.assertFalse((ROOT / "src" / "overrides" / "slot_02.s").exists())

    def test_no_slot_33_override_on_disk(self):
        self.assertNotIn(0x33, overrides())
        self.assertFalse((ROOT / "src" / "overrides" / "slot_33.s").exists())

    def test_experiment_stub_lives_outside_overrides(self):
        stub = ROOT / "tests" / "integration" / "slot02_return_stub.s"
        self.assertTrue(stub.is_file())
        self.assertFalse(str(stub).startswith(str(ROOT / "src" / "overrides")))


class PatchAccountingTests(unittest.TestCase):
    STUB = bytes((RTS,))

    def test_patch_preview_exact_five_bytes(self):
        base = bytearray(0x2000)
        base[0x10A:0x10F] = bytes((0xA2, 0x02, 0x4C, 0x45, 0xE2))
        off, before, after = patch_preview(bytes(base), 0x02, 0xE10A, 0xE245, self.STUB)
        self.assertEqual((off, before, after),
                         (0x10A, bytes((0xA2, 0x02, 0x4C, 0x45, 0xE2)),
                          bytes((0x60, 0xEA, 0xEA, 0xEA, 0xEA))))
        self.assertEqual(len(after), PATCH_LEN)

    def test_patch_rejected_when_before_bytes_differ(self):
        base = bytes(0x2000)
        with self.assertRaises(ValueError):
            patch_preview(base, 0x02, 0xE10A, 0xE245, self.STUB)

    def test_patch_rejected_outside_bios_window(self):
        base = bytes(0x2000)
        with self.assertRaises(ValueError):
            patch_preview(base, 0x02, 0x7FFF, 0xE245, self.STUB)

    def test_apply_patch_containment(self):
        base = bytearray(0x2000)
        base[0x10A:0x10F] = bytes((0xA2, 0x02, 0x4C, 0x45, 0xE2))
        off, before, after = patch_preview(bytes(base), 0x02, 0xE10A, 0xE245, self.STUB)
        patched = apply_patch(bytes(base), off, before, after)
        self.assertEqual(patched[off:off + 5], after)
        self.assertEqual(len(patched), len(base))

    def test_apply_patch_refuses_window_tamper(self):
        # apply_patch guarantees the patch stays inside the verified 5-byte
        # window; base-image integrity itself rests on the trusted normal
        # pipeline (build_slot02_return_bios builds the base in-process and
        # asserts size/vectors). A tamper inside the window breaks the
        # before-bytes and must refuse.
        base = bytearray(0x2000)
        base[0x10A:0x10F] = bytes((0xA2, 0x02, 0x4C, 0x45, 0xE2))
        off, before, after = patch_preview(bytes(base), 0x02, 0xE10A, 0xE245, self.STUB)
        tampered = bytearray(base)
        tampered[0x10B] = 0x03  # corrupt the slot byte inside the window
        with self.assertRaises(ValueError):
            apply_patch(bytes(tampered), off, before, after)

    def test_stub_side_effect_bytes_rejected(self):
        base = bytearray(0x2000)
        base[0x10A:0x10F] = bytes((0xA2, 0x02, 0x4C, 0x45, 0xE2))
        with self.assertRaises(ValueError):
            patch_preview(bytes(base), 0x02, 0xE10A, 0xE245, bytes((0x8D, 0x00, 0x20)))
        with self.assertRaises(ValueError):
            patch_preview(bytes(base), 0x02, 0xE10A, 0xE245, bytes((RTS, 0xEA, 0xEA, 0xEA, 0xEA, 0xEA)))


class VariantCleanupTests(unittest.TestCase):
    def test_clean_removes_only_this_variant(self):
        import tempfile
        with tempfile.TemporaryDirectory(prefix="pce-clean-") as tmp:
            out = Path(tmp)
            keep = ["dev-ipl-loader-bios-not-compatible-OTHER.pce",
                    "manifest-OTHER-experiment.json", "unrelated.txt"]
            drop = ["dev-ipl-loader-bios-not-compatible-v-base.pce",
                    "ipl-loader-bios-v-base.lbl", "ipl-loader-bios-v-base.map",
                    "ipl_loader_v-base.o", "manifest-v-base.json",
                    "dev-ipl-loader-bios-not-compatible-v.pce",
                    "manifest-v-experiment.json"]
            for name in keep + drop:
                (out / name).write_text("x", encoding="ascii")
            _clean_variant_files(out, "v")
            for name in drop:
                self.assertFalse((out / name).exists(), name)
            for name in keep:
                self.assertTrue((out / name).is_file(), name)


class MultiSlotPatchTests(unittest.TestCase):
    STUB = bytes((RTS,))

    def _base_with(self, slots_addrs, unimpl):
        base = bytearray(0x3000)
        for slot, addr in slots_addrs:
            off = addr - 0xE000
            base[off:off + 5] = bytes((0xA2, slot, 0x4C, unimpl & 0xFF, (unimpl >> 8) & 0xFF))
        return bytes(base)

    def test_two_slot_windows(self):
        from build_slot02_return_loader import apply_patches, patch_preview
        base = self._base_with([(0x02, 0xE10A), (0x33, 0xE1A0)], 0xE245)
        patches = [patch_preview(base, s, a, 0xE245, self.STUB)
                   for s, a in ((0x02, 0xE10A), (0x33, 0xE1A0))]
        patched = apply_patches(base, patches)
        self.assertEqual(patched[0x10A:0x10F], bytes((0x60, 0xEA, 0xEA, 0xEA, 0xEA)))
        self.assertEqual(patched[0x1A0:0x1A5], bytes((0x60, 0xEA, 0xEA, 0xEA, 0xEA)))
        diffs = [i for i, (a, b) in enumerate(zip(base, patched)) if a != b]
        self.assertEqual(diffs, list(range(0x10A, 0x10F)) + list(range(0x1A0, 0x1A5)))

    def test_second_slot_wrong_slot_byte_rejected(self):
        from build_slot02_return_loader import patch_preview
        base = self._base_with([(0x33, 0xE1A0)], 0xE245)
        tampered = bytearray(base)
        tampered[0x1A1] = 0x34  # slot byte no longer $33
        with self.assertRaises(ValueError):
            patch_preview(bytes(tampered), 0x33, 0xE1A0, 0xE245, self.STUB)

    def test_empty_slots_rejected(self):
        from build_slot02_return_loader import build_slot02_return_bios
        with self.assertRaises(ValueError):
            build_slot02_return_bios(Path("/nonexistent"), Path("/tmp/nonexistent-out-756"),
                                     0x109, slots=())

    def test_manifest_lists_both_slots(self):
        m = experiment_manifest("base.pce", "exp.pce", b"\x00" * 256 * 1024,
                                {"api_unimplemented": 0xE245}, "redacted", "debug",
                                "slot02-33ret", ["-D", "IPL_PUBLIC"], (0x02, 0x33),
                                issue=758)
        self.assertEqual(m["slots"], ["$02", "$33"])
        self.assertEqual(m["patch"]["targets"], ["api_slot_02", "api_slot_33"])
        self.assertFalse(m["release_eligible"])
        self.assertEqual(m["issue"], 758)
        self.assertIn("NOT genuine System Card behavior", m["assumption"])

    def test_allowlist_rejects_nonlisted_combinations(self):
        from build_slot02_return_loader import ALLOWED_EXPERIMENTS, build_slot02_return_bios
        self.assertEqual(ALLOWED_EXPERIMENTS, {(0x02,): 756, (0x02, 0x33): 758})
        bad = [((0x02, 0x02), 756),  # duplicate
               ((0x33,), 758),  # unlisted set
               ((0x33, 0x02), 758),  # unlisted order
               ((0x02, 0x33), 756),  # issue mismatch
               ((0x02,), 999),  # unknown issue
               ((), 756)]  # empty
        for slots, issue in bad:
            with self.assertRaises(ValueError, msg=f"{slots}/{issue}"):
                build_slot02_return_bios(Path("/nonexistent"), Path("/tmp/nonexistent-out-allow"),
                                         0x109, slots=slots, issue=issue)


class ManifestFlagTests(unittest.TestCase):
    def test_experiment_manifest_is_simulated_only(self):
        m = experiment_manifest("base.pce", "exp.pce", b"\x00" * 256 * 1024,
                                {"api_unimplemented": 0xE245}, "redacted", "debug",
                                "slot02ret", ["-D", "IPL_PUBLIC"])
        self.assertFalse(m["release_eligible"])
        self.assertTrue(m["experiment_only"])
        self.assertEqual(m["system_card_abi"], "NO_CLAIM")
        self.assertIn("SIMULATED", m["status"])
        self.assertIn("SIMULATED_", m["verdict_prefix"])
        self.assertIn("NOT genuine System Card behavior", m["assumption"])
        self.assertEqual(m["issue"], 756)


class ClassifySimulatedTests(unittest.TestCase):
    STUB = 0xE245

    def test_next_api_sequence(self):
        calls = [_call("cold", a=0xAA), _call("cold", entry=0xE009, slot=3, a=0xCC),
                 _call("warm", a=0xAA), _call("warm", entry=0xE009, slot=3, a=0xCC)]
        runs = [_run("cold", reason="api_unimplemented", pc=self.STUB, x=3),
                _run("warm", reason="api_unimplemented", pc=self.STUB, x=3)]
        verdict, detail = S.classify_simulated(_records(calls, runs), self.STUB)
        self.assertEqual(verdict, "SIMULATED_NEXT_API")
        self.assertIn("(2, 'JSR')", detail)
        self.assertIn("non-returning", detail)

    def test_next_api_rejects_x_mismatch(self):
        # Halt X names slot 2 while the last recorded call is slot 3: the
        # halt cannot be explained by the trace, never a success.
        calls = [_call("cold", a=0xAA), _call("cold", entry=0xE009, slot=3, a=0xCC),
                 _call("warm", a=0xAA), _call("warm", entry=0xE009, slot=3, a=0xCC)]
        runs = [_run("cold", reason="api_unimplemented", pc=self.STUB, x=2),
                _run("warm", reason="api_unimplemented", pc=self.STUB, x=2)]
        verdict, _ = S.classify_simulated(_records(calls, runs), self.STUB)
        self.assertEqual(verdict, "SIMULATED_UNEXPECTED_FIRST")

    def test_next_api_rejects_pc_mismatch(self):
        # Right reason, wrong halt PC: reason-only matching is refused.
        calls = [_call("cold", a=0xAA), _call("cold", entry=0xE009, slot=3, a=0xCC),
                 _call("warm", a=0xAA), _call("warm", entry=0xE009, slot=3, a=0xCC)]
        runs = [_run("cold", reason="api_unimplemented", pc=0xF000, x=3),
                _run("warm", reason="api_unimplemented", pc=0xF000, x=3)]
        verdict, _ = S.classify_simulated(_records(calls, runs), self.STUB)
        self.assertEqual(verdict, "SIMULATED_UNEXPECTED_FIRST")

    def test_next_api_requires_verified_stub(self):
        calls = [_call("cold", a=0xAA), _call("cold", entry=0xE009, slot=3, a=0xCC),
                 _call("warm", a=0xAA), _call("warm", entry=0xE009, slot=3, a=0xCC)]
        runs = [_run("cold", reason="api_unimplemented", pc=self.STUB, x=3),
                _run("warm", reason="api_unimplemented", pc=self.STUB, x=3)]
        verdict, _ = S.classify_simulated(_records(calls, runs), None)
        self.assertEqual(verdict, "SIMULATED_UNEXPECTED_FIRST")

    def test_single_call_stub_halt_is_unexpected(self):
        # One recorded slot-2 call cannot reach the stub on the experiment
        # ROM: an unrecorded entry must exist. Never a clean return.
        calls = [_call("cold"), _call("warm")]
        runs = [_run("cold", reason="api_unimplemented", pc=self.STUB, x=2),
                _run("warm", reason="api_unimplemented", pc=self.STUB, x=2)]
        verdict, _ = S.classify_simulated(_records(calls, runs), self.STUB)
        self.assertEqual(verdict, "SIMULATED_UNEXPECTED_FIRST")

    def test_mixed_halt_outcomes_diverge(self):
        calls = [_call("cold", a=0xAA), _call("cold", entry=0xE009, slot=3, a=0xCC),
                 _call("warm", a=0xAA), _call("warm", entry=0xE009, slot=3, a=0xCC)]
        runs = [_run("cold", reason="api_unimplemented", pc=self.STUB, x=3),
                _run("warm", reason="cd_halt_fail", pc=0xF100)]
        verdict, _ = S.classify_simulated(_records(calls, runs), self.STUB)
        self.assertEqual(verdict, "SIMULATED_DIVERGENT")

    def test_first_call_unconfirmed_is_unexpected(self):
        # stack_check MISMATCH (or non-WRAM MPR1): JSR evidence missing.
        bad = _call("cold", check="MISMATCH", ret=False)
        calls = [bad, _call("cold", entry=0xE009, slot=3),
                 _call("warm"), _call("warm", entry=0xE009, slot=3)]
        runs = [_run("cold", reason="api_unimplemented", pc=self.STUB, x=3),
                _run("warm", reason="api_unimplemented", pc=self.STUB, x=3)]
        verdict, _ = S.classify_simulated(_records(calls, runs), self.STUB)
        self.assertEqual(verdict, "SIMULATED_UNEXPECTED_FIRST")

    def test_return_then_halt_single_call(self):
        calls = [_call("cold"), _call("warm")]
        runs = [_run("cold", reason="cd_halt_fail", pc=0xF100),
                _run("warm", reason="cd_halt_fail", pc=0xF100)]
        verdict, _ = S.classify_simulated(_records(calls, runs), self.STUB)
        self.assertEqual(verdict, "SIMULATED_RETURN_THEN_HALT")

    def test_pre_halt_no_calls(self):
        runs = [_run("cold", reason="cd_halt_fail", pc=0xF100),
                _run("warm", reason="cd_halt_fail", pc=0xF100)]
        verdict, _ = S.classify_simulated(_records([], runs), self.STUB)
        self.assertEqual(verdict, "SIMULATED_PRE_HALT")

    def test_unexpected_first_call(self):
        calls = [_call("cold", entry=0xE009, slot=3), _call("warm", entry=0xE009, slot=3)]
        runs = [_run("cold"), _run("warm")]
        verdict, _ = S.classify_simulated(_records(calls, runs), self.STUB)
        self.assertEqual(verdict, "SIMULATED_UNEXPECTED_FIRST")

    def test_divergent_cycles(self):
        calls = [_call("cold", a=0xAA), _call("cold", entry=0xE009, slot=3, a=0xCC),
                 _call("warm")]
        runs = [_run("cold"), _run("warm", reason="cd_halt_fail", pc=0xF100)]
        verdict, _ = S.classify_simulated(_records(calls, runs), self.STUB)
        self.assertEqual(verdict, "SIMULATED_DIVERGENT")

    def test_budget_and_truncation(self):
        runs = [_run("cold", reason="budget", halted=False, step=80_000_000),
                _run("warm", reason="budget", halted=False, step=80_000_000)]
        verdict, _ = S.classify_simulated(_records([_call("cold")], runs), self.STUB)
        self.assertEqual(verdict, "SIMULATED_BUDGET")
        runs2 = [_run("cold", trunc=True), _run("warm")]
        verdict2, _ = S.classify_simulated(_records([_call("cold"), _call("warm")], runs2), self.STUB)
        self.assertEqual(verdict2, "SIMULATED_INCOMPLETE")

    def test_missing_run_is_fail(self):
        verdict, _ = S.classify_simulated(_records([], [_run("cold")]), self.STUB)
        self.assertEqual(verdict, "FAIL")

    def test_two_slot_prefix_next_api(self):
        prefix = ((2, "JSR"), (51, "JSR"))
        calls = [_call("cold", a=0xAA), _call("cold", entry=0xE099, slot=51, a=0xCC),
                 _call("cold", entry=0xE009, slot=3, a=0xDD),
                 _call("warm", a=0xAA), _call("warm", entry=0xE099, slot=51, a=0xCC),
                 _call("warm", entry=0xE009, slot=3, a=0xDD)]
        runs = [_run("cold", reason="api_unimplemented", pc=self.STUB, x=3),
                _run("warm", reason="api_unimplemented", pc=self.STUB, x=3)]
        verdict, detail = S.classify_simulated(_records(calls, runs), self.STUB, prefix)
        self.assertEqual(verdict, "SIMULATED_NEXT_API")
        self.assertIn("non-returning", detail)

    def test_two_slot_prefix_short_sequence_unexpected(self):
        # Only the first assumed call recorded, then a stub halt: the trace
        # cannot explain the halt.
        prefix = ((2, "JSR"), (51, "JSR"))
        calls = [_call("cold"), _call("warm")]
        runs = [_run("cold", reason="api_unimplemented", pc=self.STUB, x=2),
                _run("warm", reason="api_unimplemented", pc=self.STUB, x=2)]
        verdict, _ = S.classify_simulated(_records(calls, runs), self.STUB, prefix)
        self.assertEqual(verdict, "SIMULATED_UNEXPECTED_FIRST")

    def test_two_slot_prefix_second_call_mismatch(self):
        prefix = ((2, "JSR"), (51, "JSR"))
        calls = [_call("cold"), _call("cold", entry=0xE009, slot=3),
                 _call("warm"), _call("warm", entry=0xE009, slot=3)]
        runs = [_run("cold", reason="api_unimplemented", pc=self.STUB, x=3),
                _run("warm", reason="api_unimplemented", pc=self.STUB, x=3)]
        verdict, _ = S.classify_simulated(_records(calls, runs), self.STUB, prefix)
        self.assertEqual(verdict, "SIMULATED_UNEXPECTED_FIRST")

    def test_two_slot_prefix_clean_return_then_halt(self):
        prefix = ((2, "JSR"), (51, "JSR"))
        calls = [_call("cold"), _call("cold", entry=0xE099, slot=51),
                 _call("warm"), _call("warm", entry=0xE099, slot=51)]
        runs = [_run("cold", reason="cd_halt_fail", pc=0xF100),
                _run("warm", reason="cd_halt_fail", pc=0xF100)]
        verdict, _ = S.classify_simulated(_records(calls, runs), self.STUB, prefix)
        self.assertEqual(verdict, "SIMULATED_RETURN_THEN_HALT")

    def test_caller_pc_mismatch_diverges(self):
        a = _call("cold")
        b = dict(_call("warm"))
        b["caller_pc"] = 0x3015
        runs = [_run("cold", reason="api_unimplemented", pc=self.STUB, x=2),
                _run("warm", reason="api_unimplemented", pc=self.STUB, x=2)]
        verdict, detail = S.classify_simulated(_records([a, b], runs), self.STUB)
        self.assertEqual(verdict, "SIMULATED_DIVERGENT")
        self.assertIn("caller_pc", detail)

    def test_entry_regs_mismatch_diverges(self):
        a = _call("cold")
        b = dict(_call("warm"))
        b["caller_a"] = 0x03
        runs = [_run("cold", reason="api_unimplemented", pc=self.STUB, x=2),
                _run("warm", reason="api_unimplemented", pc=self.STUB, x=2)]
        verdict, _ = S.classify_simulated(_records([a, b], runs), self.STUB)
        self.assertEqual(verdict, "SIMULATED_DIVERGENT")

    def test_mpr_mismatch_diverges(self):
        a = _call("cold")
        b = dict(_call("warm"))
        b["mpr"] = [255, 248, 128, 129, 130, 131, 132, 1]
        runs = [_run("cold", reason="api_unimplemented", pc=self.STUB, x=2),
                _run("warm", reason="api_unimplemented", pc=self.STUB, x=2)]
        verdict, _ = S.classify_simulated(_records([a, b], runs), self.STUB)
        self.assertEqual(verdict, "SIMULATED_DIVERGENT")

    def test_sp_mismatch_diverges(self):
        a = _call("cold")
        b = dict(_call("warm"))
        b["sp_before"] = 0x7F
        b["sp_after"] = 0x7D  # consistent JSR shape, different SP level
        runs = [_run("cold", reason="api_unimplemented", pc=self.STUB, x=2),
                _run("warm", reason="api_unimplemented", pc=self.STUB, x=2)]
        verdict, detail = S.classify_simulated(_records([a, b], runs), self.STUB)
        self.assertEqual(verdict, "SIMULATED_DIVERGENT")
        self.assertIn("sp_before", detail)

    def test_run_record_negatives(self):
        # MEDIUM-3 RED set: malformed RUN records are FAIL, never success.
        base = _run("cold")
        bads = []
        no_reason = dict(base); del no_reason["reason"]
        bads.append(no_reason)
        no_pc = dict(base); del no_pc["pc"]
        bads.append(no_pc)
        no_x = dict(base); del no_x["x"]
        bads.append(no_x)
        oob_pc = dict(base); oob_pc["pc"] = 0x10000
        bads.append(oob_pc)
        oob_x = dict(base); oob_x["x"] = 256
        bads.append(oob_x)
        str_conf = dict(base); str_conf["halt_confirmed"] = "true"
        bads.append(str_conf)
        str_trunc = dict(base); str_trunc["apicall_truncated"] = "0"
        bads.append(str_trunc)
        self.assertEqual(len(bads), 7)
        for bad in bads:
            valid, _ = S.validate_run(bad)
            self.assertFalse(valid, bad)
        # One-sided omission is equally malformed.
        one = dict(_run("warm")); del one["pc"]
        valid, _ = S.validate_run(one)
        self.assertFalse(valid)
        # Malformed RUNs never classify: FAIL even with a clean call.
        bad_warm = dict(_run("warm"))
        del bad_warm["pc"]
        verdict, _ = S.classify_simulated(
            _records([_call("cold")], [_run("cold"), bad_warm]), self.STUB)
        self.assertEqual(verdict, "FAIL")
        # A valid RUN still classifies normally.
        valid, _ = S.validate_run(_run("cold"))
        self.assertTrue(valid)

    def test_steps_may_differ(self):
        # Step counts legitimately differ cold/warm; they must not force DIVERGENT.
        calls = [_call("cold", a=0xAA), _call("cold", entry=0xE009, slot=3, a=0xCC),
                 _call("warm", a=0xAA), _call("warm", entry=0xE009, slot=3, a=0xCC)]
        runs = [_run("cold", reason="api_unimplemented", pc=self.STUB, x=3, step=100),
                _run("warm", reason="api_unimplemented", pc=self.STUB, x=3, step=200)]
        verdict, _ = S.classify_simulated(_records(calls, runs), self.STUB)
        self.assertEqual(verdict, "SIMULATED_NEXT_API")

    def test_halts_match_pure(self):
        ok, _ = S.halts_match(_run("cold"), _run("warm"))
        self.assertTrue(ok)
        ok2, why = S.halts_match(_run("cold", pc=0xF100), _run("warm", pc=0xF101))
        self.assertFalse(ok2)
        self.assertIn("halt pc", why)
    def test_halt_state_mismatch_diverges(self):
        # MEDIUM-1 RED set: halt reason/PC/X must agree on the
        # PRE_HALT and RETURN_THEN_HALT paths.
        base_calls = lambda: [_call("cold"), _call("warm")]
        pre_runs = lambda rc, pc, x: [_run("cold", reason=rc, pc=pc, x=x),
                                      _run("warm", reason=rc, pc=pc, x=x)]
        # PRE_HALT mismatches.
        for runs in ([_run("cold", reason="cd_halt_fail", pc=0xF100),
                       _run("warm", reason="cd_halt_ok", pc=0x3000)],
                      [_run("cold", reason="cd_halt_fail", pc=0xF100),
                       _run("warm", reason="cd_halt_fail", pc=0xF200)],
                      [_run("cold", reason="cd_halt_fail", pc=0xF100, x=0),
                       _run("warm", reason="cd_halt_fail", pc=0xF100, x=1)]):
            verdict, detail = S.classify_simulated(_records([], runs), self.STUB)
            self.assertEqual(verdict, "SIMULATED_DIVERGENT", runs)
            self.assertIn("halt", detail)
        # Matched PRE_HALT still classifies.
        verdict, _ = S.classify_simulated(
            _records([], pre_runs("cd_halt_fail", 0xF100, 0)), self.STUB)
        self.assertEqual(verdict, "SIMULATED_PRE_HALT")
        # RETURN_THEN_HALT mismatches.
        for runs in ([_run("cold", reason="cd_halt_fail", pc=0xF100),
                       _run("warm", reason="cd_unexpected_interrupt", pc=0xF100)],
                      [_run("cold", reason="cd_halt_fail", pc=0xF100),
                       _run("warm", reason="cd_halt_fail", pc=0xF101)],
                      [_run("cold", reason="cd_halt_fail", pc=0xF100, x=0),
                       _run("warm", reason="cd_halt_fail", pc=0xF100, x=2)]):
            verdict, detail = S.classify_simulated(_records(base_calls(), runs), self.STUB)
            self.assertEqual(verdict, "SIMULATED_DIVERGENT", runs)
            self.assertIn("halt", detail)
        # Matched RETURN_THEN_HALT still classifies.
        verdict, _ = S.classify_simulated(
            _records(base_calls(), pre_runs("cd_halt_fail", 0xF100, 0)), self.STUB)
        self.assertEqual(verdict, "SIMULATED_RETURN_THEN_HALT")

    def test_calls_match_pure(self):
        ok, _ = S.calls_match([_call("cold")], [_call("warm")])
        self.assertTrue(ok)
        ok2, why = S.calls_match([_call("cold")], [])
        self.assertFalse(ok2)
        self.assertIn("counts", why)

    def test_calls_match_missing_field(self):
        a = dict(_call("cold"))
        del a["caller_pc"]
        ok, why = S.calls_match([a], [_call("warm")])
        self.assertFalse(ok)
        self.assertIn("caller_pc", why)
        b = dict(_call("cold"))
        del b["caller_pc"]
        ok2, _ = S.calls_match([a], [b])
        self.assertFalse(ok2)  # missing on both sides still mismatches

    def test_both_sides_missing_field_is_unexpected(self):
        # Validation runs before comparison: corrupt records can never match.
        a = dict(_call("cold"))
        del a["caller_pc"]
        b = dict(_call("warm"))
        del b["caller_pc"]
        runs = [_run("cold"), _run("warm")]
        verdict, _ = S.classify_simulated(_records([a, b], runs), self.STUB)
        self.assertEqual(verdict, "SIMULATED_UNEXPECTED_FIRST")

    def test_validate_table_call_negatives(self):
        base = _call("cold")
        ok, _ = S.validate_table_call(dict(base))
        self.assertTrue(ok)
        bads = []
        no_caller = dict(base); del no_caller["caller_pc"]  # 1: missing caller_pc
        bads.append(no_caller)
        no_entry = dict(base); del no_entry["entry_pc"]  # 2: missing entry_pc
        bads.append(no_entry)
        mismatch = dict(base); mismatch["slot"] = 3  # 3: slot/entry mismatch
        bads.append(mismatch)
        oob = dict(base); oob["slot"] = 0x51; oob["entry_pc"] = 0xE000 + 0x51 * 3  # 4: out of range
        bads.append(oob)
        short_mpr = dict(base); short_mpr["mpr"] = [255] * 7  # 5: short MPR
        bads.append(short_mpr)
        bad_mpr = dict(base); bad_mpr["mpr"] = [255] * 7 + [256]  # 6: MPR byte OOB
        bads.append(bad_mpr)
        bad_sp = dict(base); bad_sp["sp_delta"] = 256  # 7: bad SP
        bads.append(bad_sp)
        bad_ret = dict(base); bad_ret["return_matches_caller"] = "yes"  # 8: bad return flag
        bads.append(bad_ret)
        bad_type = dict(base); bad_type["caller_pc"] = "0x3014"  # 9: type mismatch
        bads.append(bad_type)
        self.assertEqual(len(bads), 9)
        for bad in bads:
            valid, _why = S.validate_table_call(bad)
            self.assertFalse(valid, bad)
        # Corrupt records never classify as a call sequence.
        runs = [_run("cold"), _run("warm")]
        verdict, _ = S.classify_simulated(
            _records([dict(base), dict(base)], runs), self.STUB)
        self.assertNotEqual(verdict, "FAIL")  # sanity: the valid twin classifies
        for bad in bads:
            calls = [dict(bad, cycle="cold"), dict(_call("warm"))]
            verdict, _ = S.classify_simulated(_records(calls, runs), self.STUB)
            self.assertEqual(verdict, "SIMULATED_UNEXPECTED_FIRST", bad)

    def test_exit_code_mapping(self):
        self.assertEqual(S.exit_code_for_verdict("SIMULATED_NEXT_API"), 0)
        self.assertEqual(S.exit_code_for_verdict("SIMULATED_RETURN_THEN_HALT"), 0)
        for abnormal in ("SIMULATED_PRE_HALT", "SIMULATED_BUDGET", "SIMULATED_INCOMPLETE",
                         "SIMULATED_DIVERGENT", "SIMULATED_UNEXPECTED_FIRST", "FAIL",
                         "UNKNOWN", ""):
            self.assertEqual(S.exit_code_for_verdict(abnormal), 1, abnormal)

    def test_resolve_defines(self):
        self.assertEqual(resolve_defines(None), ["-D", "IPL_PUBLIC"])
        self.assertEqual(resolve_defines([]), [])
        self.assertEqual(resolve_defines(["-D", "X"]), ["-D", "X"])

    def _receipt_env(self, tmp, variant="slot02ret", slots=(0x02,), issue=756):
        import hashlib
        import json
        out = Path(tmp)
        rom_name = f"dev-ipl-loader-bios-not-compatible-{variant}.pce"
        rom_bytes = b"\x00" * 256 * 1024
        (out / rom_name).write_bytes(rom_bytes)
        exp_sha = hashlib.sha256(rom_bytes).hexdigest()
        payload = S.synthetic_receipt_payload(variant, slots, "debug", rom_name, exp_sha,
                                              S.collect_code_hashes(), S._git_head(), issue)
        (out / S.receipt_path(out, slots, "debug").name).write_text(
            json.dumps(payload), encoding="utf-8")
        return out, payload

    def test_receipt_round_trip_and_tamper(self):
        import tempfile
        with tempfile.TemporaryDirectory(prefix="pce-receipt-") as tmp:
            out, _payload = self._receipt_env(tmp)
            S.check_synthetic_receipt(out, "slot02ret", (0x02,), "debug", 756)  # must not raise
            # Missing receipt BLOCKs.
            (out / "synthetic-receipt-02-debug.json").unlink()
            with self.assertRaises(ValueError):
                S.check_synthetic_receipt(out, "slot02ret", (0x02,), "debug", 756)

    def test_receipt_variant_mismatch(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory(prefix="pce-receipt-") as tmp:
            out, payload = self._receipt_env(tmp)
            wrong = dict(payload, variant="ATTACKER")
            (out / "synthetic-receipt-02-debug.json").write_text(
                json.dumps(wrong), encoding="utf-8")
            with self.assertRaises(ValueError):
                S.check_synthetic_receipt(out, "slot02ret", (0x02,), "debug", 756)

    def test_receipt_rom_hash_mismatch(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory(prefix="pce-receipt-") as tmp:
            out, payload = self._receipt_env(tmp)
            wrong = dict(payload, exp_rom_sha256="2" * 64)
            (out / "synthetic-receipt-02-debug.json").write_text(
                json.dumps(wrong), encoding="utf-8")
            with self.assertRaises(ValueError):
                S.check_synthetic_receipt(out, "slot02ret", (0x02,), "debug", 756)

    def test_receipt_rom_missing(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory(prefix="pce-receipt-") as tmp:
            out, payload = self._receipt_env(tmp)
            (out / payload["exp_rom_name"]).unlink()
            with self.assertRaises(ValueError):
                S.check_synthetic_receipt(out, "slot02ret", (0x02,), "debug", 756)

    def test_receipt_rom_tampered(self):
        import tempfile
        with tempfile.TemporaryDirectory(prefix="pce-receipt-") as tmp:
            out, payload = self._receipt_env(tmp)
            with open(out / payload["exp_rom_name"], "r+b") as f:
                f.seek(0x10A)
                f.write(b"\x60")
            with self.assertRaises(ValueError):
                S.check_synthetic_receipt(out, "slot02ret", (0x02,), "debug", 756)

    def test_receipt_bad_sha_format(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory(prefix="pce-receipt-") as tmp:
            out, payload = self._receipt_env(tmp)
            for bad in ("xyz", "1" * 63, "1" * 65, "G" * 64, 12345):
                wrong = dict(payload, exp_rom_sha256=bad)
                (out / "synthetic-receipt-02-debug.json").write_text(
                    json.dumps(wrong), encoding="utf-8")
                with self.assertRaises(ValueError, msg=repr(bad)):
                    S.check_synthetic_receipt(out, "slot02ret", (0x02,), "debug", 756)

    def test_receipt_probe_code_change_blocks(self):
        # Uncommitted or swapped probe/dependency source changes the content
        # hash; the stored receipt must then refuse to unlock anything.
        import json
        import tempfile
        with tempfile.TemporaryDirectory(prefix="pce-receipt-") as tmp:
            out, payload = self._receipt_env(tmp)
            code = dict(payload["code_sha256"])
            code["tests/integration/geargrafx_api_entry_probe.cpp"] = "3" * 64
            wrong = dict(payload, code_sha256=code)
            (out / "synthetic-receipt-02-debug.json").write_text(
                json.dumps(wrong), encoding="utf-8")
            with self.assertRaises(ValueError):
                S.check_synthetic_receipt(out, "slot02ret", (0x02,), "debug", 756)

    def test_receipt_hash_set_must_match(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory(prefix="pce-receipt-") as tmp:
            out, payload = self._receipt_env(tmp)
            code = dict(payload["code_sha256"])
            del code["tools/build_rom.py"]  # dropping a file must BLOCK
            wrong = dict(payload, code_sha256=code)
            (out / "synthetic-receipt-02-debug.json").write_text(
                json.dumps(wrong), encoding="utf-8")
            with self.assertRaises(ValueError):
                S.check_synthetic_receipt(out, "slot02ret", (0x02,), "debug", 756)

    def test_receipt_head_mismatch(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory(prefix="pce-receipt-") as tmp:
            out, payload = self._receipt_env(tmp)
            wrong = dict(payload, git_head="0" * 40)
            (out / "synthetic-receipt-02-debug.json").write_text(
                json.dumps(wrong), encoding="utf-8")
            with self.assertRaises(ValueError):
                S.check_synthetic_receipt(out, "slot02ret", (0x02,), "debug", 756)

    def test_receipt_wrong_combo(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory(prefix="pce-receipt-") as tmp:
            out, _payload = self._receipt_env(tmp)
            with self.assertRaises(ValueError):
                S.check_synthetic_receipt(out, "slot02ret", (0x02, 0x33), "debug", 756)
            with self.assertRaises(ValueError):
                S.check_synthetic_receipt(out, "slot02ret", (0x02,), "debug", 758)
            with self.assertRaises(ValueError):
                S.check_synthetic_receipt(out, "slot02ret", (0x02,), "release", 756)
            with self.assertRaises(ValueError):
                S.check_synthetic_receipt(out, "nope", (0x02,), "debug", 756)

    def test_never_confirms(self):
        calls = [_call("cold"), _call("warm")]
        runs = [_run("cold"), _run("warm")]
        verdict, _ = S.classify_simulated(_records(calls, runs), self.STUB)
        self.assertTrue(verdict.startswith("SIMULATED_"))
        self.assertNotIn("CONFIRMED", verdict)

    def test_sp_consistency_positives(self):
        # Genuine JSR evidence shape: delta == (before - after) mod 256.
        ok, _ = S.validate_table_call(_call())  # 0x80/0x7E/2
        self.assertTrue(ok)
        wrap = _call(before=0x01, after=0xFF)  # (0x01 - 0xFF) & 0xFF == 2
        ok, _ = S.validate_table_call(wrap)
        self.assertTrue(ok)
        jmp = _call(sdelta=0, after=0x80)  # JMP pushes nothing
        ok, _ = S.validate_table_call(jmp)
        self.assertTrue(ok)

    def test_sp_consistency_negatives(self):
        # MEDIUM-2 RED set: inconsistent SP evidence must never classify.
        bads = [
            _call(after=0x80),                      # 0x80/0x80/2: delta wrong
            _call(after=0x7E, sdelta=1),            # 0x80/0x7E/1: delta wrong
            _call(before=0x01, after=0x00),         # wraparound mismatch
            _call(sdelta=256),                      # out of byte range
        ]
        for bad in bads:
            valid, why = S.validate_table_call(bad)
            self.assertFalse(valid, bad)
            self.assertIn("sp", why)

    def test_stack_return_flag_pairing(self):
        # Probe contract pairings: only (MATCH,True), (MISMATCH,False),
        # (UNKNOWN,None) are genuine; anything else is inconsistent.
        for check, ret in (("MATCH", True), ("MISMATCH", False), ("UNKNOWN", None)):
            valid, _ = S.validate_table_call(_call(check=check, ret=ret))
            self.assertTrue(valid, (check, ret))
        for check, ret in (("MATCH", False), ("MATCH", None), ("MISMATCH", True),
                           ("MISMATCH", None), ("UNKNOWN", True), ("UNKNOWN", False),
                           ("bogus", True)):
            valid, _ = S.validate_table_call(_call(check=check, ret=ret))
            self.assertFalse(valid, (check, ret))


class StubAssemblyTests(unittest.TestCase):
    @requires_cc65
    def test_assembled_stub_is_rts_first(self):
        import tempfile
        from build_slot02_return_loader import assemble_stub_bytes
        with tempfile.TemporaryDirectory(prefix="pce-slot02ret-host-") as tmp:
            code = assemble_stub_bytes(Path(tmp))
        self.assertEqual(code[0], RTS)
        self.assertLessEqual(len(code), PATCH_LEN)
        self.assertTrue(all(b in (RTS, 0xEA) for b in code))


if __name__ == "__main__":
    unittest.main()
