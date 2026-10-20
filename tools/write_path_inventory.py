#!/usr/bin/env python3
"""Validate the unmonitored write-path inventory for the slot-1e in-core guard (Issue #173).

spec/write_path_inventory.json records every known CPU/device write path that the pinned
Geargrafx core can take during a reference JSR, with its monitoring status, whether the store is
stopped before its side effect, where detection happens, and the repository evidence for each
claim. This module only checks the inventory's shape and invariants. It grants nothing: every row
must stay gate_status=BLOCKED, and no code path here can set it to anything else.

Synthetic inputs only. It reads no emulator source, no BIOS and no ROM byte.

Trust boundary: a well-formed evidence entry is NOT authentic evidence. A run marker is a text line
that anyone who can write the repository can type, so a matching execution ID proves only its format.
Nothing here authenticates a producer or a run (producer authentication is #124, not implemented).
Consequently:
- A static source reference (measurement STATIC_SOURCE_REFERENCE) may back PREVENTED_STATIC only.
  It is a claim about source text, not a measurement.
- PREVENTED_MEASURED asserts a real-core measurement. It is unreachable here and fails closed.
- REAL_CORE_SYNTHETIC evidence never backs a prevention claim.
- Passing this check never changes a gate. Every row stays BLOCKED.
"""
import json
import re
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
# Fixed in code, never derived from the inventory: the inventory must declare exactly these sets.
MONITOR_STATUS_VALUES = ("PREVENTED_MEASURED", "PREVENTED_STATIC", "PREVENTED_BY_REACHABILITY",
                         "DETECTED_AT_INSTRUCTION_BOUNDARY", "DETECTED_AFTER_SIDE_EFFECT", "PERMITTED_UNISOLATED",
                         "UNMONITORED", "UNKNOWN")
