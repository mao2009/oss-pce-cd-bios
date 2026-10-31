"""Host-to-experimental-ROM selection record for original synthetic CUE tests.

The host alone analyses the CUE.  The resulting record tells the DEV-only ROM
which already-identified MODE1/2352 INDEX 01 sector to read; it is not a guest
TOC format or a System Card API.
"""

KIND_MODE1_INDEX01 = 1
KIND_AUDIO_OR_INVALID = 0
MAX_LBA = 0x1FFFFF


def selection(track: int, lba: int, kind: int = KIND_MODE1_INDEX01) -> dict:
    """Create a bounded record matching ``experimental_selection`` in assembly."""
    if not isinstance(track, int) or not isinstance(lba, int) or not isinstance(kind, int):
        raise ValueError("selection fields must be integers")
    if not 0 <= lba <= MAX_LBA:
        raise ValueError("selection LBA is outside READ(6)'s 21-bit range")
    if not 0 <= track <= 255 or not 0 <= kind <= 255:
        raise ValueError("selection byte field is outside 0..255")
    return {"version": 1, "kind": kind, "track": track, "lba": lba}


def assembler_defines(record: dict) -> list[str]:
    """Return explicit ca65 defines for the ROM-embedded record."""
    value = selection(record["track"], record["lba"], record["kind"])
    return ["-D", f"EXP_SELECTED_KIND=${value['kind']:02X}",
            "-D", f"EXP_SELECTED_TRACK=${value['track']:02X}",
            "-D", f"EXP_SELECTED_LBA_HI=${value['lba'] >> 16:02X}",
            "-D", f"EXP_SELECTED_LBA_MID=${value['lba'] >> 8 & 0xFF:02X}",
            "-D", f"EXP_SELECTED_LBA_LO=${value['lba'] & 0xFF:02X}"]
