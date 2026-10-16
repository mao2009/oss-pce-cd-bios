#!/usr/bin/env python3
"""Host-side TOC / track table diagnostic for a synthetic CUE/BIN image (Issue #5).

Reads a CUE sheet and its BIN file, then reports each track's type, INDEX 01
start (LBA and absolute MSF), length and pregap as declared by the CUE.
Everything here is measured from the generated file on the host only. It is NOT
a hardware or emulator TOC read: those fields stay "not verified" by design.

Address definitions (physical, BIN-relative; differs from PR #110, see below):
- lba_start: sector index in the BIN file, 0 = first BIN sector. The pregap of
  a track is part of the BIN, so INDEX 01 of track 1 with INDEX 00 at 00:00:00
  gives lba_start 150.
- absolute_msf: lba_start + 150 (lead-in), i.e. BIN sector 0 is 00:02:00.
- length_sectors: from this track's INDEX 01 to the next track's INDEX 00 (or
  INDEX 01 when the next track has no INDEX 00), or to the end of the BIN for
  the last track. The next track's pregap belongs to the next track and is not
  counted here.
- pregap_sectors: INDEX 01 - INDEX 00 of the same track, 0 when undeclared.

PR #110 (cue analyzer) uses a logical LBA: disc LBA 0 is INDEX 01 of the first
data track, and its track spans include pregap bytes. The two parsers do not
share semantics; do not compare their numbers directly.
"""
import argparse
import json
import os
from pathlib import Path
import re
import sys

if __package__:
    from .make_cd_image import RAW_BYTES
else:
    from make_cd_image import RAW_BYTES

SCHEMA = "oss-pce-toc-diagnostic/v1"
FRAMES_PER_SECOND = 75
SECONDS_PER_MINUTE = 60
LEAD_IN_FRAMES = 150  # absolute MSF 00:02:00 is LBA 0
MAX_TRACK = 99
DATA_TYPES = {"MODE1/2352", "MODE2/2352"}
AUDIO_TYPE = "AUDIO"
NOT_VERIFIED = ("not verified: host-side CUE/BIN measurement only; "
                "no real hardware or Geargrafx TOC read was performed")
MSF_RE = re.compile(r"^(\d{2}):(\d{2}):(\d{2})$")


class TocError(ValueError):
    pass


def parse_msf(text: str) -> int:
    """MM:SS:FF (file-relative, as used by CUE INDEX) to a frame count."""
    match = MSF_RE.match(text)
    if not match:
        raise TocError(f"malformed MSF: {text!r}")
    minutes, seconds, frames = (int(group) for group in match.groups())
    if seconds >= SECONDS_PER_MINUTE or frames >= FRAMES_PER_SECOND:
        raise TocError(f"MSF field out of range: {text!r}")
    return (minutes * SECONDS_PER_MINUTE + seconds) * FRAMES_PER_SECOND + frames


def format_msf(frames: int) -> str:
    return f"{frames // (FRAMES_PER_SECOND * SECONDS_PER_MINUTE):02d}:" \
           f"{frames // FRAMES_PER_SECOND % SECONDS_PER_MINUTE:02d}:{frames % FRAMES_PER_SECOND:02d}"


def parse_cue(text: str) -> dict:
    """Return {"file": name, "tracks": [{number, type, index00, index01}]} or raise TocError."""
    files, tracks = [], []
    for line_number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        where = f"line {line_number}"
        if line.startswith("FILE "):
            match = re.match(r'^FILE "([^"]+)" BINARY$', line)
            if not match:
                raise TocError(f"{where}: unsupported FILE line")
            files.append(match.group(1))
        elif line.startswith("TRACK "):
            match = re.match(r"^TRACK (\d{2}) (\S+)$", line)
            if not match:
                raise TocError(f"{where}: malformed TRACK line")
            number, kind = int(match.group(1)), match.group(2)
            if not 1 <= number <= MAX_TRACK:
                raise TocError(f"{where}: track number out of range: {number}")
            if kind not in DATA_TYPES and kind != AUDIO_TYPE:
                raise TocError(f"{where}: unsupported track type: {kind}")
            if tracks and number != tracks[-1]["number"] + 1:
                raise TocError(f"{where}: track numbers must be consecutive (got {number})")
            if not tracks and number != 1:
                raise TocError(f"{where}: first track must be 1")
            tracks.append({"number": number, "type": "data" if kind in DATA_TYPES else "audio",
                           "index00": None, "index01": None})
        elif line.startswith("INDEX "):
            match = re.match(r"^INDEX (\d{2}) (\S+)$", line)
            if not match or match.group(1) not in ("00", "01"):
                raise TocError(f"{where}: malformed or unsupported INDEX line")
            if not tracks:
                raise TocError(f"{where}: INDEX before any TRACK")
            key = f"index{match.group(1)}"
            if tracks[-1][key] is not None:
                raise TocError(f"{where}: duplicate INDEX {match.group(1)}")
            tracks[-1][key] = parse_msf(match.group(2))
        else:
            raise TocError(f"{where}: unsupported CUE directive: {line.split()[0]}")
    if len(files) != 1:
        raise TocError(f"exactly one FILE is supported, found {len(files)}")
    if not tracks:
        raise TocError("no tracks in CUE")
    if any(track["index01"] is None for track in tracks):
        raise TocError("every track needs INDEX 01")
    return {"file": files[0], "tracks": tracks}


