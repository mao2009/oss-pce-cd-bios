"""SHA-256 of a probe source file, recomputed from the file and checked against a recorded value.

A recorded digest that no longer matches the current source is STALE and must fail. Nothing here
reads ROM or BIOS data; it only hashes project source text.
"""
import hashlib
import json
from pathlib import Path


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check_recorded(recorded, path):
    """Return the current digest of `path`; raise ValueError if `recorded` is not that digest (stale)."""
    current = sha256_file(path)
    if recorded != current:
        raise ValueError(f"STALE probe_source_sha256: recorded {str(recorded)[:12]}, current {current[:12]} "
                         f"for {Path(path).name}; regenerate the evidence from the current source")
    return current


def check_evidence_file(evidence_path, source_path):
    """If an evidence file exists, its recorded probe digest must match the current source (else FAIL)."""
    evidence_path = Path(evidence_path)
    if not evidence_path.exists():
        return None
    recorded = json.loads(evidence_path.read_text(encoding="utf-8")).get("probe_source_sha256")
    return check_recorded(recorded, source_path)
