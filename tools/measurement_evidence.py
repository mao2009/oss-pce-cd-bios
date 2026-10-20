"""Bind an evidence record to its raw log by measurement point id and log SHA-256 (Issue #364).

Scope: registry + verifier only. No real core is run here and no real-core evidence exists.
A record is accepted only if the recomputed SHA-256 of the raw log bytes equals log_sha256, the
log names the same measurement point id, the claim field is covered by that point, and any
declared observation kind equals the registry kind. Self-declared kinds are never trusted alone.
Every passing record yields UNKNOWN: OBSERVED is unreachable by design until producer authentication (#124).
binary_sha256 and provenance are caller-supplied and are not authenticated here.
"""
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / 'spec' / 'measurement_points.json'
# Pin of the registry file bytes. Editing the registry requires updating this pin in review.
REGISTRY_SHA256 = 'b9634b90dead3dc1a7ea8c32b2a22e8951c7fd8a2f2bc08a2484eba105368819'
PINNED_CORE_REVISION = json.loads((ROOT / 'tools/dependencies.json').read_text())['geargrafx']['revision']
PROVENANCES = frozenset({'SYNTHETIC', 'REAL_CORE'})
RECORD_KEYS = frozenset({'provenance', 'core_revision', 'binary_sha256', 'measurement_point_id', 'log_sha256'})
KINDS = frozenset({'HW_BUS_READBACK', 'CALLEE_EXECUTION'})
HEX40 = re.compile(r'[0-9a-f]{40}\Z')
HEX64 = re.compile(r'[0-9a-f]{64}\Z')
MARKER = re.compile(rb'^MEASUREMENT_POINT (\S+)\r?$', re.MULTILINE)
EXECUTED = re.compile(rb'^EXECUTED_CALLEE 1\r?$', re.MULTILINE)


def load_registry(path=REGISTRY_PATH):
    """Return {id: entry}. Fails closed if the file bytes differ from REGISTRY_SHA256."""
    raw = Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != REGISTRY_SHA256:
        raise ValueError('measurement registry sha256 differs from pin')
    points = json.loads(raw)['points']
    registry = {}
    for point in points:
        if set(point) != {'id', 'kind', 'covers_fields', 'hook'} or point['kind'] not in KINDS                 or not isinstance(point['covers_fields'], list):
            raise ValueError(f'invalid measurement point entry: {point!r}')
        registry[point['id']] = point
    return registry


def verify_record(record, log_path, *, field, binary_sha256, declared_kind=None,
                  registry=None, pinned_revision=PINNED_CORE_REVISION):
    """Return the claim status for one record, or raise ValueError (fail closed).

    Always returns 'UNKNOWN' when every check passes. OBSERVED is unreachable until producer
    authentication exists (#124); a caller-supplied provenance value cannot promote a record.
    """
    def require(ok, message):
        if not ok:
            raise ValueError(message)

    registry = load_registry() if registry is None else registry
    require(isinstance(record, dict) and set(record) == RECORD_KEYS, 'record must contain exactly the record keys')
    require(record['provenance'] in PROVENANCES, 'unknown provenance')
    require(isinstance(record['core_revision'], str) and HEX40.fullmatch(record['core_revision']) and
            record['core_revision'] == pinned_revision, 'core_revision must equal the pin')
    require(isinstance(binary_sha256, str) and HEX64.fullmatch(binary_sha256) is not None and
            record['binary_sha256'] == binary_sha256,
            'binary_sha256 differs from the built core')
    point = registry.get(record['measurement_point_id'])
    require(point is not None, 'measurement_point_id not in registry')
    require(field in point['covers_fields'], f'measurement point does not cover field {field!r}')
    require(declared_kind is None or declared_kind == point['kind'],
            'declared observation_kind does not match the registry')
    path = Path(log_path)
    require(path.is_file() and not path.is_symlink(), 'log missing or not a regular file')
    data = path.read_bytes()
    require(hashlib.sha256(data).hexdigest() == record['log_sha256'],
            'log sha256 differs from record (tampered or substituted log)')
    require(MARKER.findall(data) == [record['measurement_point_id'].encode()],
            'log measurement point marker differs from record')
    if point['kind'] == 'CALLEE_EXECUTION':
        require(EXECUTED.search(data) is not None, 'log has no EXECUTED_CALLEE 1 record')
    # Fail closed: OBSERVED is unreachable by design until a real producer authenticates the run (#124).
    # The caller-supplied provenance field is not evidence of a real core run, so it never yields OBSERVED.
    return 'UNKNOWN'
