"""Versioned compatibility evidence schema and fail-closed verdict (Issue #7).

A record claims one stage. verdict() never trusts the claimed result: any
missing input yields BLOCKED/SKIP, any unsupported or contradicted claim yields
FAIL, and PASS requires every rule for the claimed stage to hold.

Limit: the runner output (attestation and raw probe output) is plain unsigned JSON. verdict()
checks that it is consistent with the record and the #119 rules; it does not authenticate
who produced it. A PASS is therefore a consistency result, not a certification.
"""
import json
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests' / 'integration'))
import geargrafx_harness  # noqa: E402  (#119 trace judge: classify() and parse_records(); the only stage judge)

SCHEMA = 'pce-compat-evidence-v1'
STAGES = ('source-only', 'rom-built', 'bios-loaded', 'startup', 'cd-detected', 'homebrew',
          'retail-boot', 'playable', 'save-tested', 'completion-tested')
RANK = {stage: index for index, stage in enumerate(STAGES)}
RESULTS = ('PASS', 'FAIL', 'SKIP', 'BLOCKED')
WORST_FIRST = ('FAIL', 'BLOCKED', 'SKIP', 'PASS')
ORIGINS = ('original-synthetic', 'local-proprietary')
EMULATOR_KINDS = ('real-core', 'mock', 'static')
SCOPES = ('single-launch', 'title')
MOCK_NAME = re.compile(r'mock|stub|fake|static|dummy|simulat', re.I)
HEX64 = re.compile(r'[0-9a-f]{64}')
HEX40 = re.compile(r'[0-9a-f]{40}')

ROM_STAGE = RANK['rom-built']
CORE_STAGE = RANK['bios-loaded']
CD_STAGE = RANK['cd-detected']
CHECK_STAGE = RANK['startup']
HOMEBREW_STAGE = RANK['homebrew']
RETAIL_STAGE = RANK['retail-boot']

# Stage labels for the four retail-and-above stages. They are defined HERE (module-defined), not in
# tests/integration/geargrafx_harness.py; a probe must be run with this value as --expect to be judged.
# No shipped producer emits them yet (no #119 retail probe exists), so these stages are BLOCKED or SKIP.
STAGE_LABEL = {'retail-boot': 'retail-boot-reached', 'playable': 'playable-reached',
               'save-tested': 'save-reached', 'completion-tested': 'completion-reached'}
# Labels that always fail a stage's probe, added to whatever forbid list the runner passes.
FORBID_MIN = ('cd_halt_fail',)
AUTHENTICITY = 'not verified: runner JSON is unsigned and unauthenticated; this output is not a certification'
TRUST_LOCAL = 'local-only, untrusted (unsigned JSON; not certifiable)'
TRUST_CI = ('ci-job context (GITHUB_ACTIONS=true is an environment variable any process can set; '
            'the JSON is still unsigned, so this is not a certification)')


def _is_hex(value, pattern):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _is_name(value):
    return isinstance(value, str) and value != ''


def _list_field(container, key):
    """A missing or null field is absent ([]); any other value is kept so that validation can reject it."""
    value = container.get(key)
    return [] if value is None else value


