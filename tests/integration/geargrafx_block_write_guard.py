#!/usr/bin/env python3
"""Issue #367 (parent #173): free-running pre-write refusal for TII/TIN/TIA/TDD/TAI byte stores.

The refusal is an in-core hook, not a harness single-step: a VERIFICATION-ONLY patch
(patches/geargrafx-b49ae82-block-write-guard.patch, against b49ae82a012eade566d72e9d61c9d95a5caa4da3)
makes the five block-transfer handlers call a guard before the per-byte read and Memory::Write.
The probe (geargrafx_block_write_guard.cpp) runs the core with RunToVBlank and no debugger stepping.

Only the patched huc6280_opcodes object is substituted into the probe link; the stock source tree is
not modified. The GPL-3.0 core is linked locally and never distributed. Synthetic BIOS/CD only; no
private reference BIOS or ROM is read. Nothing here changes BLOCKED_ABI or release_eligible.

Cases (for each of tii, tin, tia, tdd, tai; one-byte transfer):
* ``<op>-forbidden-armed``: MPR0 = $FF, dest $0000 (VDC AR). The guard refuses the store before the
  side effect. Required: exactly one guard call, one refusal, VDC AR unchanged after the frame.
* ``<op>-forbidden-unguarded`` (MUTATION): same guest, no hook. The VDC AR write must land.
* ``<op>-allowed-armed`` (POSITIVE): MPR0 = $00, dest $3E90 (WRAM). The guard is consulted once and
  allows the store; the byte must land.
* ``<op>-stackff-armed`` (Issue #459): MPR1 = $FF, so the TransferStart A/X/Y pushes ($2100|S) hit the
  hardware page. The TransferStart guard refuses the pushes before the side effect: exactly one stack
  guard call and one refusal, VDC AR and R[7] unchanged, S balanced. The block store (dest $1E90, WRAM)
  must still land.
* ``<op>-stackff-unguarded`` (MUTATION, #459): same layout, no hook. The pushes must land on the VDC:
  AR 0 -> 7 and R[7] 0 -> 564 (the A/X bytes $12/$34 masked to BXR's 10 bits).
* ``<op>-stackwram-armed`` (POSITIVE, #459): MPR1 = WRAM. The stack guard is consulted once, allows
  the pushes, and the stack bytes X=$34, A=$12, Y=$07 land at WRAM $0102..$0104.

Scope and limits (read before citing this as evidence):
* The refusal policy (refuse a store whose destination page is mapped to $FF) is written inside the
  probe (geargrafx_block_write_guard.cpp). It is a test policy, NOT a production gate. This test shows
  only that the core's pre-write hook point can refuse a store before its side effect; it does not show
  that a production gate would refuse it.
* ``row["armed"]`` is set in this driver from the case name (``case.endswith("-armed")``), so the armed
  checks confirm the requested configuration only and are not independent evidence. The discriminating
  evidence is guard_calls / guard_refusals, the VDC AR and WRAM observations, and the unguarded control.

Run through the Makefile (``make block-write-guard``; also part of ``make integration``, which
``make test-real-core`` runs in CI), or directly:
    python3 tests/integration/geargrafx_block_write_guard.py
"""
import copy
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from geargrafx_api import build_command  # noqa: E402  (also puts tools/ on sys.path)
from environment import dependency_dir  # noqa: E402
from geargrafx_reference_guard import synthetic_media  # noqa: E402

PINNED_CORE_REVISION = "b49ae82a012eade566d72e9d61c9d95a5caa4da3"
PATCH = Path(__file__).resolve().parent / "patches" / "geargrafx-b49ae82-block-write-guard.patch"
PROBE_SOURCE = "geargrafx_block_write_guard.cpp"
FRAMES = 3
GUEST_BASE = 0x3E00
FORBIDDEN_DST = 0x0000
ALLOWED_DST = 0x3E90
# op -> (block_op index passed to the guard, opcode byte)
OPS = {"tii": (0, 0x73), "tin": (1, 0xD3), "tia": (2, 0xE3), "tdd": (3, 0xC3), "tai": (4, 0xF3)}
FORBIDDEN_VALUE = 0x13  # written to VDC AR (register index, no masking issue)
ALLOWED_VALUE = 0x5A
# Issue #459 stack-page layout: S=$04, Y=$07 (-> AR 7), A=$12, X=$34 pushed to $0104/$0103/$0102.
# If the pushes land on the VDC, R[7] (BXR, 10 bits) = (A<<8|X) & 0x3FF = $234 = 564.
STACK_AR_LANDED = 7
STACK_R7_LANDED = 0x1234 & 0x3FF
STACK_WRAM_LANDED = [0x34, 0x12, 0x07]  # WRAM $0102, $0103, $0104 = X, A, Y
STACK_SP = 0x04
ALLOWED_STACK_DST = 0x1E90  # block store dest in the stack layout (page 0 = WRAM)
# Exactly the flags tools/setup.py build_geargrafx(debugger=True) passes as CFLAGS/CXXFLAGS overrides
# (the debugger tree's objects are built with these, not with the stock Makefile defaults). Note the
# debugger build does NOT define GG_DISABLE_DISASSEMBLER; adding it would change object layout.
DEBUGGER_BUILD_FLAGS = ("-O2 -fPIC -fno-exceptions -D__LIBRETRO__ -DZ7_ST -DZSTD_DISABLE_ASM "
                        "-DGG_DISABLE_VGMRECORDER -DGG_ENABLE_CDROM_CUEBIN_READAHEAD -pthread").split()
