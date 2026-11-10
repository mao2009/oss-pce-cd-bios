#!/usr/bin/env python3
"""CD_BASE slot $02 observation catalogue and sanitized record schema (Issue #186).

NO EXECUTION. This module runs no JSR, reads no ROM/BIOS/disc and grants no permission. It
defines (1) which slot-$02 facts can be obtained without executing the genuine callee and which
need a real JSR, (2) a sanitized, fail-closed record format that the #635 caller-side entry
observer (docs/635-api-entry-observation.md) and a future guarded genuine-callee run can share,
and (3) a stat-derived media classifier that reports only structural booleans (never LBA values).

Evidence classes are never merged: a caller-side entry observation (the commercial IPL's own
JSR into the table, with a synthetic stub as callee) is not a CD_BASE callee measurement, and
host arithmetic is not either. A record that claims a genuine-callee measurement is rejected
while the #228 gate refuses slot $02 (tools/reference_jsr_gate.py).
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cd_base_lba_candidates as C  # noqa: E402

SCHEMA = "oss-pce-cd-bios/cd-base-slot02-observation/v1"
SLOT = "$02"
TABLE_ENTRY = "$E006"  # BIOS jump table $E000 + 3 * 2 (docs/635-api-entry-observation.md table range)

EVIDENCE_CLASSES = (
    "HOST_ARITHMETIC",          # candidate-model arithmetic on synthetic discs
    "SYNTHETIC_REAL_CORE",      # pinned core, synthetic BIOS/disc (instrument validation)
    "COMMERCIAL_STAT_ONLY",     # CUE text + BIN lstat size only; no content read
    "COMMERCIAL_CALLER_ENTRY",  # #635: commercial IPL's own JSR/JMP into table, synthetic stub
    "GENUINE_CALLEE",           # genuine System Card callee executed (gate-blocked today)
)
STATUSES = ("NOT_RUN", "SKIP", "BLOCKED", "UNKNOWN", "SOURCE_CONFIRMED", "MEASURED")
# VERIFIED / DEV_CALLABLE are deliberately not representable.

# tier: where an observable can come from. REAL_JSR means the genuine callee must execute.
OBSERVABLES = {
    "O1_table_slot_address":   {"tier": "STATIC", "what": "slot $02 -> table entry $E006 (3-byte stride)"},
    "O2_media_structure":      {"tier": "STAT_ONLY", "what": "data-track INDEX01/INDEX00 relations as booleans (not values)"},
    "O3_candidate_classes":    {"tier": "HOST_ARITHMETIC", "what": "which LBA-model classes the media can separate"},
    "O4_instrument_validity":  {"tier": "SYNTHETIC_REAL_CORE", "what": "#635 observer classifies jsr_slot02 vs jmp/direct/nocall"},
    "O5_caller_first_api":     {"tier": "CALLER_ENTRY", "what": "commercial IPL first table entry: slot, JSR/JMP/DIRECT, return-matches-caller"},
    "O6_caller_sets_regs":     {"tier": "CALLER_ENTRY", "what": "entry A/X/Y/P/MPR at the call site (private-local; public record carries a boolean only)"},
    "O7_callee_input_bytes":   {"tier": "REAL_JSR", "what": "which CD_BASE input bytes the callee reads (1-byte differential)"},
    "O8_read_lba":             {"tier": "REAL_JSR", "what": "SCSI READ LBA after CD_BASE -> CD_READ (LBA vs INDEX01 coordinate)"},
    "O9_callee_return":        {"tier": "REAL_JSR", "what": "A/X/Y/P and return position on exit"},
    "O10_callee_mpr_ram":      {"tier": "REAL_JSR", "what": "MPR and RAM side effects"},
    "O11_reset_retention":     {"tier": "REAL_JSR", "what": "base retention across CD_RESET (#187)"},
    "O12_failure_retry":       {"tier": "REAL_JSR", "what": "retry/recbase switch after READ failure injection"},
}
# Tiers that never need a genuine-callee run.
NO_JSR_TIERS = ("STATIC", "STAT_ONLY", "HOST_ARITHMETIC", "SYNTHETIC_REAL_CORE", "CALLER_ENTRY")

# Which evidence class may legitimately carry which tier.
CLASS_TIERS = {
    "HOST_ARITHMETIC": ("STATIC", "HOST_ARITHMETIC"),
    "SYNTHETIC_REAL_CORE": ("SYNTHETIC_REAL_CORE",),
    "COMMERCIAL_STAT_ONLY": ("STAT_ONLY",),
    "COMMERCIAL_CALLER_ENTRY": ("CALLER_ENTRY",),
    "GENUINE_CALLEE": ("REAL_JSR",),
}

# Defence in depth: key names that would leak private/proprietary material. The structural
# whitelist below already fixes every allowed key; this scan also rejects camelCase/concatenated
# spellings and non-string keys, anywhere in the record.
FORBIDDEN_KEY_SUBSTRINGS = ("path", "hash", "sha", "md5", "crc", "bytes", "hex", "title", "name", "sector",
                            "payload", "dump", "audio", "save", "cue", "firmware", "address", "file", "rom",
                            "lba", "pc", "bin")
FORBIDDEN_VALUE_WORDS = ("VERIFIED", "DEVCALLABLE", "COMPATIBLE")
TOP_KEYS = frozenset({"schema", "evidence_class", "slot", "tool", "core_revision", "jsr_count",
                      "status", "reason", "items"})
ITEM_KEYS = frozenset({"status", "value"})
CALLER_METHODS = ("JSR", "JMP", "DIRECT", "NONE")
CALLER_VERDICTS = ("API_ENTRY_CONFIRMED", "PRE_API_BLOCKER_OBSERVED", "UNKNOWN", "INCOMPLETE",
                   "BUDGET", "NOT_RUN", "BLOCKED", "FAIL")
PINNED_CORE_REVISION = "b49ae82a012eade566d72e9d61c9d95a5caa4da3"
REASON_CODES = frozenset({  # reason is an allowlisted code, never free text
    "GATE_REFUSES_SLOT_02", "NONE_RUN", "CALLER_SIDE_ENTRY_ONLY", "STAT_ONLY", "HOST_ARITHMETIC_ONLY",
    "SYNTHETIC_ONLY", "NO_LOCAL_MEDIA", "FORMAT_UNSUPPORTED", "OWNER_OPT_IN_REQUIRED", "X",
})
TOOL_RE = re.compile(r"[a-z0-9_]{0,40}")
O2_KEYS = frozenset({"index01_nonzero", "index00_differs_from_zero", "data_tracks_after_first"})
O5_KEYS = frozenset({"verdict", "first_table_slot", "method", "in_table", "return_matches_caller"})
VALUE_STATUSES = ("MEASURED", "SOURCE_CONFIRMED")
# Keys that are part of the fixed schema (value-object keys) and spelled with a forbidden fragment
# only coincidentally ('from' contains 'rom').
SCHEMA_KEYS = O2_KEYS | O5_KEYS | TOP_KEYS | ITEM_KEYS


def _walk(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k, v
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def check_no_leak_keys(obj):
    """Raise ValueError for any non-string key, or any key outside the fixed schema vocabulary
    that contains a forbidden spelling (case- and underscore-insensitive)."""
    for key, _ in _walk(obj):
        if type(key) is not str:
            raise ValueError(f"non-string key {key!r}")
        if key in OBSERVABLES or key in SCHEMA_KEYS:
            continue
        low = key.lower().replace("_", "")
        if any(frag in low for frag in FORBIDDEN_KEY_SUBSTRINGS):
            raise ValueError(f"forbidden key {key!r}")


def _no_overclaim(text):
    squashed = re.sub(r"[^A-Za-z]", "", text).upper()
    return not any(w in squashed for w in FORBIDDEN_VALUE_WORDS)


def _check_value(name, status, value, req):
    if status not in VALUE_STATUSES:
        req(value is None, "non-valued item must have a null value")
        return
    req(value is not None, "valued item needs a value")
    if name == "O1_table_slot_address":
        req(type(value) is bool, "O1 value is a boolean (entry matches $E006)")
    elif name == "O2_media_structure":
        req(isinstance(value, dict) and set(value) == O2_KEYS and all(type(x) is bool for x in value.values()),
            "O2 value: three booleans only")
    elif name == "O3_candidate_classes":
        req(isinstance(value, list) and value and all(isinstance(g, list) and g and all(
            type(n) is str and n in C.CANDIDATE_NAMES for n in g) for g in value),
            "O3 value: non-empty list of non-empty candidate-name lists")
        flat = [n for g in value for n in g]
        req(len(flat) == len(set(flat)), "O3 groups must be disjoint")
    elif name == "O4_instrument_validity":
        req(type(value) is bool, "O4 value is a boolean")
    elif name == "O5_caller_first_api":
        req(isinstance(value, dict) and set(value) == O5_KEYS, "O5 value keys")
        slot = value["first_table_slot"]
        req(value["verdict"] in CALLER_VERDICTS and value["method"] in CALLER_METHODS, "O5 verdict/method")
        req(slot is None or (type(slot) is int and 0 <= slot <= 80), "O5 first_table_slot")
        req(type(value["in_table"]) is bool, "O5 in_table")
        ret = value["return_matches_caller"]
        req(ret is None or type(ret) is bool, "O5 return flag")
        if value["method"] == "NONE":
            req(slot is None and not value["in_table"] and ret is None and value["verdict"] != "API_ENTRY_CONFIRMED",
                "O5 NONE method is inconsistent with an entry")
        if value["verdict"] == "API_ENTRY_CONFIRMED":
            req(value["method"] != "NONE", "O5 confirmed entry needs a method")
        if not value["in_table"]:
            req(value["method"] in ("DIRECT", "NONE") and slot is None,
                "O5 non-table entry is DIRECT/NONE without a slot")
        if value["method"] in ("JSR", "JMP"):
            req(value["in_table"] and slot is not None, "O5 JSR/JMP is a table entry with a slot")
    elif name == "O6_caller_sets_regs":
        req(type(value) is bool, "O6 public value is a boolean only")
    else:
        req(False, f"{name} cannot carry a value")  # REAL_JSR items: never valued


def validate_record(rec):
    """Raise ValueError on a malformed, leaking or over-claiming record; return it."""
    def req(ok, msg):
        if not ok:
            raise ValueError(msg)

    req(isinstance(rec, dict), "record must be an object")
    check_no_leak_keys(rec)
    req(set(rec) == TOP_KEYS, "record keys")
    req(rec["schema"] == SCHEMA, "schema")
    req(rec["slot"] == SLOT, "slot must be $02")
    cls = rec["evidence_class"]
    req(cls in EVIDENCE_CLASSES, "evidence_class")
    req(rec["status"] in STATUSES, "status")
    req(isinstance(rec["reason"], str) and rec["reason"] in REASON_CODES, "reason must be an allowlisted code")
    req(isinstance(rec["tool"], str) and TOOL_RE.fullmatch(rec["tool"]) and _no_overclaim(rec["tool"]), "tool")
    req(type(rec["jsr_count"]) is int and rec["jsr_count"] == 0,
        "jsr_count must be 0: no genuine JSR is recordable while the gate refuses slot $02")
    items = rec["items"]
    req(isinstance(items, dict), "items")

    for name, item in items.items():
        req(name in OBSERVABLES, f"unknown observable {name!r}")
        req(isinstance(item, dict) and set(item) == ITEM_KEYS, "item keys")
        req(item["status"] in STATUSES, "item status")
        tier = OBSERVABLES[name]["tier"]
        req(tier in CLASS_TIERS[cls], f"{name} (tier {tier}) cannot be carried by evidence class {cls}")
        if tier == "REAL_JSR":
            req(item["status"] not in VALUE_STATUSES, "genuine-callee measurement refused: slot $02 gate is BLOCKED")
        _check_value(name, item["status"], item["value"], req)

    measured = [i for i in items.values() if i["status"] == "MEASURED"]
    req(bool(measured) == (rec["status"] == "MEASURED"), "record status MEASURED iff some item is MEASURED")
    if cls == "GENUINE_CALLEE":
        req(rec["status"] != "MEASURED", "genuine-callee measurement refused: slot $02 gate is BLOCKED")
    if cls in ("SYNTHETIC_REAL_CORE", "COMMERCIAL_CALLER_ENTRY"):
        req(rec["core_revision"] in (None, PINNED_CORE_REVISION) and
            (cls != "SYNTHETIC_REAL_CORE" or rec["core_revision"] == PINNED_CORE_REVISION),
            "core_revision must be the exact pinned revision (required for synthetic real-core)")
    else:
        req(rec["core_revision"] is None, "core_revision only for real-core classes")
    return rec


def new_record(evidence_class, status, reason, items=None, tool="", core_revision=None):
    rec = {"schema": SCHEMA, "evidence_class": evidence_class, "slot": SLOT, "tool": tool,
           "core_revision": core_revision, "jsr_count": 0, "status": status, "reason": reason,
           "items": items or {}}
    return validate_record(rec)


def blocked_genuine_record(reason):
    """The only genuine-callee record that can exist today: every REAL_JSR item BLOCKED."""
    items = {k: {"status": "BLOCKED", "value": None} for k, v in OBSERVABLES.items() if v["tier"] == "REAL_JSR"}
    return new_record("GENUINE_CALLEE", "BLOCKED", reason, items, tool="none")


class _MediaDisc:
    def __init__(self, index00, index01, length=400):
        self.index00, self.index01, self._len = index00, index01, length

    def read(self, lba):
        return lba if 0 <= lba < self._len else None


def media_structure(index00, index01, data_tracks_after_first=None):
    """Booleans derived from stat-only CUE parsing; the numeric values are not returned."""
    if data_tracks_after_first is None:
        data_tracks_after_first = index00 != 0
    return {"index01_nonzero": index01 != 0, "index00_differs_from_zero": index00 != 0,
            "data_tracks_after_first": bool(data_tracks_after_first)}


def candidate_partition(index00, index01):
    """Equivalence-class partition of the five registry candidates on a disc with these
    structural constants (names only; no values). Host arithmetic, not a callee fact."""
    disc = _MediaDisc(index00, index01, length=max(index00, index01, 150) + 400)
    probes = [(b, base) for b in (5, 150, 155, 300, 305) for base in (0, 150, 300)]
    sep = C.separation(dict(C.MODELS), [("media", disc)], probes)
    parent = {n: n for n in C.CANDIDATE_NAMES}

    def find(x):
        while parent[x] != x:
            x = parent[x]
        return x

    for a, b in sep["inseparable"]:
        parent[find(a)] = find(b)
    groups = {}
    for n in C.CANDIDATE_NAMES:
        groups.setdefault(find(n), []).append(n)
    return sorted(sorted(g) for g in groups.values())


def main():
    print(json.dumps({"slot": SLOT, "table_entry": TABLE_ENTRY, "observables": OBSERVABLES,
                      "genuine_callee": blocked_genuine_record("GATE_REFUSES_SLOT_02")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
