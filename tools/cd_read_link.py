"""EXPERIMENTAL CD_READ link V1 host mirror (Agent-B G5 P0).

NOT a compatible System Card BIOS. NOT the genuine CD_READ ABI. NOT compatibility
evidence. Pure-Python mirror of src/cd_read_link.s (CD_READ_LINK_V1) for host
targeted tests only. Genuine CD_BASE/CD_READ ABI stays UNKNOWN/BLOCKED per
docs/cd-base-abi-ledger.md and docs/cd-read-contract.md. Reference JSR 0/81,
ABI VERIFIED 0/81, DEV_CALLABLE 0, release_eligible=False.
"""
MAX_U24 = 0xFFFFFF
MAX_READ6 = 0x1FFFFF
WINDOW_LEN = 16  # synthetic-only data-track window length (frames)

# LINK_RES LBA spec (mirrors the guest contract in src/cd_read_link.s):
# LR_LHI/LR_LMID/LR_LLO hold the low 24 bits of base+block (total & $FFFFFF)
# on every call except INVALID_STATE (which stores $00/$00/$00; no base is
# claimed). In particular an OVER_CAP via 24-bit carry reports the wrapped
# value (e.g. base $FFFFFF + block 1 -> LR $00/$00/$00 with code $62); an
# OVER_CAP via HI >= $20 without carry reports the computed LBA.
# BEFORE/AFTER/WINDOW/valid paths report the computed LBA.

LINK_RES_ADDR = 0x2320
STATE_ADDR = 0x2400
BUF_ADDR = 0x2800
SECTOR_BYTES = 2048

ERR_INVALID_STATE = 0x60
ERR_INVALID_WINDOW = 0x61
ERR_OVER_CAP = 0x62
ERR_BEFORE = 0x63
ERR_AFTER = 0x64
ERR_READ_FAIL = 0x70
ERR_READ_TIMEOUT = 0x71


def _u8(v, name):
    if type(v) is not int or not 0 <= v <= 0xFF:
        raise ValueError(f"{name} must be 0..0xFF, got {v!r}")
    return v


def compute_lba(base_hi, base_mid, base_lo, blk_lo, blk_mid, blk_hi):
    """24-bit base+block with carry. Returns (lba24, carry_out, hi, mid, lo)."""
    for val, sub in ((base_hi, "base_hi"), (base_mid, "base_mid"),
                     (base_lo, "base_lo"), (blk_lo, "blk_lo"),
                     (blk_mid, "blk_mid"), (blk_hi, "blk_hi")):
        _u8(val, sub)
    base = (base_hi << 16) | (base_mid << 8) | base_lo
    blk = (blk_hi << 16) | (blk_mid << 8) | blk_lo
    total = base + blk
    carry = 1 if total > MAX_U24 else 0
    lba = total & MAX_U24
    return lba, carry, (lba >> 16) & 0xFF, (lba >> 8) & 0xFF, lba & 0xFF


def window_end(win_lo, win_mid, win_hi):
    """Exclusive end of the synthetic window [win, win+WINDOW_LEN)."""
    for val, sub in ((win_lo, "win_lo"), (win_mid, "win_mid"), (win_hi, "win_hi")):
        _u8(val, sub)
    win = (win_hi << 16) | (win_mid << 8) | win_lo
    return win + WINDOW_LEN