def validate(record):
    """Return a list of schema errors; an empty list means structurally valid."""
    if not isinstance(record, dict):
        return ['record is not an object']
    errors = []
    if record.get('schema') != SCHEMA:
        errors.append(f'schema must be {SCHEMA}')
    if record.get('stage') not in STAGES:
        errors.append('unknown stage')
    claimed = record.get('result')
    if claimed not in RESULTS:
        errors.append('unknown result')
    if record.get('claim_scope') == 'all-titles':
        errors.append('all-title compatibility cannot be claimed from launch evidence')
    elif record.get('claim_scope') not in SCOPES:
        errors.append('claim_scope must be single-launch or title')
    if claimed in ('BLOCKED', 'SKIP') and not record.get('blocker'):
        errors.append('BLOCKED/SKIP requires a blocker reason')
    rom = record.get('rom')
    if rom is not None and not (isinstance(rom, dict) and _is_hex(rom.get('sha256'), HEX64)):
        errors.append('rom.sha256 missing or not SHA-256')
    emulator = record.get('emulator')
    if emulator is not None:
        if not isinstance(emulator, dict) or emulator.get('kind') not in EMULATOR_KINDS:
            errors.append('emulator.kind must be real-core, mock or static')
        elif emulator['kind'] == 'real-core':
            if not isinstance(emulator.get('name'), str) or not emulator['name']:
                errors.append('real-core emulator name missing')
            elif MOCK_NAME.search(emulator['name']):
                errors.append('mock/static source marked as real-core')
            if not emulator.get('version'):
                errors.append('real-core emulator version missing')
            if not _is_hex(emulator.get('commit'), HEX40):
                errors.append('real-core emulator commit missing or not Git SHA-1')
    fixture = record.get('fixture')
    if fixture is not None:
        if not isinstance(fixture, dict) or not fixture.get('id'):
            errors.append('fixture.id missing')
        elif not _is_hex(fixture.get('sha256'), HEX64):
            errors.append('fixture.sha256 missing or not SHA-256')
        elif fixture.get('origin') not in ORIGINS:
            errors.append('fixture.origin must be original-synthetic or local-proprietary')
    checkpoints = _list_field(record, 'checkpoints')
    if not isinstance(checkpoints, list):
        errors.append('checkpoints must be an array')
    elif any(not isinstance(item, dict) or not _is_name(item.get('name')) or 'expected' not in item
             or 'observed' not in item for item in checkpoints):
        errors.append('checkpoint needs name, expected and observed')
    trace = _list_field(record, 'trace_deltas')
    if not isinstance(trace, list):
        errors.append('trace_deltas must be an array')
    elif any(not isinstance(item, dict) or not _is_name(item.get('name')) or 'delta' not in item for item in trace):
        errors.append('trace delta needs name and delta')
    return errors


def _attestation(record, runner):
    """Check that a runner attestation is consistent with the record.

    The record's own fields (including any 'runner' key) are never used as the attestation.
    The attestation is unsigned: this checks consistency, not who produced it.
    ponytail: no signature; upgrade to signed attestation (Issue #124, PR #125 branch
    feat/124-evidence-provenance) if the gate is shared.
    """
    if not isinstance(runner, dict) or runner.get('verified') is not True:
        return [('BLOCKED', 'independent runner attestation required')]
    rom = record.get('rom') or {}
    fixture = record.get('fixture') or {}
    emulator = record.get('emulator') or {}
    findings = []
    if runner.get('rom_sha256') != rom.get('sha256'):
        findings.append(('FAIL', 'runner ROM hash does not match record'))
    if fixture and runner.get('fixture_sha256') != fixture.get('sha256'):
        findings.append(('FAIL', 'runner fixture hash does not match record'))
    if runner.get('emulator_commit') != emulator.get('commit'):
        findings.append(('FAIL', 'runner emulator commit does not match record'))
    runner_checkpoints = _list_field(runner, 'checkpoints')
    if not isinstance(runner_checkpoints, list):
        return findings + [('FAIL', 'runner checkpoints must be an array')]
    names = [item.get('name') for item in runner_checkpoints if isinstance(item, dict) and _is_name(item.get('name'))]
    if len(names) != len(set(names)):
        findings.append(('FAIL', 'runner checkpoint names are duplicated'))
    observed = {item.get('name'): item.get('observed')
                for item in runner_checkpoints if isinstance(item, dict) and _is_name(item.get('name'))}
    for item in _list_field(record, 'checkpoints'):
        if item['name'] not in observed or observed[item['name']] != item['expected']:
            findings.append(('FAIL', f"checkpoint {item['name']} not confirmed by runner"))
    return findings


