#!/usr/bin/env python3
"""Fail-closed pre-execution safety gate for the CD_SEEK reference JSR (Issue #217).

Slot ``$04`` / candidate ``$E00C``. The gate decides, before any guest instruction runs,
whether the genuine callee may be JSR'd. It always returns BLOCKED: the callee bytes and ABI
are UNKNOWN (#13), and no general pre-side-effect interception point has been established on the
pinned Geargrafx ``b49ae82a`` core for the hazards below (#173, #194, #200, #202). The gate has no
unlock input and never invokes any runner.

Evidence per hazard is recorded in docs/217-cdseek-safety-audit.md. This module never reads a
BIOS, never launches an emulator and never runs a reference JSR.
"""
import argparse
import json
import sys

EXIT_BLOCKED = 3  # repo BLOCKED convention (tools/cdreset_safety_preflight.py)

NONE = "NONE"
DETECT_ONLY = "DETECT_ONLY"  # observed after the write completed (#173)
PREVENT_CPU_ONLY = "PREVENT_CPU_ONLY"  # CPU Memory::Write path denied; ST path bypasses (#170/#202)
KNOWN_PC_ONLY = "KNOWN_PC_ONLY"  # pre-block only for statically known guest PC (#200)
POST_HOC = "POST_HOC"  # snapshot/budget after execution, does not stop the side effect
TAM_BOUNDARY_SYNTHETIC = "TAM_BOUNDARY_SYNTHETIC"  # MPR change stops at the next instruction boundary, measured
# only for synthetic LDA+TAM+STA (geargrafx_reference_guard.py tam); not a general control for callees

#: (id, hazard, why it matters for CD_SEEK, strongest pre-side-effect control on pinned core)
HAZARDS = (
    ("H1", "vdc_st_bypass", "ST0/ST1/ST2 WriteFromCPU lands VDC writes without Memory::Write hooks (#202)", NONE),
    ("H2", "unmonitored_mmio_write", "$1804 SCSI RST lands before stop; known guest PC only (#194/#200)",
     KNOWN_PC_ONLY),
    ("H3", "cd_device_side_effect", "seek command and SCSI/CD register writes change the device; no host rollback established",
     NONE),
    ("H4", "irq_side_effect", "$1802/$1402/$1403 writes complete before stop; IRQ2 arrival not guaranteed (#173/#201)",
     DETECT_ONLY),
    ("H5", "mpr_dynamic_change", "TAM/TMA remaps memory mid-callee; core stops after the TAM only in the synthetic "
     "LDA+TAM+STA case (#170); other remap-then-side-effect sequences unestablished", TAM_BOUNDARY_SYNTHETIC),
    ("H6", "ram_write_outside_allowlist", "CPU-path writes denied by map_write; ST path bypasses the hook (#170/#202)",
     PREVENT_CPU_ONLY),
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
        "reasons": [f"{hid} {name}: {why}; pre-side-effect control = {control}" for hid, name, why, control in HAZARDS],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    print(json.dumps(decide(), ensure_ascii=False, indent=2))
    return EXIT_BLOCKED  # BLOCKED is the only verdict; argument errors exit 2 inside parse_args


if __name__ == "__main__":
    sys.exit(main())