def link(base_hi, base_mid, base_lo, gen, last,
         blk_lo, blk_mid, blk_hi, win_lo, win_mid, win_hi,
         scsi="ok", state=None):
    """Mirror of the guest link routine. Returns dict with A/C/Z and LINK_RES.

    base_hi/mid/lo: CD_BASE state $2400/$2401/$2402 (HI/MID/LO, preserved).
    gen/last: state $2403/$2404 (preserved; last must be 0/DATA else INVALID_STATE).
    blk_lo/mid/hi: block operand $20F2/$20F3/$20F4 (LO/MID/HI, preserved).
    win_lo/mid/hi: window start $20F5/$20F6/$20F7 (LO/MID/HI, preserved).
    scsi: synthetic drive outcome for a valid request: "ok", "fail", "timeout".
    state: optional dict with base_hi/mid/lo, gen, last, off_hi/mid/lo (preserved).
    """
    for val, name in ((base_hi, "base_hi"), (base_mid, "base_mid"),
                      (base_lo, "base_lo"), (gen, "gen"), (last, "last"),
                      (blk_lo, "blk_lo"), (blk_mid, "blk_mid"),
                      (blk_hi, "blk_hi"), (win_lo, "win_lo"),
                      (win_mid, "win_mid"), (win_hi, "win_hi")):
        _u8(val, name)
    if scsi not in ("ok", "fail", "timeout"):
        raise ValueError(f"scsi must be ok/fail/timeout, got {scsi!r}")
    if state is None:
        state = {"base_hi": base_hi, "base_mid": base_mid, "base_lo": base_lo,
                 "gen": gen, "last": last,
                 "off_hi": 0, "off_mid": 0, "off_lo": 0}
    else:
        state = dict(state)
        for k in ("base_hi", "base_mid", "base_lo", "gen", "last",
                  "off_hi", "off_mid", "off_lo"):
            _u8(state[k], f"state.{k}")

    def refuse(code):
        lba, _, hi, mid, lo = compute_lba(base_hi, base_mid, base_lo,
                                         blk_lo, blk_mid, blk_hi)
        return {"a": code, "c": 1, "z": 0, "issued": False,
                "lba": lba, "lba_hi": hi, "lba_mid": mid, "lba_lo": lo,
                "stage": 1, "state": dict(state),
                "link_res": {"code": code, "stage": 1, "bytes": 0,
                             "scsi_status": 0, "msg": 0, "last_bus": 0,
                             "lba_hi": hi, "lba_mid": mid, "lba_lo": lo}}

    # Invalid-state: base never established (proto last != 0/DATA). The guest
    # zero-clears LR_LHI/LMID/LLO on this path (no LBA is claimed), so the
    # host mirror must NOT compute an LBA here.
    if last != 0:
        return {"a": ERR_INVALID_STATE, "c": 1, "z": 0, "issued": False,
                "lba": 0, "lba_hi": 0, "lba_mid": 0, "lba_lo": 0,
                "stage": 1, "state": dict(state),
                "link_res": {"code": ERR_INVALID_STATE, "stage": 1, "bytes": 0,
                             "scsi_status": 0, "msg": 0, "last_bus": 0,
                             "lba_hi": 0, "lba_mid": 0, "lba_lo": 0}}
    # Invalid-window: window start must fit READ(6) 21 bits.
    if win_hi >= 0x20:
        return refuse(ERR_INVALID_WINDOW)
    lba, carry, hi, mid, lo = compute_lba(base_hi, base_mid, base_lo,
                                         blk_lo, blk_mid, blk_hi)
    # 21-bit cap exceeded (including 24-bit wrap).
    if carry or lba > MAX_READ6:
        return refuse(ERR_OVER_CAP)
    win = (win_hi << 16) | (win_mid << 8) | win_lo
    if lba < win:
        return refuse(ERR_BEFORE)
    if lba >= win + WINDOW_LEN:
        return refuse(ERR_AFTER)
    # Valid request: a real READ(6) would be issued on hardware.
    if scsi == "fail":
        return {"a": ERR_READ_FAIL, "c": 1, "z": 0, "issued": True,
                "lba": lba, "lba_hi": hi, "lba_mid": mid, "lba_lo": lo,
                "stage": 6, "state": dict(state),
                "link_res": {"code": ERR_READ_FAIL, "stage": 6, "bytes": 0,
                             "scsi_status": 2, "msg": 0, "last_bus": 0,
                             "lba_hi": hi, "lba_mid": mid, "lba_lo": lo}}
    if scsi == "timeout":
        return {"a": ERR_READ_TIMEOUT, "c": 1, "z": 0, "issued": True,
                "lba": lba, "lba_hi": hi, "lba_mid": mid, "lba_lo": lo,
                "stage": 5, "state": dict(state),
                "link_res": {"code": ERR_READ_TIMEOUT, "stage": 5, "bytes": 0,
                             "scsi_status": 0, "msg": 0, "last_bus": 0,
                             "lba_hi": hi, "lba_mid": mid, "lba_lo": lo}}
    return {"a": 0, "c": 0, "z": 1, "issued": True,
            "lba": lba, "lba_hi": hi, "lba_mid": mid, "lba_lo": lo,
            "stage": 8, "state": dict(state),
            "link_res": {"code": 0, "stage": 8, "bytes": SECTOR_BYTES,
                         "scsi_status": 0, "msg": 0, "last_bus": 0,
                         "lba_hi": hi, "lba_mid": mid, "lba_lo": lo}}
