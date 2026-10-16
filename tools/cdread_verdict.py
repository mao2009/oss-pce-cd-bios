"""Trusted CI producer of the CD_READ verdict (Issue #156). Schema: docs/verdict-schema.md v1 (owned by #153).

Runs only in api-source.yml job verify-cdread-attestation (push to protected main), after gh attestation verify.
It turns the #125 verifier result, the workflow run record and main's active branch rules into verdict bytes that
the next job (attest-cdread-verdict, no repository code) signs with actions/attest-build-provenance. The verdict
binds issuer (repository, ref, producer workflow/run), commit, evidence/ROM/fixture digests and the Geargrafx pin;
the attestation over the verdict bytes binds the same issuer and commit in its certificate.

The verdict JSON is not evidence by itself: the consumer (#153) runs gh attestation verify on it and cross-checks.
The producer writes T1-T5 from the *authenticated* #125 evidence and the approval boundary (transport_claims): a
claim is VERIFIED only when every scenario it cites is present in the attested evidence with its required kind
(cd_read_contract.SCENARIO_KIND) AND main requires >= 1 approving review. Otherwise it stays BLOCKED. With
protect-main at 0 approvals (#124 plan C) the verdict is always BLOCKED, and T6-T8 / the callee ABI stay unverified.
The consumer independently re-checks the signature, the pins and the same approval boundary, so it stays BLOCKED
until VERIFIED_PATH_ENABLED is set in a reviewed PR (docs/verdict-schema.md section 3). Facts and authenticity are
separate: the scenario coverage says what holds, the verdict's signature says who produced it.

  python3 tools/cdread_verdict.py EVIDENCE --rom ROM --attestation VERIFY.json --run RUN.json \
      --rules RULES.json --out cdread-verdict.json
Exit 0 = verdict written. Exit 1 = no verdict may be issued (not a #125 PASS, other run, unreadable rules).
Exit 2 = refused outside the trusted CI context. Nothing is written on exit 1 or 2 (the consumer sees BLOCKED).
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cd_read_contract as crc  # noqa: E402
import evidence_provenance as ep  # noqa: E402
from provenance_guard import load_denylist  # noqa: E402

SCHEMA = 'oss-pce-cd-bios/cdread-verdict/v1'
TIME_FORMAT = '%Y-%m-%dT%H:%M:%SZ'
VALIDITY = timedelta(days=7)  # schema allows at most 30 days
PROMOTABLE = ('T1', 'T2', 'T3', 'T4', 'T5')  # only the transport claims; T6-T8 and the callee ABI stay declared


class Refused(Exception):
    """The producer is not running in the trusted main CI context."""


def check_env(env, policy):
    """Refuse unless env says push to main of policy repository, api-source.yml, at the checkout commit.

    Env is not proof (the attestation over the verdict is); this only keeps PR/fork/dispatch runs from producing one.
    """
    repo = policy['repository']
    want = {'GITHUB_ACTIONS': 'true', 'GITHUB_EVENT_NAME': 'push', 'GITHUB_REF': ep.TRUSTED_REF,
            'GITHUB_REPOSITORY': repo, 'GITHUB_SHA': policy['commit'],
            'GITHUB_WORKFLOW_REF': f"{repo}/{policy['signer_workflow']}@{ep.TRUSTED_REF}"}
    bad = sorted(k for k, v in want.items() if env.get(k) != v)
    bad += [k for k in ('GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT') if not str(env.get(k, '')).isdigit()]
    if bad:
        raise Refused(f'not the trusted main CI producer context: {", ".join(bad)}')


def required_approvals(branch_rules):
    """Highest required_approving_review_count among pull_request rules, or None when unreadable."""
    if not isinstance(branch_rules, list):
        return None
    counts = [ep._get(rule, 'parameters', 'required_approving_review_count')
              for rule in branch_rules if ep._get(rule, 'type') == 'pull_request']
    if not counts or any(type(c) is not int for c in counts):
        return None
    return max(counts)


def transport_claims(artifact, approvals):
    """T1-T5 status for the verdict, derived from the authenticated #125 evidence, not from a ledger copy.

    Source of facts: the `tests` table of the artifact the caller just verified as a #125 PASS. A claim is VERIFIED
    only when every scenario it cites is present with the kind cd_read_contract.SCENARIO_KIND requires, AND main
    requires >= 1 approving review. Missing coverage, a re-kind, or < 1 approval leaves it BLOCKED. T6-T8 keep the
    ledger's declared status. This is what makes the consumer's promotion path reachable without inventing evidence;
    the verdict's signature, not this function, is what later vouches for who produced it.
    """
    claims = crc.compat_status()  # T1-T5 BLOCKED, T6-T8 declared: never a promotion on its own
    tests = artifact.get('tests') if isinstance(artifact, dict) else None
    if approvals < 1 or not isinstance(tests, dict):
        return claims
    for claim in crc.TRANSPORT_CLAIMS:
        cid, scenarios = claim['id'], claim['scenarios']
        if cid not in PROMOTABLE or claim['status'] != 'VERIFIED':
            continue
        covered = bool(scenarios) and all(
            scenario in crc.SCENARIO_KIND and isinstance(tests.get(scenario), dict)
            and tests[scenario].get('status') == crc.SCENARIO_KIND[scenario]
            for scenario in scenarios)
        claims[cid] = 'VERIFIED' if covered else 'BLOCKED'
    return claims


def produce(evidence_bytes, rom_bytes, attestation, run, branch_rules, env, policy, fixture, now=None):
    """Verdict dict (docs/verdict-schema.md v1). Raises Refused whenever no verdict may be issued.

    A verdict exists only for a #125 PASS of this run at this commit with readable main rules. T1-T5 come from
    transport_claims() (the authenticated evidence's scenario coverage plus the >= 1 approval boundary), so with
    protect-main at 0 approvals the verdict stays BLOCKED.
    """
    check_env(env, policy)
    try:
        prov = ep.verify(evidence_bytes, attestation, policy, rom_bytes, run, fixture)
    except Exception as error:  # fail closed: a verifier crash is never evidence
        raise Refused(f'#125 verifier crashed: {type(error).__name__}') from error
    if prov.get('result') != ep.PASS:
        raise Refused(f"#125 provenance result {prov.get('result')}: {prov.get('reasons')}")
    if str(run.get('id')) != env['GITHUB_RUN_ID'] or str(run.get('run_attempt')) != env['GITHUB_RUN_ATTEMPT']:
        raise Refused('workflow run record is not the producing run attempt')
    approvals = required_approvals(branch_rules)
    if approvals is None:
        raise Refused('main branch rules unreadable: required approval count unknown')
    now = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    artifact = json.loads(evidence_bytes)
    return {
        'schema': SCHEMA,
        'repository': policy['repository'],
        'commit': policy['commit'],
        'ref': ep.TRUSTED_REF,
        'producer': {'workflow': policy['signer_workflow'], 'run_id': int(env['GITHUB_RUN_ID']),
                     'run_attempt': int(env['GITHUB_RUN_ATTEMPT'])},
        'issued_at': now.strftime(TIME_FORMAT),
        'expires_at': (now + VALIDITY).strftime(TIME_FORMAT),
        'geargrafx': {'revision': policy['geargrafx_revision'], 'source_archive_sha256': policy['geargrafx_sha256']},
        'evidence': {'artifact_sha256': hashlib.sha256(evidence_bytes).hexdigest(),
                     'rom_sha256': hashlib.sha256(rom_bytes).hexdigest(),
                     'bin_sha256': artifact['disc']['bin_sha256'], 'cue_sha256': artifact['disc']['cue_sha256']},
        'provenance': {'result': ep.PASS, 'verifier': 'tools/evidence_provenance.py'},
        'main_rules': {'source': 'github-api', 'required_approving_review_count': approvals},
        'claims': transport_claims(artifact, approvals),
    }


def encode(verdict):
    """Canonical verdict bytes: these exact bytes are the attested subject."""
    return (json.dumps(verdict, sort_keys=True, separators=(',', ':'), ensure_ascii=True) + '\n').encode('ascii')


def main(argv=None):
    parser = argparse.ArgumentParser(description='Produce the CD_READ verdict in trusted main CI (Issue #156).')
    parser.add_argument('evidence')
    parser.add_argument('--rom')
    parser.add_argument('--attestation', help='gh attestation verify --format json output')
    parser.add_argument('--run', help='gh api repos/R/actions/runs/ID/attempts/N output')
    parser.add_argument('--rules', help='gh api repos/R/rules/branches/main output')
    parser.add_argument('--out', required=True)
    args = parser.parse_args(argv)
    try:
        commit = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=crc.ROOT, capture_output=True, text=True,
                                check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        commit = ''
    policy = {'repository': crc.REPOSITORY, 'commit': commit, 'signer_workflow': ep.SIGNER_WORKFLOW,
              'geargrafx_revision': crc.PINNED['revision'], 'geargrafx_sha256': crc.PINNED['sha256'],
              'denied_sha256': load_denylist(crc.ROOT)}
    try:
        check_env(os.environ, policy)
    except Refused as error:
        print(f'refused: {error}', file=sys.stderr)
        return 2
    evidence = Path(args.evidence).read_bytes()

    def load(path):
        return ep._load_json(path) if path else None
    try:
        rom = Path(args.rom).read_bytes() if args.rom else None
    except OSError:
        rom = None
    attestation = load(args.attestation)
    if args.attestation and attestation is None:
        attestation = []  # unreadable gh output -> BLOCKED, never LOCAL_ONLY
    try:
        verdict = produce(evidence, rom, attestation, load(args.run), load(args.rules), os.environ, policy,
                          ep.recompute_fixture())
    except Refused as error:
        print(f'no verdict issued (consumer sees BLOCKED): {error}', file=sys.stderr)
        return 1
    Path(args.out).write_bytes(encode(verdict))
    print(f"verdict sha256={hashlib.sha256(encode(verdict)).hexdigest()} claims={verdict['claims']} "
          f"main approvals={verdict['main_rules']['required_approving_review_count']}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
