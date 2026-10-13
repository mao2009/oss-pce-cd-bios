#!/usr/bin/env python3
"""Generate the original, reproducible CD-ROM fixture for the Issue #5 read diagnostic.

Single MODE1/2352 data track, CUE/BIN. Every 2048-byte user-data block is
unique (sector number + SHA-256 counter stream), so a wrong, stale, shifted or
partially copied sector cannot compare equal. No third-party data.

Raw-sector EDC/ECC fields are ZERO (not computed): enough for emulators that
only return user data, NOT a burnable/valid Red Book image.
"""
import argparse
import hashlib
import json
from pathlib import Path

USER_BYTES = 2048
RAW_BYTES = 2352
MAGIC = b"OSSPCE-CD-TEST-V1\x00"  # 18 bytes
BIN_NAME = "oss-pce-cd-test.bin"
CUE_NAME = "oss-pce-cd-test.cue"
DEFAULT_SECTORS = 16


def sector_payload(lba: int) -> bytes:
    """Deterministic 2048 bytes: 32-byte header, then SHA-256 counter stream."""
    if not 0 <= lba < 1 << 24:
        raise ValueError("LBA out of range")
    head = MAGIC.ljust(24, b"\x00") + lba.to_bytes(4, "little") + (~lba & 0xFFFFFFFF).to_bytes(4, "little")
    body = b""
    counter = 0
    while len(body) < USER_BYTES - len(head):
        body += hashlib.sha256(MAGIC + lba.to_bytes(4, "little") + counter.to_bytes(4, "little")).digest()
        counter += 1
    return (head + body)[:USER_BYTES]


def _bcd(value: int) -> int:
    return ((value // 10) << 4) | (value % 10)


def raw_sector(lba: int) -> bytes:
    """MODE1/2352 frame; absolute MSF = LBA + 150 frames. EDC/ECC left zero."""
    frames = lba + 150
    msf = bytes((_bcd(frames // 4500), _bcd(frames // 75 % 60), _bcd(frames % 75), 1))
    raw = b"\x00" + b"\xff" * 10 + b"\x00" + msf + sector_payload(lba) + bytes(4 + 8 + 276)
    assert len(raw) == RAW_BYTES
    return raw


def audio_frame(index: int) -> bytes:
    """One 2352-byte CD-DA frame (16-bit stereo): a quiet, deterministic, non-silent ramp."""
    samples = bytearray()
    for i in range(RAW_BYTES // 4):
        value = ((index * 588 + i) * 37 % 2001) - 1000
        samples += value.to_bytes(2, "little", signed=True) * 2
    return bytes(samples)


def build_image(directory: Path, sectors: int = DEFAULT_SECTORS, audio_sectors: int = 0) -> dict:
    """Write CUE/BIN into `directory` and return the manifest (also written as JSON).

    audio_sectors > 0 appends an original CD-DA track 02 after the data track.
    """
    if not 1 <= sectors <= 4096 or not 0 <= audio_sectors <= 4096:
        raise ValueError("sector count must be 1..4096, audio sector count 0..4096")
    directory.mkdir(parents=True, exist_ok=True)
    data = b"".join(raw_sector(lba) for lba in range(sectors))
    cue_text = f'FILE "{BIN_NAME}" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n'
    if audio_sectors:
        data += b"".join(audio_frame(i) for i in range(audio_sectors))
        cue_text += (f"  TRACK 02 AUDIO\n    INDEX 01 {sectors // 4500:02d}:{sectors // 75 % 60:02d}:{sectors % 75:02d}\n")
    cue = cue_text.encode("ascii")
    (directory / BIN_NAME).write_bytes(data)
    (directory / CUE_NAME).write_bytes(cue)
    manifest = {
        "format": "CUE/BIN, single track MODE1/2352, INDEX 01 at 00:00:00 (LBA 0 = first BIN sector)",
        "generator": "tools/make_cd_image.py",
        "sectors": sectors, "audio_sectors": audio_sectors, "user_bytes_per_sector": USER_BYTES, "raw_bytes_per_sector": RAW_BYTES,
        "edc_ecc": "ZERO (not computed)",
        "bin_sha256": hashlib.sha256(data).hexdigest(),
        "cue_sha256": hashlib.sha256(cue).hexdigest(),
        "user_data_sha256": [hashlib.sha256(sector_payload(i)).hexdigest() for i in range(sectors)],
        "third_party_content": "NONE",
    }
    (directory / "disc-manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--sectors", type=int, default=DEFAULT_SECTORS)
    parser.add_argument("--audio-sectors", type=int, default=0)
    args = parser.parse_args()
    manifest = build_image(args.output, args.sectors, args.audio_sectors)
    print(f"PASS original CD fixture: {args.output / CUE_NAME} bin_sha256={manifest['bin_sha256']}")


if __name__ == "__main__":
    main()
