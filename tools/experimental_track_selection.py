"""Fail-closed host-to-experimental-ROM selection records.

Only :func:`selection_from_cue` constructs a normal record.  It derives the
first MODE1/2352 track's INDEX 01 absolute LBA after the synthetic medium is
finalized, and binds the record to its CUE text, ordered CUE FILE references,
referenced BIN bytes, and parsed layout.  This is a DEV-only host contract,
not a guest TOC format or a System Card API.
"""
import hashlib
import os
from pathlib import Path

try:
    from .synthetic_track_select import NoDataTrackError, select_first_mode1_track
    from .cue_analysis import analyze, parse_cue
except ImportError:
    from synthetic_track_select import NoDataTrackError, select_first_mode1_track
    from cue_analysis import analyze, parse_cue

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


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def media_identity(cue_path: Path) -> dict:
    """Return the deterministic content identity for the exact mountable CUE/BIN set.

    FILE order and spelling are part of the identity.  `analyze` supplies the
    parsed MODE1/2352/index layout and also rejects malformed referenced media.
    This validates host inputs only; it is not a guest cryptographic proof.
    """
    cue_path = Path(cue_path)
    try:
        report = analyze(cue_path)
        files = parse_cue(cue_path)["files"]
        references = []
        for entry in files:
            relative = entry["name"]
            path = cue_path.parent / relative
            if not path.is_file() or path.is_symlink():
                raise SelectionError(f"referenced BIN is not a regular file: {relative}")
            references.append({"path": relative, "bytes": path.stat().st_size, "sha256": _sha256(path)})
    except (OSError, ValueError) as error:
        if isinstance(error, SelectionError):
            raise
        raise SelectionError(f"cannot identify mounted media: {error}") from error
    layout = [{key: track[key] for key in ("number", "mode", "file", "index", "index01_frame",
                                            "pregap_start_lba", "start_lba", "length_frames", "sector_bytes")}
              for track in report["tracks"]]
    return {"cue": cue_path.name, "cue_sha256": _sha256(cue_path), "files": references, "layout": layout}


def verify_media_identity(identity: dict, cue_path: Path) -> None:
    """Reject any CUE/BIN/layout change before passing the CUE to Geargrafx."""
    if type(identity) is not dict or set(identity) != {"cue", "cue_sha256", "files", "layout"}:
        raise SelectionError("selection media identity is malformed")
    actual = media_identity(cue_path)
    if actual != identity:
        raise SelectionError("mounted media content differs from the selection record")


def snapshot_media(cue_path: Path, destination: Path) -> Path:
    """Copy a verified synthetic CUE/BIN set to a test-private read-only mount snapshot.

    The copied files are read-only and live in a caller-owned temporary
    directory whose normal test path never mutates them afterwards.  It cannot
    protect against a writer with directory access (or a privileged external
    writer) between the final check and Geargrafx's eventual open; callers must
    retain that limit.
    """
    cue_path, destination = Path(cue_path), Path(destination)
    identity = media_identity(cue_path)
    if destination.exists():
        raise SelectionError("media snapshot destination already exists")
    destination.mkdir(mode=0o700)
    try:
        (destination / cue_path.name).write_bytes(cue_path.read_bytes())
        for item in identity["files"]:
            relative = Path(item["path"])
            if relative.is_absolute() or ".." in relative.parts:
                raise SelectionError("CUE reference is unsafe for a private snapshot")
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((cue_path.parent / relative).read_bytes())
            os.chmod(target, 0o444)
        mounted = destination / cue_path.name
        os.chmod(mounted, 0o444)
        if media_identity(mounted) != identity:
            raise SelectionError("private media snapshot differs from its verified source")
        return mounted
    except Exception:
        # This destination was just created and is caller-owned; do not remove
        # it here, so a failed snapshot remains inspectable as failed evidence.
        raise


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
    identity = media_identity(cue_path)
    selected_track = next((item for item in identity["layout"] if item["number"] == track), None)
    if selected_track is None or selected_track["mode"] != "MODE1/2352" or selected_track["start_lba"] != lba:
        raise SelectionError("selected MODE1 INDEX 01 does not match finalized media layout")
    return {"magic": MAGIC, "version": VERSION, "format": FORMAT,
            "kind": KIND_MODE1_INDEX01, "track": track, "lba": lba,
            "cue": cue_path.name, "media_identity": identity}


def assembler_defines(record: dict) -> list[str]:
    """Return explicit ca65 defines for the ROM-embedded record."""
    required = {"magic", "version", "format", "kind", "track", "lba", "cue", "media_identity"}
    if not isinstance(record, dict) or set(record) != required:
        raise SelectionError("selection record is not host-derived")
    if (record["magic"], record["version"], record["format"], record["kind"]) != \
            (MAGIC, VERSION, FORMAT, KIND_MODE1_INDEX01):
        raise SelectionError("selection record has an unsupported format or kind")
    track, lba = record["track"], record["lba"]
    if type(track) is not int or not 1 <= track <= 99 or type(lba) is not int or not 0 <= lba <= MAX_LBA:
        raise SelectionError("selection record has an invalid track or LBA")
    identity = record["media_identity"]
    if type(record["cue"]) is not str or not record["cue"] or type(identity) is not dict \
            or set(identity) != {"cue", "cue_sha256", "files", "layout"} or identity["cue"] != record["cue"]:
        raise SelectionError("selection record has no valid media identity")
    return ["-D", f"EXP_SELECTED_KIND=${KIND_MODE1_INDEX01:02X}",
            "-D", f"EXP_SELECTED_TRACK=${track:02X}",
            "-D", f"EXP_SELECTED_LBA_HI=${lba >> 16:02X}",
            "-D", f"EXP_SELECTED_LBA_MID=${lba >> 8 & 0xFF:02X}",
            "-D", f"EXP_SELECTED_LBA_LO=${lba & 0xFF:02X}"]
