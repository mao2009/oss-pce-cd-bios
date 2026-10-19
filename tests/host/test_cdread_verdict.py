"""Trusted CI producer of the CD_READ verdict (Issue #156). Synthetic inputs only; no network, no gh, no ROM.

The producer must fail closed on forged input, wrong commit, wrong ROM, missing attestation ("key"), untrusted
trigger/ref, and must never write VERIFIED while protect-main has 0 approvals (#124 plan C).
"""
import json
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'tests' / 'host'))
import cdread_verdict as cv  # noqa: E402
import cd_read_contract as crc  # noqa: E402
from test_evidence_provenance import (  # noqa: E402
    ATTEMPT, COMMIT, FIXTURE, OTHER_COMMIT, POLICY, REPO, ROM, RUN_ID, encode, make_artifact, make_attestation,
    make_run, sha)

ENV = {'GITHUB_ACTIONS': 'true', 'GITHUB_EVENT_NAME': 'push', 'GITHUB_REF': 'refs/heads/main',
       'GITHUB_REPOSITORY': REPO, 'GITHUB_SHA': COMMIT, 'GITHUB_RUN_ID': str(RUN_ID),
       'GITHUB_RUN_ATTEMPT': str(ATTEMPT),
       'GITHUB_WORKFLOW_REF': f'{REPO}/.github/workflows/api-source.yml@refs/heads/main'}
ALL_VERIFIED = {f'T{i}': 'VERIFIED' for i in range(1, 6)}
BLOCKED_CLAIMS = {**{f'T{i}': 'BLOCKED' for i in range(1, 6)}, 'T6': 'HYPOTHESIS', 'T7': 'UNKNOWN', 'T8': 'UNKNOWN'}
VERIFIED_CLAIMS = {**ALL_VERIFIED, 'T6': 'HYPOTHESIS', 'T7': 'UNKNOWN', 'T8': 'UNKNOWN'}


def rules(approvals):
    return [{'type': 'deletion'},
            {'type': 'pull_request', 'parameters': {'required_approving_review_count': approvals}}]


def produce(evidence=None, attestation='default', rom=ROM, run='default', branch_rules='default', env=None):
    evidence = encode(make_artifact()) if evidence is None else evidence
    if attestation == 'default':
        attestation = make_attestation(evidence)
    return cv.produce(evidence, rom, attestation, make_run() if run == 'default' else run,
                      rules(0) if branch_rules == 'default' else branch_rules, ENV if env is None else env,
                      POLICY, FIXTURE, now=NOW)


NOW = datetime(2026, 10, 16, 1, 2, 3, tzinfo=timezone.utc)
SCHEMA_FIELDS = {'schema', 'repository', 'commit', 'ref', 'producer', 'issued_at', 'expires_at', 'geargrafx',
                 'evidence', 'provenance', 'main_rules', 'claims'}