CXX_EXTRA = ["-std=c++11", "-fno-rtti"]  # CXXFLAGS adds -fno-rtti; the Makefile rule adds -std=c++11


def _cases():
    for op in OPS:
        yield f"{op}-forbidden-armed"
        yield f"{op}-forbidden-unguarded"
        yield f"{op}-allowed-armed"
        yield f"{op}-stackff-armed"
        yield f"{op}-stackff-unguarded"
        yield f"{op}-stackwram-armed"


def _common(row, case):
    if row.get("exit", 0) != 0:
        raise ValueError(f"{case} probe refused to run (exit {row.get('exit')}): {row.get('stderr', '')[-300:]}")
    if row.get("mode") != case:
        raise ValueError(f"{case} measured wrong mode: {row.get('mode')}")
    if row.get("status") != "ok":
        raise ValueError(f"{case}: guest did not reach its end (status={row.get('status')})")
    for name in ("vdc_ar_before", "vdc_ar_after", "vdc_r_before", "vdc_r_after", "stop_pc", "guard_calls",
                 "guard_refusals", "dst_before", "dst_after", "guard_block_op", "guard_dest"):
        if type(row.get(name)) is not int:
            raise ValueError(f"{case} missing/non-integer {name}: {row.get(name)!r}")
    for side in ("before", "after"):
        mpr = row.get(side, {}).get("mpr")
        if type(mpr) is not list or len(mpr) != 8:
            raise ValueError(f"{case}: malformed MPR {side}")
    if row["stop_pc"] != row["expected_end_pc"]:
        raise ValueError(f"{case}: guest stopped at {row['stop_pc']:#x}, expected {row['expected_end_pc']:#x}")
    op, variant = case.split("-")[0], case.split("-")[1]
    want_mpr0, want_mpr1 = _expected_mpr(variant)
    for side in ("before", "after"):
        mpr = row[side]["mpr"]
        if mpr[0] != want_mpr0 or mpr[1] != want_mpr1:
            raise ValueError(f"{case}: {side} MPR0/MPR1 {mpr[0]:#x}/{mpr[1]:#x}, "
                             f"want {want_mpr0:#x}/{want_mpr1:#x}")
    if row["src_value"] != (FORBIDDEN_VALUE if variant == "forbidden" else ALLOWED_VALUE):
        raise ValueError(f"{case}: source byte mismatch {row['src_value']:#x}")
    return op, variant


def _expected_mpr(variant):
    """(MPR0, MPR1) the probe configures before and after the run, per variant."""
    if variant == "forbidden":
        return 0xFF, 0xF8
    if variant == "allowed":
        return 0x00, 0xF8
    if variant == "stackff":
        return 0xF8, 0xFF
    if variant == "stackwram":
        return 0xF8, 0xF8
    raise ValueError(f"unknown variant {variant}")


