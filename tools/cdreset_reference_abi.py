"""Minimal reference ABI record and fail-closed judge for the slot-$01 CD_RESET (Issue #10, preparation).

Defines what one reference observation of $E003 must contain (caller inputs, returned state, MPR/RAM/IRQ
before and after, media present/absent, bounded poll budget) and what verdict it may earn. Nothing here
runs a reference callee. The JSR gate is read via tools/reference_jsr_gate.require_allowed(); while it is
closed every real-source record is NOT_RUN + BLOCKED. Verdicts are OBSERVED / FAIL / SKIP / BLOCKED, never
PASS and never VERIFIED.
"""
import re

if __package__:
    from .environment import DEPENDENCIES
    from .reference_jsr_gate import GateRefused, require_allowed
else:
    from environment import DEPENDENCIES
    from reference_jsr_gate import GateRefused, require_allowed

SLOT = "$01"
GATE_ID = "slot-01"
CANDIDATE_ENTRY = 0xE003
REAL_SOURCE = "geargrafx-real"
MEDIA = ("present", "absent")
# Every item is UNKNOWN until a reviewed reference run; HYPOTHESIS = caller-side claim only (cd-reset-contract.md).
CONTRACT = {"inputs": "HYPOTHESIS", "returned_registers": "UNKNOWN", "return_pc_sp": "UNKNOWN",
            "mpr": "UNKNOWN", "ram": "UNKNOWN", "irq": "UNKNOWN", "media_absent": "UNKNOWN",
            "timeout": "UNKNOWN"}
REQUIRED = ("source", "core_revision", "rom_sha256", "slot", "entry_pc", "media", "caller", "before",
            "after", "returned", "polls", "poll_budget")
SHA256_HEX = re.compile(r"[0-9a-f]{64}")
# Expected key set and value type per snapshot field. Types: "byte", "pc", "bool", "mpr", "ram".
FIELD_TYPES = {"A": "byte", "X": "byte", "Y": "byte", "P": "byte", "SP": "byte", "MPR": "mpr",
               "irq_disable": "bool", "irq_mask": "byte", "ram": "ram", "return_pc": "pc"}
SNAPSHOT_KEYS = {"caller": {"A", "X", "Y", "P", "MPR"},
                 "before": {"SP", "MPR", "irq_disable", "irq_mask", "ram"},
                 "after": {"A", "X", "Y", "P", "SP", "MPR", "irq_disable", "irq_mask", "ram", "return_pc"}}


def _is_int(value):
    return type(value) is int  # bool is an int subclass; reject it explicitly


def _check_value(name, kind, value):
    if kind == "bool":
        ok = type(value) is bool
    elif kind == "byte":
        ok = _is_int(value) and 0 <= value <= 0xFF
    elif kind == "pc":
        ok = _is_int(value) and 0 <= value <= 0xFFFF
    elif kind == "mpr":
        ok = isinstance(value, list) and len(value) == 8 and all(_is_int(b) and 0 <= b <= 0xFF for b in value)
    else:  # ram: address -> byte, addresses 16-bit
        ok = isinstance(value, dict) and all(_is_int(a) and 0 <= a <= 0xFFFF and _is_int(v) and 0 <= v <= 0xFF
                                             for a, v in value.items())
    if not ok:
        raise ValueError(f"{name} has an invalid {kind} value: {value!r}")


def _check_snapshot(name, snap):
    if not isinstance(snap, dict) or set(snap) != SNAPSHOT_KEYS[name]:
        raise ValueError(f"{name} must have exactly the keys {sorted(SNAPSHOT_KEYS[name])}")
    for key, value in snap.items():
        _check_value(f"{name}.{key}", FIELD_TYPES[key], value)


def validate(record: dict) -> None:
    """Raise ValueError unless the record is a well-formed slot-$01 reference observation."""
    missing = [key for key in REQUIRED if key not in record]
    if missing:
        raise ValueError(f"reference record missing {missing}")
    unknown = sorted(set(record) - set(REQUIRED))
    if unknown:
        raise ValueError(f"reference record has unknown keys {unknown}")
    for key in ("source", "core_revision", "slot", "media"):
        if not isinstance(record[key], str):
            raise ValueError(f"{key} must be a string")
    if record["slot"] != SLOT:
        raise ValueError("only slot $01 is covered by this record")
    if record["media"] not in MEDIA:
        raise ValueError(f"media must be one of {MEDIA}")
    rom = record["rom_sha256"]
    if not isinstance(rom, str) or (rom and not SHA256_HEX.fullmatch(rom)):
        raise ValueError("rom_sha256 must be empty (unidentified) or 64 lowercase hex characters")
    if not _is_int(record["entry_pc"]) or not 0 <= record["entry_pc"] <= 0xFFFF:
        raise ValueError("entry_pc must be a 16-bit int")
    if type(record["returned"]) is not bool:
        raise ValueError("returned must be a bool")
    if not _is_int(record["polls"]) or record["polls"] < 0:
        raise ValueError("polls must be a non-negative int")
    if not _is_int(record["poll_budget"]) or record["poll_budget"] < 1:
        raise ValueError("poll_budget must be an int >= 1")
    for name in ("caller", "before", "after"):
        _check_snapshot(name, record[name])
    if set(record["before"]["ram"]) != set(record["after"]["ram"]):
        raise ValueError("RAM observation region must be identical before and after")


def state_changes(record: dict) -> dict:
    """Observed MPR / IRQ / RAM differences between before and after (observation only, no verdict)."""
    validate(record)
    before, after = record["before"], record["after"]
    changed_ram = sorted(addr for addr, value in before["ram"].items() if after["ram"][addr] != value)
    return {"mpr": before["MPR"] != after["MPR"],
            "irq": (before["irq_disable"], before["irq_mask"]) != (after["irq_disable"], after["irq_mask"]),
            "ram": changed_ram}


def _classify(record: dict) -> tuple[str, str]:
    """Return (verdict, reason) for a validated record. Private: callers must go through judge(), which checks the gate.

    Verdicts: OBSERVED (outcome recorded, no pass defined), FAIL (structural), SKIP, BLOCKED.
    """
    validate(record)
    if record["source"] != REAL_SOURCE:
        return "SKIP", "non_real_source"
    if record["core_revision"] != DEPENDENCIES["geargrafx"]["revision"]:
        return "BLOCKED", "core_unpinned"
    if not record["rom_sha256"]:
        return "BLOCKED", "rom_unidentified"
    if record["entry_pc"] != CANDIDATE_ENTRY:
        return "FAIL", "wrong_entry"
    if record["polls"] > record["poll_budget"]:
        return "FAIL", "budget_overrun"  # the bound is hard, returned or not
    if not record["returned"]:
        return "OBSERVED", "timeout" if record["polls"] == record["poll_budget"] else "not_returned"
    return "OBSERVED", "returned"


def gate_closed_reason():
    """None when the JSR gate permits slot $01; otherwise the gate's refusal text."""
    try:
        require_allowed(GATE_ID)
    except GateRefused as refusal:
        return str(refusal)
    return None


def judge(record: dict) -> tuple[str, str, str]:
    """Return (execution, verdict, reason). Raises ValueError on malformed input before any verdict."""
    validate(record)
    if record["source"] != REAL_SOURCE:
        return "NOT_RUN", "SKIP", "non_real_source"
    if gate_closed_reason() is not None:
        return "NOT_RUN", "BLOCKED", "gate_closed"
    # "EVALUATED" = classification only; it does not mean the callee ran (execution is NOT_RUN while the gate is closed).
    verdict, reason = _classify(record)
    return "EVALUATED", verdict, reason
