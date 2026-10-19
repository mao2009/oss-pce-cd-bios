#!/usr/bin/env python3
"""Fail-closed pre-execution safety gate for the CD_SEEK reference JSR (Issue #217).

Slot ``$04`` / candidate ``$E00C``. The gate decides, before any guest instruction runs,
whether the genuine callee may be JSR'd. It always returns BLOCKED: the callee bytes and ABI
are UNKNOWN (#13), and the pinned Geargrafx ``b49ae82a`` core has no general pre-side-effect
interception point for the hazards below (#173, #194, #200, #202, #203). The gate has no
unlock input. ``run_gate`` never calls the runner it is given.

Evidence per hazard is recorded in docs/217-cdseek-safety-audit.md. This module never reads a
BIOS, never launches an emulator and never runs a reference JSR.
"""
import argparse
import json
import sys

EXIT_BLOCKED = 3  # repo BLOCKED convention (tools/cdreset_safety_preflight.py)

NONE = "NONE"
DETECT_ONLY = "DETECT_ONLY"  # observed after the write completed (#173)
KNOWN_PC_ONLY = "KNOWN_PC_ONLY"  # pre-block only for statically known guest PC (#200/#203)
POST_HOC = "POST_HOC"  # snapshot/budget after execution, does not stop the side effect

#: (id, hazard, why it matters for CD_SEEK, strongest pre-side-effect control on pinned core)
HAZARDS = (
    ("H1", "vdc_st_bypass", "ST0/ST1/ST2 WriteFromCPU lands VDC writes without Memory::Write hooks (#202)", NONE),
    ("H2", "unmonitored_mmio_write", "$1802/$1402/$1403 writes complete before stop; $1804 SCSI RST lands (#173/#194)",
     KNOWN_PC_ONLY),
    ("H3", "cd_device_side_effect", "seek command and SCSI/CD register writes change the device; not reversible",
     NONE),
    ("H4", "irq_side_effect", "IRQ mask/ack writes complete before stop; IRQ arrival not guaranteed (#201)", DETECT_ONLY),
    ("H5", "mpr_dynamic_change", "TAM/TMA remaps memory mid-callee; only the post-run MPR snapshot sees it (#170)",
     POST_HOC),
    ("H6", "ram_write_outside_allowlist", "RAM writes outside the allowlist; CPU-path write hook exists, ST path not",
     DETECT_ONLY),
    ("H7", "unknown_callee_abi", "callee bytes, return/flag/error/completion contract UNKNOWN (#13/#178)", NONE),
    ("H8", "budget_timeout", "instruction budget and timeout bound execution, they do not prevent side effects",
     POST_HOC),
)


def decide():
    """Return the gate record. Always BLOCKED with zero JSR executions; no input unlocks it."""
    return {
        "slot": "$04",
        "candidate": "$E00C",
        "verdict": "BLOCKED",
        "jsr_count": 0,
        "reasons": [f"{hid} {name}: pre-side-effect control = {control}" for hid, name, _, control in HAZARDS],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="exit with BLOCKED status (3)")
    args = parser.parse_args(argv)
    print(json.dumps(decide(), ensure_ascii=False, indent=2))
    return EXIT_BLOCKED if args.check else 0


if __name__ == "__main__":
    sys.exit(main())
