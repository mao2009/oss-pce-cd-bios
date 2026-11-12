"""EXPERIMENTAL CD_BASE prototype V1 host mirror (Agent-A G5 P0).

NOT a System Card BIOS. NOT the genuine CD_BASE ABI. NOT compatibility evidence.
This is a pure-Python mirror of src/cd_base_proto.s (CD_BASE_PROTO_V1) for host
targeted tests only. It establishes no BIOS ABI; genuine (1)-(6) stay UNKNOWN/BLOCKED
per docs/cd-base-abi-ledger.md. Reference JSR 0/81, ABI VERIFIED 0/81, DEV_CALLABLE 0,
release_eligible=False.
"""
LEAD_IN = 150
MAX_U24 = 0xFFFFFF
MAX_READ6 = 0x1FFFFF

ERR_TRACK = 0x10
ERR_FLAGS = 0x11
ERR_RSVD = 0x12
ERR_RANGE = 0x21
ERR_PREGAP = 0x30
ERR_BEFORE = 0x31

STATE_ADDR = 0x2400
INPUT_BASE = 0x20F8


def _u8(v, name):
    if type(v) is not int or not 0 <= v <= 0xFF:
        raise ValueError(f"{name} must be 0..0xFF, got {v!r}")
    return v


def _u24(lo, mid, hi, name="base"):
    for val, sub in ((lo, "lo"), (mid, "mid"), (hi, "hi")):
        _u8(val, f"{name}.{sub}")
    return (hi << 16) | (mid << 8) | lo


def virtual_pregap_start(index01):
    """None when INDEX01 < LEAD_IN (no virtual pregap), else INDEX01-150."""
    if type(index01) is not int or not 0 <= index01 <= MAX_U24:
        raise ValueError(f"index01 out of 24-bit range: {index01!r}")
    if index01 < LEAD_IN:
        return None
    return index01 - LEAD_IN


def proto_v1(track, rsvd_ah, rsvd_bl, flags, base_lo, base_mid, base_hi,
             sel_track, index01, state=None):
    """Mirror of the guest routine. Returns dict with A/C/Z and updated state.

    inputs: track/flags/reserved/base bytes + selection track/index01.
    state: None (fresh, zeroed, gen 0, last 0xFF) or dict with base_hi/mid/lo,
      gen, last, off_hi/mid/lo. Errors preserve base/gen/off, update last only.
    """
    for v, n in ((track, "track"), (rsvd_ah, "rsvd_ah"), (rsvd_bl, "rsvd_bl"),
                 (flags, "flags"), (base_lo, "base_lo"), (base_mid, "base_mid"),
                 (base_hi, "base_hi"), (sel_track, "sel_track")):
        _u8(v, n)
    if type(index01) is not int or not 0 <= index01 <= MAX_READ6:
        # Selection LBA is 21-bit (READ(6) range) for synthetic media.
        raise ValueError(f"index01 out of 21-bit range: {index01!r}")
    if state is None:
        state = {"base_hi": 0, "base_mid": 0, "base_lo": 0, "gen": 0,
                 "last": 0xFF, "off_hi": 0, "off_mid": 0, "off_lo": 0}
    else:
        state = dict(state)
        for k in ("base_hi", "base_mid", "base_lo", "gen", "last",
                  "off_hi", "off_mid", "off_lo"):
            _u8(state[k], f"state.{k}")

    def fail(code):
        state["last"] = code
        return {"a": code, "c": 1, "z": 0, "state": dict(state)}

    if track != sel_track:
        return fail(ERR_TRACK)
    if flags != 0x80:
        return fail(ERR_FLAGS)
    if rsvd_ah != 0 or rsvd_bl != 0:
        return fail(ERR_RSVD)
    # Note: rsvd_dh ($20FF) checked by the caller wrapper; this core takes
    # rsvd_ah/bl only. Use proto_v1_full for the 8-byte input block.
    base = _u24(base_lo, base_mid, base_hi)
    if base > MAX_READ6:
        return fail(ERR_RANGE)
    if base >= index01:
        off = base - index01
        state.update({"base_hi": base_hi, "base_mid": base_mid, "base_lo": base_lo,
                      "gen": (state["gen"] + 1) & 0xFF, "last": 0,
                      "off_hi": (off >> 16) & 0xFF, "off_mid": (off >> 8) & 0xFF,
                      "off_lo": off & 0xFF})
        return {"a": 0, "c": 0, "z": 1, "state": dict(state)}
    pregap_start = virtual_pregap_start(index01)
    if pregap_start is None or base < pregap_start:
        return fail(ERR_BEFORE)
    return fail(ERR_PREGAP)


def proto_v1_full(inputs, sel_track, index01, state=None):
    """8-byte input block $20F8-$20FF wrapper. inputs is a dict or 8-list."""
    if isinstance(inputs, dict):
        ordered = [inputs[k] for k in ("al", "ah", "bl", "bh", "cl", "ch", "dl", "dh")]
    else:
        ordered = list(inputs)
        if len(ordered) != 8:
            raise ValueError("inputs must be 8 bytes $20F8-$20FF")
    al, ah, bl, bh, cl, ch, dl, dh = (_u8(v, f"in[{i}]") for i, v in enumerate(ordered))
    if dh != 0:
        # Preserve base/gen/off, update last only (same fail discipline).
        base_state = dict(state) if state is not None else None
        out = proto_v1(al, ah, bl, bh, cl, ch, dl, sel_track, index01, base_state)
        if out["a"] != ERR_RSVD:
            # The guest checks $20FF ($dh) alongside $20F9/$20FA; force $12.
            st = out["state"]
            st["last"] = ERR_RSVD
            return {"a": ERR_RSVD, "c": 1, "z": 0, "state": st}
        return out
    return proto_v1(al, ah, bl, bh, cl, ch, dl, sel_track, index01, state)
