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
STATUSES = ("NOT_RUN", "SKIP", "BLOCKED", "UNKNOWN", "MEASURED")
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

# Keys that would leak private/proprietary material; rejected anywhere in a record.
FORBIDDEN_KEY_TOKENS = frozenset({"path", "hash", "sha", "md5", "crc", "bytes", "hex", "title", "name",
                                  "sector", "payload", "dump", "audio", "save", "cue", "bin", "rom",
                                  "firmware", "pc", "lba", "address", "file"})
FORBIDDEN_VALUE_WORDS = ("VERIFIED", "DEV_CALLABLE", "COMPATIBLE")
TOP_KEYS = frozenset({"schema", "evidence_class", "slot", "tool", "core_revision", "jsr_count",
                      "status", "reason", "items"})
ITEM_KEYS = frozenset({"status", "value"})
CALLER_METHODS = ("JSR", "JMP", "DIRECT", "NONE")
CALLER_VERDICTS = ("API_ENTRY_CONFIRMED", "PRE_API_BLOCKER_OBSERVED", "UNKNOWN", "INCOMPLETE",
                   "BUDGET", "NOT_RUN", "BLOCKED", "FAIL")
PINNED_CORE_REVISION_PREFIX = "b49ae82a"


def _walk(obj, path="record"):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield path, k, v
            yield from _walk(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk(v, f"{path}[{i}]")


def validate_record(rec):
    """Raise ValueError on a malformed, leaking or over-claiming record; return it."""
    def req(ok, msg):
        if not ok:
            raise ValueError(msg)

    req(isinstance(rec, dict) and set(rec) == TOP_KEYS, "record keys")
    req(rec["schema"] == SCHEMA, "schema")
    req(rec["slot"] == SLOT, "slot must be $02")
    cls = rec["evidence_class"]
    req(cls in EVIDENCE_CLASSES, "evidence_class")
    req(rec["status"] in STATUSES, "status")
    req(isinstance(rec["reason"], str) and len(rec["reason"]) <= 200, "reason")
    req(type(rec["jsr_count"]) is int and rec["jsr_count"] >= 0, "jsr_count")
    items = rec["items"]
    req(isinstance(items, dict), "items")

    for where, key, value in _walk(rec):
        if key not in OBSERVABLES:
            req(not (set(key.lower().split("_")) & FORBIDDEN_KEY_TOKENS), f"forbidden key {key!r}")
        if isinstance(value, str):
            req(not any(w in value.upper().replace(" ", "_") for w in FORBIDDEN_VALUE_WORDS),
                "forbidden status word in value")
            req(len(value) <= 200, "string too long")
    req(not any(w in rec["reason"].upper() for w in FORBIDDEN_VALUE_WORDS), "forbidden word in reason")

    for name, item in items.items():
        req(name in OBSERVABLES, f"unknown observable {name!r}")
        req(isinstance(item, dict) and set(item) == ITEM_KEYS, "item keys")
        req(item["status"] in STATUSES, "item status")
        tier = OBSERVABLES[name]["tier"]
        req(tier in CLASS_TIERS[cls], f"{name} (tier {tier}) cannot be carried by evidence class {cls}")
        if item["status"] == "MEASURED":
            req(item["value"] is not None, "MEASURED item needs a value")
        else:
            req(item["value"] is None, "non-MEASURED item must have a null value")
        if name == "O5_caller_first_api" and item["status"] == "MEASURED":
            v = item["value"]
            req(isinstance(v, dict) and set(v) == {"verdict", "slot", "method", "in_table", "return_matches_caller"},
                "O5 value keys")
            req(v["verdict"] in CALLER_VERDICTS and v["method"] in CALLER_METHODS, "O5 verdict/method")
            req(v["slot"] is None or (type(v["slot"]) is int and 0 <= v["slot"] <= 80), "O5 slot")
            req(type(v["in_table"]) is bool and (v["return_matches_caller"] is None
                                                or type(v["return_matches_caller"]) is bool), "O5 flags")
        if name == "O6_caller_sets_regs" and item["status"] == "MEASURED":
            req(type(item["value"]) is bool, "O6 public value is a boolean only")
        if name == "O2_media_structure" and item["status"] == "MEASURED":
            v = item["value"]
            req(isinstance(v, dict) and set(v) == {"index01_nonzero", "index00_differs_from_zero",
                                                   "data_tracks_after_first"}, "O2 value keys")
            req(all(type(x) is bool for x in v.values()), "O2 booleans only")

    if cls == "GENUINE_CALLEE":
        # Fail-closed: the gate refuses slot $02, so no genuine-callee run can be recorded.
        req(rec["status"] != "MEASURED" and all(i["status"] != "MEASURED" for i in items.values()),
            "genuine-callee measurement refused: slot $02 gate is BLOCKED")
        req(rec["jsr_count"] == 0, "genuine-callee jsr_count must be 0 while the gate refuses slot $02")
    else:
        req(rec["jsr_count"] == 0, "non-genuine classes cannot contain a genuine JSR")
    if cls == "SYNTHETIC_REAL_CORE":
        req(isinstance(rec["core_revision"], str) and rec["core_revision"].startswith(PINNED_CORE_REVISION_PREFIX),
            "real-core record must carry the pinned core revision")
    else:
        req(rec["core_revision"] is None or cls == "COMMERCIAL_CALLER_ENTRY", "core_revision only for real-core classes")
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


def main(argv=None):
    print(json.dumps({"slot": SLOT, "table_entry": TABLE_ENTRY, "observables": OBSERVABLES,
                      "genuine_callee": blocked_genuine_record("gate refuses slot $02 (#173/#228)")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
