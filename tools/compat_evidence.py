"""Versioned compatibility evidence schema and fail-closed verdict (Issue #7).

A record claims one stage. verdict() never trusts the claimed result: any
missing input yields BLOCKED/SKIP, any unsupported or contradicted claim yields
FAIL, and PASS requires every rule for the claimed stage to hold.
"""
import json
import os
import re
import sys

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

# Stage-specific execution evidence (names and rules mirror the #119 trace judge, geargrafx_harness.classify).
STAGE_LABEL = {'playable': 'playable-reached', 'save-tested': 'save-reached',
               'completion-tested': 'completion-reached'}
REQUIRED_CYCLES = ('cold', 'warm')
CAP_FLAGS = ('instruction_step', 'cdrom_hardware', 'cdrom_media', 'disc_given')
HALT_CONFIRM_STEPS = 64
TRUST_LOCAL = 'local-only, untrusted (unsigned JSON; not certifiable)'
TRUST_CI = 'ci-job runner output (unsigned; trust rests on workflow integrity)'


def _is_hex(value, pattern):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def check_halt_confirmation(run):
    """Same rule as #119 check_halt_confirmation: 64 consecutive halt-loop instructions, fail-closed."""
    cycle = run.get('cycle')
    try:
        confirmed, steps = run['halt_confirmed'], run['halt_confirmation_steps']
        required, arrival, step = run['halt_confirmation_required'], run['halted_after_step'], run['step']
    except KeyError as missing:
        raise ValueError(f'{cycle}: halt confirmation evidence missing ({missing})') from None
    if confirmed is not True:
        raise ValueError(f'{cycle}: halt not confirmed (halt_confirmed={confirmed!r})')
    if required != HALT_CONFIRM_STEPS or steps != HALT_CONFIRM_STEPS:
        raise ValueError(f'{cycle}: halt confirmation covered {steps} of {required} steps, need {HALT_CONFIRM_STEPS}')
    if not _is_int(arrival) or arrival < 0 or not _is_int(step) or step != arrival + steps:
        raise ValueError(f'{cycle}: halt confirmation step accounting inconsistent')


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
    for item in record.get('checkpoints') or []:
        if not isinstance(item, dict) or not item.get('name') or 'expected' not in item or 'observed' not in item:
            errors.append('checkpoint needs name, expected and observed')
            break
    for item in record.get('trace_deltas') or []:
        if not isinstance(item, dict) or not item.get('name') or 'delta' not in item:
            errors.append('trace delta needs name and delta')
            break
    return errors


