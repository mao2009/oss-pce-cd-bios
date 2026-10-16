#!/usr/bin/env python3
"""All-270 case scheduler: only reviewed cases can run; no implicit API support."""
import argparse
import json
import sys
from pathlib import Path
from reference_case_catalog import classify

SCHEMA = "oss-pce-cd-bios/reference-test-catalog/v1"


def plan(catalog):
    if catalog.get("schema") != SCHEMA or type(catalog.get("cases")) is not list:
        raise ValueError("invalid catalog")
    if len(catalog["cases"]) != 270 or len({x["id"] for x in catalog["cases"]}) != 270:
        raise ValueError("catalog must contain exactly 270 distinct scenarios")
    return [{"id": x["id"], "api_id": x["api_id"], "state": classify(x),
             "runnable": False, "reason": "No audited API-specific guest fixture / real-core call contract"
             if classify(x).startswith("READY") else "ABI / inputs / execution prerequisites not established"}
            for x in catalog["cases"]]


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("catalog", type=Path)
    p.add_argument("--output", type=Path)
    a = p.parse_args(argv)
    try:
        catalog = json.loads(a.catalog.read_text(encoding="utf-8"))
        items = plan(catalog)
        result = {"schema": "oss-pce-cd-bios/reference-dispatch-report/v1",
                  "claims": "NO_API_EXECUTION", "total": 270,
                  "executed": 0, "cases": items}
        if a.output:
            a.output.parent.mkdir(parents=True, exist_ok=True)
            a.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                                encoding="utf-8")
        counts = {}
        for case in items:
            counts[case["state"]] = counts.get(case["state"], 0) + 1
        print(json.dumps({"total": 270, "executed": 0, "statuses": counts}, sort_keys=True))
        return 0
    except (OSError, ValueError, TypeError, KeyError) as e:
        print(f"BLOCKED: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
