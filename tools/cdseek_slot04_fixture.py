#!/usr/bin/env python3
"""Definition-only slot-$04 (CD_SEEK, candidate $E00C) fixtures + fail-closed preflight (Issue #216).

Four local cases (nominal candidate, no media, invalid position, boundary) define the single
slot $04 / $E00C API with every behavioural field UNKNOWN: the callee ABI stays unresolved even after the
Issue #214 ledger (PR #223), which keeps the callee items UNKNOWN / HYPOTHESIS / BLOCKED,
so no argument, register, stack or return value is invented. There is no execution path here: no
process launch, no reference API JSR, no ROM byte read (path/sha256 shape and comparison only).
Well-formed input is ``BLOCKED_ABI`` (exit 3), malformed input is ``BLOCKED`` (exit 3), and ROM
mismatch or unsafe hardware I/O is ``REJECTED``
(exit 3). Nothing here ever emits a permission, result or verification verdict.

Input identity: the file extension is NOT a content identity. ``--config`` accepts only a small
regular file whose name ends in ``.json`` as a convenience filter; its content is then parsed as
JSON and checked field by field against ``CONFIG_SCHEMA``. A ROM renamed to ``.json`` therefore
fails to parse (or fails the schema) and is ``BLOCKED`` without echoing any of its bytes. The
``rom_path`` string inside the config names a ROM that is never opened. ``rom_sha256`` is a
declared value: the ROM file itself is never hashed, so the hash check compares two declared
strings (declared vs approved), not the bytes on disk.
"""
import argparse
import json
from pathlib import Path

EXIT_FAIL_CLOSED = 3  # repo BLOCKED convention (tools/cdreset_safety_preflight.py)
CASE_SCHEMA = "oss-pce-cd-bios/cdseek-slot04-case/v1"
CONFIG_SCHEMA = "oss-pce-cd-bios/cdseek-slot04-run-config/v1"
SLOT, ENTRY_CANDIDATE, UNKNOWN = "$04", "$E00C", "UNKNOWN"
ABI_STATUS = "BLOCKED_ABI"  # slot $04 callee ABI unresolved (Issue #214 ledger, PR #223); not released by this module
CATEGORIES = ("candidate_nominal", "no_media", "invalid_position", "boundary")
CASE_FIELDS = frozenset({"schema", "case_id", "slot", "entry_candidate", "category", "media",
                         "position", "registers", "work_ram", "mpr", "stack", "irq", "disk_state",
                         "io_writes", "expected_result", "abi_status", "execution"})
CONFIG_FIELDS = frozenset({"schema", "rom_path", "rom_sha256", "approved_rom_sha256",
                           "unsafe_io_allow", "side_effects_allowed", "execution_allowed",
                           "safety_gate_approved"})
EVIDENCE_FIELDS = ("position", "work_ram", "mpr", "stack", "irq", "disk_state", "expected_result")
PERMISSION_FIELDS = ("unsafe_io_allow", "side_effects_allowed", "execution_allowed")
# PC Engine hardware page $0000-$1FFF: any write is a side effect. This is deliberately wider than the
# CD unit register block (commonly cited as $1800-$1BFF, not verified here) alone, so the refusal
# does not depend on a narrower guess.
HARDWARE_PAGE = range(0x0000, 0x2000)
MAX_CONFIG_BYTES = 65536  # a run config is small JSON; anything larger is refused unread
HEX = set("0123456789abcdef")


def _case(case_id, category, media):
    """One definition-only case: inputs fixed by category, every measured outcome UNKNOWN."""
    return {"schema": CASE_SCHEMA, "case_id": case_id, "slot": SLOT, "entry_candidate": ENTRY_CANDIDATE,
            "category": category, "media": media, "registers": {name: UNKNOWN for name in ("A", "X", "Y", "P")},
            **{field: UNKNOWN for field in EVIDENCE_FIELDS}, "io_writes": UNKNOWN,
            "abi_status": ABI_STATUS, "execution": "NOT_RUN"}


