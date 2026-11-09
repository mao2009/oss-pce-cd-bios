#!/usr/bin/env python3
"""Host tests for the block-preblock driver classifier (synthetic rows only).

No emulator, no ROM, no core. Every falsified row must be rejected; the
unmutated armed rows must pass. Covers RED negatives: source-read side
effect, forbidden destination, length overflow proxy, address wrap,
MPR change, IRQ concurrency, unknown PC, and missing evidence.
"""
import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "integration"))
sys.path.insert(0, str(ROOT / "tools"))
import geargrafx_block_preblock as B  # noqa: E402


def _armed_row(mode="tii-entry"):
    # Measured mapping: host sets MPR1=$F8 with the rest $00; only the guest
    # TAM changes MPR2 (tia-wrap/hwpage -> $FF, tai-mpr2 -> $F8).
    before = list(B.MPR_INITIAL)
    after = list(B.MPR_INITIAL)
    if mode == "tia-wrap-forbidden":
        after = list(B.MPR_AFTER_TIA_WRAP)
    elif mode == "tai-mpr2-unknown":
        after = list(B.MPR_AFTER_TAI_MPR2)
    op = B.BLOCK_OP[mode]
    return {
        "exit": 0, "mode": mode, "status": "refused",
        "reason": "block_opcode_at_instruction_boundary",
        "armed": True, "refused": True, "refused_opcode": op,
        "refused_pc": B.BLOCK_EXPECTED_PC[mode], "fetch_opcode": op,
        "fetch_safe": True, "fetch_failed": False,
        "operand_bytes": [op, 0x80, 0x3E, 0x90, 0x3E, 0x04, 0x00],
        "operand_complete": True,
        "stop_pc": B.BLOCK_EXPECTED_PC[mode],
        "instructions_executed": B.BLOCK_EXECUTED_BEFORE_REFUSAL[mode],
        "executed_at_refusal": B.BLOCK_EXECUTED_BEFORE_REFUSAL[mode],
        "vdc_ar_before": 0, "vdc_ar_after": 0,
        "vdc_sr_before": 0, "vdc_sr_after": 0,
        "vdc_r_before": 0, "vdc_r_after": 0,
        "vdc_read_buffer_before": 0xBEEF, "vdc_read_buffer_after": 0xBEEF,
        "ram_diff": [], "only_stack_net_diffs": True,
        "before": {"mpr": before, "sp": 0xFF},
        "after": {"mpr": after, "sp": 0xFF},
        "mpr_changed": mode in ("tia-wrap-forbidden", "tai-mpr2-unknown"),
        "resume_attempts": 3, "resume_refused": 3,
        "executed_after_resume": B.BLOCK_EXECUTED_BEFORE_REFUSAL[mode],
        "ram_unchanged_during_resume": True,
        "irq_accepted": False, "observed_vector": 0, "handler_steps": 0,
    }


