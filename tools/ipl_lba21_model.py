"""Issue #744 (child of #670, Owner decision B): 21-bit LBA reference model.

Pure arithmetic mirror of the DEV/PUBLIC IPL loader's LBA path
(tests/integration/ipl_loader_rom.s, post-#744): 24-bit IPLBLK field,
24-bit body-base calculation, 21-bit READ(6) range gate, READ(6) CDB encoding.

Nothing here runs an emulator or touches media. The real-core suite
(tests/integration/geargrafx_ipl_loader.py) verifies the guest against the
same boundary table; tests/host/test_744_ipl_21bit_lba.py checks this model
and audits the assembled ROM for the matching decision structure.
"""

MAX_READ6_LBA = 0x1FFFFF  # SCSI READ(6) LBA field is 21 bits
END_EXCLUSIVE_LIMIT = 0x200000  # body + nsec must stay <= this (last LBA <= MAX_READ6_LBA)
MAX_IPLBLK = 0xFFFFFF  # on-disc field width: 24-bit big-endian

CODE_IPLBLK_RANGE = 21  # IPLBLK < 2
CODE_COUNT_RANGE = 22  # nsec == 0 or above the format maximum
CODE_LBA_RANGE = 27  # body or tail outside the 21-bit READ(6) range


def _check_int(value, label, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{label} must be an integer in {low:#x}..{high:#x}, got {value!r}")
    return value


def cdb_for(lba):
    """Exact 6-byte READ(6) CDB for one sector (transfer length 1)."""
    _check_int(lba, "lba", 0, MAX_READ6_LBA)
    return [0x08, (lba >> 16) & 0x1F, (lba >> 8) & 0xFF, lba & 0xFF, 0x01, 0x00]


def info_lba(sel):
    """Information-sector LBA for a 16-bit INDEX 01 LBA: sel + 1, full width.

    The guest adds at runtime with the 16-bit carry preserved into the high
    byte (ipl_loader_rom.s reset path), so sel=0xFFFF yields 0x010000, never
    a 16-bit-truncated 0x000000. The result always fits 24 bits.
    """
    _check_int(sel, "sel", 0, 0xFFFF)
    return sel + 1


def decide(sel, b0, b1, b2, nsec, dev=True):
    """Mirror the loader's validate_info LBA decision.

    sel: 16-bit INDEX 01 LBA. b0..b2: IPLBLK field bytes (24-bit big-endian).
    nsec: body sector count byte. dev: True for DEV (1..10), False for PUBLIC (1..31).

    Returns {"ok": True, "iplblk", "body", "last", "probe_cdb", "sectors"}
    or {"ok": False, "code"}. A refusal precedes any load-area write by
    construction: no sector LBAs are emitted on the "ok": False path.
    """
    _check_int(sel, "sel", 0, 0xFFFF)
    for label, byte in (("b0", b0), ("b1", b1), ("b2", b2)):
        _check_int(byte, label, 0, 0xFF)
    _check_int(nsec, "nsec", 0, 0xFF)
    iplblk = (b0 << 16) | (b1 << 8) | b2
    if iplblk < 2:
        return {"ok": False, "code": CODE_IPLBLK_RANGE}
    body = sel + iplblk
    if body > MAX_IPLBLK:
        # 24-bit wraparound: the base does not fit the computed field width.
        # Checked before the sector count, mirroring the guest's base-add
        # carry-out refusal (code 27 precedes code 22 on double faults).
        return {"ok": False, "code": CODE_LBA_RANGE}
    limit = 10 if dev else 31
    if nsec == 0 or nsec > limit:
        return {"ok": False, "code": CODE_COUNT_RANGE}
    end = body + nsec  # exclusive end; last body LBA is end - 1
    if end > END_EXCLUSIVE_LIMIT:
        return {"ok": False, "code": CODE_LBA_RANGE}
    last = end - 1
    return {"ok": True, "iplblk": iplblk, "body": body, "last": last,
            "probe_cdb": cdb_for(last), "sectors": [cdb_for(body + i) for i in range(nsec)]}
