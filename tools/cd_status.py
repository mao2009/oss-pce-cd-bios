"""Host-side status classifier for the Issue #5 CD sector-read diagnostic (pure, no emulator).

Maps one observed guest result (reason + result block) to PASS / FAIL / NOT_RUN / BLOCKED with a
category and reason. It judges STATUS only; byte-exact data and SCSI-phase checks stay in
tools/cdread_verify.py, which remains the authority for a success: a PASS here is necessary,
never sufficient, and the runner must also accept the same records with check_success().

  FAIL     the guest ran and did not meet the expectation (includes step-budget exhaustion,
           which never completes the 64-step halt confirmation).
  BLOCKED  the record cannot be judged (no media, incomplete or unreadable record).
  NOT_RUN  no RUN record exists at all, so nothing was executed.

Evidence labels (per code):
  MEASURED        exercised by a Geargrafx run in the docs/cd-read-diagnostic.md test matrix.
  SOURCE_DEFINED  defined in src/cd_read_probe.s but never observed; its meaning is HYPOTHESIS.
Categories are classifier labels grouping codes, not codes of the real hardware.
"""
from collections import namedtuple

if __package__:
    from .build_cdread import ERROR_NAMES, SECTOR_BYTES
    from .cdread_verify import check_halt_confirmation, result_block
else:
    from build_cdread import ERROR_NAMES, SECTOR_BYTES
    from cdread_verify import check_halt_confirmation, result_block

PASS, FAIL, NOT_RUN, BLOCKED = 'PASS', 'FAIL', 'NOT_RUN', 'BLOCKED'
MEASURED, SOURCE_DEFINED, HYPOTHESIS = 'MEASURED', 'SOURCE_DEFINED', 'HYPOTHESIS'
MAX_SECTORS = 3  # the buffer holds three sectors (docs/cd-read-diagnostic.md, step 3)
INVALID_REQUEST_STAGE = 1  # RES+1 is set to 1 before the count check, so a refused count halts at stage 1
HALT_OK, HALT_FAIL, BUDGET = 'cd_halt_ok', 'cd_halt_fail', 'budget'
REQUIRED_KEYS = ('reason', 'result_hex', 'halt_confirmed', 'halt_confirmation_steps',
                 'halt_confirmation_required', 'halted_after_step', 'step')

# code -> (category, evidence). Names come from build_cdread.ERROR_NAMES, never duplicated here.
CODES = {
    0: ('SUCCESS', MEASURED),
    1: ('BUS_NOT_FREE', SOURCE_DEFINED),
    2: ('TIMEOUT', MEASURED),
    3: ('TIMEOUT', SOURCE_DEFINED),
    4: ('TIMEOUT', MEASURED),
    5: ('PREMATURE_STATUS', MEASURED),
    6: ('BAD_STATUS', SOURCE_DEFINED),
    7: ('TIMEOUT', SOURCE_DEFINED),
    8: ('BUS_NOT_FREE', SOURCE_DEFINED),
    9: ('INVALID_REQUEST', MEASURED),
    10: ('TIMEOUT', SOURCE_DEFINED),
}

Verdict = namedtuple('Verdict', 'outcome category code name evidence reason transferred')


def _verdict(outcome, category, reason, code=None, evidence=HYPOTHESIS, transferred=None):
    name = ERROR_NAMES.get(code) if code is not None else None
    return Verdict(outcome, category, code, name, evidence, reason, transferred)


