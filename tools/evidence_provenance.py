"""Evidence provenance verifier (Issue #124): binds a Geargrafx evidence artifact to a GitHub attestation.

The artifact is build/<mode>/cdread-probe/cdread-evidence.json, written by tests/integration/geargrafx_cdread.py
inside the BIOS DEV workflow (api-source.yml). That workflow signs it with actions/attest-build-provenance.
CI then runs (protected main only, see api-source.yml):
  gh attestation verify ARTIFACT --repo OWNER/REPO --signer-workflow .github/workflows/api-source.yml \
      --format json > VERIFY.json
  python3 tools/evidence_provenance.py ARTIFACT --repo OWNER/REPO --commit SHA --attestation VERIFY.json

verify() takes the gh result as an injected input and never runs gh or the network. Self-declared JSON fields,
hashes, and environment variables (GITHUB_ACTIONS, GITHUB_SHA, ...) are never proof of origin.

Results:
  PASS       the artifact content is consistent (pinned Geargrafx, scenarios PASS, ROM hash) AND a verified
             attestation whose subject is this exact file was produced by the protected main branch, the signing
             workflow, the expected repository and commit, on a GitHub-hosted runner via push or workflow_dispatch.
  FAIL       content contradicts the pins, OR the attested producer is not the trusted one (PR head, fork,
             other workflow/ref/repo/commit), OR the digest does not match.
  BLOCKED    an attestation is required but the gh output is missing or unusable.
  LOCAL_ONLY no attestation: local, unsigned, or forged JSON. Never compatibility evidence.

PASS here means attested diagnostic evidence only. It does not promote any compatibility stage.
Not checked: branch protection on main (a repository setting, not visible in the attestation), and whether a
PR-added workflow was reviewed before it was merged to main (human review control).
"""
import argparse
import hashlib
import json
import re
import sys

PASS, FAIL, BLOCKED, LOCAL_ONLY = 'PASS', 'FAIL', 'BLOCKED', 'LOCAL_ONLY'
TRUSTED_TRIGGERS = ('push', 'workflow_dispatch')
# Positive scenarios of tests/integration/geargrafx_cdread.py; each must be PASS in the artifact.
REQUIRED_SCENARIOS = ('single_sector_lba5', 'first_sector_lba0', 'last_sector_lba15',
                      'three_sectors_lba5', 'disc_end_boundary_lba13_x3')