class ArmedTest(unittest.TestCase):
    def test_all_armed_modes_pass(self):
        for mode in B.ARMED_MODES:
            if mode in B.HWPAGE_MODES:
                continue
            with self.subTest(mode=mode):
                row = _armed_row(mode)
                if mode == "block-irq-handler":
                    row.update({"irq_accepted": True, "observed_vector": 0x3F80,
                                "ram_diff": [{"address": 0x21FF, "before": 0, "after": 1},
                                             {"address": 0x21FE, "before": 0, "after": 2},
                                             {"address": 0x21FD, "before": 0, "after": 3}]})
                    row["after"]["sp"] = 0xFC
                self.assertEqual(B.check_armed(row, mode), B.REFUSED_VERDICT)

    def test_hwpage_armed_passes(self):
        row = _armed_row("tii-entry")
        row.update({"mode": "tii-hwpage-fetch-blocked", "status": "fetch_blocked",
                    "fetch_blocked": True,
                    "reason": "unsafe_opcode_fetch_fail_closed",
                    "refused": False, "stop_pc": 0x4000, "instructions_executed": 3,
                    "executed_at_refusal": 3, "fetch_safe": False, "fetch_failed": True,
                    "vdc_ar_before": 5, "vdc_ar_after": 5,
                    "vdc_sr_before": 0x3F, "vdc_sr_after": 0x3F,
                    "vdc_read_buffer_before": 0xBEEF, "vdc_read_buffer_after": 0xBEEF,
                    "after": {"mpr": [0x00, 0xF8, 0xFF, 0, 0, 0, 0, 0], "sp": 0xFF}})
        self.assertEqual(B.check_hwpage_armed(row, "tii-hwpage-fetch-blocked"),
                         B.HWPAGE_BLOCKED_VERDICT)

    def test_falsifications_rejected(self):
        base = _armed_row("tdd-unknown")
        fals = {
            "status-ok": {"status": "ok"},
            "wrong-op": {"refused_opcode": 0xEA, "fetch_opcode": 0xEA,
                         "operand_bytes": [0xEA, 0, 0, 0, 0, 0, 0]},
            "wrong-pc": {"refused_pc": 0x1234, "stop_pc": 0x1234},
            "vdc": {"vdc_sr_before": 0x3F, "vdc_sr_after": 0x00},
            "vdc-read-buffer": {"vdc_read_buffer_before": 0xBEEF,
                                "vdc_read_buffer_after": 0x1234},
            "ram": {"ram_diff": [{"address": 0x3E90, "before": 0xCC, "after": 0x60}]},
            "resume": {"resume_refused": 2},
            "operand": {"operand_complete": False},
            "mpr": {"after": {"mpr": [0xFF] * 8, "sp": 0xFF}},
        }
        self.assertGreaterEqual(len(fals), 8)
        for name, change in fals.items():
            with self.subTest(name=name):
                row = copy.deepcopy(base)
                row.update(change)
                with self.assertRaises(ValueError):
                    B.check_armed(row, "tdd-unknown")

    def test_missing_evidence_blocked(self):
        for key in ("operand_bytes", "stop_pc", "resume_refused",
                    "vdc_read_buffer_before", "vdc_read_buffer_after"):
            row = _armed_row("tii-entry")
            del row[key]
            with self.subTest(missing=key):
                with self.assertRaises((ValueError, KeyError, TypeError)):
                    B.check_armed(row, "tii-entry")

    def test_read_buffer_wrong_type_blocked(self):
        for bad in (None, "0xBEEF", 0.5, True, [0xBEEF]):
            row = _armed_row("tii-entry")
            row["vdc_read_buffer_after"] = bad
            with self.subTest(bad=bad):
                with self.assertRaises((ValueError, KeyError, TypeError)):
                    B.check_armed(row, "tii-entry")


class UnarmedTest(unittest.TestCase):
    def _landed(self, mode="tii-entry"):
        row = _armed_row(mode)
        row.update({"armed": False, "refused": False, "status": "ok",
                    "reason": "", "stop_pc": B.BLOCK_EXPECTED_PC[mode] + 7,
                    "instructions_executed": B.BLOCK_EXECUTED_BEFORE_REFUSAL[mode] + 2,
                    "ram_diff": [{"address": 0x3E90 + i, "before": 0xCC, "after": v}
                                 for i, v in enumerate(B.PATTERN)]})
        return row

    def test_unguarded_lands(self):
        self.assertEqual(B.check_unguarded_landed(self._landed(), "tii-entry"), B.LANDED_VERDICT)

    def test_wrap_unguarded_vdc_trace(self):
        row = self._landed("tia-wrap-forbidden")
        row.update({"ram_diff": [], "vdc_ar_before": 5, "vdc_ar_after": 0x1B})
        self.assertEqual(B.check_unguarded_landed(row, "tia-wrap-forbidden"), B.LANDED_VERDICT)

    def test_wrap_unguarded_without_trace_blocked(self):
        row = self._landed("tia-wrap-forbidden")
        row.update({"ram_diff": [], "vdc_ar_before": 5, "vdc_ar_after": 5})
        with self.assertRaises(ValueError):
            B.check_unguarded_landed(row, "tia-wrap-forbidden")

    def test_refused_mutant_rejected(self):
        row = self._landed()
        row.update({"refused": True, "status": "refused"})
        with self.assertRaises(ValueError):
            B.check_unguarded_landed(row, "tii-entry")

    def test_resume_allowed_lands(self):
        row = self._landed("tdd-unknown")
        row.update({"refused": True, "resume_refused": 0, "stop_pc": 0x3E17,
                    "ram_unchanged_during_resume": False})
        self.assertEqual(B.check_resume_allowed_landed(row, "tdd-unknown"), B.LANDED_VERDICT)

    def test_resume_held_is_not_landed(self):
        row = self._landed("tdd-unknown")
        row.update({"resume_refused": 3, "stop_pc": 0x3E10,
                    "instructions_executed": 1, "ram_diff": []})
        with self.assertRaises(ValueError):
            B.check_resume_allowed_landed(row, "tdd-unknown")

    def test_hwpage_unguarded_stays_blocked(self):
        row = _armed_row("tii-entry")
        row.update({"mode": "tii-hwpage-fetch-blocked", "status": "fetch_blocked",
                    "refused": False, "fetch_blocked": True, "stop_pc": 0x4000,
                    "instructions_executed": 3, "fetch_safe": False, "fetch_failed": True,
                    "vdc_ar_before": 5, "vdc_ar_after": 5,
                    "vdc_sr_before": 0x3F, "vdc_sr_after": 0x3F,
                    "vdc_read_buffer_before": 0xBEEF, "vdc_read_buffer_after": 0xBEEF,
                    "after": {"mpr": [0x00, 0xF8, 0xFF, 0, 0, 0, 0, 0], "sp": 0xFF}})
        self.assertEqual(B.check_hwpage_unguarded_still_blocked(row, "tii-hwpage-fetch-blocked"),
                         B.HWPAGE_BLOCKED_VERDICT)


