#!/usr/bin/env python3
"""Host oracle for the synthetic boot media (wave26 W06). Independent of tools/synthetic_boot_media.py.

It parses the CUE text and the raw BIN bytes itself and applies the DEV loader's header contract
(src/cd_load_exec_probe.s load_check: magic, then length 1..255, entry < length, checksum; 8-byte PCEX header).
It does not import the generator, so a generator bug cannot make the oracle agree with it.
Outcome names follow docs/cd-load-exec-probe.md. (docs/cd-boot-loader-spec.md does not exist on this branch.)

judge(cue_text, bin_bytes, read_plan) -> "OK" | "NO_TRACK" | "DATA_TIMEOUT" | "PREMATURE_STATUS"
                                         | "BAD_MAGIC" | "BAD_LENGTH" | "BAD_ENTRY_OFFSET" | "BAD_CHECKSUM"
read_plan: {"lba_offset": int, "abort_in_data": bool}; the read starts at the data track's INDEX 01 + lba_offset.
"""
RAW = 2352
USER_OFFSET = 16
USER_BYTES = 2048
HEADER_BYTES = 8
MAGIC = b"PCEX"
DATA_MODE = "MODE1/2352"


def _frames(msf: str) -> int:
    minutes, seconds, frames = (int(part) for part in msf.split(":"))
    return (minutes * 60 + seconds) * 75 + frames


def parse_tracks(cue_text: str) -> list:
    tracks = []
    for raw in cue_text.splitlines():
        words = raw.split()
        if len(words) >= 3 and words[0] == "TRACK":
            tracks.append({"type": words[2], "index01": None})
        elif len(words) == 3 and words[0] == "INDEX" and words[1] == "01" and tracks:
            tracks[-1]["index01"] = _frames(words[2])
    return tracks


def judge(cue_text: str, bin_bytes: bytes, read_plan: dict) -> str:
    data = [track for track in parse_tracks(cue_text) if track["type"] == DATA_MODE]
    if not data or data[0]["index01"] is None:
        return "NO_TRACK"
    if read_plan.get("abort_in_data"):
        return "DATA_TIMEOUT"
    lba = data[0]["index01"] + read_plan["lba_offset"]
    if not 0 <= lba < len(bin_bytes) // RAW:
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
