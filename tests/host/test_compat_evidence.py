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
from compat_evidence import SCHEMA, STAGE_LABEL, main, validate, verdict

BUDGET = 12_000_000
LABEL = STAGE_LABEL['playable']
REAL ={'kind': 'real-core', 'name': 'Geargrafx', 'version': '1.0', 'commit': 'b' * 40}
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
    def test_homebrew_runner_only_is_not_pass_without_a_judge(self):
        # No #119 label exists for homebrew, so no probe can judge it: BLOCKED (reporter PASS -> overclaim FAIL).
        self.assertNotEqual(run(record())['result'], 'PASS')
        self.assertEqual(run(record('homebrew', result='BLOCKED', blocker='no judge'))['result'], 'BLOCKED')

    def test_source_only_needs_no_emulator_or_fixture(self):
        result = run(record('source-only', rom=None, emulator=None, fixture=None,
                                checkpoints=[]))
        self.assertEqual(result['result'], 'PASS')

    def test_rom_built_boundary_below_core_stage_passes_without_emulator(self):
        result = run(record('rom-built', emulator=None, fixture=None, checkpoints=[]))
        self.assertEqual(result['result'], 'PASS')

    def test_retail_boot_with_local_fixture_region_and_raw_probe_passes(self):
        result = run_with(staged('retail-boot'), probe_for('retail-boot'))
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

    def test_reporter_blocked_with_complete_evidence_is_not_silently_passed(self):
        result = run_with(staged(result='BLOCKED', blocker='reporter says blocked'), probe_for('playable'))
        self.assertEqual(result['result'], 'BLOCKED')
        self.assertTrue(any('conflict' in reason for reason in result['reasons']))

    def test_reporter_fail_with_complete_evidence_stays_fail(self):
        result = run_with(staged(result='FAIL'), probe_for('playable'))
        self.assertEqual(result['result'], 'FAIL')
        self.assertTrue(any('conflict' in reason for reason in result['reasons']))


class MalformedInputTests(unittest.TestCase):
    """Non-array fields give a FAIL finding, never a traceback."""

    def test_non_array_record_checkpoints_fail(self):
        result = verdict(record(checkpoints=5), attest(record()))
        self.assertEqual(result['result'], 'FAIL')
        self.assertIn('checkpoints must be an array', result['reasons'])

    def test_non_array_record_trace_deltas_fail(self):
        self.assertEqual(run(record(trace_deltas=5))['result'], 'FAIL')

    def test_non_array_runner_checkpoints_fail(self):
        result = verdict(record(), attest(record(), checkpoints=5))
        self.assertEqual(result['result'], 'FAIL')

    def test_fail_finding_with_blocked_finding_reports_no_blocker(self):
        # Overall FAIL (checkpoint mismatch) while a BLOCKED finding also exists (no ROM): no blocker reported.
        rec = record(rom=None, checkpoints=[{'name': 'cd-ident', 'expected': 'ok', 'observed': 'err'}])
        result = run(rec)
        self.assertEqual(result['result'], 'FAIL')
        self.assertIsNone(result['blocker'])

    def test_falsy_non_array_checkpoints_are_fail_not_absent(self):
        for value in (0, False, ''):
            with self.subTest(value=value):
                # Reporter BLOCKED so that only the malformed field can decide the result (not an overclaim).
                rec = record('startup', checkpoints=value, result='BLOCKED', blocker='x')
                self.assertEqual(run(rec)['result'], 'FAIL')
                self.assertEqual(verdict(record(), attest(record(), checkpoints=value))['result'], 'FAIL')

    def test_falsy_non_array_trace_deltas_are_fail_not_absent(self):
        for value in (0, False, ''):
            with self.subTest(value=value):
                rec = staged(result='BLOCKED', blocker='x')
                rec['trace_deltas'] = value
                self.assertEqual(run_with(rec, probe_for('playable'))['result'], 'FAIL')

    def test_duplicate_runner_checkpoint_names_fail(self):
        att = attest(record())
        att['checkpoints'] = att['checkpoints'] * 2
        self.assertEqual(verdict(record(), att)['result'], 'FAIL')

    def test_trace_delta_name_must_be_a_string(self):
        for bad_name in (5, ['x']):
            with self.subTest(name=bad_name):
                rec = staged()
                rec['trace_deltas'] = [{'name': bad_name, 'delta': 1}]
                self.assertEqual(run_with(rec, probe_for('playable'))['result'], 'FAIL')

    def test_non_string_checkpoint_name_fails(self):
        bad = record(checkpoints=[{'name': ['cd-ident'], 'expected': 'ok', 'observed': 'ok'}])
        self.assertEqual(run(bad)['result'], 'FAIL')


