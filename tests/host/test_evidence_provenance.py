"""Evidence provenance (Issue #124): synthetic artifacts and an injected gh attestation result. No network, no gh.

All hashes come from short synthetic strings. No ROM, disc, BIOS or fixture data is used.
"""
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import evidence_provenance as ep  # noqa: E402

REPO = 'example-owner/example-repo'
COMMIT = 'a' * 40
OTHER_COMMIT = 'b' * 40
WORKFLOW = '.github/workflows/api-source.yml'
REF = 'refs/heads/main'
GEARGRAFX_REV = 'c' * 40
GEARGRAFX_SHA = 'd' * 64
ROM = b'synthetic rom'
POLICY = {'repository': REPO, 'commit': COMMIT, 'signer_workflow': WORKFLOW, 'ref': REF,
          'geargrafx_revision': GEARGRAFX_REV, 'geargrafx_sha256': GEARGRAFX_SHA}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def make_artifact(**overrides):
    """Synthetic stand-in for cdread-evidence.json: a status that is consistent with its scenarios."""
    tests = {name: {'status': 'PASS', 'lba': 0, 'count': 1} for name in ep.REQUIRED_SCENARIOS}
    tests['judge_observation_mutations'] = {'status': 'PASS_EXPECTED_FAILURE', 'mutants': ['x']}
    artifact = {'scope': 'synthetic', 'rom_sha256': sha(ROM), 'geargrafx_revision': GEARGRAFX_REV,
                'source_archive_sha256': GEARGRAFX_SHA, 'disc': {'bin_sha256': 'e' * 64, 'cue_sha256': 'f' * 64},
                'tests': tests, 'primary_observations': {'RUN': [{'cycle': 'cold'}, {'cycle': 'warm'}]},
                'status': 'PASS'}
    artifact.update(overrides)
    return artifact


def encode(artifact):
    return json.dumps(artifact, sort_keys=True).encode()


def make_attestation(artifact_bytes, commit=COMMIT, repo=REPO, workflow=WORKFLOW, ref=REF,
                     trigger='push', runner='github-hosted'):
    """Synthetic stand-in for one entry of `gh attestation verify --format json` (field names unconfirmed live)."""
    return [{'verificationResult': {
        'statement': {'subject': [{'name': 'cdread-evidence.json', 'digest': {'sha256': sha(artifact_bytes)}}]},
        'signature': {'certificate': {
            'sourceRepositoryURI': f'https://github.com/{repo}',
            'sourceRepositoryDigest': commit,
            'sourceRepositoryRef': ref,
            'buildSignerURI': f'https://github.com/{repo}/{workflow}@{ref}',
            'githubWorkflowTrigger': trigger,
            'runnerEnvironment': runner,
        }},
    }}]


class EvidenceProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.data = encode(make_artifact())

    def test_normal_attested_producer_passes(self):
        result = ep.verify(self.data, make_attestation(self.data), POLICY, ROM)
        self.assertEqual(result['result'], ep.PASS, result)
        self.assertEqual(result['reasons'], [])

    def test_forged_json_without_attestation_is_local_only(self):
        # Everything in the JSON looks like a PASS, and the environment claims CI. Neither is proof.
        forged = encode(make_artifact(attestation={'verified': True}, verified=True))
        env = {'GITHUB_ACTIONS': 'true', 'GITHUB_SHA': COMMIT}
        with mock.patch.dict(os.environ, env):
            result = ep.verify(forged, None, POLICY, ROM)
        self.assertEqual(result['result'], ep.LOCAL_ONLY)
        self.assertTrue(result['trust'].startswith('LOCAL_ONLY'))

    def test_forged_json_with_copied_attestation_fails_digest(self):
        other = encode(make_artifact(scope='other'))
        result = ep.verify(self.data, make_attestation(other), POLICY, ROM)
        self.assertEqual(result['result'], ep.FAIL)
        self.assertIn('no verified attestation subject matches', result['reasons'][0])

    def test_tampered_artifact_fails_digest(self):
        tampered = encode(make_artifact(tests={**make_artifact()['tests'],
                                               'single_sector_lba5': {'status': 'FAIL'}}))
        result = ep.verify(tampered, make_attestation(self.data), POLICY, ROM)
        self.assertEqual(result['result'], ep.FAIL)

    def test_different_commit_fails(self):
        result = ep.verify(self.data, make_attestation(self.data, commit=OTHER_COMMIT), POLICY, ROM)
        self.assertEqual(result['result'], ep.FAIL)
        self.assertIn('commit SHA differs', result['reasons'][0])

    def test_different_workflow_fails(self):
        result = ep.verify(self.data, make_attestation(self.data, workflow='.github/workflows/evil.yml'), POLICY, ROM)
        self.assertEqual(result['result'], ep.FAIL)
        self.assertIn('signing workflow identity', result['reasons'][0])

    def test_different_repository_fails(self):
        result = ep.verify(self.data, make_attestation(self.data, repo='other/fork'), POLICY, ROM)
        self.assertEqual(result['result'], ep.FAIL)
        self.assertIn('source repository differs', result['reasons'][0])

    def test_pr_head_and_fork_producers_are_not_trusted(self):
        pr = make_attestation(self.data, ref='refs/pull/7/merge', trigger='pull_request')
        self.assertEqual(ep.verify(self.data, pr, POLICY, ROM)['result'], ep.FAIL)
        fork = make_attestation(self.data, trigger='pull_request')  # same ref string, PR-triggered
        self.assertEqual(ep.verify(self.data, fork, POLICY, ROM)['result'], ep.FAIL)

    def test_self_hosted_producer_is_not_trusted(self):
        result = ep.verify(self.data, make_attestation(self.data, runner='self-hosted'), POLICY, ROM)
        self.assertEqual(result['result'], ep.FAIL)

    def test_empty_or_non_array_attestation_is_blocked(self):
        for attestation in ([], {'verified': True}, 'ok'):
            self.assertEqual(ep.verify(self.data, attestation, POLICY, ROM)['result'], ep.BLOCKED, attestation)

    def test_content_pins_and_scenarios_fail_closed(self):
        cases = {
            'missing rom_sha256': make_artifact(rom_sha256=None),
            'wrong geargrafx revision': make_artifact(geargrafx_revision='0' * 40),
            'wrong archive hash': make_artifact(source_archive_sha256='0' * 64),
            'status not PASS': make_artifact(status='FAIL'),
            'required scenario missing': make_artifact(tests={'first_sector_lba0': {'status': 'PASS'}}),
            'RUN not cold and warm': make_artifact(primary_observations={'RUN': [{'cycle': 'cold'}]}),
        }
        for label, artifact in cases.items():
            data = encode(artifact)
            self.assertEqual(ep.verify(data, make_attestation(data), POLICY, ROM)['result'], ep.FAIL, label)
        missing = make_artifact()
        del missing['tests']
        self.assertEqual(ep.verify(encode(missing), None, POLICY, ROM)['result'], ep.FAIL)

    def test_rom_file_and_denylist_fail_closed(self):
        self.assertEqual(ep.verify(self.data, make_attestation(self.data), POLICY, b'other rom')['result'], ep.FAIL)
        denied = dict(POLICY, denied_sha256={'e' * 64})
        self.assertEqual(ep.verify(self.data, make_attestation(self.data), denied, ROM)['result'], ep.FAIL)

    def test_cli_unattested_run_exits_nonzero_with_local_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / 'cdread-evidence.json'
            artifact.write_bytes(self.data)
            deps = Path(tmp) / 'dependencies.json'
            deps.write_text(json.dumps({'geargrafx': {'revision': GEARGRAFX_REV, 'sha256': GEARGRAFX_SHA}}))
            with mock.patch('builtins.print') as printed:
                code = ep.main(['x', str(artifact), '--repo', REPO, '--commit', COMMIT, '--deps', str(deps)])
        self.assertEqual(code, 1)
        self.assertIn('LOCAL_ONLY', printed.call_args[0][0])


if __name__ == '__main__':
    unittest.main()
