"""EXPERIMENTAL CD_READ commercial first-mode host mirror (Agent-B G5 P1).

NOT a compatible System Card BIOS. NOT the genuine CD_READ ABI. NOT compatibility
evidence. Pure-Python mirror of src/cd_read_commercial.s (CD_READ_COMMERCIAL_V1)
for host targeted tests only. Genuine CD_BASE/CD_READ ABI stays UNKNOWN/BLOCKED per
docs/cd-base-abi-ledger.md and docs/cd-read-contract.md. Reference JSR 0/81,
ABI VERIFIED 0/81, DEV_CALLABLE 0, release_eligible=False.
"""
MAX_U24 = 0xFFFFFF
MAX_READ6 = 0x1FFFFF

CRES_ADDR = 0x2330
STATE_ADDR = 0x2400
BUF_ADDR = 0x2800
SECTOR_BYTES = 2048
FIXED_DEST = 0x2800
FIXED_SIZE = 2048

ERR_INVALID_STATE = 0x60
ERR_INVALID_WINDOW = 0x61
ERR_OVER_CAP = 0x62
ERR_BEFORE = 0x63
ERR_AFTER = 0x64  # reserved: the guest never returns this (no synthetic window end)
ERR_BAD_MODE = 0x65
ERR_BAD_SIZE = 0x66
ERR_BAD_DEST = 0x67
ERR_READ_FAIL = 0x70
ERR_READ_TIMEOUT = 0x71


def _u8(v, name):
    if type(v) is not int or not 0 <= v <= 0xFF:
        raise ValueError(f"{name} must be 0..0xFF, got {v!r}")
    return v


def compute_lba(base_hi, base_mid, base_lo, cblk_hi, cblk_mid, cblk_lo):
    """24-bit recbase0 + commercial block (_cl:_ch:_dl HI/MID/LO).

    Returns (lba24, carry_out, hi, mid, lo). $20F2-$20F4 play no part.
    """
    for val, sub in ((base_hi, "base_hi"), (base_mid, "base_mid"),
                     (base_lo, "base_lo"), (cblk_hi, "cblk_hi"),
                     (cblk_mid, "cblk_mid"), (cblk_lo, "cblk_lo")):
        _u8(val, sub)
    base = (base_hi << 16) | (base_mid << 8) | base_lo
    blk = (cblk_hi << 16) | (cblk_mid << 8) | cblk_lo
    total = base + blk
    carry = 1 if total > MAX_U24 else 0
    lba = total & MAX_U24
    return lba, carry, (lba >> 16) & 0xFF, (lba >> 8) & 0xFF, lba & 0xFF


PH_STATUS = 0xD8
PH_COMMAND = 0xD0
PH_FREE = 0x00


