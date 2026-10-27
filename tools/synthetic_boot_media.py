#!/usr/bin/env python3
"""Synthetic CD boot media generator (wave26 W06). Original fixture only: no game data, no third-party content.

Layout (single MODE1/2352 data track after one AUDIO track, one FILE, INDEX values are file-relative):
  V1: TRACK 01 AUDIO (16 frames), TRACK 02 MODE1/2352 INDEX 01 at frame 16 (disc LBA 16).
  V2: as V1, but TRACK 02 has INDEX 00 at frame 16 and INDEX 01 at frame 16 + 225 (pregap stored in the BIN).
      225 = the game CUE's INDEX 00 00:00:00 -> INDEX 01 00:03:00 (3 s x 75 frames), docs/cd-boot-loader-spec.md S5.
      docs/cd-boot-loader-spec.md S3/S5 is on main (merged PRs #552 and #554).
  Single FILE is the M4/M5 baseline. Multi-FILE (V2M) is a separate condition and is not generated here.
Every data-track frame (pregap included) carries sync, BCD MSF = LBA + 150 and mode byte 1 (tools/cue_analysis.py).
Data track = 4 frames: header sector at INDEX 01, then 3 filler sectors.
TRACK 01 AUDIO is 16 frames, below the Red Book minimum track length (4 s = 300 frames). This only keeps the host
fixture small; a drive or emulator may reject such a disc, so do not read real-core results from these images as is.

Header (8 bytes at the start of the user data, USER_OFFSET 16 of a raw frame), the DEV loader's PCEX form
(src/cd_load_exec_probe.s: HDR_BYTES 8, BUF+4 entry, BUF+5 length, BUF+6 checksum):
  [0:4] magic b"PCEX", [4] entry offset u8, [5] length u8 (1..255), [6] checksum (sum of code mod 256),
  [7] reserved zero. Code (length bytes) follows the header at [8:].

Mutations (applied to the header sector, or to the read plan): NONE, BAD_MAGIC, BAD_CHECKSUM, BAD_LENGTH,
BAD_ENTRY_OFFSET, NO_TRACK (data track replaced by AUDIO), DATA_TIMEOUT, PREMATURE_STATUS, WRONG_LBA.
Expected outcomes are NOT computed here; see tools/synthetic_boot_oracle.py and tests/host/test_synthetic_boot_media.py.
"""
import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

RAW = 2352
USER_OFFSET = 16
USER_BYTES = 2048
HEADER = 8
MAGIC = b"PCEX"
CODE = bytes([0xEA] * 31 + [0x60])   # 32 bytes, not executed by the host; NOP padding + RTS
AUDIO_FRAMES = 16
DATA_FRAMES = 4
V2_PREGAP_FRAMES = 225               # 00:03:00, INDEX 00 -> INDEX 01 (docs/cd-boot-loader-spec.md S5)
MUTATIONS = ("NONE", "BAD_MAGIC", "BAD_CHECKSUM", "BAD_LENGTH", "BAD_ENTRY_OFFSET", "NO_TRACK",
             "DATA_TIMEOUT", "PREMATURE_STATUS", "WRONG_LBA")
BIN_NAME = "synthetic-boot.bin"
CUE_NAME = "synthetic-boot.cue"


@dataclass(frozen=True)
class Image:
    cue: str
    bin: bytes
    read_plan: dict   # {"lba_offset": int relative to INDEX 01, "abort_in_data": bool}


