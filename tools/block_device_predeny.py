#!/usr/bin/env python3
"""E4 block/device pre-deny, deny-by-default (Issue #737, child of #173/#367).

Synthetic only, pinned Geargrafx b49ae82a012eade566d72e9d61c9d95a5caa4da3.
Reads no emulator source, BIOS or ROM byte; installs no genuine callee; makes no
hardware claim. Production allowlist is UNDECIDED, so even a fully in-window
transfer is PERMITTED_STATIC_ONLY with gate BLOCKED, mid-instruction halt is
BLOCKED_NOT_SAFE_TO_RESUME (per-byte refusal is not a safe stop, docs/367). Unknown
op, malformed MPR snapshot, MPR0-6 $FF, empty allowlist, GG_TESTING/libretro
build, same-value write, temp-write-restore, source-read side-effect, UNKNOWN
device verdict or device/unknown store to WRAM all refuse before any store. Never
PASS, never ALLOWED; callers must require gate==BLOCKED and execution==BLOCKED.
"""
BLOCKED = "BLOCKED"
PINNED_REVISION = "b49ae82a012eade566d72e9d61c9d95a5caa4da3"
OPS = ("TII", "TIN", "TDD", "TIA", "TAI")
HARDWARE_BANK = 0xFF
MAX_BYTES = 0x10000
DEVICE_READ_SIDE_EFFECT_ADDRS = frozenset({0x2104, 0x4104})
DEVICE_WRITER_ROLES = frozenset({"device_store", "unknown_store"})


def _count(length):
    return MAX_BYTES if length == 0 else length


def destination_set(op, dst, length):
    if op not in OPS:
        raise ValueError(f"unknown block transfer op: {op}")
    count = _count(length)
    if op == "TII" or op == "TAI":
        return {(dst + i) & 0xFFFF for i in range(count)}
    if op == "TDD":
        return {(dst - i) & 0xFFFF for i in range(count)}
    if op == "TIN":
        return {dst & 0xFFFF}
    return {(dst + i) & 0xFFFF for i in range(count + 1)}


def source_set(op, src, length):
    if op not in OPS:
        raise ValueError(f"unknown block transfer op: {op}")
    count = _count(length)
    if op == "TDD":
        return {(src - i) & 0xFFFF for i in range(count)}
    if op == "TAI":
        return {(src + (i & 1)) & 0xFFFF for i in range(count)}
    return {(src + i) & 0xFFFF for i in range(count)}


def _refused(op, reason, first=None):
    return {"op": op, "verdict": "REFUSED_PREEXEC", "gate": BLOCKED, "execution": BLOCKED,
            "jsr_count": 0, "reason": reason, "first_refused": first,
            "mid_instruction_stop": "BLOCKED_NOT_SAFE_TO_RESUME"}


def preflight(op, src, dst, length, mpr, window, allowed_banks, build=None, same_value=False, temp_restore=False):
    build = build or {}
    if op not in OPS:
        return _refused(op, "unknown_op")
    if build.get("gg_testing") or build.get("GG_TESTING") or build.get("libretro") or build.get("LIBRETRO"):
        return _refused(op, "testing_build")
    if not isinstance(mpr, (list, tuple)) or len(mpr) != 8:
        return _refused(op, "malformed_mpr")
    if any(not isinstance(b, int) or b < 0 or b > 0xFF for b in mpr):
        return _refused(op, "malformed_mpr")
    if any(b == HARDWARE_BANK for b in mpr[:7]):
        return _refused(op, "ff_mpr")
    if not isinstance(window, (list, tuple)) or len(window) != 2:
        return _refused(op, "bad_window_allowlist")
    if not all(isinstance(x, int) for x in window) or not (0 <= window[0] < window[1] <= 0x10000):
        return _refused(op, "bad_window_allowlist")
    if not allowed_banks:
        return _refused(op, "bad_window_allowlist")
    if same_value:
        return _refused(op, "same_value_write")
    if temp_restore:
        return _refused(op, "temp_write_restore")
    if not isinstance(length, int) or length < 0 or length > MAX_BYTES:
        return _refused(op, "bad_length")
    dset = destination_set(op, dst, length)
    sset = source_set(op, src, length)
    side = sorted(sset & DEVICE_READ_SIDE_EFFECT_ADDRS)
    if side:
        return _refused(op, "src_read_side_effect", side[0])
    lo, hi = window
    outside = sorted(a for a in dset if not lo <= a < hi)
    if outside:
        return _refused(op, "dst_outside_window", outside[0])
    bad = sorted(a for a in dset if mpr[(a >> 13) & 7] not in allowed_banks or mpr[(a >> 13) & 7] == HARDWARE_BANK)
    if bad:
        return _refused(op, "dst_bank_not_allowed", bad[0])
    return {"op": op, "verdict": "PERMITTED_STATIC_ONLY", "gate": BLOCKED, "execution": BLOCKED,
            "jsr_count": 0, "reason": "in_window_static_only", "first_refused": None,
            "mid_instruction_stop": "BLOCKED_NOT_SAFE_TO_RESUME",
            "distinct_destinations": len(dset), "stores_in_one_instruction": _count(length)}


def device_audit(rows):
    bad = []
    for r in rows:
        if r.get("verdict") == "UNKNOWN":
            bad.append(r.get("id"))
        if r.get("target") == "WRAM" and r.get("role") in DEVICE_WRITER_ROLES:
            if r.get("id") not in bad:
                bad.append(r.get("id"))
    if bad:
        return {"verdict": "BLOCKED_AUDIT", "gate": BLOCKED, "execution": BLOCKED, "jsr_count": 0, "bad_ids": bad}
    return {"verdict": "DRAINED_NO_UNKNOWN", "gate": BLOCKED, "execution": BLOCKED, "jsr_count": 0, "bad_ids": []}


if __name__ == "__main__":
    print(preflight("TII", 0x2000, 0x2100, 16, [0xF8, 0, 1, 2, 3, 4, 5, 6], (0x2000, 0x4000), {0, 1}))
