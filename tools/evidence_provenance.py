"""Evidence provenance verifier (Issue #124): binds a compat evidence bundle to a GitHub artifact attestation.

The bundle file is the attested artifact. In CI the flow is (not wired yet, see the PR):
  gh attestation verify BUNDLE.json --repo OWNER/REPO --signer-workflow PATH --format json > VERIFY.json
  python tools/evidence_provenance.py BUNDLE.json --repo OWNER/REPO --signer-workflow PATH --attestation VERIFY.json
verify() takes the gh result as an injected input and never runs gh or the network. Fields inside the
bundle, hashes, and environment variables (GITHUB_ACTIONS, GITHUB_SHA, ...) are never proof of origin.

Results:
  PASS       every check holds against a verified attestation whose subject is this exact bundle file.
  FAIL       a check contradicts the evidence (tampered digest, other repo, commit or signing workflow,
             bad fields, denylisted or unpinned ROM hash).
  BLOCKED    an attestation is required but gh output is missing or unusable.
  LOCAL_ONLY no attestation supplied: local or unsigned output. It is never compatibility evidence.

Limits: the gh JSON field names used here (verificationResult.statement.subject, signature.certificate
sourceRepositoryURI / sourceRepositoryDigest / buildSignerURI) are not yet confirmed against a live
attestation from this repository. The live check is BLOCKED until a CI run produces one.
"""
import argparse
import hashlib
import json
import re
import sys