MEASUREMENT_VALUES = ("REAL_CORE_SYNTHETIC", "STATIC_SOURCE_REFERENCE", "NOT_MEASURED")
# Statuses that claim a stop before the side effect. Such a claim needs measurement or static evidence.
PREVENTED_PREFIX = "PREVENTED_"
UNRESOLVED_STATUSES = frozenset({"UNMONITORED", "UNKNOWN", "DETECTED_AFTER_SIDE_EFFECT", "PERMITTED_UNISOLATED"})
# The categories every inventory must cover: MPR alias/remap, ST bypass, MMIO, IRQ, RAM, device, CPU.
REQUIRED_CATEGORIES = frozenset({"MPR", "ST", "MMIO", "IRQ", "RAM", "DEVICE", "CPU"})
# Named paths the inventory must keep, so that a silent deletion cannot hide a bypass.
REQUIRED_PATH_MARKERS = ("ST0", "ST1", "ST2", "$1802", "$1804", "TAM", "CLI", "TII", "$FF")
PREVENTION_VALUES = ("YES", "PER_WRITE", "KNOWN_PC_ONLY")
# Fixed in code, never derived from the inventory: the inventory must declare exactly this set.
PREVENTION_ENUM = PREVENTION_VALUES + ("NO", "UNKNOWN")
# The only values a post-side-effect row may record (allowlist, not denylist).
NOT_CLAIMING_VALUES = ("NO", "UNKNOWN")
# A prevention claim needs a non-synthetic basis. REAL_CORE_SYNTHETIC (synthetic BIOS/ROM/CD) never satisfies one.
PREVENTION_MEASUREMENTS = ("STATIC_SOURCE_REFERENCE",)
# Content-validated evidence: a file must carry this line, naming a run id and the measurement class it recorded.
# SELF-DECLARED, NOT AUTHENTICATED: this matches format only. It never shows that a run happened.
RUN_MARKER = re.compile(r"^measurement-run-id: (RUN-[0-9A-F]{12}) ([A-Z_]+)$", re.M)
# Unreachable by design until authenticated real-core evidence exists (#124). Rejected whatever the marker says.
MEASURED_PREVENTED_STATUS = "PREVENTED_MEASURED"
PRE_SIDE_EFFECT_POINTS = ("BEFORE_RUN", "BEFORE_STORE", "INSTRUCTION_BOUNDARY")
NO_MONITOR_STATUSES = ("UNMONITORED", "PERMITTED_UNISOLATED")
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
    for key, pinned, message in (
            ("monitor_status_values", MONITOR_STATUS_VALUES, "monitor_status_values must equal the fixed code set"),
            ("prevents_before_side_effect_values", PREVENTION_ENUM,
             "prevents_before_side_effect_values must equal the fixed code allowlist"),
            ("measurement_values", MEASUREMENT_VALUES, "measurement_values must equal the fixed code set")):
        declared = inventory.get(key)
        require(isinstance(declared, list) and sorted(declared) == sorted(pinned), message)
    statuses = set(MONITOR_STATUS_VALUES)
    measurements = set(MEASUREMENT_VALUES)
    categories = set(inventory.get("categories", []))
    require(categories == REQUIRED_CATEGORIES, "categories must be exactly the required set")
    rows = inventory.get("rows")
    require(isinstance(rows, list) and rows, "rows must be a non-empty list")

    seen_ids = set()
    claimed_runs = {}
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
        require(row["prevents_before_side_effect"] in PREVENTION_ENUM, f"{row_id}: unknown prevents value")
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
        if row["monitor_status"].startswith(PREVENTED_PREFIX):
            require(row["prevents_before_side_effect"] in PREVENTION_VALUES,
                    f"{row_id}: prevention status with non-prevention value")
        require(row["monitor_status"] != MEASURED_PREVENTED_STATUS,
                f"{row_id}: PREVENTED_MEASURED requires authenticated real-core evidence (#124 not implemented)")
        # A row whose side effect lands before detection cannot claim a stop before it, whatever its status name
        # (full, partial or KNOWN_PC_ONLY). Keyed on the detection point as well as the status.
        if row["monitor_status"] == "DETECTED_AFTER_SIDE_EFFECT" or row["detection_point"] == "AFTER_SIDE_EFFECT":
            require(row["prevents_before_side_effect"] in NOT_CLAIMING_VALUES,
                    f"{row_id}: post-side-effect detection cannot claim prevention before side effect")
        # A path that is unmonitored or permitted by design has no stop before the side effect.
        if row["monitor_status"] in NO_MONITOR_STATUSES:
            require(row["prevents_before_side_effect"] in ("NO", "UNKNOWN"),
                    f"{row_id}: UNMONITORED or PERMITTED_UNISOLATED cannot claim prevention")
        # A row detected after the side effect must name the after-side-effect point.
        if row["monitor_status"] == "DETECTED_AFTER_SIDE_EFFECT":
            require(row["detection_point"] == "AFTER_SIDE_EFFECT",
                    f"{row_id}: DETECTED_AFTER_SIDE_EFFECT requires detection point AFTER_SIDE_EFFECT")
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
        # A prevention claim, full or partial (KNOWN_PC_ONLY included), must name a detection point before the side effect.
        if row["prevents_before_side_effect"] in PREVENTION_VALUES:
            require(row["detection_point"] in PRE_SIDE_EFFECT_POINTS,
                    f"{row_id}: prevention requires a pre-side-effect detection point")
            require(row["measurement"] in PREVENTION_MEASUREMENTS,
                    f"{row_id}: prevention claimed without measurement")
            # Existence of the evidence file is not enough: its content must carry a run id for this measurement.
            markers = []
            for item in evidence:
                markers += RUN_MARKER.findall((root / item).read_text(encoding="utf-8", errors="replace"))
            require(any(kind == row["measurement"] for _, kind in markers),
                    f"{row_id}: prevention claimed without content-validated run evidence")
            # Tampered or mixed evidence (conflicting markers or two run ids) fails closed.
            run_ids = {run for run, _ in markers}
            require(len(run_ids) == 1 and all(kind == row["measurement"] for _, kind in markers),
                    f"{row_id}: conflicting run markers in evidence (tampered or mixed)")
            (run_id,) = run_ids
            # One run id backs one claim. Reusing another row's result for this claim is refused.
            require(claimed_runs.setdefault(run_id, row_id) == row_id,
                    f"{row_id}: run id {run_id} is reused from {claimed_runs.get(run_id)}")

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
