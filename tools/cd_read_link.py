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


PH_STATUS = 0xD8
PH_COMMAND = 0xD0
PH_FREE = 0x00


def link(base_hi, base_mid, base_lo, gen, last,
         blk_lo, blk_mid, blk_hi, win_lo, win_mid, win_hi,
         scsi="ok", state=None, partial=0, scsi_status=2, msg=0,
         timeout_stage=5, timeout_bytes=0, timeout_last=PH_COMMAND):
    """Mirror of the guest link routine. Returns dict with A/C/Z and LINK_RES.

    base_hi/mid/lo: CD_BASE state $2400/$2401/$2402 (HI/MID/LO, preserved).
    gen/last: state $2403/$2404 (preserved; last must be 0/DATA else INVALID_STATE).
    blk_lo/mid/hi: block operand $20F2/$20F3/$20F4 (LO/MID/HI, preserved).
    win_lo/mid/hi: window start $20F5/$20F6/$20F7 (LO/MID/HI, preserved).
    scsi: synthetic drive outcome for a valid request, mirroring the guest
      control flow in src/cd_read_link.s (review 5872704142). There is NO
      single "fail" mode: the guest reaches $70 via two DISTINCT paths with
      disjoint (stage, bytes, scsi_status) triples, so the mirror exposes both:
      - "ok": full 2048-byte DATA_IN, GOOD status/message -> $00, stage 8.
      - "premature": early STATUS during the DATA_IN wait (@premature) ->
        $70, stage 5, bytes = partial (0..2047), scsi_status 0 (STATUS byte
        never read), msg 0, last_bus $D8. Observed on the pinned core for an
        off-disc READ: LINK_RES 70 05 00 00 00 00 D8 ....
      - "tail_bad": CHECK CONDITION after the full transfer (@tail_bad) ->
        $70, stage 8, bytes 2048, last_bus $00; scsi_status/msg keyword args
        (at least one must be nonzero, else the guest takes success).
        NOT observed on the real core (synthetic drive always GOOD); host-only.
      - "timeout": any guest wait expiring without the wanted phase
        (free/select/command/data-ACK before DATA_IN, tail waits, bus-free
        wait) -> $71 with the stage/bytes of that site: stages 2/3/4 carry
        0 bytes, stage 5 carries partial (0..2047), stages 6/7/8 carry 2048.
        timeout_stage/timeout_bytes/timeout_last select the site; combos
        outside the guest flow raise ValueError. Default (5, 0, $D0) is a
        reachable DATA_IN-wait expiry with the drive stuck in COMMAND phase.
        No timeout site is pinned on the real core; host-only per-site model.
    state: optional dict with base_hi/mid/lo, gen, last, off_hi/mid/lo (preserved).
    """
    for val, name in ((base_hi, "base_hi"), (base_mid, "base_mid"),
                      (base_lo, "base_lo"), (gen, "gen"), (last, "last"),
                      (blk_lo, "blk_lo"), (blk_mid, "blk_mid"),
                      (blk_hi, "blk_hi"), (win_lo, "win_lo"),
                      (win_mid, "win_mid"), (win_hi, "win_hi")):
        _u8(val, name)
    if scsi == "fail":
        raise ValueError(
            "scsi='fail' was removed (review 5872704142): it returned the "
            "unreachable stage 6 + bytes 0 + scsi_status 2 combination. Use "
            "scsi='premature' (early STATUS: $70/stage 5/partial bytes) or "
            "scsi='tail_bad' (post-transfer bad status: $70/stage 8/2048 bytes).")
    if scsi not in ("ok", "premature", "tail_bad", "timeout"):
        raise ValueError(
            f"scsi must be ok/premature/tail_bad/timeout, got {scsi!r}")
    for val, name in ((scsi_status, "scsi_status"),
                      (msg, "msg"), (timeout_stage, "timeout_stage"),
                      (timeout_last, "timeout_last")):
        _u8(val, name)
    if not 0 <= timeout_bytes <= SECTOR_BYTES:
        raise ValueError(
            f"timeout_bytes must be 0..{SECTOR_BYTES}, got {timeout_bytes!r}")
    if type(partial) is not int or not 0 <= partial <= SECTOR_BYTES - 1:
        raise ValueError(
            f"partial must be 0..{SECTOR_BYTES - 1}, got {partial!r}")
    if scsi == "tail_bad" and (scsi_status | msg) == 0:
        raise ValueError(
            "tail_bad needs scsi_status|msg != 0 (all-GOOD takes success $00)")
    if scsi == "timeout":
        ok_site = ((timeout_stage in (2, 3, 4) and timeout_bytes == 0)
                   or (timeout_stage == 5 and timeout_bytes <= SECTOR_BYTES - 1)
                   or (timeout_stage in (6, 7, 8)
                       and timeout_bytes == SECTOR_BYTES))
        if not ok_site:
            raise ValueError(
                f"unreachable timeout site: stage {timeout_stage} with "
                f"{timeout_bytes} bytes (guest: 2/3/4->0, 5->0..2047, "
                "6/7/8->2048)")
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
    # Valid request: a real READ(6) would be issued on hardware. Fault modes
    # mirror the guest sites in src/cd_read_link.s (review 5872704142).
    if scsi == "premature":
        # @premature: DATA_IN wait aborted by early STATUS. Stage stays 5,
        # bytes = transfer count so far, STATUS byte never read (scsi 0).
        return {"a": ERR_READ_FAIL, "c": 1, "z": 0, "issued": True,
                "lba": lba, "lba_hi": hi, "lba_mid": mid, "lba_lo": lo,
                "stage": 5, "state": dict(state),
                "link_res": {"code": ERR_READ_FAIL, "stage": 5,
                             "bytes": partial,
                             "scsi_status": 0, "msg": 0,
                             "last_bus": PH_STATUS,
                             "lba_hi": hi, "lba_mid": mid, "lba_lo": lo}}
    if scsi == "tail_bad":
        # @tail_bad: full 2048 bytes moved, FREE observed, then SCSI|MSG != 0.
        return {"a": ERR_READ_FAIL, "c": 1, "z": 0, "issued": True,
                "lba": lba, "lba_hi": hi, "lba_mid": mid, "lba_lo": lo,
                "stage": 8, "state": dict(state),
                "link_res": {"code": ERR_READ_FAIL, "stage": 8,
                             "bytes": SECTOR_BYTES,
                             "scsi_status": scsi_status, "msg": msg,
                             "last_bus": PH_FREE,
                             "lba_hi": hi, "lba_mid": mid, "lba_lo": lo}}
    if scsi == "timeout":
        return {"a": ERR_READ_TIMEOUT, "c": 1, "z": 0, "issued": True,
                "lba": lba, "lba_hi": hi, "lba_mid": mid, "lba_lo": lo,
                "stage": timeout_stage, "state": dict(state),
                "link_res": {"code": ERR_READ_TIMEOUT,
                             "stage": timeout_stage, "bytes": timeout_bytes,
                             "scsi_status": 0, "msg": 0,
                             "last_bus": timeout_last,
                             "lba_hi": hi, "lba_mid": mid, "lba_lo": lo}}
    return {"a": 0, "c": 0, "z": 1, "issued": True,
            "lba": lba, "lba_hi": hi, "lba_mid": mid, "lba_lo": lo,
            "stage": 8, "state": dict(state),
            "link_res": {"code": 0, "stage": 8, "bytes": SECTOR_BYTES,
                         "scsi_status": 0, "msg": 0, "last_bus": 0,
                         "lba_hi": hi, "lba_mid": mid, "lba_lo": lo}}
