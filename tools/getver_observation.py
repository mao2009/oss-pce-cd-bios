#!/usr/bin/env python3
"""EX_GETVER (slot $1E) observation contract and record validator (Issue #474, Phase ABI-2).

Scope: this module validates observation records and never runs a ROM. A record describes one
measured run of a synthetic DEV ROM routine. It cannot permit a call to the reference BIOS, cannot
lift an ABI claim to VERIFIED, and cannot change the reference JSR gate (tools/reference_jsr_gate.py
stays 81/81 BLOCKED). Only SYNTHETIC_DEV_ROM sources are accepted. A REFERENCE_ROM record is refused
because no owner-approved, independently reviewed channel exists (Issues #459, #367, #473).

The contract keeps four ideas apart: a static review (tools/abi1_reference_review.py), a local-only
observation, a reproducible real-core observation, and an ABI VERIFIED claim. Only the first two
can be produced here, and neither one grants execution.

record_digest is an integrity check only: it detects accidental corruption and edits that were not
resealed. It is not authentication. Anyone who edits a record can recompute the digest, so a matching
digest proves nothing about who produced the measurement or whether the run really happened. Only a
separate provenance mechanism (for example a signed, independently reviewed evidence channel, which
is outside this module and is tracked with #473) could establish that.
"""
import hashlib
import json
import re

SCHEMA = "oss-pce-cd-bios/getver-observation/v1"
SLOT = 0x1E
SOURCE_KINDS = ("SYNTHETIC_DEV_ROM",)
TEMPERATURES = ("cold", "warm")
NOT_MEASURED = "NOT_MEASURED"
IO_MEASURED_NONE = "MEASURED_NO_ACCESS"
ABI_CLAIMS = ("NOT_RUN", "OBSERVED_SYNTHETIC")  # VERIFIED is deliberately absent
RECORD_KEYS = frozenset({"schema", "slot", "entry", "source", "core", "harness", "case", "temperature",
                         "status", "inputs", "before", "after", "io", "steps_used", "step_budget",
                         "abi_claim", "record_digest"})
STATE_KEYS = frozenset({"a", "x", "y", "p", "sp", "mpr", "zp"})
INPUT_KEYS = frozenset({"a", "x", "y", "p"})
IO_KEYS = frozenset({"observation", "accesses"})
SOURCE_KEYS = frozenset({"kind", "rom_sha256"})


class Refused(ValueError):
    """A record the validator must not accept. ``code`` names the refusal class."""

    def __init__(self, code, detail):
        self.code = code
        super().__init__(f"{code}: {detail}")


def _canonical(record):
    body = {key: value for key, value in record.items() if key != "record_digest"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(record):
    return hashlib.sha256(_canonical(record)).hexdigest()


def seal(record):
    """Return the record with its digest filled in. Sealing does not make a record valid."""
    record["record_digest"] = digest(record)
    return record


def _no_duplicate_keys(pairs):
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise Refused("FIELDS", f"duplicate JSON key {sorted({k for k in keys if keys.count(k) > 1})}")
    return dict(pairs)


def load_jsonl(text):
    """Parse observation records one per line. Duplicate keys and blank-line tricks are refused."""
    if not isinstance(text, str):
        raise Refused("FIELDS", f"input must be text, got {type(text).__name__}")
    records = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line, object_pairs_hook=_no_duplicate_keys))
        except (ValueError, TypeError, RecursionError) as error:  # deep nesting raises RecursionError
            raise Refused("FIELDS", f"line {number}: {error}") from error
    return records


def _is_byte(value):
    # bool is a subclass of int in Python, so True must be rejected explicitly.
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 255


def _state_ok(state):
    if not isinstance(state, dict) or set(state) != STATE_KEYS:
        return False
    if not all(_is_byte(state[reg]) for reg in ("a", "x", "y", "p", "sp")):
        return False
    mpr = state["mpr"]
    if not (isinstance(mpr, list) and len(mpr) == 8 and all(_is_byte(v) for v in mpr)):
        return False
    return isinstance(state["zp"], str) and re.fullmatch(r"[0-9a-f]{64}", state["zp"]) is not None


