#!/usr/bin/env python3
"""Prove the pinned real core blocks a forbidden store and still permits an allowed one.

This is a REAL-CORE negative test of the in-core write guard that the guarded
reference-BIOS observer (`tests/integration/geargrafx_reference_jsr.cpp`) relies
on. It uses only fully synthetic assets — a 256 KiB synthetic BIOS image and a
synthetic zero-filled CD — and never touches a private reference BIOS, so it is
safe to run in CI.

Two runs are executed against the pinned Geargrafx instruction-debugger build:

* ``neg-write`` attempts a store to CD-ROM RAM (a mapped device) outside the
  configured allowlist. The core must PREVENT the store (the byte is unchanged)
  and DETECT it (the CPU-address write breakpoint fires).
* ``pos-write`` attempts a store to the allowlisted work-RAM page. That store
  MUST land and must NOT be flagged, proving the guard is not a blanket drop of
  every write.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from geargrafx_api import compile_probe  # noqa: E402

BIOS_BYTES = 256 * 1024
ALLOW = (0x2000, 0x4000)  # page 1: this core hardwires the zero page/stack there
NEG_TARGET = 0x4000       # page 2 -> CD-ROM RAM bank 0x80 (mapped device, not allowlisted)
POS_TARGET = 0x3E50       # page 1 -> work RAM (allowlisted)
ALLOWED_VALUE = 0x5A


def synthetic_media(directory):
    bios = directory / "synthetic-bios.pce"
    bios.write_bytes(bytes(BIOS_BYTES))
    (directory / "synthetic.bin").write_bytes(bytes(2352 * 32))
    cue = directory / "synthetic.cue"
    cue.write_text('FILE "synthetic.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
    return bios, cue


def run_mode(probe, bios, cue, mode, target):
    command = [str(probe), str(bios), str(cue), "0x1e", "100",
               hex(ALLOW[0]), hex(ALLOW[1]), mode, hex(target)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
    if result.returncode:
        raise ValueError(f"{mode} probe exited {result.returncode}: {result.stderr[-2000:]}")
    try:
        row = json.loads(result.stderr)
    except (ValueError, TypeError) as error:
        raise ValueError(f"{mode} probe emitted no JSON observation") from error
    if type(row) is not dict:
        raise ValueError(f"{mode} probe observation is not an object")
    return row


def check(neg, pos):
    if neg.get("status") != "ok":
        raise ValueError(f"forbidden store was not rejected: {neg.get('reason')}")
    if neg.get("detected") is not True:
        raise ValueError("forbidden store was not detected by the in-core write breakpoint")
    if neg.get("guard_target") != NEG_TARGET or neg.get("guard_before") != neg.get("guard_after"):
        raise ValueError("forbidden store was NOT prevented; the guard is not enforced in-core")
    if pos.get("status") != "ok":
        raise ValueError(f"allowed control store failed: {pos.get('reason')}")
    if pos.get("detected") is not False:
        raise ValueError("allowed control store was wrongly flagged by the write breakpoint")
    if pos.get("guard_before") == ALLOWED_VALUE:
        raise ValueError("allowed control store target was not clean before execution")
    if pos.get("guard_after") != ALLOWED_VALUE:
        raise ValueError("allowed control store did not land; the guard drops every write")


def main():
    with tempfile.TemporaryDirectory(prefix="pce-reference-guard-") as tmp:
        directory = Path(tmp)
        bios, cue = synthetic_media(directory)
        probe = compile_probe(directory, source_name="geargrafx_reference_jsr.cpp",
                              target_name="reference-guard-probe")
        neg = run_mode(probe, bios, cue, "neg-write", NEG_TARGET)
        pos = run_mode(probe, bios, cue, "pos-write", POS_TARGET)
    check(neg, pos)
    print("PASS real-core write guard: forbidden CD-ROM store prevented+detected; "
          "allowed work-RAM store landed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        print(f"FAIL/BLOCKED reference write guard: {error}", file=sys.stderr)
        raise SystemExit(1) from error