class ProducerTests(unittest.TestCase):
    def assertRefused(self, **kwargs):
        with self.assertRaises(cv.Refused):
            produce(**kwargs)

    def test_verdict_matches_schema_v1_and_binds_issuer_commit_and_rom(self):
        evidence = encode(make_artifact())
        verdict = produce(evidence)
        self.assertEqual(set(verdict), SCHEMA_FIELDS)
        self.assertEqual(verdict['schema'], 'oss-pce-cd-bios/cdread-verdict/v1')
        self.assertEqual((verdict['repository'], verdict['commit'], verdict['ref']), (REPO, COMMIT, 'refs/heads/main'))
        self.assertEqual(verdict['producer'], {'workflow': '.github/workflows/api-source.yml', 'run_id': RUN_ID,
                                               'run_attempt': ATTEMPT})
        self.assertEqual(verdict['evidence'], {'artifact_sha256': sha(evidence), 'rom_sha256': sha(ROM), **FIXTURE})
        self.assertEqual(verdict['geargrafx'], {'revision': POLICY['geargrafx_revision'],
                                                'source_archive_sha256': POLICY['geargrafx_sha256']})
        self.assertEqual(verdict['provenance'], {'result': 'PASS', 'verifier': 'tools/evidence_provenance.py'})
        self.assertEqual((verdict['issued_at'], verdict['expires_at']), ('2026-10-16T01:02:03Z', '2026-10-23T01:02:03Z'))

    def test_claims_stay_blocked_below_the_approval_boundary(self):
        for approvals in (0,):
            verdict = produce(branch_rules=rules(approvals))
            self.assertEqual(verdict['main_rules'], {'source': 'github-api',
                                                     'required_approving_review_count': approvals})
            self.assertEqual(verdict['claims'], BLOCKED_CLAIMS)
            self.assertEqual(verdict['claims'], crc.compat_status())  # still the fail-closed ledger values

    def test_claims_are_verified_from_authenticated_coverage_when_approvals_met(self):
        for approvals in (1, 5):
            with self.subTest(approvals=approvals):
                verdict = produce(branch_rules=rules(approvals))
                self.assertEqual(verdict['claims'], VERIFIED_CLAIMS)

    def test_transport_claims_require_full_coverage_and_the_approval_boundary(self):
        full = make_artifact()
        self.assertEqual(cv.transport_claims(full, 1), VERIFIED_CLAIMS)
        self.assertEqual(cv.transport_claims(full, 0), BLOCKED_CLAIMS)
        missing = make_artifact()
        del missing['tests']['single_sector_lba5']  # cited by T1
        self.assertEqual(cv.transport_claims(missing, 1)['T1'], 'BLOCKED')
        rekinded = make_artifact()
        rekinded['tests']['read6_on_audio_track_lba']['status'] = 'PASS'  # T5 wants OBSERVED_EMULATOR_BEHAVIOUR
        claims = cv.transport_claims(rekinded, 1)
        self.assertEqual(claims['T5'], 'BLOCKED')
        self.assertEqual(claims['T1'], 'VERIFIED')  # a claim whose own scenarios are covered is unaffected

    def test_unreadable_branch_rules_are_refused(self):
        for bad in (None, {}, [], [{'type': 'pull_request'}], [{'type': 'pull_request', 'parameters': {
                'required_approving_review_count': True}}]):
            with self.subTest(bad=bad):
                self.assertRefused(branch_rules=bad)

    def test_forged_evidence_without_attestation_is_refused(self):
        self.assertRefused(attestation=None)

    def test_tampered_evidence_is_refused(self):
        self.assertRefused(evidence=encode(make_artifact(scope='tampered')),
                           attestation=make_attestation(encode(make_artifact())))

    def test_wrong_commit_is_refused(self):
        evidence = encode(make_artifact())
        self.assertRefused(evidence=evidence, attestation=make_attestation(evidence, commit=OTHER_COMMIT))

    def test_wrong_rom_is_refused(self):
        self.assertRefused(rom=b'another rom')

    def test_missing_attestation_output_run_or_rom_is_refused(self):
        for kwargs in ({'attestation': []}, {'run': None}, {'rom': None}):
            with self.subTest(**{k: repr(v) for k, v in kwargs.items()}):
                self.assertRefused(**kwargs)

    def test_run_record_of_another_run_is_refused(self):
        self.assertRefused(env={**ENV, 'GITHUB_RUN_ID': '1'})
        self.assertRefused(env={**ENV, 'GITHUB_RUN_ATTEMPT': '2'})

    def test_untrusted_environment_is_refused(self):
        for key, value in (('GITHUB_EVENT_NAME', 'pull_request'), ('GITHUB_EVENT_NAME', 'workflow_dispatch'),
                           ('GITHUB_REF', 'refs/pull/1/merge'), ('GITHUB_REPOSITORY', 'fork/oss-pce-cd-bios'),
                           ('GITHUB_SHA', OTHER_COMMIT), ('GITHUB_ACTIONS', None), ('GITHUB_RUN_ID', None),
                           ('GITHUB_WORKFLOW_REF', f'{REPO}/.github/workflows/ci.yml@refs/heads/main')):
            env = {k: v for k, v in {**ENV, key: value}.items() if v is not None}
            with self.subTest(key=key, value=value):
                self.assertRefused(env=env)

    def test_verdict_bytes_are_canonical(self):
        data = cv.encode(produce())
        self.assertEqual(data, cv.encode(json.loads(data)))
        self.assertTrue(data.endswith(b'\n'))
        self.assertLessEqual(len(data), 65536)

    def test_cli_refuses_outside_trusted_ci_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            evidence = Path(tmp) / 'e.json'
            evidence.write_bytes(encode(make_artifact()))
            out = Path(tmp) / 'verdict.json'
            env = {k: v for k, v in __import__('os').environ.items() if not k.startswith('GITHUB_')}
            proc = subprocess.run([sys.executable, str(ROOT / 'tools/cdread_verdict.py'), str(evidence),
                                   '--out', str(out)], capture_output=True, text=True, env=env)
            self.assertEqual(proc.returncode, 2, proc.stderr)
            self.assertFalse(out.exists())


class WorkflowWiringTests(unittest.TestCase):
    """Text checks on api-source.yml: who produces, who signs, and that no untrusted job gets signing rights."""

    @classmethod
    def setUpClass(cls):
        text = (ROOT / '.github/workflows/api-source.yml').read_text(encoding='utf-8')
        cls.jobs = dict(re.findall(r'^  ([\w-]+):\n((?:(?:    .*)?\n)+)', text + '\n', flags=re.M))

    def test_bios_dev_never_gets_signing_permissions(self):
        self.assertNotIn('id-token', self.jobs['bios-dev'])
        self.assertNotIn('attestations: write', self.jobs['bios-dev'])

    def test_producer_runs_only_on_push_to_main_after_attestation_check(self):
        job = self.jobs['verify-cdread-attestation']
        self.assertIn("github.event_name == 'push' && github.ref == 'refs/heads/main'", job)
        self.assertIn('tools/cdread_verdict.py', job)
        self.assertNotIn('id-token', job)
        self.assertLess(job.index('tools/evidence_provenance.py'), job.index('tools/cdread_verdict.py'))

    def test_verdict_signer_runs_no_repository_code(self):
        job = self.jobs['attest-cdread-verdict']
        # Signs only the guarded publication: its dependency is the guard job, which itself needs the verdict producer.
        self.assertIn('needs: guard-cdread-verdict', job)
        self.assertIn("github.event_name == 'push' && github.ref == 'refs/heads/main'", job)
        self.assertIn('id-token: write', job)
        self.assertIn('actions/attest-build-provenance@', job)
        self.assertIn('subject-path: verdict/cdread-verdict.json', job)
        self.assertNotIn('actions/checkout', job)
        self.assertNotIn('python', job)

    def test_signed_verdict_is_reverified_against_main(self):
        job = self.jobs['verify-cdread-verdict']
        self.assertIn('needs: attest-cdread-verdict', job)
        self.assertIn('gh attestation verify', job)
        self.assertIn('--source-ref refs/heads/main', job)
        self.assertIn('--source-digest "$GITHUB_SHA"', job)
        self.assertNotIn('id-token', job)


if __name__ == '__main__':
    unittest.main()