SCHEMA = 'pce-compat-evidence-provenance-v1'
PASS, FAIL, BLOCKED, LOCAL_ONLY = 'PASS', 'FAIL', 'BLOCKED', 'LOCAL_ONLY'
ORIGINS = ('original-synthetic', 'local-proprietary')
REPO = re.compile(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+')
HEX40 = re.compile(r'[0-9a-f]{40}')
HEX64 = re.compile(r'[0-9a-f]{64}')
LOCAL_TRUST = 'LOCAL_ONLY: no GitHub artifact attestation; unsigned output is not compatibility evidence'


def _hex(value, pattern):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def structure_errors(bundle):
    """Return schema errors; missing or malformed fields fail closed."""
    if not isinstance(bundle, dict):
        return ['bundle is not an object']
    errors = []
    if bundle.get('schema') != SCHEMA:
        errors.append(f'schema must be {SCHEMA}')
    if not isinstance(bundle.get('repository'), str) or not REPO.fullmatch(bundle['repository']):
        errors.append('repository missing or not owner/repo')
    if not _hex(bundle.get('commit'), HEX40):
        errors.append('commit missing or not a 40-hex Git SHA')
    rom = bundle.get('rom')
    if 'rom' not in bundle or (rom is not None and not (isinstance(rom, dict) and _hex(rom.get('sha256'), HEX64))):
        errors.append('rom must be null or {"sha256": 64 hex}')
    fixture = bundle.get('fixture')
    if fixture is not None:
        if not isinstance(fixture, dict) or not fixture.get('id'):
            errors.append('fixture.id missing')
        elif not _hex(fixture.get('sha256'), HEX64):
            errors.append('fixture.sha256 missing or not SHA-256')
        elif fixture.get('origin') not in ORIGINS:
            errors.append('fixture.origin must be original-synthetic or local-proprietary')
    if not isinstance(bundle.get('record'), dict):
        errors.append('record missing (the compat evidence record being attested)')
    return errors


def hash_policy_errors(bundle, policy):
    """ROM and fixture hashes: never on the denylist, and the ROM must match a pinned hash when one is set."""
    errors = []
    rom_sha = (bundle.get('rom') or {}).get('sha256')
    fixture_sha = (bundle.get('fixture') or {}).get('sha256')
    denied = policy.get('denied_sha256', set())
    if rom_sha in denied or fixture_sha in denied:
        errors.append('ROM or fixture sha256 is on the denylist')
    pinned = policy.get('rom_sha256')
    if pinned and rom_sha != pinned:
        errors.append('ROM sha256 differs from the pinned ROM hash')
    return errors


def _statements(attestation):
    """Return the verificationResult objects from gh attestation verify --format json, or None if unusable."""
    if not isinstance(attestation, list) or not attestation:
        return None
    results = [entry.get('verificationResult') for entry in attestation if isinstance(entry, dict)]
    results = [result for result in results if isinstance(result, dict)]
    return results or None


def _subject_digests(result):
    subjects = (result.get('statement') or {}).get('subject') or []
    return {(subject.get('digest') or {}).get('sha256') for subject in subjects if isinstance(subject, dict)}


def _certificate_findings(result, bundle, policy):
    """Compare one verified attestation's signing certificate with the bundle and the policy."""
    certificate = (result.get('signature') or {}).get('certificate') or {}
    repo = bundle['repository']
    findings = []
    if certificate.get('sourceRepositoryURI') != f'https://github.com/{repo}':
        findings.append('attested source repository differs from bundle repository')
    if certificate.get('sourceRepositoryDigest') != bundle['commit']:
        findings.append('attested commit SHA differs from bundle commit')
    signer_prefix = f"https://github.com/{policy['repository']}/{policy['signer_workflow']}@"
    signer = certificate.get('buildSignerURI') or ''
    if not (signer.startswith(signer_prefix) and signer[len(signer_prefix):] == policy['ref']):
        findings.append('signing workflow identity differs from policy')
    return findings


def verify(bundle_bytes, attestation, policy):
    """Return {'result', 'reasons', 'trust'} for the raw bundle bytes and the injected gh verify output.

    policy: {'repository': 'owner/repo', 'signer_workflow': '.github/workflows/x.yml',
             'ref': 'refs/heads/main', 'commit': optional 40-hex, 'rom_sha256': optional, 'denied_sha256': set}
    attestation: parsed JSON from `gh attestation verify --format json`, or None for a local run.
    """
    try:
        bundle = json.loads(bundle_bytes.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        return {'result': FAIL, 'reasons': [f'bundle is not JSON: {error}'], 'trust': LOCAL_TRUST}
    errors = structure_errors(bundle)
    if errors:
        return {'result': FAIL, 'reasons': errors, 'trust': LOCAL_TRUST}
    errors = hash_policy_errors(bundle, policy)
    if bundle['repository'] != policy['repository']:
        errors.append('bundle repository differs from policy repository')
    if policy.get('commit') and bundle['commit'] != policy['commit']:
        errors.append('bundle commit differs from policy commit')
    if errors:
        return {'result': FAIL, 'reasons': errors, 'trust': LOCAL_TRUST}
    if attestation is None:
        return {'result': LOCAL_ONLY, 'reasons': ['no attestation supplied; local or unsigned run'],
                'trust': LOCAL_TRUST}
    results = _statements(attestation)
    if results is None:
        return {'result': BLOCKED, 'reasons': ['gh attestation output is empty or not a verification array'],
                'trust': 'attestation required; not available'}
    digest = hashlib.sha256(bundle_bytes).hexdigest()
    matched = [result for result in results if digest in _subject_digests(result)]
    if not matched:
        return {'result': FAIL, 'reasons': ['no verified attestation subject matches this bundle digest '
                                            '(tampered bundle or another artifact)'],
                'trust': 'attestation present; subject mismatch'}
    findings = [_certificate_findings(result, bundle, policy) for result in matched]
    if any(not finding for finding in findings):
        return {'result': PASS, 'reasons': [],
                'trust': f"GitHub artifact attestation verified for {bundle['repository']}@{bundle['commit']}"}
    return {'result': FAIL, 'reasons': findings[0], 'trust': 'attestation present; certificate mismatch'}


def main(argv):
    parser = argparse.ArgumentParser(description='Verify a compat evidence bundle against gh attestation output.')
    parser.add_argument('bundle')
    parser.add_argument('--repo', required=True, help='owner/repo expected to have signed the bundle')
    parser.add_argument('--signer-workflow', required=True, help='path of the signing workflow in the repo')
    parser.add_argument('--ref', default='refs/heads/main', help='git ref the signing workflow ran from')
    parser.add_argument('--commit', help='optional expected 40-hex commit SHA (from the checkout, not env)')
    parser.add_argument('--attestation', help='saved output of gh attestation verify --format json')
    args = parser.parse_args(argv[1:])
    with open(args.bundle, 'rb') as handle:
        bundle_bytes = handle.read()
    attestation = None
    if args.attestation:
        with open(args.attestation, encoding='utf-8') as handle:
            attestation = json.load(handle)
    policy = {'repository': args.repo, 'signer_workflow': args.signer_workflow, 'ref': args.ref,
              'commit': args.commit}
    result = verify(bundle_bytes, attestation, policy)
    print(json.dumps(result, indent=2))
    return 0 if result['result'] == PASS else 1


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
