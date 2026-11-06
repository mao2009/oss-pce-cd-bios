"""Device-layer judge for CD Q-subchannel replies (Issue #261, diagnostic only).

Decodes the 10-byte SCSI READ SUBCODE-Q (`$DD`) reply that the #15 CD-DA probe stores in guest RAM and
classifies it against a (state, track, LBA) position on the self-made CD-DA medium built by
tools/make_cd_image.py. Reuses the #15 probe, its checkpoints and its reply layout (tools/cdda_verify.py).

Scope: the pinned Geargrafx SCSI/CD layer only. This is NOT the System Card CD_SUBQ ABI (arguments,
return values, Q buffer layout): those stay UNKNOWN (撤去済み文書). A PASS here says
the emulator's Q reply matched the medium at a known position, never that BIOS CD_SUBQ behaves the same way.
"""
if __package__:
    from .build_cdda import AUDIO_SECTORS, AUDIO_START_LBA, DATA_SECTORS
    from .cdda_verify import PAUSED, PLAYING, STOPPED
else:
    from build_cdda import AUDIO_SECTORS, AUDIO_START_LBA, DATA_SECTORS
    from cdda_verify import PAUSED, PLAYING, STOPPED

POSITIVE, NEGATIVE = "POSITIVE", "NEGATIVE"
# Fixed-medium geometry (tools/build_cdda.py): track 1 MODE1 LBA 0..15, track 2 AUDIO LBA 16..79.
TRACKS = {1: (0, DATA_SECTORS - 1), 2: (AUDIO_START_LBA, AUDIO_START_LBA + AUDIO_SECTORS - 1)}
LEADOUT_LBA = AUDIO_START_LBA + AUDIO_SECTORS
# States that carry a current Q position in the probe's observation (IDLE = never started, no position).
POSITION_STATES = (PLAYING, PAUSED, STOPPED)
# Q reply offsets in guest RAM (CPU $2380 / $2390 / $23A0) and the state each $DD follows (tools/cdda_verify.py).
SUBQ_SLOTS = {2: 0x80, 4: 0x90, 6: 0xA0}
SUBQ_STATE = {2: PLAYING, 4: PAUSED, 6: STOPPED}
REPLY_BYTES = 10
PREGAP_FRAMES = 150   # absolute MSF = LBA + 150 (2 s lead-in)


