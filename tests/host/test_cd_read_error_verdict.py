"""Synthetic tests for tools/cd_read_error_verdict.py (Issue #189). No ROM, emulator or media; synthetic values only."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import cd_read_error_verdict as v  # noqa: E402

VALID = {'monitor_ok': True, 'reached': True, 'gate': 'ALLOWED', 'a': 0x00, 'x': 0x01, 'y': 0x02, 'p': 0x24,
         'mpr': [0xF8, 0xFF, 0x00, 0x01, 0x02, 0x03, 0x04, 0x05], 'stack': 0xFD, 'ram_outside_dest': 0}
C1 = v.CASES['C1_OUT_OF_RANGE_LBA']
C2 = v.CASES['C2_COUNT_0']
C3 = v.CASES['C3_NO_MEDIA']


def synthetic(case, **known):
    """Copy of a case with synthetic known expectations (never shipped)."""
    return {**case, 'expected': {**case['expected'], **known}}


class ShippedCases(unittest.TestCase):
    def test_no_media_is_blocked_even_with_valid_observation(self):
        self.assertEqual(v.judge(C3, VALID)[0], v.BLOCKED)

    def test_all_valid_observation_on_unknown_expectations_is_unknown_not_pass(self):
        for case in (C1, C2):
            overall, items = v.judge(case, VALID)
            self.assertEqual(overall, v.UNKNOWN)
            self.assertNotIn(v.PASS, items.values())

    def test_shipped_expectations_are_all_unknown(self):
        for name, case in v.CASES.items():
            self.assertEqual(set(case['expected'].values()), {v.UNKNOWN}, name)


class BlockedPaths(unittest.TestCase):
    def test_monitor_failure_is_blocked(self):
        self.assertEqual(v.judge(synthetic(C1), {**VALID, 'monitor_ok': False})[0], v.BLOCKED)
        self.assertEqual(v.judge(synthetic(C1), None)[0], v.BLOCKED)

    def test_unreached_is_blocked_not_pass(self):
        self.assertEqual(v.judge(synthetic(C1), {**VALID, 'reached': False})[0], v.BLOCKED)

    def test_gate_not_allowed_is_blocked(self):
        self.assertEqual(v.judge(synthetic(C2), {**VALID, 'gate': 'NONE'})[0], v.BLOCKED)

    def test_invalid_values_are_blocked(self):
        for bad in ({'a': 256}, {'a': True}, {'a': -1}, {'mpr': [0] * 7}, {'ram_outside_dest': -2}):
            self.assertEqual(v.judge(synthetic(C1), {**VALID, **bad})[0], v.BLOCKED, bad)

    def test_missing_known_expectation_is_blocked(self):
        obs = {k: val for k, val in VALID.items() if k != 'x'}
        self.assertEqual(v.judge(synthetic(C1, x=0x01), obs)[1]['x'], v.BLOCKED)


class SyntheticVerdicts(unittest.TestCase):
    def test_known_match_on_every_item_is_pass(self):
        case = synthetic(C2, **{item: VALID[item] for item in v.ITEMS})
        self.assertEqual(v.judge(case, VALID)[0], v.PASS)

    def test_known_mismatch_is_fail_even_when_other_items_unknown(self):
        overall, items = v.judge(synthetic(C1, a=0x01), VALID)
        self.assertEqual((overall, items['a']), (v.FAIL, v.FAIL))
        self.assertEqual(items['x'], v.UNKNOWN)

    def test_ram_outside_destination_mismatch_is_fail(self):
        self.assertEqual(v.judge(synthetic(C2, ram_outside_dest=0), {**VALID, 'ram_outside_dest': 3})[0], v.FAIL)

    def test_fail_outranks_unknown_and_blocked_outranks_unknown(self):
        self.assertGreater(v.ORDER[v.FAIL], v.ORDER[v.BLOCKED])
        self.assertGreater(v.ORDER[v.BLOCKED], v.ORDER[v.UNKNOWN])
        self.assertGreater(v.ORDER[v.UNKNOWN], v.ORDER[v.PASS])


if __name__ == '__main__':
    unittest.main()
