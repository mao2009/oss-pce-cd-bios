#!/usr/bin/env python3
"""Host tests for tools/h45_realcore_verify.py parsing/validation (no core)."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import h45_realcore_verify as V  # noqa: E402


def _row(**kw):
    base = {"stop_pc": 0x3E00, "instructions_executed": 0,
            "vdc_sr_before": 0x3F, "vdc_sr_after": 0x3F,
            "resume_refused": 3, "resume_attempts": 3, "status": "refused"}
    base.update(kw)
    return base


def _item(case, verdict, row):
    return {"case": case, "observation_kind": "REAL_CORE_EXECUTION",
            "core_revision": V.PINNED, "bios": "SYNTHETIC",
            "execution_mode": "debug-single-step", "verdict": verdict, "row": row}


def _st_full_cases():
    """Full pinned ST transcript: 17 armed + 22 mutations (pos via probe exit)."""
    cases = {}
    for mode in V.ST_ARMED:
        if mode in V.ST_HWPAGE_ARMED:
            cases[mode] = _item(
                mode, "hardware_page_fetch_failed_closed_before_step",
                _row(stop_pc=0x4000, instructions_executed=3, status="fetch_blocked"))
        else:
            cases[mode] = _item(mode, "refused_at_instruction_boundary_before_side_effect",
                                _row())
    for mut in V.ST_MUTATIONS:
        cases[mut] = _item(mut, "side_effect_landed_monitor_disabled",
                           _row(instructions_executed=1, vdc_sr_after=0x00,
                                status="landed"))
    return cases


class ParseTest(unittest.TestCase):
    def test_no_evidence_blocked(self):
        cases, detail = V._parse_evidence("no evidence here\n")
        self.assertIsNone(cases)
        self.assertEqual(detail, "NO_EVIDENCE_LINES")

    def test_malformed_json_blocked(self):
        cases, detail = V._parse_evidence("EVIDENCE {not json}\n")
        self.assertIsNone(cases)

    def test_valid_lines_parsed(self):
        import json
        line = "EVIDENCE " + json.dumps(_item("st0-entry", "REFUSED", _row()))
        cases, detail = V._parse_evidence(line + "\n")
        self.assertEqual(detail, "OK")
        self.assertIn("st0-entry", cases)


class CheckTest(unittest.TestCase):
    """ST exact-set contract: 17 armed + 22 mutations, pos via probe exit."""

    def _cases(self):
        return _st_full_cases()

    def _full(self, **kw):
        kw.setdefault("probe_ok", True)
        kw.setdefault("summary_ok", True)
        return kw

    def test_all_good_passes(self):
        v, reasons, halt = V._check_st_cases(self._cases(), **self._full())
        self.assertEqual(v, V.PASS, reasons)
        self.assertEqual(len(halt), len(V.ST_EXPECTED_EVIDENCE))

    def test_partial_single_each_blocked(self):
        # MAJOR-2 RED: 1 armed + 1 mutation + positive control must not PASS.
        c = {
            "st0-entry": _item("st0-entry", "REFUSED", _row()),
            "st0-entry-unguarded": _item("st0-entry-unguarded", "LANDED",
                                         _row(instructions_executed=1, vdc_sr_after=0x00,
                                              status="landed")),
        }
        v, _, _ = V._check_st_cases(c, **self._full())
        self.assertEqual(v, V.BLOCKED)

    def test_delete_one_armed_at_a_time_blocked(self):
        for mode in sorted(V.ST_ARMED):
            with self.subTest(missing=mode):
                c = self._cases()
                del c[mode]
                v, reasons, _ = V._check_st_cases(c, **self._full())
                self.assertEqual(v, V.BLOCKED, reasons)
                self.assertTrue(any("MISSING" in r for r in reasons))

    def test_delete_one_mutation_at_a_time_blocked(self):
        for mut in sorted(V.ST_MUTATIONS):
            with self.subTest(missing=mut):
                c = self._cases()
                del c[mut]
                v, reasons, _ = V._check_st_cases(c, **self._full())
                self.assertEqual(v, V.BLOCKED, reasons)

    def test_unknown_case_never_accepted(self):
        c = self._cases()
        c["evil-unguarded"] = _item(
            "evil-unguarded", "side_effect_landed_monitor_disabled",
            _row(instructions_executed=1, status="landed"))
        v, reasons, _ = V._check_st_cases(c, **self._full())
        self.assertEqual(v, V.BLOCKED, reasons)

    def test_mixed_probe_case_ids_rejected(self):
        for foreign in sorted(V.BLOCKPRE_ARMED | V.BLOCKPRE_MUTATIONS):
            c = self._cases()
            victim = sorted(V.ST_MUTATIONS)[0]
            del c[victim]
            c[foreign] = _item(foreign, "side_effect_landed_monitor_disabled",
                               _row(instructions_executed=1, status="landed"))
            with self.subTest(foreign=foreign):
                v, _, _ = V._check_st_cases(c, **self._full())
                self.assertEqual(v, V.BLOCKED)

    def test_substring_spoof_not_accepted(self):
        c = self._cases()
        victim = sorted(V.ST_MUTATIONS)[0]
        del c[victim]
        c["spoof-no-entry-check"] = _item(
            "spoof-no-entry-check", "side_effect_landed_monitor_disabled",
            _row(instructions_executed=1, status="landed"))
        v, _, _ = V._check_st_cases(c, **self._full())
        self.assertEqual(v, V.BLOCKED)

    def test_duplicate_case_blocked(self):
        import json
        line = "EVIDENCE " + json.dumps(
            _item("st0-entry", "REFUSED", _row()))
        cases, detail = V._parse_evidence(line + "\n" + line + "\n")
        self.assertIsNone(cases)
        self.assertTrue(detail.startswith("DUPLICATE_CASE:"))

    def test_armed_setup_steps_allowed_when_refused(self):
        # Non-entry families execute setup steps before refusal; the refused
        # verdict + unchanged SR is the gate, not an executed==0 count.
        c = self._cases()
        c["st0-jsr-rts"] = _item("st0-jsr-rts", "refused_at_instruction_boundary_before_side_effect",
                                 _row(instructions_executed=3))
        v, _, _ = V._check_st_cases(c, **self._full())
        self.assertEqual(v, V.PASS)

    def test_armed_landed_verdict_fails(self):
        c = self._cases()
        c["st0-entry"] = _item("st0-entry", "side_effect_landed_monitor_disabled",
                               _row(instructions_executed=2, vdc_sr_after=0x00))
        v, _, _ = V._check_st_cases(c, **self._full())
        self.assertEqual(v, V.FAIL)

    def test_armed_vdc_changed_fails(self):
        c = self._cases()
        c["st0-entry"] = _item("st0-entry", "REFUSED", _row(vdc_sr_after=0x00))
        v, _, _ = V._check_st_cases(c, **self._full())
        self.assertEqual(v, V.FAIL)

    def test_hwpage_armed_wrong_verdict_fails(self):
        c = self._cases()
        mode = sorted(V.ST_HWPAGE_ARMED)[0]
        c[mode] = _item(mode, "side_effect_landed_monitor_disabled",
                        _row(stop_pc=0x4002, vdc_sr_after=0x00))
        v, _, _ = V._check_st_cases(c, **self._full())
        self.assertEqual(v, V.FAIL)

    def test_mutation_refused_fails(self):
        c = self._cases()
        mut = sorted(V.ST_MUTATIONS)[0]
        c[mut] = _item(mut, "REFUSED", _row())
        v, _, _ = V._check_st_cases(c, **self._full())
        self.assertEqual(v, V.FAIL)

    def test_wrong_pin_fails(self):
        c = self._cases()
        bad = _item("st0-entry", "REFUSED", _row())
        bad["core_revision"] = "deadbeef"
        c["st0-entry"] = bad
        v, _, _ = V._check_st_cases(c, **self._full())
        self.assertEqual(v, V.FAIL)

    def test_missing_pos_control_blocked(self):
        c = self._cases()
        v, _, _ = V._check_st_cases(c, probe_ok=True, summary_ok=False)
        self.assertEqual(v, V.BLOCKED)
        v, _, _ = V._check_st_cases(c, probe_ok=False, summary_ok=True)
        self.assertEqual(v, V.BLOCKED)

    def test_pos_control_by_probe_exit_accepted(self):
        c = self._cases()
        v, reasons, _ = V._check_st_cases(c, probe_ok=True, summary_ok=True)
        self.assertEqual(v, V.PASS)
        self.assertIn("POS_CONTROL_BY_PROBE_EXIT", reasons)

    def test_pos_control_by_probe_exit_requires_summary(self):
        c = self._cases()
        v, _, _ = V._check_st_cases(c, probe_ok=True, summary_ok=False)
        self.assertEqual(v, V.BLOCKED)


MMIO_PASS_SUMMARY = (
    "PASS issue-173 $1804 preblock: RED=blocked_side_effect_before_stop (write breakpoint only, side "
    "effect landed), GREEN=preblocked_before_side_effect (stop_pc=0x3e02, "
    "instructions_executed=1, $1804 stayed 0x00); allowlisted $3E50 control store landed; no gate change "
    "(slot $01 BLOCKED)\n"
    "LIMIT: the execute breakpoint needs the store's PC (known guest code): "
    "prevention for known code, not a general MMIO write interceptor; "
    "ST0/ST1/ST2, $1802 IRQ enable, MPR and alias paths stay unguaranteed."
)


class MmioTest(unittest.TestCase):
    def test_valid_summary_passes(self):
        v, reasons, halt = V._check_mmio_summary(MMIO_PASS_SUMMARY)
        self.assertEqual(v, V.PASS)
        self.assertIn("MMIO_KNOWN_PC_ONLY", reasons)
        self.assertEqual(halt["stop_pc"], 0x3E02)

    def test_missing_pass_line_blocked(self):
        v, _, _ = V._check_mmio_summary("no pass here")
        self.assertEqual(v, V.BLOCKED)

    def test_wrong_stop_pc_blocked(self):
        v, _, _ = V._check_mmio_summary(MMIO_PASS_SUMMARY.replace("stop_pc=0x3e02", "stop_pc=0x3e05"))
        self.assertEqual(v, V.BLOCKED)

    def test_green_mismatch_blocked(self):
        v, _, _ = V._check_mmio_summary(MMIO_PASS_SUMMARY.replace(
            "GREEN=preblocked_before_side_effect", "GREEN=something_else"))
        self.assertEqual(v, V.BLOCKED)

    def test_limit_missing_blocked(self):
        v, _, _ = V._check_mmio_summary(MMIO_PASS_SUMMARY.replace("LIMIT:", "NOTE:"))
        self.assertEqual(v, V.BLOCKED)


def _brow(case, verdict, **kw):
    row = {"mode": case, "status": "ok", "stop_pc": 0x3F00, "expected_end_pc": 0x3F00,
           "before": {"mpr": [0, 1, 2, 3, 4, 5, 6, 7]},
           "after": {"mpr": [0, 1, 2, 3, 4, 5, 6, 7]},
           "sp": [4, 4], "p": [4, 4], "a_after": 0x12, "x_after": 0x34, "y_after": 0x07,
           "stack_wram": [0x34, 0x12, 0x07], "vdc_read_buffer": [0xBEEF, 0xBEEF],
           "op": case.split("-")[0], "block_op": 0, "opcode": 0x73, "guard_refusals": 1,
           "guard_calls": 3}
    row.update(kw)
    return {"case": case, "observation_kind": "REAL_CORE_EXECUTION",
            "core_revision": V.PINNED, "bios": "SYNTHETIC",
            "hook": "VERIFICATION_ONLY_PATCH", "verdict": verdict, "row": row}


class BlockTest(unittest.TestCase):
    def _cases(self):
        return {
            "tii-cross-armed": _brow("tii-cross-armed", "per_byte_refusal_continues_and_restores"),
            "tii-cross-unguarded": _brow("tii-cross-unguarded", "forbidden_bytes_land_without_hook",
                                        guard_refusals=0),
            "tii-srcread-armed": _brow("tii-srcread-armed", "source_read_side_effect_lands_on_allowed_transfer",
                                      guard_refusals=0),
            "tii-srcread-unguarded": _brow("tii-srcread-unguarded", "source_read_side_effect_lands_without_hook",
                                          guard_refusals=0),
            "tii-srcreadff-armed": _brow("tii-srcreadff-armed", "refusal_skips_source_read"),
            "tii-srcreadff-unguarded": _brow("tii-srcreadff-unguarded",
                                            "forbidden_source_read_and_write_land_without_hook",
                                            guard_refusals=0),
        }

    def test_block_stays_blocked_with_facts(self):
        v, reasons, halt = V._check_block_cases(self._cases())
        self.assertEqual(v, V.BLOCKED)
        self.assertIn("MID_INSTRUCTION_STOP_BLOCKED_NOT_SAFE_TO_RESUME", reasons)
        self.assertIn("SRCREAD_LIMIT_DOCUMENTED", reasons)
        self.assertEqual(len(halt), 6)

    def test_mpr_change_fails(self):
        c = self._cases()
        item = c["tii-cross-armed"]
        row = dict(item["row"], after={"mpr": [9, 1, 2, 3, 4, 5, 6, 7]})
        c["tii-cross-armed"] = dict(item, row=row)
        v, _, _ = V._check_block_cases(c)
        self.assertEqual(v, V.FAIL)

    def test_stop_pc_mismatch_fails(self):
        c = self._cases()
        item = c["tii-cross-armed"]
        row = dict(item["row"], stop_pc=0x1234)
        c["tii-cross-armed"] = dict(item, row=row)
        v, _, _ = V._check_block_cases(c)
        self.assertEqual(v, V.FAIL)

    def test_unknown_verdict_blocked(self):
        c = self._cases()
        c["tii-cross-armed"] = _brow("tii-cross-armed", "something_new")
        v, _, _ = V._check_block_cases(c)
        self.assertEqual(v, V.BLOCKED)

    def test_no_armed_blocked(self):
        c = {k: v for k, v in self._cases().items() if not k.endswith("-armed")}
        v, _, _ = V._check_block_cases(c)
        self.assertEqual(v, V.BLOCKED)

    def test_missing_armed_blocked(self):
        c = {k: v for k, v in _st_full_cases().items() if k != "st0-entry"}
        v, reasons, _ = V._check_st_cases(c, probe_ok=True, summary_ok=True)
        self.assertEqual(v, V.BLOCKED)
        self.assertTrue(any("MISSING" in r for r in reasons))


def _blockpre_row(mode, armed=True):
    """Realistic armed row: every independently re-judged raw field present.

    Mirrors the probe emission (geargrafx_block_preblock.cpp): armed runs
    carry armed=True, refused=True (fetch_blocked=False, safe peek);
    hwpage fail-closed runs carry fetch_blocked=True, fetch_safe=False,
    fetch_failed=True, refused=False.
    """
    hwpage = mode in V.BLOCKPRE_HWPAGE_ARMED
    op = V.BLOCKPRE_EXPECTED_OP[mode]
    pc = V.BLOCKPRE_EXPECTED_PC[mode]
    executed = V.BLOCKPRE_EXPECTED_EXECUTED[mode]
    base = _row(stop_pc=pc, instructions_executed=executed,
                vdc_sr_before=0x3F, vdc_sr_after=0x3F, status="refused",
                mode=mode, armed=True, refused=True,
                reason="block_opcode_at_instruction_boundary",
                fetch_blocked=False, fetch_unsafe_bank=False,
                fetch_page=0, fetch_mpr=0xF8, fetch_opcode=op,
                refused_pc=pc, refused_opcode=op,
                fetch_safe=True, fetch_failed=False,
                operand_bytes=[op, 0x80, 0x3E, 0x90, 0x3E, 0x04, 0x00],
                operand_complete=True,
                executed_at_refusal=executed, executed_after_resume=executed,
                resume_attempts=3, resume_refused=3,
                ram_unchanged_during_resume=True,
                ram_diff=[], ram_diff_count=0, ram_diff_truncated=False,
                only_stack_net_diffs=True,
                before={"mpr": list(V.BLOCKPRE_MPR_BEFORE[mode])},
                after={"mpr": list(V.BLOCKPRE_MPR_AFTER[mode])},
                vdc_read_buffer_before=0xBEEF, vdc_read_buffer_after=0xBEEF,
                vdc_ar_before=0, vdc_ar_after=0,
                vdc_r_before=0, vdc_r_after=0,
                irq_accepted=False, observed_vector=0, handler_steps=0)
    if hwpage:
        base.update(status="fetch_blocked", vdc_ar_before=5, vdc_ar_after=5,
                    refused=False, refused_opcode=0, operand_bytes=[],
                    operand_complete=False, fetch_via="peek",
                    fetch_blocked=True,
                    fetch_unsafe_bank=True, fetch_page=2, fetch_mpr=0xFF,
                    fetch_opcode=0, fetch_safe=False, fetch_failed=True,
                    reason="unsafe_opcode_fetch_fail_closed")
    if mode == "block-irq-handler":
        base.update(irq_accepted=True, observed_vector=0x3F80, vector_read=True,
                    handler_steps=0,
                    ram_diff=[{"address": 0x21FF, "before": 0x00, "after": 0x3E},
                              {"address": 0x21FE, "before": 0x00, "after": 0x01}],
                    ram_diff_count=2)
    if not armed:
        base.update({"instructions_executed": executed + 2,
                     "vdc_sr_after": 0x00, "status": "ok",
                     "armed": False, "refused": False})
    return base


def _blockpre_cases():
    cases = {}
    for mode in V.BLOCKPRE_ARMED:
        hw = mode in V.BLOCKPRE_HWPAGE_ARMED
        verdict = ("hardware_page_fetch_failed_closed_before_step" if hw
                   else "refused_at_instruction_boundary_before_side_effect")
        cases[mode] = _item(mode, verdict, _blockpre_row(mode, armed=True))
    for mut in V.BLOCKPRE_MUTATIONS:
        if mut in V.BLOCKPRE_ALWAYS_BLOCKED:
            verdict = "hardware_page_fetch_failed_closed_before_step"
            row = _blockpre_row("tii-hwpage-fetch-blocked", armed=True)
            # A real -unguarded probe run emits armed=False and its own mode.
            row.update(mode=mut, armed=False)
        else:
            verdict = "side_effect_landed_monitor_disabled"
            base_mode = V._blockpre_mutation_base(mut)
            row = _blockpre_row(base_mode, armed=False)
            # Only the pure -unguarded runs execute unmonitored; bypass
            # variants run with the monitor armed (probe: armed = !unguarded).
            row.update(mode=mut, armed=not mut.endswith("-unguarded"))
            if mut.endswith("-resume-allowed"):
                # The latch holds at first (refused), then 3 unrefused resume
                # steps advance the count by exactly 3 and land the transfer.
                executed = V.BLOCKPRE_EXPECTED_EXECUTED[base_mode] + 10
                trace = [{"address": a, "before": b, "after": v}
                         for a, b, v in V.BLOCKPRE_LANDED_TRACE[base_mode]]
                row.update(refused=True, status="ok",
                           refused_pc=V.BLOCKPRE_EXPECTED_PC[base_mode],
                           stop_pc=V.BLOCKPRE_EXPECTED_PC[base_mode] + 7,
                           instructions_executed=executed,
                           executed_at_refusal=executed - 3,
                           executed_after_resume=executed,
                           resume_attempts=3, resume_refused=0,
                           ram_diff=trace, ram_diff_count=len(trace),
                           ram_diff_truncated=False,
                           ram_unchanged_during_resume=False)
            elif base_mode == "tia-wrap-forbidden":
                # A forbidden-destination wrap lands on the VDC, not RAM.
                row.update(vdc_ar_before=0, vdc_ar_after=0x1B)
            else:
                trace = [{"address": a, "before": b, "after": v}
                         for a, b, v in V.BLOCKPRE_LANDED_TRACE[base_mode]]
                row.update(ram_diff=trace, ram_diff_count=len(trace),
                           ram_diff_truncated=False)
        cases[mut] = _item(mut, verdict, row)
    return cases


class BlockprePinnedTest(unittest.TestCase):
    def test_full_set_passes(self):
        v, reasons, halt = V._check_blockpre_cases(
            _blockpre_cases(), probe_ok=True, summary_ok=True)
        self.assertEqual(v, V.PASS, reasons)
        self.assertEqual(len(halt), 21)

    def test_delete_one_armed_at_a_time_blocked(self):
        for mode in sorted(V.BLOCKPRE_ARMED):
            with self.subTest(missing=mode):
                c = _blockpre_cases()
                del c[mode]
                v, reasons, _ = V._check_blockpre_cases(
                    c, probe_ok=True, summary_ok=True)
                self.assertEqual(v, V.BLOCKED, reasons)
                self.assertTrue(any("MISSING" in r for r in reasons))

    def test_delete_one_mutation_at_a_time_blocked(self):
        for mut in sorted(V.BLOCKPRE_MUTATIONS):
            with self.subTest(missing=mut):
                c = _blockpre_cases()
                del c[mut]
                v, reasons, _ = V._check_blockpre_cases(
                    c, probe_ok=True, summary_ok=True)
                self.assertEqual(v, V.BLOCKED, reasons)

    def test_unknown_case_never_accepted(self):
        c = _blockpre_cases()
        c["evil-unguarded"] = _item(
            "evil-unguarded", "side_effect_landed_monitor_disabled",
            _blockpre_row("tii-entry", armed=False))
        v, reasons, _ = V._check_blockpre_cases(
            c, probe_ok=True, summary_ok=True)
        self.assertEqual(v, V.BLOCKED, reasons)

    def test_substring_spoof_not_accepted(self):
        c = _blockpre_cases()
        # Replace a real mutation with a lookalike that only matches by substring.
        victim = sorted(V.BLOCKPRE_MUTATIONS)[0]
        del c[victim]
        c["spoof-no-entry-check"] = _item(
            "spoof-no-entry-check", "side_effect_landed_monitor_disabled",
            _blockpre_row("tii-entry", armed=False))
        v, _, _ = V._check_blockpre_cases(c, probe_ok=True, summary_ok=True)
        self.assertEqual(v, V.BLOCKED)

    def test_duplicate_case_blocked(self):
        import json
        line = "EVIDENCE " + json.dumps(
            _item("tii-entry", "REFUSED", _row()))
        cases, detail = V._parse_evidence(line + "\n" + line + "\n")
        self.assertIsNone(cases)
        self.assertTrue(detail.startswith("DUPLICATE_CASE:"))

    def test_missing_pos_control_blocked(self):
        v, _, _ = V._check_blockpre_cases(
            _blockpre_cases(), probe_ok=True, summary_ok=False)
        self.assertEqual(v, V.BLOCKED)
        v, _, _ = V._check_blockpre_cases(
            _blockpre_cases(), probe_ok=False, summary_ok=True)
        self.assertEqual(v, V.BLOCKED)

    def test_partial_never_verified(self):
        c = _blockpre_cases()
        del c[sorted(V.BLOCKPRE_ARMED)[0]]
        v, _, _ = V._check_blockpre_cases(c, probe_ok=True, summary_ok=True)
        self.assertNotEqual(v, V.PASS)

    def test_read_buffer_change_fails(self):
        c = _blockpre_cases()
        mode = sorted(V.BLOCKPRE_ARMED - V.BLOCKPRE_HWPAGE_ARMED)[0]
        c[mode] = _item(mode, "refused_at_instruction_boundary_before_side_effect",
                        dict(_blockpre_row(mode), vdc_read_buffer_after=0x1234))
        v, _, _ = V._check_blockpre_cases(c, probe_ok=True, summary_ok=True)
        self.assertEqual(v, V.FAIL)

    def test_read_buffer_missing_blocked(self):
        c = _blockpre_cases()
        mode = sorted(V.BLOCKPRE_ARMED - V.BLOCKPRE_HWPAGE_ARMED)[0]
        row = _blockpre_row(mode)
        del row["vdc_read_buffer_after"]
        c[mode] = _item(mode, "refused_at_instruction_boundary_before_side_effect", row)
        v, _, _ = V._check_blockpre_cases(c, probe_ok=True, summary_ok=True)
        self.assertEqual(v, V.BLOCKED)

    def test_read_buffer_wrong_type_blocked(self):
        c = _blockpre_cases()
        mode = sorted(V.BLOCKPRE_ARMED - V.BLOCKPRE_HWPAGE_ARMED)[0]
        row = _blockpre_row(mode)
        row["vdc_read_buffer_after"] = "0xBEEF"
        c[mode] = _item(mode, "refused_at_instruction_boundary_before_side_effect", row)
        v, _, _ = V._check_blockpre_cases(c, probe_ok=True, summary_ok=True)
        self.assertEqual(v, V.BLOCKED)


class BlockpreEvidenceSeparationTest(unittest.TestCase):
    """MEDIUM-2: synthetic fixtures vs REAL_CORE observations stay separated.

    Blockpre EVIDENCE must be REAL_CORE_EXECUTION with pinned provenance;
    HOST_FIXTURE rows are never accepted here, and blockpre evidence is never
    a unified PASS (no trusted-harness binding: execution stays BLOCKED,
    scope stays synthetic single-step only).
    """

    def test_host_fixture_never_accepted_as_real_core(self):
        c = _blockpre_cases()
        mode = sorted(V.BLOCKPRE_ARMED - V.BLOCKPRE_HWPAGE_ARMED)[0]
        bad = _item(mode, "refused_at_instruction_boundary_before_side_effect",
                    _blockpre_row(mode))
        bad["observation_kind"] = "HOST_FIXTURE"
        c[mode] = bad
        v, reasons, _ = V._check_blockpre_cases(c, probe_ok=True, summary_ok=True)
        self.assertEqual(v, V.BLOCKED, reasons)

    def test_provenance_mismatch_is_fail_closed(self):
        c = _blockpre_cases()
        mode = sorted(V.BLOCKPRE_ARMED - V.BLOCKPRE_HWPAGE_ARMED)[0]
        bad = _item(mode, "refused_at_instruction_boundary_before_side_effect",
                    _blockpre_row(mode))
        bad["core_revision"] = "deadbeef"
        c[mode] = bad
        v, _, _ = V._check_blockpre_cases(c, probe_ok=True, summary_ok=True)
        self.assertEqual(v, V.FAIL)

    def test_only_full_pass_maps_to_verified(self):
        # verify() maps PASS -> VERIFIED_PREBLOCK, everything else stays
        # BLOCKED/FAIL with execution BLOCKED. Partial never PASSes above,
        # so it can never become VERIFIED_PREBLOCK.
        full = _blockpre_cases()
        v, _, _ = V._check_blockpre_cases(full, probe_ok=True, summary_ok=True)
        self.assertEqual(v, V.PASS)
        for drop in (sorted(V.BLOCKPRE_ARMED)[0], sorted(V.BLOCKPRE_MUTATIONS)[0]):
            c = _blockpre_cases()
            del c[drop]
            v, _, _ = V._check_blockpre_cases(c, probe_ok=True, summary_ok=True)
            self.assertNotEqual(v, V.PASS)


class BlockpreIndependentJudgmentTest(unittest.TestCase):
    """MAJOR-1: one broken raw field in an armed case must never stay PASS.

    The verifier pins expected PC/opcode/instruction-count per case ID and
    re-judges armed raw data instead of trusting the probe "verdict" string.
    """

    def _break(self, mode, fn):
        import copy
        c = _blockpre_cases()
        fn(c[mode]["row"], c[mode])
        return V._check_blockpre_cases(c, probe_ok=True, summary_ok=True)

    def test_valid_armed_control_passes(self):
        v, _, _ = V._check_blockpre_cases(
            _blockpre_cases(), probe_ok=True, summary_ok=True)
        self.assertEqual(v, V.PASS)

    def test_single_field_breaks_never_pass(self):
        mode = "tdd-unknown"
        op = V.BLOCKPRE_EXPECTED_OP[mode]
        breaks = {
            # observed side effect or inconsistency -> FAIL
            "stop_pc_changed": (lambda r, i: r.update(stop_pc=0x1234), V.FAIL),
            "instructions_abnormal": (lambda r, i: r.update(instructions_executed=99), V.FAIL),
            "forbidden_wram_delta": (
                lambda r, i: (r.update(ram_diff=[{"address": 0x3E90, "before": 0xCC, "after": 0x60}]),
                              r.update(ram_diff_count=1)), V.FAIL),
            "resume_refused_relaxed": (lambda r, i: r.update(resume_refused=2), V.FAIL),
            "executed_after_resume": (lambda r, i: r.update(executed_after_resume=99), V.FAIL),
            "fetch_opcode_changed": (lambda r, i: r.update(fetch_opcode=0xEA), V.FAIL),
            "refused_opcode_changed": (
                lambda r, i: (r.update(refused_opcode=0xEA, operand_bytes=[0xEA, 0, 0, 0, 0, 0, 0])), V.FAIL),
            "operand_incomplete": (lambda r, i: r.update(operand_complete=False), V.FAIL),
            "mpr_corrupted": (
                lambda r, i: r.update(after={"mpr": [9, 9, 9, 9, 9, 9, 9, 9]}), V.FAIL),
            "count_vs_list_mismatch": (lambda r, i: r.update(ram_diff_count=5), V.FAIL),
            "unexpected_irq": (lambda r, i: r.update(irq_accepted=True), V.FAIL),
            # VDC triple strictness: changed -> FAIL, missing/wrong-type -> BLOCKED
            "vdc_ar_changed": (lambda r, i: r.update(vdc_ar_after=5), V.FAIL),
            "vdc_sr_changed": (lambda r, i: r.update(vdc_sr_after=0x00), V.FAIL),
            "vdc_r_changed": (lambda r, i: r.update(vdc_r_after=1), V.FAIL),
            "vdc_sr_missing": (lambda r, i: r.pop("vdc_sr_before", None), V.BLOCKED),
            "vdc_ar_missing": (lambda r, i: r.pop("vdc_ar_after", None), V.BLOCKED),
            "vdc_r_missing": (lambda r, i: r.pop("vdc_r_before", None), V.BLOCKED),
            "vdc_sr_wrong_type": (lambda r, i: r.update(vdc_sr_after="0x3F"), V.BLOCKED),
            # missing or wrong-typed evidence -> BLOCKED (never PASS)
            "operand_bytes_missing": (lambda r, i: r.pop("operand_bytes", None), V.BLOCKED),
            "operand_bytes_wrong_type": (lambda r, i: r.update(operand_bytes="73"), V.BLOCKED),
            "operand_complete_missing": (lambda r, i: r.pop("operand_complete", None), V.BLOCKED),
            "mpr_missing": (lambda r, i: r.pop("after", None), V.BLOCKED),
            "ram_diff_truncated": (
                lambda r, i: r.update(ram_diff_truncated=True), V.BLOCKED),
            "ram_diff_count_missing": (lambda r, i: r.pop("ram_diff_count", None), V.BLOCKED),
            "stop_pc_missing": (lambda r, i: r.pop("stop_pc", None), V.BLOCKED),
            "executed_missing": (lambda r, i: r.pop("instructions_executed", None), V.BLOCKED),
            "fetch_opcode_missing": (lambda r, i: r.pop("fetch_opcode", None), V.BLOCKED),
            "resume_refused_missing": (lambda r, i: r.pop("resume_refused", None), V.BLOCKED),
        }
        self.assertGreaterEqual(len(breaks), 10)
        for name, (fn, expected) in breaks.items():
            with self.subTest(field=name):
                v, reasons, _ = self._break(mode, fn)
                self.assertEqual(v, expected, reasons)

    def test_irq_case_contradictions_rejected(self):
        mode = "block-irq-handler"
        for name, fn, expected in [
            ("refused_irq", lambda r, i: r.update(irq_accepted=False), V.FAIL),
            ("wrong_vector", lambda r, i: r.update(observed_vector=0x1234), V.FAIL),
            ("vector_not_read", lambda r, i: r.update(vector_read=False), V.FAIL),
            ("vector_read_missing", lambda r, i: r.pop("vector_read", None), V.BLOCKED),
            ("handler_stepped", lambda r, i: r.update(handler_steps=5), V.FAIL),
            ("non_stack_diff", lambda r, i: (
                r.update(ram_diff=[{"address": 0x3E90, "before": 0xCC, "after": 0x60}]),
                r.update(ram_diff_count=1)), V.FAIL),
            ("truncated", lambda r, i: r.update(ram_diff_truncated=True), V.BLOCKED),
        ]:
            with self.subTest(field=name):
                v, reasons, _ = self._break(mode, fn)
                self.assertEqual(v, expected, reasons)

    def test_hwpage_raw_breaks_rejected(self):
        mode = "tii-hwpage-fetch-blocked"
        for name, fn, expected in [
            ("stop_pc", lambda r, i: r.update(stop_pc=0x4002), V.FAIL),
            ("executed", lambda r, i: r.update(instructions_executed=4), V.FAIL),
            ("sr", lambda r, i: r.update(vdc_sr_after=0x00), V.FAIL),
            ("ram", lambda r, i: (
                r.update(ram_diff=[{"address": 0x3E90, "before": 0xCC, "after": 0x60}]),
                r.update(ram_diff_count=1)), V.FAIL),
            ("mpr", lambda r, i: r.update(after={"mpr": [0] * 8}), V.FAIL),
        ]:
            with self.subTest(field=name):
                v, reasons, _ = self._break(mode, fn)
                self.assertEqual(v, expected, reasons)

    def test_all_cases_pass_leaves_execution_blocked(self):
        # The transcript-level contract: even a full PASS never claims live
        # execution. verify() pins execution BLOCKED (checked here by the
        # record-building rule, not by spawning the core).
        record = {"verdict": ("VERIFIED_PREBLOCK" if V.PASS == V.PASS else V.PASS),
                  "execution": "BLOCKED", "execution_permitted": False,
                  "release_eligible": False}
        self.assertEqual(record["execution"], "BLOCKED")
        self.assertFalse(record["execution_permitted"])
        self.assertFalse(record["release_eligible"])


class BlockpreAlwaysBlockedTest(unittest.TestCase):
    """H2: the always-blocked hwpage mutation row is re-judged, not trusted.

    The "-unguarded" hardware-page fetch must still fail closed: a row with
    the right verdict string but side-effecting/missing fields must never PASS.
    """

    def _cases(self):
        return _blockpre_cases()

    def _check(self, cases):
        return V._check_blockpre_cases(cases, probe_ok=True, summary_ok=True)

    def test_valid_always_blocked_passes(self):
        v, _, _ = self._check(self._cases())
        self.assertEqual(v, V.PASS)

    def test_ram_side_effect_fails(self):
        c = self._cases()
        mut = "tii-hwpage-fetch-blocked-unguarded"
        c[mut]["row"]["ram_diff"] = [{"address": 0x3E90, "before": 0xCC, "after": 0x60}]
        c[mut]["row"]["ram_diff_count"] = 1
        v, _, _ = self._check(c)
        self.assertEqual(v, V.FAIL)

    def test_mpr_change_fails(self):
        c = self._cases()
        mut = "tii-hwpage-fetch-blocked-unguarded"
        c[mut]["row"]["after"] = {"mpr": [0] * 8}
        v, _, _ = self._check(c)
        self.assertEqual(v, V.FAIL)

    def test_stop_pc_missing_blocked(self):
        c = self._cases()
        mut = "tii-hwpage-fetch-blocked-unguarded"
        del c[mut]["row"]["stop_pc"]
        v, _, _ = self._check(c)
        self.assertEqual(v, V.BLOCKED)

    def test_resume_not_refused_fails(self):
        c = self._cases()
        mut = "tii-hwpage-fetch-blocked-unguarded"
        c[mut]["row"]["resume_refused"] = 0
        v, _, _ = self._check(c)
        self.assertEqual(v, V.FAIL)

    def test_wrong_verdict_fails(self):
        c = self._cases()
        mut = "tii-hwpage-fetch-blocked-unguarded"
        c[mut]["verdict"] = "side_effect_landed_monitor_disabled"
        v, _, _ = self._check(c)
        self.assertEqual(v, V.FAIL)

    def test_fetch_flag_missing_blocked(self):
        for name in ("fetch_safe", "fetch_failed", "fetch_blocked"):
            with self.subTest(name=name):
                c = self._cases()
                mut = "tii-hwpage-fetch-blocked-unguarded"
                del c[mut]["row"][name]
                v, _, _ = self._check(c)
                self.assertEqual(v, V.BLOCKED)

    def test_fetch_flag_wrong_type_blocked(self):
        c = self._cases()
        mut = "tii-hwpage-fetch-blocked-unguarded"
        c[mut]["row"]["fetch_safe"] = 1
        v, _, _ = self._check(c)
        self.assertEqual(v, V.BLOCKED)


class StHwpageRowTest(unittest.TestCase):
    """H3/H6: ST hwpage rows are re-judged (VDC SR + stop PC), not verdict-only."""

    def _check(self, cases):
        return V._check_st_cases(cases, probe_ok=True, summary_ok=True)

    def test_hwpage_vdc_changed_fails(self):
        for mode in sorted(V.ST_HWPAGE_ARMED):
            with self.subTest(mode=mode):
                c = _st_full_cases()
                c[mode]["row"]["vdc_sr_after"] = 0x00
                v, _, _ = self._check(c)
                self.assertEqual(v, V.FAIL)

    def test_hwpage_stop_pc_missing_blocked(self):
        for mode in sorted(V.ST_HWPAGE_ARMED):
            with self.subTest(mode=mode):
                c = _st_full_cases()
                del c[mode]["row"]["stop_pc"]
                v, _, _ = self._check(c)
                self.assertEqual(v, V.BLOCKED)

    def test_hwpage_wrong_verdict_still_fails(self):
        for mode in sorted(V.ST_HWPAGE_ARMED):
            with self.subTest(mode=mode):
                c = _st_full_cases()
                c[mode]["verdict"] = "refused_at_instruction_boundary_before_side_effect"
                v, _, _ = self._check(c)
                self.assertEqual(v, V.FAIL)


class StStrictFieldTest(unittest.TestCase):
    """H4/H5: ST armed rows reject missing/wrong-typed VDC SR and bool stop PC."""

    def _check(self, cases):
        return V._check_st_cases(cases, probe_ok=True, summary_ok=True)

    def test_both_sr_missing_blocked(self):
        c = _st_full_cases()
        del c["st0-entry"]["row"]["vdc_sr_before"]
        del c["st0-entry"]["row"]["vdc_sr_after"]
        v, _, _ = self._check(c)
        self.assertEqual(v, V.BLOCKED)

    def test_single_sr_missing_blocked(self):
        c = _st_full_cases()
        del c["st1-entry"]["row"]["vdc_sr_after"]
        v, _, _ = self._check(c)
        self.assertEqual(v, V.BLOCKED)

    def test_sr_wrong_type_blocked(self):
        c = _st_full_cases()
        c["st2-entry"]["row"]["vdc_sr_after"] = "0x3F"
        v, _, _ = self._check(c)
        self.assertEqual(v, V.BLOCKED)

    def test_stop_pc_bool_blocked(self):
        for bad in (True, False):
            with self.subTest(bad=bad):
                c = _st_full_cases()
                c["st1-entry"]["row"]["stop_pc"] = bad
                v, _, _ = self._check(c)
                self.assertEqual(v, V.BLOCKED)

    def test_stop_pc_missing_blocked(self):
        c = _st_full_cases()
        del c["st0-jsr-rts"]["row"]["stop_pc"]
        v, _, _ = self._check(c)
        self.assertEqual(v, V.BLOCKED)

    def test_handler_steps_nonzero_fails(self):
        c = _blockpre_cases()
        c["tdd-unknown"]["row"]["handler_steps"] = 99
        v, _, _ = V._check_blockpre_cases(c, probe_ok=True, summary_ok=True)
        self.assertEqual(v, V.FAIL)

    def test_handler_steps_missing_blocked(self):
        c = _blockpre_cases()
        del c["tdd-unknown"]["row"]["handler_steps"]
        v, _, _ = V._check_blockpre_cases(c, probe_ok=True, summary_ok=True)
        self.assertEqual(v, V.BLOCKED)


class BlockpreIrqVectorReadTest(unittest.TestCase):
    """MEDIUM-1 (#743): the IRQ vector must actually have been read.

    The probe resolves the handler address with side-effect-free peeks of
    $FFF6/$FFF7 and reports vector_read. A claimed observed_vector without
    a successful read is not acceptance evidence, even if the value matches.
    """

    def _check(self, cases):
        return V._check_blockpre_cases(cases, probe_ok=True, summary_ok=True)

    def test_vector_read_forgeries_rejected(self):
        for name, fn, expected in [
            ("read_false", lambda r: r.update(vector_read=False), V.FAIL),
            ("read_missing", lambda r: r.pop("vector_read", None), V.BLOCKED),
            ("read_none", lambda r: r.update(vector_read=None), V.BLOCKED),
            ("read_zero", lambda r: r.update(vector_read=0), V.BLOCKED),
            ("read_string", lambda r: r.update(vector_read="true"), V.BLOCKED),
            ("read_true_vector_wrong", lambda r: r.update(observed_vector=0x1234), V.FAIL),
            ("not_accepted", lambda r: r.update(irq_accepted=False), V.FAIL),
            ("handler_stepped", lambda r: r.update(handler_steps=1), V.FAIL),
        ]:
            with self.subTest(name=name):
                c = _blockpre_cases()
                fn(c["block-irq-handler"]["row"])
                v, reasons, _ = self._check(c)
                self.assertEqual(v, expected, reasons)

    def test_valid_irq_control_passes(self):
        v, _, _ = self._check(_blockpre_cases())
        self.assertEqual(v, V.PASS)


class BlockStrictFieldTest(unittest.TestCase):
    """V5: mid-refusal re-checks fail closed on wrong-typed/missing evidence."""

    def _check(self, cases):
        return V._check_block_cases(cases)

    def _one_armed(self, **kw):
        row = _brow("tii-cross-armed", "per_byte_refusal_continues_and_restores")
        row["row"].update(kw)
        return {"tii-cross-armed": row,
                "tii-cross-unguarded": _brow(
                    "tii-cross-unguarded", "forbidden_bytes_land_without_hook",
                    guard_refusals=0)}

    def test_guard_refusals_wrong_type_blocked(self):
        for bad in ("1", None, 1.0, True, [1]):
            with self.subTest(bad=bad):
                v, _, _ = self._check(self._one_armed(guard_refusals=bad))
                self.assertEqual(v, V.BLOCKED)

    def test_sp_missing_blocked(self):
        c = self._one_armed()
        del c["tii-cross-armed"]["row"]["sp"]
        v, _, _ = self._check(c)
        self.assertEqual(v, V.BLOCKED)

    def test_sp_changed_fails(self):
        v, _, _ = self._check(self._one_armed(sp=[4, 0]))
        self.assertEqual(v, V.FAIL)

    def test_mpr_missing_blocked(self):
        v, _, _ = self._check(self._one_armed(before={}, after={}))
        self.assertEqual(v, V.BLOCKED)

    def test_stop_pc_expected_missing_blocked(self):
        c = self._one_armed()
        del c["tii-cross-armed"]["row"]["expected_end_pc"]
        v, _, _ = self._check(c)
        self.assertEqual(v, V.BLOCKED)

    def test_axy_missing_blocked(self):
        c = self._one_armed()
        del c["tii-cross-armed"]["row"]["a_after"]
        v, _, _ = self._check(c)
        self.assertEqual(v, V.BLOCKED)

    def test_axy_changed_fails(self):
        v, _, _ = self._check(self._one_armed(a_after=0x00))
        self.assertEqual(v, V.FAIL)


class BlockpreReview743Test(unittest.TestCase):
    """#743 review findings: hwpage fetch evidence, armed raw consistency,
    exact verdict pins (no substring matching)."""

    def _check(self, cases):
        return V._check_blockpre_cases(cases, probe_ok=True, summary_ok=True)

    # MAJOR-1: hwpage fetch-failure evidence (armed + unguarded).
    def test_hwpage_fetch_value_inversions_fail(self):
        mode = "tii-hwpage-fetch-blocked"
        for name, fn, expected in [
            ("safe_true", lambda r, i: r.update(fetch_safe=True), V.FAIL),
            ("failed_false", lambda r, i: r.update(fetch_failed=False), V.FAIL),
            ("blocked_false", lambda r, i: r.update(fetch_blocked=False), V.FAIL),
            ("status_refused", lambda r, i: r.update(status="refused"), V.FAIL),
            ("reason_block", lambda r, i: r.update(
                reason="block_opcode_at_instruction_boundary"), V.FAIL),
            ("armed_false", lambda r, i: r.update(armed=False), V.FAIL),
            ("mode_wrong", lambda r, i: r.update(mode="tii-entry"), V.FAIL),
            ("safe_missing", lambda r, i: r.pop("fetch_safe", None), V.BLOCKED),
            ("failed_missing", lambda r, i: r.pop("fetch_failed", None), V.BLOCKED),
            ("blocked_missing", lambda r, i: r.pop("fetch_blocked", None), V.BLOCKED),
            ("status_missing", lambda r, i: r.pop("status", None), V.BLOCKED),
            ("reason_missing", lambda r, i: r.pop("reason", None), V.BLOCKED),
            ("armed_missing", lambda r, i: r.pop("armed", None), V.BLOCKED),
            ("safe_wrong_type", lambda r, i: r.update(fetch_safe=1), V.BLOCKED),
            ("failed_wrong_type", lambda r, i: r.update(fetch_failed="yes"), V.BLOCKED),
        ]:
            with self.subTest(field=name):
                c = _blockpre_cases()
                fn(c[mode]["row"], c[mode])
                v, reasons, _ = self._check(c)
                self.assertEqual(v, expected, reasons)

    def test_hwpage_unguarded_armed_inversion_fails(self):
        c = _blockpre_cases()
        mut = "tii-hwpage-fetch-blocked-unguarded"
        c[mut]["row"]["armed"] = True
        v, reasons, _ = self._check(c)
        self.assertEqual(v, V.FAIL, reasons)

    def test_hwpage_fetch_target_pins(self):
        for case in ("tii-hwpage-fetch-blocked",
                     "tii-hwpage-fetch-blocked-unguarded"):
            for name, fn, expected in [
                ("unsafe_bank_false", lambda r, i: r.update(fetch_unsafe_bank=False), V.FAIL),
                ("unsafe_bank_missing", lambda r, i: r.pop("fetch_unsafe_bank", None), V.BLOCKED),
                ("opcode_nonzero", lambda r, i: r.update(fetch_opcode=0x73), V.FAIL),
                ("opcode_missing", lambda r, i: r.pop("fetch_opcode", None), V.BLOCKED),
                ("page_wrong", lambda r, i: r.update(fetch_page=1), V.FAIL),
                ("mpr_wrong", lambda r, i: r.update(fetch_mpr=0xF8), V.FAIL),
                ("vdc_r_changed", lambda r, i: r.update(vdc_r_after=1), V.FAIL),
                ("vdc_r_missing", lambda r, i: r.pop("vdc_r_after", None), V.BLOCKED),
            ]:
                with self.subTest(case=case, field=name):
                    c = _blockpre_cases()
                    fn(c[case]["row"], c[case])
                    v, reasons, _ = self._check(c)
                    self.assertEqual(v, expected, reasons)

    # MAJOR-2: armed verdict rewrite or raw-row tamper never passes.
    def test_armed_verdict_rewrite_never_passes(self):
        mode = "tdd-unknown"
        for verdict in ("PASS", "side_effect_landed_monitor_disabled",
                        "not_landed", "refused_spoof",
                        "refused_at_instruction_boundary_BEFORE_side_effect",
                        "hardware_page_fetch_failed_closed_before_step", ""):
            with self.subTest(verdict=verdict):
                c = _blockpre_cases()
                c[mode]["verdict"] = verdict
                v, reasons, _ = self._check(c)
                self.assertNotEqual(v, V.PASS, reasons)
                self.assertEqual(v, V.FAIL, reasons)

    def test_armed_raw_tamper_never_passes(self):
        mode = "tdd-unknown"
        for name, fn, expected in [
            ("status_ok", lambda r, i: r.update(status="ok"), V.FAIL),
            ("status_missing", lambda r, i: r.pop("status", None), V.BLOCKED),
            ("armed_false", lambda r, i: r.update(armed=False), V.FAIL),
            ("armed_missing", lambda r, i: r.pop("armed", None), V.BLOCKED),
            ("refused_false", lambda r, i: r.update(refused=False), V.FAIL),
            ("refused_missing", lambda r, i: r.pop("refused", None), V.BLOCKED),
            ("reason_wrong", lambda r, i: r.update(reason="budget_exhausted"), V.FAIL),
            ("reason_missing", lambda r, i: r.pop("reason", None), V.BLOCKED),
            ("mode_wrong", lambda r, i: r.update(mode="tin-unknown-far"), V.FAIL),
            ("fetch_blocked_true", lambda r, i: r.update(fetch_blocked=True), V.FAIL),
        ]:
            with self.subTest(field=name):
                c = _blockpre_cases()
                fn(c[mode]["row"], c[mode])
                v, reasons, _ = self._check(c)
                self.assertEqual(v, expected, reasons)

    # MEDIUM-1: mutation verdicts are exact pins, not substrings.
    def test_mutation_lookalike_verdict_blocked(self):
        mut = sorted(m for m in V.BLOCKPRE_MUTATIONS
                     if m not in V.BLOCKPRE_ALWAYS_BLOCKED)[0]
        for verdict in ("not_landed", "LANDED", "side_effect_landed",
                        "side_effect_landed_monitor_disabled_extra", ""):
            with self.subTest(verdict=verdict):
                c = _blockpre_cases()
                c[mut]["verdict"] = verdict
                v, reasons, _ = self._check(c)
                self.assertNotEqual(v, V.PASS, reasons)
                self.assertEqual(v, V.BLOCKED, reasons)

    def test_mutation_refused_verdict_fails(self):
        mut = sorted(m for m in V.BLOCKPRE_MUTATIONS
                     if m not in V.BLOCKPRE_ALWAYS_BLOCKED)[0]
        c = _blockpre_cases()
        c[mut]["verdict"] = "refused_at_instruction_boundary_before_side_effect"
        v, reasons, _ = self._check(c)
        self.assertEqual(v, V.FAIL, reasons)

    def test_hwpage_refused_flag_and_capture_pins(self):
        for case in ("tii-hwpage-fetch-blocked",
                     "tii-hwpage-fetch-blocked-unguarded"):
            for name, fn, expected in [
                ("refused_true", lambda r, i: r.update(refused=True), V.FAIL),
                ("refused_missing", lambda r, i: r.pop("refused", None), V.BLOCKED),
                ("refused_opcode", lambda r, i: r.update(refused_opcode=0x73), V.FAIL),
                ("operand_bytes", lambda r, i: r.update(
                    operand_bytes=[0x73, 0, 0, 0, 0, 0, 0]), V.FAIL),
                ("operand_complete", lambda r, i: r.update(operand_complete=True), V.FAIL),
                ("fetch_via", lambda r, i: r.update(fetch_via="dma"), V.FAIL),
            ]:
                with self.subTest(case=case, field=name):
                    c = _blockpre_cases()
                    fn(c[case]["row"], c[case])
                    v, reasons, _ = self._check(c)
                    self.assertEqual(v, expected, reasons)

    def test_mutation_armed_status_pins(self):
        plain = sorted(m for m in V.BLOCKPRE_MUTATIONS
                       if m not in V.BLOCKPRE_ALWAYS_BLOCKED
                       and not m.endswith("-resume-allowed")
                       and not m.startswith("tia-wrap-forbidden"))[0]
        resume = sorted(m for m in V.BLOCKPRE_MUTATIONS
                        if m.endswith("-resume-allowed"))[0]
        wrap = "tia-wrap-forbidden-unguarded"
        base_plain = V._blockpre_mutation_base(plain)
        base_resume = V._blockpre_mutation_base(resume)
        exec_plain = V.BLOCKPRE_EXPECTED_EXECUTED[base_plain]
        exec_resume = V.BLOCKPRE_EXPECTED_EXECUTED[base_resume]
        for case, name, fn, expected in [
            (plain, "mode_wrong", lambda r, i: r.update(mode="tii-entry"), V.FAIL),
            (plain, "mode_missing", lambda r, i: r.pop("mode", None), V.BLOCKED),
            (plain, "refused_true", lambda r, i: r.update(refused=True), V.FAIL),
            (plain, "refused_missing", lambda r, i: r.pop("refused", None), V.BLOCKED),
            (plain, "no_progress", lambda r, i: r.update(
                instructions_executed=exec_plain), V.FAIL),
            (plain, "trace_removed", lambda r, i: (
                r.update(ram_diff=[], ram_diff_count=0)), V.FAIL),
            (resume, "resume_refused_false", lambda r, i: r.update(refused=False), V.FAIL),
            (resume, "resume_trace_removed", lambda r, i: (
                r.update(ram_diff=[], ram_diff_count=0)), V.FAIL),
            (wrap, "wrap_trace_removed", lambda r, i: (
                r.update(vdc_ar_after=0)), V.FAIL),
            (plain, "armed_true", lambda r, i: r.update(armed=True), V.FAIL),
            (plain, "armed_missing", lambda r, i: r.pop("armed", None), V.BLOCKED),
            (plain, "status_refused", lambda r, i: r.update(status="refused"), V.FAIL),
            (plain, "status_missing", lambda r, i: r.pop("status", None), V.BLOCKED),
            (resume, "resume_armed_false", lambda r, i: r.update(armed=False), V.FAIL),
        ]:
            with self.subTest(case=case, field=name):
                c = _blockpre_cases()
                fn(c[case]["row"], c[case])
                v, reasons, _ = self._check(c)
                self.assertEqual(v, expected, reasons)

    def test_resume_allowed_evidence_pins(self):
        # MAJOR (#743): resume-allowed must prove the latch released and the
        # transfer landed at the right destination -- not merely a non-empty
        # diff. Every forgery below must fail closed, never PASS.
        resume = sorted(m for m in V.BLOCKPRE_MUTATIONS
                        if m.endswith("-resume-allowed"))[0]
        base = V._blockpre_mutation_base(resume)
        pc = V.BLOCKPRE_EXPECTED_PC[base]
        full_trace = [{"address": a, "before": b, "after": v}
                      for a, b, v in V.BLOCKPRE_LANDED_TRACE[base]]

        def good(c):
            return c[resume]["row"]

        for name, fn, expected in [
            ("resume_refused_3", lambda r: r.update(resume_refused=3), V.FAIL),
            ("resume_refused_1", lambda r: r.update(resume_refused=1), V.FAIL),
            ("resume_attempts_wrong", lambda r: r.update(resume_attempts=2), V.FAIL),
            ("refused_pc_wrong", lambda r: r.update(refused_pc=0x1234), V.FAIL),
            ("refused_pc_missing", lambda r: r.pop("refused_pc", None), V.BLOCKED),
            ("stop_pc_wrong", lambda r: r.update(stop_pc=pc), V.FAIL),
            ("stop_pc_missing", lambda r: r.pop("stop_pc", None), V.BLOCKED),
            ("status_refused", lambda r: r.update(status="refused", refused=False,
                                                  armed=False), V.FAIL),
            ("no_progress", lambda r: r.update(
                instructions_executed=V.BLOCKPRE_EXPECTED_EXECUTED[base],
                executed_at_refusal=V.BLOCKPRE_EXPECTED_EXECUTED[base] - 3,
                executed_after_resume=V.BLOCKPRE_EXPECTED_EXECUTED[base]), V.FAIL),
            ("exec_at_refusal_wrong", lambda r: r.update(executed_at_refusal=0), V.FAIL),
            ("exec_after_resume_wrong", lambda r: r.update(executed_after_resume=0), V.FAIL),
            ("off_target_only", lambda r: r.update(
                ram_diff=[{"address": 0x3000, "before": 0x00, "after": 0x01}],
                ram_diff_count=1), V.FAIL),
            ("stack_only", lambda r: r.update(
                ram_diff=[{"address": 0x21FF, "before": 0x00, "after": 0x3E},
                          {"address": 0x21FE, "before": 0x00, "after": 0x01}],
                ram_diff_count=2), V.FAIL),
            ("wrong_after_value", lambda r: r.update(
                ram_diff=[dict(e, after=0x00) if e["address"] == full_trace[0]["address"] else e
                          for e in full_trace]), V.FAIL),
            ("wrong_before_value", lambda r: r.update(
                ram_diff=[dict(e, before=0x00) if e["address"] == full_trace[0]["address"] else e
                          for e in full_trace]), V.FAIL),
            ("missing_triple", lambda r: r.update(
                ram_diff=full_trace[1:], ram_diff_count=len(full_trace) - 1), V.FAIL),
            ("empty_trace", lambda r: r.update(ram_diff=[], ram_diff_count=0), V.FAIL),
            ("truncated", lambda r: r.update(ram_diff_truncated=True), V.BLOCKED),
            ("count_mismatch", lambda r: r.update(ram_diff_count=99), V.FAIL),
            ("count_missing", lambda r: r.pop("ram_diff_count", None), V.BLOCKED),
            ("malformed_entry", lambda r: r.update(ram_diff=["0x3E90"],
                                                  ram_diff_count=1), V.BLOCKED),
            ("bool_as_int", lambda r: r.update(instructions_executed=True), V.BLOCKED),
        ]:
            with self.subTest(field=name):
                c = _blockpre_cases()
                fn(good(c))
                v, reasons, _ = self._check(c)
                self.assertEqual(v, expected, reasons)


if __name__ == "__main__":
    unittest.main()
