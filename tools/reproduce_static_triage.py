#!/usr/bin/env python3
"""Regenerate the Phase ABI-3 static triage projection from a local image and compare it (Issue #476).

Local only. It needs the private reference image and the pinned cc65 da65 (TOOLS_DIR). It never runs in CI,
which has no private image. It fails closed: a missing image or a difference is an error, never a pass.
Output is compared in memory; nothing derived from the image is written into the repository.
"""
from pathlib import Path
import argparse
import copy
import hashlib
import json
import os
import re
import shutil
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
            "header_kind": "UNSUPPORTED_SIZE" if len(data) not in (262144, 262656) else "headerless" if header == 0 else "headered",
            "sha256_whole_file": hashlib.sha256(data).hexdigest(),
            "sha256_payload": hashlib.sha256(data[header:]).hexdigest(),
            "crc32_payload": f"{zlib.crc32(data[header:]) & 0xFFFFFFFF:08X}"}


# Callee metrics added to the analyzer after the committed evidence was captured (#485, PR #490).
# Named allowlist: only these keys may be absent from the committed evidence. Anything else must match exactly.
ADDED_CALLEE_FIELDS = ("states_without_guaranteed_return",)
UNBASELINED = "UNBASELINED"
EXIT_UNBASELINED = 3  # 0 PASS, 1 FAIL, 2 BLOCKED, 3 UNBASELINED (existing fields match, new metric unverified)
SHA256_RE = re.compile(r"[0-9a-fA-F]{64}")


def _valid_added_field(callee, key):
    """Type, range and scope check of an allowlisted added metric (callee-level count)."""
    v, visited, floor = callee[key], callee.get("states_visited"), callee.get("non_returning_states", 0)
    if any(type(x) is not int for x in (v, visited, floor)):  # fail closed, never TypeError
        return False
    # states with no existence-path to a return are a subset of states with no guaranteed return
    return floor <= v <= visited


def compare_projection(fresh, committed):
    """Return (ok, message, unbaselined_fields). Existing fields: exact equality of value and structure.

    An allowlisted field present in fresh but absent from committed is validated, dropped from the comparison
    and reported as UNBASELINED (not verified evidence). Every other difference or extra key is a failure.
    """
    fresh = copy.deepcopy(fresh)
    unbaselined = set()
    f_apis, c_apis = fresh.get("apis"), committed.get("apis")
    if not (isinstance(f_apis, list) and isinstance(c_apis, list)) or len(f_apis) != len(c_apis):
        return False, "apis structure differs", []
    for f, c in zip(f_apis, c_apis):
        fc, cc = f.get("callee"), c.get("callee")
        if not (isinstance(fc, dict) and isinstance(cc, dict)):
            return False, "callee structure differs", []
        for key in ADDED_CALLEE_FIELDS:
            if key not in fc:
                continue
            if not _valid_added_field(fc, key):
                return False, f"{key} invalid in {f.get('api_id')} (type/range/scope)", []
            if key not in cc:
                del fc[key]
                unbaselined.add(key)
    # type-strict: json text distinguishes true/1 and 4.0/4, which Python == does not
    if json.dumps(fresh, sort_keys=True) != json.dumps(committed, sort_keys=True):
        return False, "regenerated projection differs from docs/evidence/reference-static-triage-81.json", []
    return True, "", sorted(unbaselined)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rom", required=True, type=Path, help="local reference image (never committed)")
    parser.add_argument("--expect-sha256", help="owner-confirmed whole-file SHA-256; mismatch is an error")
    args = parser.parse_args(argv)
    if not args.rom.is_file():
        print(f"BLOCKED: reference image not found: {args.rom}", file=sys.stderr)
        return 2
    if args.rom.stat().st_size not in (262144, 262656):  # size gate before reading the content
        print("BLOCKED: image size is neither 262144 nor 262656 bytes", file=sys.stderr)
        return 2
    data = args.rom.read_bytes()  # the ONLY read of the original; everything below uses these bytes
    ident = describe_image(data)
    print("IDENTITY (local, not a provenance claim): " + json.dumps(ident, sort_keys=True))
    if ident["header_kind"] == "UNSUPPORTED_SIZE":
        print("BLOCKED: image size is neither 262144 nor 262656 bytes", file=sys.stderr)
        return 2
    expect = args.expect_sha256
    if expect is not None:
        expect = expect.strip()
        if not SHA256_RE.fullmatch(expect):
            print("FAIL: --expect-sha256 is not a 64-hex-digit SHA-256", file=sys.stderr)
            return 1
        if expect.lower() != ident["sha256_whole_file"]:
            print("FAIL: whole-file SHA-256 differs from --expect-sha256", file=sys.stderr)
            return 1
        print("SHA-256 matches --expect-sha256 (owner-provided value; not canonical identity)")
    else:
        print("NOTE: no --expect-sha256; identity is UNVERIFIED (CRC32 is a database label, not provenance)")
    tmp = tempfile.mkdtemp(prefix="abi3-triage-")  # outside the repo; 0700 on POSIX, no ACL hardening on Windows
    try:
        rc = _analyze(tmp, data, ident)
    finally:
        left = _remove(tmp)
    return 1 if left and rc in (0, EXIT_UNBASELINED) else rc


def _remove(tmp):
    """Delete the snapshot dir; report loudly (stderr) if it is still there. Returns True when left behind."""
    try:
        shutil.rmtree(tmp)
    except OSError:
        pass
    if Path(tmp).exists():
        print(f"SNAPSHOT NOT DELETED: {tmp}", file=sys.stderr)
        return True
    return False


def _analyze(tmp, data, ident):
    snap = Path(tmp) / "snapshot.bin"
    fd = os.open(snap, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    if hashlib.sha256(snap.read_bytes()).hexdigest() != ident["sha256_whole_file"]:
        print("FAIL: snapshot differs from the hashed bytes", file=sys.stderr)
        return 1
    out = Path(tmp) / "projection.json"
    completed = subprocess.run([sys.executable, str(AUDITOR), "--rom", str(snap), "--slots", SLOTS,
                                "--output", str(out)], capture_output=True, text=True)
    if completed.returncode != 0 or not out.is_file():
        print(f"FAIL: auditor exited {completed.returncode}\n{(completed.stderr or '')[-500:]}", file=sys.stderr)
        return 1
    fresh = json.loads(out.read_text(encoding="utf-8"))
    committed = json.loads(COMMITTED.read_text(encoding="utf-8"))
    ok, msg, unbaselined = compare_projection(fresh, committed)
    if not ok:
        print("FAIL: " + msg, file=sys.stderr)
        return 1
    if unbaselined:
        print(f"{UNBASELINED}: {', '.join(unbaselined)} present in the analyzer but not in the committed evidence; "
              "existing fields match, new metric NOT verified (exit 3, not a PASS)", file=sys.stderr)
        return EXIT_UNBASELINED
    print("PASS: all fields of the regenerated projection match the committed file (81 slots); "
          "this is not canonical ROM identity")
    return 0


if __name__ == "__main__":
    sys.exit(main())
