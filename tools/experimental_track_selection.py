"""Fail-closed host-to-experimental-ROM selection records.

Only :func:`selection_from_cue` constructs a normal record.  It derives the
first MODE1/2352 track's INDEX 01 absolute LBA from the exact CUE that will be
mounted.  This is a DEV-only handoff, not a guest TOC format or a System Card
API.
"""
from pathlib import Path

try:
    from .synthetic_track_select import NoDataTrackError, select_first_mode1_track
except ImportError:
    from synthetic_track_select import NoDataTrackError, select_first_mode1_track

MAGIC = "XSEL"
VERSION = 1
FORMAT = 1
KIND_MODE1_INDEX01 = 1
KIND_AUDIO_OR_INVALID = 0
MAX_LBA = 0x1FFFFF


class SelectionError(ValueError):
    """The host cannot construct a safe record for the mounted synthetic CUE."""


def _same_mount(cue_path: Path, mounted_cue: Path) -> None:
    """Refuse a record derived from one CUE while another CUE is mounted."""
    if cue_path.resolve() != mounted_cue.resolve():
        raise SelectionError("selection CUE and mounted CUE differ")


def selection_from_cue(cue_path: Path, mounted_cue: Path | None = None) -> dict:
    """Derive the sole accepted normal record from a parsed mounted CUE.

    The CUE must be the actual mount target.  Audio-only, MODE2, malformed, or
    structurally mismatched media never turn into arbitrary metadata records.
    """
    cue_path = Path(cue_path)
    mounted_cue = cue_path if mounted_cue is None else Path(mounted_cue)
    _same_mount(cue_path, mounted_cue)
    try:
        selected = select_first_mode1_track(cue_path)
    except (NoDataTrackError, ValueError) as error:
        raise SelectionError(str(error)) from error
    track, lba = selected["track"], selected["lba"]
    if type(track) is not int or not 1 <= track <= 99:
        raise SelectionError("parsed MODE1 track number is outside 1..99")
    if type(lba) is not int or not 0 <= lba <= MAX_LBA:
        raise SelectionError("parsed INDEX 01 LBA is outside READ(6)'s 21-bit range")
    return {"magic": MAGIC, "version": VERSION, "format": FORMAT,
            "kind": KIND_MODE1_INDEX01, "track": track, "lba": lba,
            "cue": cue_path.name}


def assembler_defines(record: dict) -> list[str]:
    """Return explicit ca65 defines for the ROM-embedded record."""
    required = {"magic", "version", "format", "kind", "track", "lba", "cue"}
    if not isinstance(record, dict) or set(record) != required:
        raise SelectionError("selection record is not host-derived")
    if (record["magic"], record["version"], record["format"], record["kind"]) != \
            (MAGIC, VERSION, FORMAT, KIND_MODE1_INDEX01):
        raise SelectionError("selection record has an unsupported format or kind")
    track, lba = record["track"], record["lba"]
    if type(track) is not int or not 1 <= track <= 99 or type(lba) is not int or not 0 <= lba <= MAX_LBA:
        raise SelectionError("selection record has an invalid track or LBA")
    return ["-D", f"EXP_SELECTED_KIND=${KIND_MODE1_INDEX01:02X}",
            "-D", f"EXP_SELECTED_TRACK=${track:02X}",
            "-D", f"EXP_SELECTED_LBA_HI=${lba >> 16:02X}",
            "-D", f"EXP_SELECTED_LBA_MID=${lba >> 8 & 0xFF:02X}",
            "-D", f"EXP_SELECTED_LBA_LO=${lba & 0xFF:02X}"]
