"""Classify how a probe run ended when the DEV unimplemented-API stub may have been reached (Issue #692).

Pure logic, no emulator. Inputs are one RUN record of the pinned probe plus the label address of the stub.
The result is a STATE, never a PASS: a PASS exists only when a harness expectation matches a state.

States
- API_UNIMPLEMENTED_CONFIRMED : the stub label was reached and held (halt_confirmed) at the stub address,
                                 and X equals the expected slot number. Not a success.
- API_SLOT_MISMATCH           : the stub was reached, but X does not equal the expected slot (fail-closed).
- API_PC_MISMATCH             : the stop label is not at the stub address (fail-closed).
- API_NOT_CONFIRMED           : the stub label is the reason but halt_confirmed is not true (fail-closed).
- BUDGET                      : the step budget ended with no halt label (reason is undetermined).
- CD_HALT_FAIL / CD_HALT_OK / OTHER : other halt labels, reported as they are.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OVERRIDES = ROOT / "src" / "overrides"

API_UNIMPLEMENTED_CONFIRMED = "API_UNIMPLEMENTED_CONFIRMED"
API_SLOT_MISMATCH = "API_SLOT_MISMATCH"
API_PC_MISMATCH = "API_PC_MISMATCH"
API_NOT_CONFIRMED = "API_NOT_CONFIRMED"
BUDGET = "BUDGET"
CD_HALT_FAIL = "CD_HALT_FAIL"
CD_HALT_OK = "CD_HALT_OK"
OTHER = "OTHER"

STUB_LABEL = "api_unimplemented"


def implemented_slots(overrides: Path = OVERRIDES) -> set[int]:
    """Slots that have an override body in src/overrides (the DEV implementation ledger, by file)."""
    found = set()
    for path in overrides.glob("slot_*.s"):
        match = re.fullmatch(r"slot_([0-9A-Fa-f]{2})\.s", path.name)
        if match:
            found.add(int(match.group(1), 16))
    return found


def unimplemented_slots(overrides: Path = OVERRIDES) -> set[int]:
    return set(range(81)) - implemented_slots(overrides)


def classify(run: dict, *, stub_address: int, expected_slot: int | None = None) -> dict:
    """Classify one RUN record. `run` uses the probe's keys: reason, halt_confirmed, pc, x."""
    reason = run.get("reason")
    confirmed = run.get("halt_confirmed") is True
    if reason == "budget":
        if confirmed:
            return {"state": OTHER, "detail": "budget with halt_confirmed is inconsistent"}
        return {"state": BUDGET, "detail": "step budget ended; no halt label confirmed"}
    if reason == "cd_halt_fail":
        return {"state": CD_HALT_FAIL, "detail": "cd_halt_fail label"}
    if reason == "cd_halt_ok":
        return {"state": CD_HALT_OK, "detail": "cd_halt_ok label"}
    if reason != STUB_LABEL:
        return {"state": OTHER, "detail": f"reason {reason!r}"}
    if not confirmed:
        return {"state": API_NOT_CONFIRMED, "detail": "stub label reached but not confirmed"}
    if run.get("pc") != stub_address:
        return {"state": API_PC_MISMATCH, "detail": f"pc {run.get('pc')} != stub {stub_address}"}
    x = run.get("x")
    if expected_slot is not None and x != expected_slot:
        return {"state": API_SLOT_MISMATCH, "detail": f"x {x} != expected slot {expected_slot}"}
    return {"state": API_UNIMPLEMENTED_CONFIRMED, "detail": f"stub held; x={x}", "slot": x}
