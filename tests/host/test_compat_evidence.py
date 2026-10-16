"""Fail-closed verdict and validator tests for the compatibility evidence schema."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
from compat_evidence import SCHEMA, validate, verdict

REAL = {'kind': 'real-core', 'name': 'Geargrafx', 'version': '1.0', 'commit': 'b' * 40}
SYNTH = {'id': 'synthetic-cd-001', 'sha256': 'c' * 64, 'origin': 'original-synthetic'}
LOCAL = {'id': 'local-retail-001', 'sha256': 'd' * 64, 'origin': 'local-proprietary'}
CHECK = [{'name': 'cd-ident', 'expected': 'ok', 'observed': 'ok'}]
TRACE = [{'name': 'irq-vector', 'delta': 1}]


def record(stage='homebrew', **overrides):
    base = {'schema': SCHEMA, 'stage': stage, 'result': 'PASS', 'claim_scope': 'single-launch',
            'rom': {'sha256': 'a' * 64}, 'emulator': dict(REAL), 'fixture': dict(SYNTH),
            'region': None, 'checkpoints': copy.deepcopy(CHECK), 'trace_deltas': [],
            'blocker': None}
    base.update(overrides)
    return base


class VerdictPositiveTests(unittest.TestCase):
    def test_homebrew_with_real_core_and_synthetic_fixture_passes(self):
        self.assertEqual(verdict(record())['result'], 'PASS')

    def test_source_only_needs_no_emulator_or_fixture(self):
        result = verdict(record('source-only', rom=None, emulator=None, fixture=None,
                                checkpoints=[]))
        self.assertEqual(result['result'], 'PASS')

    def test_rom_built_boundary_below_core_stage_passes_without_emulator(self):
        result = verdict(record('rom-built', emulator=None, fixture=None, checkpoints=[]))
        self.assertEqual(result['result'], 'PASS')

    def test_retail_boot_with_local_fixture_region_and_trace_passes(self):
        result = verdict(record('retail-boot', fixture=dict(LOCAL), region='JP',
                                trace_deltas=copy.deepcopy(TRACE)))
        self.assertEqual(result['result'], 'PASS')


class VerdictNegativeTests(unittest.TestCase):
    def test_mock_emulator_cannot_pass_bios_loaded(self):
        result = verdict(record('bios-loaded', emulator={'kind': 'mock', 'name': 'stub'},
                                checkpoints=[]))
        self.assertNotEqual(result['result'], 'PASS')

    def test_mock_response_claiming_homebrew_pass_is_overclaim_fail(self):
        result = verdict(record(emulator={'kind': 'mock', 'name': 'stub'}))
        self.assertEqual(result['result'], 'FAIL')
        self.assertTrue(any('overclaim' in reason for reason in result['reasons']))

    def test_static_emulator_cannot_pass_startup(self):
        result = verdict(record('startup', emulator={'kind': 'static'}))
        self.assertNotEqual(result['result'], 'PASS')

    def test_mock_source_marked_real_is_rejected(self):
        bad = record(emulator=dict(REAL, name='MockGeargrafx'))
        self.assertIn('mock/static source marked as real-core', validate(bad))
        self.assertEqual(verdict(bad)['result'], 'FAIL')

    def test_missing_fixture_yields_blocked_not_pass(self):
        result = verdict(record(fixture=None, result='BLOCKED', blocker='fixture missing'))
        self.assertEqual(result['result'], 'BLOCKED')
        self.assertEqual(result['blocker'], 'fixture missing')

    def test_missing_local_fixture_at_retail_yields_skip(self):
        result = verdict(record('retail-boot', fixture=None, region='JP', result='SKIP',
                                blocker='fixture missing', trace_deltas=copy.deepcopy(TRACE)))
        self.assertEqual(result['result'], 'SKIP')

    def test_missing_fixture_hash_is_rejected(self):
        bad = record(fixture={'id': 'synthetic-cd-001', 'origin': 'original-synthetic'})
        self.assertEqual(verdict(bad)['result'], 'FAIL')

    def test_missing_rom_hash_is_rejected(self):
        self.assertEqual(verdict(record(rom={}))['result'], 'FAIL')

    def test_missing_rom_yields_blocked(self):
        result = verdict(record('rom-built', rom=None, result='BLOCKED', blocker='ROM not built'))
        self.assertEqual(result['result'], 'BLOCKED')

    def test_blocked_claim_with_missing_fixture_is_not_overclaim(self):
        result = verdict(record(fixture=None, result='BLOCKED', blocker='fixture missing'))
        self.assertFalse(any('overclaim' in reason for reason in result['reasons']))

    def test_overclaimed_playable_with_synthetic_fixture_fails(self):
        result = verdict(record('playable', trace_deltas=copy.deepcopy(TRACE)))
        self.assertEqual(result['result'], 'FAIL')

    def test_retail_boot_with_synthetic_fixture_fails(self):
        result = verdict(record('retail-boot', region='JP', trace_deltas=copy.deepcopy(TRACE)))
        self.assertEqual(result['result'], 'FAIL')

    def test_all_titles_claim_from_single_launch_fails(self):
        bad = record(claim_scope='all-titles')
        self.assertEqual(verdict(bad)['result'], 'FAIL')

    def test_checkpoint_mismatch_fails(self):
        bad = record(checkpoints=[{'name': 'cd-ident', 'expected': 'ok', 'observed': 'err'}])
        self.assertEqual(verdict(bad)['result'], 'FAIL')

    def test_unknown_schema_version_fails(self):
        self.assertEqual(verdict(record(schema='pce-compat-evidence-v0'))['result'], 'FAIL')

    def test_blocked_claim_without_blocker_is_rejected(self):
        self.assertEqual(verdict(record(result='BLOCKED', blocker=None))['result'], 'FAIL')

    def test_trace_delta_without_name_is_rejected(self):
        self.assertEqual(verdict(record(trace_deltas=[{'delta': 1}]))['result'], 'FAIL')


class VerdictBoundaryTests(unittest.TestCase):
    def test_startup_without_checkpoints_is_blocked(self):
        result = verdict(record('startup', checkpoints=[], result='BLOCKED',
                                blocker='no checkpoints observed'))
        self.assertEqual(result['result'], 'BLOCKED')

    def test_startup_boundary_with_one_matching_checkpoint_passes(self):
        self.assertEqual(verdict(record('startup'))['result'], 'PASS')

    def test_homebrew_is_last_stage_without_trace_deltas(self):
        self.assertEqual(verdict(record('homebrew', trace_deltas=[]))['result'], 'PASS')

    def test_playable_needs_trace_deltas(self):
        result = verdict(record('playable', fixture=dict(LOCAL), region='JP', trace_deltas=[],
                                result='BLOCKED', blocker='guest trace deltas required above homebrew'))
        self.assertEqual(result['result'], 'BLOCKED')

    def test_retail_boot_without_region_is_blocked(self):
        result = verdict(record('retail-boot', fixture=dict(LOCAL), trace_deltas=copy.deepcopy(TRACE),
                                result='BLOCKED', blocker='region unknown'))
        self.assertEqual(result['result'], 'BLOCKED')

    def test_no_stage_above_homebrew_passes_on_synthetic_evidence(self):
        for stage in ('retail-boot', 'playable', 'save-tested', 'completion-tested'):
            with self.subTest(stage=stage):
                result = verdict(record(stage, trace_deltas=copy.deepcopy(TRACE), region='JP'))
                self.assertNotEqual(result['result'], 'PASS')


if __name__ == '__main__':
    unittest.main()