HEX40 = re.compile(r'[0-9a-f]{40}')
HEX64 = re.compile(r'[0-9a-f]{64}')
REPO = re.compile(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+')
LOCAL_TRUST = 'LOCAL_ONLY: no GitHub artifact attestation; local or unsigned output is not evidence'


def artifact_findings(artifact, policy, rom_bytes=None):
    """Content checks: the artifact must reproduce the pinned Geargrafx and the required scenarios."""
    if not isinstance(artifact, dict):
        return ['artifact is not a JSON object']
    errors = []
    if artifact.get('status') != 'PASS':
        errors.append('artifact status is not PASS')
    if artifact.get('geargrafx_revision') != policy['geargrafx_revision']:
        errors.append('geargrafx_revision differs from the pinned tools/dependencies.json revision')
    if artifact.get('source_archive_sha256') != policy['geargrafx_sha256']:
        errors.append('source_archive_sha256 differs from the pinned Geargrafx archive hash')
    rom_sha = artifact.get('rom_sha256')
    if not isinstance(rom_sha, str) or not HEX64.fullmatch(rom_sha):
        errors.append('rom_sha256 missing or not SHA-256')
    elif rom_bytes is not None and hashlib.sha256(rom_bytes).hexdigest() != rom_sha:
        errors.append('ROM file does not match rom_sha256')
    disc = artifact.get('disc') or {}
    hashes = {rom_sha, disc.get('bin_sha256'), disc.get('cue_sha256')}
    if hashes & policy.get('denied_sha256', set()):
        errors.append('ROM or disc sha256 is on the denylist')
    tests = artifact.get('tests')
    if not isinstance(tests, dict):
        errors.append('tests missing')
    else:
        for name in REQUIRED_SCENARIOS:
            if (tests.get(name) or {}).get('status') != 'PASS':
                errors.append(f'required scenario {name} is not PASS')
        bad = sorted(name for name, item in tests.items()
                     if isinstance(item, dict) and item.get('status') in ('FAIL', 'BLOCKED'))
        if bad:
            errors.append(f"scenarios FAIL or BLOCKED: {', '.join(bad)}")
    runs = (artifact.get('primary_observations') or {}).get('RUN')
    if not isinstance(runs, list) or len(runs) != 2:
        errors.append('primary RUN observations must be exactly cold and warm')
    return errors


def _statements(attestation):
    """Return verificationResult objects from gh attestation verify --format json, or None if unusable."""
    if not isinstance(attestation, list) or not attestation:
        return None
    results = [entry.get('verificationResult') for entry in attestation if isinstance(entry, dict)]
    results = [result for result in results if isinstance(result, dict)]
    return results or None


def _subject_digests(result):
    subjects = (result.get('statement') or {}).get('subject') or []
    return {(subject.get('digest') or {}).get('sha256') for subject in subjects if isinstance(subject, dict)}


def certificate_findings(result, policy):
    """Producer identity: the attested run must be the protected main branch's signing workflow."""
    certificate = (result.get('signature') or {}).get('certificate') or {}
    repo, commit, ref = policy['repository'], policy['commit'], policy['ref']
    findings = []
    if certificate.get('sourceRepositoryURI') != f'https://github.com/{repo}':
        findings.append('attested source repository differs from policy repository')
    if certificate.get('sourceRepositoryDigest') != commit:
        findings.append('attested commit SHA differs from checkout commit')
    if certificate.get('sourceRepositoryRef') != ref:
        findings.append(f"attested source ref is not {ref} (PR head, fork or other branch)")
    signer = f"https://github.com/{repo}/{policy['signer_workflow']}@{ref}"
    if certificate.get('buildSignerURI') != signer:
        findings.append('signing workflow identity differs from policy')
    trigger = certificate.get('githubWorkflowTrigger', certificate.get('buildTrigger'))
    if trigger not in TRUSTED_TRIGGERS:
        findings.append(f'producer trigger {trigger!r} is not a trusted push or workflow_dispatch')
    if certificate.get('runnerEnvironment') != 'github-hosted':
        findings.append('producer did not run on a GitHub-hosted runner')
    return findings


def verify(artifact_bytes, attestation, policy, rom_bytes=None):
    """Return {'result', 'reasons', 'trust'} for the artifact bytes and the injected gh verify output.

    policy: {'repository', 'commit' (checkout SHA), 'signer_workflow', 'ref', 'geargrafx_revision',
             'geargrafx_sha256', 'denied_sha256' (optional set)}
    attestation: parsed JSON from `gh attestation verify --format json`, or None for a local run.
    """
    try:
        artifact = json.loads(artifact_bytes.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        return {'result': FAIL, 'reasons': [f'artifact is not JSON: {error}'], 'trust': LOCAL_TRUST}
    errors = artifact_findings(artifact, policy, rom_bytes)
    if errors:
        return {'result': FAIL, 'reasons': errors, 'trust': LOCAL_TRUST}
    if attestation is None:
        return {'result': LOCAL_ONLY, 'reasons': ['no attestation supplied'], 'trust': LOCAL_TRUST}
    results = _statements(attestation)
    if results is None:
        return {'result': BLOCKED, 'reasons': ['gh attestation output is empty or not a verification array'],
                'trust': 'attestation required; not available'}
    digest = hashlib.sha256(artifact_bytes).hexdigest()
    matched = [result for result in results if digest in _subject_digests(result)]
    if not matched:
        return {'result': FAIL, 'reasons': ['no verified attestation subject matches this artifact digest '
                                            '(forged, tampered, or another artifact)'],
                'trust': 'attestation present; subject mismatch'}
    findings = [certificate_findings(result, policy) for result in matched]
    if any(not finding for finding in findings):
        return {'result': PASS, 'reasons': [],
                'trust': f"attested by protected {policy['ref']} producer {policy['signer_workflow']} "
                         f"at {policy['commit']}"}
    return {'result': FAIL, 'reasons': findings[0], 'trust': 'attestation present; producer not trusted'}


def main(argv):
    parser = argparse.ArgumentParser(description='Verify a Geargrafx evidence artifact against gh attestation output.')
    parser.add_argument('artifact')
    parser.add_argument('--repo', required=True, help='owner/repo expected to have produced the artifact')
    parser.add_argument('--commit', required=True, help='40-hex checkout commit (git rev-parse HEAD)')
    parser.add_argument('--signer-workflow', default='.github/workflows/api-source.yml')
    parser.add_argument('--ref', default='refs/heads/main', help='protected ref the producer must have run from')
    parser.add_argument('--deps', default='tools/dependencies.json', help='pinned dependencies at the checkout')
    parser.add_argument('--attestation', help='saved output of gh attestation verify --format json')
    parser.add_argument('--rom', help='ROM file whose sha256 must equal rom_sha256')
    args = parser.parse_args(argv[1:])
    if not HEX40.fullmatch(args.commit) or not REPO.fullmatch(args.repo):
        print('refused: --commit must be a 40-hex SHA and --repo owner/repo', file=sys.stderr)
        return 2
    with open(args.deps, encoding='utf-8') as handle:
        geargrafx = json.load(handle)['geargrafx']
    with open(args.artifact, 'rb') as handle:
        artifact_bytes = handle.read()
    rom_bytes = None
    if args.rom:
        with open(args.rom, 'rb') as handle:
            rom_bytes = handle.read()
    attestation = None
    if args.attestation:
        with open(args.attestation, encoding='utf-8') as handle:
            attestation = json.load(handle)
    policy = {'repository': args.repo, 'commit': args.commit, 'signer_workflow': args.signer_workflow,
              'ref': args.ref, 'geargrafx_revision': geargrafx['revision'], 'geargrafx_sha256': geargrafx['sha256']}
    result = verify(artifact_bytes, attestation, policy, rom_bytes)
    print(json.dumps(result, indent=2))
    return 0 if result['result'] == PASS else 1


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
