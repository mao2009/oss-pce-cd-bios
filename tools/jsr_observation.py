#!/usr/bin/env python3
"""Machine-readable observation record for a guarded reference-JSR run (Issue #173).

One schema for A/X/Y/P/SP/PC, MPR[0..7], RAM differences, IRQ state and the return PC. The
provenance field separates a REAL_CORE_EXECUTION (pinned core, synthetic BIOS) from a HOST_FIXTURE
(hand-made input). A fixture can never claim real-core evidence. IRQ state is UNKNOWN until a probe
actually observes it, so the schema never implies that an interrupt was checked when it was not.

Synthetic inputs only. This module reads no BIOS, no ROM and no emulator source. It does not grant
execution: a valid observation is still not an approval, and it never changes gate_status.
"""
import json

SCHEMA = "oss-pce-cd-bios/jsr-observation/v1"
PINNED_CORE_REVISION = "b49ae82a012eade566d72e9d61c9d95a5caa4da3"
PROVENANCE_KINDS = ("REAL_CORE_EXECUTION", "HOST_FIXTURE")
OUTCOME_STATUSES = ("ok", "rejected")
TOP_KEYS = frozenset({"schema", "provenance", "slot", "outcome", "registers", "mpr", "ram_diff", "irq", "guard"})


def _byte(value, name):
    if type(value) is not int or not 0 <= value <= 0xFF:
        raise ValueError(f"{name} must be a byte: {value!r}")


def _addr(value, name):
    if type(value) is not int or not 0 <= value <= 0xFFFF:
        raise ValueError(f"{name} must be a 16-bit address: {value!r}")


def validate_observation(obs):
    """Raise ValueError on a malformed or over-claiming observation. Return the observation."""
    def require(ok, message):
        if not ok:
            raise ValueError(message)

    require(isinstance(obs, dict) and set(obs) == TOP_KEYS, "observation keys")
    require(obs["schema"] == SCHEMA, "schema mismatch")
    prov = obs["provenance"]
    require(isinstance(prov, dict) and prov.get("kind") in PROVENANCE_KINDS, "provenance kind")
    if prov["kind"] == "REAL_CORE_EXECUTION":
        require(prov.get("core_revision") == PINNED_CORE_REVISION, "real-core revision must be the pinned revision")
        require(prov.get("bios") == "SYNTHETIC", "real-core observation must use a synthetic BIOS")
        require(prov.get("real_core_claim") is True, "real_core_claim must be true for REAL_CORE_EXECUTION")
    else:
        require(prov.get("real_core_claim") is False, "HOST_FIXTURE must not claim real-core evidence")
        require(prov.get("core_revision") is None, "HOST_FIXTURE must not carry a core revision")
    require(obs["slot"] == "$1E", "slot must be $1E (only slot under observation)")

    outcome = obs["outcome"]
    require(isinstance(outcome, dict) and outcome.get("status") in OUTCOME_STATUSES, "outcome.status")
    require(isinstance(outcome.get("reason"), str), "outcome.reason must be a string")
    if outcome["status"] == "rejected":
        require(outcome["reason"], "rejected outcome needs a reason")
    _addr(outcome.get("stop_pc"), "outcome.stop_pc")
    executed, budget = outcome.get("instructions_executed"), outcome.get("budget")
    require(type(executed) is int and executed >= 0, "instructions_executed")
    require(type(budget) is int and 4 <= budget <= 1000000, "budget")
    require(executed <= budget, "instructions_executed exceeds budget")
    returned = outcome.get("returned")
    require(type(returned) is bool, "returned must be bool")
    if returned:
        _addr(outcome.get("return_pc"), "return_pc")
    else:
        require(outcome.get("return_pc") is None, "return_pc must be null when not returned")

    regs = obs["registers"]
    require(isinstance(regs, dict) and set(regs) == {"a", "x", "y", "p", "sp", "pc"}, "register keys")
    for name in ("a", "x", "y", "p", "sp"):
        _byte(regs[name], name)
    _addr(regs["pc"], "pc")

    mpr = obs["mpr"]
    require(isinstance(mpr, list) and len(mpr) == 8, "mpr must have 8 entries")
    for index, value in enumerate(mpr):
        _byte(value, f"mpr[{index}]")

    ram = obs["ram_diff"]
    require(isinstance(ram, list), "ram_diff must be a list")
    for entry in ram:
        require(isinstance(entry, dict) and set(entry) == {"address", "before", "after"}, "ram_diff entry keys")
        _addr(entry["address"], "ram_diff.address")
        _byte(entry["before"], "ram_diff.before")
        _byte(entry["after"], "ram_diff.after")

    irq = obs["irq"]
    require(isinstance(irq, dict) and irq.get("status") in ("UNKNOWN", "OBSERVED"), "irq.status")
    if irq["status"] == "OBSERVED":
        _byte(irq.get("mask"), "irq.mask")
        _byte(irq.get("pending"), "irq.pending")
    else:
        require(set(irq) == {"status"}, "UNKNOWN irq must not carry values")

    guard = obs["guard"]
    require(isinstance(guard, dict) and set(guard) == {"detected", "detected_pc", "mpr_changed"}, "guard keys")
    require(type(guard["detected"]) is bool and type(guard["mpr_changed"]) is bool, "guard flags")
    if guard["detected_pc"] is not None:
        _addr(guard["detected_pc"], "guard.detected_pc")
    return obs


