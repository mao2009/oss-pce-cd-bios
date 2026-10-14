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
MODES = {
    "MODE1/2048": (2048, 0, 2048),
    "MODE1/2352": (2352, 16, 2048),
    "MODE2/2336": (2336, 8, 2048),
    "MODE2/2352": (2352, 24, 2048),
    "AUDIO": (2352, 0, 2352),
}
PCE_BOOT_SIGNATURE = b"PC Engine CD-ROM SYSTEM"
MAX_PROBE_FRAMES = 4096  # bounded frame probing so a huge track stays fast


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
                _, number_text, mode = line.split()
                if mode.upper() not in MODES:
                    raise CueError(f"unsupported track mode: {mode!r}")
                files[-1]["tracks"].append({"number": int(number_text), "mode": mode.upper(), "index": {}})
            elif keyword in ("INDEX", "PREGAP", "POSTGAP"):
                if not files or not files[-1]["tracks"]:
                    raise CueError(f"{keyword} before any TRACK")
                _, index_text, timestamp = line.split()
                track = files[-1]["tracks"][-1]
                if keyword == "INDEX":
                    track["index"][int(index_text)] = _parse_msf(timestamp)
                else:
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


def _probe_track(data: bytes, sector_bytes: int, track_lba: int, mode: str) -> dict:
    """Validate raw frames of one track: sync, mode byte and MSF == LBA + 150 for MODE1/2352."""
    frames = len(data) // sector_bytes
    probe = min(frames, MAX_PROBE_FRAMES)
    sync_frames, modes, msf_bad = 0, set(), []
    first_sync = first_user_data = None
    user_offset, user_bytes = MODES[mode][1], MODES[mode][2]
    for frame in range(probe):
        start = frame * sector_bytes
        if data[start:start + 12] != SYNC:
            continue
        sync_frames += 1
        if first_sync is None:
            first_sync = frame
        modes.add(data[start + 15])
        if mode == "MODE1/2352" and _frame_lba(data[start + 12:start + 15]) != track_lba + frame + 150:
            if len(msf_bad) < 5:
                msf_bad.append(frame)
        if first_user_data is None and any(data[start + user_offset:start + user_offset + user_bytes]):
            first_user_data = frame
    return {"frames": frames, "probed_frames": probe, "sync_frames": sync_frames,
            "first_sync_frame": first_sync, "first_user_data_frame": first_user_data,
            "mode_bytes": sorted(modes), "msf_mismatch_frames": msf_bad}


