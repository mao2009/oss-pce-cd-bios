"""Fail-closed verdict and validator tests for the compatibility evidence schema."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
from compat_evidence import SCHEMA, main, validate, verdict

# Probe label each upper stage must reach (pinned here; the runner passes it as --expect).
STAGE_LABEL = {'retail-boot': 'retail-boot-reached', 'playable': 'playable-reached',
               'save-tested': 'save-reached', 'completion-tested': 'completion-reached'}
REAL = {'kind': 'real-core', 'name': 'Geargrafx', 'version': '1.0', 'commit': 'b' * 40}
SYNTH = {'id': 'synthetic-cd-001', 'sha256': 'c' * 64, 'origin': 'original-synthetic'}
LOCAL = {'id': 'local-retail-001', 'sha256': 'd' * 64, 'origin': 'local-proprietary'}
CHECK = [{'name': 'cd-ident', 'expected': 'ok', 'observed': 'ok'}]
TRACE = [{'name': 'irq-vector', 'delta': 1}]


def attest(rec, **overrides):
    """Runner attestation that matches the record. Stands in for the harness output."""
    fixture = rec.get('fixture') or {}
    emulator = rec.get('emulator') or {}
    att = {'verified': True, 'rom_sha256': (rec.get('rom') or {}).get('sha256'),
           'fixture_sha256': fixture.get('sha256'), 'emulator_commit': emulator.get('commit'),
           'checkpoints': [{'name': c['name'], 'observed': c['expected']}
                           for c in rec.get('checkpoints') or []]}
    att.update(overrides)
    return att


def run(rec):
    return verdict(rec, attest(rec))


def record(stage='homebrew', **overrides):
    base = {'schema': SCHEMA, 'stage': stage, 'result': 'PASS', 'claim_scope': 'single-launch',
            'rom': {'sha256': 'a' * 64}, 'emulator': dict(REAL), 'fixture': dict(SYNTH),
            'region': None, 'checkpoints': copy.deepcopy(CHECK), 'trace_deltas': [],
            'blocker': None}
    base.update(overrides)
    return base


class VerdictPositiveTests(unittest.TestCase):
    def test_homebrew_with_real_core_and_synthetic_fixture_passes(self):
        self.assertEqual(run(record())['result'], 'PASS')

    def test_source_only_needs_no_emulator_or_fixture(self):
        result = run(record('source-only', rom=None, emulator=None, fixture=None,
                                checkpoints=[]))
        self.assertEqual(result['result'], 'PASS')

    def test_rom_built_boundary_below_core_stage_passes_without_emulator(self):
        result = run(record('rom-built', emulator=None, fixture=None, checkpoints=[]))
        self.assertEqual(result['result'], 'PASS')

    def test_retail_boot_with_local_fixture_region_and_trace_passes(self):
        result = run_with(staged('retail-boot'), stage_ev('retail-boot'))
        self.assertEqual(result['result'], 'PASS')


class VerdictNegativeTests(unittest.TestCase):
    def test_mock_emulator_cannot_pass_bios_loaded(self):
        result = run(record('bios-loaded', emulator={'kind': 'mock', 'name': 'stub'},
                                checkpoints=[]))
        self.assertNotEqual(result['result'], 'PASS')

    def test_mock_response_claiming_homebrew_pass_is_overclaim_fail(self):
        result = run(record(emulator={'kind': 'mock', 'name': 'stub'}))
        self.assertEqual(result['result'], 'FAIL')
        self.assertTrue(any('overclaim' in reason for reason in result['reasons']))

    def test_static_emulator_cannot_pass_startup(self):
        result = run(record('startup', emulator={'kind': 'static'}))
        self.assertNotEqual(result['result'], 'PASS')

    def test_mock_source_marked_real_is_rejected(self):
        bad = record(emulator=dict(REAL, name='MockGeargrafx'))
        self.assertIn('mock/static source marked as real-core', validate(bad))
        self.assertEqual(run(bad)['result'], 'FAIL')

    def test_missing_fixture_yields_blocked_not_pass(self):
        result = run(record(fixture=None, result='BLOCKED', blocker='fixture missing'))
        self.assertEqual(result['result'], 'BLOCKED')
        self.assertEqual(result['blocker'], 'fixture missing')

    def test_missing_local_fixture_at_retail_yields_skip(self):
        result = run(record('retail-boot', fixture=None, region='JP', result='SKIP',
                                blocker='fixture missing', trace_deltas=copy.deepcopy(TRACE)))
        self.assertEqual(result['result'], 'SKIP')

    def test_missing_fixture_hash_is_rejected(self):
        bad = record(fixture={'id': 'synthetic-cd-001', 'origin': 'original-synthetic'})
        self.assertEqual(run(bad)['result'], 'FAIL')

    def test_missing_rom_hash_is_rejected(self):
        self.assertEqual(run(record(rom={}))['result'], 'FAIL')

    def test_missing_rom_yields_blocked(self):
        result = run(record('rom-built', rom=None, result='BLOCKED', blocker='ROM not built'))
        self.assertEqual(result['result'], 'BLOCKED')

    def test_blocked_claim_with_missing_fixture_is_not_overclaim(self):
        result = run(record(fixture=None, result='BLOCKED', blocker='fixture missing'))
        self.assertFalse(any('overclaim' in reason for reason in result['reasons']))

    def test_overclaimed_playable_with_synthetic_fixture_fails(self):
        result = run(record('playable', trace_deltas=copy.deepcopy(TRACE)))
        self.assertEqual(result['result'], 'FAIL')

    def test_retail_boot_trace_deltas_alone_is_blocked(self):
        result = run(staged('retail-boot', result='BLOCKED', blocker='stage evidence'))
        self.assertEqual(result['result'], 'BLOCKED')

    def test_retail_boot_with_synthetic_fixture_fails(self):
        result = run(record('retail-boot', region='JP', trace_deltas=copy.deepcopy(TRACE)))
        self.assertEqual(result['result'], 'FAIL')

    def test_all_titles_claim_from_single_launch_fails(self):
        bad = record(claim_scope='all-titles')
        self.assertEqual(run(bad)['result'], 'FAIL')

    def test_checkpoint_mismatch_fails(self):
        bad = record(checkpoints=[{'name': 'cd-ident', 'expected': 'ok', 'observed': 'err'}])
        self.assertEqual(run(bad)['result'], 'FAIL')

    def test_unknown_schema_version_fails(self):
        self.assertEqual(run(record(schema='pce-compat-evidence-v0'))['result'], 'FAIL')

    def test_blocked_claim_without_blocker_is_rejected(self):
        self.assertEqual(run(record(result='BLOCKED', blocker=None))['result'], 'FAIL')

    def test_trace_delta_without_name_is_rejected(self):
        self.assertEqual(run(record(trace_deltas=[{'delta': 1}]))['result'], 'FAIL')


class VerdictBoundaryTests(unittest.TestCase):
    def test_startup_without_checkpoints_is_blocked(self):
        result = run(record('startup', checkpoints=[], result='BLOCKED',
                                blocker='no checkpoints observed'))
        self.assertEqual(result['result'], 'BLOCKED')

    def test_startup_boundary_with_one_matching_checkpoint_passes(self):
        self.assertEqual(run(record('startup'))['result'], 'PASS')

    def test_homebrew_is_last_stage_without_trace_deltas(self):
        self.assertEqual(run(record('homebrew', trace_deltas=[]))['result'], 'PASS')

    def test_playable_needs_trace_deltas(self):
        result = run(record('playable', fixture=dict(LOCAL), region='JP', trace_deltas=[],
                                result='BLOCKED', blocker='guest trace deltas required above homebrew'))
        self.assertEqual(result['result'], 'BLOCKED')

    def test_retail_boot_without_region_is_blocked(self):
        result = run(record('retail-boot', fixture=dict(LOCAL), trace_deltas=copy.deepcopy(TRACE),
                                result='BLOCKED', blocker='region unknown'))
        self.assertEqual(result['result'], 'BLOCKED')

    def test_no_stage_above_homebrew_passes_on_synthetic_evidence(self):
        for stage in ('retail-boot', 'playable', 'save-tested', 'completion-tested'):
            with self.subTest(stage=stage):
                result = run(record(stage, trace_deltas=copy.deepcopy(TRACE), region='JP'))
                self.assertNotEqual(result['result'], 'PASS')


class RunnerAttestationTests(unittest.TestCase):
    def test_forged_real_core_record_without_runner_is_not_pass(self):
        result = verdict(record())
        self.assertEqual(result['result'], 'FAIL')
        self.assertIn('independent runner attestation required', result['reasons'])

    def test_self_declared_runner_field_in_record_is_ignored(self):
        forged = record(runner=attest(record()))
        self.assertNotEqual(verdict(forged)['result'], 'PASS')

    def test_unverified_runner_yields_blocked(self):
        result = verdict(record(result='BLOCKED', blocker='x'), attest(record(), verified=False))
        self.assertEqual(result['result'], 'BLOCKED')

    def test_runner_with_arbitrary_rom_hash_fails(self):
        self.assertEqual(verdict(record(), attest(record(), rom_sha256='e' * 64))['result'], 'FAIL')

    def test_runner_with_other_emulator_commit_fails(self):
        self.assertEqual(verdict(record(), attest(record(), emulator_commit='f' * 40))['result'], 'FAIL')

    def test_runner_with_other_fixture_hash_fails(self):
        self.assertEqual(verdict(record(), attest(record(), fixture_sha256='e' * 64))['result'], 'FAIL')

    def test_checkpoint_not_confirmed_by_runner_fails(self):
        self.assertEqual(verdict(record(), attest(record(), checkpoints=[]))['result'], 'FAIL')

    def test_source_only_needs_no_runner(self):
        self.assertEqual(verdict(record('source-only', rom=None, emulator=None, fixture=None,
                                        checkpoints=[]))['result'], 'PASS')


def cycle(name, **overrides):
    """One RUN record as the #119 runner summarises it (cold or warm)."""
    run = {'cycle': name, 'reason': STAGE_LABEL['playable'], 'step': 1000, 'halt_confirmed': True,
           'halt_confirmation_steps': 64, 'halt_confirmation_required': 64, 'halted_after_step': 936,
           'hits_before_run': 1, 'hits': 1}
    run.update(overrides)
    return run