def _stage_findings(stage, probe):
    """Stage evidence for retail-boot / playable / save-tested / completion-tested.

    The only accepted proof is the raw #119 probe output: probe['result'] is the run_job() result
    (status, reason, exit_code, stdout). #119 classify() judges that stdout, and parse_records()
    reads the TAG lines itself, so a runner summary or a body-level 'tag' cannot stand in for it.
    The forbid list is the runner's, plus FORBID_MIN, which it cannot drop. A missing probe is
    BLOCKED. Anything classify() does not call PASS keeps its own status (FAIL, BLOCKED or SKIP).
    A stage with no #119 label (bios-loaded to homebrew) has no judge, so it is always BLOCKED.
    The raw output is unsigned, so this is a consistency check, not a certification.
    """
    if stage not in STAGE_LABEL:
        return [('BLOCKED', f'no #119 judge label for {stage}; PASS needs a judged probe, which none defines yet')]
    if not isinstance(probe, dict) or not isinstance(probe.get('result'), dict):
        return [('BLOCKED', f'raw #119 probe output required for {stage}')]
    result, budget, forbid = probe['result'], probe.get('budget'), probe.get('forbid')
    kind = probe.get('kind', 'cdread')  # absent kind defaults to cdread, the only kind #119 judges
    if result.get('status') not in (None, 'FAIL', 'BLOCKED'):
        return [('FAIL', 'probe status must be absent, FAIL or BLOCKED; the judge sets it, not the runner')]
    if not _is_int(budget) or budget < 1:
        return [('FAIL', 'probe budget must be a positive integer')]
    if not isinstance(forbid, list) or not all(isinstance(label, str) for label in forbid):
        return [('FAIL', 'probe forbid must be a list of labels')]
    labels = tuple(sorted(set(forbid) | set(FORBID_MIN)))
    try:
        status, reason, _ = geargrafx_harness.classify(result, STAGE_LABEL[stage], labels, budget, kind)
    except (KeyError, TypeError, ValueError, AttributeError, RecursionError) as error:
        # RecursionError: #119 parse_records() json.loads() on deeply nested stdout (not caught by classify()).
        return [('FAIL', f'probe output rejected: {type(error).__name__}: {error}')]
    return [] if status == 'PASS' else [(status, reason)]


def verdict(record, runner=None, probe=None):
    """Return {'result', 'blocker', 'reasons'} for a record; never PASS on inconsistent evidence.

    `runner` is the runner attestation, checked for consistency with the record. `probe` is the raw
    #119 probe output for retail-boot and above (see _stage_findings). Both are unsigned, so a PASS
    here is not a compatibility certification. Without an attestation, any stage that claims
    execution (bios-loaded and above) cannot PASS.
    """
    errors = validate(record)
    if errors:
        return {'result': 'FAIL', 'blocker': None, 'reasons': errors}
    rank = RANK[record['stage']]
    findings = []  # (result, reason)
    rom, fixture, emulator = record.get('rom'), record.get('fixture'), record.get('emulator')
    if rank >= CORE_STAGE:
        findings.extend(_attestation(record, runner))
    if rank >= ROM_STAGE and not rom:
        findings.append(('BLOCKED', 'ROM not built'))
    if rank >= CD_STAGE and fixture is None:
        # Local proprietary discs are opt-in; anything else is a missing input.
        findings.append(('SKIP' if rank >= RETAIL_STAGE else 'BLOCKED', 'fixture missing'))
    if rank >= CORE_STAGE and (not emulator or emulator.get('kind') != 'real-core'):
        findings.append(('BLOCKED', 'real-core emulator evidence required'))
    if rank >= RETAIL_STAGE and fixture is not None:
        if fixture['origin'] != 'local-proprietary':
            findings.append(('FAIL', 'retail-boot claimed with a synthetic fixture'))
        if not record.get('region'):
            findings.append(('BLOCKED', 'region unknown'))
    if rank >= CHECK_STAGE:
        checkpoints = _list_field(record, 'checkpoints')
        if not checkpoints:
            findings.append(('BLOCKED', 'no checkpoints observed'))
        for item in checkpoints:
            if item['expected'] != item['observed']:
                findings.append(('FAIL', f"checkpoint {item['name']} mismatch"))
    if rank > HOMEBREW_STAGE and not _list_field(record, 'trace_deltas'):
        findings.append(('BLOCKED', 'guest trace deltas required above homebrew'))
    # A retail-stage record with no local fixture gets no probe check (opt-in disc, nothing was run);
    # its SKIP finding applies only if no other finding is BLOCKED or FAIL (BLOCKED outranks SKIP).
    if rank >= CORE_STAGE and not (fixture is None and rank >= RETAIL_STAGE):
        findings.extend(_stage_findings(record['stage'], probe))
        if record['stage'] in STAGE_LABEL and not any(
                isinstance(item, dict) and item.get('name') == STAGE_LABEL[record['stage']]
                for item in _list_field(record, 'checkpoints')):
            findings.append(('BLOCKED', f"stage checkpoint {STAGE_LABEL[record['stage']]!r} missing from record"))
    if findings:
        computed = min((result for result, _ in findings), key=WORST_FIRST.index)
    else:
        computed = 'PASS'
    reasons = [reason for _, reason in findings]
    blocker = next((reason for result, reason in findings if result in ('BLOCKED', 'SKIP')), None)
    if record['result'] == 'PASS' and computed != 'PASS':
        # The reporter claimed more than the evidence supports.
        reasons.insert(0, f"overclaim: {record['stage']} claimed PASS, evidence supports {computed}")
        computed = 'FAIL'
    elif computed == 'PASS' and record['result'] != 'PASS':
        # Complete evidence never overrides the reporter's own BLOCKED/SKIP/FAIL claim.
        reasons.insert(0, f"conflict: reporter claimed {record['result']}, evidence supports PASS; kept {record['result']}")
        computed, blocker = record['result'], record.get('blocker')
    elif record['result'] != 'PASS' and computed != record['result']:
        # Informational only: keep why the reporter's non-PASS claim differs from the verdict (Issue #130).
        reasons.insert(0, f"conflict: reporter claimed {record['result']}, evidence supports {computed}")
    if computed not in ('BLOCKED', 'SKIP'):
        blocker = None  # a blocker reason is reported only when the overall result is BLOCKED or SKIP
    return {'result': computed, 'blocker': blocker, 'reasons': reasons}