def analyze(cue_path: Path, boot_signature: bytes = PCE_BOOT_SIGNATURE) -> dict:
    """Full read-only analysis of `cue_path`; returns a JSON-serializable report and never payload bytes."""
    cue_path = Path(cue_path)
    sheet = parse_cue(cue_path)
    folder = cue_path.parent
    tracks, findings, next_lba = [], [], 0
    for file in sheet["files"]:
        if not file["tracks"]:
            raise CueError(f"FILE {file['name']!r} has no tracks")
        sizes = {MODES[t["mode"]][0] for t in file["tracks"]}
        if len(sizes) != 1:
            raise CueError(f"FILE {file['name']!r} mixes sector sizes {sorted(sizes)}; not supported")
        bin_path = folder / file["name"]
        if not bin_path.is_file():
            raise CueError(f"referenced BIN does not exist: {bin_path}")
        data = bin_path.read_bytes()
        sector_bytes = sizes.pop()
        if len(data) % sector_bytes:
            raise CueError(f"{file['name']}: size {len(data)} is not a multiple of {sector_bytes}")
        file_frames = len(data) // sector_bytes
        starts = [min(t["index"].values()) if t["index"] else 0 for t in file["tracks"]]
        if starts != sorted(starts):
            raise CueError(f"{file['name']}: INDEX offsets are not increasing")
        for position, track in enumerate(file["tracks"]):
            declared = track["index"].get(1, starts[position])
            length = (starts[position + 1] if position + 1 < len(starts) else file_frames) - starts[position]
            if length <= 0:
                raise CueError(f"{file['name']}: track {track['number']} has non-positive length")
            span = data[starts[position] * sector_bytes:(starts[position] + length) * sector_bytes]
            is_audio = track["mode"] == "AUDIO"
            entry = {"number": track["number"], "mode": track["mode"], "file": file["name"],
                     "audio": is_audio, "sector_bytes": sector_bytes, "length_frames": length,
                     "length_seconds": round(length / 75, 3), "start_lba": next_lba,
                     "index": {str(k): v for k, v in sorted(track["index"].items())},
                     "declared_data_lba": next_lba + declared,
                     "sha256": hashlib.sha256(span).hexdigest()}
            if is_audio:
                if any(span[i:i + 12] == SYNC for i in range(0, min(len(span), 2352 * 64))):
                    findings.append(f"track {track['number']}: AUDIO track unexpectedly contains MODE sync frames")
            else:
                entry["sector"] = _probe_track(span, sector_bytes, next_lba, track["mode"])
                sector = entry["sector"]
                if declared and sector["first_sync_frame"] is not None and sector["first_sync_frame"] < declared:
                    if sector["first_sync_frame"] < max(0, declared - 150):
                        findings.append(f"track {track['number']}: sync data appears at frame {sector['first_sync_frame']}, "
                                        f"before the declared INDEX 01 at frame {declared}")
                if not sector["sync_frames"]:
                    findings.append(f"track {track['number']}: no MODE1/2352 sync frame found in the first "
                                    f"{sector['probed_frames']} frames")
                if sector["msf_mismatch_frames"]:
                    findings.append(f"track {track['number']}: MSF headers disagree with start LBA "
                                    f"{next_lba} at frames {sector['msf_mismatch_frames']}")
            tracks.append(entry)
            next_lba += length
    signatures = []
    for track in tracks:
        if track["audio"] or "sector" not in track or track["mode"] != "MODE1/2352":
            continue
        body = (folder / track["file"]).read_bytes()
        base = min(track["index"].values()) * track["sector_bytes"]
        step = min(track["length_frames"], MAX_PROBE_FRAMES)
        for frame in range(step):
            offset = base + frame * track["sector_bytes"] + 16
            at = body[offset:offset + 2048].find(boot_signature)
            if at >= 0:
                signatures.append({"track": track["number"], "lba": track["start_lba"] + frame,
                                   "user_offset": at, "signature": boot_signature.decode("ascii", "replace")})
                break
    data_tracks = [t["number"] for t in tracks if not t["audio"]]
    return {"cue": cue_path.name, "cue_sha256": hashlib.sha256(cue_path.read_bytes()).hexdigest(),
            "catalog": sheet["catalog"], "total_frames": next_lba, "total_seconds": round(next_lba / 75, 3),
            "data_tracks": data_tracks, "audio_tracks": [t["number"] for t in tracks if t["audio"]],
            "tracks": tracks, "boot_signatures": signatures, "findings": findings,
            "structurally_valid": not findings and bool(data_tracks)}


def user_sector(cue_path: Path, report: dict, lba: int) -> bytes:
    """Return the 2048-byte user data at absolute disc `lba` (MODE1/2352 data tracks only)."""
    for track in report["tracks"]:
        if track["audio"] or track["start_lba"] > lba >= track["start_lba"] + track["length_frames"]:
            continue
        if track["mode"] != "MODE1/2352":
            raise CueError(f"track {track['number']} mode {track['mode']} has no 2048-byte user area")
        if not track["start_lba"] <= lba < track["start_lba"] + track["length_frames"]:
            continue
        data = (Path(cue_path).parent / track["file"]).read_bytes()
        offset = (min(track["index"].values()) + lba - track["start_lba"]) * 2352
        return data[offset + 16:offset + 16 + 2048]
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
    for track in report["tracks"]:
        extra = ""
        if not track["audio"]:
            sector = track["sector"]
            extra = (f" sync_frames={sector['sync_frames']} first_sync={sector['first_sync_frame']} "
                     f"first_user_data={sector['first_user_data_frame']} modes={sector['mode_bytes']}")
        print(f"  track {track['number']:02d} {track['mode']:<11} start_lba={track['start_lba']:>6} "
              f"frames={track['length_frames']:>6}{extra}")
    for signature in report["boot_signatures"]:
        print(f"  boot signature {signature['signature']!r} at track {signature['track']} "
              f"LBA {signature['lba']} user+0x{signature['user_offset']:X}")
    for finding in report["findings"]:
        print(f"  FINDING: {finding}")
    if not report["structurally_valid"]:
        raise SystemExit("cue_analysis: FAIL (see findings)")
    print("cue_analysis: PASS structural CUE/BIN validation (no payload emitted)")


if __name__ == "__main__":
    try:
        main()
    except (OSError, CueError) as error:
        raise SystemExit(f"cue_analysis: FAIL/BLOCKED: {error}") from error
