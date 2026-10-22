#!/usr/bin/env python3
"""DEV implementation ledger (Issue #324 items 1-2, owner approved): spec/dev_implementations.json.

Records which of the 81 System Card API slots the DEV ROM implements, with its source, license, ABI-contract
status, host and real-core evidence, and approval. It is a separate axis from the reference BIOS ABI: nothing here
marks an API as VERIFIED against the genuine System Card, and release_eligible stays false.

States: NOT_READY (default; not approved, never counted as PASS), DEV_CALLABLE (approved for DEV use only),
BLOCKED (explicitly refused, with a reason, and no implementation).
Usage: python3 tools/dev_implementations.py   (exit 0 = consistent, 1 = violations, printed to stderr)
"""
import json
import sys
from pathlib import Path

if __package__:
    from .build_rom import LAST_SLOT, SLOTS_FILE, ROOT, load_slots, overrides
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from build_rom import LAST_SLOT, SLOTS_FILE, ROOT, load_slots, overrides

LEDGER_FILE = ROOT / 'spec' / 'dev_implementations.json'
STATES = {'NOT_READY', 'DEV_CALLABLE', 'BLOCKED'}
ABI_STATUSES = {'UNKNOWN', 'PROJECT_SPEC_ONLY', 'VERIFIED'}  # VERIFIED is only the reference-BIOS axis; none is set here


def load_ledger(path: Path = LEDGER_FILE) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def _abi_status(entry):
    abi = entry.get('abi_contract') if isinstance(entry, dict) else None
    return abi.get('status') if isinstance(abi, dict) else None


def _owner_approved(entry):
    approval = entry.get('approval') if isinstance(entry, dict) else None
    return (isinstance(approval, dict)
            and all(isinstance(approval.get(k), str) and approval[k].strip() for k in ('approved_by', 'review_ref')))


def validate(ledger: dict, slots: list[dict], override_slots: set[int], root: Path = ROOT) -> list[str]:
    """Return every consistency problem. An empty list means the ledger matches the repository.

    Fail closed: any malformed shape that escapes the explicit checks is reported, never raised or passed."""
    try:
        return _validate(ledger, slots, override_slots, root)
    except Exception as exc:  # noqa: BLE001
        return [f'malformed ledger: {type(exc).__name__}: {exc}']


def _validate(ledger, slots, override_slots, root):
    problems = []
    if not isinstance(ledger, dict):
        return ['ledger must be a JSON object']
    if type(ledger.get('schema_version')) is not int or ledger.get('schema_version') != 1:
        problems.append('schema_version must be 1')
    if ledger.get('release_eligible') is not False:
        problems.append('release_eligible must be false')
    entries = ledger.get('slots')
    if not isinstance(entries, list):
        return problems + ['slots must be a list']
    if not all(isinstance(e, dict) for e in entries):
        return problems + ['every slots entry must be an object']
    ids = [e.get('id') for e in entries]
    if not all(type(i) is int for i in ids):
        return problems + ['every slot id must be an integer']
    if not all(type(i) is int for i in override_slots):
        return problems + ['override slots must be integers']
    if len(ids) != len(set(ids)):
        problems.append('duplicate slot id in ledger')
    if sorted(i for i in ids if isinstance(i, int)) != list(range(LAST_SLOT + 1)):
        problems.append(f'ledger must list exactly slots 0..{LAST_SLOT:#x} (81 entries, unregistered slots are not PASS)')
    names = {s['id']: s['candidate_name'] for s in slots}
    for entry in entries:
        slot = entry.get('id')
        label = f'slot {slot}'
        if slot not in names:
            problems.append(f'{label}: not one of the 81 API slots')
            continue
        if entry.get('candidate_name') != names[slot]:
            problems.append(f'{label}: candidate_name {entry.get("candidate_name")!r} != {names[slot]!r}')
        state = entry.get('state')
        if not isinstance(state, str) or state not in STATES:
            problems.append(f'{label}: unknown state {state!r}')
            continue
        if not isinstance(_abi_status(entry), str) or _abi_status(entry) not in ABI_STATUSES:
            problems.append(f'{label}: unknown abi_contract status')
        if _abi_status(entry) == 'VERIFIED':
            # Fail closed: this ledger has no evidence schema for the reference-BIOS axis, so VERIFIED is refused
            # even when host_evidence / real_core_evidence / approval are filled in.
            problems.append(f'{label}: VERIFIED is the reference-BIOS axis and must not be set in this ledger '
                            f'(refused even with evidence; expected 0 VERIFIED rows)')
        implementation = entry.get('implementation')
        if implementation is not None and not isinstance(implementation, dict):
            problems.append(f'{label}: implementation must be an object or null')
            continue
        implemented = slot in override_slots
        if implemented != (implementation is not None):
            problems.append(f'{label}: DEV ROM override is {"present" if implemented else "absent"} but the ledger '
                            f'{"lacks" if implementation is None else "claims"} an implementation')
        if implementation is not None:
            expected = f'src/overrides/slot_{slot:02X}.s'
            if implementation.get('source') != expected or not (root / expected).is_file():
                problems.append(f'{label}: implementation source must be an existing {expected}')
            if not isinstance(implementation.get('license'), str) or not implementation['license'].strip():
                problems.append(f'{label}: implementation license is missing')
        if state == 'BLOCKED':
            if implemented:
                problems.append(f'{label}: BLOCKED slot must not have an implementation')
            if not isinstance(entry.get('reason'), str) or not entry['reason']:
                problems.append(f'{label}: BLOCKED requires a reason')
        if state == 'DEV_CALLABLE':
            problems += _approved_problems(label, entry, implemented, root)
    return problems