def check_forbidden_armed(row, case):
    """The store must be refused inside the core before the side effect (VDC AR unchanged)."""
    op, _ = _common(row, case)
    block_op, _opcode = OPS[op]
    if row.get("armed") is not True:
        raise ValueError(f"{case}: run is not armed")
    if row["guard_calls"] != 1 or row["guard_refusals"] != 1:
        raise ValueError(f"{case}: guard calls/refusals {row['guard_calls']}/{row['guard_refusals']}, want 1/1")
    if row["guard_block_op"] != block_op or row["guard_dest"] != FORBIDDEN_DST:
        raise ValueError(f"{case}: guard saw op {row['guard_block_op']} dest {row['guard_dest']:#x}, "
                         f"want {block_op}/{FORBIDDEN_DST:#x}")
    if row["guard_ar_at_call"] != row["vdc_ar_before"]:
        raise ValueError(f"{case}: VDC AR changed before the guard call")
    if row["vdc_ar_before"] == FORBIDDEN_VALUE:
        raise ValueError(f"{case}: initial VDC AR already equals the forbidden value; test is not discriminating")
    if row["vdc_ar_after"] != row["vdc_ar_before"]:
        raise ValueError(f"{case}: VDC side effect landed although refused "
                         f"(AR {row['vdc_ar_before']:#x}->{row['vdc_ar_after']:#x})")
    return "refused_inside_core_before_side_effect"


def check_forbidden_unguarded(row, case):
    """Control (null hook): the same store must land in the VDC."""
    op, _ = _common(row, case)
    if row.get("armed") is not False or row["guard_calls"] != 0:
        raise ValueError(f"{case}: unguarded run consulted a guard")
    if row["vdc_ar_after"] != FORBIDDEN_VALUE or row["vdc_ar_before"] == FORBIDDEN_VALUE:
        raise ValueError(f"{case}: VDC side effect did not land without the hook "
                         f"(AR {row['vdc_ar_before']:#x}->{row['vdc_ar_after']:#x})")
    return "side_effect_landed_without_hook"


def check_allowed_armed(row, case):
    """Positive: the guard is consulted once, allows the store, and the byte lands in WRAM."""
    op, _ = _common(row, case)
    block_op, _opcode = OPS[op]
    if row.get("armed") is not True:
        raise ValueError(f"{case}: run is not armed")
    if row["guard_calls"] != 1 or row["guard_refusals"] != 0:
        raise ValueError(f"{case}: guard calls/refusals {row['guard_calls']}/{row['guard_refusals']}, want 1/0")
    if row["guard_block_op"] != block_op or row["guard_dest"] != ALLOWED_DST:
        raise ValueError(f"{case}: guard saw op {row['guard_block_op']} dest {row['guard_dest']:#x}")
    if row["dst_before"] != 0 or row["dst_after"] != ALLOWED_VALUE:
        raise ValueError(f"{case}: allowed store did not land ({row['dst_before']} -> {row['dst_after']})")
    return "allowed_store_landed"


def _stack_block_ok(row, case):
    """Shared stack-layout checks: the guest finished and the block store to WRAM $1E90 landed."""
    if row["guard_block_op"] != OPS[case.split("-")[0]][0] or row["guard_dest"] != ALLOWED_STACK_DST:
        raise ValueError(f"{case}: block guard saw op {row['guard_block_op']} dest {row['guard_dest']:#x}")
    if row["guard_calls"] != 1 or row["guard_refusals"] != 0:
        raise ValueError(f"{case}: block guard calls/refusals {row['guard_calls']}/{row['guard_refusals']}")
    if row["dst_after"] != ALLOWED_VALUE:
        raise ValueError(f"{case}: block store did not land ({row['dst_after']})")
    if row["sp_before"] != STACK_SP:
        raise ValueError(f"{case}: S before the run is {row['sp_before']:#x}, want {STACK_SP:#x}")


def check_stackff_armed(row, case):
    """Issue #459: the TransferStart pushes are refused before the side effect when the stack page is $FF."""
    _common(row, case)
    if row.get("armed") is not True:
        raise ValueError(f"{case}: run is not armed")
    if row["stack_guard_calls"] != 1 or row["stack_guard_refusals"] != 1:
        raise ValueError(f"{case}: stack guard calls/refusals {row['stack_guard_calls']}/"
                         f"{row['stack_guard_refusals']}, want 1/1")
    if row["stack_guard_sp"] != STACK_SP:
        raise ValueError(f"{case}: stack guard saw S {row['stack_guard_sp']:#x}")
    if row["vdc_ar_before"] == STACK_AR_LANDED or row["vdc_r7_before"] == STACK_R7_LANDED:
        raise ValueError(f"{case}: initial VDC state already equals the landed value; not discriminating")
    if row["vdc_ar_after"] != row["vdc_ar_before"] or row["vdc_r7_after"] != row["vdc_r7_before"]:
        raise ValueError(f"{case}: push side effect landed although refused (AR {row['vdc_ar_after']}, "
                         f"R7 {row['vdc_r7_after']:#x})")
    if row["sp_after"] != row["sp_before"]:
        raise ValueError(f"{case}: S not balanced ({row['sp_before']:#x}->{row['sp_after']:#x})")
    _stack_block_ok(row, case)
    return "stack_push_refused_inside_core_before_side_effect"


