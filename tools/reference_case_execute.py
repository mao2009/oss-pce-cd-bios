#!/usr/bin/env python3
"""Opt-in case-level execution bridge; only independently reviewed slot-1e works.

Never calls an unknown ABI. A local reference observation is not an oracle,
not a compatible ABI verdict, and never sufficient for VERIFIED.
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
    validate_case_pair(case, config)
    observation = run(config, adapter)
    if observation.get("checkpoint") != "synthetic_guest_jsr_return_not_abi_verified":
        raise Blocked("unexpected checkpoint for case")
    return {"id": case["id"], "api_id": case["api_id"],
            "status": "LOCAL_OBSERVATION_ONLY", "expectation": "NOT_MEASURED",
            "observation": observation}


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
        result = execute_case(matching[0], config, args.adapter)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print("LOCAL_OBSERVATION_ONLY: reference BIOS guest JSR observed; no ABI/compatibility PASS")
        return 0
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
