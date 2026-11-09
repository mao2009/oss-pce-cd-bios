#!/usr/bin/env python3
"""DEV CD_BASE boundary (Issue #735, host-only).

Fail-closed boundary between host arithmetic and any callee claim.
Solid: slot hypothesis noted, LBA arithmetic checked via cd_base_model,
judge never emits VERIFIED, unknown callee facts stay BLOCKED/UNKNOWN.
No BIOS wiring, no SCSI/MPR/RAM side-effect invention.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cd_base_model as M  # noqa: E402

SLOT = "$02"
TABLE_ENTRY = "$E006"
CANDIDATE_MODES = ("ABS_LBA", "INDEX01_REL", "BASE_PLUS_BLOCK", "MSF_FRAMES")
RELEASE_ELIGIBLE = False


def validate_block(value, name="block"):
    return M.check_u24(value, name=name)


def plan_fixture(index01_lba):
    if isinstance(index01_lba, bool) or not isinstance(index01_lba, int):
        raise ValueError("index01_lba must be int")
    if not 0 <= index01_lba <= M.MAX_U24:
        raise ValueError("index01_lba out of range")
    return {
        "index01_lba": index01_lba,
        "index01_zero_collapses": index01_lba == 0,
        "fixture": "INDEX01=150" if index01_lba == 150 else "control",
        "release_eligible": False,
    }


def judge(request):
    """Return fail-closed verdict; never success on unknown."""
    if not isinstance(request, dict):
        return {"verdict": "BLOCKED", "reason": "BAD_REQUEST"}
    mode = request.get("mode")
    if mode not in CANDIDATE_MODES:
        return {"verdict": "BLOCKED", "reason": "UNKNOWN_MODE"}
    try:
        validate_block(request.get("block"), "block")
    except ValueError as e:
        return {"verdict": "BLOCKED", "reason": "INVALID_BLOCK", "detail": str(e)}
    callee = request.get("callee_observed", False)
    if callee is not True:
        return {"verdict": "BLOCKED", "reason": "NO_CALLEE", "mode": mode}
    return {"verdict": "OBSERVED", "mode": mode}


def side_effect_matrix(callee_observed=False):
    fields = ("A", "X", "Y", "P", "SP", "MPR0-7", "RAM_$20F8-$20FF", "RAM_$2000-$3FFF")
    status = "OBSERVED" if callee_observed is True else "UNKNOWN"
    return {f: status for f in fields}
