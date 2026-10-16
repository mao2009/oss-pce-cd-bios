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
TAM_SENTINEL = 0xA5  # sentinel written to physical WRAM[0] before TAM execution

# forbidden targets: outside the allowlist. Each must be prevented AND detected.
NEG_TARGETS = (0x0000, 0x1FFF, 0x4000)
# allowed-control targets: inside the allowlist. Each must land and not be flagged.
POS_TARGETS = (0x2000, 0x3FFF, 0x3E50)

# Exact TAM guest program layout (all addresses are CPU addresses):
#   $3E00-$3E01: LDA #$F8
#   $3E02-$3E03: TAM #$01  (MPR0: 0x00 -> 0xF8)
#   $3E04-$3E06: STA $0000 (prohibited; must not execute)
#   $3E07-$3E08: BRA *
TAM_STOP_PC = 0x3E04  # PC right after TAM, before STA
TAM_INSTRUCTIONS = 2  # LDA + TAM only; STA must not run
TAM_MPR0_BEFORE = 0x00  # page 0 initially maps to ROM
TAM_MPR0_AFTER = 0xF8   # after TAM, page 0 maps to WRAM bank

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

    Verifies with exact expected values (not loose ranges):
      * status == "rejected"
      * mpr_changed == True
      * reason == "mpr_changed_during_execution[mpr0]"
      * stop_pc == TAM_STOP_PC (0x3E04, right after TAM, before STA)
      * instructions_executed == TAM_INSTRUCTIONS (2: LDA + TAM only)
      * MPR0 changed from TAM_MPR0_BEFORE (0x00) to TAM_MPR0_AFTER (0xF8)
      * Physical WRAM[0] sentinel unchanged (TAM_SENTINEL == 0xA5)
      * ram_diff empty (no prohibited store reached RAM)
    """
    if row.get("exit", 0) != 0:
        raise ValueError(f"tam probe refused to run (exit {row.get('exit')}): {row.get('stderr','')[-300:]}")
    if row.get("status") != "rejected":
        raise ValueError(f"tam probe was not rejected: status={row.get('status')} reason={row.get('reason')}")
    if row.get("mpr_changed") is not True:
        raise ValueError(f"tam MPR change was not detected: mpr_changed={row.get('mpr_changed')}")
    reason = row.get("reason", "")
    if reason != "mpr_changed_during_execution[mpr0]":
        raise ValueError(f"tam stop reason mismatch: expected 'mpr_changed_during_execution[mpr0]', got {reason!r}")
    stop_pc = row.get("stop_pc")
    if type(stop_pc) is not int or stop_pc != TAM_STOP_PC:
        raise ValueError(f"tam stop PC mismatch: expected {TAM_STOP_PC:#x} (after TAM, before STA), got {stop_pc!r}")
    executed = row.get("instructions_executed")
    if executed != TAM_INSTRUCTIONS:
        raise ValueError(f"tam instruction count mismatch: expected {TAM_INSTRUCTIONS} (LDA+TAM only), got {executed}")
    # MPR0 must have changed from ROM (0x00) to WRAM bank (0xF8).
    before_mpr = row.get("before", {}).get("mpr", [])
    after_mpr = row.get("after", {}).get("mpr", [])
    if type(before_mpr) is not list or len(before_mpr) != 8:
        raise ValueError(f"tam before MPR snapshot invalid: {before_mpr}")
    if type(after_mpr) is not list or len(after_mpr) != 8:
        raise ValueError(f"tam after MPR snapshot invalid: {after_mpr}")
    if before_mpr[0] != TAM_MPR0_BEFORE:
        raise ValueError(f"tam MPR0 before mismatch: expected {TAM_MPR0_BEFORE:#x} (ROM), got {before_mpr[0]:#x}")
    if after_mpr[0] != TAM_MPR0_AFTER:
        raise ValueError(f"tam MPR0 after mismatch: expected {TAM_MPR0_AFTER:#x} (WRAM), got {after_mpr[0]:#x}")
    # The prohibited STA $0000 must not execute. After TAM remaps MPR0 to WRAM,
    # CPU address $0000 maps to physical WRAM[0], which holds the sentinel.
    if row.get("guard_before") != TAM_SENTINEL:
        raise ValueError(f"tam sentinel before mismatch: expected {TAM_SENTINEL:#x}, got {row.get('guard_before')!r}")
    if row.get("guard_after") != TAM_SENTINEL:
        raise ValueError(f"tam sentinel after mismatch: expected {TAM_SENTINEL:#x} (STA did not execute), got {row.get('guard_after')!r}")
    if row.get("guard_before") != row.get("guard_after"):
        raise ValueError("tam: prohibited STA modified physical RAM (guard_before != guard_after)")
    ram_diff = row.get("ram_diff")
    if type(ram_diff) is not list or len(ram_diff) != 0:
        raise ValueError(f"tam: unexpected ram_diff={ram_diff}")


def check_blocked(row, expected_exit):
    if row.get("exit") != expected_exit:
        raise ValueError(f"expected exit {expected_exit} but got {row.get('exit')}: {row.get('stderr','')[-300:]}")


def mutation_test_tam():
    import copy

    def make_base():
        """Construct a nominally-passing tam observation."""
        return {
            "exit": 0,
            "status": "rejected",
            "reason": "mpr_changed_during_execution[mpr0]",
            "mpr_changed": True,
            "stop_pc": TAM_STOP_PC,
            "instructions_executed": TAM_INSTRUCTIONS,
            "before": {"mpr": [TAM_MPR0_BEFORE, 0xF8, 0, 0, 0, 0, 0, 0]},
            "after": {"mpr": [TAM_MPR0_AFTER, 0xF8, 0, 0, 0, 0, 0, 0]},
            "guard_before": TAM_SENTINEL,
            "guard_after": TAM_SENTINEL,
            "ram_diff": [],
        }

    mutations = [
        # (name, mutation_dict, pop_keys, expected_failure_reason)
        # mutation_dict: keys to set/overwrite
        # pop_keys: keys to remove from the row (for missing-field mutations)
        ("stop_pc=0x3E05", {"stop_pc": 0x3E05}, [], "stop PC after TAM but not at STA boundary"),
        ("stop_pc=0x3E07", {"stop_pc": 0x3E07}, [], "stop PC past STA (STA may have executed)"),
        ("instructions_executed=3", {"instructions_executed": 3}, [], "STA instruction was stepped"),
        ("status=ok", {"status": "ok"}, [], "tam run was accepted instead of rejected"),
        ("mpr_changed=false", {"mpr_changed": False}, [], "MPR change not detected"),
        ("reason=mismatch", {"reason": "mpr_changed_during_execution[mpr1]"}, [], "wrong MPR index in reason"),
        ("mpr0_unchanged", {}, ["before", "after"], "MPR0 was already WRAM (no change detected)"),
        ("guard_after=0x5A", {"guard_after": ALLOWED_VALUE}, [], "sentinel overwritten by prohibited STA"),
        ("guard_before=0x5A", {"guard_before": ALLOWED_VALUE}, [], "sentinel initialized to STA value (false pass)"),
        ("ram_diff_nonempty", {"ram_diff": [{"address": 0x2000, "before": 0, "after": 0x5A}]}, [], "prohibited store reached RAM"),
        ("missing_stop_pc", {}, ["stop_pc"], "stop_pc field missing"),
        ("missing_guard_before", {}, ["guard_before"], "guard_before field missing"),
        ("missing_guard_after", {}, ["guard_after"], "guard_after field missing"),
    ]

    for name, mutation, pop_keys, _reason in mutations:
        row = copy.deepcopy(make_base())
        for k in pop_keys:
            row.pop(k, None)
        row.update(mutation)
        try:
            check_tam(row)
        except ValueError:
            continue  # expected: mutation correctly rejected
        raise ValueError(f"mutation test FAILED: {name} was accepted but should be rejected ({_reason})")


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

        # Phase 3: mutation tests - every abnormal observation must FAIL.
        mutation_test_tam()

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
          "work-RAM stores landed; dynamic MPR change detected with exact stop PC "
          "and physical-RAM sentinel; physical-bank aliasing rejected; non-standard "
          "allowlist rejected; mutation tests pass")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        print(f"FAIL/BLOCKED reference write guard: {error}", file=sys.stderr)
        raise SystemExit(1) from error