class ExitValidationTest(unittest.TestCase):
    """MEDIUM-1: exit is required, type(exit) is int, only 0 is normal."""

    def _classifiers(self):
        hw = _armed_row("tii-entry")
        hw.update({"mode": "tii-hwpage-fetch-blocked", "status": "fetch_blocked",
                   "fetch_blocked": True, "refused": False, "stop_pc": 0x4000,
                   "instructions_executed": 3, "executed_at_refusal": 3,
                   "fetch_safe": False, "fetch_failed": True,
                   "vdc_ar_before": 5, "vdc_ar_after": 5,
                   "vdc_sr_before": 0x3F, "vdc_sr_after": 0x3F,
                   "after": {"mpr": list(B.MPR_AFTER_TIA_WRAP), "sp": 0xFF}})
        landed = _armed_row("tii-entry")
        landed.update({"armed": False, "refused": False, "status": "ok", "reason": "",
                       "stop_pc": B.BLOCK_EXPECTED_PC["tii-entry"] + 7,
                       "instructions_executed": B.BLOCK_EXECUTED_BEFORE_REFUSAL["tii-entry"] + 2,
                       "ram_diff": [{"address": 0x3E90 + i, "before": 0xCC, "after": v}
                                    for i, v in enumerate(B.PATTERN)]})
        resume = _armed_row("tdd-unknown")
        resume.update({"armed": False, "refused": True, "status": "ok", "reason": "",
                       "stop_pc": 0x3E17, "resume_refused": 0,
                       "instructions_executed": B.BLOCK_EXECUTED_BEFORE_REFUSAL["tdd-unknown"] + 2,
                       "ram_diff": [{"address": 0x3E90, "before": 0xCC, "after": 0x60}],
                       "ram_unchanged_during_resume": False})
        return [
            (B.check_armed, _armed_row("tii-entry"), "tii-entry"),
            (B.check_unguarded_landed, landed, "tii-entry"),
            (B.check_resume_allowed_landed, resume, "tdd-unknown"),
            (B.check_hwpage_armed, hw, "tii-hwpage-fetch-blocked"),
            (B.check_hwpage_unguarded_still_blocked, copy.deepcopy(hw),
             "tii-hwpage-fetch-blocked"),
        ]

    def test_exit_zero_passes(self):
        for fn, row, mode in self._classifiers():
            with self.subTest(fn=fn.__name__):
                fn(copy.deepcopy(row), mode)  # must not raise

    def test_exit_missing_rejected(self):
        for fn, row, mode in self._classifiers():
            with self.subTest(fn=fn.__name__):
                bad = copy.deepcopy(row)
                del bad["exit"]
                with self.assertRaises(ValueError):
                    fn(bad, mode)

    def test_exit_non_int_rejected(self):
        for bad in (None, True, False, "0", 0.0, [0]):
            for fn, row, mode in self._classifiers():
                with self.subTest(fn=fn.__name__, bad=bad):
                    broken = copy.deepcopy(row)
                    broken["exit"] = bad
                    with self.assertRaises(ValueError):
                        fn(broken, mode)

    def test_exit_nonzero_rejected(self):
        for bad in (1, -1, 8):
            for fn, row, mode in self._classifiers():
                with self.subTest(fn=fn.__name__, bad=bad):
                    broken = copy.deepcopy(row)
                    broken["exit"] = bad
                    with self.assertRaises(ValueError):
                        fn(broken, mode)


