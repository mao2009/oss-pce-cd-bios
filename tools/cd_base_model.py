"""Address arithmetic model for the CD_BASE contract (Issue #11).

Pure functions, standard library only. This is a host-side model of the CUE/BIN layout
(INDEX 00/01, virtual PREGAP) and of the candidate 24-bit block-number bases. It does not
run the System Card callee and does not establish the BIOS ABI. See docs/cd-base-contract.md.
"""

FRAMES_PER_SECOND = 75
SECONDS_PER_MINUTE = 60
LEAD_IN_FRAMES = 150  # absolute MSF 00:02:00 is LBA 0
MAX_MSF_MINUTES = 99  # MSF fields are two BCD digits
MAX_U24 = 0xFFFFFF
MAX_READ6_LBA = 0x1FFFFF  # SCSI READ(6) LBA field is 21 bits


def _check_int(value, name):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an int, got {value!r}")
    return value


def check_u24(value, name="value"):
    """Accept only integers in 0..0xFFFFFF. No wrapping."""
    _check_int(value, name)
    if not 0 <= value <= MAX_U24:
        raise ValueError(f"{name} out of 24-bit range: {value}")
    return value


def fits_read6_lba(lba):
    """True when lba fits the 21-bit READ(6) LBA field (no exception for bad input)."""
    return isinstance(lba, int) and not isinstance(lba, bool) and 0 <= lba <= MAX_READ6_LBA


def msf_to_frames(minutes, seconds, frames):
    """Relative MSF (within a track or FILE) to a frame count. Field ranges are checked."""
    for name, value, limit in (
        ("minutes", minutes, MAX_MSF_MINUTES + 1),
        ("seconds", seconds, SECONDS_PER_MINUTE),
        ("frames", frames, FRAMES_PER_SECOND),
    ):
        _check_int(value, name)
        if not 0 <= value < limit:
            raise ValueError(f"MSF {name} out of range: {value}")
    return (minutes * SECONDS_PER_MINUTE + seconds) * FRAMES_PER_SECOND + frames


def frames_to_msf(count):
    """Frame count to (minutes, seconds, frames). Rejects counts beyond 99:59:74."""
    _check_int(count, "count")
    if not 0 <= count < (MAX_MSF_MINUTES + 1) * SECONDS_PER_MINUTE * FRAMES_PER_SECOND:
        raise ValueError(f"frame count out of MSF range: {count}")
    seconds_total, frames = divmod(count, FRAMES_PER_SECOND)
    minutes, seconds = divmod(seconds_total, SECONDS_PER_MINUTE)
    return minutes, seconds, frames


def absolute_msf_to_lba(minutes, seconds, frames):
    """Absolute MSF (as printed in the TOC) to disc LBA. 00:02:00 is LBA 0."""
    lba = msf_to_frames(minutes, seconds, frames) - LEAD_IN_FRAMES
    if lba < 0:
        raise ValueError(f"absolute MSF {minutes:02}:{seconds:02}:{frames:02} is before LBA 0")
    return lba


def lba_to_absolute_msf(lba):
    check_u24(lba, "lba")
    return frames_to_msf(lba + LEAD_IN_FRAMES)


def virtual_pregap_start(index01_lba, pregap_frames):
    """First disc LBA of a track whose PREGAP (not stored in the BIN) precedes INDEX 01."""
    check_u24(index01_lba, "index01_lba")
    _check_int(pregap_frames, "pregap_frames")
    if pregap_frames < 0 or pregap_frames > index01_lba:
        raise ValueError(f"virtual PREGAP of {pregap_frames} frames does not fit before LBA {index01_lba}")
    return index01_lba - pregap_frames


def region(lba, index01_lba, pregap_start_lba, end_lba):
    """Classify a disc LBA against one track.

    Returns 'data' for index01_lba <= lba < end_lba, 'after_track' for lba >= end_lba (end is
    exclusive; the next track or disc end), 'pregap' for pregap_start_lba <= lba < index01_lba,
    and 'before_track' otherwise. end_lba is index01_lba + length_frames and must be greater
    than index01_lba. pregap_start_lba is None when the track has no pregap frames before INDEX 01.
    Host-side layout classification only; it does not establish the BIOS ABI.
    """
    check_u24(lba, "lba")
    check_u24(index01_lba, "index01_lba")
    check_u24(end_lba, "end_lba")
    if end_lba <= index01_lba:
        raise ValueError(f"end_lba {end_lba} must be greater than index01_lba {index01_lba}")
    if lba >= end_lba:
        return "after_track"
    if lba >= index01_lba:
        return "data"
    if pregap_start_lba is not None and pregap_start_lba <= lba:
        return "pregap"
    return "before_track"


def index01_relative(lba, index01_lba):
    """Signed offset of lba from INDEX 01 (negative inside the pregap)."""
    check_u24(lba, "lba")
    check_u24(index01_lba, "index01_lba")
    return lba - index01_lba


def lba_to_block(lba, cd_base_lba):
    """Candidate block number: lba counted from base. Base 0 means absolute LBA.

    Raises for lba below the base. It never wraps. Whether the BIOS uses this form is UNKNOWN.
    """
    check_u24(lba, "lba")
    check_u24(cd_base_lba, "cd_base_lba")
    if lba < cd_base_lba:
        raise ValueError(f"lba {lba} is below base {cd_base_lba}")
    return lba - cd_base_lba


def block_to_lba(block, cd_base_lba):
    """Inverse of lba_to_block. Raises when base + block leaves the 24-bit space."""
    check_u24(block, "block")
    check_u24(cd_base_lba, "cd_base_lba")
    return check_u24(cd_base_lba + block, "base + block")
