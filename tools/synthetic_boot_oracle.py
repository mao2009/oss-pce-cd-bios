#!/usr/bin/env python3
"""Host oracle for the synthetic boot media (wave26 W06). Independent of tools/synthetic_boot_media.py.

It parses the CUE text and the raw BIN bytes itself and applies the DEV loader's header contract
(src/cd_load_exec_probe.s load_check: magic, then length 1..255, entry < length, checksum; 8-byte PCEX header).
It does not import the generator, so a generator bug cannot make the oracle agree with it.
Outcome names follow docs/cd-load-exec-probe.md. (docs/cd-boot-loader-spec.md does not exist on this branch.)

judge(cue_text, bin_bytes, read_plan) -> "OK" | "NO_TRACK" | "DATA_TIMEOUT" | "PREMATURE_STATUS"
                                         | "BAD_MAGIC" | "BAD_LENGTH" | "BAD_ENTRY_OFFSET" | "BAD_CHECKSUM"
                                         | fail-closed media/input statuses (never OK):
                                           "NO_FILE", "UNSUPPORTED_MULTI_FILE", "BAD_CUE", "BAD_BIN_SIZE",
                                           "BAD_READ_PLAN", "BAD_SECTOR_HEADER"
read_plan: {"lba_offset": int, "abort_in_data": bool}; the read starts at the data track's INDEX 01 + lba_offset.
Single FILE only (INDEX values are then absolute LBAs, no virtual PREGAP). A multi-FILE CUE (V2M) returns
"UNSUPPORTED_MULTI_FILE": it is not evaluated and never reported as OK.
Every frame of the data track (pregap included) must carry sync, BCD MSF = LBA + 150 and mode byte 1,
otherwise "BAD_SECTOR_HEADER" (same rule as tools/cue_analysis.py).
"""
import re

RAW = 2352
USER_OFFSET = 16
USER_BYTES = 2048
HEADER_BYTES = 8
MAGIC = b"PCEX"
DATA_MODE = "MODE1/2352"
SYNC = b"\x00" + b"\xff" * 10 + b"\x00"
_MSF = re.compile(r"^(\d{2}):(\d{2}):(\d{2})$")


class _Reject(Exception):
    def __init__(self, status: str):
        super().__init__(status)
        self.status = status


def _frames(msf: str) -> int:
    m = _MSF.match(msf)
    if not m:
        raise _Reject("BAD_CUE")
    minutes, seconds, frames = (int(part) for part in m.groups())
    if seconds >= 60 or frames >= 75:
        raise _Reject("BAD_CUE")
    return (minutes * 60 + seconds) * 75 + frames


def _parse(cue_text: str) -> list:
    """Return [{"type", "index": {0|1: frame}}] for a single-FILE BINARY CUE, or raise _Reject."""
    files = [raw.split() for raw in cue_text.splitlines() if raw.split()[:1] and raw.split()[0].upper() == "FILE"]
    if not files:
        raise _Reject("NO_FILE")
    if len(files) > 1:
        raise _Reject("UNSUPPORTED_MULTI_FILE")
    if files[0][-1].upper() != "BINARY":
        raise _Reject("BAD_CUE")
    tracks = []
    for raw in cue_text.splitlines():
        words = raw.split()
        keyword = words[0].upper() if words else ""
        if keyword == "TRACK":
            if len(words) != 3 or not words[1].isdigit():
                raise _Reject("BAD_CUE")
            tracks.append({"type": words[2].upper(), "index": {}})
        elif keyword == "INDEX":
            if not tracks or len(words) != 3 or words[1] not in ("00", "01") or int(words[1]) in tracks[-1]["index"]:
                raise _Reject("BAD_CUE")
            tracks[-1]["index"][int(words[1])] = _frames(words[2])
        elif keyword == "PREGAP":
            raise _Reject("BAD_CUE")   # virtual pregap would break "INDEX value == LBA"
    starts = []
    for track in tracks:
        if 1 not in track["index"] or track["index"].get(0, 0) > track["index"][1]:
            raise _Reject("BAD_CUE")
        starts.append(track["index"].get(0, track["index"][1]))
    if starts != sorted(starts) or (starts and starts[0] != 0):
        raise _Reject("BAD_CUE")
    for track, start in zip(tracks, starts):
        track["start"] = start
    return tracks


def _bcd(byte: int):
    hi, lo = byte >> 4, byte & 0x0F
    return None if hi > 9 or lo > 9 else hi * 10 + lo


def _check_sector_headers(bin_bytes: bytes, first: int, end: int) -> None:
    for lba in range(first, end):
        frame = bin_bytes[lba * RAW:(lba + 1) * RAW]
        mm, ss, ff = (_bcd(b) for b in frame[12:15])
        if frame[:12] != SYNC or frame[15] != 1 or None in (mm, ss, ff) or (mm * 60 + ss) * 75 + ff != lba + 150:
            raise _Reject("BAD_SECTOR_HEADER")


def _judge(cue_text: str, bin_bytes: bytes, read_plan) -> str:
    tracks = _parse(cue_text)
    if len(bin_bytes) % RAW:
        raise _Reject("BAD_BIN_SIZE")
    total = len(bin_bytes) // RAW
    if not isinstance(read_plan, dict):
        raise _Reject("BAD_READ_PLAN")
    offset, abort = read_plan.get("lba_offset"), read_plan.get("abort_in_data", False)
    if type(offset) is not int or type(abort) is not bool:
        raise _Reject("BAD_READ_PLAN")
    position = next((n for n, track in enumerate(tracks) if track["type"] == DATA_MODE), None)
    if position is None:
        return "NO_TRACK"
    data = tracks[position]
    end = tracks[position + 1]["start"] if position + 1 < len(tracks) else total
    if not data["index"][1] < end <= total:
        raise _Reject("BAD_CUE")
    _check_sector_headers(bin_bytes, data["start"], end)
    if abort:
        return "DATA_TIMEOUT"
    lba = data["index"][1] + offset
    if not 0 <= lba < total:
        return "PREMATURE_STATUS"
    start = lba * RAW + USER_OFFSET
    user = bin_bytes[start:start + USER_BYTES]
    if user[:len(MAGIC)] != MAGIC:
        return "BAD_MAGIC"
    entry, length, checksum = user[4], user[5], user[6]   # single bytes in the DEV loader's PCEX header
    if length == 0:
        return "BAD_LENGTH"
    if entry >= length:
        return "BAD_ENTRY_OFFSET"
    if sum(user[HEADER_BYTES:HEADER_BYTES + length]) & 0xFF != checksum:
        return "BAD_CHECKSUM"
    return "OK"


def judge(cue_text: str, bin_bytes: bytes, read_plan) -> str:
    try:
        return _judge(cue_text, bin_bytes, read_plan)
    except _Reject as reject:
        return reject.status


def parse_tracks(cue_text: str) -> list:
    """[{"type", "index01"}] for a valid single-FILE CUE (used by the tests to locate INDEX 01)."""
    return [{"type": t["type"], "index01": t["index"][1]} for t in _parse(cue_text)]
