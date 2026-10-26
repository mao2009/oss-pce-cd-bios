"""Synthetic judge for the CD_READ single-sector, single-mode transfer contract (Refs #188, #189). No ROM, emulator, media or BIOS.

Two evidence kinds are kept apart:
- SYNTHETIC: a synthetic SCSI transport observation (our own media). Judged by judge_synthetic(). Never callee evidence.
- CALLEE: a reference BIOS callee observation. Judged by judge_callee(), which delegates to cd_read_error_verdict.judge
  and requires gate ALLOWED. Never synthetic evidence.

The 2048-byte sector comparison (compare_sector) is a full byte-for-byte comparison; it never samples.
Shipped expectations are synthetic and carry no BIOS claim. Per-item PASS can occur for synthetic input;
judge_synthetic never returns overall PASS for shipped cases (effects are always UNKNOWN).
"""
from cd_read_error_verdict import judge as _callee_judge

PASS, FAIL, BLOCKED, UNKNOWN = 'PASS', 'FAIL', 'BLOCKED', 'UNKNOWN'
ORDER = {FAIL: 3, BLOCKED: 2, UNKNOWN: 1, PASS: 0}
SYNTHETIC, CALLEE = 'synthetic_scsi', 'reference_callee'
SECTOR_BYTES = 2048
LBA_LIMIT = 1 << 21          # 21-bit LBA field (cd-read-contract.md T1)
DEST_MODE = 3                # _dh = 3 only (188 D1); other modes are out of scope
DEST_WINDOW = 0x2000         # MPR3 window, 8 KiB
IN_SCOPE_COUNTS = (1,)       # count 0, 2, 3, 4, 256 stay UNKNOWN (188 section 2.3). Unit of count: HYPOTHESIS A4.
IN_WINDOW, CROSSES = 'IN_WINDOW', 'CROSSES_WINDOW'

SYNTHETIC_CASES = {
    'S1_LBA15_IN_RANGE': {'lba': 15, 'count': 1, 'dest_mode': DEST_MODE, 'dest_offset': 0x0000, 'media_sectors': 16},
    'S2_LBA16_OUT_OF_RANGE': {'lba': 16, 'count': 1, 'dest_mode': DEST_MODE, 'dest_offset': 0x0000,
                              'media_sectors': 16},
}


def reference_sector(lba):
    """Deterministic synthetic 2048-byte pattern for a synthetic sector. Not a disc image."""
    return bytes(((lba * 31) + (i * 7) + (i >> 8)) & 0xFF for i in range(SECTOR_BYTES))


def span_bytes(case):
    return case['count'] * SECTOR_BYTES


def compare_sector(expected, observed):
    """Full comparison of 2048 bytes. Wrong length or type is BLOCKED (invalid evidence), never PASS."""
    if not isinstance(expected, (bytes, bytearray)) or len(expected) != SECTOR_BYTES:
        return {'verdict': BLOCKED, 'reason': 'expected is not 2048 bytes', 'mismatches': None, 'first': None}
    if not isinstance(observed, (bytes, bytearray)) or len(observed) != SECTOR_BYTES:
        return {'verdict': BLOCKED, 'reason': 'observed is not 2048 bytes', 'mismatches': None, 'first': None}
    diffs = [i for i in range(SECTOR_BYTES) if expected[i] != observed[i]]
    if not diffs:
        return {'verdict': PASS, 'reason': '', 'mismatches': 0, 'first': None}
    return {'verdict': FAIL, 'reason': 'byte mismatch', 'mismatches': len(diffs), 'first': diffs[0]}


def dest_span_ok(offset, count):
    """IN_WINDOW if count*2048 bytes from offset fit in the MPR3 window; CROSSES if they do not (out of scope)."""
    if type(offset) is not int or type(count) is not int or count < 0:
        return BLOCKED
    if not 0 <= offset < DEST_WINDOW:
        return BLOCKED
    if offset + count * SECTOR_BYTES <= DEST_WINDOW:
        return IN_WINDOW
    return UNKNOWN


