#!/usr/bin/env python3
"""Reference-case fixture validator and guarded local executor.

A reviewed slot-1e fixture now executes through the guarded C++ probe, which
independently enforces RAM write boundaries *during* execution (in-core write
protection + CPU-address breakpoints) and whose forbidden-write rejection is
proven by the synthetic negative test ``tests/integration/geargrafx_reference_guard.py``.
Observations remain LOCAL_ONLY evidence, never ABI verification.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

from reference_case_catalog import classify
from reference_bios_preflight import validate
from reference_bios_runner import Blocked, run

CATALOG_SCHEMA = "oss-pce-cd-bios/reference-test-catalog/v1"
ALLOWED_API = "slot-1e"
ALLOWED_CASES = {ALLOWED_API + "/" + category for category in
                 ("candidate_nominal", "boundary", "invalid_or_timeout")}
# The guarded probe drives a fixed synthetic caller. Fixtures must match it exactly.
GUEST_CALLER = (0xAA, 0x55, 0xCC, 0xFF)  # initial_a, initial_x, initial_y, initial_sp


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
    # Current C++ observer preloads the following fixed caller registers. Reject anything
    # else rather than pretending the fixture parameterizes guest registers.
    if tuple(fixture[n] for n in ("initial_a", "initial_x", "initial_y", "initial_sp")) != GUEST_CALLER:
        raise Blocked("fixture differs from actual hardwired guest caller")
    if fixture["expected_entry"] != "0xE05A":
        raise Blocked("entry differs from supported slot")
    if case["timeout_instructions"] != config["max_instructions"]:
        raise Blocked("case/config instruction budget mismatch")


def execute_case(case, config, adapter):
    """Validate a reviewed fixture, then run it through the guarded real-core adapter."""
    validate_case_pair(case, config)
    return run(config, adapter)


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
        observation = execute_case(matching[0], config, args.adapter)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps({"trust": "LOCAL_ONLY_NOT_VERIFIED",
                                               "observation": observation},
                                              indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print("OBSERVED LOCAL_ONLY: guarded slot-1e case executed; no VERIFIED claim")
        return 0
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
