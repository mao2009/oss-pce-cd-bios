#!/usr/bin/env python3
"""Pre-block audit model for HuC6280 block transfers TII/TIN/TDD/TIA/TAI (Issue #367, child of #173).

Host-only, synthetic inputs. It reads no emulator source, no BIOS and no ROM byte.

Each block transfer performs `length` byte stores inside ONE instruction. This module answers a
static question: for a given (op, src, dst, length) and a logical write policy, can any store of the
instruction reach a forbidden address? It over-approximates the destination set on purpose, so a
refusal may be conservative but an allow is never optimistic:
- TII / TDD: destination walks up / down by one per byte (16-bit wrap). Exact set.
- TIN: destination fixed. Exact set.
- TIA: destination alternates between a pair. The module covers dst..dst+length (one extra byte), a
  superset that does not assert the pair order or base advance (both UNCONFIRMED, see docs/367).
  TAI: only the source alternates, so the destination set is the same as TII.
- length 0 is treated as the worst case 0x10000 bytes (the exact length rule is UNCONFIRMED).

Verdict is never PASS. `gate` is always BLOCKED because a static range check does not show that the
core cannot be interrupted mid-instruction (unmeasured, see docs/367-block-transfer-audit.md) and
because the operands are read at runtime, so a pre-execution decode is not yet wired.
"""

BLOCKED = "BLOCKED"
OPS = ("TII", "TIN", "TDD", "TIA", "TAI")
HARDWARE_BANK = 0xFF  # MPR value that maps the I/O (hardware) page; never writable by a block transfer
MAX_BYTES = 0x10000


def destination_set(op, dst, length):
    """Return the set of logical addresses an instruction may store to (over-approximation)."""
    if op not in OPS:
        raise ValueError(f"unknown block transfer op: {op}")
    count = length or MAX_BYTES
    if op == "TII":
        return {(dst + i) & 0xFFFF for i in range(count)}
    if op == "TDD":
        return {(dst - i) & 0xFFFF for i in range(count)}
    if op == "TIN":
        return {dst & 0xFFFF}
    # TIA: destination alternates (pair order and base advance UNCONFIRMED), so cover dst..dst+count
    # to be safe; TAI: destination increments, only the source alternates.
    extra = 1 if op == "TIA" else 0
    return {(dst + i) & 0xFFFF for i in range(count + extra)}


def policy_from_mpr(mpr, allowed_banks, window):
    """Build an allowed(addr) predicate from the eight MPR values and an explicit logical window.

    An address is allowed only if it lies in `window` (lo, hi) with hi exclusive, and its MPR bank
    is in `allowed_banks` and is not the hardware page.
    """
    lo, hi = window

    def allowed(addr):
        if not (lo <= addr < hi):
            return False
        bank = mpr[(addr >> 13) & 7]
        return bank in allowed_banks and bank != HARDWARE_BANK

    return allowed


def audit(op, src, dst, length, allowed):
    """Static pre-block audit of one block transfer instruction. Returns a dict, never PASS."""
    writes = destination_set(op, dst, length)
    refused = sorted(a for a in writes if not allowed(a))
    return {
        "op": op,
        "stores_in_one_instruction": length or MAX_BYTES,
        "distinct_destinations": len(writes),
        "refused_addresses": len(refused),
        "first_refused": refused[0] if refused else None,
        "verdict": "REFUSED_STATIC" if refused else "PERMITTED_STATIC_ONLY",
        "mid_instruction_stop": "UNMEASURED",
        "gate": BLOCKED,
    }


if __name__ == "__main__":
    mpr = [0xF8, 0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0xFF]
    print(audit("TII", 0x2000, 0x2100, 16, policy_from_mpr(mpr, {0x00, 0x01}, (0x2000, 0x4000))))