def stage_ev(stage='playable', **overrides):
    """Stage-specific runner evidence: the classify() rules of #119 in summary form."""
    ev = {'stage': stage, 'expect': STAGE_LABEL[stage], 'probe_kind': 'cdread', 'exit_code': 0,
          'timed_out': False, 'output_capped': False, 'budget': 12_000_000,
          'cap': {'instruction_step': True, 'cdrom_hardware': True, 'cdrom_media': True,
                  'disc_given': True},
          'forbid_hits': [],
          'runs': [cycle('cold', reason=STAGE_LABEL[stage]), cycle('warm', reason=STAGE_LABEL[stage])]}
    ev.update(overrides)
    return ev


def staged(stage='playable', **overrides):
    """Record for a stage above homebrew whose checkpoint is the stage label."""
    checks = [{'name': STAGE_LABEL[stage], 'expected': STAGE_LABEL[stage],
               'observed': STAGE_LABEL[stage]}]
    return record(stage, fixture=dict(LOCAL), region='JP', trace_deltas=copy.deepcopy(TRACE),
                  checkpoints=checks, **overrides)


def run_with(rec, evidence):
    """Verdict with the attestation plus stage evidence supplied by the runner."""
    return verdict(rec, attest(rec, stage_evidence=evidence))


class StageEvidenceTests(unittest.TestCase):
    """playable / save-tested / completion-tested need runner-verified stage evidence."""

    def test_self_declared_trace_deltas_alone_do_not_pass_upper_stages(self):
        for stage in ('retail-boot', 'playable', 'save-tested', 'completion-tested'):
            with self.subTest(stage=stage):
                result = run(staged(stage, result='BLOCKED', blocker='stage evidence'))
                self.assertEqual(result['result'], 'BLOCKED')

    def test_stage_evidence_in_record_is_ignored(self):
        forged = staged('playable', result='BLOCKED', blocker='stage evidence',
                        stage_evidence=stage_ev('playable'))
        self.assertEqual(run(forged)['result'], 'BLOCKED')

    def test_valid_runner_stage_evidence_passes_each_upper_stage(self):
        for stage in ('retail-boot', 'playable', 'save-tested', 'completion-tested'):
            with self.subTest(stage=stage):
                self.assertEqual(run_with(staged(stage), stage_ev(stage))['result'], 'PASS')

    def test_stage_evidence_for_another_stage_fails(self):
        self.assertEqual(run_with(staged('playable'), stage_ev('save-tested'))['result'], 'FAIL')

    def test_probe_kind_api_is_not_supported_and_blocked(self):
        # #119 classify(): kind without a RUN/HIT judge is NOT_SUPPORTED, which is BLOCKED.
        blocked = staged(result='BLOCKED', blocker='NOT_SUPPORTED')
        self.assertEqual(run_with(blocked, stage_ev(probe_kind='api'))['result'], 'BLOCKED')

    def test_probe_exit_6_debugger_missing_is_blocked_not_fail(self):
        # #119 PROBE_EXIT: 6 = instruction-step debugger capability missing (BLOCKED).
        blocked = staged(result='BLOCKED', blocker='debugger')
        self.assertEqual(run_with(blocked, stage_ev(exit_code=6))['result'], 'BLOCKED')

    def test_forbidden_label_reached_fails(self):
        self.assertEqual(run_with(staged(), stage_ev(forbid_hits=['cd_halt_fail']))['result'], 'FAIL')

    def test_missing_forbid_evidence_is_blocked(self):
        ev = stage_ev()
        del ev['forbid_hits']
        blocked = staged(result='BLOCKED', blocker='forbid')
        self.assertEqual(run_with(blocked, ev)['result'], 'BLOCKED')

    def test_nonzero_exit_timeout_or_output_cap_fails(self):
        for bad in ({'exit_code': 1}, {'timed_out': True}, {'output_capped': True}):
            with self.subTest(bad=bad):
                self.assertEqual(run_with(staged(), stage_ev(**bad))['result'], 'FAIL')

    def test_missing_or_false_capability_is_blocked(self):
        missing = stage_ev(cap={'instruction_step': True, 'cdrom_hardware': True, 'cdrom_media': True})
        blocked = staged(result='BLOCKED', blocker='capability')
        self.assertEqual(run_with(blocked, missing)['result'], 'BLOCKED')
        off = stage_ev(cap=dict(stage_ev()['cap'], disc_given=False))
        self.assertEqual(run_with(blocked, off)['result'], 'BLOCKED')

    def test_missing_warm_cycle_fails(self):
        ev = stage_ev(runs=[cycle('cold', reason=STAGE_LABEL['playable'])])
        self.assertEqual(run_with(staged(), ev)['result'], 'FAIL')

    def test_extra_run_with_other_reason_fails(self):
        for reason in ('budget', 'cd_halt_fail', 'cd_unexpected_interrupt'):
            with self.subTest(reason=reason):
                ev = stage_ev()
                ev['runs'].append(cycle('warm', reason=reason))
                self.assertEqual(run_with(staged(), ev)['result'], 'FAIL')

    def test_halt_not_confirmed_fails(self):
        ev = stage_ev(runs=[cycle('cold', halt_confirmed=False), cycle('warm')])
        self.assertEqual(run_with(staged(), ev)['result'], 'FAIL')

    def test_halt_confirmation_shorter_than_64_fails(self):
        ev = stage_ev(runs=[cycle('cold', halt_confirmation_steps=32), cycle('warm')])
        self.assertEqual(run_with(staged(), ev)['result'], 'FAIL')

    def test_success_hit_missing_or_after_run_fails(self):
        for hits in (0, 2):
            with self.subTest(hits=hits):
                ev = stage_ev(runs=[cycle('cold', hits_before_run=hits), cycle('warm')])
                self.assertEqual(run_with(staged(), ev)['result'], 'FAIL')

    def test_step_must_be_non_negative_int_within_budget(self):
        bad_steps = (-1, 1.5, True, 12_000_001)
        for step in bad_steps:
            with self.subTest(step=step):
                ev = stage_ev(runs=[cycle('cold', step=step), cycle('warm')])
                self.assertEqual(run_with(staged(), ev)['result'], 'FAIL')

    def test_expect_label_must_match_stage(self):
        self.assertEqual(run_with(staged(), stage_ev(expect='cd-ident'))['result'], 'FAIL')

    def test_stage_checkpoint_missing_from_record_is_blocked(self):
        rec = staged(result='BLOCKED', blocker='stage checkpoint missing')
        rec['checkpoints'] = copy.deepcopy(CHECK)
        self.assertEqual(run_with(rec, stage_ev())['result'], 'BLOCKED')


