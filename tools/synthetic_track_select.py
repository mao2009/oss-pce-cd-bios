"""Select a readable data track from an original synthetic CUE/BIN layout.

This is a host-side test helper.  It consumes the read-only CUE analysis report;
it is not a System Card TOC command, guest code, or BIOS API implementation.
"""
from pathlib import Path

try:
    from .cue_analysis import CueError, analyze
except ImportError:  # direct execution after tools/ was placed on sys.path
    from cue_analysis import CueError, analyze


class NoDataTrackError(ValueError):
    """The synthetic layout has no MODE1/2352 INDEX 01 data track."""


def select_first_mode1_track(cue_path: Path) -> dict:
    """Return the first MODE1/2352 track's number and INDEX 01 absolute LBA.

    Stored INDEX 00 pregap frames are deliberately not readable user data: the
    selected LBA is ``start_lba`` (INDEX 01), as computed by cue_analysis.
    """
    report = analyze(Path(cue_path))
    if report["findings"]:
        raise CueError("synthetic CUE layout is structurally invalid: " + "; ".join(report["findings"]))
    for track in report["tracks"]:
        if track["mode"] == "MODE1/2352":
            return {"track": track["number"], "lba": track["start_lba"], "report": report}
    raise NoDataTrackError("synthetic layout has no MODE1/2352 data track")
