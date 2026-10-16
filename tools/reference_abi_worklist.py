#!/usr/bin/env python3
"""Extract source-attributed ABI hypotheses into a non-executable fixture worklist."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIELDS = ("arguments", "returns", "clobbered", "preserved", "stack", "mpr",
          "ram", "flags", "irq", "errors", "synchronization")


def extract(doc):
    result = []
    if len(doc["apis"]) != 81:
        raise ValueError("expected 81 API contracts")
    for api in doc["apis"]:
        fields = {}
        for key in FIELDS:
            item = api["contract"][key]
            fields[key] = {
                "status": item["status"],
                "hypothesis": item["value"] if item["status"] != "UNKNOWN" else None,
                "sources": item["sources"],
            }
        result.append({
            "id": "slot-%02x" % api["slot"],
            "name": api["candidate_name"],
            "entry_candidate": api["candidate_entry"],
            "mapping_status": api["mapping_status"],
            "contract_hypotheses": fields,
            "test_input_fixture": None,
            "test_rom_path": None,
            "reference_expectation": None,
            "measurement_status": "NOT_MEASURED",
            "execution_status": "BLOCKED_ABI_REVIEW",
        })
    return {"schema": "oss-pce-cd-bios/reference-abi-worklist/v1",
            "status": "NOT_MEASURED", "source": "spec/api_contracts.json",
            "apis": result}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", type=Path)
    a = ap.parse_args(argv)
    doc = json.loads((ROOT / "spec/api_contracts.json").read_text(encoding="utf-8"))
    result = extract(doc)
    if a.output:
        a.output.parent.mkdir(parents=True, exist_ok=True)
        a.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    else:
        print("81 source-attributed ABI worklists generated; zero runnable fixtures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
