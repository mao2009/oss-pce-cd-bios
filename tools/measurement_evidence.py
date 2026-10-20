"""Bind an evidence record to its raw log by measurement point id and log SHA-256 (Issue #364).

Scope: registry + verifier only. No real core is run here and no real-core evidence exists.
A record is accepted only if the recomputed SHA-256 of the raw log bytes equals log_sha256, the
log names the same measurement point id, the claim field is covered by that point, and any
declared observation kind equals the registry kind. Self-declared kinds are never trusted alone.
Malformed input raises ValueError (controlled rejection); no other exception type escapes.
Every passing record yields UNKNOWN: OBSERVED is unreachable by design until producer authentication (#124).
No authenticated executing principal exists yet, so verification success is UNKNOWN, never OBSERVED.
binary_sha256 and provenance are caller-supplied and are not authenticated here.
"""
import hashlib
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / 'spec' / 'measurement_points.json'
REVIEW_MARKER_PATH = ROOT / 'spec' / 'measurement_points.review.json'
# Pin of the registry file bytes (.gitattributes keeps it -text so checkouts stay byte-exact).
# Editing the registry requires updating this pin in independent review.
REGISTRY_SHA256 = 'b9634b90dead3dc1a7ea8c32b2a22e8951c7fd8a2f2bc08a2484eba105368819'
PINNED_CORE_REVISION = json.loads((ROOT / 'tools/dependencies.json').read_text())['geargrafx']['revision']
PROVENANCES = frozenset({'SYNTHETIC', 'REAL_CORE'})
RECORD_KEYS = frozenset({'provenance', 'core_revision', 'binary_sha256', 'measurement_point_id', 'log_sha256'})
POINT_KEYS = frozenset({'id', 'kind', 'covers_fields', 'hook'})
REVIEW_MARKER_KEYS = frozenset({'registry_sha256', 'core_revision', 'reviewed_in_pr'})
KINDS = frozenset({'HW_BUS_READBACK', 'CALLEE_EXECUTION'})
POINT_ID = re.compile(r'[A-Z0-9_]+\Z')
HEX64 = re.compile(r'[0-9a-f]{64}\Z')
MARKER = re.compile(rb'^MEASUREMENT_POINT (\S+)\r?$', re.MULTILINE)
EXECUTED = re.compile(rb'^EXECUTED_CALLEE 1\r?$', re.MULTILINE)


def parse_registry(doc):
    """Return {id: entry} from a parsed registry document. Fails closed on any malformed or duplicate entry."""
    if not (isinstance(doc, dict) and type(doc.get('schema_version')) is int and doc['schema_version'] == 1
            and isinstance(doc.get('points'), list)):
        raise ValueError('invalid measurement registry document')
    registry = {}
    for point in doc['points']:
        if not (isinstance(point, dict) and set(point) == POINT_KEYS
                and isinstance(point['id'], str) and POINT_ID.match(point['id'])
                and isinstance(point['kind'], str) and point['kind'] in KINDS
                and isinstance(point['hook'], str)
                and isinstance(point['covers_fields'], list)
                and all(isinstance(f, str) and f for f in point['covers_fields'])):
            raise ValueError(f'invalid measurement point entry: {point!r}')
        if point['id'] in registry:
            raise ValueError(f"duplicate measurement_point_id: {point['id']}")
        registry[point['id']] = point
    return registry


def load_registry(path=REGISTRY_PATH):
    """Return {id: entry}. Fails closed if the file bytes differ from REGISTRY_SHA256."""
    raw = Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != REGISTRY_SHA256:
        raise ValueError('measurement registry sha256 differs from pin')
    return parse_registry(json.loads(raw))


