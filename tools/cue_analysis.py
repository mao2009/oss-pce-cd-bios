#!/usr/bin/env python3
"""Read-only CUE/BIN inspector used to diagnose a locally owned disc image (Issue #5).

It parses the CUE sheet, verifies that every referenced BIN exists and has a whole
number of sectors, classifies data vs CD-DA tracks, maps each track to an absolute
disc LBA, and probes MODE1/2352 frames for their sync pattern, mode byte and BCD
MSF/LBA header consistency.

It never executes the disc, never emulates the CD controller and never copies sector
payloads into the report: the JSON output carries layout, validation results and
hashes only. That keeps the tool safe to run against a retail disc whose image must
not be committed, uploaded or attached to a PR. The interface is reusable by
tools/cdread_verify.py (see `user_sector`).
"""
import argparse
import hashlib
import json
from pathlib import Path

SYNC = b"\x00" + b"\xff" * 10 + b"\x00"
# CD sector layouts we understand: mode -> (raw bytes, user-data offset, user bytes).
# MODE2 Form 1/Form 2 are not supported for user data extraction; only MODE1/2352.
MODES = {
    "MODE1/2048": (2048, 0, 2048),
    "MODE1/2352": (2352, 16, 2048),
    "MODE2/2336": (2336, 8, 2048),
    "MODE2/2352": (2352, 24, 2048),
    "AUDIO": (2352, 0, 2352),
}
# Modes that have RAW sync pattern (00 FF...FF 00) and MSF header at bytes 12-14
RAW_SYNC_MODES = {"MODE1/2352", "MODE2/2352", "AUDIO"}
# Raw data modes whose header MSF must equal the physical disc LBA + 150 (AUDIO has no header)
MSF_MODES = {"MODE1/2352", "MODE2/2352"}
# Modes that support 2048-byte user data extraction via user_sector()
USER_DATA_MODES = {"MODE1/2352"}
PCE_BOOT_SIGNATURE = b"PC Engine CD-ROM SYSTEM"
MAX_PROBE_FRAMES = 4096  # bounded frame probing so a huge track stays fast
# Streaming chunk sizes: the analysis holds only these bounded buffers, never a whole track.
FRAME_CHUNK = 64  # frames per read while probing or scanning (~150 KiB for 2352-byte frames)
HASH_CHUNK = 1 << 20  # bytes per read while hashing a track
AUDIO_SCAN_FRAMES = 64  # AUDIO tracks are only scanned for MODE sync in their first frames


class CueError(ValueError):
    """Malformed CUE/BIN input; the caller must fail closed."""


def _parse_msf(token: str) -> int:
    parts = token.split(":")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise CueError(f"bad MSF timestamp: {token!r}")
    minute, second, frame = (int(p) for p in parts)
    if second > 59 or frame > 74:
        raise CueError(f"out-of-range MSF timestamp: {token!r}")
    return (minute * 60 + second) * 75 + frame


