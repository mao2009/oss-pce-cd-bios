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
    patch_preview,
)
from environment import requires_cc65  # noqa: E402
import geargrafx_slot02_return as S  # noqa: E402


def _call(cycle="cold", caller=0x3014, entry=0xE006, table=True, slot=2,
          sdelta=2, before=0x80, after=0x80, a=0xAA, check="MATCH", ret=True):
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
                                "slot02-33ret", ["-D", "IPL_PUBLIC"], (0x02, 0x33))
        self.assertEqual(m["slots"], ["$02", "$33"])
        self.assertEqual(m["patch"]["targets"], ["api_slot_02", "api_slot_33"])
        self.assertFalse(m["release_eligible"])
        self.assertIn("NOT genuine CD_BASE", m["assumption"])


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
        self.assertIn("NOT genuine CD_BASE", m["assumption"])
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

    def test_never_confirms(self):
        calls = [_call("cold"), _call("warm")]
        runs = [_run("cold"), _run("warm")]
        verdict, _ = S.classify_simulated(_records(calls, runs), self.STUB)
        self.assertTrue(verdict.startswith("SIMULATED_"))
        self.assertNotIn("CONFIRMED", verdict)

    def test_sp_balance(self):
        self.assertTrue(S.sp_balanced([_call(before=0x80, after=0x7E), _call(before=0x7E, after=0x80)]))
        self.assertFalse(S.sp_balanced([_call(before=0x80, after=0x7E), _call(before=0x7E, after=0x7D)]))
        self.assertFalse(S.sp_balanced([]))


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
