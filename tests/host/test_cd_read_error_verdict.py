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


def without(mapping, key):
    return {k: val for k, val in mapping.items() if k != key}


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

    def test_no_shipped_case_can_pass(self):
        for name, case in v.CASES.items():
            for obs in (VALID, without(VALID, 'a'), {**VALID, 'mpr': [0] * 8}):
                self.assertNotEqual(v.judge(case, obs)[0], v.PASS, (name, obs))


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
        obs = without(VALID, 'x')
        self.assertEqual(v.judge(synthetic(C1, x=0x01), obs)[1]['x'], v.BLOCKED)

    def test_missing_expected_key_is_blocked_not_keyerror(self):
        # RED on the pre-fix code: case['expected'][item] raised KeyError for an absent item.
        case = {**C1, 'expected': without(C1['expected'], 'x')}
        overall, items = v.judge(case, VALID)
        self.assertEqual((overall, items['x']), (v.BLOCKED, v.BLOCKED))

    def test_missing_expected_table_is_blocked_not_keyerror(self):
        case = {k: val for k, val in C1.items() if k != 'expected'}
        self.assertEqual(v.judge(case, VALID)[0], v.BLOCKED)

    def test_executable_must_be_exactly_true(self):
        for value in ('yes', 1, 'True', None):
            self.assertEqual(v.judge({**C1, 'executable': value}, VALID)[0], v.BLOCKED, repr(value))
        self.assertEqual(v.judge({k: val for k, val in C1.items() if k != 'executable'}, VALID)[0], v.BLOCKED)

    def test_bool_is_not_int_in_observations_or_expectations(self):
        # True == 1 in Python: a bool observation must not match an int expectation, and vice versa.
        self.assertEqual(v.judge(synthetic(C2, a=1), {**VALID, 'a': True})[0], v.BLOCKED)
        self.assertEqual(v.judge(synthetic(C2, a=True), VALID)[0], v.BLOCKED)
        self.assertEqual(v.judge(synthetic(C2, ram_outside_dest=True), VALID)[0], v.BLOCKED)


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


class Precedence(unittest.TestCase):
    """judge() on real cases where FAIL, BLOCKED, UNKNOWN and PASS each appear; overall must be the max."""

    def test_fail_and_blocked_and_unknown_together_yield_fail(self):
        case = synthetic(C1, a=0x01, x=0x01)          # a: FAIL, x: BLOCKED (missing), y: UNKNOWN
        overall, items = v.judge(case, without(VALID, 'x'))
        self.assertEqual(items['a'], v.FAIL)
        self.assertEqual(items['x'], v.BLOCKED)
        self.assertEqual(items['y'], v.UNKNOWN)
        self.assertEqual(overall, v.FAIL)

    def test_blocked_and_unknown_together_yield_blocked(self):
        case = synthetic(C1, x=0x01)                  # x: BLOCKED (missing), others UNKNOWN
        overall, items = v.judge(case, without(VALID, 'x'))
        self.assertEqual(items['y'], v.UNKNOWN)
        self.assertEqual(overall, v.BLOCKED)

    def test_unknown_alone_yields_unknown(self):
        overall, items = v.judge(C1, VALID)
        self.assertEqual(set(items.values()), {v.UNKNOWN})
        self.assertEqual(overall, v.UNKNOWN)

    def test_all_known_match_yields_pass(self):
        case = synthetic(C2, **{item: VALID[item] for item in v.ITEMS})
        overall, items = v.judge(case, VALID)
        self.assertEqual(set(items.values()), {v.PASS})
        self.assertEqual(overall, v.PASS)

    def test_fail_outranks_blocked_regardless_of_item_order(self):
        # The FAIL item is last in ITEMS order; BLOCKED comes first. Result must still be FAIL.
        case = synthetic(C1, a=0x00, ram_outside_dest=0)   # a known, so its missing observation is BLOCKED
        overall, items = v.judge(case, {**without(VALID, 'a'), 'ram_outside_dest': 3})
        self.assertEqual((items['a'], items['ram_outside_dest']), (v.BLOCKED, v.FAIL))
        self.assertEqual(overall, v.FAIL)

    def test_early_gate_blocked_beats_item_fail(self):
        # Code order: monitor / reached / gate return BLOCKED before any item is judged, so a known FAIL is hidden.
        case = synthetic(C1, a=0x01)                     # a would be FAIL (observed 0x00)
        self.assertEqual(v.judge(case, {**VALID, 'reached': False})[0], v.BLOCKED)
        self.assertEqual(v.judge(case, {**VALID, 'gate': 'NONE'})[0], v.BLOCKED)
        self.assertEqual(v.judge(case, {**VALID, 'monitor_ok': False})[0], v.BLOCKED)
        self.assertEqual(v.judge(case, VALID)[0], v.FAIL)


class MissingObservation(unittest.TestCase):
    """Rule (doc section 3): a key missing while monitor_ok/reached/gate pass is 'not observed'.
    UNKNOWN expectation -> UNKNOWN (not BLOCKED). Known expectation -> BLOCKED. Never PASS."""

    def test_missing_unknown_item_with_monitor_ok_is_unknown_not_blocked(self):
        overall, items = v.judge(C1, without(VALID, 'a'))
        self.assertEqual(items['a'], v.UNKNOWN)
        self.assertEqual(overall, v.UNKNOWN)

    def test_all_keys_missing_with_monitor_ok_is_unknown(self):
        obs = {'monitor_ok': True, 'reached': True, 'gate': 'ALLOWED'}
        overall, items = v.judge(C2, obs)
        self.assertEqual(set(items.values()), {v.UNKNOWN})
        self.assertEqual(overall, v.UNKNOWN)


class InputSafety(unittest.TestCase):
    """Any non-conforming input yields BLOCKED: no exception, never PASS."""

    def test_case_not_a_dict_is_blocked(self):
        # RED on the pre-fix code: judge(None, ...) raised AttributeError on case.get().
        for bad in (None, [], 'C1', 1, True, ('executable',)):
            self.assertEqual(v.judge(bad, VALID)[0], v.BLOCKED, repr(bad))

    def test_non_dict_observation_is_blocked(self):
        for bad in (None, [], 'x', 1, True):
            self.assertEqual(v.judge(C1, bad)[0], v.BLOCKED, repr(bad))

    def test_non_conforming_expectation_values_are_blocked(self):
        for bad in ({'a': 256}, {'a': True}, {'a': '0'}, {'a': 'unknown'}, {'mpr': [0] * 7},
                    {'mpr': (0,) * 8}, {'ram_outside_dest': -1}, {'a': None}, {'a': [0]}):
            overall, _ = v.judge(synthetic(C2, **bad), VALID)
            self.assertEqual(overall, v.BLOCKED, bad)

    def test_non_conforming_observation_values_never_pass(self):
        known = synthetic(C2, **{item: VALID[item] for item in v.ITEMS})
        for bad in ({'a': '0'}, {'a': 0.0}, {'mpr': 'x'}, {'ram_outside_dest': '0'}, {'stack': None}):
            self.assertEqual(v.judge(known, {**VALID, **bad})[0], v.BLOCKED, bad)


if __name__ == '__main__':
    unittest.main()
