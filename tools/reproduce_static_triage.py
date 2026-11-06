#!/usr/bin/env python3
"""Regenerate the Phase ABI-3 static triage projection from a local image (Issue #476).

Local only. It needs the private reference image and the pinned cc65 da65 (TOOLS_DIR). It never runs in CI,
which has no private image. It fails closed: a missing image or a difference is an error, never a pass.
No public projection is committed (Issue #324, owner ruling A). Comparison is possible only against a local
baseline passed with --baseline, which must lie outside the repository. Without one the result is NOT_VERIFIED.
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
# Local baseline JSON, set only through --baseline. No public projection exists in the repository.
BASELINE = None
SLOTS = ",".join(hex(i) for i in range(81))


def _inside_repo(path):
    """True when the path lies inside the repository. An unresolvable path counts as inside (fail closed)."""
    try:
        return path.resolve().is_relative_to(ROOT.resolve())
    except (OSError, ValueError):  # ValueError: a path with a NUL byte
        return True


def describe_image(data):
    """Identity facts of a local image. Evidence levels differ: SHA-256 binds bytes, CRC32 is only a database label."""
    header = len(data) % 8192
    return {"image_bytes": len(data), "header_bytes": header,
            "header_kind": "UNSUPPORTED_SIZE" if len(data) not in (262144, 262656) else "headerless" if header == 0 else "headered",
            "sha256_whole_file": hashlib.sha256(data).hexdigest(),
            "sha256_payload": hashlib.sha256(data[header:]).hexdigest(),
            "crc32_payload": f"{zlib.crc32(data[header:]) & 0xFFFFFFFF:08X}"}


# Callee metrics added to the analyzer after the local baseline was captured (#485, PR #490).
# Named allowlist: only these keys may be absent from the local baseline. Anything else must match exactly.
# Remove an entry here once the local baseline is regenerated with it (then it is compared strictly).
ADDED_CALLEE_FIELDS = ("states_without_guaranteed_return",)
UNBASELINED = "UNBASELINED"
EXIT_UNBASELINED = 3  # 0 PASS, 1 FAIL, 2 BLOCKED, 3 UNBASELINED (existing fields match, new metric unverified)
SHA256_RE = re.compile(r"[0-9a-fA-F]{64}")


def _valid_added_field(callee, key):
    """Type, range and scope check of an allowlisted added metric (callee-level count)."""
    v, floor = callee[key], callee.get("non_returning_states", 0)
    if type(v) is not int or type(floor) is not int:  # fail closed, never TypeError
        return False
    # Lower bound: states with no path to a return also have no guaranteed return (subset).
    # Upper bound (derived from #490 tools/reference_rom_static_audit.py, audit()): the value is at most len(visited).
    # Decoded states = states_visited (S). Every other visited state is a dead end (depth/unknown bank/undecodable)
    # that was emitted as a successor of a decoded state; a decoded state emits at most 2 successors, so there are at
    # most 2*S of them, plus the entry state itself when it is a dead end. Hence v <= 3*S + 1. It can exceed S.
    visited = callee.get("states_visited")
    return type(visited) is int and max(0, floor) <= v <= 3 * visited + 1


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
                return False, f"{key} invalid in one slot (type/range/scope; slot withheld)", []
            if key not in cc:
                del fc[key]
                unbaselined.add(key)
    # type-strict: json text distinguishes true/1 and 4.0/4, which Python == does not
    if json.dumps(fresh, sort_keys=True) != json.dumps(committed, sort_keys=True):
        return False, "regenerated projection differs from the local baseline", []
    return True, "", sorted(unbaselined)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rom", required=True, type=Path, help="local reference image (never committed)")
    parser.add_argument("--expect-sha256", help="owner-confirmed whole-file SHA-256; mismatch is an error")
    parser.add_argument("--baseline", type=Path,
                        help="local baseline projection JSON (outside the repository); without it the result is NOT_VERIFIED")
    args = parser.parse_args(argv)
    if args.baseline is not None and _inside_repo(args.baseline):
        print("BLOCKED: --baseline inside the repository is refused (no projection is committed)", file=sys.stderr)
        return 2
    try:
        if not args.rom.is_file():
            print("BLOCKED: reference image not found (path withheld)", file=sys.stderr)
            return 2
        size = args.rom.stat().st_size
    except (OSError, ValueError):  # ValueError: a path with a NUL byte; no traceback, which can carry the path
        print("BLOCKED: the reference image could not be inspected (path withheld)", file=sys.stderr)
        return 2
    if size not in (262144, 262656):  # size gate before reading the content
        print("BLOCKED: image size is not a supported size (value withheld)", file=sys.stderr)
        return 2
    try:
        data = args.rom.read_bytes()  # the ONLY read of the original; everything below uses these bytes
    except OSError:
        print("BLOCKED: the reference image could not be read (path withheld)", file=sys.stderr)
        return 2
    ident = describe_image(data)
    # The identity values are never printed (owner ruling: identity of the private image is not published, and
    # terminal or CI logs are publication too). Only the classification is shown.
    print("IDENTITY (local, not a provenance claim): values withheld")
    if ident["header_kind"] == "UNSUPPORTED_SIZE":
        print("BLOCKED: image size is not a supported size (value withheld)", file=sys.stderr)
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
    try:
        tmp = tempfile.mkdtemp(prefix="abi3-triage-")  # outside the repo; 0700 on POSIX, no ACL hardening on Windows
    except OSError:
        print("BLOCKED: no temporary directory could be created (details withheld)", file=sys.stderr)
        return 2
    try:
        try:
            rc = _analyze(tmp, data, ident, args.baseline)
        except Exception as error:  # no traceback: it can carry the temporary path or the image path
            print(f"BLOCKED: internal error ({type(error).__name__}; details withheld)", file=sys.stderr)
            rc = 2
    finally:
        left = _remove(tmp)
    # A snapshot left behind is a failure whatever the comparison said: the private bytes must not remain.
    return 1 if left else rc


def _remove(tmp):
    """Delete the snapshot dir; report loudly (stderr) if it is still there. Returns True when left behind."""
    try:
        shutil.rmtree(tmp)
    except OSError:
        pass
    if Path(tmp).exists():
        print("SNAPSHOT NOT DELETED: the temporary directory is left behind (path withheld)", file=sys.stderr)
        return True
    return False


def _analyze(tmp, data, ident, baseline_path=None):
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
        # The auditor's stderr is not echoed: it can carry paths or analysis detail.
        print(f"FAIL: auditor exited with code {completed.returncode} (output withheld)", file=sys.stderr)
        return 1
    fresh = json.loads(out.read_text(encoding="utf-8"))
    baseline = baseline_path or BASELINE
    if baseline is None:
        print("NOT_VERIFIED: regenerated locally, but no local baseline was given (--baseline). "
              "Nothing was compared; this is not a PASS (exit 3).", file=sys.stderr)
        return EXIT_UNBASELINED
    try:
        committed = json.loads(Path(baseline).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print("UNBASELINED: the local baseline could not be read (path withheld); not a PASS (exit 3)",
              file=sys.stderr)
        return EXIT_UNBASELINED
    if not (isinstance(committed, dict) and isinstance(committed.get("apis"), list)):
        # Not a projection at all: that is an unusable baseline (exit 3), not a comparison FAIL (exit 1).
        print("UNBASELINED: the local baseline is not a projection (shape withheld); not a PASS (exit 3)",
              file=sys.stderr)
        return EXIT_UNBASELINED
    ok, msg, unbaselined = compare_projection(fresh, committed)
    if not ok:
        print("FAIL: " + msg, file=sys.stderr)
        return 1
    if unbaselined:
        cut = [a.get("api_id") for a in fresh.get("apis", []) if a.get("callee", {}).get("budget_exhausted") is True]
        print(f"{UNBASELINED}: {', '.join(unbaselined)} present in the analyzer but not in the committed evidence; "
              "existing fields match, new metric NOT verified (exit 3, not a PASS)"
              + (f"; {len(cut)} slot(s) exhausted the budget (value is only a lower bound; slots not printed)" if cut else ""),
              file=sys.stderr)
        return EXIT_UNBASELINED
    print("PASS: all fields of the regenerated projection match the local baseline (81 slots); "
          "this is not canonical ROM identity")
    return 0


if __name__ == "__main__":
    sys.exit(main())
