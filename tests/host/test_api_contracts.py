"""Synthetic negative tests for the implemented-slot contract checker (slot $1E EX_GETVER).

These feed hand-built record dicts to check_implemented_slot(), the same function the real-core
Geargrafx run uses. They prove the P bitmask (Z=1, N=0, I=1) and carry-preservation checks fail
closed; they are NOT real-core measurements. The real-core positive run lives in
tests/integration/geargrafx_contracts.py.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'tests' / 'integration'))

from diagnostics import MPR  # noqa: E402
from geargrafx_contracts import (FLAG_CARRY, FLAG_INTERRUPT, FLAG_NEGATIVE, FLAG_ZERO,  # noqa: E402
                                 IMPLEMENTED_SLOTS, check_implemented_slot)

SLOT = 0x1E
RETURN_PC = 0x9003  # symbols['api_call_return'] + 3 (the self-loop after the caller's STA)
# Synthetic checker inputs, not real-core measurements: a cold caller P with C=0 and a warm one
# with C=1, so the carry-in==carry-out check is exercised with both carry values.
CALLER_P = {'cold': 0x06, 'warm': 0x07}


def records(cold_p=0x06, warm_p=0x07, ram_a=0x11, drop_return=False, pc=RETURN_PC):
    """Build the minimal OBS/TRACE shape that execute() produces for an api_jsr run."""
    ram = bytearray(0x40)
    ram[0x11] = ram_a
    row = lambda cycle, p: {'phase': 'cold', 'cycle': cycle, 'pc': pc, 'a': 0x11, 'x': 3, 'y': 0,
                            'p': p, 'sp': 255, 'mpr': MPR, 'work_ram_0200_hex': ram.hex()}
    traces = [{'cycle': 'cold', 'phase': 'api_jsr', 'p': CALLER_P['cold']},
              {'cycle': 'warm', 'phase': 'api_jsr', 'p': CALLER_P['warm']}]
    if not drop_return:
        traces += [{'cycle': 'cold', 'phase': 'api_call_return', 'p': cold_p},
                   {'cycle': 'warm', 'phase': 'api_call_return', 'p': warm_p}]
    return {'OBS': [row('cold', 0x02), row('cold', cold_p), row('cold', cold_p), row('warm', warm_p)],
            'TRACE': traces}


class ImplementedSlotContractTests(unittest.TestCase):
    SYMBOLS = {'api_call_return': RETURN_PC - 3}
    EXPECTED = IMPLEMENTED_SLOTS[SLOT]

    def assert_rejected(self, message, **kwargs):
        with self.assertRaisesRegex(ValueError, message):
            check_implemented_slot(records(**kwargs), self.SYMBOLS, SLOT, self.EXPECTED)

    def test_baseline_is_accepted(self):
        check_implemented_slot(records(), self.SYMBOLS, SLOT, self.EXPECTED)

    def test_z_clear_is_rejected(self):
        self.assert_rejected('requires Z=1', cold_p=0x06 & ~FLAG_ZERO)

    def test_n_set_is_rejected(self):
        self.assert_rejected('requires N=0', cold_p=0x06 | FLAG_NEGATIVE)

    def test_i_clear_is_rejected(self):
        self.assert_rejected('requires I=1', cold_p=0x06 & ~FLAG_INTERRUPT)

    def test_carry_not_preserved_is_rejected(self):
        # warm caller P has C=1 but the synthetic callee return has C=0 (both here are synthetic).
        self.assert_rejected('did not preserve carry', warm_p=0x06)

    def test_wrong_x_is_rejected(self):
        data = records()
        data['OBS'][1]['x'] = 4
        with self.assertRaisesRegex(ValueError, 'returned'):
            check_implemented_slot(data, self.SYMBOLS, SLOT, self.EXPECTED)

    def test_wrong_y_is_rejected(self):
        data = records()
        data['OBS'][1]['y'] = 1
        with self.assertRaisesRegex(ValueError, 'returned'):
            check_implemented_slot(data, self.SYMBOLS, SLOT, self.EXPECTED)

    def test_changed_a_is_rejected(self):
        data = records()
        data['OBS'][1]['a'] = 0x12
        with self.assertRaisesRegex(ValueError, 'returned'):
            check_implemented_slot(data, self.SYMBOLS, SLOT, self.EXPECTED)

    def test_bad_stack_pointer_is_rejected(self):
        data = records()
        data['OBS'][1]['sp'] = 253
        with self.assertRaisesRegex(ValueError, 'returned'):
            check_implemented_slot(data, self.SYMBOLS, SLOT, self.EXPECTED)

    def test_accumulator_not_preserved_in_ram_is_rejected(self):
        data = records(ram_a=0x12)
        with self.assertRaisesRegex(ValueError, 'preserve the caller accumulator'):
            check_implemented_slot(data, self.SYMBOLS, SLOT, self.EXPECTED)

    def test_no_return_to_caller_is_rejected(self):
        self.assert_rejected('did not return', drop_return=True)

    def test_wrong_return_address_is_rejected(self):
        self.assert_rejected('continuation', pc=RETURN_PC + 1)

    def test_incomplete_observation_set_cannot_pass(self):
        """The contract must fail closed if the loader returns too few observations: an empty
        or loaded-only OBS list must not be accepted just because it has nothing to contradict."""
        for obs in ([], [{'phase': 'loaded', 'cycle': 'cold', 'pc': 0, 'a': 0, 'x': 0, 'y': 0,
                          'p': 0, 'sp': 0, 'mpr': MPR, 'work_ram_0200_hex': '00' * 0x40}]):
            with self.subTest(observations=len(obs)):
                data = records()
                data['OBS'] = obs
                with self.assertRaisesRegex(ValueError, 'incomplete'):
                    check_implemented_slot(data, self.SYMBOLS, SLOT, self.EXPECTED)


if __name__ == '__main__':
    unittest.main()