# The single source of the required case set: case_id -> (category, media). Each case_id must carry
# exactly this category and media. The set is CLOSED (reject, do not allow subsets or extras): a
# missing, duplicated or added case would silently change what slot $04 coverage means.
REQUIRED_CASES = {"slot04_nominal_media_present": ("candidate_nominal", "present"),
                  "slot04_no_media": ("no_media", "absent"),
                  "slot04_invalid_position": ("invalid_position", "present"),
                  "slot04_boundary": ("boundary", "present")}

#: The four local cases for the single slot-$04 API. Definition-only; never executed.
CASES = tuple(_case(case_id, category, media) for case_id, (category, media) in REQUIRED_CASES.items())


def validate_case(case):
    """Structural errors for one fixture case; [] means well formed. No field may hold a guess."""
    if not isinstance(case, dict) or set(case) != CASE_FIELDS:
        return ["invalid case fields"]
    errors = []
    if (case["schema"], case["slot"], case["entry_candidate"]) != (CASE_SCHEMA, SLOT, ENTRY_CANDIDATE):
        errors.append("unknown schema or not the single slot $04 / $E00C API")
    if case["category"] not in CATEGORIES:
        errors.append("unknown category")
    if case["media"] not in ("present", "absent"):
        errors.append("media must be present or absent")
    # Semantic check: case_id, category and media must be the defined combination. The pairs are
    # unique across REQUIRED_CASES, so this also catches a right category under the wrong case_id.
    if type(case["case_id"]) is not str or case["case_id"] not in REQUIRED_CASES:
        errors.append("unknown case_id")
    elif (case["category"], case["media"]) != REQUIRED_CASES[case["case_id"]]:
        errors.append("category/media do not match the defined case_id")
    registers = case["registers"]
    if (not isinstance(registers, dict) or set(registers) != {"A", "X", "Y", "P"}
            or any(type(v) is not str or v != UNKNOWN for v in registers.values())):
        errors.append("registers A/X/Y/P must each be UNKNOWN (no evidence)")
    errors += [f"{field} must stay UNKNOWN (no evidence)" for field in EVIDENCE_FIELDS
               if type(case[field]) is not str or case[field] != UNKNOWN]
    writes = case["io_writes"]
    if writes != UNKNOWN and (not isinstance(writes, list)
                              or any(type(address) is not int or not 0 <= address <= 0xFFFF
                                     for address in writes)):
        errors.append("io_writes must be UNKNOWN or a list of 16-bit addresses")
    if case["abi_status"] != ABI_STATUS:
        errors.append("abi_status must be BLOCKED_ABI (slot $04 ABI unreviewed)")
    if case["execution"] != "NOT_RUN":
        errors.append("execution must be NOT_RUN (this fixture is never run)")
    return errors


def validate_case_set(cases):
    """Set-level errors: exactly the REQUIRED_CASES ids, each once. Ids are printed only as our own
    fixed names; a caller-supplied id value is never echoed."""
    ids = [case["case_id"] for case in cases if isinstance(case, dict) and type(case.get("case_id")) is str]
    errors = []
    if len(cases) != len(REQUIRED_CASES):
        errors.append(f"exactly {len(REQUIRED_CASES)} cases required, got {len(cases)}")
    if len(set(ids)) != len(ids):
        errors.append("duplicate case_id")
    missing = sorted(set(REQUIRED_CASES) - set(ids))
    if missing:
        errors.append("missing required case_id: " + ", ".join(missing))
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
    if not _is_sha256(config["rom_sha256"]):
        errors.append("rom_sha256 missing or not lowercase sha256")
    approved = config["approved_rom_sha256"]
    if approved is not None and not _is_sha256(approved):
        errors.append("approved_rom_sha256 must be null or lowercase sha256")
    if type(config["safety_gate_approved"]) is not bool:
        errors.append("safety_gate_approved must be a bool")
    errors += [f"{field} must be a bool" for field in PERMISSION_FIELDS if type(config[field]) is not bool]
    return errors


