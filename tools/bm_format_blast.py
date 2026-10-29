"""Non-executing blast-radius model for the SYNTHETIC FORMAT image (Issue #303).

Reports which bytes of an in-memory synthetic image would change if the model's
format_image() were applied. Output is a model property only: it is NOT a BIOS write set,
NOT a BIOS return value, and NOT a safety approval. It performs no file, device, or BIOS I/O.

Contract paths (docs/contracts/bm-format.md section 6, P-1..P-7) stay open: path A (BIOS
$E048) and path B (HuC direct write) have UNKNOWN write sets, so bios_write_set is always
"UNKNOWN" and verdict is always NOT_EXECUTED. release_eligible is always False.
"""
import bm_format_model as bm

NOT_EXECUTED = "NOT_EXECUTED"


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
        "verdict": NOT_EXECUTED,
        "model_status": status,
        "changed_ranges": _ranges(changed),
        "changed_count": len(changed),
        "nonzero_destroyed_count": len(destroyed),
        "user_data_at_risk": bool(destroyed),
        "bios_write_set": "UNKNOWN",
        "release_eligible": False,
    }
