#!/usr/bin/env python3
"""Fail-closed pre-execution safety preflight for the CD_RESET reference JSR (Issue #178, Safety-01).

Slot ``$01`` / candidate ``$E003`` stays **BLOCKED**. This module only decides, *before any
guest instruction executes*, whether the pinned #170 in-core guard plus a reviewed ABI would
let the reference JSR run. It never launches an emulator, never reads a BIOS and never runs a
reference API JSR. The common runtime gate is #173; the merged slot-``$1E`` guard is #170.
This module re-implements neither: it records which hazards #170 already enforces before a
side effect and which it does not. Its hazard rows, the pinned Geargrafx ``b49ae82a`` core
facts they rely on (``Memory::Write`` hook order, the ungated ``bank == 0xFF`` hardware page,
``$1804`` -> ``CdRom::WriteRegister`` reg ``0x04`` with ``AssertIRQ2()``, and the ``ST0/ST1/ST2``
``WriteFromCPU`` bypass) and the per-row evidence category are in docs/cd-reset-contract.md.

The decision is falsifiable: a synthetic posture that covers every hazard together with a
reviewed ABI returns ``ALLOWED``; every missing, malformed, unsafe-I/O-allowing or UNKNOWN-ABI
posture fails closed to ``BLOCKED`` with per-condition reasons.
"""
import argparse
from typing import NamedTuple

EXIT_BLOCKED = 3  # repo BLOCKED convention (tools/build_rom.py, tools/cd_read_contract.py)
EXIT_ALLOWED = 0


class Hazard(NamedTuple):
    """One pre-execution hazard; ``posture_key`` must hold for it to be covered."""

    id: str
    name: str
    posture_key: str
    enforced_by: str  # what (if anything) #170/#173 already enforces before a side effect


#: Ordered hazard table. Order is the decision order and the doc order.
HAZARDS = (
    Hazard("H1", "physical_bank_alias", "physical_bank_alias_rejected", "#170 exit 8"),
    Hazard("H2", "dynamic_mpr_change", "mpr_change_detected", "#170 MPR snapshot"),
    Hazard("H3", "unmonitored_mmio_1804", "hardware_page_policy", "#170 forbid $FF only"),
    Hazard("H4", "vdc_st_bypass", "st_instruction_intercept", "NONE"),
    Hazard("H5", "irq_side_effect", "irq_register_intercept", "NONE"),
    Hazard("H6", "ram_write_outside_allowlist", "ram_write_allowlist_enforced", "#170 map_write/breakpoints"),
    Hazard("H7", "instruction_budget_timeout", "instruction_budget_bounded", "#170 stepped budget"),
)

POSTURE_FIELDS = (
    "physical_bank_alias_rejected",
    "mpr_change_detected",
    "hardware_page_policy",
    "st_instruction_intercept",
    "irq_register_intercept",
    "ram_write_allowlist_enforced",
    "instruction_budget_bounded",
    "entry_environment_reproduced",
)
BOOL_FIELDS = tuple(field for field in POSTURE_FIELDS if field != "hardware_page_policy")
HARDWARE_PAGE_POLICIES = ("forbid", "intercept", "allow")
ABI_STATUSES = ("UNKNOWN", "REVIEWED")
ENTRY_ENVIRONMENT_STATES = ("UNKNOWN", "KNOWN")

#: The merged #170 slot-$1E guard's actual posture for a CD_RESET-shaped call.
#: ``forbid`` makes $1804 unreachable by construction, but that same MPR pinning cannot
#: reproduce the HuCC entry environment (MPR0 = $FF, HYPOTHESIS), so the entry environment is
#: NOT reproduced and ST/IRQ remain uninterceptable.
CURRENT_POSTURE = {
    "physical_bank_alias_rejected": True,
    "mpr_change_detected": True,
    "hardware_page_policy": "forbid",
    "st_instruction_intercept": False,
    "irq_register_intercept": False,
    "ram_write_allowlist_enforced": True,
    "instruction_budget_bounded": True,
    "entry_environment_reproduced": False,
}

