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
    def _cases(self, armed_verdict="REFUSED", mut_verdict="LANDED"):
        return {
            "st0-entry": _item("st0-entry", armed_verdict, _row()),
            "st0-entry-unguarded": _item("st0-entry-unguarded", mut_verdict,
                                         _row(instructions_executed=1, vdc_sr_after=0x00,
                                              status="landed")),
            "pos-write": _item("pos-write", "PASS", _row()),
        }

    def test_all_good_passes(self):
        v, reasons, halt = V._check_st_cases(self._cases())
        self.assertEqual(v, V.PASS)
        self.assertIn("st0-entry", halt)

    def test_armed_setup_steps_allowed_when_refused(self):
        # Non-entry families execute setup steps before refusal; the refused
        # verdict + unchanged SR is the gate, not an executed==0 count.
        c = self._cases()
        c["st0-entry"] = _item("st0-entry", "refused_at_instruction_boundary_before_side_effect",
                               _row(instructions_executed=3))
        v, _, _ = V._check_st_cases(c)
        self.assertEqual(v, V.PASS)

    def test_armed_hwpage_verdict_passes(self):
        c = self._cases()
        c["st0-entry"] = _item("st0-entry", "hardware_page_fetch_failed_closed_before_step",
                               _row(vdc_sr_before=0x3F, vdc_sr_after=0x3F))
        v, _, _ = V._check_st_cases(c)
        self.assertEqual(v, V.PASS)

    def test_armed_landed_verdict_fails(self):
        c = self._cases()
        c["st0-entry"] = _item("st0-entry", "side_effect_landed_monitor_disabled",
                               _row(instructions_executed=2, vdc_sr_after=0x00))
        v, _, _ = V._check_st_cases(c)
        self.assertEqual(v, V.FAIL)

    def test_armed_vdc_changed_fails(self):
        c = self._cases()
        c["st0-entry"] = _item("st0-entry", "REFUSED", _row(vdc_sr_after=0x00))
        v, _, _ = V._check_st_cases(c)
        self.assertEqual(v, V.FAIL)

    def test_mutation_refused_fails(self):
        c = self._cases(mut_verdict="REFUSED")
        v, _, _ = V._check_st_cases(c)
        self.assertEqual(v, V.FAIL)

    def test_wrong_pin_fails(self):
        c = self._cases()
        bad = _item("st0-entry", "REFUSED", _row())
        bad["core_revision"] = "deadbeef"
        c["st0-entry"] = bad
        v, _, _ = V._check_st_cases(c)
        self.assertEqual(v, V.FAIL)

    def test_missing_pos_control_blocked(self):
        c = self._cases()
        del c["pos-write"]
        v, _, _ = V._check_st_cases(c)
        self.assertEqual(v, V.BLOCKED)

    def test_pos_control_by_probe_exit_accepted(self):
        c = {k: v for k, v in self._cases().items() if k != "pos-write"}
        v, reasons, _ = V._check_st_cases(c, probe_ok=True, summary_ok=True)
        self.assertEqual(v, V.PASS)
        self.assertIn("POS_CONTROL_BY_PROBE_EXIT", reasons)

    def test_pos_control_by_probe_exit_requires_summary(self):
        c = {k: v for k, v in self._cases().items() if k != "pos-write"}
        v, _, _ = V._check_st_cases(c, probe_ok=True, summary_ok=False)
        self.assertEqual(v, V.BLOCKED)

    def test_hwpage_unguarded_expected_blocked(self):
        c = self._cases()
        c["tii-hwpage-fetch-blocked-unguarded"] = _item(
            "tii-hwpage-fetch-blocked-unguarded",
            "hardware_page_fetch_failed_closed_before_step",
            _row(stop_pc=0x4000))
        v, _, _ = V._check_st_cases(
            c, probe_ok=True, summary_ok=True,
            always_blocked=("tii-hwpage-fetch-blocked-unguarded",))
        self.assertEqual(v, V.PASS)

    def test_hwpage_unguarded_landed_is_fail(self):
        c = self._cases()
        c["tii-hwpage-fetch-blocked-unguarded"] = _item(
            "tii-hwpage-fetch-blocked-unguarded",
            "side_effect_landed_monitor_disabled",
            _row(stop_pc=0x4002, vdc_sr_after=0x00))
        v, _, _ = V._check_st_cases(
            c, probe_ok=True, summary_ok=True,
            always_blocked=("tii-hwpage-fetch-blocked-unguarded",))
        self.assertEqual(v, V.FAIL)


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
        c = {k: v for k, v in self._cases().items() if k != "st0-entry"}
        v, _, _ = V._check_st_cases(c)
        self.assertEqual(v, V.BLOCKED)


if __name__ == "__main__":
    unittest.main()