def _attestation(record, runner):
    """Bind a claimed execution to a runner attestation produced outside the record.

    The record's own fields (including any 'runner' key) are never trusted here.
    ponytail: attestation is a plain dict from the harness, no signature; trust
    ends at the runner producer. Upgrade to signed attestation if the gate is shared.
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
    observed = {item.get('name'): item.get('observed')
                for item in runner.get('checkpoints') or [] if isinstance(item, dict)}
    for item in record.get('checkpoints') or []:
        if item['name'] not in observed or observed[item['name']] != item['expected']:
            findings.append(('FAIL', f"checkpoint {item['name']} not confirmed by runner"))
    return findings


def _stage_findings(stage, runner):
    """Runner-verified stage evidence for playable / save-tested / completion-tested.

    Only runner['stage_evidence'] counts; trace_deltas and any record field never do. Missing
    evidence is BLOCKED. Present but contradicting the #119 rules (exit, HIT order, RUN count,
    halt confirmation, step bounds, probe kind) is FAIL.
    """
    evidence = runner.get('stage_evidence') if isinstance(runner, dict) and runner.get('verified') is True else None
    if not isinstance(evidence, dict):
        return [('BLOCKED', f'stage-specific runner evidence required for {stage}')]
    if evidence.get('stage') != stage:
        return [('FAIL', f"stage evidence is for {evidence.get('stage')!r}, not {stage!r}")]
    findings = []
    expect = STAGE_LABEL[stage]
    if evidence.get('expect') != expect:
        findings.append(('FAIL', f"stage evidence expects {evidence.get('expect')!r}, need {expect!r}"))
    if evidence.get('probe_kind') != 'cdread':
        findings.append(('FAIL', 'probe_kind must be cdread (the api probe emits no RUN records)'))
    exit_code = evidence.get('exit_code')
    if not _is_int(exit_code) or exit_code != 0 \
            or evidence.get('timed_out') is not False or evidence.get('output_capped') is not False:
        findings.append(('FAIL', 'probe did not exit 0 within the time and output limits'))
    cap = evidence.get('cap')
    if not isinstance(cap, dict) or any(cap.get(flag) is not True for flag in CAP_FLAGS):
        findings.append(('BLOCKED', 'incomplete capability record (instruction step, CD hardware, media or disc)'))
    budget = evidence.get('budget')
    if not _is_int(budget) or budget < 1:
        findings.append(('FAIL', 'budget must be a positive integer'))
        budget = None
    runs = evidence.get('runs')
    if not isinstance(runs, list) or not all(isinstance(run, dict) for run in runs):
        return findings + [('FAIL', 'runs must be a list of RUN records')]
    other = [str(run.get('reason')) for run in runs if run.get('reason') != expect]
    if other:
        findings.append(('FAIL', f"run ended without {expect!r}: {', '.join(other)}"))
    if [run.get('cycle') for run in runs] != list(REQUIRED_CYCLES):
        findings.append(('FAIL', f'RUN records must be exactly one cold then one warm, got '
                                 f"{[run.get('cycle') for run in runs]}"))
    for run in runs:
        cycle, step, hits = run.get('cycle'), run.get('step'), run.get('hits_before_run')
        if not _is_int(step) or step < 0 or (budget is not None and step > budget):
            findings.append(('FAIL', f'{cycle}: step must be an integer in 0..{budget}'))
        try:
            check_halt_confirmation(run)
        except ValueError as error:
            findings.append(('FAIL', str(error)))
        if not _is_int(hits) or hits != 1:
            findings.append(('FAIL', f'{cycle}: success HIT missing, repeated or after its RUN'))
    return findings


def verdict(record, runner=None):
    """Return {'result', 'blocker', 'reasons'} for a record; never PASS on bad evidence.

    `runner` is independent attestation (verified, hash-bound run output). Without it,
    any stage that claims execution (bios-loaded and above) cannot PASS.
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
        checkpoints = record.get('checkpoints') or []
        if not checkpoints:
            findings.append(('BLOCKED', 'no checkpoints observed'))
        for item in checkpoints:
            if item['expected'] != item['observed']:
                findings.append(('FAIL', f"checkpoint {item['name']} mismatch"))
    if rank > HOMEBREW_STAGE and not record.get('trace_deltas'):
        findings.append(('BLOCKED', 'guest trace deltas required above homebrew'))
    if record['stage'] in STAGE_LABEL:
        findings.extend(_stage_findings(record['stage'], runner))
        if not any(isinstance(item, dict) and item.get('name') == STAGE_LABEL[record['stage']]
                   for item in record.get('checkpoints') or []):
            findings.append(('BLOCKED', f"stage checkpoint {STAGE_LABEL[record['stage']]!r} missing from record"))
    if findings:
        computed = min((result for result, _ in findings), key=WORST_FIRST.index)
    else:
        computed = 'PASS'
    reasons = [reason for _, reason in findings]
    if record['result'] == 'PASS' and computed != 'PASS':
        # The reporter claimed more than the evidence supports.
        reasons.insert(0, f"overclaim: {record['stage']} claimed PASS, evidence supports {computed}")
        computed = 'FAIL'
    blocker = next((reason for result, reason in findings if result in ('BLOCKED', 'SKIP')), None)
    return {'result': computed, 'blocker': blocker, 'reasons': reasons}


def main(argv):
    """CLI: `[--ci] RECORD.json [RUNNER.json]`. Without --ci the input is local, untrusted JSON.

    A local PASS for an execution stage is printed as BLOCKED (local_verdict keeps the raw result),
    so unsigned JSON never yields a compatibility PASS. --ci is accepted only inside GitHub Actions.
    """
    ci = '--ci' in argv[1:]
    args = [arg for arg in argv[1:] if arg != '--ci']
    if len(args) not in (1, 2):
        print('usage: compat_evidence.py [--ci] RECORD.json [RUNNER_ATTESTATION.json]', file=sys.stderr)
        return 2
    if ci and os.environ.get('GITHUB_ACTIONS') != 'true':
        print('refused: --ci is accepted only inside GitHub Actions', file=sys.stderr)
        return 2
    runner = None
    if len(args) == 2:
        with open(args[1], encoding='utf-8') as handle:
            runner = json.load(handle)
    with open(args[0], encoding='utf-8') as handle:
        record = json.load(handle)
    local = verdict(record, runner)
    result = dict(local, local_verdict=local['result'], trust=TRUST_CI if ci else TRUST_LOCAL)
    executed = isinstance(record, dict) and RANK.get(record.get('stage'), -1) >= CORE_STAGE
    if not ci and local['result'] == 'PASS' and executed:
        result.update(result='BLOCKED', blocker='untrusted local input; CI-generated runner evidence required',
                      reasons=['local PASS is not a compatibility certification'] + result['reasons'])
    print(json.dumps(result, indent=2))
    return 0 if result['result'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