class VerdictBoundaryTests(unittest.TestCase):
    def test_startup_without_checkpoints_is_blocked(self):
        result = run(record('startup', checkpoints=[], result='BLOCKED',
                                blocker='no checkpoints observed'))
        self.assertEqual(result['result'], 'BLOCKED')

    def test_startup_runner_only_is_not_pass(self):
        self.assertNotEqual(run(record('startup'))['result'], 'PASS')

    def test_homebrew_without_trace_deltas_is_not_blocked_by_trace_rule(self):
        result = run(record('homebrew', trace_deltas=[]))
        self.assertNotIn('guest trace deltas required above homebrew', result['reasons'])

    def test_runner_only_bios_loaded_and_other_judgeless_stages_are_blocked_not_pass(self):
        # Attestation plus matching checkpoints is not evidence of execution: no #119 probe can judge these.
        for stage in ('bios-loaded', 'startup', 'cd-detected', 'homebrew'):
            with self.subTest(stage=stage):
                self.assertEqual(run(record(stage, result='BLOCKED', blocker='no judge'))['result'], 'BLOCKED')
                self.assertNotEqual(run(record(stage))['result'], 'PASS')

    def test_rom_built_build_only_stage_needs_no_probe(self):
        self.assertEqual(run(record('rom-built', emulator=None, fixture=None, checkpoints=[]))['result'], 'PASS')

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


def run_rec(cycle, label=None, **overrides):
    """One RUN record as #119 trace emits it (cold or warm)."""
    label = label or STAGE_LABEL['playable']
    rec = {'tag': 'RUN', 'name': label, 'reason': label, 'cycle': cycle, 'pc': 1, 'step': 1000,
           'halt_confirmed': True, 'halt_confirmation_steps': 64, 'halt_confirmation_required': 64,
           'halted_after_step': 936}
    rec.update(overrides)
    return rec


def good_events(label):
    """CAP, then one success HIT and one RUN per cycle (cold, warm): the #119 PASS shape."""
    cap = {'tag': 'CAP', 'instruction_step': True, 'cdrom_hardware': True, 'cdrom_media': True,
           'disc_given': True}
    events = [cap]
    for cycle in ('cold', 'warm'):
        events += [{'tag': 'HIT', 'name': label, 'cycle': cycle}, run_rec(cycle, label)]
    return events


def to_stdout(events):
    """Raw probe stdout: one 'TAG {json}' line per record, as #119 parse_records() reads it."""
    return '\n'.join(f"{e['tag']} {json.dumps({k: v for k, v in e.items() if k != 'tag'})}" for e in events)


def probe_for(stage, events=None, *, exit_code=0, status=None, reason=None, forbid=('cd_halt_fail',),
              budget=BUDGET, kind='cdread', stdout=None):
    """Raw #119 probe output for a stage: the job result plus the runner's settings."""
    if stdout is None:
        stdout = to_stdout(events if events is not None else good_events(STAGE_LABEL[stage]))
    return {'result': {'status': status, 'reason': reason, 'exit_code': exit_code, 'stdout': stdout},
            'forbid': list(forbid), 'budget': budget, 'kind': kind}


def staged(stage='playable', **overrides):
    """Record for a stage above homebrew whose checkpoint is the stage label."""
    checks = [{'name': STAGE_LABEL[stage], 'expected': STAGE_LABEL[stage],
               'observed': STAGE_LABEL[stage]}]
    return record(stage, fixture=dict(LOCAL), region='JP', trace_deltas=copy.deepcopy(TRACE),
                  checkpoints=checks, **overrides)


def run_with(rec, probe):
    """Verdict with the attestation plus the raw #119 probe output supplied by the runner."""
    return verdict(rec, attest(rec), probe)