def build_toc(cue: dict, bin_bytes: int) -> list:
    """Derive per-track LBA, length and pregap; every value is checked against the BIN size."""
    if bin_bytes <= 0 or bin_bytes % RAW_BYTES:
        raise TocError(f"BIN size {bin_bytes} is not a positive multiple of {RAW_BYTES}")
    total = bin_bytes // RAW_BYTES
    rows = []
    tracks = cue["tracks"]
    for position, track in enumerate(tracks):
        start = track["index01"]
        if start >= total:
            raise TocError(f"track {track['number']} starts at LBA {start}, beyond {total} sectors")
        index00 = track["index00"]
        if index00 is not None and index00 > start:
            raise TocError(f"track {track['number']} INDEX 00 is after INDEX 01")
        if position + 1 < len(tracks):
            following = tracks[position + 1]
            # The next track's pregap (INDEX 00) belongs to the next track, so it ends this track.
            boundary_index = "00" if following["index00"] is not None else "01"
            end = following[f"index{boundary_index}"]
            if end <= start:
                raise TocError(f"track {following['number']} INDEX {boundary_index} "
                               f"is not after the previous track")
        else:
            end = total
        rows.append({
            "track": track["number"],
            "type": track["type"],
            "lba_start": start,
            "absolute_msf": format_msf(start + LEAD_IN_FRAMES),
            "length_sectors": end - start,
            "pregap_sectors": 0 if index00 is None else start - index00,
            "pregap_declared": index00 is not None,
        })
    return rows


def diagnose(cue_path: Path) -> dict:
    cue_path = Path(cue_path)
    try:
        cue = parse_cue(cue_path.read_text(encoding="ascii"))
    except (OSError, UnicodeDecodeError) as error:
        raise TocError(f"cannot read CUE: {error}") from error
    name = cue["file"]
    if name != Path(name).name:
        raise TocError("FILE must name a file in the same directory as the CUE")
    bin_path = cue_path.parent / name
    try:
        bin_bytes = os.path.getsize(bin_path)
    except OSError as error:
        raise TocError(f"cannot read BIN: {error}") from error
    return {
        "schema": SCHEMA,
        "status": "PASS",
        "cue": cue_path.name,
        "bin": name,
        "bin_bytes": bin_bytes,
        "sectors": bin_bytes // RAW_BYTES if bin_bytes % RAW_BYTES == 0 else None,
        "evidence": {"measured_from": "generated CUE/BIN file (host-side parse)",
                     "hardware_verified": False, "emulator_verified": False, "note": NOT_VERIFIED},
        "tracks": build_toc(cue, bin_bytes),
    }


def render_text(report: dict) -> str:
    lines = [f"{report['status']} TOC {report['cue']} ({report['bin']}, {report['sectors']} sectors)",
             "track  type   lba  abs_msf   length  pregap  pregap_declared"]
    for row in report["tracks"]:
        lines.append(f"{row['track']:>5}  {row['type']:<5}  {row['lba_start']:>5}  {row['absolute_msf']}"
                     f"  {row['length_sectors']:>6}  {row['pregap_sectors']:>6}  {str(row['pregap_declared']).lower()}")
    lines.append(f"NOTE {NOT_VERIFIED}")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cue", type=Path)
    parser.add_argument("--json", action="store_true", help="emit the JSON report instead of text")
    args = parser.parse_args(argv)
    try:
        report = diagnose(args.cue)
    except TocError as error:
        report = {"schema": SCHEMA, "status": "FAIL", "cue": args.cue.name, "error": str(error),
                  "evidence": {"hardware_verified": False, "emulator_verified": False, "note": NOT_VERIFIED}}
        print(json.dumps(report, indent=2) if args.json else f"FAIL TOC {args.cue.name}: {error}")
        return 1
    print(json.dumps(report, indent=2, sort_keys=True) if args.json else render_text(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