def link(base_hi, base_mid, base_lo, gen, last,
         cblk_hi, cblk_mid, cblk_lo, win_lo, win_mid, win_hi,
         mode, size_lo, size_hi, dst_lo, dst_hi, blk_lo_ignored=0,
         scsi="ok", state=None, partial=0, scsi_status=2, msg=0,
         timeout_stage=5, timeout_bytes=0, timeout_last=PH_COMMAND):
    """Mirror of the guest commercial first-mode routine.

    base_hi/mid/lo: recbase0 $2400/$2401/$2402 (HI/MID/LO, preserved).
    gen/last: state $2403/$2404 (preserved; last must be 0/DATA else INVALID_STATE).
    cblk_hi/mid/lo: commercial block $20FC/$20FD/$20FE (HI/MID/LO, preserved).
    win_lo/mid/hi: INDEX 01 lower bound $20F5/$20F6/$20F7 (LO/MID/HI, preserved).
    mode/size/dst: _dh $20FF, _ax $20F8/$20F9, _bx $20FA/$20FB (preserved).
    blk_lo_ignored: accepted and IGNORED (proves $20F2 independence; any value,
      including out-of-range ints, never changes the result -- validated loosely).
    scsi: synthetic drive outcome for a valid request: "ok" (full 2048-byte
      DATA_IN, GOOD status/message -> $00 stage 8), "premature" (early STATUS
      in DATA_IN wait -> $70 stage 5 partial bytes), "tail_bad" (bad status
      after full transfer -> $70 stage 8 2048 bytes), "timeout" (wait expiry
      -> $71 with the timeout_* site; stages 2/3/4 0 bytes, stage 5 partial,
      stages 6/7/8 2048 bytes).
    Returns dict with A/C/Z and CRES fields; issued=True only when the guest
    would touch $1800 (valid request reaching the READ path).
    """
    for val, name in ((base_hi, "base_hi"), (base_mid, "base_mid"),
                      (base_lo, "base_lo"), (gen, "gen"), (last, "last"),
                      (cblk_hi, "cblk_hi"), (cblk_mid, "cblk_mid"),
                      (cblk_lo, "cblk_lo"), (win_lo, "win_lo"),
                      (win_mid, "win_mid"), (win_hi, "win_hi"),
                      (mode, "mode"), (size_lo, "size_lo"),
                      (size_hi, "size_hi"), (dst_lo, "dst_lo"),
                      (dst_hi, "dst_hi")):
        _u8(val, name)
    if scsi == "fail":
        raise ValueError("scsi='fail' is ambiguous; use 'premature' or 'tail_bad'")
    if scsi not in ("ok", "premature", "tail_bad", "timeout"):
        raise ValueError(f"scsi must be ok/premature/tail_bad/timeout, got {scsi!r}")
    for val, name in ((scsi_status, "scsi_status"),
                      (msg, "msg"), (timeout_stage, "timeout_stage"),
                      (timeout_last, "timeout_last")):
        _u8(val, name)
    if not 0 <= timeout_bytes <= SECTOR_BYTES:
        raise ValueError(f"timeout_bytes must be 0..{SECTOR_BYTES}")
    if not 0 <= partial <= SECTOR_BYTES:
        raise ValueError(f"partial must be 0..{SECTOR_BYTES}")

    def refusal(code, lba_bytes=None):
        lhi, lmid, llo = lba_bytes or (0, 0, 0)
        return {"a": code, "c": 1, "z": 0, "issued": False, "stage": 1,
                "bytes": 0, "scsi_status": 0, "msg": 0, "last_bus": 0,
                "lba": (lhi << 16) | (lmid << 8) | llo,
                "lhi": lhi, "lmid": lmid, "llo": llo, "code": code}

    if last != 0:
        return refusal(ERR_INVALID_STATE)
    if win_hi >= 0x20:
        lba, _, hi, mid, lo = compute_lba(
            base_hi, base_mid, base_lo, cblk_hi, cblk_mid, cblk_lo)
        out = refusal(ERR_INVALID_WINDOW, (hi, mid, lo))
        return out
    lba, carry, hi, mid, lo = compute_lba(
        base_hi, base_mid, base_lo, cblk_hi, cblk_mid, cblk_lo)
    if carry:
        out = refusal(ERR_OVER_CAP, (hi, mid, lo))
        return out
    if hi >= 0x20:
        return refusal(ERR_OVER_CAP, (hi, mid, lo))
    win = (win_hi << 16) | (win_mid << 8) | win_lo
    if lba < win:
        return refusal(ERR_BEFORE, (hi, mid, lo))
    # No AFTER: LBAs past a synthetic window stay valid inputs here.
    if mode != 0x00:
        return refusal(ERR_BAD_MODE, (hi, mid, lo))
    if (size_lo, size_hi) != (0x00, 0x08):
        return refusal(ERR_BAD_SIZE, (hi, mid, lo))
    if (dst_lo, dst_hi) != (0x00, 0x28):
        return refusal(ERR_BAD_DEST, (hi, mid, lo))

    dest = (dst_hi << 8) | dst_lo
    if not (dest == FIXED_DEST and dest + SECTOR_BYTES == 0x3000):
        # Defensive: the guest only accepts exactly $2800; mirror the bound.
        return refusal(ERR_BAD_DEST, (hi, mid, lo))

    base_out = {"a": 0, "c": 0, "z": 1, "issued": True, "stage": 8,
                "bytes": SECTOR_BYTES, "scsi_status": 0, "msg": 0,
                "last_bus": PH_FREE, "lba": lba,
                "lhi": hi, "lmid": mid, "llo": lo, "code": 0}
    if scsi == "ok":
        return base_out
    if scsi == "premature":
        out = dict(base_out)
        out.update({"a": ERR_READ_FAIL, "c": 1, "z": 0, "code": ERR_READ_FAIL,
                    "stage": 5, "bytes": partial, "scsi_status": 0, "msg": 0,
                    "last_bus": PH_STATUS})
        return out
    if scsi == "tail_bad":
        if scsi_status == 0 and msg == 0:
            raise ValueError("tail_bad needs nonzero scsi_status or msg")
        out = dict(base_out)
        out.update({"a": ERR_READ_FAIL, "c": 1, "z": 0, "code": ERR_READ_FAIL,
                    "stage": 8, "bytes": SECTOR_BYTES,
                    "scsi_status": scsi_status, "msg": msg, "last_bus": PH_FREE})
        return out
    # timeout
    if timeout_stage not in (2, 3, 4, 5, 6, 7, 8):
        raise ValueError(f"bad timeout_stage {timeout_stage}")
    if timeout_stage in (2, 3, 4):
        nbytes = 0
    elif timeout_stage == 5:
        nbytes = timeout_bytes
    else:
        nbytes = SECTOR_BYTES
    out = dict(base_out)
    out.update({"a": ERR_READ_TIMEOUT, "c": 1, "z": 0,
                "code": ERR_READ_TIMEOUT, "stage": timeout_stage,
                "bytes": nbytes, "scsi_status": 0, "msg": 0,
                "last_bus": timeout_last})
    return out