def from_probe_row(row, provenance):
    """Map one line of the guarded probe's stderr JSON to the schema. IRQ is recorded as UNKNOWN."""
    def state(part):
        return {"a": part["a"], "x": part["x"], "y": part["y"], "p": part["p"],
                "sp": part["sp"], "pc": part["pc"]}

    after = row["after"]
    observation = {
        "schema": SCHEMA,
        "provenance": provenance,
        "slot": "$1E",
        "outcome": {"status": row["status"], "reason": row.get("reason", ""),
                    "stop_pc": row["stop_pc"], "instructions_executed": row["instructions_executed"],
                    "budget": row["budget"], "returned": row["returned"],
                    "return_pc": row["stop_pc"] if row["returned"] else None},
        "registers": state(after),
        "mpr": list(after["mpr"]),
        "ram_diff": [dict(entry) for entry in row.get("ram_diff", [])],
        "irq": {"status": "UNKNOWN"},
        "guard": {"detected": row["detected"], "detected_pc": row.get("detected_pc") if row["detected"] else None,
                  "mpr_changed": row["mpr_changed"]},
    }
    return validate_observation(observation)


def _self_check():
    """Synthetic smoke check: a fixture row maps to a valid HOST_FIXTURE observation."""
    row = {"status": "ok", "reason": "", "before": {"a": 0, "x": 0, "y": 0, "p": 4, "sp": 255, "pc": 0x3E00,
                                                    "mpr": [0, 0xF8, 0, 0, 0, 0, 0, 0]},
           "after": {"a": 0xAA, "x": 0x55, "y": 0xCC, "p": 4, "sp": 0xFD, "pc": 0x3E05,
                     "mpr": [0, 0xF8, 0, 0, 0, 0, 0, 0]},
           "stop_pc": 0x3E05, "instructions_executed": 3, "budget": 100, "returned": True,
           "detected": False, "mpr_changed": False, "ram_diff": []}
    observation = from_probe_row(row, {"kind": "HOST_FIXTURE", "core_revision": None, "bios": "SYNTHETIC",
                                       "real_core_claim": False})
    assert observation["outcome"]["return_pc"] == 0x3E05
    return json.dumps(observation, sort_keys=True)


if __name__ == "__main__":
    _self_check()
    print("PASS: jsr observation schema self-check (synthetic fixture only)")