def _is_sha256(value):
    return isinstance(value, str) and len(value) == 64 and all(c in HEX for c in value)


def _reject_reasons(cases, config):
    """Hard refusals: permissions granted, ROM hash differs from the approved one, or any case
    requests a write into the hardware I/O page. These never become execution permission."""
    reasons = [f"{field} must be false: device side effects and execution are never permitted"
               for field in PERMISSION_FIELDS if config[field] is not False]
    approved = config["approved_rom_sha256"]
    if approved is not None and config["rom_sha256"] != approved:
        # Compares declared strings only: the ROM file is never hashed (no ROM read in this module).
        reasons.append("rom_sha256_mismatch: declared rom_sha256 differs from approved_rom_sha256")
    for index, case in enumerate(cases):
        writes = case["io_writes"]
        if writes != UNKNOWN and any(address in HARDWARE_PAGE for address in writes):
            reasons.append(f"case[{index}]: hardware I/O page write requested")
    return reasons


def preflight(cases, config):
    """Return ``(status, reasons)``. Malformed input -> ``BLOCKED``; refusals -> ``REJECTED``;
    otherwise ``BLOCKED_ABI`` because the slot $04 ABI is unreviewed. Gate approval alone never
    unlocks execution: there is no execution path in this module."""
    if not isinstance(cases, (list, tuple)) or not cases:
        return "BLOCKED", ["malformed_configuration: at least one fixture case is required"]
    errors = [f"case[{index}]: {error}" for index, case in enumerate(cases)
              for error in validate_case(case)]
    errors += validate_case_set(cases)
    errors += [f"config: {error}" for error in validate_config(config)]
    if errors:
        return "BLOCKED", [f"malformed_configuration: {error}" for error in errors]
    rejects = _reject_reasons(cases, config)
    if rejects:
        return "REJECTED", rejects
    reasons = ["abi_unreviewed: slot $04 / $E00C callee ABI unresolved (Issue #214 ledger: callee items UNKNOWN/HYPOTHESIS/BLOCKED); BLOCKED_ABI"]
    if not config["safety_gate_approved"]:
        reasons.append("safety_gate_not_approved: slot $04 case execution BLOCKED")
    if config["approved_rom_sha256"] is None:
        reasons.append("rom_not_approved: no approved ROM hash supplied")
    reasons.append("execution BLOCKED: definition-only fixtures; this module implements no execution path")
    return ABI_STATUS, reasons


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="validate the built-in slot $04 cases")
    parser.add_argument("--config", type=Path,
                        help="local run-config JSON (.json filter only; content must be a run config, "
                             "and the rom_path it names is never opened)")
    arguments = parser.parse_args(argv)
    if not arguments.check:
        parser.error("--check is required")
    config = None
    if arguments.config is not None:
        try:
            # The .json suffix is a convenience filter, not identity: a renamed ROM is refused by the
            # JSON parse and CONFIG_SCHEMA check below. ponytail: no magic-byte sniffing (ROMs have no
            # reliable header); the strict schema is the gate.
            path = arguments.config
            if path.suffix != ".json" or not path.is_file() or path.stat().st_size > MAX_CONFIG_BYTES:
                raise ValueError("config path refused")
            with path.open("rb") as handle:  # bounded read: the size stat alone is racy
                data = handle.read(MAX_CONFIG_BYTES + 1)
            if len(data) > MAX_CONFIG_BYTES:
                raise ValueError("config path refused")
            config = json.loads(data.decode("utf-8"))
        except (OSError, ValueError, RecursionError):
            # Parse failure (incl. a ROM renamed to .json) and refusal both yield None: BLOCKED.
            # Only fixed text is printed; the file's bytes or parse error text are never echoed.
            config = None  # unreadable config is indistinguishable from an unspecified one: BLOCKED
    status, reasons = preflight(CASES, config)
    print(f"{status}: " + "; ".join(reasons))
    return EXIT_FAIL_CLOSED


if __name__ == "__main__":
    raise SystemExit(main())
