"""Synthetic verdict for the CD_READ error/side-effect contract (Issue #189). No ROM, emulator, media or BIOS.

judge(case, obs) judges one observation record against one case's expectations. Ordering: FAIL > BLOCKED > UNKNOWN > PASS.
An UNKNOWN expectation never becomes PASS, and an observed value never replaces an UNKNOWN expectation.
Shipped cases hold only UNKNOWN expectations, so no shipped case can PASS. A synthetic PASS/FAIL is not BIOS ABI evidence.
"""
PASS, UNKNOWN, FAIL, BLOCKED = 'PASS', 'UNKNOWN', 'FAIL', 'BLOCKED'
ORDER = {FAIL: 3, BLOCKED: 2, UNKNOWN: 1, PASS: 0}
ITEMS = ('a', 'x', 'y', 'p', 'mpr', 'stack', 'ram_outside_dest')
_ALL_UNKNOWN = {item: UNKNOWN for item in ITEMS}

CASES = {
    'C1_OUT_OF_RANGE_LBA': {'executable': True, 'reason': '', 'params': {'lba': 16, 'count': 1},
                            'expected': dict(_ALL_UNKNOWN)},
    'C2_COUNT_0': {'executable': True, 'reason': '', 'params': {'lba': 5, 'count': 0},
                   'expected': dict(_ALL_UNKNOWN)},
    'C3_NO_MEDIA': {'executable': False, 'reason': 'T7: no executable evidence without media (Geargrafx cannot run)',
                    'params': {'lba': 0, 'count': 1}, 'expected': dict(_ALL_UNKNOWN)},
}


def _byte(value):
    return type(value) is int and 0 <= value <= 255


def _valid(item, value):
    if item == 'mpr':
        return isinstance(value, list) and len(value) == 8 and all(_byte(v) for v in value)
    if item == 'ram_outside_dest':
        return type(value) is int and value >= 0
    return _byte(value)


def _known_expectation_valid(item, expected):
    return expected == UNKNOWN or _valid(item, expected)


def judge(case, obs):
    """Return (overall, per_item). obs keys: monitor_ok, reached, gate, and ITEMS (missing = not observed).

    Anything that cannot be evaluated (case not executable, malformed or missing expectations, malformed
    observations) is BLOCKED; it never raises and never yields PASS.
    """
    if case.get('executable') is not True:
        return BLOCKED, {'reason': case.get('reason', 'not executable')}
    if not isinstance(obs, dict) or obs.get('monitor_ok') is not True:
        return BLOCKED, {'reason': 'monitor failed or evidence unreadable'}
    if obs.get('reached') is not True:
        return BLOCKED, {'reason': 'not reached'}
    if obs.get('gate') != 'ALLOWED':
        return BLOCKED, {'reason': 'JSR gate not ALLOWED'}
    expectations = case.get('expected')
    if not isinstance(expectations, dict):
        return BLOCKED, {'reason': 'expectation table missing'}
    items = {}
    for item in ITEMS:
        if item not in expectations or not _known_expectation_valid(item, expectations[item]):
            items[item] = BLOCKED
            continue
        expected = expectations[item]
        if item not in obs:
            items[item] = UNKNOWN if expected == UNKNOWN else BLOCKED
        elif not _valid(item, obs[item]):
            items[item] = BLOCKED
        elif expected == UNKNOWN:
            items[item] = UNKNOWN
        else:
            items[item] = PASS if obs[item] == expected else FAIL
    overall = max(items.values(), key=ORDER.get)
    return overall, items