class MprTransitionTest(unittest.TestCase):
    """MEDIUM-2: all 8 MPRs validated; only the expected MPR2 change allowed."""

    def test_normal_before_after_accepted(self):
        for mode in B.ARMED_MODES:
            if mode in B.HWPAGE_MODES:
                continue
            with self.subTest(mode=mode):
                row = _armed_row(mode)
                if mode == "block-irq-handler":
                    row.update({"irq_accepted": True, "observed_vector": 0x3F80,
                                "ram_diff": [{"address": 0x21FF, "before": 0, "after": 1},
                                             {"address": 0x21FE, "before": 0, "after": 2},
                                             {"address": 0x21FD, "before": 0, "after": 3}]})
                    row["after"]["sp"] = 0xFC
                self.assertEqual(B.check_armed(row, mode), B.REFUSED_VERDICT)

    def test_expected_mpr2_with_other_change_rejected(self):
        cases = {
            "tia-wrap-forbidden": [0x00, 0xF8, 0xFF, 0x01, 0x00, 0x00, 0x00, 0x00],
            "tai-mpr2-unknown": [0x01, 0xF8, 0xF8, 0x00, 0x00, 0x00, 0x00, 0x00],
        }
        for mode, bad_after in cases.items():
            with self.subTest(mode=mode):
                row = _armed_row(mode)
                row["after"] = {"mpr": list(bad_after), "sp": 0xFF}
                with self.assertRaises(ValueError):
                    B.check_armed(row, mode)

    def test_wrong_mpr2_rejected(self):
        row = _armed_row("tia-wrap-forbidden")
        row["after"] = {"mpr": list(B.MPR_INITIAL), "sp": 0xFF}
        with self.assertRaises(ValueError):
            B.check_armed(row, "tia-wrap-forbidden")
        row = _armed_row("tai-mpr2-unknown")
        row["after"] = {"mpr": list(B.MPR_AFTER_TIA_WRAP), "sp": 0xFF}
        with self.assertRaises(ValueError):
            B.check_armed(row, "tai-mpr2-unknown")

    def test_malformed_mpr_rejected(self):
        for bad in (None, [0] * 7, [0] * 9, "not-a-list", [0x100] * 8, [None] * 8):
            row = _armed_row("tii-entry")
            row["after"] = {"mpr": bad, "sp": 0xFF}
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    B.check_armed(row, "tii-entry")

    def test_irq_mpr_change_rejected(self):
        row = _armed_row("block-irq-handler")
        row.update({"irq_accepted": True, "observed_vector": 0x3F80,
                    "ram_diff": [{"address": 0x21FF, "before": 0, "after": 1}],
                    "after": {"mpr": [0x01, 0xF8, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00],
                              "sp": 0xFC}})
        with self.assertRaises(ValueError):
            B.check_armed(row, "block-irq-handler")


class IntValidationTest(unittest.TestCase):
    """P1/P2/P4: missing/wrong-typed integers fail with a clear ValueError."""

    def test_armed_int_fields_strict(self):
        for name in ("refused_opcode", "fetch_opcode", "refused_pc", "stop_pc",
                     "instructions_executed", "executed_at_refusal",
                     "executed_after_resume", "resume_attempts", "resume_refused"):
            for bad in (None, "0", 0.0, True, False, [0]):
                with self.subTest(field=name, bad=bad):
                    row = _armed_row("tdd-unknown")
                    row[name] = bad
                    with self.assertRaises(ValueError) as ctx:
                        B.check_armed(row, "tdd-unknown")
                    self.assertIn("missing/non-integer", str(ctx.exception))

    def test_unguarded_executed_strict(self):
        for bad in (None, "1", 1.0, True, False):
            with self.subTest(bad=bad):
                row = _armed_row("tii-entry")
                row.update({"armed": False, "refused": False, "status": "ok", "reason": "",
                            "stop_pc": B.BLOCK_EXPECTED_PC["tii-entry"] + 7,
                            "instructions_executed": bad,
                            "ram_diff": [{"address": 0x3E90 + i, "before": 0xCC, "after": v}
                                         for i, v in enumerate(B.PATTERN)]})
                with self.assertRaises(ValueError):
                    B.check_unguarded_landed(row, "tii-entry")

    def test_resume_allowed_int_fields_strict(self):
        for name in ("refused_pc", "stop_pc", "resume_refused"):
            for bad in (None, "0", True):
                with self.subTest(field=name, bad=bad):
                    row = _armed_row("tdd-unknown")
                    row.update({"armed": False, "refused": True, "status": "ok", "reason": "",
                                "stop_pc": 0x3E17, "resume_refused": 0,
                                "instructions_executed": B.BLOCK_EXECUTED_BEFORE_REFUSAL["tdd-unknown"] + 2,
                                "ram_diff": [{"address": 0x3E90, "before": 0xCC, "after": 0x60}],
                                "ram_unchanged_during_resume": False})
                    row[name] = bad
                    with self.assertRaises(ValueError):
                        B.check_resume_allowed_landed(row, "tdd-unknown")

    def test_str_refused_pc_message_is_clear(self):
        row = _armed_row("tdd-unknown")
        row["refused_pc"] = "0x3E10"
        with self.assertRaises(ValueError) as ctx:
            B.check_armed(row, "tdd-unknown")
        self.assertIn("missing/non-integer", str(ctx.exception))

    def test_non_handler_steps_nonzero_rejected(self):
        row = _armed_row("tdd-unknown")
        row["handler_steps"] = 2
        with self.assertRaises(ValueError):
            B.check_armed(row, "tdd-unknown")

    def test_non_handler_steps_missing_rejected(self):
        row = _armed_row("tdd-unknown")
        del row["handler_steps"]
        with self.assertRaises(ValueError):
            B.check_armed(row, "tdd-unknown")