def _inputs_ok(inputs, before):
    """Inputs must be exactly the A/X/Y/P bytes the harness observed before the routine ran."""
    if not isinstance(inputs, dict) or set(inputs) != INPUT_KEYS:
        raise Refused("FIELDS", f"inputs must have exactly the keys {sorted(INPUT_KEYS)}")
    if not all(_is_byte(inputs[reg]) for reg in INPUT_KEYS):
        raise Refused("FIELDS", f"inputs values must be integers 0..255, got {inputs!r}")
    if any(inputs[reg] != before[reg] for reg in INPUT_KEYS):
        raise Refused("INPUTS", f"inputs {inputs} differ from the observed before state")


def _io_ok(io):
    """Unmeasured I/O is recorded as NOT_MEASURED and never read as 'no access'."""
    if not isinstance(io, dict) or set(io) != IO_KEYS:
        raise Refused("IO", "io must be an object with exactly the keys observation and accesses")
    if not isinstance(io["accesses"], list):
        raise Refused("IO", "io.accesses must be a list")
    if io["observation"] == NOT_MEASURED:
        if io["accesses"]:
            raise Refused("IO", f"accesses recorded although I/O was not measured: {io['accesses']}")
        return
    if io["observation"] == IO_MEASURED_NONE:
        # No I/O meter exists in the pinned core harness, so no record can prove 'no access' yet.
        raise Refused("IO", "MEASURED_NO_ACCESS needs an I/O meter with its own provenance; none is accepted")
    raise Refused("IO", f"io.observation {io['observation']!r} is not a known value")


def validate(record, *, rom_sha256, entry, core_revision, harness_revision, step_budget):
    """Return the record's status ``OBSERVED_SYNTHETIC`` or raise ``Refused``. Checks run fail-closed in order."""
    if not isinstance(record, dict) or set(record) != RECORD_KEYS:
        raise Refused("FIELDS", "record keys do not match the v1 contract")
    if record["schema"] != SCHEMA:
        raise Refused("SCHEMA", f"unknown schema {record['schema']!r}")
    try:
        recomputed = digest(record)
    except (TypeError, ValueError, RecursionError) as error:  # non-JSON, NaN or too deep is malformed, not a crash
        raise Refused("FIELDS", f"record body is not canonical JSON: {error}") from error
    if record["record_digest"] != recomputed:  # integrity only; a match is not provenance
        raise Refused("TAMPERED", "record_digest does not match the record body")
    if type(record["slot"]) is not int or type(record["entry"]) is not int or record["slot"] != SLOT or record["entry"] != entry:  # 30.0 == 30 must not pass
        raise Refused("ENTRY_MISMATCH", f"slot {record['slot']!r} entry {record['entry']!r}, expected {entry!r}")
    source = record["source"]
    if not isinstance(source, dict) or set(source) != SOURCE_KEYS:
        raise Refused("SOURCE", f"source must have exactly the keys {sorted(SOURCE_KEYS)}")
    if source.get("kind") == "REFERENCE_ROM":
        raise Refused("REFERENCE_UNAPPROVED", "reference BIOS observation has no approved channel (#473)")
    if source.get("kind") not in SOURCE_KINDS:
        raise Refused("SOURCE", f"source kind {source.get('kind')!r} is not accepted")
    if source.get("rom_sha256") != rom_sha256:
        raise Refused("ROM_MISMATCH", "rom_sha256 differs from the DEV ROM under test")
    if record["core"] != {"name": "geargrafx", "revision": core_revision}:
        raise Refused("ROM_MISMATCH", "core identity differs from the pinned revision")
    if record["harness"] != {"name": "geargrafx_core_harness", "revision": harness_revision}:
        raise Refused("ROM_MISMATCH", "harness identity differs from the committed harness")
    if not isinstance(record["case"], str) or not record["case"]:
        raise Refused("FIELDS", "case must be a non-empty string")
    if record["temperature"] not in TEMPERATURES:
        raise Refused("FIELDS", f"temperature {record['temperature']!r}")
    if record["status"] == "TIMEOUT":
        raise Refused("BUDGET", "routine did not return within the step budget")
    if record["status"] != "RETURNED":
        raise Refused("STATUS", f"status {record['status']!r} is not RETURNED")
    if type(record["step_budget"]) is not int or record["step_budget"] != step_budget:
        raise Refused("BUDGET", "step_budget differs from the harness budget")
    steps = record["steps_used"]
    if steps != NOT_MEASURED and not (isinstance(steps, int) and not isinstance(steps, bool) and 0 <= steps <= step_budget):
        raise Refused("BUDGET", f"steps_used {steps!r} is outside 0..{step_budget}")
    if not (_state_ok(record["before"]) and _state_ok(record["after"])):
        raise Refused("FIELDS", "before/after state is malformed")
    _inputs_ok(record["inputs"], record["before"])
    if record["after"]["mpr"] != record["before"]["mpr"]:
        raise Refused("MPR", f"MPR changed {record['before']['mpr']} -> {record['after']['mpr']}")
    if record["after"]["sp"] != record["before"]["sp"]:
        raise Refused("STACK", f"SP changed {record['before']['sp']:#04x} -> {record['after']['sp']:#04x}")
    _io_ok(record["io"])
    if record["abi_claim"] not in ABI_CLAIMS:
        raise Refused("ABI_CLAIM", f"abi_claim {record['abi_claim']!r} is not permitted by this contract")
    return "OBSERVED_SYNTHETIC"


