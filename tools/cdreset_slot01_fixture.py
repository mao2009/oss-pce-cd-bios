#!/usr/bin/env python3
"""Definition-only slot-$01 (CD_RESET, candidate $E003) fixtures + fail-closed preflight (Issue #180).

Two cases (media present/absent) define the single slot $01 / $E003 API with every behavioural
field UNKNOWN (no measured callee run): no instruction sequence or return value is invented.
There is no execution path here — no process launch, no reference API JSR, no ROM byte read
(path/sha256 shape only) — so slot-$01 execution stays BLOCKED (safety gate #179 pending; repo
exit-3 convention). MODEL_ELIGIBLE (#191/#193) is synthetic-model eligibility, never execution
permission, and is never emitted here.
"""
import argparse
import json
from pathlib import Path

EXIT_BLOCKED = 3  # repo BLOCKED convention (tools/cdreset_safety_preflight.py)
CASE_SCHEMA = "oss-pce-cd-bios/cdreset-slot01-case/v1"
CONFIG_SCHEMA = "oss-pce-cd-bios/cdreset-slot01-run-config/v1"
SLOT, ENTRY_CANDIDATE, UNKNOWN = "$01", "$E003", "UNKNOWN"
CASE_FIELDS = frozenset({"schema", "case_id", "slot", "entry_candidate", "media", "registers",
                         "work_ram", "mpr", "stack", "irq", "disk_state", "expected_result",
                         "execution"})
CONFIG_FIELDS = frozenset({"schema", "rom_path", "rom_sha256", "unsafe_io_allow",
                           "side_effects_allowed", "execution_allowed", "safety_gate_approved"})
EVIDENCE_FIELDS = ("work_ram", "mpr", "stack", "irq", "disk_state", "expected_result")
PERMISSION_FIELDS = ("unsafe_io_allow", "side_effects_allowed", "execution_allowed")
HEX = set("0123456789abcdef")


def _case(case_id, media):
    """One definition-only case: inputs fixed, every measured outcome UNKNOWN."""
    return {"schema": CASE_SCHEMA, "case_id": case_id, "slot": SLOT, "entry_candidate": ENTRY_CANDIDATE,
            "media": media, "registers": {name: UNKNOWN for name in ("A", "X", "Y", "P")},
            **{field: UNKNOWN for field in EVIDENCE_FIELDS}, "execution": "NOT_RUN"}


#: The two local cases for the single slot-$01 API. Definition-only; never executed.
CASES = (_case("slot01_media_present", "present"), _case("slot01_media_absent", "absent"))


def validate_case(case):
    """Structural errors for one fixture case; [] means well formed. No field may hold a guess."""
    if not isinstance(case, dict) or set(case) != CASE_FIELDS:
        return ["invalid case fields"]
    errors = []
    if (case["schema"], case["slot"], case["entry_candidate"]) != (CASE_SCHEMA, SLOT, ENTRY_CANDIDATE):
        errors.append("unknown schema or not the single slot $01 / $E003 API")
    if case["media"] not in ("present", "absent"):
        errors.append("media must be present or absent")
    registers = case["registers"]
    if (not isinstance(registers, dict) or set(registers) != {"A", "X", "Y", "P"}
            or any(type(v) is not str or v != UNKNOWN for v in registers.values())):
        errors.append("registers A/X/Y/P must each be UNKNOWN (no evidence)")
    errors += [f"{field} must stay UNKNOWN (no evidence)" for field in EVIDENCE_FIELDS
               if type(case[field]) is not str or case[field] != UNKNOWN]
    if case["execution"] != "NOT_RUN":
        errors.append("execution must be NOT_RUN (this fixture is never run)")
    return errors


def validate_config(config):
    """Fail-closed run-configuration shape. ROM bytes are never read: path/hash presence only."""
    if config is None:
        return ["run config missing: ROM path/sha256 unspecified"]
    if not isinstance(config, dict) or set(config) != CONFIG_FIELDS:
        return ["invalid run config fields"]
    errors = []
    if config["schema"] != CONFIG_SCHEMA:
        errors.append("unknown schema")
    if not isinstance(config["rom_path"], str) or not config["rom_path"].strip():
        errors.append("rom_path missing")
    digest = config["rom_sha256"]
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in HEX for c in digest):
        errors.append("rom_sha256 missing or not lowercase sha256")
    errors += [f"{field} must be false: device side effects and execution are never permitted"
               for field in PERMISSION_FIELDS if config[field] is not False]
    if type(config["safety_gate_approved"]) is not bool:
        errors.append("safety_gate_approved must be a bool")
    return errors


def preflight(cases, config):
    """Return ``(status, reasons)``: malformed input fails closed to ``BLOCKED``; a well-formed
    fixture returns ``NOT_RUN`` (definitions this module never executes) with the reasons
    execution stays ``BLOCKED``. Gate approval alone never unlocks execution here."""
    if not isinstance(cases, (list, tuple)) or not cases:
        return "BLOCKED", ["malformed_configuration: at least one fixture case is required"]
    errors = [f"case[{index}]: {error}" for index, case in enumerate(cases)
              for error in validate_case(case)]
    errors += [f"config: {error}" for error in validate_config(config)]
    if errors:
        return "BLOCKED", [f"malformed_configuration: {error}" for error in errors]
    reasons = []
    if not config["safety_gate_approved"]:
        reasons.append("safety_gate_not_approved: slot $01 case execution BLOCKED (#179 review pending)")
    reasons.append("execution BLOCKED: definition-only fixtures; this module implements no execution path")
    return "NOT_RUN", reasons


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="validate the built-in slot $01 cases")
    parser.add_argument("--config", type=Path, help="local run-config JSON (never a ROM)")
    arguments = parser.parse_args(argv)
    if not arguments.check:
        parser.error("--check is required")
    config = None
    if arguments.config is not None:
        try:
            config = json.loads(arguments.config.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            config = None  # unreadable config is indistinguishable from an unspecified one: BLOCKED
    status, reasons = preflight(CASES, config)
    if status == "NOT_RUN":
        print(f"NOT_RUN: {len(CASES)} slot $01 fixture cases structurally valid; definition-only, never executed")
    print("BLOCKED: " + "; ".join(reasons))
    return EXIT_BLOCKED


if __name__ == "__main__":
    raise SystemExit(main())
