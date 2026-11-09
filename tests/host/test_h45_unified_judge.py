#!/usr/bin/env python3
"""Issue #740 unified judge host tests (synthetic HOST_FIXTURE only)."""
import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import h45_unified_judge as U  # noqa: E402

PINNED = U.PINNED
DENY = U.DENY_KEYS


def _stub_a():
    def judge_allowlist(snap):
        if not isinstance(snap, dict):
            return U.BLOCKED, "stub: not dict"
        if snap.get("jsr_count") != 0:
            return U.FAIL, "stub: jsr_count"
        if snap.get("execution") != "BLOCKED":
            return U.FAIL, "stub: execution"
        if snap.get("release_eligible") is not False:
            return U.FAIL, "stub: release"
        if snap.get("build") != "debugger":
            return U.BLOCKED, "stub: build"
        if snap.get("first_opcode") in (0x03, 0x13, 0x23):
            return U.BLOCKED, "stub: ST"
        if snap.get("first_opcode_screened") is not True or snap.get("peek_ok") is not True:
            return U.BLOCKED, "stub: screen"
        if snap.get("vdc_changed") is not False or snap.get("halt_equal") is not True:
            return U.BLOCKED, "stub: vdc/halt"
        if snap.get("mmio_stores"):
            return U.BLOCKED, "stub: mmio"
        mpr = snap.get("mpr")
        if not isinstance(mpr, list) or len(mpr) != 8:
            return U.BLOCKED, "stub: mpr"
        if any(v == 0xFF for v in mpr[:7]) or mpr[1] != 0xF8:
            return U.FAIL, "stub: mpr ff/f8"
        if snap.get("alias_detected") is not False:
            return U.BLOCKED, "stub: alias"
        if snap.get("irq_status") != "OBSERVED":
            return U.BLOCKED, "stub: irq status"
        if snap.get("rom_sha256") != snap.get("expected_sha256") or not isinstance(snap.get("rom_sha256"), str):
            return U.BLOCKED, "stub: hash"
        if not isinstance(snap.get("budget"), int) or not 4 <= snap["budget"] <= 1000000:
            return U.BLOCKED, "stub: budget"
        if snap.get("pc") is None or not (0x1E00 <= snap["pc"] < 0x4000):
            return U.FAIL if isinstance(snap.get("pc"), int) else U.BLOCKED, "stub: pc"
        deny = snap.get("deny_flags")
        if not isinstance(deny, dict) or any(deny.get(k) is not False for k in DENY):
            return U.BLOCKED, "stub: deny"
        return U.PASS, "stub allowlist"

    def judge_irq(row, vector, expected_handler, build="debugger"):
        if build != "debugger":
            return U.BLOCKED, "stub: libretro"
        if not isinstance(row, dict) or row.get("vector") != vector or row.get("exit") != 0:
            return U.BLOCKED, "stub: irq row"
        if row.get("stop_pc") != expected_handler or row.get("handler_instructions") != 0:
            return U.FAIL, "stub: stop"
        if row.get("execbp_hit") is not True:
            return U.FAIL, "stub: execbp"
        if type(row.get("pending")) is not bool or type(row.get("p_i")) is not bool:
            return U.BLOCKED, "stub: pending"
        return U.PASS, "stub irq"

    return SimpleNamespace(PINNED_CORE_REVISION=PINNED, judge_allowlist=judge_allowlist, judge_irq_first_instruction=judge_irq)


