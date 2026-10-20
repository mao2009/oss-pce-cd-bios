"""Validator for the hardware-observation ledger (Issue #226). Pure checks, no execution.

spec/hardware_observations.json holds low-level hardware observations only (currently hw_bus_reset).
It is separate from spec/api_contracts.json: it never stores callee ABI fields, never sets
release_eligible, and never promotes a callee claim. The field allowlist is the shared
HARDWARE_OBSERVATION_FIELDS from tools/contracts.py (read only, not changed here).
"""
import json
import re
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from contracts import HARDWARE_OBSERVATION_FIELDS  # noqa: E402

LEDGER_PATH = ROOT / 'spec' / 'hardware_observations.json'
SCHEMA = 'hardware-observation-ledger/1'
STATUSES = frozenset({'OBSERVED', 'NOT_RUN', 'BLOCKED'})  # VERIFIED is deliberately absent
ENVIRONMENT_KINDS = frozenset({'EMULATOR_CORE'})         # no real-hardware entries yet (#7 NOT_TESTED)
RUN_KINDS = frozenset({'cold', 'warm'})
TRUST_LEVELS = frozenset({
    'EMULATOR_CORE_UNARCHIVED',  # real core run; artifact not stored or hash-pinned, not authenticated
    'NOT_MEASURED',              # no run exists (NOT_RUN / BLOCKED entries only)
})
TOP_KEYS = frozenset({'schema', 'release_eligible', 'callee_abi', 'entries'})
ENTRY_KEYS = frozenset({'field', 'status', 'value', 'source', 'environment', 'trust_level',
                        'not_evidence_for', 'notes'})
SOURCE_KEYS = frozenset({'kind', 'name', 'revision', 'reference'})
ENV_KEYS = frozenset({'kind', 'platform', 'media', 'runs'})
REVISION_RE = re.compile(r'[0-9a-f]{40}\Z')


def _require(ok, message):
    if not ok:
        raise ValueError(message)


def _exact_keys(obj, keys, context):
    _require(isinstance(obj, dict), f'{context}: must be an object')
    _require(set(obj) == keys, f'{context}: keys must be exactly {sorted(keys)} (got {sorted(obj)})')


def _nonempty_str(obj, key, context):
    _require(isinstance(obj.get(key), str) and obj[key].strip(), f'{context}: {key} must be a non-empty string')


def _is_repo_file(reference):
    """True only for a repository-relative path whose realpath (symlinks resolved) is an existing file under ROOT."""
    if PureWindowsPath(reference).anchor or PurePosixPath(reference).is_absolute():
        return False
    if '..' in re.split(r'[\\/]', reference):
        return False
    target = (ROOT / reference).resolve()
    return target.is_relative_to(ROOT.resolve()) and target.is_file()


def validate_entry(entry, seen_fields):
    _exact_keys(entry, ENTRY_KEYS, 'entry')
    field = entry['field']
    context = f'entry {field!r}'
    _require(field in HARDWARE_OBSERVATION_FIELDS,
             f'{context}: not a hardware observation field (callee ABI fields belong in spec/api_contracts.json)')
    _require(field not in seen_fields, f'{context}: duplicate entry')
    seen_fields.add(field)
    _require(entry['status'] in STATUSES, f'{context}: unsupported status {entry["status"]!r}')
    _nonempty_str(entry, 'value', context)
    _require(entry['value'] != 'UNKNOWN', f'{context}: value must not be UNKNOWN')

    _exact_keys(entry['source'], SOURCE_KEYS, f'{context} source')
    src = entry['source']
    _require(src['kind'] == 'EMULATOR_SOURCE', f'{context}: source kind must be EMULATOR_SOURCE')
    _nonempty_str(src, 'name', f'{context} source')
    _require(isinstance(src['revision'], str) and REVISION_RE.match(src['revision']),
             f'{context}: source revision must be a full 40-hex commit')
    _nonempty_str(src, 'reference', f'{context} source')
    _require(_is_repo_file(src['reference']), f'{context}: source reference is not a repository file')

    _exact_keys(entry['environment'], ENV_KEYS, f'{context} environment')
    env = entry['environment']
    _require(env['kind'] in ENVIRONMENT_KINDS,
             f'{context}: environment kind {env["kind"]!r} is not an accepted measurement environment')
    _nonempty_str(env, 'platform', f'{context} environment')
    _nonempty_str(env, 'media', f'{context} environment')
    _require(isinstance(env['runs'], list) and all(r in RUN_KINDS for r in env['runs'])
             and len(env['runs']) == len(set(env['runs'])),
             f'{context}: runs must be unique cold/warm values')

    _require(entry['trust_level'] in TRUST_LEVELS, f'{context}: unsupported trust_level {entry["trust_level"]!r}')
    not_evidence = entry['not_evidence_for']
    _require(isinstance(not_evidence, list) and not_evidence and all(isinstance(x, str) and x for x in not_evidence),
             f'{context}: not_evidence_for must be a non-empty list of strings')
    _require(isinstance(entry['notes'], str), f'{context}: notes must be a string')

    if entry['status'] == 'OBSERVED':
        _require(entry['trust_level'] != 'NOT_MEASURED', f'{context}: OBSERVED requires a measured trust level')
        _require(bool(env['runs']), f'{context}: OBSERVED requires at least one run')
        # Fail closed (Issue #124 / #171): the trust_level, runs and source above are self-declared. Nothing
        # authenticates the run, so no entry may be OBSERVED. Matching a file is not authenticity either.
        raise ValueError(f'{context}: OBSERVED refused: executing principal not authenticated (#124)')
    else:
        _require(entry['trust_level'] == 'NOT_MEASURED' and env['runs'] == [],
                 f'{context}: {entry["status"]} must not claim a measurement or runs')


def validate_ledger(data):
    """Raise ValueError unless data is a well-formed hardware-observation ledger. Returns None if valid."""
    _exact_keys(data, TOP_KEYS, 'ledger')
    _require(data['schema'] == SCHEMA, f'ledger: schema must be {SCHEMA!r}')
    _require(data['release_eligible'] is False, 'ledger: release_eligible must be false')
    _require(data['callee_abi'] == 'UNVERIFIED', 'ledger: callee_abi must be UNVERIFIED')
    _require(isinstance(data['entries'], list), 'ledger: entries must be a list')
    seen = set()
    for entry in data['entries']:
        validate_entry(entry, seen)


def main(argv=None):
    path = Path(argv[0]) if argv else LEDGER_PATH
    try:
        validate_ledger(json.loads(path.read_text(encoding='utf-8')))
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f'FAIL {path}: {exc}')
        return 1
    print(f'PASS {path}')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