class HwpageUnguardedStrictTest(unittest.TestCase):
    """P3: the unguarded hwpage fetch is pinned like the armed one."""

    def _unguarded_hwpage(self):
        row = _armed_row("tii-entry")
        row.update({"mode": "tii-hwpage-fetch-blocked", "status": "fetch_blocked",
                    "refused": False, "fetch_blocked": True, "stop_pc": 0x4000,
                    "instructions_executed": 3, "fetch_safe": False, "fetch_failed": True,
                    "vdc_ar_before": 5, "vdc_ar_after": 5,
                    "vdc_sr_before": 0x3F, "vdc_sr_after": 0x3F,
                    "vdc_read_buffer_before": 0xBEEF, "vdc_read_buffer_after": 0xBEEF,
                    "resume_attempts": 3, "resume_refused": 3,
                    "after": {"mpr": list(B.MPR_AFTER_TIA_WRAP), "sp": 0xFF}})
        return row

    def test_valid_still_blocked(self):
        self.assertEqual(B.check_hwpage_unguarded_still_blocked(
            self._unguarded_hwpage(), "tii-hwpage-fetch-blocked"), B.HWPAGE_BLOCKED_VERDICT)

    def test_wrong_stop_pc_rejected(self):
        row = self._unguarded_hwpage()
        row["stop_pc"] = 0x4002
        with self.assertRaises(ValueError):
            B.check_hwpage_unguarded_still_blocked(row, "tii-hwpage-fetch-blocked")

    def test_wrong_executed_rejected(self):
        row = self._unguarded_hwpage()
        row["instructions_executed"] = 4
        with self.assertRaises(ValueError):
            B.check_hwpage_unguarded_still_blocked(row, "tii-hwpage-fetch-blocked")

    def test_stop_pc_missing_rejected(self):
        row = self._unguarded_hwpage()
        del row["stop_pc"]
        with self.assertRaises(ValueError):
            B.check_hwpage_unguarded_still_blocked(row, "tii-hwpage-fetch-blocked")

    def test_mpr_change_rejected(self):
        row = self._unguarded_hwpage()
        row["after"] = {"mpr": [0] * 8, "sp": 0xFF}
        with self.assertRaises(ValueError):
            B.check_hwpage_unguarded_still_blocked(row, "tii-hwpage-fetch-blocked")

    def test_vdc_pair_missing_rejected(self):
        row = self._unguarded_hwpage()
        del row["vdc_ar_before"]
        del row["vdc_ar_after"]
        with self.assertRaises(ValueError):
            B.check_hwpage_unguarded_still_blocked(row, "tii-hwpage-fetch-blocked")

    def test_resume_not_refused_rejected(self):
        row = self._unguarded_hwpage()
        row["resume_refused"] = 0
        with self.assertRaises(ValueError):
            B.check_hwpage_unguarded_still_blocked(row, "tii-hwpage-fetch-blocked")

    def test_fetch_flags_rejected(self):
        row = self._unguarded_hwpage()
        row["fetch_safe"] = True
        with self.assertRaises(ValueError):
            B.check_hwpage_unguarded_still_blocked(row, "tii-hwpage-fetch-blocked")


if __name__ == "__main__":
    unittest.main()
