#!/usr/bin/env python3
"""E4 block/device pre-deny, deny-by-default (Issue #737, child of #173/#367).

Synthetic only, pinned Geargrafx b49ae82a012eade566d72e9d61c9d95a5caa4da3.
Reads no emulator source, BIOS or ROM byte; installs no genuine callee; makes no
hardware claim. Production allowlist is UNDECIDED, so even a fully in-window
transfer is PERMITTED_STATIC_ONLY with gate BLOCKED, mid-instruction halt is
BLOCKED_NOT_SAFE_TO_RESUME (per-byte refusal is not a safe stop, docs/367). Unknown
op, malformed MPR snapshot, MPR0-6 $FF, empty allowlist, GG_TESTING/libretro
build, same-value write, temp-write-restore, source-read side-effect, FORBIDDEN_MMIO
intersection (the same set enforced by tools/h45_preblock_gate.py), UNKNOWN
device verdict or device/unknown store to WRAM all refuse before any store. Never
PASS, never ALLOWED; callers must require gate==BLOCKED and execution==BLOCKED.
REFUSED_PREEXEC is a pre-execution refusal only: it does not claim that a
post-detection observation would have been a pre-block, and no verdict here is a
detection-after-side-effect result.
"""
BLOCKED = "BLOCKED"
PINNED_REVISION = "b49ae82a012eade566d72e9d61c9d95a5caa4da3"
OPS = ("TII", "TIN", "TDD", "TIA", "TAI")
HARDWARE_BANK = 0xFF
MAX_BYTES = 0x10000
FORBIDDEN_MMIO = frozenset({0x1802, 0x1804, 0x1402, 0x1403})
DEVICE_READ_SIDE_EFFECT_ADDRS = frozenset({0x2104, 0x4104})
DEVICE_WRITER_ROLES = frozenset({"device_store", "unknown_store"})
ALLOWED_DEVICE_PAIRS = frozenset({
    ("device_store", "NOT_MAIN_RAM"),
    ("cpu_store", "CPU_ORIGINATED"),
    ("reset_store", "RESET_ONLY"),
    ("host_store", "HOST_ONLY"),
    ("cpu_block_store", "PER_BYTE_STEP"),
})
TESTING_BUILD_KEYS = frozenset({"gg_testing", "libretro"})


def _is_int(value):
    return type(value) is int


def _is_byte(value):
    return _is_int(value) and 0 <= value <= 0xFF


def _is_addr(value):
    return _is_int(value) and 0 <= value <= 0xFFFF


def _count(length):
    return MAX_BYTES if length == 0 else length


def _check_transfer(op, src, dst, length):
    if op not in OPS:
        raise ValueError(f"unknown block transfer op: {op}")
    if not _is_addr(src) or not _is_addr(dst):
        raise ValueError(f"src/dst must be 16-bit addresses: {src!r}/{dst!r}")
    if not _is_int(length) or length < 0 or length > MAX_BYTES:
        raise ValueError(f"length must be 0..{MAX_BYTES}: {length!r}")


def destination_set(op, dst, length):
    if op not in OPS:
        raise ValueError(f"unknown block transfer op: {op}")
    if not _is_addr(dst):
        raise ValueError(f"dst must be a 16-bit address: {dst!r}")
    if not _is_int(length) or length < 0 or length > MAX_BYTES:
        raise ValueError(f"length must be 0..{MAX_BYTES}: {length!r}")
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
    if not _is_addr(src):
        raise ValueError(f"src must be a 16-bit address: {src!r}")
    if not _is_int(length) or length < 0 or length > MAX_BYTES:
        raise ValueError(f"length must be 0..{MAX_BYTES}: {length!r}")
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
    build = {} if build is None else build
    if op not in OPS:
        return _refused(op, "unknown_op")
    if not isinstance(build, dict):
        return _refused(op, "malformed_build")
    if any(str(k).lower() in TESTING_BUILD_KEYS and v for k, v in build.items()):
        return _refused(op, "testing_build")
    if not isinstance(mpr, (list, tuple)) or len(mpr) != 8:
        return _refused(op, "malformed_mpr")
    if any(not _is_byte(b) for b in mpr):
        return _refused(op, "malformed_mpr")
    if any(b == HARDWARE_BANK for b in mpr[:7]):
        return _refused(op, "ff_mpr")
    if not isinstance(window, (list, tuple)) or len(window) != 2:
        return _refused(op, "bad_window_allowlist")
    if not all(_is_int(x) for x in window) or not (0 <= window[0] < window[1] <= 0x10000):
        return _refused(op, "bad_window_allowlist")
    if not isinstance(allowed_banks, (set, frozenset, list, tuple)) or not allowed_banks:
        return _refused(op, "bad_window_allowlist")
    if any(not _is_byte(b) for b in allowed_banks):
        return _refused(op, "bad_window_allowlist")
    allowed_banks = set(allowed_banks)
    if same_value:
        return _refused(op, "same_value_write")
    if temp_restore:
        return _refused(op, "temp_write_restore")
    try:
        _check_transfer(op, src, dst, length)
    except ValueError:
        return _refused(op, "malformed_addr" if not (_is_addr(src) and _is_addr(dst)) else "bad_length")
    dset = destination_set(op, dst, length)
    sset = source_set(op, src, length)
    hit = sorted(dset & FORBIDDEN_MMIO)
    if hit:
        return _refused(op, "dst_forbidden_mmio", hit[0])
    hit_src = sorted(sset & FORBIDDEN_MMIO)
    if hit_src:
        return _refused(op, "src_forbidden_mmio", hit_src[0])
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
    stores = 1 if op == "TIN" else _count(length)
    return {"op": op, "verdict": "PERMITTED_STATIC_ONLY", "gate": BLOCKED, "execution": BLOCKED,
            "jsr_count": 0, "reason": "in_window_static_only", "first_refused": None,
            "mid_instruction_stop": "BLOCKED_NOT_SAFE_TO_RESUME",
            "distinct_destinations": len(dset), "stores_in_one_instruction": stores}


def device_audit(rows):
    block_audit = {"verdict": "BLOCKED_AUDIT", "gate": BLOCKED, "execution": BLOCKED, "jsr_count": 0}
    if not isinstance(rows, (list, tuple)):
        block_audit["bad_ids"] = ["<malformed rows>"]
        return block_audit
    bad = []
    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            bad.append(f"<row {i}>")
            continue
        ident = r.get("id")
        role = str(r.get("role", "")).strip().lower()
        target = str(r.get("target", "")).strip().upper()
        verdict = str(r.get("verdict", "")).strip().upper()
        if (verdict == "UNKNOWN" or (role, verdict) not in ALLOWED_DEVICE_PAIRS
                or (role in DEVICE_WRITER_ROLES and target == "WRAM")):
            if ident not in bad:
                bad.append(ident)
    if bad:
        block_audit["bad_ids"] = bad
        return block_audit
    return {"verdict": "DRAINED_NO_UNKNOWN", "gate": BLOCKED, "execution": BLOCKED, "jsr_count": 0, "bad_ids": []}


if __name__ == "__main__":
    print(preflight("TII", 0x2000, 0x2100, 16, [0xF8, 0, 1, 2, 3, 4, 5, 6], (0x2000, 0x4000), {0, 1}))