def parse_cue(path: Path) -> dict:
    """Parse CUE keywords into {'catalog', 'files': [...]}, raising CueError on malformed input."""
    text = path.read_text(encoding="ascii", errors="replace")
    catalog, files = None, []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.upper().startswith("REM"):
            continue
        keyword = line.split(None, 1)[0].upper()
        try:
            if keyword == "CATALOG":
                catalog = line.split(None, 1)[1].strip()
            elif keyword == "FILE":
                if '"' not in line or line.count('"') < 2:
                    raise CueError("FILE requires a quoted name")
                name = line.split('"')[1]
                kind = line.split('"', 2)[2].strip().upper()
                if kind not in ("BINARY", "MOTOROLA", "AIFF", "WAVE", "MP3"):
                    raise CueError(f"unsupported FILE type: {kind!r}")
                files.append({"name": name, "type": kind, "tracks": []})
            elif keyword == "TRACK":
                if not files:
                    raise CueError("TRACK before any FILE")
                parts = line.split()
                if len(parts) != 3:
                    raise CueError(f"TRACK requires number and mode: {line!r}")
                _, number_text, mode = parts
                if mode.upper() not in MODES:
                    raise CueError(f"unsupported track mode: {mode!r}")
                files[-1]["tracks"].append({"number": int(number_text), "mode": mode.upper(), "index": {}})
            elif keyword == "INDEX":
                if not files or not files[-1]["tracks"]:
                    raise CueError("INDEX before any TRACK")
                parts = line.split()
                if len(parts) != 3:
                    raise CueError(f"INDEX requires index and timestamp: {line!r}")
                _, index_text, timestamp = parts
                track = files[-1]["tracks"][-1]
                track["index"][int(index_text)] = _parse_msf(timestamp)
            elif keyword in ("PREGAP", "POSTGAP"):
                if not files or not files[-1]["tracks"]:
                    raise CueError(f"{keyword} before any TRACK")
                parts = line.split()
                if len(parts) != 2:
                    raise CueError(f"{keyword} requires timestamp: {line!r}")
                _, timestamp = parts
                track = files[-1]["tracks"][-1]
                track[keyword.lower()] = _parse_msf(timestamp)
            elif keyword in ("FLAGS", "ISRC", "CDTEXTFILE", "PERFORMER", "TITLE", "SONGWRITER"):
                continue
            else:
                raise CueError(f"unsupported CUE keyword: {keyword!r}")
        except (IndexError, ValueError) as error:
            if isinstance(error, CueError):
                raise
            raise CueError(f"line {number}: {raw!r}") from error
    if not files or not any(f["tracks"] for f in files):
        raise CueError("CUE has no tracks")
    return {"catalog": catalog, "files": files}


