#!/usr/bin/env python3
"""Host-side TOC / track table diagnostic for a single-BIN CUE image (Issue #5).

Reads a CUE sheet and its one BIN file, applies the LBA contract shared with PR #110
(its tools/cue_analysis.py and docs/cd-read-diagnostic.md "address contract"; this branch
does not import that code), checks the
sector headers that the BIN stores for each data track, and reports each track's type,
pregap, data start LBA, absolute MSF and length. Everything here is measured from the
CUE/BIN file on the host only. It is NOT a hardware or emulator TOC read: those fields
stay "not verified" by design.

LBA contract (same as PR #110):
1. The first BIN frame is LBA 0. Frames are consumed in BIN order.
2. Pregap frames stored in the BIN (INDEX 00..01) consume LBA.
3. lba_start (INDEX 01) is the first user-data frame of the track.
4. A MODE1/2352 or MODE2/2352 sector header MSF must equal LBA + 150 (absolute_msf).
5. The next track's pregap is not part of this track's length_sectors.
6. A CUE PREGAP is a virtual gap: its frames take LBA but are not stored in the BIN.
7. Unsupported CUE forms are rejected explicitly, never converted.
Multiple FILE entries are refused (single BIN only).
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
CHUNK_FRAMES = 64  # frames per read while checking sector headers (bounded memory)
DATA_TYPES = {"MODE1/2352", "MODE2/2352"}
AUDIO_TYPE = "AUDIO"
SYNC = b"\x00" + b"\xff" * 10 + b"\x00"
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


def _bcd_to_int(byte: int) -> int:
    return (byte >> 4) * 10 + (byte & 0x0F)


def _bcd_msf_frames(header: bytes) -> int:
    """BCD MSF header bytes (minute, second, frame) to an absolute frame count (lead-in included)."""
    return (_bcd_to_int(header[0]) * SECONDS_PER_MINUTE + _bcd_to_int(header[1])) * FRAMES_PER_SECOND \
        + _bcd_to_int(header[2])


def parse_cue(text: str) -> dict:
    """Return {"file": name, "tracks": [{number, type, pregap, index00, index01}]} or raise TocError."""
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
                           "pregap": None, "index00": None, "index01": None})
        elif line.startswith("PREGAP "):
            match = re.match(r"^PREGAP (\S+)$", line)
            if not match:
                raise TocError(f"{where}: malformed PREGAP line")
            if not tracks:
                raise TocError(f"{where}: PREGAP before any TRACK")
            if tracks[-1]["index01"] is not None:
                raise TocError(f"{where}: PREGAP must precede INDEX 01")
            if tracks[-1]["pregap"] is not None:
                raise TocError(f"{where}: duplicate PREGAP")
            tracks[-1]["pregap"] = parse_msf(match.group(1))
        elif line.startswith("INDEX "):
            match = re.match(r"^INDEX (\d{2}) (\S+)$", line)
            if not match or match.group(1) not in ("00", "01"):
                raise TocError(f"{where}: malformed or unsupported INDEX line")
            if not tracks:
                raise TocError(f"{where}: INDEX before any TRACK")
            key = f"index{match.group(1)}"
            if tracks[-1][key] is not None:
                raise TocError(f"{where}: duplicate INDEX {match.group(1)}")
            if key == "index00" and tracks[-1]["index01"] is not None:
                raise TocError(f"{where}: INDEX 00 must precede INDEX 01")
            tracks[-1][key] = parse_msf(match.group(2))
        else:
            raise TocError(f"{where}: unsupported CUE directive: {line.split()[0]}")
    if len(files) != 1:
        raise TocError(f"exactly one FILE is supported, found {len(files)}")
    if not tracks:
        raise TocError("no tracks in CUE")
    for track in tracks:
        if track["index01"] is None:
            raise TocError(f"track {track['number']} needs INDEX 01")
        if track["pregap"] is not None and track["index00"] is not None:
            raise TocError(f"track {track['number']}: PREGAP and INDEX 00 are both given; ambiguous")
    return {"file": files[0], "tracks": tracks}


def build_toc(cue: dict, bin_bytes: int) -> list:
    """Derive per-track disc LBA, length and pregap with the shared LBA contract.

    Disc LBA advances through the BIN in order. A CUE PREGAP adds virtual frames that
    take LBA but are not stored in the BIN, so the BIN frame and the disc LBA can differ.
    """
    if bin_bytes <= 0 or bin_bytes % RAW_BYTES:
        raise TocError(f"BIN size {bin_bytes} is not a positive multiple of {RAW_BYTES}")
    total = bin_bytes // RAW_BYTES
    rows, cursor = [], 0
    tracks = cue["tracks"]
    for position, track in enumerate(tracks):
        number, index01, index00 = track["number"], track["index01"], track["index00"]
        if index01 >= total:
            raise TocError(f"track {number} starts at BIN frame {index01}, beyond {total} sectors")
        if index00 is not None and index00 > index01:
            raise TocError(f"track {number} INDEX 00 is after INDEX 01")
        bin_start = index01 if index00 is None else index00
        if position == 0 and bin_start != 0:
            # Contract 1 (LBA 0 = first BIN frame): frames before the first track start would have
            # no track, so they are refused instead of being guessed (same rule as PR #110).
            raise TocError(f"BIN frames before the first track start (frame {bin_start}) are not mapped to any track")
        if position + 1 < len(tracks):
            following = tracks[position + 1]
            # The next track's pregap (INDEX 00) belongs to the next track, so it ends this track.
            boundary_index = "00" if following["index00"] is not None else "01"
            bin_end = following[f"index{boundary_index}"]
            if bin_end <= index01:
                raise TocError(f"track {following['number']} INDEX {boundary_index} "
                               f"is not after the previous track")
        else:
            bin_end = total
        virtual = track["pregap"] or 0
        pregap_bin = index01 - bin_start
        pregap_start_lba = cursor
        start_lba = cursor + virtual + pregap_bin
        length = bin_end - index01
        cursor = start_lba + length
        rows.append({
            "track": number,
            "type": track["type"],
            "pregap_start_lba": pregap_start_lba,
            "lba_start": start_lba,
            "absolute_msf": format_msf(start_lba + LEAD_IN_FRAMES),
            "length_sectors": length,
            "pregap_sectors": virtual + pregap_bin,
            "pregap_declared": index00 is not None or track["pregap"] is not None,
            "bin_start_frame": bin_start,
            "bin_index01_frame": index01,
            "bin_end_frame": bin_end,
        })
    return rows


def check_sectors(bin_path: Path, rows: list) -> dict:
    """Bounded header check of the BIN for data tracks.

    A stored frame with a sync pattern must carry an MSF header equal to its disc LBA + 150.
    The first user-data frame of each data track must have sync. Pregap frames without sync
    are legal (silence or unknown data). Payload bytes are never reported.
    """
    findings, probed, sync_frames = [], 0, 0
    with open(bin_path, "rb") as f:
        for row in rows:
            if row["type"] != "data":
                continue
            index01 = row["bin_index01_frame"]
            # Whole stored span of the track, streamed in bounded CHUNK_FRAMES reads.
            for first in range(row["bin_start_frame"], row["bin_end_frame"], CHUNK_FRAMES):
                count = min(CHUNK_FRAMES, row["bin_end_frame"] - first)
                f.seek(first * RAW_BYTES)
                chunk = f.read(count * RAW_BYTES)
                if len(chunk) != count * RAW_BYTES:
                    raise TocError(f"short read at BIN frame {first}: BIN changed during analysis")
                for i in range(count):
                    frame = first + i
                    head = chunk[i * RAW_BYTES:i * RAW_BYTES + 15]
                    probed += 1
                    disc = row["lba_start"] + (frame - index01)
                    if head[:12] != SYNC:
                        if frame == index01:
                            findings.append(f"track {row['track']}: first user-data frame has no sync")
                        continue
                    sync_frames += 1
                    if _bcd_msf_frames(head[12:15]) != disc + LEAD_IN_FRAMES and len(findings) < 5:
                        findings.append(f"track {row['track']}: BIN frame {frame} header MSF disagrees with "
                                        f"LBA {disc} + 150")
    return {"probed_frames": probed, "sync_frames": sync_frames, "findings": findings}


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
    rows = build_toc(cue, bin_bytes)
    sector_check = check_sectors(bin_path, rows)
    if sector_check["findings"]:
        raise TocError("sector header check failed: " + "; ".join(sector_check["findings"]))
    return {
        "schema": SCHEMA,
        "status": "PASS",
        "cue": cue_path.name,
        "bin": name,
        "bin_bytes": bin_bytes,
        "sectors": bin_bytes // RAW_BYTES if bin_bytes % RAW_BYTES == 0 else None,
        "evidence": {"measured_from": "generated CUE/BIN file (host-side parse)",
                     "hardware_verified": False, "emulator_verified": False, "note": NOT_VERIFIED},
        "sector_check": sector_check,
        "tracks": rows,
    }


def render_text(report: dict) -> str:
    lines = [f"{report['status']} TOC {report['cue']} ({report['bin']}, {report['sectors']} sectors)",
             "track  type   pregap_lba  lba  abs_msf   length  pregap  pregap_declared"]
    for row in report["tracks"]:
        lines.append(f"{row['track']:>5}  {row['type']:<5}  {row['pregap_start_lba']:>10}  {row['lba_start']:>5}"
                     f"  {row['absolute_msf']}  {row['length_sectors']:>6}  {row['pregap_sectors']:>6}"
                     f"  {str(row['pregap_declared']).lower()}")
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