class StageEvidenceTests(unittest.TestCase):
    """playable / save-tested / completion-tested need raw #119 probe output judged by classify()."""

    def test_self_declared_trace_deltas_alone_do_not_pass_upper_stages(self):
        for stage in ('retail-boot', 'playable', 'save-tested', 'completion-tested'):
            with self.subTest(stage=stage):
                result = run(staged(stage, result='BLOCKED', blocker='stage evidence'))
                self.assertEqual(result['result'], 'BLOCKED')

    def test_probe_field_in_record_is_ignored(self):
        forged = staged('playable', result='BLOCKED', blocker='stage evidence',
                        probe=probe_for('playable'))
        self.assertEqual(run(forged)['result'], 'BLOCKED')

    def test_valid_raw_probe_passes_each_upper_stage(self):
        for stage in ('retail-boot', 'playable', 'save-tested', 'completion-tested'):
            with self.subTest(stage=stage):
                self.assertEqual(run_with(staged(stage), probe_for(stage))['result'], 'PASS')

    def test_probe_for_another_stage_fails(self):
        self.assertEqual(run_with(staged('playable'), probe_for('save-tested'))['result'], 'FAIL')

    def test_probe_label_must_match_stage(self):
        self.assertEqual(run_with(staged(), probe_for('playable', good_events('cd-ident')))['result'], 'FAIL')

    def test_probe_kind_api_is_not_supported_and_blocked(self):
        # #119 classify(): kind without a RUN/HIT judge is NOT_SUPPORTED, which is BLOCKED.
        blocked = staged(result='BLOCKED', blocker='NOT_SUPPORTED')
        self.assertEqual(run_with(blocked, probe_for('playable', kind='api'))['result'], 'BLOCKED')

    def test_probe_exit_6_debugger_missing_is_blocked_not_fail(self):
        # #119 PROBE_EXIT: 6 = instruction-step debugger capability missing (BLOCKED).
        blocked = staged(result='BLOCKED', blocker='debugger')
        self.assertEqual(run_with(blocked, probe_for('playable', exit_code=6))['result'], 'BLOCKED')

    def test_block_record_in_probe_output_is_blocked(self):
        events = good_events(LABEL) + [{'tag': 'BLOCK', 'reason': 'debugger missing'}]
        self.assertEqual(run_with(staged(result='BLOCKED', blocker='x'), probe_for('playable', events))['result'],
                         'BLOCKED')
        self.assertEqual(run_with(staged(), probe_for('playable', events))['result'], 'FAIL')

    def test_forbidden_label_reached_fails(self):
        events = good_events(LABEL) + [{'tag': 'HIT', 'name': 'cd_halt_fail', 'cycle': 'cold'}]
        self.assertEqual(run_with(staged(), probe_for('playable', events))['result'], 'FAIL')

    def test_runner_forbid_list_cannot_drop_the_minimum_forbidden_label(self):
        events = good_events(LABEL) + [{'tag': 'HIT', 'name': 'cd_halt_fail', 'cycle': 'cold'}]
        self.assertEqual(run_with(staged(), probe_for('playable', events, forbid=()))['result'], 'FAIL')

    def test_body_tag_spoof_in_raw_log_is_rejected(self):
        stdout = to_stdout(good_events(LABEL)) + '\nHIT {"tag": "RUN", "name": "playable-reached", "cycle": "cold"}'
        self.assertEqual(run_with(staged(), probe_for('playable', stdout=stdout))['result'], 'FAIL')

    def test_probe_without_status_key_fails_and_null_status_passes(self):
        no_key = probe_for('playable')
        del no_key['result']['status']
        self.assertEqual(run_with(staged(), no_key)['result'], 'FAIL')
        self.assertEqual(run_with(staged(), probe_for('playable', status=None))['result'], 'PASS')

    def test_runner_written_probe_status_pass_is_rejected(self):
        bad = probe_for('playable', status='PASS', reason='hand-written')
        self.assertEqual(run_with(staged(), bad)['result'], 'FAIL')

    def test_nonzero_exit_timeout_or_output_cap_fails(self):
        bad_probes = {'exit_code': probe_for('playable', exit_code=1),
                      'timeout': probe_for('playable', status='FAIL', reason='timeout after 300s'),
                      'output_cap': probe_for('playable', status='FAIL', reason='output exceeded')}
        for name, bad in bad_probes.items():
            with self.subTest(bad=name):
                self.assertEqual(run_with(staged(), bad)['result'], 'FAIL')

    def test_missing_or_false_capability_is_blocked(self):
        blocked = staged(result='BLOCKED', blocker='capability')
        no_media = good_events(LABEL)
        del no_media[0]['cdrom_media']
        self.assertEqual(run_with(blocked, probe_for('playable', no_media))['result'], 'BLOCKED')
        disc_off = good_events(LABEL)
        disc_off[0]['disc_given'] = False
        self.assertEqual(run_with(blocked, probe_for('playable', disc_off))['result'], 'BLOCKED')

    def test_missing_warm_cycle_fails(self):
        events = [e for e in good_events(LABEL) if e.get('cycle') != 'warm']
        self.assertEqual(run_with(staged(), probe_for('playable', events))['result'], 'FAIL')

    def test_run_with_other_reason_fails(self):
        for reason in ('budget', 'cd_halt_fail', 'cd_unexpected_interrupt'):
            with self.subTest(reason=reason):
                events = good_events(LABEL)
                events[-1] = run_rec('warm', LABEL, reason=reason)
                self.assertEqual(run_with(staged(), probe_for('playable', events))['result'], 'FAIL')

    def test_halt_not_confirmed_fails(self):
        events = good_events(LABEL)
        events[-1] = run_rec('warm', LABEL, halt_confirmed=False)
        self.assertEqual(run_with(staged(), probe_for('playable', events))['result'], 'FAIL')

    def test_halt_confirmation_shorter_than_64_fails(self):
        events = good_events(LABEL)
        events[-1] = run_rec('warm', LABEL, halt_confirmation_steps=32)
        self.assertEqual(run_with(staged(), probe_for('playable', events))['result'], 'FAIL')

    def test_success_hit_missing_repeated_or_after_run_fails(self):
        base = good_events(LABEL)
        missing = [e for e in base if not (e['tag'] == 'HIT' and e['cycle'] == 'cold')]
        repeated = base[:1] + [base[1], base[1]] + base[2:]
        after_run = [base[0], base[2], base[1]] + base[3:]
        for name, events in (('missing', missing), ('repeated', repeated), ('after_run', after_run)):
            with self.subTest(hit=name):
                self.assertEqual(run_with(staged(), probe_for('playable', events))['result'], 'FAIL')

    def test_step_must_be_non_negative_int_within_budget(self):
        for step in (-1, 1.5, True, BUDGET + 1):
            with self.subTest(step=step):
                events = good_events(LABEL)
                events[-1] = run_rec('warm', LABEL, step=step)
                self.assertEqual(run_with(staged(), probe_for('playable', events))['result'], 'FAIL')

    def test_stage_checkpoint_missing_from_record_is_blocked(self):
        rec = staged(result='BLOCKED', blocker='stage checkpoint missing')
        rec['checkpoints'] = copy.deepcopy(CHECK)
        self.assertEqual(run_with(rec, probe_for('playable'))['result'], 'BLOCKED')