class CliTrustTests(unittest.TestCase):
    """The CLI never certifies local, unsigned JSON; --ci only runs inside GitHub Actions."""

    def _run_cli(self, argv, env):
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for name, value in (('record.json', record()), ('runner.json', attest(record()))):
                path = Path(tmp) / name
                path.write_text(json.dumps(value), encoding='utf-8')
                paths.append(str(path))
            out = io.StringIO()
            with mock.patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(out):
                code = main(['x', *paths, *argv])
        return code, out.getvalue()

    def test_local_untrusted_pass_is_downgraded_to_blocked(self):
        code, text = self._run_cli([], {})
        self.assertEqual(code, 1)
        output = json.loads(text)
        self.assertEqual(output['result'], 'BLOCKED')
        self.assertEqual(output['local_verdict'], 'PASS')
        self.assertIn('local-only', output['trust'])

    def test_ci_flag_outside_github_actions_is_refused(self):
        with contextlib.redirect_stderr(io.StringIO()):
            code, _ = self._run_cli(['--ci'], {})
        self.assertEqual(code, 2)

    def test_ci_flag_inside_github_actions_accepts_pass(self):
        code, text = self._run_cli(['--ci'], {'GITHUB_ACTIONS': 'true'})
        self.assertEqual(code, 0)
        output = json.loads(text)
        self.assertEqual(output['result'], 'PASS')
        self.assertTrue(output['trust'].startswith('ci-job'))
        self.assertIn('not verified', output['authenticity'])

    def test_output_never_claims_authenticity(self):
        for argv, env in (([], {}), (['--ci'], {'GITHUB_ACTIONS': 'true'})):
            with self.subTest(argv=argv):
                _, text = self._run_cli(argv, env)
                self.assertIn('not verified', json.loads(text)['authenticity'])


if __name__ == '__main__':
    unittest.main()
