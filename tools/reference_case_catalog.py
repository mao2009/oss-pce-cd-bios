#!/usr/bin/env python3
"""Build and audit all 270 reference-BIOS case manifests, without fabricating ABI inputs."""
import argparse
import json
from pathlib import Path
from reference_bios_scenarios import generate
from reference_abi_worklist import extract

ROOT = Path(__file__).resolve().parents[1]
TYPES = ("candidate_nominal", "boundary", "invalid_or_timeout")
KINDS = {"CD": "synthetic-disc-or-no-media", "AD": "adpcm-fixture", "BM": "isolated-backup-memory",
         "PSG": "psg-state", "EX": "cpu-video-input", "MA": "arithmetic-register-workram"}


def cases(slots, contracts):
    definitions = generate(slots)
    ledger = {row["id"]: row for row in extract(contracts)["apis"]}
    out = []
    for definition in definitions:
        key = definition["id"]
        entry = ledger.get(key)
        name = definition["candidate_name"]
        area = "PSG" if definition["kind"] == "psg_subcommand" else name.split("_")[0].split(" ")[0]
        for index, category in enumerate(TYPES):
            out.append({
                "id": key + "/" + category,
                "api_id": key,
                "candidate_name": name,
                "category": category,
                "scenario_number": index + 1,
                "entry_candidate": definition.get("entry_candidate"),
                "psg_dispatch_slot": definition.get("dispatch_slot"),
                "psg_selector_candidate": definition.get("selector_candidate"),
                "reference_contract": "spec/api_contracts.json" if entry else "docs/api-inventory.md",
                "arguments_hypothesis": (entry["contract_hypotheses"]["arguments"] if entry else None),
                "preconditions": {
                    "fixture_class": KINDS.get(area, "api-specific"),
                    "initial_cpu_state": "REVIEW_REQUIRED",
                    "memory_mapping": "REVIEW_REQUIRED",
                    "private_bios": "USER_PROVIDED",
                    "synthetic_test_media": "WHEN_REQUIRED",
                },
                "input_fixture": None,
                "observation": definition["observe"],
                "timeout_instructions": None,
                "expected": None,
                "reviewed_abi": False,
                "execution_status": "BLOCKED_ABI",
                "measurement_status": "NOT_MEASURED",
                "evidence": [],
            })
    if len(out) != 270 or len({x["id"] for x in out}) != 270:
        raise ValueError("incomplete or duplicate case catalog")
    return {"schema": "oss-pce-cd-bios/reference-test-catalog/v1",
            "source_slots": "spec/api_slots.json",
            "source_contracts": "spec/api_contracts.json",
            "claim": "PLANNED_ONLY",
            "total": 270, "cases": out}


def classify(case):
    if case.get("measurement_status") == "MEASURED":
        # Local measurement is never equivalent to verification of BIOS compatibility.
        return "LOCAL_MEASUREMENT"
    if case.get("reviewed_abi") is not True or case.get("input_fixture") is None:
        return "BLOCKED_ABI"
    if not isinstance(case.get("timeout_instructions"), int) or isinstance(case.get("timeout_instructions"), bool) or not 0 < case["timeout_instructions"] <= 10000000:
        return "BLOCKED_CONFIG"
    if case.get("expected") is None:
        return "READY_FOR_LOCAL_REFERENCE_ONLY"
    return "READY_FOR_COMPARISON"


def main(argv=None):
    a = argparse.ArgumentParser(description=__doc__)
    a.add_argument("--output", type=Path)
    a.add_argument("--summary", action="store_true")
    opts = a.parse_args(argv)
    slots = json.loads((ROOT / "spec/api_slots.json").read_text(encoding="utf-8"))["slots"]
    contracts = json.loads((ROOT / "spec/api_contracts.json").read_text(encoding="utf-8"))
    data = cases(slots, contracts)
    if opts.output:
        opts.output.parent.mkdir(parents=True, exist_ok=True)
        opts.output.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if opts.summary or not opts.output:
        counts = {}
        for item in data["cases"]:
            state = classify(item)
            counts[state] = counts.get(state, 0) + 1
        print(json.dumps({"total": data["total"], "status": counts}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