#: slot-$01 contract status: every field UNKNOWN, so the callee may do anything (docs/cd-reset-contract.md).
CURRENT_ABI = {"status": "UNKNOWN", "entry_environment": "UNKNOWN"}


def validate_posture(posture):
    """Return a list of structural errors; an empty list means the posture is well formed."""
    if not isinstance(posture, dict):
        return ["posture must be a mapping"]
    if set(posture) != set(POSTURE_FIELDS):
        return [f"posture fields must be exactly {sorted(POSTURE_FIELDS)}"]
    errors = [f"{field} must be a bool" for field in BOOL_FIELDS if type(posture[field]) is not bool]
    if posture["hardware_page_policy"] not in HARDWARE_PAGE_POLICIES:
        errors.append(f"hardware_page_policy must be one of {HARDWARE_PAGE_POLICIES}")
    return errors


def validate_abi(abi):
    """Return a list of structural errors; an empty list means the ABI status is well formed."""
    if not isinstance(abi, dict) or set(abi) != {"status", "entry_environment"}:
        return ["abi must have exactly {status, entry_environment}"]
    errors = []
    if abi["status"] not in ABI_STATUSES:
        errors.append(f"abi status must be one of {ABI_STATUSES}")
    if abi["entry_environment"] not in ENTRY_ENVIRONMENT_STATES:
        errors.append(f"entry_environment must be one of {ENTRY_ENVIRONMENT_STATES}")
    return errors


def hazard_covered(hazard, posture):
    """True only when the posture provably covers ``hazard`` before any side effect."""
    if hazard.posture_key == "hardware_page_policy":
        # $1804 is covered either by making the $FF page unreachable ("forbid") or by trapping
        # the store before m_cdrom->WriteRegister ("intercept"). "allow" covers nothing.
        return posture["hardware_page_policy"] in ("forbid", "intercept")
    return posture[hazard.posture_key]


def decide(posture, abi):
    """Return ``(verdict, reasons)``. Malformed or incomplete inputs fail closed to BLOCKED.

    An UNKNOWN ABI means the candidate callee may do anything, so it can never excuse a hazard;
    only a reviewed ABI plus an explicit, side-effect-before mechanism may cover one.
    """
    errors = validate_posture(posture) + validate_abi(abi)
    if errors:
        return "BLOCKED", [f"malformed_configuration: {error}" for error in errors]

    reasons = []
    if abi["status"] != "REVIEWED":
        reasons.append("abi_status_unknown: candidate_callee_contract_not_reviewed")
    if abi["entry_environment"] != "KNOWN":
        reasons.append("entry_environment_unknown: cannot reproduce callee entry state")
    for hazard in HAZARDS:
        if not hazard_covered(hazard, posture):
            reasons.append(f"{hazard.id}: {hazard.name} not covered before a side effect")
    if not posture["entry_environment_reproduced"]:
        reasons.append("entry_environment_not_reproduced")
    elif posture["hardware_page_policy"] == "forbid":
        # The guard's only way to cover $1804 cannot coexist with the required MPR0 = $FF entry.
        reasons.append("entry_environment_contradiction: forbid_$FF conflicts with MPR0=$FF hypothesis")
    return ("ALLOWED", []) if not reasons else ("BLOCKED", reasons)


def format_verdict(verdict, reasons):
    """Render the CLI line; BLOCKED carries the per-condition reasons."""
    if verdict == "ALLOWED":
        return "ALLOWED: every hazard covered and entry environment reproduced"
    return "BLOCKED: " + "; ".join(reasons)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="evaluate the pinned #170 posture and the UNKNOWN slot-$01 ABI")
    arguments = parser.parse_args(argv)
    if not arguments.check:
        parser.error("--check is required")
    verdict, reasons = decide(CURRENT_POSTURE, CURRENT_ABI)
    print(format_verdict(verdict, reasons))
    return EXIT_ALLOWED if verdict == "ALLOWED" else EXIT_BLOCKED


if __name__ == "__main__":
    raise SystemExit(main())
