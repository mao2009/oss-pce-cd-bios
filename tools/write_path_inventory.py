#!/usr/bin/env python3
"""Validate the unmonitored write-path inventory for the slot-1e in-core guard (Issue #173).

spec/write_path_inventory.json records every known CPU/device write path that the pinned
Geargrafx core can take during a reference JSR, with its monitoring status, whether the store is
stopped before its side effect, where detection happens, and the repository evidence for each
claim. This module only checks the inventory's shape and invariants. It grants nothing: every row
must stay gate_status=BLOCKED, and no code path here can set it to anything else.

Synthetic inputs only. It reads no emulator source, no BIOS and no ROM byte.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INVENTORY_PATH = ROOT / "spec" / "write_path_inventory.json"
DEPENDENCIES_PATH = ROOT / "tools" / "dependencies.json"
SCHEMA = "oss-pce-cd-bios/write-path-inventory/v1"
GATE_LEDGER = "spec/reference_jsr_gate.json"
ROW_KEYS = frozenset({"id", "path", "category", "monitor_status", "prevents_before_side_effect",
                      "detection_point", "measurement", "evidence", "gate_status", "notes"})
DETECTION_POINTS = ("BEFORE_RUN", "BEFORE_STORE", "INSTRUCTION_BOUNDARY", "AFTER_SIDE_EFFECT", "NONE", "UNKNOWN")
# Statuses that claim a stop before the side effect. Such a claim needs measurement or static evidence.
PREVENTED_PREFIX = "PREVENTED_"
UNRESOLVED_STATUSES = frozenset({"UNMONITORED", "UNKNOWN", "DETECTED_AFTER_SIDE_EFFECT", "PERMITTED_UNISOLATED"})
# The categories every inventory must cover: MPR alias/remap, ST bypass, MMIO, IRQ, RAM, device, CPU.
REQUIRED_CATEGORIES = frozenset({"MPR", "ST", "MMIO", "IRQ", "RAM", "DEVICE", "CPU"})
# Named paths the inventory must keep, so that a silent deletion cannot hide a bypass.
REQUIRED_PATH_MARKERS = ("ST0", "ST1", "ST2", "$1802", "$1804", "TAM", "CLI", "TII", "$FF")
PREVENTION_VALUES = ("YES", "PER_WRITE", "KNOWN_PC_ONLY")
PIN_KEY = "geargrafx"


def _no_duplicate_keys(pairs):
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError(f"duplicate JSON key: {sorted({k for k in keys if keys.count(k) > 1})}")
    return dict(pairs)


def validate(inventory, pinned_revision, root=ROOT):
    """Raise ValueError on any malformed, drifting or unlocking inventory. Return a summary dict."""
    def require(ok, message):
        if not ok:
            raise ValueError(message)

    require(isinstance(inventory, dict), "inventory must be an object")
    require(inventory.get("schema") == SCHEMA, "schema mismatch")
    require(inventory.get("pinned_core_revision") == pinned_revision,
            "pinned_core_revision drift from tools/dependencies.json")
    require(inventory.get("gate_ledger") == GATE_LEDGER, "gate_ledger reference mismatch")
    require(inventory.get("gate_status_default") == "BLOCKED", "gate_status_default must be BLOCKED")
    statuses = set(inventory.get("monitor_status_values", []))
    prevents = set(inventory.get("prevents_before_side_effect_values", []))
    measurements = set(inventory.get("measurement_values", []))
    categories = set(inventory.get("categories", []))
    require(categories == REQUIRED_CATEGORIES, "categories must be exactly the required set")
    rows = inventory.get("rows")
    require(isinstance(rows, list) and rows, "rows must be a non-empty list")

    seen_ids = set()
    counts = {}
    covered_categories = set()
    text = ""
    for row in rows:
        require(isinstance(row, dict) and set(row) == ROW_KEYS, f"row keys: {row.get('id') if isinstance(row, dict) else row}")
        row_id = row["id"]
        require(isinstance(row_id, str) and row_id.startswith("W") and row_id[1:].isdigit(), f"bad row id {row_id!r}")
        require(row_id not in seen_ids, f"duplicate row id {row_id}")
        seen_ids.add(row_id)
        require(row["category"] in categories, f"{row_id}: unknown category")
        covered_categories.add(row["category"])
        require(row["monitor_status"] in statuses, f"{row_id}: unknown monitor_status")
        require(row["prevents_before_side_effect"] in prevents, f"{row_id}: unknown prevents value")
        require(row["detection_point"] in DETECTION_POINTS, f"{row_id}: unknown detection_point")
        require(row["measurement"] in measurements, f"{row_id}: unknown measurement")
        require(isinstance(row["path"], str) and row["path"], f"{row_id}: path empty")
        require(isinstance(row["notes"], str) and row["notes"], f"{row_id}: notes empty")
        # Fail closed: the inventory can only record BLOCKED. Nothing here unlocks a path.
        require(row["gate_status"] == "BLOCKED", f"{row_id}: gate_status must be BLOCKED")
        evidence = row["evidence"]
        require(isinstance(evidence, list) and evidence, f"{row_id}: evidence list empty")
        for item in evidence:
            require(isinstance(item, str) and item and not Path(item).is_absolute() and ".." not in Path(item).parts,
                    f"{row_id}: evidence must be a repository-relative path")
            require((root / item).is_file(), f"{row_id}: evidence file missing: {item}")
        # Any prevention value (YES, PER_WRITE, KNOWN_PC_ONLY) must be backed by a measurement or a static
        # reference, never NOT_MEASURED.
        if row["prevents_before_side_effect"] in PREVENTION_VALUES:
            require(row["measurement"] != "NOT_MEASURED", f"{row_id}: prevention claimed without measurement")
        if row["monitor_status"].startswith(PREVENTED_PREFIX):
            require(row["prevents_before_side_effect"] in PREVENTION_VALUES,
                    f"{row_id}: prevention status with non-prevention value")
        # A row detected after the side effect has landed cannot also claim a stop before it.
        if row["monitor_status"] == "DETECTED_AFTER_SIDE_EFFECT":
            require(row["prevents_before_side_effect"] in ("NO", "KNOWN_PC_ONLY"),
                    f"{row_id}: DETECTED_AFTER_SIDE_EFFECT cannot claim prevention before side effect")
        # An UNKNOWN row has no recorded detection point, so it cannot claim any prevention value.
        if row["monitor_status"] == "UNKNOWN":
            require(row["prevents_before_side_effect"] in ("UNKNOWN", "NO"),
                    f"{row_id}: UNKNOWN status must not claim a prevention value")
        # A row that is not prevented before the side effect must not be recorded as prevented.
        if row["prevents_before_side_effect"] == "NO":
            require(row["monitor_status"] not in ("PREVENTED_MEASURED", "PREVENTED_STATIC"),
                    f"{row_id}: NO prevention but a PREVENTED status")
        if row["monitor_status"] in UNRESOLVED_STATUSES:
            require(row["gate_status"] == "BLOCKED", f"{row_id}: unresolved path must remain BLOCKED")
        counts[row["monitor_status"]] = counts.get(row["monitor_status"], 0) + 1
        text += " " + row["path"]

    require(covered_categories == REQUIRED_CATEGORIES, f"missing categories: {sorted(REQUIRED_CATEGORIES - covered_categories)}")
    for marker in REQUIRED_PATH_MARKERS:
        require(marker in text, f"required path marker missing from inventory: {marker}")
    unresolved = sum(counts.get(s, 0) for s in UNRESOLVED_STATUSES)
    return {"rows": len(rows), "unresolved": unresolved, "status_counts": dict(sorted(counts.items())),
            "gate_status": "BLOCKED"}


def load(path=INVENTORY_PATH):
    inventory = json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=_no_duplicate_keys)
    dependencies = json.loads(DEPENDENCIES_PATH.read_text(encoding="utf-8"))
    return validate(inventory, dependencies[PIN_KEY]["revision"]), inventory


def main(argv=None):
    if argv not in (None, [], ["--check"]):
        print("usage: write_path_inventory.py [--check]", file=sys.stderr)
        return 2
    try:
        summary, _ = load()
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(f"FAIL: write-path inventory invalid: {error}")
        return 1
    print(f"PASS: write-path inventory valid; rows={summary['rows']} unresolved={summary['unresolved']} "
          f"gate={summary['gate_status']}")
    print(json.dumps(summary["status_counts"], ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
