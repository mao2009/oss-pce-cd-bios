#!/usr/bin/env python3
"""Reference-case fixture validator; execution is currently blocked.

Even a reviewed slot-1e fixture cannot execute until the real emulator core
independently enforces runtime RAM/I/O write boundaries. JSON observations and
post-run diffs are not enforcement and cannot be used as an ABI oracle.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

from reference_case_catalog import classify
from reference_bios_preflight import validate
from reference_bios_runner import Blocked

CATALOG_SCHEMA = "oss-pce-cd-bios/reference-test-catalog/v1"
ALLOWED_API = "slot-1e"
ALLOWED_CASES = {ALLOWED_API + "/" + category for category in
                 ("candidate_nominal", "boundary", "invalid_or_timeout")}


def validate_case_pair(case, config):
    if case.get("id") not in ALLOWED_CASES or case.get("api_id") != ALLOWED_API:
        raise Blocked("API not implemented in reference adapter")
    if classify(case) != "READY_FOR_LOCAL_REFERENCE_ONLY":
        raise Blocked("case not reviewed or contains unsupported expectation")
    if type(config) is not dict or config.get("api_id") != case["api_id"]:
        raise Blocked("fixture API mismatch")
    problems = validate(config)
    if problems:
        raise Blocked("; ".join(problems))
    if not isinstance(case.get("input_fixture"), dict):
        raise Blocked("case fixture is not a structured object")
    fixture = case["input_fixture"]
    if set(fixture) != {"mode", "initial_a", "initial_x", "initial_y", "initial_sp", "expected_entry"}:
        raise Blocked("fixture schema mismatch")
    if fixture["mode"] != "synthetic_guest_jsr":
        raise Blocked("unknown execution mode")
    if any(type(fixture[n]) is not int or not 0 <= fixture[n] <= 255
           for n in ("initial_a", "initial_x", "initial_y", "initial_sp")):
        raise Blocked("invalid register fixture")
    # Current C++ observer uses the following explicit constants. Reject anything else
    # rather than pretending that it parameterizes guest registers.
    if tuple(fixture[n] for n in ("initial_a", "initial_x", "initial_y", "initial_sp")) != (0, 0, 0, 255):
        raise Blocked("fixture differs from actual hardwired guest caller")
    if fixture["expected_entry"] != "0xE05A":
        raise Blocked("entry differs from supported slot")
    if case["timeout_instructions"] != config["max_instructions"]:
        raise Blocked("case/config instruction budget mismatch")


def execute_case(case, config, adapter):
    """Validate a proposed fixture, then refuse unguarded reference API execution.

    Do not change this gate to call the adapter until the C++ core enforces
    write allowlists *during* execution (including mapped devices) and a
    real-core negative test demonstrates a forbidden write is prevented.
    """
    validate_case_pair(case, config)
    raise Blocked("runtime RAM/I/O write enforcement NOT_IMPLEMENTED; reference API execution BLOCKED")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("catalog", type=Path)
    p.add_argument("--case-id", required=True)
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--adapter", type=Path, required=True)
    p.add_argument("--output", type=Path)
    args = p.parse_args(argv)
    try:
        document = json.loads(args.catalog.read_text(encoding="utf-8"))
        if document.get("schema") != CATALOG_SCHEMA or len(document.get("cases", [])) != 270:
            raise Blocked("invalid full catalog")
        matching = [x for x in document["cases"] if x.get("id") == args.case_id]
        if len(matching) != 1:
            raise Blocked("case ID absent or duplicated")
        config = json.loads(args.config.read_text(encoding="utf-8"))
        execute_case(matching[0], config, args.adapter)
        # Defense in depth: no success or output path until runtime guarding is implemented.
        raise Blocked("reference API execution remains disabled")
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