def _byte(value):
    return type(value) is int and 0 <= value <= 255


def _valid_effects(obs):
    mpr = obs.get('mpr')
    return (isinstance(mpr, list) and len(mpr) == 8 and all(_byte(b) for b in mpr)
            and _byte(obs.get('stack', -1)) and type(obs.get('irq')) is bool)


def judge_synthetic(case, obs):
    """Judge one synthetic SCSI transfer. Returns (overall, per_item). Never PASS overall; never callee evidence.

    Items: delivered, bytes (full 2048-byte compare, or dest unchanged when out of range), outside_writes,
    and mpr/stack/irq (recorded, UNKNOWN because the expected callee effects are not established).
    """
    if not isinstance(case, dict) or not isinstance(obs, dict):
        return BLOCKED, {'reason': 'case or observation is not a dict'}
    if obs.get('kind') != SYNTHETIC:
        return BLOCKED, {'reason': 'not synthetic evidence'}
    if obs.get('monitor_ok') is not True:
        return BLOCKED, {'reason': 'monitor failed'}
    lba, count, media = case.get('lba'), case.get('count'), case.get('media_sectors')
    if type(lba) is not int or not 0 <= lba < LBA_LIMIT or type(media) is not int or media < 0:
        return BLOCKED, {'reason': 'lba or media size invalid'}
    if case.get('dest_mode') != DEST_MODE:
        return BLOCKED, {'reason': 'destination mode out of scope'}
    # bool is an int subclass: the type check rejects True/False before the scope check.
    if type(count) is not int or count < 0:
        return BLOCKED, {'reason': 'count invalid'}
    if count not in IN_SCOPE_COUNTS:
        return UNKNOWN, {'reason': 'count boundary not established', 'count': UNKNOWN}
    geometry = dest_span_ok(case.get('dest_offset'), count)
    if geometry == BLOCKED:
        return BLOCKED, {'reason': 'destination offset invalid'}
    if geometry == UNKNOWN:
        return UNKNOWN, {'reason': 'destination crosses MPR3 window (out of scope)', 'geometry': UNKNOWN}
    span = span_bytes(case)
    before, after = obs.get('dest_before'), obs.get('dest_after')
    if not isinstance(before, (bytes, bytearray)) or not isinstance(after, (bytes, bytearray)) \
            or len(before) != span or len(after) != span:
        return BLOCKED, {'reason': 'destination snapshot has wrong length'}
    if type(obs.get('delivered')) is not int or type(obs.get('outside_writes')) is not int:
        return BLOCKED, {'reason': 'delivered or outside_writes not an int'}
    if not _valid_effects(obs):
        return BLOCKED, {'reason': 'mpr, stack or irq not recorded validly'}

    items = {}
    in_range = lba < media
    if in_range:
        items['delivered'] = PASS if obs['delivered'] == 1 else FAIL
        items['bytes'] = compare_sector(reference_sector(lba), after)['verdict']
        if items['bytes'] == BLOCKED:
            return BLOCKED, {'reason': 'sector comparison invalid'}
    else:
        items['delivered'] = PASS if obs['delivered'] == 0 else FAIL
        items['bytes'] = PASS if bytes(after) == bytes(before) else FAIL
    items['outside_writes'] = PASS if obs['outside_writes'] == 0 else FAIL
    items['mpr'] = items['stack'] = items['irq'] = UNKNOWN
    overall = max(items.values(), key=ORDER.get)
    return overall, items


def judge_callee(case, obs):
    """Reference BIOS callee evidence only. Synthetic observations are BLOCKED here."""
    if not isinstance(obs, dict) or obs.get('kind') != CALLEE:
        return BLOCKED, {'reason': 'not reference callee evidence'}
    return _callee_judge(case, obs)