def _stub_b():
    def preflight(op, src, dst, length, mpr, window, allowed_banks, build=None, same_value=False, temp_restore=False):
        base = {"gate": U.BLOCKED, "execution": U.BLOCKED, "jsr_count": 0}
        if op not in ("TII", "TIN", "TDD", "TIA", "TAI"):
            return {**base, "op": op, "verdict": "REFUSED_PREEXEC", "reason": "unknown_op", "first_refused": None, "mid_instruction_stop": "BLOCKED_NOT_SAFE_TO_RESUME"}
        build = build or {}
        if build.get("gg_testing") or build.get("GG_TESTING") or build.get("libretro") or build.get("LIBRETRO"):
            return {**base, "op": op, "verdict": "REFUSED_PREEXEC", "reason": "testing_build", "first_refused": None, "mid_instruction_stop": "BLOCKED_NOT_SAFE_TO_RESUME"}
        if not isinstance(mpr, (list, tuple)) or len(mpr) != 8 or any(not isinstance(x, int) or x < 0 or x > 0xFF for x in mpr):
            return {**base, "op": op, "verdict": "REFUSED_PREEXEC", "reason": "malformed_mpr", "first_refused": None, "mid_instruction_stop": "BLOCKED_NOT_SAFE_TO_RESUME"}
        if any(x == 0xFF for x in mpr[:7]):
            return {**base, "op": op, "verdict": "REFUSED_PREEXEC", "reason": "ff_mpr", "first_refused": None, "mid_instruction_stop": "BLOCKED_NOT_SAFE_TO_RESUME"}
        if not allowed_banks:
            return {**base, "op": op, "verdict": "REFUSED_PREEXEC", "reason": "bad_window_allowlist", "first_refused": None, "mid_instruction_stop": "BLOCKED_NOT_SAFE_TO_RESUME"}
        if same_value:
            return {**base, "op": op, "verdict": "REFUSED_PREEXEC", "reason": "same_value_write", "first_refused": None, "mid_instruction_stop": "BLOCKED_NOT_SAFE_TO_RESUME"}
        if temp_restore:
            return {**base, "op": op, "verdict": "REFUSED_PREEXEC", "reason": "temp_write_restore", "first_refused": None, "mid_instruction_stop": "BLOCKED_NOT_SAFE_TO_RESUME"}
        if not isinstance(length, int) or length < 0 or length > 0x10000:
            return {**base, "op": op, "verdict": "REFUSED_PREEXEC", "reason": "bad_length", "first_refused": None, "mid_instruction_stop": "BLOCKED_NOT_SAFE_TO_RESUME"}
        if not isinstance(window, (list, tuple)) or len(window) != 2:
            return {**base, "op": op, "verdict": "REFUSED_PREEXEC", "reason": "bad_window_allowlist", "first_refused": None, "mid_instruction_stop": "BLOCKED_NOT_SAFE_TO_RESUME"}
        if not all(isinstance(x, int) for x in window) or not (0 <= window[0] < window[1] <= 0x10000):
            return {**base, "op": op, "verdict": "REFUSED_PREEXEC", "reason": "bad_window_allowlist", "first_refused": None, "mid_instruction_stop": "BLOCKED_NOT_SAFE_TO_RESUME"}
        count = 0x10000 if length == 0 else length
        if op == "TIN":
            dset = {dst & 0xFFFF}
        elif op == "TDD":
            dset = {(dst - i) & 0xFFFF for i in range(count)}
        elif op == "TIA":
            dset = {(dst + i) & 0xFFFF for i in range(count + 1)}
        else:
            dset = {(dst + i) & 0xFFFF for i in range(count)}
        lo, hi = window
        outside = sorted(a for a in dset if not lo <= a < hi)
        if outside:
            return {**base, "op": op, "verdict": "REFUSED_PREEXEC", "reason": "dst_outside_window", "first_refused": outside[0], "mid_instruction_stop": "BLOCKED_NOT_SAFE_TO_RESUME"}
        bad = sorted(a for a in dset if mpr[(a >> 13) & 7] not in allowed_banks or mpr[(a >> 13) & 7] == 0xFF)
        if bad:
            return {**base, "op": op, "verdict": "REFUSED_PREEXEC", "reason": "dst_bank_not_allowed", "first_refused": bad[0], "mid_instruction_stop": "BLOCKED_NOT_SAFE_TO_RESUME"}
        return {**base, "op": op, "verdict": "PERMITTED_STATIC_ONLY", "reason": "in_window_static_only", "first_refused": None, "mid_instruction_stop": "BLOCKED_NOT_SAFE_TO_RESUME", "distinct_destinations": len(dset)}

    def device_audit(rows):
        bad = []
        for r in rows:
            if r.get("verdict") == "UNKNOWN":
                bad.append(r.get("id"))
            if r.get("target") == "WRAM" and r.get("role") in ("device_store", "unknown_store"):
                if r.get("id") not in bad:
                    bad.append(r.get("id"))
        if bad:
            return {"verdict": "BLOCKED_AUDIT", "gate": U.BLOCKED, "execution": U.BLOCKED, "jsr_count": 0, "bad_ids": bad}
        return {"verdict": "DRAINED_NO_UNKNOWN", "gate": U.BLOCKED, "execution": U.BLOCKED, "jsr_count": 0, "bad_ids": []}

    return SimpleNamespace(PINNED_REVISION=PINNED, preflight=preflight, device_audit=device_audit)