def _load_json(path):
    with open(path, encoding='utf-8') as handle:
        return json.load(handle)


def main(argv):
    """CLI: `[--ci] RECORD.json [RUNNER.json [PROBE.json]]`. The input is unsigned JSON in every mode.
    PROBE.json is the raw #119 run_job() result plus forbid/budget/kind (see _stage_findings).

    A local PASS for an execution stage is printed as BLOCKED (local_verdict keeps the raw result).
    --ci is accepted only when GITHUB_ACTIONS=true; that variable is not authentication, so --ci
    output still says it is not a certification (see AUTHENTICITY).
    """
    ci = '--ci' in argv[1:]
    args = [arg for arg in argv[1:] if arg != '--ci']
    if len(args) not in (1, 2, 3):
        print('usage: compat_evidence.py [--ci] RECORD.json [RUNNER_ATTESTATION.json [PROBE.json]]', file=sys.stderr)
        return 2
    if ci and os.environ.get('GITHUB_ACTIONS') != 'true':
        print('refused: --ci is accepted only inside GitHub Actions', file=sys.stderr)
        return 2
    try:
        runner = _load_json(args[1]) if len(args) >= 2 else None
        probe = _load_json(args[2]) if len(args) == 3 else None
        record = _load_json(args[0])
    except (OSError, ValueError, RecursionError) as error:  # unreadable, malformed or too deeply nested: FAIL, not a traceback
        failure = {'result': 'FAIL', 'blocker': None, 'reasons': [f'input unreadable: {error}'],
                   'local_verdict': 'FAIL', 'trust': TRUST_CI if ci else TRUST_LOCAL, 'authenticity': AUTHENTICITY}
        print(json.dumps(failure, indent=2))
        return 1
    local = verdict(record, runner, probe)
    result = dict(local, local_verdict=local['result'], trust=TRUST_CI if ci else TRUST_LOCAL,
                  authenticity=AUTHENTICITY)
    executed = isinstance(record, dict) and isinstance(record.get('stage'), str) \
        and RANK.get(record['stage'], -1) >= CORE_STAGE
    if not ci and local['result'] == 'PASS' and executed:
        result.update(result='BLOCKED', blocker='untrusted local input; CI-generated runner evidence required',
                      reasons=['local PASS is not a compatibility certification'] + result['reasons'])
    print(json.dumps(result, indent=2))
    return 0 if result['result'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