def bcd(value: int) -> int:
    return (value // 10) << 4 | value % 10


def unbcd(byte: int) -> int:
    high, low = byte >> 4, byte & 0x0F
    if high > 9 or low > 9:
        raise ValueError(f"non-BCD byte {byte:#04x}")
    return high * 10 + low


def msf_bytes(frames: int) -> bytes:
    return bytes((bcd(frames // 4500), bcd(frames // 75 % 60), bcd(frames % 75)))


def msf_frames(field: bytes) -> int:
    minutes, seconds, frames = (unbcd(b) for b in field)
    if seconds >= 60 or frames >= 75:
        raise ValueError(f"MSF {field.hex()} out of range")
    return (minutes * 60 + seconds) * 75 + frames


def on_track(track: int, lba: int) -> bool:
    start_end = TRACKS.get(track)
    return start_end is not None and start_end[0] <= lba <= start_end[1]


def canonical_fields(state: int, track: int, lba: int) -> dict:
    """Expected decoded fields for a medium position. No range gate, so boundary LBAs can be compared too."""
    return {"state": state, "adr_control": 0x01, "track": track, "index": 1,
            "rel_frames": lba - TRACKS[track][0], "abs_frames": lba + PREGAP_FRAMES}


def _field_diff(observed: dict, state: int, track: int, lba: int) -> str:
    wanted = canonical_fields(state, track, lba)
    return ", ".join(f"{key} {observed[key]!r} != {wanted[key]!r}" for key in wanted if observed[key] != wanted[key])


def encode_reply(state: int, track: int, lba: int) -> bytes:
    """Canonical reply for a medium position: state, ADR/control 01, track, index 01, rel MSF, abs MSF."""
    return bytes((state, 0x01, bcd(track), 0x01)) + msf_bytes(lba - TRACKS[track][0]) + msf_bytes(lba + PREGAP_FRAMES)


def decode_reply(reply: bytes) -> dict:
    """Split a $DD reply into fields. Raises ValueError for a wrong length or a non-BCD / out-of-range field."""
    if len(reply) != REPLY_BYTES:
        raise ValueError(f"reply has {len(reply)} bytes, expected {REPLY_BYTES}")
    return {"state": reply[0], "adr_control": reply[1], "track": unbcd(reply[2]), "index": unbcd(reply[3]),
            "rel_frames": msf_frames(reply[4:7]), "abs_frames": msf_frames(reply[7:10])}


def judge(reply: bytes, state: int, track: int, lba: int) -> tuple:
    """Classify `reply` against the expected position (state, track, lba). Returns (verdict, reason).

    POSITIVE only when the reply matches the canonical reply for that position in every field. Everything
    else is NEGATIVE with the first reason found: no position state, off-track LBA, malformed reply, or
    differing fields (named individually so a boundary miss is visible in the evidence).
    """
    if state not in POSITION_STATES:
        return NEGATIVE, f"state {state} carries no current Q position"
    if not on_track(track, lba):
        return NEGATIVE, f"LBA {lba} is outside track {track} on the medium"
    try:
        observed = decode_reply(reply)
    except ValueError as error:
        return NEGATIVE, f"malformed reply: {error}"
    differ = _field_diff(observed, state, track, lba)
    if differ:
        return NEGATIVE, "fields differ: " + differ
    return POSITIVE, "all fields match the canonical reply for the medium position"


def observe_run(run: dict, points: dict) -> dict:
    """Judge the three $DD replies of one #15 probe run against the CD-DA medium (fail-closed).

    `points` is cdda_verify.checkpoints(records). Each reply's window is the CURRENT_LBA range between the
    checkpoint before its $DD and the checkpoint after it. Exactly one LBA in that window must match
    (POSITIVE), and the LBAs just outside it must not (boundary NEGATIVE). Anything else raises.
    """
    result = bytes.fromhex(run["result_hex"])
    out = {}
    for index, offset in SUBQ_SLOTS.items():
        state = SUBQ_STATE[index]
        reply = result[offset:offset + REPLY_BYTES]
        before = points[index - 1][1] or points[index - 1][0]
        after = points[index][0]
        low, high = before["audio"]["current_lba"], after["audio"]["current_lba"]
        matched = [lba for lba in range(low, high + 1) if judge(reply, state, 2, lba)[0] == POSITIVE]
        if len(matched) != 1:
            raise ValueError(f"$DD reply {reply.hex()} (state {state}) matches {matched} in LBA {low}..{high}; expected 1")
        fields = decode_reply(reply)
        # Field-level comparison at the neighbours, so an edge window is not rejected merely for being off-track.
        before_reason, after_reason = _field_diff(fields, state, 2, low - 1), _field_diff(fields, state, 2, high + 1)
        if not before_reason or not after_reason:
            raise ValueError(f"$DD reply {reply.hex()}: a window boundary matches its neighbour LBA "
                             f"(before {bool(before_reason)}, after {bool(after_reason)})")
        out[index] = {"reply_hex": reply.hex(), "state": fields["state"], "track": 2, "index": fields["index"],
                      "rel_frames": fields["rel_frames"], "abs_frames": fields["abs_frames"],
                      "lba": matched[0], "window": [low, high],
                      "boundary_before": {"lba": low - 1, "verdict": NEGATIVE, "reason": before_reason},
                      "boundary_after": {"lba": high + 1, "verdict": NEGATIVE, "reason": after_reason}}
    return out
