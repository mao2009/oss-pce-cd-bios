"""Synthetic tests for tools/cd_read_v16_contract.py (CD_READ reference-ABI preparation, Refs #188 #189).
No ROM, emulator, media or BIOS. Synthetic values only. A synthetic PASS is not BIOS ABI evidence."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import cd_read_v16_contract as v  # noqa: E402
import cd_read_error_verdict as ev  # noqa: E402

SEC = v.SECTOR_BYTES
REF5 = v.reference_sector(5)
MPR = [0xF8, 0xFF, 0x00, 0x01, 0x02, 0x03, 0x04, 0x05]
CASE = v.SYNTHETIC_CASES['S1_LBA15_IN_RANGE']
OUT = v.SYNTHETIC_CASES['S2_LBA16_OUT_OF_RANGE']


def synth(case_name, **over):
    """Synthetic observation for a shipped case; `over` replaces fields."""
    case = v.SYNTHETIC_CASES[case_name]
    span = v.span_bytes(case)
    if case['media_sectors'] > case['lba']:
        after = v.reference_sector(case['lba'])
    else:
        after = bytes(span)
    obs = {'kind': v.SYNTHETIC, 'monitor_ok': True, 'delivered': 1 if case['media_sectors'] > case['lba'] else 0,
           'dest_before': bytes(span), 'dest_after': after, 'outside_writes': 0,
           'mpr': MPR, 'stack': 0xFD, 'irq': False}
    obs.update(over)
    return obs


class Reference(unittest.TestCase):
    def test_reference_sector_is_2048_bytes_deterministic_with_known_prefix(self):
        self.assertEqual(len(REF5), SEC)
        self.assertEqual(REF5, v.reference_sector(5))
        self.assertEqual(v.reference_sector(0)[:4], bytes([0, 7, 14, 21]))

    def test_reference_sectors_differ_between_lbas(self):
        self.assertNotEqual(v.reference_sector(5), v.reference_sector(6))


class FullComparison(unittest.TestCase):
    def test_identical_sector_passes(self):
        self.assertEqual(v.compare_sector(REF5, bytes(REF5))['verdict'], v.PASS)

    def test_last_byte_difference_is_detected(self):
        bad = bytearray(REF5)
        bad[SEC - 1] ^= 0x01
        r = v.compare_sector(REF5, bytes(bad))
        self.assertEqual((r['verdict'], r['first'], r['mismatches']), (v.FAIL, SEC - 1, 1))

    def test_first_byte_difference_is_detected(self):
        bad = bytearray(REF5)
        bad[0] ^= 0x80
        self.assertEqual(v.compare_sector(REF5, bytes(bad))['first'], 0)

    def test_middle_byte_difference_is_detected(self):
        bad = bytearray(REF5)
        bad[1000] ^= 0x10
        self.assertEqual(v.compare_sector(REF5, bytes(bad))['verdict'], v.FAIL)

    def test_short_long_or_non_bytes_observation_is_blocked_not_pass(self):
        for bad in (REF5[:-1], REF5 + b'\x00', b'', 'x' * SEC, None, [0] * SEC):
            self.assertEqual(v.compare_sector(REF5, bad)['verdict'], v.BLOCKED, type(bad))

    def test_invalid_expected_is_blocked(self):
        self.assertEqual(v.compare_sector(REF5[:-1], REF5)['verdict'], v.BLOCKED)


class InRangeSynthetic(unittest.TestCase):
    def test_good_in_range_transfer_has_pass_items_and_unknown_effects(self):
        overall, items = v.judge_synthetic(CASE, synth('S1_LBA15_IN_RANGE'))
        self.assertEqual((items['delivered'], items['bytes'], items['outside_writes']), (v.PASS,) * 3)
        self.assertEqual((items['mpr'], items['stack'], items['irq']), (v.UNKNOWN,) * 3)
        self.assertEqual(overall, v.UNKNOWN)

    def test_wrong_bytes_fail(self):
        bad = bytearray(v.reference_sector(15))
        bad[SEC - 1] ^= 0xFF
        self.assertEqual(v.judge_synthetic(CASE, synth('S1_LBA15_IN_RANGE', dest_after=bytes(bad)))[1]['bytes'],
                         v.FAIL)

    def test_wrong_sector_content_fails(self):
        obs = synth('S1_LBA15_IN_RANGE', dest_after=v.reference_sector(14))
        self.assertEqual(v.judge_synthetic(CASE, obs)[1]['bytes'], v.FAIL)

    def test_in_range_without_delivery_fails(self):
        _, items = v.judge_synthetic(CASE, synth('S1_LBA15_IN_RANGE', delivered=0))
        self.assertEqual(items['delivered'], v.FAIL)

    def test_no_overall_pass_is_possible_for_synthetic(self):
        for name, case in v.SYNTHETIC_CASES.items():
            self.assertNotEqual(v.judge_synthetic(case, synth(name))[0], v.PASS, name)


class OutOfRangeSynthetic(unittest.TestCase):
    def test_out_of_range_no_delivery_and_dest_unchanged_is_not_fail(self):
        _, items = v.judge_synthetic(OUT, synth('S2_LBA16_OUT_OF_RANGE'))
        self.assertEqual((items['delivered'], items['bytes']), (v.PASS, v.PASS))

    def test_out_of_range_delivering_a_sector_fails(self):
        _, items = v.judge_synthetic(OUT, synth('S2_LBA16_OUT_OF_RANGE', delivered=1))
        self.assertEqual(items['delivered'], v.FAIL)

    def test_out_of_range_dest_write_fails(self):
        obs = synth('S2_LBA16_OUT_OF_RANGE', dest_after=b'\x01' + bytes(v.span_bytes(OUT) - 1))
        self.assertEqual(v.judge_synthetic(OUT, obs)[1]['bytes'], v.FAIL)

    def test_lba_equal_to_media_size_is_out_of_range(self):
        case = {**CASE, 'lba': 16}
        obs = synth('S2_LBA16_OUT_OF_RANGE')
        self.assertEqual(v.judge_synthetic(case, obs)[1]['delivered'], v.PASS)

    def test_huge_lba_is_out_of_range_not_blocked(self):
        case = {**OUT, 'lba': (1 << 21) - 1}
        self.assertEqual(v.judge_synthetic(case, synth('S2_LBA16_OUT_OF_RANGE'))[1]['delivered'], v.PASS)


class CountBoundaries(unittest.TestCase):
    def test_only_count_1_is_in_scope(self):
        self.assertEqual(v.IN_SCOPE_COUNTS, (1,))

    def test_boundary_counts_are_unknown_never_pass(self):
        for count in (0, 2, 3, 4, 256):
            overall, items = v.judge_synthetic({**CASE, 'count': count}, synth('S1_LBA15_IN_RANGE'))
            self.assertEqual(overall, v.UNKNOWN, count)
            self.assertNotIn(v.PASS, items.values(), count)

    def test_count_bool_or_negative_is_blocked(self):
        for bad in (True, -1, '1', None):
            self.assertEqual(v.judge_synthetic({**CASE, 'count': bad}, synth('S1_LBA15_IN_RANGE'))[0], v.BLOCKED, bad)


class DestinationGeometry(unittest.TestCase):
    def test_fits_in_mpr3_window_edges(self):
        self.assertEqual(v.dest_span_ok(0x0000, 1), v.IN_WINDOW)
        self.assertEqual(v.dest_span_ok(0x1800, 1), v.IN_WINDOW)

    def test_crossing_window_is_unknown_not_pass(self):
        self.assertEqual(v.dest_span_ok(0x1801, 1), v.UNKNOWN)
        case = {**CASE, 'dest_offset': 0x1801}
        overall, _ = v.judge_synthetic(case, synth('S1_LBA15_IN_RANGE'))
        self.assertEqual(overall, v.UNKNOWN)

    def test_offset_outside_window_or_wrong_type_is_blocked(self):
        for off in (0x2000, -1, 0.5, True):
            self.assertEqual(v.dest_span_ok(off, 1), v.BLOCKED, off)

    def test_other_destination_mode_is_blocked(self):
        for mode in (0, 2, 6, 0xFE, 0xFF):
            self.assertEqual(v.judge_synthetic({**CASE, 'dest_mode': mode}, synth('S1_LBA15_IN_RANGE'))[0], v.BLOCKED)


class EvidenceSeparation(unittest.TestCase):
    def test_synthetic_observation_cannot_be_callee_evidence(self):
        self.assertEqual(v.judge_callee(ev.CASES['C1_OUT_OF_RANGE_LBA'], synth('S1_LBA15_IN_RANGE'))[0], v.BLOCKED)

    def test_synthetic_observation_with_callee_fields_is_blocked_by_kind_check(self):
        obs = {**synth('S1_LBA15_IN_RANGE'), 'reached': True, 'gate': 'ALLOWED', 'a': 0, 'x': 0, 'y': 0,
               'p': 0x24, 'ram_outside_dest': 0}
        self.assertEqual(v.judge_callee(ev.CASES['C2_COUNT_0'], obs)[0], v.BLOCKED)

    def test_callee_observation_cannot_be_synthetic_evidence(self):
        obs = {**synth('S1_LBA15_IN_RANGE'), 'kind': v.CALLEE}
        self.assertEqual(v.judge_synthetic(CASE, obs)[0], v.BLOCKED)

    def test_callee_gate_not_allowed_is_blocked(self):
        obs = {'kind': v.CALLEE, 'monitor_ok': True, 'reached': True, 'gate': 'NONE'}
        self.assertEqual(v.judge_callee(ev.CASES['C2_COUNT_0'], obs)[0], v.BLOCKED)

    def test_callee_allowed_with_shipped_unknown_expectations_is_unknown_not_pass(self):
        obs = {'kind': v.CALLEE, 'monitor_ok': True, 'reached': True, 'gate': 'ALLOWED', 'a': 0, 'x': 0, 'y': 0,
               'p': 0x24, 'mpr': MPR, 'stack': 0xFD, 'ram_outside_dest': 0}
        overall, _ = v.judge_callee(ev.CASES['C2_COUNT_0'], obs)
        self.assertEqual(overall, v.UNKNOWN)

    def test_callee_case_must_be_a_dict(self):
        self.assertEqual(v.judge_callee(None, {'kind': v.CALLEE})[0], v.BLOCKED)


class Monitoring(unittest.TestCase):
    def test_monitor_failure_blocks(self):
        self.assertEqual(v.judge_synthetic(CASE, synth('S1_LBA15_IN_RANGE', monitor_ok=False))[0], v.BLOCKED)

    def test_outside_write_fails(self):
        self.assertEqual(v.judge_synthetic(CASE, synth('S1_LBA15_IN_RANGE', outside_writes=1))[1]['outside_writes'],
                         v.FAIL)

    def test_missing_or_invalid_effects_block(self):
        for bad in ({'mpr': [0] * 7}, {'mpr': [256] * 8}, {'stack': 256}, {'irq': 1}):
            self.assertEqual(v.judge_synthetic(CASE, synth('S1_LBA15_IN_RANGE', **bad))[0], v.BLOCKED, bad)
        obs = synth('S1_LBA15_IN_RANGE')
        del obs['stack']
        self.assertEqual(v.judge_synthetic(CASE, obs)[0], v.BLOCKED)

    def test_non_dict_inputs_block_without_raising(self):
        self.assertEqual(v.judge_synthetic(None, synth('S1_LBA15_IN_RANGE'))[0], v.BLOCKED)
        self.assertEqual(v.judge_synthetic(CASE, None)[0], v.BLOCKED)


class ShippedCases(unittest.TestCase):
    def test_no_shipped_case_is_pass_overall_with_any_observation(self):
        for name, case in v.SYNTHETIC_CASES.items():
            for obs in (synth(name), synth(name, delivered=2), synth(name, outside_writes=3)):
                self.assertNotEqual(v.judge_synthetic(case, obs)[0], v.PASS, (name, obs.get('delivered')))

    def test_only_count_1_cases_are_shipped_as_in_scope(self):
        for name, case in v.SYNTHETIC_CASES.items():
            self.assertEqual(case['count'], 1, name)
            self.assertEqual(case['dest_mode'], v.DEST_MODE, name)


if __name__ == '__main__':
    unittest.main()
