"""Blast-radius model for the SYNTHETIC FORMAT image (Issue #303).

The BIOS is not executed. bm_format_model.format_image() is applied to a copy of the
in-memory synthetic image, and only the changed bytes of that copy are reported.
Output is a model property only: it is NOT a BIOS write set, NOT a BIOS return value,
and NOT a safety approval. The module performs no file, device, or BIOS I/O.

scope.modeled_paths lists the paths this model covers (path A). scope.not_modeled lists
path B, which is NOT part of this model (docs/contracts/bm-format.md section 2.1).
Because path B is not modeled, user_data_verdict is always "UNKNOWN". The path A
boolean is reported separately as path_a_user_data_at_risk.
bios_write_set is always "UNKNOWN" and release_eligible is always False.
"""
import bm_format_model as bm

USER_DATA_VERDICT = "UNKNOWN"

SCOPE = {
    "modeled_paths": ["path_A"],
    "not_modeled": [
        "path_B: $8000-$9FFF, $A000 page (HYPOTHESIS), zero page $20F8-$20FF",
    ],
}


def _ranges(indices):
    """Group sorted indices into half-open (start, end) runs."""
    runs = []
    for i in indices:
        if runs and runs[-1][1] == i:
            runs[-1][1] = i + 1
        else:
            runs.append([i, i + 1])
    return [tuple(r) for r in runs]


def blast_radius(image, *, allow_destructive=False):
    """Describe what applying the synthetic format would change, without applying it."""
    if not isinstance(image, (bytes, bytearray)):
        raise TypeError("synthetic image must be bytes or bytearray")
    before = bytes(image)
    status, after = bm.format_image(before, allow_destructive=allow_destructive)
    changed = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
    # A changed byte that held a non-zero value is data that would be lost (fail-closed flag).
    destroyed = [i for i in changed if before[i] != 0]
    return {
        "verdict": USER_DATA_VERDICT,
        "bios_executed": False,
        "scope": {"modeled_paths": list(SCOPE["modeled_paths"]), "not_modeled": list(SCOPE["not_modeled"])},
        "model_status": status,
        "changed_ranges": _ranges(changed),
        "changed_count": len(changed),
        "nonzero_destroyed_count": len(destroyed),
        "path_a_user_data_at_risk": bool(destroyed),
        "bios_write_set": "UNKNOWN",
        "release_eligible": False,
    }