def classify_run(run, count, expect=None, media=True):
    """Classify one RUN record. `expect` is an error code (0 = success) the caller asserts; None = must succeed."""
    cycle = run.get('cycle', '?')
    if not media:
        return _verdict(BLOCKED, 'NO_MEDIA', 'no media reported: status codes are not judged without a disc')
    reason = run.get('reason')
    if reason == BUDGET:
        # an executed run that ran out of steps never completed the halt confirmation: a failure, not a skip
        return _verdict(FAIL, 'BUDGET_EXHAUSTED', f'{cycle}: step budget exhausted before the halt was confirmed')
    missing = [key for key in REQUIRED_KEYS if key not in run]
    if missing:
        return _verdict(BLOCKED, 'INCOMPLETE_RECORD', f'{cycle}: missing {missing}')
    if reason not in (HALT_OK, HALT_FAIL):
        return _verdict(FAIL, 'UNEXPECTED_HALT', f'{cycle}: stopped at {reason!r}, not a result halt')
    try:
        check_halt_confirmation(run)
    except ValueError as error:
        return _verdict(FAIL, 'HALT_NOT_CONFIRMED', f'{cycle}: {error}')
    try:
        block = result_block(run)
    except (ValueError, KeyError, IndexError) as error:
        return _verdict(BLOCKED, 'INCOMPLETE_RECORD', f'{cycle}: result block unreadable ({error})')
    code, stage, transferred = block['code'], block['stage'], block['bytes']
    if not 1 <= count <= MAX_SECTORS:
        # the probe refuses this request at stage 1 (src/cd_read_probe.s, @bad_request) with code 9 and no
        # transfer. This checks the single RUN record only; it does not prove the bus was untouched (PHASE/HIT
        # evidence is a separate step).
        if reason != HALT_FAIL or code != 9 or stage != INVALID_REQUEST_STAGE or transferred != 0:
            return _verdict(FAIL, 'PROTOCOL_MISMATCH', f'{cycle}: count {count} outside 1..{MAX_SECTORS} must halt '
                            f'with code 9 at stage {INVALID_REQUEST_STAGE} and 0 bytes, observed code {code} '
                            f'stage {stage} {transferred} bytes', code, MEASURED, transferred)
        return _verdict(PASS if expect == 9 else FAIL, 'INVALID_REQUEST',
                        f'{cycle}: invalid count rejected with code 9', 9, MEASURED, transferred)
    if reason == HALT_OK:
        if (code, block['stage'], block['scsi_status'], block['scsi_message']) != (0, 9, 0, 0) \
                or transferred != count * SECTOR_BYTES:
            return _verdict(FAIL, 'PROTOCOL_MISMATCH', f'{cycle}: success halt but result block is {block}',
                            code, MEASURED, transferred)
        category, evidence = 'SUCCESS', MEASURED
    else:
        if code == 0:
            return _verdict(FAIL, 'PROTOCOL_MISMATCH', f'{cycle}: failure halt with code 0', 0, MEASURED, transferred)
        if code == 9:
            # code 9 is only produced for a refused count (above), so a valid count with code 9 is a protocol
            # mismatch whatever `expect` says
            return _verdict(FAIL, 'PROTOCOL_MISMATCH', f'{cycle}: code 9 (invalid request) for valid count {count}',
                            9, MEASURED, transferred)
        if code not in CODES:
            return _verdict(FAIL, 'UNKNOWN_CODE', f'{cycle}: unrecognised code {code:#x}', code, HYPOTHESIS, transferred)
        category, evidence = CODES[code]
    detail = f'{transferred} of {count * SECTOR_BYTES} bytes transferred'
    if expect is None:
        outcome = PASS if category == 'SUCCESS' else FAIL
        return _verdict(outcome, category, f'{cycle}: {ERROR_NAMES[code]}, {detail}', code, evidence, transferred)
    if code != expect:
        return _verdict(FAIL, category, f'{cycle}: expected code {expect}, observed {code} ({detail})',
                        code, evidence, transferred)
    return _verdict(PASS, category, f'{cycle}: expected code {code} observed ({detail})', code, evidence, transferred)


def classify_records(records, count, expect=None):
    """Classify a cold+warm record set. Returns (overall outcome, per-run verdicts)."""
    caps = records.get('CAP') or [{}]
    media = caps[0].get('cdrom_media') is True
    runs = records.get('RUN', [])
    if not runs:
        return NOT_RUN, [_verdict(NOT_RUN, 'NOT_EXECUTED', 'no RUN record: the diagnostic was not executed')]
    if [run.get('cycle') for run in runs] != ['cold', 'warm']:
        return BLOCKED, [_verdict(BLOCKED, 'INCOMPLETE_RECORD', 'missing cold/warm RUN records')]
    verdicts = [classify_run(run, count, expect, media) for run in runs]
    return overall([v.outcome for v in verdicts]), verdicts


def overall(outcomes):
    """Aggregate: any FAIL dominates, then BLOCKED, then NOT_RUN; all PASS (and non-empty) is PASS."""
    if not outcomes:
        return NOT_RUN
    for outcome in (FAIL, BLOCKED, NOT_RUN):
        if outcome in outcomes:
            return outcome
    return PASS
