#!/usr/bin/env python3
"""Host-side TOC / track table diagnostic for a single-BIN CUE image (Issue #5).

Reads a CUE sheet and its one BIN file, applies the LBA contract shared with PR #110
(its tools/cue_analysis.py and docs/cd-read-diagnostic.md "address contract"; this branch
does not import that code), checks the
sector headers that the BIN stores for each data track, and reports each track's type,
pregap, data start LBA, absolute MSF and length. Everything here is measured from the
CUE/BIN file on the host only. It is NOT a hardware or emulator TOC read: those fields
stay "not verified" by design.

Address terms (the same definitions as PR #110's cue_analysis.py):
- physical disc LBA: the disc address. It advances by one for every frame, pregap frames included.
- INDEX 01 start (`lba_start`): the physical LBA of the track's first user-data frame.
- BIN frame offset (`bin_*_frame`): a frame index within the single BIN file. It is not a disc LBA.
- BIN INDEX 00 pregap (`bin_start_frame` .. `bin_index01_frame`): pregap frames stored in the BIN.
- virtual CUE PREGAP (`pregap`): frames that take disc LBA but are not stored in the BIN.

LBA contract:
1. Without a virtual PREGAP before the first track, the first BIN frame is physical LBA 0. With a
   virtual PREGAP, the first BIN frame can be physical LBA > 0, so do not assume LBA 0 = BIN frame 0.
2. Frames are consumed in BIN order. Pregap frames stored in the BIN (INDEX 00..01) consume LBA.
3. `lba_start` (INDEX 01) is the first user-data frame of the track.
4. A MODE1/2352 or MODE2/2352 sector header MSF must equal LBA + 150 (absolute_msf).
5. The next track's pregap is not part of this track's length_sectors.
6. A CUE PREGAP is a virtual gap: its frames take LBA but are not stored in the BIN.
7. Unsupported CUE forms are rejected explicitly, never converted.

Explicit non-support (FAIL, not guessed): multiple FILE entries, Cooked modes (MODE1/2048, MODE2/2336),
and every FILE type other than BINARY.

Sector checks for data tracks (see `check_sectors`): every user-data frame (INDEX 01 onward) must carry the
RAW sync pattern, and every sync frame (pregap or user data) must have header byte 15 equal to the
declared mode (MODE1/2352 = 0x01, MODE2/2352 = 0x02) and a valid BCD MSF equal to its disc LBA + 150.
Pregap frames without sync are legal.
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
# Header byte 15 of a RAW data frame must equal the value for the mode declared in the CUE
HEADER_MODE_BYTE = {"MODE1/2352": 0x01, "MODE2/2352": 0x02}
FINDING_POSITIONS = 5  # frame positions listed per finding; sync and mode-byte counts are always exact
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


def _decode_msf(header: bytes):
    """BCD MSF header bytes (minute, second, frame) to an absolute frame count (lead-in included), or None.

    None when any nibble is above 9 or a field is out of range. A None result never equals an LBA + 150,
    so an invalid header is reported as a mismatch and 0x1A is never read as the number 20.
    """
    if any((b >> 4) > 9 or (b & 0x0F) > 9 for b in header[:3]):
        return None
    minute, second, frame = (_bcd_to_int(b) for b in header[:3])
    if second >= SECONDS_PER_MINUTE or frame >= FRAMES_PER_SECOND:
        return None
    return (minute * SECONDS_PER_MINUTE + second) * FRAMES_PER_SECOND + frame


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
            tracks.append({"number": number, "type": "data" if kind in DATA_TYPES else "audio", "mode": kind,
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
            "declared_mode": track["mode"],
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

    Every user-data frame (INDEX 01 onward) of a data track must carry the RAW sync pattern. Pregap
    frames (INDEX 00..01 in the BIN) may lack sync, which is legal. Every sync frame, pregap or user data,
    must have header byte 15 equal to its declared mode and a valid BCD MSF equal to its disc LBA + 150.
    Counts are exact; only the first FINDING_POSITIONS BIN frame offsets are listed. Payload bytes are never reported.
    """
    findings, probed, sync_frames = [], 0, 0
    with open(bin_path, "rb") as f:
        for row in rows:
            if row["type"] != "data":
                continue
            index01 = row["bin_index01_frame"]
            expected_mode = HEADER_MODE_BYTE[row["declared_mode"]]
            sync_missing, sync_missing_at = 0, []
            mode_bad, mode_bad_at = 0, []
            msf_bad_at = []
            # Whole stored span of the track, streamed in bounded CHUNK_FRAMES reads.
            for first in range(row["bin_start_frame"], row["bin_end_frame"], CHUNK_FRAMES):
                count = min(CHUNK_FRAMES, row["bin_end_frame"] - first)
                f.seek(first * RAW_BYTES)
                chunk = f.read(count * RAW_BYTES)
                if len(chunk) != count * RAW_BYTES:
                    raise TocError(f"short read at BIN frame {first}: BIN changed during analysis")
                for i in range(count):
                    frame = first + i
                    head = chunk[i * RAW_BYTES:i * RAW_BYTES + 16]  # sync (0..11), MSF (12..14), mode (15)
                    probed += 1
                    if head[:12] != SYNC:
                        # A pregap frame without sync is legal; a user-data frame without sync is corruption
                        if frame >= index01:
                            sync_missing += 1
                            if len(sync_missing_at) < FINDING_POSITIONS:
                                sync_missing_at.append(frame)
                        continue
                    sync_frames += 1
                    if head[15] != expected_mode:
                        mode_bad += 1
                        if len(mode_bad_at) < FINDING_POSITIONS:
                            mode_bad_at.append(frame)
                    disc = row["lba_start"] + (frame - index01)
                    if _decode_msf(head[12:15]) != disc + LEAD_IN_FRAMES and len(msf_bad_at) < FINDING_POSITIONS:
                        msf_bad_at.append(frame)
            number = row["track"]
            if sync_missing:
                findings.append(f"track {number}: {sync_missing} of {row['length_sectors']} user-data frames lack "
                                f"RAW sync (first at BIN frames {sync_missing_at})")
            if mode_bad:
                findings.append(f"track {number}: {mode_bad} frames have header mode byte != "
                                f"0x{expected_mode:02x} for declared {row['declared_mode']} "
                                f"(first at BIN frames {mode_bad_at})")
            if msf_bad_at:
                findings.append(f"track {number}: BIN frames {msf_bad_at} header MSF disagrees with their "
                                f"disc LBA + 150 (invalid BCD counts as a mismatch)")
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
        "evidence": {"measured_from": "provided local CUE/BIN input (host-side parse; origin not asserted)",
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