def _bcd(value: int) -> int:
    return ((value // 10) << 4) | value % 10


def _from_bcd(byte: int) -> int:
    return (byte >> 4) * 10 + (byte & 0x0F)


def _frame_lba(header: bytes) -> int:
    """BCD MSF (12..14) -> absolute LBA, i.e. the sector address without the 150-frame lead-in."""
    return (_from_bcd(header[0]) * 60 + _from_bcd(header[1])) * 75 + _from_bcd(header[2])


def _read_exact(f, size: int) -> bytes:
    """Read exactly `size` bytes; a short read means truncation or a BIN changed during analysis."""
    data = f.read(size)
    if len(data) != size:
        raise CueError(f"short read: wanted {size} bytes, got {len(data)} (BIN truncated or changed)")
    return data


def _iter_frames(f, start: int, count: int, sector_bytes: int):
    """Yield (frame_index, raw_frame) for `count` frames from byte `start`, reading FRAME_CHUNK at a time."""
    f.seek(start)
    for first in range(0, count, FRAME_CHUNK):
        n = min(FRAME_CHUNK, count - first)
        chunk = _read_exact(f, n * sector_bytes)
        for i in range(n):
            yield first + i, chunk[i * sector_bytes:(i + 1) * sector_bytes]


def _sha256_range(f, start: int, length: int) -> str:
    """Streaming SHA-256 of `length` bytes from `start`, read HASH_CHUNK at a time."""
    h = hashlib.sha256()
    f.seek(start)
    remaining = length
    while remaining:
        chunk = _read_exact(f, min(HASH_CHUNK, remaining))
        h.update(chunk)
        remaining -= len(chunk)
    return h.hexdigest()


def _sha256_file(path: Path) -> str:
    """Streaming SHA-256 to avoid loading large files entirely into memory."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(HASH_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _check_pregap(f, start: int, frames: int, sector_bytes: int, pregap_lba: int) -> tuple:
    """Count sync sectors in a pregap and report those whose header MSF disagrees with their disc LBA.

    A pregap may legitimately hold sync sectors (for example a data pregap written as MODE1 frames),
    so a sync frame is a finding only when its header does not match its position. Non-sync bytes are not checked.
    """
    sync_frames, bad = 0, []
    for frame, data in _iter_frames(f, start, frames, sector_bytes):
        if data[:len(SYNC)] != SYNC:
            continue
        sync_frames += 1
        if _frame_lba(data[12:15]) != pregap_lba + frame + 150 and len(bad) < 5:
            bad.append(frame)
    return sync_frames, bad


def _probe_track(f, start: int, frames: int, sector_bytes: int, track_lba: int, mode: str) -> dict:
    """Validate up to MAX_PROBE_FRAMES frames from `start`: sync, mode byte and MSF == LBA + 150 for MSF modes.

    `track_lba` is the physical disc LBA of the first probed frame.
    """
    probe = min(frames, MAX_PROBE_FRAMES)
    sync_frames, modes, msf_bad = 0, set(), []
    first_sync = first_user_data = None
    user_offset, user_bytes = MODES[mode][1], MODES[mode][2]
    has_raw_sync = mode in RAW_SYNC_MODES
    for frame, data in _iter_frames(f, start, probe, sector_bytes):
        if has_raw_sync:
            if data[:12] != SYNC:
                continue
            sync_frames += 1
            if first_sync is None:
                first_sync = frame
            modes.add(data[15])
            if mode in MSF_MODES and _frame_lba(data[12:15]) != track_lba + frame + 150:
                if len(msf_bad) < 5:
                    msf_bad.append(frame)
        if first_user_data is None and any(data[user_offset:user_offset + user_bytes]):
            first_user_data = frame
    result = {"frames": frames, "probed_frames": probe, "first_user_data_frame": first_user_data}
    if has_raw_sync:
        result.update({"sync_frames": sync_frames,
                       "first_sync_frame": first_sync,
                       "mode_bytes": sorted(modes),
                       "msf_mismatch_frames": msf_bad})
    return result


def analyze(cue_path: Path, boot_signature: bytes = PCE_BOOT_SIGNATURE) -> dict:
    """Full read-only analysis of `cue_path`; returns a JSON-serializable report and never payload bytes.

    LBA contract (docs/cd-read-diagnostic.md): the first BIN frame is physical disc LBA 0, every frame
    advances the disc position including pregap frames, and a sector header MSF must equal LBA + 150.
    `pregap_start_lba` is the track's first frame (INDEX 00, or the CUE PREGAP start) and `start_lba`
    (= `declared_data_lba`) is INDEX 01. A CUE PREGAP adds disc frames that are not stored in the BIN.
    """
    cue_path = Path(cue_path)
    sheet = parse_cue(cue_path)
    folder = cue_path.parent
    tracks, findings, disc_cursor = [], [], 0
    for file in sheet["files"]:
        if not file["tracks"]:
            raise CueError(f"FILE {file['name']!r} has no tracks")
        sizes = {MODES[t["mode"]][0] for t in file["tracks"]}
        if len(sizes) != 1:
            raise CueError(f"FILE {file['name']!r} mixes sector sizes {sorted(sizes)}; not supported")
        bin_path = folder / file["name"]
        if not bin_path.is_file():
            raise CueError(f"referenced BIN does not exist: {bin_path}")
        sector_bytes = sizes.pop()
        # Use file size check without reading entire file
        file_size = bin_path.stat().st_size
        if file_size % sector_bytes:
            raise CueError(f"{file['name']}: size {file_size} is not a multiple of {sector_bytes}")
        file_frames = file_size // sector_bytes
        # First BIN frame of each track: its INDEX 00, or INDEX 01 when there is no INDEX 00
        starts = [min(t["index"].values()) if t["index"] else 0 for t in file["tracks"]]
        if starts != sorted(starts):
            raise CueError(f"{file['name']}: INDEX offsets are not increasing")
        for position, track in enumerate(file["tracks"]):
            if "pregap" in track and 0 in track["index"]:
                raise CueError(f"track {track['number']}: PREGAP and INDEX 00 are both given; ambiguous")
            bin_start_frame = starts[position]
            index01_frame = track["index"].get(1, bin_start_frame)
            bin_pregap = index01_frame - bin_start_frame  # pregap frames stored in the BIN (INDEX 00..01)
            pregap_frames = bin_pregap + track.get("pregap", 0)  # CUE PREGAP frames are not stored in the BIN
            bin_end_frame = starts[position + 1] if position + 1 < len(starts) else file_frames
            user_data_frames = bin_end_frame - index01_frame
            if user_data_frames <= 0:
                raise CueError(f"{file['name']}: track {track['number']} has non-positive user data length")
            pregap_lba = disc_cursor
            start_lba = pregap_lba + pregap_frames
            # Stream the track span: hash in HASH_CHUNK reads, probe/scan in FRAME_CHUNK reads.
            # Nothing here holds a whole track in memory, so peak usage does not grow with the BIN.
            track_offset = bin_start_frame * sector_bytes
            track_size = (bin_end_frame - bin_start_frame) * sector_bytes
            is_audio = track["mode"] == "AUDIO"
            with bin_path.open("rb") as f:
                entry = {"number": track["number"], "mode": track["mode"], "file": file["name"],
                         "audio": is_audio, "sector_bytes": sector_bytes, "length_frames": user_data_frames,
                         "length_seconds": round(user_data_frames / 75, 3),
                         "pregap_start_lba": pregap_lba, "start_lba": start_lba,
                         "declared_data_lba": start_lba,
                         "index": {str(k): v for k, v in sorted(track["index"].items())},
                         "index01_frame": index01_frame,
                         "pregap_frames": pregap_frames,
                         "sha256": _sha256_range(f, track_offset, track_size)}
                if "pregap" in track:
                    entry["pregap"] = track["pregap"]
                if "postgap" in track:
                    entry["postgap"] = track["postgap"]
                if is_audio:
                    # Bounded read: only the first AUDIO_SCAN_FRAMES frames (plus a 11-byte overlap for the sync match)
                    scan_len = min(track_size, AUDIO_SCAN_FRAMES * 2352)
                    f.seek(track_offset)
                    head = _read_exact(f, min(track_size, scan_len + len(SYNC) - 1))
                    if any(head[i:i + len(SYNC)] == SYNC for i in range(scan_len)):
                        findings.append(f"track {track['number']}: AUDIO track unexpectedly contains MODE sync frames")
                else:
                    # Probe only the user data portion (from INDEX 01 onwards)
                    entry["sector"] = _probe_track(f, index01_frame * sector_bytes, user_data_frames,
                                                   sector_bytes, start_lba, track["mode"])
                    sector = entry["sector"]
                    # Only validate sync/MSF for RAW modes
                    if track["mode"] in RAW_SYNC_MODES:
                        if bin_pregap:
                            # Sync sectors inside a legal pregap are expected; only a header/position mismatch is corruption
                            pregap_sync, pregap_bad = _check_pregap(f, track_offset, bin_pregap, sector_bytes, pregap_lba)
                            entry["pregap_sync_frames"] = pregap_sync
                            if pregap_bad:
                                findings.append(f"track {track['number']}: pregap sync sectors disagree with their disc "
                                                f"LBA (pregap starts at {pregap_lba}) at frames {pregap_bad}")
                        if not sector["sync_frames"]:
                            findings.append(f"track {track['number']}: no RAW sync frame found in the first "
                                            f"{sector['probed_frames']} user-data frames")
                        if sector["msf_mismatch_frames"]:
                            findings.append(f"track {track['number']}: MSF headers disagree with start LBA "
                                            f"{start_lba} at frames {sector['msf_mismatch_frames']}")
            tracks.append(entry)
            disc_cursor = start_lba + user_data_frames
    signatures = []
    for track in tracks:
        if track["audio"] or "sector" not in track or track["mode"] != "MODE1/2352":
            continue
        bin_path = folder / track["file"]
        # Search from INDEX 01 onwards
        base = track["index01_frame"] * track["sector_bytes"]
        step = min(track["length_frames"], MAX_PROBE_FRAMES)
        with bin_path.open("rb") as f:
            for frame in range(step):
                offset = base + frame * track["sector_bytes"] + 16
                f.seek(offset)
                user_data = _read_exact(f, 2048)
                at = user_data.find(boot_signature)
                if at >= 0:
                    signatures.append({"track": track["number"], "lba": track["start_lba"] + frame,
                                       "user_offset": at, "signature": boot_signature.decode("ascii", "replace")})
                    break
    data_tracks = [t["number"] for t in tracks if not t["audio"]]
    return {"cue": cue_path.name, "cue_sha256": _sha256_file(cue_path),
            "catalog": sheet["catalog"], "total_frames": disc_cursor, "total_seconds": round(disc_cursor / 75, 3),
            "data_tracks": data_tracks, "audio_tracks": [t["number"] for t in tracks if t["audio"]],
            "tracks": tracks, "boot_signatures": signatures, "findings": findings,
            "structurally_valid": not findings and bool(data_tracks)}


def user_sector(cue_path: Path, report: dict, lba: int) -> bytes:
    """Return the 2048-byte user data at physical disc `lba` (MODE1/2352 data tracks only)."""
    if lba < 0:
        raise CueError(f"LBA must be non-negative, got {lba}")
    for track in report["tracks"]:
        if track["audio"]:
            continue
        if track["pregap_start_lba"] <= lba < track["start_lba"]:
            raise CueError(f"LBA {lba} is in the pregap of track {track['number']}; pregap is not user data")
        # First check if LBA is within this track's user data range
        if not track["start_lba"] <= lba < track["start_lba"] + track["length_frames"]:
            continue
        # Now check if this track supports user data extraction
        if track["mode"] not in USER_DATA_MODES:
            raise CueError(f"track {track['number']} mode {track['mode']} has no 2048-byte user area (supported: {sorted(USER_DATA_MODES)})")
        bin_path = Path(cue_path).parent / track["file"]
        index01_frame = track["index01_frame"]
        user_offset = MODES[track["mode"]][1]
        frame_in_track = lba - track["start_lba"]
        bin_frame = index01_frame + frame_in_track
        offset = bin_frame * track["sector_bytes"] + user_offset
        with bin_path.open("rb") as f:
            f.seek(offset)
            data = f.read(2048)
        if len(data) != 2048:
            raise CueError(f"track {track['number']}: truncated user data at LBA {lba} (offset {offset})")
        return data
    raise CueError(f"no data track covers absolute LBA {lba}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cue", type=Path)
    parser.add_argument("--json", type=Path, help="also write the report to this path")
    args = parser.parse_args()
    report = analyze(args.cue)
    if args.json:
        args.json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"CUE {report['cue']}: {len(report['tracks'])} tracks "
          f"({len(report['data_tracks'])} data, {len(report['audio_tracks'])} audio), "
          f"{report['total_frames']} frames")
    unverified = []
    for track in report["tracks"]:
        extra = ""
        if not track["audio"]:
            sector = track["sector"]
            if track["mode"] in RAW_SYNC_MODES:
                extra = (f" sync_frames={sector['sync_frames']} first_sync={sector['first_sync_frame']} "
                         f"first_user_data={sector['first_user_data_frame']} modes={sector['mode_bytes']}")
            else:
                # Cooked frames carry no RAW sync/MSF, so only the user-data probe is available
                extra = f" first_user_data={sector['first_user_data_frame']} sync/MSF=not verified (cooked)"
                unverified.append(track["number"])
        print(f"  track {track['number']:02d} {track['mode']:<11} start_lba={track['start_lba']:>6} "
              f"frames={track['length_frames']:>6}{extra}")
        if not track["audio"] and track["mode"] not in USER_DATA_MODES:
            print(f"    note: user_sector() supports only {sorted(USER_DATA_MODES)}; {track['mode']} user data is not extractable")
    for signature in report["boot_signatures"]:
        print(f"  boot signature {signature['signature']!r} at track {signature['track']} "
              f"LBA {signature['lba']} user+0x{signature['user_offset']:X}")
    for finding in report["findings"]:
        print(f"  FINDING: {finding}")
    if not report["structurally_valid"]:
        raise SystemExit("cue_analysis: FAIL (see findings)")
    if unverified:
        print(f"cue_analysis: PASS layout validation only (no payload emitted); "
              f"sync/MSF not verified for cooked track(s) {unverified}")
    else:
        print("cue_analysis: PASS structural CUE/BIN validation (no payload emitted)")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as error:  # CueError is a ValueError; keep every failure in one format
        raise SystemExit(f"cue_analysis: FAIL/BLOCKED: {error}") from error
