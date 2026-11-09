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

    def test_missing_armed_blocked(self):
        c = {k: v for k, v in self._cases().items() if k != "st0-entry"}
        v, _, _ = V._check_st_cases(c)
        self.assertEqual(v, V.BLOCKED)


if __name__ == "__main__":
    unittest.main()