def _approved_problems(label, entry, implemented, root):
    problems = []
    if not implemented:
        problems.append(f'{label}: DEV_CALLABLE needs a DEV ROM implementation')
    for kind in ('host_evidence', 'real_core_evidence'):
        paths = entry.get(kind) or []
        if not isinstance(paths, list) or not all(isinstance(x, str) for x in paths):
            problems.append(f'{label}: {kind} must be a list of repo-relative paths')
            continue
        if not paths:
            problems.append(f'{label}: DEV_CALLABLE needs {kind}')
        for path in paths:
            target = (root / path).resolve()
            if not target.is_relative_to(root.resolve()) or not target.is_file():
                problems.append(f'{label}: {kind} file missing: {path}')
    if not _owner_approved(entry):
        problems.append(f'{label}: DEV_CALLABLE needs approval.approved_by and approval.review_ref')
    return problems


def summary(ledger: dict, override_slots: set[int]) -> dict:
    raw = ledger.get('slots') if isinstance(ledger, dict) else None
    entries = [e for e in raw if isinstance(e, dict)] if isinstance(raw, list) else []
    states = [e.get('state') for e in entries if isinstance(e.get('state'), str)]
    return {'dev_rom_callable': len(override_slots),  # DEV ROM implementations (not owner approval)
            'dev_rom_unimplemented': LAST_SLOT + 1 - len(override_slots),
            'owner_approved': sum(_owner_approved(e) for e in entries),  # approval recorded, separate from implementation
            'dev_callable_approved': states.count('DEV_CALLABLE'),
            'not_ready': states.count('NOT_READY'),
            'blocked': states.count('BLOCKED'),
            'abi_verified': sum(_abi_status(e) == 'VERIFIED' for e in entries),  # counted from rows; validate() refuses any
            'release_eligible': ledger.get('release_eligible') if isinstance(ledger, dict) else None}


def main() -> int:
    try:
        return _main()
    except Exception as exc:  # noqa: BLE001  fail closed: any crash is a failure, never a pass
        print(f'FAIL ledger: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 1


def _main() -> int:
    chosen = set(overrides())
    try:
        ledger = load_ledger()
    except (OSError, ValueError, RecursionError) as exc:
        print(f'FAIL ledger: cannot load {LEDGER_FILE}: {exc}', file=sys.stderr)
        return 1
    slots = load_slots(SLOTS_FILE)
    problems = validate(ledger, slots, chosen)
    for problem in problems:
        print(f'FAIL ledger: {problem}', file=sys.stderr)
    print(json.dumps(summary(ledger, chosen), ensure_ascii=False))
    return 1 if problems else 0


if __name__ == '__main__':
    raise SystemExit(main())