class UnifiedTest(unittest.TestCase):
    def _inject(self):
        a, b = _stub_a(), _stub_b()
        old = {}
        for k, v in (("h45_preblock_gate", a), ("block_device_predeny", b),
                     ("tools.h45_preblock_gate", a), ("tools.block_device_predeny", b)):
            old[k] = sys.modules.get(k)
            sys.modules[k] = v
        self.addCleanup(lambda: [sys.modules.__setitem__(k, v) if v is not None else sys.modules.pop(k, None) for k, v in old.items()])
        return a, b

    def test_premerge_never_pass(self):
        v, r, rec = U.judge_unified(U.clean_case())
        self.assertNotEqual(v, U.PASS)
        self.assertEqual(v, U.BLOCKED)
        self.assertEqual(rec["execution"], "BLOCKED")
        self.assertIs(rec["execution_permitted"], False)
        self.assertEqual(rec["jsr_count"], 0)
        self.assertIs(rec["release_eligible"], False)
        with self.assertRaises(U.GateRefused):
            U.require_unified(U.clean_case())

    def test_prereq_false_blocks_even_with_modules(self):
        self._inject()
        c = U.clean_case()
        c["prereq"] = {k: False for k in U.PREREQ_KEYS}
        v, _, _ = U.judge_unified(c)
        self.assertEqual(v, U.BLOCKED)
        self.assertNotEqual(v, U.PASS)

    def test_clean_with_modules_and_prereq_passes_synthetic(self):
        self._inject()
        v, r, rec = U.judge_unified(U.clean_case())
        self.assertEqual(v, U.PASS, r)
        self.assertEqual(rec["execution"], "BLOCKED")
        self.assertIs(rec["execution_permitted"], False)
        self.assertEqual(rec["jsr_count"], 0)
        rec2 = U.require_unified(U.clean_case())
        self.assertEqual(rec2["verdict"], U.PASS)

    def test_deny_forgery_same_value_rejected(self):
        self._inject()
        c = U.clean_case()
        c["block_request"]["same_value"] = True
        v, _, _ = U.judge_unified(c)
        self.assertNotEqual(v, U.PASS)

    def test_deny_forgery_temp_restore_rejected(self):
        self._inject()
        c = U.clean_case()
        c["block_request"]["temp_restore"] = True
        self.assertNotEqual(U.judge_unified(c)[0], U.PASS)

    def test_single_module_pass_insufficient(self):
        a, b = self._inject()
        c = U.clean_case()
        c["block_request"]["dst"] = 0x3FF8
        c["block_request"]["length"] = 16
        av, _ = a.judge_allowlist(c["snapshot"])
        self.assertEqual(av, U.PASS)
        self.assertNotEqual(U.judge_unified(c)[0], U.PASS)

    def test_mpr_inconsistent_blocked(self):
        self._inject()
        c = U.clean_case()
        c["block_request"]["mpr"] = [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]
        self.assertNotEqual(U.judge_unified(c)[0], U.PASS)

    def test_device_unknown_blocked(self):
        self._inject()
        c = U.clean_case()
        c["device_rows"] = [{"id": "UNK-01", "role": "unknown_store", "target": "ANY", "verdict": "UNKNOWN"}]
        self.assertNotEqual(U.judge_unified(c)[0], U.PASS)

    def test_unimplemented_none_blocked(self):
        self._inject()
        for key in ("block_request", "device_rows", "irq"):
            c = U.clean_case()
            c[key] = None
            with self.subTest(missing=key):
                self.assertNotEqual(U.judge_unified(c)[0], U.PASS)

    def test_pinned_mismatch_blocked(self):
        a, b = self._inject()
        b.PINNED_REVISION = "deadbeef"
        self.assertNotEqual(U.judge_unified(U.clean_case())[0], U.PASS)

    def test_build_libretro_blocked(self):
        self._inject()
        c = U.clean_case()
        c["snapshot"]["build"] = "libretro"
        self.assertNotEqual(U.judge_unified(c)[0], U.PASS)

    def test_snapshot_gate_violation_not_pass(self):
        self._inject()
        for key, val in (("jsr_count", 1), ("execution", "ALLOWED"), ("release_eligible", True)):
            c = U.clean_case()
            c["snapshot"][key] = val
            with self.subTest(k=key):
                self.assertNotEqual(U.judge_unified(c)[0], U.PASS)

    def test_forged_run_id_halt_real_core_blocked(self):
        self._inject()
        c = U.clean_case()
        c["snapshot"]["provenance"] = {"kind": "REAL_CORE_EXECUTION",
                                       "core_revision": PINNED, "bios": "SYNTHETIC",
                                       "real_core_claim": True, "run_id": "forged-run"}
        c["snapshot"]["run_id"] = "forged-run"
        c["snapshot"]["halt_registers"] = {"PC": 0x3E00, "A": 0, "X": 0, "Y": 0, "P": 0, "S": 0x21FF}
        v, r, _ = U.judge_unified(c)
        self.assertEqual(v, U.BLOCKED, r)
        self.assertNotEqual(v, U.PASS)

    def test_host_with_run_id_blocked(self):
        self._inject()
        c = U.clean_case()
        c["snapshot"]["run_id"] = "host-carries-harness-id"
        self.assertEqual(U.judge_unified(c)[0], U.BLOCKED)

    def test_host_to_real_core_mutation_blocked(self):
        self._inject()
        c = U.clean_case()
        prov = dict(c["snapshot"]["provenance"])
        prov["kind"] = "REAL_CORE_EXECUTION"
        prov["core_revision"] = PINNED
        prov["real_core_claim"] = True
        prov["run_id"] = "r1"
        c["snapshot"]["provenance"] = prov
        c["snapshot"]["run_id"] = "r1"
        c["snapshot"]["halt_registers"] = {"PC": 0x3E00, "A": 0, "X": 0, "Y": 0, "P": 0, "S": 0x21FF}
        self.assertEqual(U.judge_unified(c)[0], U.BLOCKED)

    def test_widened_approved_range_blocked(self):
        self._inject()
        c = U.clean_case()
        c["snapshot"]["approved_range"] = [0, 0xFFFF]
        c["snapshot"]["pc"] = 0x2000
        self.assertEqual(U.judge_unified(c)[0], U.BLOCKED)

    def test_second_st_in_sequence_fails(self):
        self._inject()
        c = U.clean_case()
        c["snapshot"]["executed"] = 2
        c["snapshot"]["instruction_sequence"] = [0xEA, 0x03]
        v, _, _ = U.judge_unified(c)
        self.assertEqual(v, U.FAIL)

    def test_second_jsr_in_sequence_fails(self):
        self._inject()
        c = U.clean_case()
        c["snapshot"]["executed"] = 2
        c["snapshot"]["instruction_sequence"] = [0xEA, 0x20]
        self.assertEqual(U.judge_unified(c)[0], U.FAIL)

    def test_sequence_count_mismatch_blocked(self):
        self._inject()
        c = U.clean_case()
        c["snapshot"]["executed"] = 1
        c["snapshot"]["instruction_sequence"] = []
        self.assertEqual(U.judge_unified(c)[0], U.BLOCKED)

    def test_e4_rejection_never_ignored(self):
        self._inject()
        c = U.clean_case()
        c["block_request"]["dst"] = 0x1000
        c["block_request"]["length"] = 8
        v, r, _ = U.judge_unified(c)
        self.assertNotEqual(v, U.PASS, r)
        self.assertEqual(v, U.BLOCKED)

    def test_irq_pending_unmasked_fails(self):
        self._inject()
        c = U.clean_case()
        c["snapshot"]["irq_pending"] = True
        c["snapshot"]["p_i"] = False
        c["irq"]["row"]["pending"] = True
        c["irq"]["row"]["p_i"] = False
        self.assertEqual(U.judge_unified(c)[0], U.FAIL)

    def test_irq_snapshot_row_mismatch_blocked(self):
        self._inject()
        c = U.clean_case()
        c["snapshot"]["irq_pending"] = True
        c["snapshot"]["p_i"] = True
        c["irq"]["row"]["pending"] = False
        c["irq"]["row"]["p_i"] = True
        v, r, _ = U.judge_unified(c)
        self.assertEqual(v, U.BLOCKED, r)
        self.assertIn("IRQ_EVIDENCE_MISMATCH", r)
        c = U.clean_case()
        c["snapshot"]["irq_pending"] = False
        c["snapshot"]["p_i"] = True
        c["irq"]["row"]["pending"] = False
        c["irq"]["row"]["p_i"] = False
        v, r, _ = U.judge_unified(c)
        self.assertEqual(v, U.BLOCKED, r)
        self.assertIn("IRQ_EVIDENCE_MISMATCH", r)

    def test_irq_row_missing_or_unobserved_blocked(self):
        self._inject()
        c = U.clean_case()
        c["irq"]["row"] = None
        v, r, _ = U.judge_unified(c)
        self.assertEqual(v, U.BLOCKED, r)
        self.assertIn("IRQ_ROW_MISSING", r)
        for key, val in (("pending", None), ("pending", 0), ("p_i", None), ("p_i", 1)):
            c = U.clean_case()
            c["irq"]["row"][key] = val
            with self.subTest(k=key, v=val):
                v, r, _ = U.judge_unified(c)
                self.assertEqual(v, U.BLOCKED, r)
                self.assertIn("IRQ_ROW_PENDING_PI_MISSING", r)

    def test_block_deny_observation_missing_blocked(self):
        self._inject()
        for key in ("same_value", "temp_restore"):
            c = U.clean_case()
            del c["block_request"][key]
            with self.subTest(missing=key):
                v, r, _ = U.judge_unified(c)
                self.assertEqual(v, U.BLOCKED, r)
                self.assertIn("BLOCK_DENY_OBSERVATION_MISSING", r)
        for key, val in (("same_value", None), ("same_value", 0), ("same_value", "false"),
                         ("temp_restore", None), ("temp_restore", 0), ("temp_restore", "false")):
            c = U.clean_case()
            c["block_request"][key] = val
            with self.subTest(k=key, v=val):
                v, r, _ = U.judge_unified(c)
                self.assertEqual(v, U.BLOCKED, r)
                self.assertIn("BLOCK_DENY_OBSERVATION_MISSING", r)

    def test_block_deny_explicit_false_passes_true_rejected(self):
        self._inject()
        v, r, _ = U.judge_unified(U.clean_case())
        self.assertEqual(v, U.PASS, r)
        for key in ("same_value", "temp_restore"):
            c = U.clean_case()
            c["block_request"][key] = True
            with self.subTest(k=key):
                self.assertNotEqual(U.judge_unified(c)[0], U.PASS)

    def test_prereq_true_without_evidence_still_blocked(self):
        self._inject()
        c = U.clean_case()
        c["block_request"] = None
        v, r, _ = U.judge_unified(c)
        self.assertEqual(v, U.BLOCKED, r)

    def test_prereq_truthy_non_true_blocked(self):
        self._inject()
        for forged in (1, "yes", 1.0, [True], None):
            c = U.clean_case()
            c["prereq"] = {k: forged for k in U.PREREQ_KEYS}
            with self.subTest(forged=forged):
                self.assertEqual(U.judge_unified(c)[0], U.BLOCKED)

    def test_prereq_not_dict_blocked(self):
        self._inject()
        for forged in (True, [True], "merged", None, 0):
            c = U.clean_case()
            c["prereq"] = forged
            with self.subTest(forged=forged):
                self.assertEqual(U.judge_unified(c)[0], U.BLOCKED)

    def test_forged_prereq_real_core_blocked(self):
        self._inject()
        c = U.clean_case()
        self.assertTrue(all(v is True for v in c["prereq"].values()))
        c["snapshot"]["provenance"] = {"kind": "REAL_CORE_EXECUTION",
                                       "core_revision": U.PINNED, "bios": "SYNTHETIC",
                                       "real_core_claim": True, "run_id": "forged-run"}
        c["snapshot"]["run_id"] = "forged-run"
        c["snapshot"]["halt_registers"] = {"PC": 0x3E00, "A": 0, "X": 0,
                                          "Y": 0, "P": 0, "S": 0x21FF}
        v, r, _ = U.judge_unified(c)
        self.assertEqual(v, U.BLOCKED, r)
        self.assertIn("REAL_CORE_REQUIRES_HARNESS", r)

    def test_pass_record_marks_prereq_unauthenticated(self):
        self._inject()
        v, r, rec = U.judge_unified(U.clean_case())
        self.assertEqual(v, U.PASS, r)
        self.assertIn("PREREQ_UNAUTHENTICATED_ASSERTION_NOT_PROOF", r)
        self.assertIn("PREREQ_UNAUTHENTICATED_ASSERTION_NOT_PROOF", rec["reasons"])
        self.assertIs(rec["execution_permitted"], False)
        self.assertEqual(rec["jsr_count"], 0)
        self.assertIs(rec["release_eligible"], False)

    def test_mutations_never_pass(self):
        self._inject()
        base = U.clean_case()
        muts = []
        c = copy.deepcopy(base); c["snapshot"]["first_opcode"] = 0x03; muts.append(c)
        c = copy.deepcopy(base); c["snapshot"]["mmio_stores"] = [0x1802]; muts.append(c)
        c = copy.deepcopy(base); c["snapshot"]["mpr"] = [0xFF, 0xF8, 0, 0, 0, 0, 0, 0]; c["block_request"]["mpr"] = list(c["snapshot"]["mpr"]); muts.append(c)
        c = copy.deepcopy(base); c["snapshot"]["irq_status"] = "UNKNOWN"; muts.append(c)
        c = copy.deepcopy(base); c["snapshot"]["expected_sha256"] = "b" * 64; muts.append(c)
        c = copy.deepcopy(base); c["snapshot"]["pc"] = 0x5000; muts.append(c)
        c = copy.deepcopy(base); c["block_request"]["dst"] = 0x3FFC; c["block_request"]["length"] = 8; muts.append(c)
        c = copy.deepcopy(base); c["block_request"]["op"] = "TXX"; muts.append(c)
        c = copy.deepcopy(base); c["block_request"]["same_value"] = True; muts.append(c)
        c = copy.deepcopy(base); c["device_rows"] = copy.deepcopy(base["device_rows"]) + [{"id": "UNK-99", "role": "device_store", "target": "WRAM", "verdict": "NOT_MAIN_RAM"}]; muts.append(c)
        c = copy.deepcopy(base); c["snapshot"]["deny_flags"]["same_value_write"] = True; muts.append(c)
        c = copy.deepcopy(base); c["snapshot"]["peek_ok"] = False; muts.append(c)
        c = copy.deepcopy(base); c["irq"]["build"] = "libretro"; muts.append(c)
        self.assertGreaterEqual(len(muts), 13)
        for i, c in enumerate(muts):
            with self.subTest(i=i):
                self.assertNotEqual(U.judge_unified(c)[0], U.PASS)


if __name__ == "__main__":
    unittest.main()