def _msf(frames: int) -> str:
    return "%02d:%02d:%02d" % (frames // 4500, frames // 75 % 60, frames % 75)


def _filler(tag: bytes, lba: int) -> bytes:
    """Deterministic 2048 bytes (SHA-256 counter stream), so a wrong sector never matches by accident."""
    out = b""
    counter = 0
    while len(out) < USER_BYTES:
        out += hashlib.sha256(tag + lba.to_bytes(4, "little") + counter.to_bytes(4, "little")).digest()
        counter += 1
    return out[:USER_BYTES]


def _bcd(value: int) -> int:
    return (value // 10) << 4 | value % 10


def _raw_data_frame(user: bytes, lba: int) -> bytes:
    assert len(user) == USER_BYTES
    msf = lba + 150
    head = bytes([_bcd(msf // 4500), _bcd(msf // 75 % 60), _bcd(msf % 75), 1])
    return b"\x00" + b"\xff" * 10 + b"\x00" + head + user + bytes(RAW - USER_OFFSET - USER_BYTES)


def _audio_frame(index: int) -> bytes:
    return (hashlib.sha256(b"synthetic-audio" + index.to_bytes(4, "little")).digest() * 74)[:RAW]


def _header_user(entry: int, length: int, checksum: int, code: bytes, lba: int) -> bytes:
    head = MAGIC + bytes([entry, length, checksum, 0])
    body = head + code
    return body + _filler(b"synthetic-data", lba)[len(body):]


def build_case_image(mutation: str, layout: str) -> Image:
    if mutation not in MUTATIONS:
        raise ValueError("unknown mutation: %s" % mutation)
    if layout == "V1":
        pregap = 0
    elif layout == "V2":
        pregap = V2_PREGAP_FRAMES
    else:
        raise ValueError("unknown layout: %s" % layout)

    index01 = AUDIO_FRAMES + pregap           # disc LBA of the data track's INDEX 01 (no virtual pregap)
    total = index01 + DATA_FRAMES

    # Header values. Mutations change exactly one field of the header or the code bytes.
    code = bytearray(CODE)
    entry, length, checksum = 0, len(CODE), sum(CODE) & 0xFF
    if mutation == "BAD_LENGTH":
        length = 0
    if mutation == "BAD_ENTRY_OFFSET":
        entry = length
    lba_header = index01
    user = bytearray(_header_user(entry, length, checksum, bytes(code), lba_header))
    if mutation == "BAD_MAGIC":
        user[0] ^= 0xFF
    if mutation == "BAD_CHECKSUM":
        user[HEADER] ^= 0x01                  # flip the first code byte; the stored checksum no longer matches
    frames = [_audio_frame(i) for i in range(AUDIO_FRAMES)]
    frames += [_raw_data_frame(bytes(USER_BYTES), AUDIO_FRAMES + i) for i in range(pregap)]
    if mutation == "NO_TRACK":
        frames += [_audio_frame(AUDIO_FRAMES + pregap + i) for i in range(DATA_FRAMES)]
    else:
        frames.append(_raw_data_frame(bytes(user), index01))
        frames += [_raw_data_frame(_filler(b"synthetic-data", index01 + i), index01 + i) for i in range(1, DATA_FRAMES)]

    lines = ['FILE "%s" BINARY' % BIN_NAME, "  TRACK 01 AUDIO", "    INDEX 01 00:00:00"]
    kind = "AUDIO" if mutation == "NO_TRACK" else "MODE1/2352"
    lines.append("  TRACK 02 " + kind)
    if layout == "V2":
        lines.append("    INDEX 00 " + _msf(AUDIO_FRAMES))
    lines.append("    INDEX 01 " + _msf(index01))
    cue = "\n".join(lines) + "\n"

    read_plan = {"lba_offset": 0, "abort_in_data": False}
    if mutation == "DATA_TIMEOUT":
        read_plan["abort_in_data"] = True
    if mutation == "PREMATURE_STATUS":
        read_plan["lba_offset"] = total - index01          # first LBA past the end of the medium
    if mutation == "WRONG_LBA":
        read_plan["lba_offset"] = 1                        # off by one: a filler sector, not the header
    return Image(cue=cue, bin=b"".join(frames), read_plan=read_plan)


def write_case(directory: Path, mutation: str, layout: str) -> dict:
    """Write CUE/BIN for one case into `directory` (caller owns the directory) and return a manifest."""
    img = build_case_image(mutation, layout)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / BIN_NAME).write_bytes(img.bin)
    (directory / CUE_NAME).write_text(img.cue, encoding="ascii")
    manifest = {
        "mutation": mutation, "layout": layout, "pregap_frames": V2_PREGAP_FRAMES if layout == "V2" else 0,
        "read_plan": img.read_plan, "bin_bytes": len(img.bin),
        "cue_sha256": hashlib.sha256(img.cue.encode("ascii")).hexdigest(),
        "bin_sha256": hashlib.sha256(img.bin).hexdigest(), "third_party_content": "NONE",
    }
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="ascii")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Write one synthetic boot-media case (no game data).")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--mutation", choices=MUTATIONS, default="NONE")
    parser.add_argument("--layout", choices=("V1", "V2"), default="V1")
    args = parser.parse_args()
    print(json.dumps(write_case(args.out, args.mutation, args.layout), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
