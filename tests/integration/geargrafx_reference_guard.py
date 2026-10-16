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
* ``tam`` attempts a dynamic MPR change (``TAM`` re-mapping page 0 to the
  WRAM bank) followed by a store through the remapped page. The core must detect
  the MPR change before the store executes.

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

# forbidden targets: outside the allowlist. Each must be prevented AND detected.
NEG_TARGETS = (0x0000, 0x1FFF, 0x4000)
# allowed-control targets: inside the allowlist. Each must land and not be flagged.
POS_TARGETS = (0x2000, 0x3FFF, 0x3E50)

# Exit codes the probe returns when it refuses to run.
BLOCKED_EXIT_ALIAS = 8   # physical bank shared by allowlisted/non-allowlisted pages
BLOCKED_EXIT_ALLOWLIST = 9  # allowlist not exactly [0x2000, 0x4000)


def synthetic_media(directory):
    bios = directory / "synthetic-bios.pce"
    bios.write_bytes(bytes(BIOS_BYTES))
    (directory / "synthetic.bin").write_bytes(bytes(2352 * 32))
    cue = directory / "synthetic.cue"
    cue.write_text('FILE "synthetic.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
    return bios, cue


def run_mode(probe, bios, cue, mode, target, env_alias=False, allow_start=None,
             allow_end=None):
    a_start = ALLOW[0] if allow_start is None else allow_start
    a_end = ALLOW[1] if allow_end is None else allow_end
    command = [str(probe), str(bios), str(cue), "0x1e", "100",
               hex(a_start), hex(a_end), mode, hex(target)]
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
    if row.get("guard_target") != target:
        raise ValueError(f"allowed control store ${target:04X} targeted wrong address: {row.get('guard_target')}")
    if row.get("guard_before") == ALLOWED_VALUE:
        raise ValueError(f"allowed control store ${target:04X} target was already {ALLOWED_VALUE:#x} before execution")
    if row.get("guard_before") == row.get("guard_after"):
        raise ValueError(f"allowed control store ${target:04X} did not change the target byte")
    if row.get("guard_after") != ALLOWED_VALUE:
        raise ValueError(f"allowed control store ${target:04X} did not land; the guard drops every write")
    # Normal execution: a small positive instruction count, not a timeout.
    executed = row.get("instructions_executed")
    if type(executed) is not int or not 1 <= executed <= 100:
        raise ValueError(f"allowed control store ${target:04X} invalid instruction count: {executed}")


def check_tam(row):
    """Tam mode: MPR change must be detected and the following STA must not execute.

    Verifies status=rejected, mpr_changed=True, reason names the MPR change,
    instruction count is small (TAM was stepped, not timed out), stop PC is
    right after TAM (before the STA), and no RAM was modified by the STA.
    """
    if row.get("exit", 0) != 0:
        raise ValueError(f"tam probe refused to run (exit {row.get('exit')}): {row.get('stderr','')[-300:]}")
    if row.get("status") != "rejected":
        raise ValueError(f"tam probe was not rejected: status={row.get('status')} reason={row.get('reason')}")
    if row.get("mpr_changed") is not True:
        raise ValueError(f"tam MPR change was not detected: mpr_changed={row.get('mpr_changed')}")
    reason = row.get("reason", "")
    if not reason.startswith("mpr_changed_during_execution"):
        raise ValueError(f"tam stop reason does not name MPR change: {reason}")
    executed = row.get("instructions_executed")
    if type(executed) is not int or not 2 <= executed <= 10:
        raise ValueError(f"tam instruction count out of expected range: {executed}")
    # The STA after TAM must not have executed: no RAM changes at all.
    if row.get("guard_before") != row.get("guard_after"):
        raise ValueError(f"tam: prohibited STA modified RAM (guard_before={row.get('guard_before')} != guard_after={row.get('guard_after')})")
    ram_diff = row.get("ram_diff")
    if type(ram_diff) is not list or len(ram_diff) != 0:
        raise ValueError(f"tam: unexpected ram_diff={ram_diff}")
    # Stop PC should be right after TAM (the STA address), before the store.
    stop_pc = row.get("stop_pc")
    if type(stop_pc) is not int or not 0x3E00 <= stop_pc <= 0x3EFF:
        raise ValueError(f"tam stop PC out of guest range: {stop_pc}")


def check_blocked(row, expected_exit):
    if row.get("exit") != expected_exit:
        raise ValueError(f"expected exit {expected_exit} but got {row.get('exit')}: {row.get('stderr','')[-300:]}")


def main():
    with tempfile.TemporaryDirectory(prefix="pce-reference-guard-") as tmp:
        directory = Path(tmp)
        bios, cue = synthetic_media(directory)
        probe = compile_probe(directory, source_name="geargrafx_reference_jsr.cpp",
                              target_name="reference-guard-probe")

        # Phase 2/3: forbidden stores must be prevented AND detected.
        for target in NEG_TARGETS:
            check_neg(run_mode(probe, bios, cue, "neg-write", target), target)

        # Phase 2/3: allowed stores must land and not be flagged.
        for target in POS_TARGETS:
            check_pos(run_mode(probe, bios, cue, "pos-write", target), target)

        # Phase 1: dynamic MPR change (TAM remapping page 0 to WRAM bank) must
        # be detected before the subsequent store executes.
        check_tam(run_mode(probe, bios, cue, "tam", NEG_TARGETS[0]))

        # Phase 1: static physical-bank aliasing must be rejected before execution.
        alias = run_mode(probe, bios, cue, "neg-write", NEG_TARGETS[0], env_alias=True)
        check_blocked(alias, BLOCKED_EXIT_ALIAS)

        # Phase 2: allowlist must be exactly [0x2000, 0x4000); any other interval
        # is rejected at the C++ level (defense-in-depth beyond the Python adapter).
        # neg_addr is chosen inside each bad allowlist so the pos-write range check
        # passes and we reach the allowlist validation.
        for bad_start, bad_end, bad_target in ((0x0, 0x10000, 0x2000),
                                               (0x4000, 0x6000, 0x5000),
                                               (0x0, 0x2000, 0x1000)):
            bad = run_mode(probe, bios, cue, "pos-write", bad_target,
                           allow_start=bad_start, allow_end=bad_end)
            check_blocked(bad, BLOCKED_EXIT_ALLOWLIST)

    print("PASS real-core write guard: forbidden stores prevented+detected; allowed "
          "work-RAM stores landed; dynamic MPR change detected; physical-bank "
          "aliasing rejected; non-standard allowlist rejected")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        print(f"FAIL/BLOCKED reference write guard: {error}", file=sys.stderr)
        raise SystemExit(1) from error