def check_pin_review(marker_path=REVIEW_MARKER_PATH, *, registry_sha256=REGISTRY_SHA256,
                     core_revision=PINNED_CORE_REVISION):
    """Fail closed unless the committed review marker names the current registry and core pins.

    Changing either pin without updating this marker in the same change is rejected. The marker is a
    consistency gate for review, not authentication: the approval itself is the PR review.
    """
    try:
        marker = json.loads(Path(marker_path).read_bytes())
    except (OSError, ValueError) as exc:
        raise ValueError('pin review marker missing or unreadable') from exc
    if not (isinstance(marker, dict) and set(marker) == REVIEW_MARKER_KEYS):
        raise ValueError('pin review marker malformed')
    if marker['registry_sha256'] != registry_sha256:
        raise ValueError('registry pin changed without updated review marker')
    if marker['core_revision'] != core_revision:
        raise ValueError('core_revision pin changed without updated review marker')
    if not (isinstance(marker['reviewed_in_pr'], int) and not isinstance(marker['reviewed_in_pr'], bool)
            and marker['reviewed_in_pr'] > 0):
        raise ValueError('pin review marker must name the approving PR number')


def verify_record(record, log_path, *, field, binary_sha256, declared_kind=None):
    """Return the claim status for one record, or raise ValueError (fail closed).

    The registry always comes from the pinned file and the core revision from tools/dependencies.json;
    neither can be overridden by the caller. Always returns 'UNKNOWN' when every check passes.
    OBSERVED is unreachable until producer authentication exists (#124); a caller-supplied provenance
    value cannot promote a record.
    """
    def require(ok, message):
        if not ok:
            raise ValueError(message)

    # Pin-change gate on the verifier path: a registry or core pin edited without its review marker fails here.
    check_pin_review(REVIEW_MARKER_PATH, registry_sha256=REGISTRY_SHA256, core_revision=PINNED_CORE_REVISION)
    registry = load_registry(REGISTRY_PATH)
    pinned_revision = PINNED_CORE_REVISION
    require(isinstance(record, dict) and set(record) == RECORD_KEYS, 'record must contain exactly the record keys')
    require(isinstance(record['provenance'], str) and record['provenance'] in PROVENANCES, 'unknown provenance')
    require(record['core_revision'] == pinned_revision, 'core_revision must equal the pin')
    require(isinstance(binary_sha256, str) and HEX64.fullmatch(binary_sha256) is not None and
            record['binary_sha256'] == binary_sha256,
            'binary_sha256 differs from the built core')
    mp_id = record['measurement_point_id']
    require(isinstance(mp_id, str) and mp_id in registry, 'measurement_point_id not in registry')
    point = registry[mp_id]
    require(isinstance(record['log_sha256'], str) and HEX64.fullmatch(record['log_sha256']) is not None,
            'log_sha256 must be a SHA-256 hex digest')
    require(isinstance(field, str) and field in point['covers_fields'],
            f'measurement point does not cover field {field!r}')
    require(declared_kind is None or declared_kind == point['kind'],
            'declared observation_kind does not match the registry')
    try:
        raw_path = os.fspath(log_path) if isinstance(log_path, (str, os.PathLike)) else None
    except TypeError:
        raw_path = None
    require(isinstance(raw_path, str), 'log path must be a str path')
    path = Path(raw_path)
    require(path.is_file() and not path.is_symlink(), 'log missing or not a regular file')
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ValueError(f'log unreadable: {exc.strerror}') from exc
    require(hashlib.sha256(data).hexdigest() == record['log_sha256'],
            'log sha256 differs from record (tampered or substituted log)')
    require(MARKER.findall(data) == [mp_id.encode()], 'log measurement point marker differs from record')
    if point['kind'] == 'CALLEE_EXECUTION':
        require(EXECUTED.search(data) is not None, 'log has no EXECUTED_CALLEE 1 record')
    # Fail closed: OBSERVED is unreachable by design until a real producer authenticates the run (#124).
    # The caller-supplied provenance field is not evidence of a real core run, so it never yields OBSERVED.
    return 'UNKNOWN'