def check_stackff_unguarded(row, case):
    """Control (null hook): the pushes must land on the VDC (AR and R[7] change)."""
    _common(row, case)
    if row.get("armed") is not False or row["stack_guard_calls"] != 0:
        raise ValueError(f"{case}: unguarded run consulted the stack guard")
    if row["vdc_ar_after"] != STACK_AR_LANDED or row["vdc_r7_after"] != STACK_R7_LANDED:
        raise ValueError(f"{case}: push side effect did not land without the hook "
                         f"(AR {row['vdc_ar_after']}, R7 {row['vdc_r7_after']:#x})")
    return "push_side_effect_landed_without_hook"


def check_stackwram_armed(row, case):
    """Positive: the stack guard is consulted once, allows the pushes, and the bytes land in WRAM."""
    _common(row, case)
    if row.get("armed") is not True:
        raise ValueError(f"{case}: run is not armed")
    if row["stack_guard_calls"] != 1 or row["stack_guard_refusals"] != 0:
        raise ValueError(f"{case}: stack guard calls/refusals {row['stack_guard_calls']}/"
                         f"{row['stack_guard_refusals']}, want 1/0")
    if row["stack_wram_before"] != [0, 0, 0] or row["stack_wram_after"] != STACK_WRAM_LANDED:
        raise ValueError(f"{case}: stack bytes did not land ({row['stack_wram_before']} -> "
                         f"{row['stack_wram_after']})")
    _stack_block_ok(row, case)
    return "stack_push_allowed_bytes_landed"


def mutation_test(row, case):
    """Falsified armed observations must be rejected (proves the classifier can fail)."""
    variant = case.split("-")[1]
    if variant == "stackff":
        mutations = [
            ("pushes_landed", {"vdc_ar_after": STACK_AR_LANDED, "vdc_r7_after": STACK_R7_LANDED}),
            ("no_stack_guard_call", {"stack_guard_calls": 0, "stack_guard_refusals": 0}),
            ("stack_accepted_not_refused", {"stack_guard_refusals": 0}),
            ("stack_wrong_sp", {"stack_guard_sp": STACK_SP + 1}),
            ("sp_unbalanced", {"sp_after": STACK_SP - 3}),
        ]
        check = check_stackff_armed
    elif variant == "stackwram":
        mutations = [
            ("stack_bytes_not_landed", {"stack_wram_after": [0, 0, 0]}),
            ("stack_refused_although_allowed", {"stack_guard_refusals": 1}),
        ]
        check = check_stackwram_armed
    elif variant == "forbidden":
        mutations = [
            ("side_effect_landed", {"vdc_ar_after": FORBIDDEN_VALUE}),
            ("no_guard_call", {"guard_calls": 0, "guard_refusals": 0}),
            ("accepted_not_refused", {"guard_refusals": 0}),
            ("wrong_dest", {"guard_dest": ALLOWED_DST}),
            ("guest_stopped_early", {"stop_pc": GUEST_BASE}),
        ]
        check = check_forbidden_armed
    else:
        mutations = [
            ("store_not_landed", {"dst_after": 0}),
            ("refused_although_allowed", {"guard_refusals": 1}),
        ]
        check = check_allowed_armed
    for name, overwrite in mutations:
        broken = copy.deepcopy(row)
        broken.update(overwrite)
        try:
            check(broken, case)
        except ValueError:
            continue
        raise ValueError(f"mutation test FAILED: {case} {name} accepted by the armed classifier")


