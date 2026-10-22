#!/usr/bin/env python3
"""Regenerate the Phase ABI-3 static triage projection from a local image and compare it (Issue #476).

Local only. It needs the private reference image and the pinned cc65 da65 (TOOLS_DIR). It never runs in CI,
which has no private image. It fails closed: a missing image or a difference is an error, never a pass.
Output is compared in memory; nothing derived from the image is written into the repository.
"""
from pathlib import Path
import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import zlib

ROOT = Path(__file__).resolve().parents[1]
AUDITOR = ROOT / "tools" / "reference_rom_static_audit.py"
COMMITTED = ROOT / "docs" / "evidence" / "reference-static-triage-81.json"
SLOTS = ",".join(hex(i) for i in range(81))


def describe_image(data):
    """Identity facts of a local image. Evidence levels differ: SHA-256 binds bytes, CRC32 is only a database label."""
    header = len(data) % 8192
    return {"image_bytes": len(data), "header_bytes": header,
            "header_kind": "headerless" if header == 0 else "headered" if header == 512 else "UNSUPPORTED_SIZE",
            "sha256_whole_file": hashlib.sha256(data).hexdigest(),
            "sha256_payload": hashlib.sha256(data[header:]).hexdigest(),
            "crc32_payload": f"{zlib.crc32(data[header:]) & 0xFFFFFFFF:08X}"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rom", required=True, type=Path, help="local reference image (never committed)")
    parser.add_argument("--expect-sha256", help="owner-confirmed whole-file SHA-256; mismatch is an error")
    args = parser.parse_args(argv)
    if not args.rom.is_file():
        print(f"BLOCKED: reference image not found: {args.rom}", file=sys.stderr)
        return 2
    ident = describe_image(args.rom.read_bytes())
    print("IDENTITY (local, not a provenance claim): " + json.dumps(ident, sort_keys=True))
    if ident["header_kind"] == "UNSUPPORTED_SIZE":
        print("BLOCKED: image size is neither 262144 nor 262656 bytes", file=sys.stderr)
        return 2
    if args.expect_sha256 and args.expect_sha256.lower() != ident["sha256_whole_file"]:
        print("FAIL: whole-file SHA-256 differs from --expect-sha256", file=sys.stderr)
        return 1
    if not args.expect_sha256:
        print("NOTE: no --expect-sha256; identity is UNVERIFIED (CRC32 is a database label, not provenance)")
    with tempfile.TemporaryDirectory(prefix="abi3-triage-") as tmp:
        out = Path(tmp) / "projection.json"
        completed = subprocess.run([sys.executable, str(AUDITOR), "--rom", str(args.rom), "--slots", SLOTS,
                                    "--output", str(out)], capture_output=True, text=True)
        if completed.returncode != 0 or not out.is_file():
            print(f"FAIL: auditor exited {completed.returncode}\n{completed.stderr}", file=sys.stderr)
            return 1
        fresh = json.loads(out.read_text(encoding="utf-8"))
    committed = json.loads(COMMITTED.read_text(encoding="utf-8"))
    if fresh != committed:
        print("FAIL: regenerated projection differs from docs/evidence/reference-static-triage-81.json", file=sys.stderr)
        return 1
    print("PASS: regenerated projection matches the committed file (81 slots)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
