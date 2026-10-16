"""Evidence provenance (Issue #124): synthetic bundles and an injected gh attestation result. No network, no gh.

Every hash here is computed from short synthetic strings; no ROM, disc or fixture data is used.
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
WORKFLOW = '.github/workflows/compat-evidence.yml'
REF = 'refs/heads/main'
POLICY = {'repository': REPO, 'signer_workflow': WORKFLOW, 'ref': REF}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def make_bundle(**overrides):
    bundle = {'schema': ep.SCHEMA, 'repository': REPO, 'commit': COMMIT,
              'rom': {'sha256': sha(b'synthetic rom')}, 'fixture': None,
              'record': {'schema': 'pce-compat-evidence-v1', 'stage': 'rom-built'}}
    bundle.update(overrides)
    return bundle


def encode(bundle):
    return json.dumps(bundle, sort_keys=True).encode()


def make_attestation(bundle_bytes, commit=COMMIT, repo=REPO, workflow=WORKFLOW, ref=REF):
    """Synthetic stand-in for one entry of `gh attestation verify --format json`."""
    return [{'verificationResult': {
        'statement': {'subject': [{'name': 'compat-evidence.json', 'digest': {'sha256': sha(bundle_bytes)}}]},
        'signature': {'certificate': {
            'sourceRepositoryURI': f'https://github.com/{repo}',
            'sourceRepositoryDigest': commit,
            'buildSignerURI': f'https://github.com/{repo}/{workflow}@{ref}',
        }},
    }}]


class EvidenceProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.bundle = make_bundle()
        self.data = encode(self.bundle)

    def test_normal_attested_bundle_passes(self):
        result = ep.verify(self.data, make_attestation(self.data), POLICY)
        self.assertEqual(result['result'], ep.PASS, result)
        self.assertEqual(result['reasons'], [])

    def test_tampered_bundle_fails_digest_match(self):
        attestation = make_attestation(self.data)
        tampered = encode(make_bundle(rom={'sha256': sha(b'other rom')}))
        result = ep.verify(tampered, attestation, POLICY)
        self.assertEqual(result['result'], ep.FAIL)
        self.assertIn('no verified attestation subject matches', result['reasons'][0])

    def test_different_commit_sha_fails(self):
        result = ep.verify(self.data, make_attestation(self.data, commit=OTHER_COMMIT), POLICY)
        self.assertEqual(result['result'], ep.FAIL)
        self.assertIn('commit SHA differs', result['reasons'][0])

    def test_different_workflow_identity_fails(self):
        attestation = make_attestation(self.data, workflow='.github/workflows/evil.yml')
        result = ep.verify(self.data, attestation, POLICY)
        self.assertEqual(result['result'], ep.FAIL)
        self.assertIn('signing workflow identity', result['reasons'][0])

    def test_different_ref_fails(self):
        result = ep.verify(self.data, make_attestation(self.data, ref='refs/heads/feature'), POLICY)
        self.assertEqual(result['result'], ep.FAIL)

    def test_different_source_repository_fails(self):
        result = ep.verify(self.data, make_attestation(self.data, repo='other/fork'), POLICY)
        self.assertEqual(result['result'], ep.FAIL)
        self.assertIn('source repository differs', result['reasons'][0])

    def test_unsigned_local_run_is_local_only_even_with_self_declared_proof(self):
        # Self-declared proof in the bundle and CI env variables are not evidence.
        bundle = make_bundle(attestation={'verified': True}, verified=True)
        env = {'GITHUB_ACTIONS': 'true', 'GITHUB_SHA': COMMIT}
        with mock.patch.dict(os.environ, env):
            result = ep.verify(encode(bundle), None, POLICY)
        self.assertEqual(result['result'], ep.LOCAL_ONLY)
        self.assertTrue(result['trust'].startswith('LOCAL_ONLY'))

    def test_empty_or_non_array_attestation_is_blocked(self):
        for attestation in ([], {'verified': True}, 'ok'):
            result = ep.verify(self.data, attestation, POLICY)
            self.assertEqual(result['result'], ep.BLOCKED, attestation)

    def test_missing_fields_fail_closed(self):
        for key in ('repository', 'commit', 'rom', 'record', 'schema'):
            bundle = make_bundle()
            del bundle[key]
            result = ep.verify(encode(bundle), make_attestation(encode(bundle)), POLICY)
            self.assertEqual(result['result'], ep.FAIL, key)
        bad_commit = encode(make_bundle(commit='not-a-sha'))
        self.assertEqual(ep.verify(bad_commit, None, POLICY)['result'], ep.FAIL)

    def test_denylisted_or_unpinned_rom_hash_fails(self):
        denied = dict(POLICY, denied_sha256={sha(b'synthetic rom')})
        self.assertEqual(ep.verify(self.data, make_attestation(self.data), denied)['result'], ep.FAIL)
        pinned = dict(POLICY, rom_sha256=sha(b'pinned rom'))
        self.assertEqual(ep.verify(self.data, make_attestation(self.data), pinned)['result'], ep.FAIL)

    def test_fixture_origin_must_be_known(self):
        fixture = {'id': 'synthetic-1', 'sha256': sha(b'fixture'), 'origin': 'downloaded'}
        data = encode(make_bundle(fixture=fixture))
        self.assertEqual(ep.verify(data, None, POLICY)['result'], ep.FAIL)

    def test_cli_prints_local_only_and_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'bundle.json'
            path.write_bytes(self.data)
            with mock.patch('builtins.print') as printed:
                code = ep.main(['x', str(path), '--repo', REPO, '--signer-workflow', WORKFLOW])
        self.assertEqual(code, 1)
        self.assertIn('LOCAL_ONLY', printed.call_args[0][0])


if __name__ == '__main__':
    unittest.main()