def from_core_evidence(evidence, *, case_name, rom_sha256, entry, harness_revision, step_budget):
    """Convert the named core harness case (which must target api_slot_1E) into sealed records.

    The core harness does not measure I/O or step counts; both are recorded as NOT_MEASURED rather
    than assumed clean. A cycle that did not RETURN produces no record, so it can never be accepted.
    """
    records = []
    try:
        named = [case for case in evidence["cases"] if case["name"] == case_name]
        if len(named) != 1:  # a missing or duplicated case name is ambiguous evidence, not an empty result
            raise Refused("CASE", f"{len(named)} cases named {case_name!r}; exactly one is required")
        case = named[0]
        if case["target"] != "api_slot_1E":
            raise Refused("ENTRY_MISMATCH", f"case {case_name!r} does not target api_slot_1E")
        for cycle in TEMPERATURES:
            observed = case["observed"].get(cycle)
            if observed is None or case["status"].get(cycle) != "RETURNED":
                continue
            before, after = observed["before"], observed["after"]
            item = seal({
                "schema": SCHEMA, "slot": SLOT, "entry": entry,
                "source": {"kind": "SYNTHETIC_DEV_ROM", "rom_sha256": rom_sha256},
                "core": {"name": "geargrafx", "revision": evidence["geargrafx_revision"]},
                "harness": {"name": "geargrafx_core_harness", "revision": harness_revision},
                "case": case["name"], "temperature": cycle, "status": "RETURNED",
                "inputs": {r: before[r] for r in "axyp"},  # delivered inputs as the harness observed them
                "before": before, "after": after,
                "io": {"observation": NOT_MEASURED, "accesses": []},
                "steps_used": NOT_MEASURED, "step_budget": step_budget,
                "abi_claim": "OBSERVED_SYNTHETIC",
            })
            # Only records the validator accepts leave the converter; a refused run stops the conversion.
            validate(item, rom_sha256=rom_sha256, entry=entry, core_revision=evidence["geargrafx_revision"],
                     harness_revision=harness_revision, step_budget=step_budget)
            records.append(item)
    except Refused:
        raise
    except (KeyError, TypeError, AttributeError, ValueError, RecursionError) as error:  # malformed evidence is refused, not a crash
        raise Refused("FIELDS", f"malformed core evidence: {error!r}") from error
    return records


def decide_execution(static_review, records):
    """Execution decision for the reference BIOS. Always BLOCKED: neither a static review nor any
    observation (synthetic or otherwise) grants execution in this phase."""
    if records:
        return {"execution": "BLOCKED", "reason": "SYNTHETIC_OBSERVATION_NEVER_PERMITS"}
    return {"execution": "BLOCKED", "reason": "NO_OBSERVATION_STATIC_ONLY" if static_review else "NO_OBSERVATION"}


def abi_status(records):
    """Counts for the reporting axes. ABI VERIFIED is a constant zero here by design."""
    return {
        "synthetic_observations": len(records),
        "reproducible_reference_observations": 0,
        "reference_abi_verified": 0,
        "owner_approved_for_observation": 0,
    }
