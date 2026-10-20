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
- A marker alone is not enough. The evidence file must also carry a record bound to the row (row-id,
  write-path-category, exact write-path, detection-point, prevention, and a non-placeholder evidence-summary).
  The record is still self-declared, and a copy of public inventory data passes it. So every prevention claim is
  refused at the end of validation until an authenticated run producer exists (#124, AUTHENTICATED_RUN_PRODUCER).
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
# Named paths the inventory must keep, each bound to its exact row id. A substring anywhere in the inventory is not
# enough: "$FF" also appears in W08, so deleting W04 would otherwise go unnoticed.
REQUIRED_ROW_MARKERS = {"W03": "TAM", "W04": "$FF", "W05": "ST0", "W06": "ST1", "W07": "ST2", "W08": "$1802",
                        "W12": "$1804", "W14": "CLI", "W15": "TII"}
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
# Template or filler ids (RUN-000000000000, RUN-FFFFFFFFFFFF) match the format but name no run. Always refused.
PLACEHOLDER_RUN = re.compile(r"^RUN-([0-9A-F])\1{11}$")
# Evidence record: the marker's file must also carry these `key: value` lines, each bound to the row it backs.
# Values are compared exactly with the row (id, category, path, detection point, prevention). The summary is free text
# that must be substantive and must not be a template word. Self-declared like the marker, so it is still not proof of a run.
# No authenticated run producer exists (#124). Until one does, no prevention claim may pass, whatever the file holds.
# Flip only together with a real, authenticated producer; this is the single upgrade point.
AUTHENTICATED_RUN_PRODUCER = False
RECORD_BINDINGS = ("row-id", "write-path-category", "write-path", "detection-point", "prevention")
RECORD_SUMMARY = "evidence-summary"
RECORD_SUMMARY_MIN = 20
FIELD_LINE = re.compile(r"^([a-z][a-z-]*): (.+)$", re.M)
PLACEHOLDER_TEXT = re.compile(r"(?i)\b(TBD|TODO|FIXME|XXX|PLACEHOLDER|LOREM|DUMMY|N/A)\b")


def _record_problem(row, text):
    """Return why the record in text does not back row's claim, or None when it does."""
    fields = {}
    for key, value in FIELD_LINE.findall(text):
        if key in fields:
            return f"duplicate field {key}"
        fields[key] = value.strip()
    expected = {"row-id": row["id"], "write-path-category": row["category"], "write-path": row["path"],
                "detection-point": row["detection_point"], "prevention": row["prevents_before_side_effect"]}
    for key in RECORD_BINDINGS:
        if key not in fields:
            return f"missing field {key}"
        if fields[key] != expected[key]:
            return f"field {key} does not match the row"
    if RECORD_SUMMARY not in fields:
        return f"missing field {RECORD_SUMMARY}"
    summary = fields[RECORD_SUMMARY]
    if len(summary) < RECORD_SUMMARY_MIN or PLACEHOLDER_TEXT.search(summary):
        return f"field {RECORD_SUMMARY} is empty or a placeholder"
    return None
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
            # Contradiction guard: a prevention claim needs a PREVENTED_* status, so a DETECTED_* or UNKNOWN row
            # cannot carry prevention, whatever its detection point says.
            require(row["monitor_status"].startswith(PREVENTED_PREFIX),
                    f"{row_id}: prevention claim requires a PREVENTED_* monitor_status")
            require(row["detection_point"] in PRE_SIDE_EFFECT_POINTS,
                    f"{row_id}: prevention requires a pre-side-effect detection point")
            require(row["measurement"] in PREVENTION_MEASUREMENTS,
                    f"{row_id}: prevention claimed without measurement")
            # Existence of the evidence file is not enough: its content must carry a run id for this measurement.
            markers = []
            texts = {}
            for item in evidence:
                texts[item] = (root / item).read_text(encoding="utf-8", errors="replace")
                markers += RUN_MARKER.findall(texts[item])
            require(any(kind == row["measurement"] for _, kind in markers),
                    f"{row_id}: prevention claimed without content-validated run evidence")
            # Tampered or mixed evidence (conflicting markers or two run ids) fails closed.
            run_ids = {run for run, _ in markers}
            require(len(run_ids) == 1 and all(kind == row["measurement"] for _, kind in markers),
                    f"{row_id}: conflicting run markers in evidence (tampered or mixed)")
            (run_id,) = run_ids
            require(not PLACEHOLDER_RUN.match(run_id), f"{row_id}: placeholder run id {run_id} is not evidence")
            # One run id backs one claim. Reusing another row's result for this claim is refused.
            require(claimed_runs.setdefault(run_id, row_id) == row_id,
                    f"{row_id}: run id {run_id} is reused from {claimed_runs.get(run_id)}")
            # A marker alone is not evidence. Every file carrying the marker must hold a complete record bound to
            # this row: its id, category, exact path, detection point, prevention value and a real summary.
            for item in evidence:
                if RUN_MARKER.search(texts[item]):
                    problem = _record_problem(row, texts[item])
                    require(problem is None, f"{row_id}: evidence record rejected in {item}: {problem}")
            # Final gate, after the format checks above. A record copied from public data (this inventory) passes
            # them, so no evidence file can establish a prevention claim until an authenticated run producer exists.
            require(AUTHENTICATED_RUN_PRODUCER,
                    f"{row_id}: {row['monitor_status']} requires an authenticated run producer (#124); none exists")

        counts[row["monitor_status"]] = counts.get(row["monitor_status"], 0) + 1

    require(covered_categories == REQUIRED_CATEGORIES, f"missing categories: {sorted(REQUIRED_CATEGORIES - covered_categories)}")
    by_id = {r["id"]: r for r in rows}
    for row_id, marker in REQUIRED_ROW_MARKERS.items():
        require(row_id in by_id and marker in by_id[row_id]["path"],
                f"required path marker missing from inventory: {row_id} ({marker})")
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
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"FAIL: write-path inventory invalid: {error}")
        return 1
    print(f"PASS: write-path inventory valid; rows={summary['rows']} unresolved={summary['unresolved']} "
          f"gate={summary['gate_status']}")
    print(json.dumps(summary["status_counts"], ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
