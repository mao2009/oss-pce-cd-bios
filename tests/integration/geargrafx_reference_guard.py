#!/usr/bin/env python3
"""Prove the pinned real core blocks a forbidden store and still permits an allowed one.

This is a REAL-CORE negative test of the in-core write guard that the guarded
reference-BIOS observer (`tests/integration/geargrafx_reference_jsr.cpp`) relies
on. It uses only fully synthetic assets — a 256 KiB synthetic BIOS image and a
synthetic zero-filled CD — and never touches a private reference BIOS, so it is
safe to run in CI.

Two classes of runs are executed against the pinned Geargrafx instruction-debugger
build:

* ``neg-write`` attempts a store to a CPU address **outside** the configured
  allowlist. The core must PREVENT the store (the target byte is unchanged) and
  DETECT it (the CPU-address write breakpoint fires before the store).
* ``pos-write`` attempts a store to a CPU address **inside** the allowlist. That
  store MUST land and must NOT be flagged, proving the guard is not a blanket drop
  of every write.

A third run forces MPR0 == MPR1 (both pages alias the same work-RAM bank) while
page 1 is allowlisted and page 0 is not. Because
``Memory::GetMemoryMapWrite()`` is indexed by physical bank, that aliasing would
let a store to page 0 slip through the allowlist. The probe must refuse to run
(fail closed, exit 8) before any guest instruction executes.

Guard verdicts are decided on **before/after byte equality at the target address**
plus the breakpoint detection flag — never on an empty post-run ``ram_diff``
alone.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from geargrafx_api import compile_probe  # noqa: E402

BIOS_BYTES = 256 * 1024
ALLOW = (0x2000, 0x4000)  # page 1: this core hardwires the zero page/stack there
ALLOWED_VALUE = 0x5A

# forbidden targets: outside the allowlist (page 0 aliases page 1's WRAM bank;
# page 2 is CD-ROM RAM). Each must be prevented AND detected.
NEG_TARGETS = (0x0000, 0x1FFF, 0x4000)
# allowed-control targets: inside the allowlist. Each must land and not be flagged.
POS_TARGETS = (0x2000, 0x3FFF, 0x3E50)

# Exit code the fixed probe returns when it refuses to run because a physical
# bank is shared by an allowlisted and a non-allowlisted CPU page.
BLOCKED_EXIT = 8


def synthetic_media(directory):
    bios = directory / "synthetic-bios.pce"
    bios.write_bytes(bytes(BIOS_BYTES))
    (directory / "synthetic.bin").write_bytes(bytes(2352 * 32))
    cue = directory / "synthetic.cue"
    cue.write_text('FILE "synthetic.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
    return bios, cue


def run_mode(probe, bios, cue, mode, target, env_alias=False):
    command = [str(probe), str(bios), str(cue), "0x1e", "100",
               hex(ALLOW[0]), hex(ALLOW[1]), mode, hex(target)]
    env = dict(os.environ)
    if env_alias:
        env["PCE_REF_TEST_ALIAS"] = "1"
    result = subprocess.run(command, capture_output=True, text=True, timeout=60,
                            check=False, env=env)
    if result.returncode:
        return {"exit": result.returncode, "stderr": result.stderr}
    try:
        row = json.loads(result.stderr)
    except (ValueError, TypeError) as error:
        raise ValueError(f"{mode} ${target:04X} probe emitted no JSON observation") from error
    if type(row) is not dict:
        raise ValueError(f"{mode} ${target:04X} probe observation is not an object")
    row["exit"] = 0
    return row


def check_neg(row, target):
    if row.get("exit", 0) != 0:
        raise ValueError(f"neg-write ${target:04X} probe refused to run (exit {row.get('exit')}): {row.get('stderr','')[-300:]}")
    if row.get("status") != "ok":
        raise ValueError(f"forbidden store ${target:04X} was not rejected: {row.get('reason')}")
    if row.get("detected") is not True:
        raise ValueError(f"forbidden store ${target:04X} was not detected by the in-core write breakpoint")
    if row.get("guard_target") != target or row.get("guard_before") != row.get("guard_after"):
        raise ValueError(f"forbidden store ${target:04X} was NOT prevented; the guard is not enforced in-core")


def check_pos(row, target):
    if row.get("exit", 0) != 0:
        raise ValueError(f"pos-write ${target:04X} probe refused to run (exit {row.get('exit')}): {row.get('stderr','')[-300:]}")
    if row.get("status") != "ok":
        raise ValueError(f"allowed control store ${target:04X} failed: {row.get('reason')}")
    if row.get("detected") is not False:
        raise ValueError(f"allowed control store ${target:04X} was wrongly flagged by the write breakpoint")
    if row.get("guard_after") != ALLOWED_VALUE:
        raise ValueError(f"allowed control store ${target:04X} did not land; the guard drops every write")


def main():
    with tempfile.TemporaryDirectory(prefix="pce-reference-guard-") as tmp:
        directory = Path(tmp)
        bios, cue = synthetic_media(directory)
        probe = compile_probe(directory, source_name="geargrafx_reference_jsr.cpp",
                              target_name="reference-guard-probe")

        for target in NEG_TARGETS:
            check_neg(run_mode(probe, bios, cue, "neg-write", target), target)
        for target in POS_TARGETS:
            check_pos(run_mode(probe, bios, cue, "pos-write", target), target)

        # Aliasing BLOCKED path: page 0 and page 1 map to the same WRAM bank while
        # only page 1 is allowlisted -> the probe must refuse to run.
        alias = run_mode(probe, bios, cue, "neg-write", NEG_TARGETS[0], env_alias=True)
        if alias.get("exit") != BLOCKED_EXIT:
            raise ValueError(f"aliasing configuration was not rejected (exit {alias.get('exit')}); "
                             "physical-bank write protection cannot isolate CPU addresses")

    print("PASS real-core write guard: forbidden stores prevented+detected; allowed work-RAM stores landed; "
          "physical-bank aliasing rejected before execution")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        print(f"FAIL/BLOCKED reference write guard: {error}", file=sys.stderr)
        raise SystemExit(1) from error
