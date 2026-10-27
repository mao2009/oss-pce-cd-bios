"""Evidence provenance (Issue #124): synthetic artifacts and injected gh outputs. No network, no gh.

All hashes come from short synthetic strings or the original synthetic CD generator. No ROM, disc, BIOS or
third-party data is used.
"""
import copy
import hashlib
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import evidence_provenance as ep  # noqa: E402

REPO = 'example-owner/example-repo'
COMMIT = 'a' * 40
OTHER_COMMIT = 'b' * 40
WORKFLOW = '.github/workflows/api-source.yml'
REF = 'refs/heads/main'
RUN_ID, ATTEMPT = 4242, 1
GEARGRAFX_REV = 'c' * 40
GEARGRAFX_SHA = 'd' * 64
ROM = b'synthetic rom'
FIXTURE = {'bin_sha256': 'e' * 64, 'cue_sha256': 'f' * 64}
POLICY = {'repository': REPO, 'commit': COMMIT, 'signer_workflow': WORKFLOW,
          'geargrafx_revision': GEARGRAFX_REV, 'geargrafx_sha256': GEARGRAFX_SHA}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def make_artifact(**overrides):
    """Synthetic stand-in for cdread-evidence.json, shaped like tests/integration/geargrafx_cdread.py output."""
    tests = {name: {'status': kind} for name, kind in ep.SCENARIO_KIND.items()}
    artifact = {'scope': 'synthetic', 'rom_sha256': sha(ROM), 'geargrafx_revision': GEARGRAFX_REV,
                'source_archive_sha256': GEARGRAFX_SHA, 'disc': dict(FIXTURE), 'tests': tests,
                'primary_observations': {'RUN': [{'cycle': 'cold', 'reason': 'cd_halt_ok'},
                                                 {'cycle': 'warm', 'reason': 'cd_halt_ok'}]},
                'status': 'PASS'}
    artifact.update(overrides)
    return artifact


def encode(artifact):
    return json.dumps(artifact, sort_keys=True).encode()


def make_attestation(artifact_bytes, commit=COMMIT, repo=REPO, workflow=WORKFLOW, ref=REF, trigger='push',
                     runner='github-hosted', rom=ROM, run_uri=None):
    """One entry shaped like `gh attestation verify --format json` (shape checked against a real gh 2.x output)."""
    subjects = [{'name': 'cdread-evidence.json', 'digest': {'sha256': sha(artifact_bytes)}}]
    if rom is not None:
        subjects.append({'name': 'cdread-probe-not-bios.pce', 'digest': {'sha256': sha(rom)}})
    return [{'verificationResult': {
        'statement': {'subject': subjects},
        'signature': {'certificate': {
            'sourceRepositoryURI': f'https://github.com/{repo}',
            'sourceRepositoryDigest': commit,
            'sourceRepositoryRef': ref,
            'buildSignerURI': f'https://github.com/{repo}/{workflow}@{ref}',
            'buildSignerDigest': commit,
            'githubWorkflowTrigger': trigger,
            'runnerEnvironment': runner,
            'runInvocationURI': run_uri or f'https://github.com/{repo}/actions/runs/{RUN_ID}/attempts/{ATTEMPT}',
        }},
    }}]


def make_run(**overrides):
    """Shaped like `gh api repos/R/actions/runs/ID/attempts/N`."""
    run = {'id': RUN_ID, 'run_attempt': ATTEMPT, 'head_sha': COMMIT, 'head_branch': 'main', 'event': 'push',
           'path': WORKFLOW, 'repository': {'full_name': REPO}, 'head_repository': {'full_name': REPO}}
    run.update(overrides)
    return run


def check(data, attestation='default', rom=ROM, run='default', fixture=FIXTURE, policy=POLICY):
    attestation = make_attestation(data) if attestation == 'default' else attestation
    run = make_run() if run == 'default' else run
    return ep.verify(data, attestation, policy, rom, run, fixture)


class EvidenceProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.data = encode(make_artifact())

    def assertResult(self, result, expected, reason=None):
        self.assertEqual(result['result'], expected, result)
        self.assertTrue(result['compatibility'].startswith('NOT_PROMOTED'))
        if reason:
            self.assertTrue(any(reason in item for item in result['reasons']), result)

    def test_normal_attested_producer_passes_but_never_promotes_compatibility(self):
        result = check(self.data)
        self.assertResult(result, ep.PASS)
        self.assertEqual(result['reasons'], [])
        self.assertNotIn('compatib', result['trust'].lower())

    def test_scenario_kinds_match_integration_call_sites(self):
        # Both directions: every scenario the integration suite records is in the table with the same kind.
        text = (ROOT / 'tests/integration/geargrafx_cdread.py').read_text(encoding='utf-8')
        derived = {name: 'PASS' for name in re.findall(r'suite\.positive\("(\w+)"', text)}
        derived.update({name: 'PASS_EXPECTED_FAILURE' for name in
                        re.findall(r'suite\.(?:failing_guest|must_be_rejected|tampered_disc)\("(\w+)"', text)})
        derived.update(re.findall(r'results\["(\w+)"\] = \{\s*"status": "(\w+)"', text))
        self.assertIn('f"halt_unconfirmed_cut_{extra}_steps_after_arrival"', text)
        self.assertIn('for extra in (0, 1, HALT_CONFIRM_STEPS - 1):', text)
        steps = int(re.search(r'^HALT_CONFIRM_STEPS = (\d+)$', (ROOT / 'tools/build_cdread.py').read_text(
            encoding='utf-8'), re.M)[1])
        derived.update({f'halt_unconfirmed_cut_{n}_steps_after_arrival': 'PASS_EXPECTED_FAILURE'
                        for n in (0, 1, steps - 1)})
        self.assertEqual(derived, ep.SCENARIO_KIND)
        # The #118 tools/cd_read_contract.py table is a subset with the same kinds.
        contract = {'single_sector_lba5': 'PASS', 'first_sector_lba0': 'PASS', 'last_sector_lba15': 'PASS',
                    'three_sectors_lba5': 'PASS', 'disc_end_boundary_lba13_x3': 'PASS',
                    'wrong_sector_requested_lba6': 'PASS_EXPECTED_FAILURE',
                    'invalid_sector_lba16': 'PASS_EXPECTED_FAILURE', 'invalid_sector_lba_max': 'PASS_EXPECTED_FAILURE',
                    'read_past_disc_end_lba14_x3': 'PASS_EXPECTED_FAILURE',
                    'read6_on_audio_track_lba': 'OBSERVED_EMULATOR_BEHAVIOUR'}
        self.assertEqual({name: ep.SCENARIO_KIND[name] for name in contract}, contract)
        self.assertEqual(ep.REQUIRED_SCENARIOS, ('single_sector_lba5', 'first_sector_lba0', 'last_sector_lba15',
                                                 'three_sectors_lba5', 'disc_end_boundary_lba13_x3'))

    def test_missing_forged_or_wrong_kind_scenarios_fail(self):
        for name, kind in ep.SCENARIO_KIND.items():
            for bad in (None, 'PASS', 'PASS_EXPECTED_FAILURE', 'OBSERVED_EMULATOR_BEHAVIOUR', 'NOT_TESTED', 'FAIL'):
                if bad == kind:
                    continue
                artifact = make_artifact()
                if bad is None:
                    del artifact['tests'][name]
                else:
                    artifact['tests'][name] = {'status': bad}
                data = encode(artifact)
                with self.subTest(name=name, status=bad):
                    self.assertResult(check(data), ep.FAIL, name)

    def test_unknown_or_malformed_scenario_status_fails(self):
        for status in ('FAIL', 'BLOCKED', 'MAYBE', None, [], {}, 1):
            artifact = make_artifact()
            artifact['tests']['zero_sector_count'] = {'status': status}
            self.assertResult(check(encode(artifact)), ep.FAIL, 'zero_sector_count')
        for status in ('PASS', [], {}):
            artifact = make_artifact()
            artifact['tests']['extra_scenario'] = {'status': status}
            self.assertResult(check(encode(artifact)), ep.FAIL, 'unexpected scenarios')

    def test_deep_or_huge_json_does_not_raise(self):
        # Parsing failure and valid non-object JSON are both safely rejected.
        deeply_nested = b'[' * 100000 + b']' * 100000
        outcome = check(deeply_nested)
        self.assertEqual(outcome['result'], ep.FAIL)
        self.assertTrue(
            any('not JSON' in reason or 'not a JSON object' in reason
                for reason in outcome['reasons']), outcome,
        )
        malformed = b'{"rom_sha256": ' + b'9' * 5000 + b'}'
        self.assertResult(check(malformed), ep.FAIL, 'not JSON')

    def test_valid_nonobject_json_is_rejected(self):
        for data in (b'[]', b'null', b'"text"', b'42', b'true'):
            with self.subTest(data=data):
                self.assertResult(check(data), ep.FAIL, 'not a JSON object')

    def test_primary_run_must_be_one_cold_and_one_warm_halt(self):
        bad_runs = ([{'cycle': 'cold', 'reason': 'cd_halt_ok'}] * 2,
                    [{'cycle': 'warm', 'reason': 'cd_halt_ok'}] * 2,
                    [{'cycle': 'cold', 'reason': 'cd_halt_ok'}],
                    [{'cycle': 'cold', 'reason': 'cd_halt_ok'}, {'cycle': 'warm', 'reason': 'cd_halt_ok'},
                     {'cycle': 'warm', 'reason': 'cd_halt_ok'}],
                    [{'cycle': 'cold', 'reason': 'cd_halt_ok'}, {'cycle': 'warm', 'reason': 'budget'}],
                    ['cold', 'warm'], {'cold': 1, 'warm': 1}, None)
        for runs in bad_runs:
            data = encode(make_artifact(primary_observations={'RUN': runs}))
            with self.subTest(runs=runs):
                self.assertResult(check(data), ep.FAIL)

    def test_unexpected_json_types_never_raise(self):
        weird = (None, 0, 1.5, 'x', [], [None], {}, {'a': None}, True)
        artifacts = [encode(value) for value in weird]
        for key in ('tests', 'disc', 'primary_observations', 'rom_sha256', 'status'):
            artifacts += [encode(make_artifact(**{key: value})) for value in weird]
        artifacts += [encode(make_artifact(tests={**make_artifact()['tests'], 'x': value})) for value in weird]
        artifacts += [b'\xff\xfe', b'{not json']
        for data in artifacts:
            with self.subTest(data=data[:60]):
                self.assertIn(check(data)['result'], (ep.FAIL, ep.BLOCKED))
        for attestation in weird[1:] + ([{'verificationResult': value} for value in weird],
                                    [{'verificationResult': {'statement': value, 'signature': value}}
                                     for value in weird]):
            with self.subTest(attestation=attestation):
                self.assertIn(check(self.data, attestation=attestation)['result'], (ep.FAIL, ep.BLOCKED))
        for run in weird:
            with self.subTest(run=run):
                self.assertIn(check(self.data, run=run)['result'], (ep.FAIL, ep.BLOCKED))
        att = make_attestation(self.data)
        for value in weird:
            broken = copy.deepcopy(att)
            broken[0]['verificationResult']['signature']['certificate'] = value
            self.assertResult(check(self.data, attestation=broken), ep.FAIL)

    def test_rom_and_fixture_are_recomputed_not_trusted(self):
        self.assertResult(check(self.data, rom=b'other rom'), ep.FAIL, 'recomputed ROM')
        for key in FIXTURE:
            self.assertResult(check(self.data, fixture=dict(FIXTURE, **{key: '0' * 64})), ep.FAIL, key)
        denied = dict(POLICY, denied_sha256={'e' * 64})
        self.assertResult(check(self.data, policy=denied), ep.FAIL, 'denylist')

    def test_recompute_fixture_uses_the_generator_on_disk(self):
        fixture = ep.recompute_fixture()
        self.assertEqual(fixture, ep.recompute_fixture())  # deterministic
        self.assertTrue(all(re.fullmatch(r'[0-9a-f]{64}', value) for value in fixture.values()))

    def test_missing_rom_fixture_run_or_gh_output_is_blocked(self):
        self.assertResult(check(self.data, rom=None), ep.BLOCKED, 'ROM file')
        self.assertResult(check(self.data, fixture=None), ep.BLOCKED, 'fixture')
        self.assertResult(check(self.data, run=None), ep.BLOCKED, 'workflow run')
        for attestation in ([], {'verified': True}, 'ok', [{}]):
            self.assertResult(check(self.data, attestation=attestation), ep.BLOCKED)

    def test_forged_json_without_attestation_is_local_only(self):
        # Everything in the JSON looks like a PASS, and the environment claims CI. Neither is proof.
        forged = encode(make_artifact(attestation={'verified': True}, verified=True))
        with mock.patch.dict(os.environ, {'GITHUB_ACTIONS': 'true', 'GITHUB_SHA': COMMIT}):
            result = ep.verify(forged, None, POLICY, ROM, make_run(), FIXTURE)
        self.assertResult(result, ep.LOCAL_ONLY)
        self.assertTrue(result['trust'].startswith('LOCAL_ONLY'))

    def test_copied_or_tampered_artifact_fails_digest(self):
        other = encode(make_artifact(scope='other'))
        self.assertResult(check(self.data, attestation=make_attestation(other)), ep.FAIL, 'no verified attestation')
        tampered = encode(make_artifact(scope='tampered'))
        self.assertResult(check(tampered, attestation=make_attestation(self.data)), ep.FAIL, 'no verified attestation')

    def test_subjects_cannot_be_combined_across_statements(self):
        json_only = make_attestation(self.data, rom=None)
        rom_only = make_attestation(self.data, rom=ROM)
        rom_only[0]['verificationResult']['statement']['subject'].pop(0)
        self.assertResult(check(self.data, attestation=json_only + rom_only), ep.FAIL, 'both this artifact')

    def test_one_trusted_attestation_among_untrusted_ones_passes(self):
        untrusted = make_attestation(self.data, ref='refs/heads/feature', trigger='workflow_dispatch')
        self.assertResult(check(self.data, attestation=untrusted + make_attestation(self.data)), ep.PASS)
        self.assertResult(check(self.data, attestation=make_attestation(self.data) + untrusted), ep.PASS)

    def test_signer_digest_trigger_and_run_uri_fields_are_required(self):
        for field, value, reason in (('buildSignerDigest', OTHER_COMMIT, 'signing workflow file'),
                                     ('runInvocationURI', None, 'runInvocationURI'),
                                     ('buildTrigger', 'pull_request', 'trigger')):
            attestation = make_attestation(self.data)
            attestation[0]['verificationResult']['signature']['certificate'][field] = value
            self.assertResult(check(self.data, attestation=attestation), ep.FAIL, reason)
        attestation = make_attestation(self.data)
        del attestation[0]['verificationResult']['signature']['certificate']['githubWorkflowTrigger']
        self.assertResult(check(self.data, attestation=attestation), ep.FAIL, 'trigger')

    def test_attestation_without_the_rom_subject_fails(self):
        self.assertResult(check(self.data, attestation=make_attestation(self.data, rom=None)), ep.FAIL)
        self.assertResult(check(self.data, attestation=make_attestation(self.data, rom=b'x')), ep.FAIL)

    def test_untrusted_producers_fail(self):
        cases = {
            'commit SHA differs': make_attestation(self.data, commit=OTHER_COMMIT),
            'signing workflow identity': make_attestation(self.data, workflow='.github/workflows/evil.yml'),
            'source repository differs': make_attestation(self.data, repo='other/fork'),
            'source ref is not': make_attestation(self.data, ref='refs/pull/7/merge', trigger='pull_request'),
            'pull_request': make_attestation(self.data, trigger='pull_request'),
            'pull_request_target': make_attestation(self.data, trigger='pull_request_target'),
            'GitHub-hosted': make_attestation(self.data, runner='self-hosted'),
        }
        for reason, attestation in cases.items():
            with self.subTest(reason=reason):
                self.assertResult(check(self.data, attestation=attestation), ep.FAIL, reason)

    def test_workflow_dispatch_on_other_branch_is_not_main(self):
        branch = 'refs/heads/feat/124-evidence-provenance'
        attestation = make_attestation(self.data, ref=branch, trigger='workflow_dispatch')
        run = make_run(event='workflow_dispatch', head_branch='feat/124-evidence-provenance')
        result = check(self.data, attestation=attestation, run=run)
        self.assertResult(result, ep.FAIL, 'source ref is not refs/heads/main')
        # workflow_dispatch on main is the same producer as push on main
        ok = check(self.data, attestation=make_attestation(self.data, trigger='workflow_dispatch'),
                   run=make_run(event='workflow_dispatch'))
        self.assertResult(ok, ep.PASS)

    def test_run_invocation_uri_must_match_the_supplied_run(self):
        cases = {
            'not a workflow run': make_attestation(self.data, run_uri='https://evil.example/actions/runs/1/attempts/1'),
            'not a workflow run of': make_attestation(
                self.data, run_uri=f'https://github.com/other/fork/actions/runs/{RUN_ID}/attempts/1'),
            'does not name': make_attestation(
                self.data, run_uri=f'https://github.com/{REPO}/actions/runs/{RUN_ID + 1}/attempts/1'),
        }
        for reason, attestation in cases.items():
            self.assertResult(check(self.data, attestation=attestation), ep.FAIL, reason)
        runs = {
            'does not name': make_run(run_attempt=2),
            'head_sha differs': make_run(head_sha=OTHER_COMMIT),
            'did not run on main': make_run(head_branch='feature'),
            'is not push or workflow_dispatch': make_run(event='pull_request'),
            'not the signing workflow': make_run(path='.github/workflows/ci.yml'),
            'fork': make_run(head_repository={'full_name': 'other/fork'}),
        }
        for reason, run in runs.items():
            with self.subTest(reason=reason):
                self.assertResult(check(self.data, run=run), ep.FAIL, reason)

    def test_cli_runs_without_network_and_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / 'a.json').write_bytes(self.data)
            (tmp / 'deps.json').write_text(json.dumps({'geargrafx': {'revision': GEARGRAFX_REV,
                                                                     'sha256': GEARGRAFX_SHA}}))
            (tmp / 'verify.json').write_text('not json')
            base = ['x', str(tmp / 'a.json'), '--repo', REPO, '--commit', COMMIT, '--deps', str(tmp / 'deps.json'),
                    '--root', str(tmp)]
            fixture = mock.patch.object(ep, 'recompute_fixture', return_value=FIXTURE)
            with fixture, mock.patch('builtins.print') as printed:
                self.assertEqual(ep.main(base), 1)
                self.assertIn('LOCAL_ONLY', printed.call_args[0][0])
                self.assertEqual(ep.main(base + ['--attestation', str(tmp / 'verify.json')]), 1)
                self.assertIn('BLOCKED', printed.call_args[0][0])
                (tmp / 'rom').write_bytes(ROM)
                (tmp / 'att.json').write_text(json.dumps(make_attestation(self.data)))
                full = base + ['--attestation', str(tmp / 'att.json'), '--rom', str(tmp / 'rom')]
                self.assertEqual(ep.main(full + ['--run', str(tmp / 'verify.json')]), 1)
                self.assertIn('BLOCKED', printed.call_args[0][0])
                (tmp / 'run.json').write_text(json.dumps(make_run()))
                self.assertEqual(ep.main(full + ['--run', str(tmp / 'run.json')]), 0)
                # the repository denylist (provenance/denylist.sha256, #117 format) is enforced by the CLI too
                (tmp / 'provenance').mkdir()
                for denied in (FIXTURE['bin_sha256'], sha(ROM).upper(), FIXTURE['cue_sha256']):
                    (tmp / 'provenance' / 'denylist.sha256').write_text(f'# comment\n{denied}  some-title.bin\n')
                    self.assertEqual(ep.main(full + ['--run', str(tmp / 'run.json')]), 1)
                    self.assertIn('denylist', printed.call_args[0][0])
                (tmp / 'provenance' / 'denylist.sha256').unlink()
                missing_rom = base + ['--attestation', str(tmp / 'att.json'), '--rom', str(tmp / 'nope'),
                                      '--run', str(tmp / 'run.json')]
                self.assertEqual(ep.main(missing_rom), 1)
                self.assertIn('BLOCKED', printed.call_args[0][0])
            with mock.patch('builtins.print'), mock.patch('sys.stderr'):
                self.assertEqual(ep.main(['x', 'a', '--repo', 'bad', '--commit', COMMIT]), 2)
                with self.assertRaises(SystemExit):
                    ep.main(base + ['--ref', 'refs/heads/feature'])  # no way to accept a non-main ref
                with self.assertRaises(SystemExit):
                    ep.main(base + ['--signer-workflow', '.github/workflows/evil.yml'])

    def test_reused_commit_evidence_fails_even_with_a_forged_run_record(self):
        # Attestation and run record both forged to the other commit: the checkout commit still differs.
        attestation = make_attestation(self.data, commit=OTHER_COMMIT)
        run = make_run(head_sha=OTHER_COMMIT)
        result = check(self.data, attestation=attestation, run=run)
        self.assertResult(result, ep.FAIL, 'attested commit SHA differs')
        self.assertNotEqual(result['result'], ep.PASS)

    def test_self_declared_approvals_never_count_as_approval(self):
        # The artifact may claim approvals, reviewers, or branch rules. This verifier never reads them as approval.
        claims = {'approval': {'approved': True, 'approved_by': 'v'}, 'approvals': 1,
                  'human_review': {'reviewer': 'agent', 'status': 'APPROVED'},
                  'main_rules': {'required_approving_review_count': 1}}
        data = encode(make_artifact(**claims))
        plain = check(self.data)
        claimed = check(data, attestation=make_attestation(data))
        self.assertResult(claimed, ep.PASS)
        self.assertEqual(claimed['reasons'], plain['reasons'])
        self.assertEqual(claimed['trust'], plain['trust'])
        self.assertNotIn('approv', json.dumps(claimed).lower())
        self.assertResult(check(data, attestation=None), ep.LOCAL_ONLY)  # no attestation: the claims are still not evidence
        self.assertResult(check(data, attestation=make_attestation(self.data)), ep.FAIL, 'no verified attestation')

    def test_transport_pass_is_never_api_compatibility_proof(self):
        # A PASS is emulator transport evidence only: the compatibility field is fixed and never says VERIFIED.
        for result in (check(self.data), check(self.data, rom=None), check(self.data, attestation=None),
                       check(encode(make_artifact(status='FAIL')), attestation=None)):
            with self.subTest(result=result['result']):
                self.assertEqual(result['compatibility'], ep.NOT_PROMOTED)
                self.assertNotIn('verified', json.dumps(result).lower())


if __name__ == '__main__':
    unittest.main()