def build_probes(directory):
    """Stock probe objects with ONLY src/huc6280_opcodes.o replaced by the patched build."""
    source = dependency_dir("geargrafx").with_name(dependency_dir("geargrafx").name + "-debugger")
    make_dir = source / "platforms/libretro"
    objects = build_command(["make", "-s", "--no-print-directory", "-C", str(make_dir),
                             "--eval", "probe-objects:;@echo $(OBJECTS)", "probe-objects"]).stdout.split()
    objects = [(make_dir / p).resolve() for p in objects]
    if not objects or not all(p.is_file() for p in objects):
        raise ValueError("BLOCKED: official Geargrafx objects missing; run make setup")
    stock = [p for p in objects if p.name == "huc6280_opcodes.o" and p.parent.name == "src"]
    if len(stock) != 1:
        raise ValueError("BLOCKED: expected exactly one src/huc6280_opcodes.o in the link list")
    staged = directory / "patched"
    shutil.copytree(source / "src", staged / "src")
    # ValueError (not CalledProcessError) so the caller reports BLOCKED/FAIL instead of a traceback.
    build_command(["patch", "-p1", "--batch", "--forward", "-d", str(staged), "-i", str(PATCH)])
    # Upstream's own INCLUDES (relative to the libretro dir, so run g++ from there); staged src first.
    includes = build_command(["make", "-s", "--no-print-directory", "-C", str(make_dir),
                              "--eval", "print-includes:;@echo $(INCLUDES)", "print-includes"]).stdout.split()
    flags = [*CXX_EXTRA, *DEBUGGER_BUILD_FLAGS, "-I" + str(staged / "src"), *includes]
    patched_obj = directory / "huc6280_opcodes_block_guard.o"
    build_command(["g++", *flags, "-c", str(staged / "src/huc6280_opcodes.cpp"), "-o", str(patched_obj)],
                  cwd=str(make_dir))
    link = [patched_obj if p == stock[0] else p for p in objects]
    probe = directory / "block-write-guard-probe"
    build_command(["g++", *flags, str(Path(__file__).with_name(PROBE_SOURCE)), *map(str, link),
                   "-lm", "-o", str(probe)], cwd=str(make_dir))
    return probe


def run_case(probe, bios, cue, case):
    command = [str(probe), str(bios), str(cue), case, str(FRAMES)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
    if result.returncode:
        return {"mode": case, "exit": result.returncode, "stderr": result.stderr[-400:]}
    try:
        row = json.loads(result.stderr)
    except ValueError as error:
        raise ValueError(f"{case}: probe emitted no JSON observation") from error
    if type(row) is not dict:
        raise ValueError(f"{case}: observation is not an object")
    row["exit"] = 0
    row["armed"] = case.endswith("-armed")
    return row


def main():
    with tempfile.TemporaryDirectory(prefix="pce-block-write-guard-") as tmp:
        directory = Path(tmp)
        bios, cue = synthetic_media(directory)
        probe = build_probes(directory)
        evidence = []
        for case in _cases():
            row = run_case(probe, bios, cue, case)
            variant = case.split("-")[1]
            if variant == "forbidden" and case.endswith("-armed"):
                verdict = check_forbidden_armed(row, case)
                mutation_test(row, case)
            elif variant == "forbidden":
                verdict = check_forbidden_unguarded(row, case)
            elif variant == "allowed":
                verdict = check_allowed_armed(row, case)
                mutation_test(row, case)
            elif variant == "stackff" and case.endswith("-armed"):
                verdict = check_stackff_armed(row, case)
                mutation_test(row, case)
            elif variant == "stackff":
                verdict = check_stackff_unguarded(row, case)
            else:
                verdict = check_stackwram_armed(row, case)
                mutation_test(row, case)
            evidence.append((case, verdict, row))
            print(f"PASS {case}: {verdict}")
    for case, verdict, row in evidence:
        print("EVIDENCE " + json.dumps({"case": case, "observation_kind": "REAL_CORE_EXECUTION",
                                        "core_revision": PINNED_CORE_REVISION, "bios": "SYNTHETIC",
                                        "hook": "VERIFICATION_ONLY_PATCH", "verdict": verdict,
                                        "row": row}, sort_keys=True))
    print("PASS issue-367 TII/TIN/TIA/TDD/TAI: one-byte transfers only, destination pages mapped to $FF only, "
          "pre-write refusal hook, synthetic media")
    print("PASS issue-459 TransferStart A/X/Y pushes: refused before the side effect only when the stack page "
          "(MPR1) is mapped to $FF; pushes only, TransferStart only, synthetic media")
    print("LIMIT: one-byte transfers only; no mid-instruction stop measured; no device-originated write probe; "
          "block guard refuses only dest pages mapped to $FF (other banks not covered); refusal policy is defined "
          "inside the probe, not a production gate; refusal is silent and the source read is skipped; "
          "#459 scope: the TransferStart A/X/Y pushes only. Other pushes (PHA/PHP/JSR/IRQ), TransferEnd pops "
          "(reads from the $FF stack page, unguarded) and all other writes are NOT guarded; the TransferStart "
          "guard only applies to the stack page; a refused push still moves S; armed flag is set from the case "
          "name; no gate change; no BIOS.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        print(f"FAIL/BLOCKED issue-367 block write guard: {error}", file=sys.stderr)
        raise SystemExit(1) from error