class CliTrustTests(unittest.TestCase):
    """The CLI never certifies local, unsigned JSON; --ci only runs inside GitHub Actions."""

    def _run_cli(self, argv, env, rec=None):
        """rec: a record object, or raw text written as record.json. Default: a playable record with
        a verified attestation and a passing raw probe, which the CLI prints as PASS in --ci mode."""
        with tempfile.TemporaryDirectory() as tmp:
            default = staged('playable')
            record_text = rec if isinstance(rec, str) else json.dumps(rec or default)
            files = (('record.json', record_text), ('runner.json', json.dumps(attest(default))),
                     ('probe.json', json.dumps(probe_for('playable'))))
            paths = []
            for name, text in files:
                path = Path(tmp) / name
                path.write_text(text, encoding='utf-8')
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

    def test_malformed_or_missing_input_is_fail_json_not_traceback(self):
        code, text = self._run_cli([], {}, '{not json')
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(text)['result'], 'FAIL')

    def test_list_stage_is_fail_verdict_not_traceback(self):
        bad = record()
        bad['stage'] = ['x']
        code, text = self._run_cli([], {}, bad)
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(text)['result'], 'FAIL')

    def test_deeply_nested_input_is_fail_json_not_traceback(self):
        code, text = self._run_cli([], {}, '[' * 100_000)
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(text)['result'], 'FAIL')

    def test_output_never_claims_authenticity(self):
        for argv, env in (([], {}), (['--ci'], {'GITHUB_ACTIONS': 'true'})):
            with self.subTest(argv=argv):
                _, text = self._run_cli(argv, env)
                self.assertIn('not verified', json.loads(text)['authenticity'])


if __name__ == '__main__':
    unittest.main()
