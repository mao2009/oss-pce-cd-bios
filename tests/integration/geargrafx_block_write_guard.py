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
  must still land. The refusal is NOT a safe termination: TransferEnd still pops three never-stored stack
  slots, so the core reads $2102/$2103/$2104 (VDC data read buffer LSB/MSB and the VDC status register),
  overwrites A/X/Y with the values read and clears the VDC status register. Those facts are measured and
  pinned (see the #459 BLOCKED line printed at the end).
* ``<op>-stackff-unguarded`` (MUTATION, #459): same layout, no hook. The pushes must land on the VDC:
  AR 0 -> 7 and R[7] 0 -> 564 (the A/X bytes $12/$34 masked to BXR's 10 bits). The TransferEnd pops read
  the same VDC registers as in the refused case, which shows the I/O reads come from the core's
  unconditional TransferEnd and not from the guard.
* ``<op>-stackwram-armed`` (POSITIVE, #459): MPR1 = WRAM. The stack guard is consulted once, allows
  the pushes, and the stack bytes X=$34, A=$12, Y=$07 land at WRAM $0102..$0104. This is the differential
  control: A/X/Y survive the TransferEnd pops and the VDC status register is never read.

Scope and limits (read before citing this as evidence):
* The refusal policy (refuse a store whose destination page is mapped to $FF) is written inside the
  probe (geargrafx_block_write_guard.cpp). It is a test policy, NOT a production gate. This test shows
  only that the core's pre-write hook point can refuse a store before its side effect; it does not show
  that a production gate would refuse it.
* The TransferStart push guard is the same kind of test policy. It shows the core has a hook point that
  can refuse the push before the VDC AR/R[7] write happens. It does NOT make the refusal safe: the core
  still pops three never-stored stack slots on TransferEnd, which on a $FF stack page are VDC registers.
  A production safety mechanism (never map the stack page to $FF, or a gate that also covers the pops)
  is required and is NOT implemented or verified here. The gate therefore stays BLOCKED.
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
# Issue #459 follow-up: markers pre-loaded into the VDC before the run so that the TransferEnd pops can be
# attributed to a specific hardware register read. $2102 returns the VDC data read buffer LSB, $2103 the
# MSB and $2104 the status register (reading the status register CLEARS it and de-asserts IRQ1).
STACK_READ_BUFFER_PRESET = 0xBEEF
STACK_STATUS_PRESET = 0x05  # bits the VDC cannot raise on its own at reset (every event-driven bit is gated)
STACK_IO_POP_X = STACK_READ_BUFFER_PRESET & 0xFF        # 0xEF: $2102 = data read buffer LSB
STACK_IO_POP_A = (STACK_READ_BUFFER_PRESET >> 8) & 0xFF  # 0xBE: $2103 = data read buffer MSB
STACK_IO_POP_Y = STACK_STATUS_PRESET & 0x3F              # 0x05: $2104 = status register
STACK_IO_STATUS_CLEARED = 0x00  # the status register read clears the register
STACK_RESTORED_X = 0x34  # X restored from the WRAM stack in the positive control
STACK_RESTORED_A = 0x12
STACK_RESTORED_Y = 0x07
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
        yield f"{op}-safeabort-armed"
        yield f"{op}-safeabort-unguarded"


def _common(row, case):
    if row.get("exit", 0) != 0:
        raise ValueError(f"{case} probe refused to run (exit {row.get('exit')}): {row.get('stderr', '')[-300:]}")
    if row.get("mode") != case:
        raise ValueError(f"{case} measured wrong mode: {row.get('mode')}")
    if row.get("status") != "ok":
        raise ValueError(f"{case}: guest did not reach its end (status={row.get('status')})")
    for name in ("vdc_ar_before", "vdc_ar_after", "vdc_r_before", "vdc_r_after", "stop_pc", "guard_calls",
                 "guard_refusals", "dst_before", "dst_after", "guard_block_op", "guard_dest",
                 "sp_before", "sp_after", "p_before", "p_after", "a_after", "x_after", "y_after",
                 "vdc_sr_before", "vdc_sr_after", "vdc_read_buffer_before", "vdc_read_buffer_after"):
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
    if row["before"]["mpr"] != row["after"]["mpr"]:
        raise ValueError(f"{case}: MPR changed during the run "
                         f"({row['before']['mpr']} -> {row['after']['mpr']})")
    return op, variant


def _final_state_ok(row, case):
    """Final register state after the guest finished: S balanced, PC at the end, P unchanged."""
    if row["sp_after"] != row["sp_before"]:
        raise ValueError(f"{case}: S not balanced ({row['sp_before']:#x}->{row['sp_after']:#x})")
    if row["stop_pc"] != row["expected_end_pc"]:
        raise ValueError(f"{case}: PC ended at {row['stop_pc']:#x}, want {row['expected_end_pc']:#x}")
    if row["p_after"] != row["p_before"]:
        raise ValueError(f"{case}: P changed ({row['p_before']:#x}->{row['p_after']:#x})")


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
    if variant == "safeabort":
        return 0xF8, 0xFF
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
    """Issue #459: the TransferStart pushes are refused before the side effect when the stack page is $FF.

    The refusal is NOT a safe termination: TransferEnd always pops three bytes, and because the push was
    refused those three stack slots were never written. With MPR1 = $FF they are VDC registers, so the core
    reads $2102 (data LSB), $2103 (data MSB) and $2104 (status register) and overwrites X/A/Y with the
    values read. Reading $2104 also clears the VDC status register. The measured facts are pinned here.
    """
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
    if row["stack_wram_after"] != [0, 0, 0]:
        raise ValueError(f"{case}: refused push wrote the WRAM stack image ({row['stack_wram_after']})")
    # Issue #459 follow-up: the refused push leaves three never-stored stack slots. TransferEnd pops them.
    if row["vdc_read_buffer_before"] != STACK_READ_BUFFER_PRESET or row["vdc_sr_before"] != STACK_STATUS_PRESET:
        raise ValueError(f"{case}: VDC markers not in place (read buffer {row['vdc_read_buffer_before']:#x}, "
                         f"status {row['vdc_sr_before']:#x})")
    if row["x_after"] != STACK_IO_POP_X or row["a_after"] != STACK_IO_POP_A or row["y_after"] != STACK_IO_POP_Y:
        raise ValueError(f"{case}: final A/X/Y {row['a_after']:#x}/{row['x_after']:#x}/{row['y_after']:#x} do not "
                         f"match the VDC values read by the TransferEnd pops "
                         f"({STACK_IO_POP_A:#x}/{STACK_IO_POP_X:#x}/{STACK_IO_POP_Y:#x})")
    if row["vdc_sr_after"] != STACK_IO_STATUS_CLEARED:
        raise ValueError(f"{case}: VDC status register was not cleared by the $2104 read "
                         f"({row['vdc_sr_before']:#x}->{row['vdc_sr_after']:#x})")
    _final_state_ok(row, case)
    _stack_block_ok(row, case)
    return "stack_push_refused_but_transferend_pops_read_io_registers"


def check_stackff_unguarded(row, case):
    """Control (null hook): the pushes must land on the VDC (AR and R[7] change).

    The TransferEnd pops read the same VDC registers as in the refused case, so the control also shows that
    the I/O reads come from the core's unconditional TransferEnd, not from the guard.
    """
    _common(row, case)
    if row.get("armed") is not False or row["stack_guard_calls"] != 0:
        raise ValueError(f"{case}: unguarded run consulted the stack guard")
    if row["vdc_ar_after"] != STACK_AR_LANDED or row["vdc_r7_after"] != STACK_R7_LANDED:
        raise ValueError(f"{case}: push side effect did not land without the hook "
                         f"(AR {row['vdc_ar_after']}, R7 {row['vdc_r7_after']:#x})")
    if row["x_after"] != STACK_IO_POP_X or row["a_after"] != STACK_IO_POP_A or row["y_after"] != STACK_IO_POP_Y:
        raise ValueError(f"{case}: final A/X/Y {row['a_after']:#x}/{row['x_after']:#x}/{row['y_after']:#x} do not "
                         f"match the VDC values read by the TransferEnd pops")
    if row["vdc_sr_after"] != STACK_IO_STATUS_CLEARED:
        raise ValueError(f"{case}: VDC status register was not cleared by the $2104 read")
    _final_state_ok(row, case)
    return "push_side_effect_landed_without_hook"


def check_stackwram_armed(row, case):
    """Positive: the stack guard is consulted once, allows the pushes, and the bytes land in WRAM.

    This is the differential control for the $FF stack page: with a WRAM stack the TransferEnd pops read
    back the pushed bytes, so A/X/Y survive and the VDC status register is never read.
    """
    _common(row, case)
    if row.get("armed") is not True:
        raise ValueError(f"{case}: run is not armed")
    if row["stack_guard_calls"] != 1 or row["stack_guard_refusals"] != 0:
        raise ValueError(f"{case}: stack guard calls/refusals {row['stack_guard_calls']}/"
                         f"{row['stack_guard_refusals']}, want 1/0")
    if row["stack_wram_before"] != [0, 0, 0] or row["stack_wram_after"] != STACK_WRAM_LANDED:
        raise ValueError(f"{case}: stack bytes did not land ({row['stack_wram_before']} -> "
                         f"{row['stack_wram_after']})")
    if row["x_after"] != STACK_RESTORED_X or row["a_after"] != STACK_RESTORED_A \
            or row["y_after"] != STACK_RESTORED_Y:
        raise ValueError(f"{case}: A/X/Y not restored from the WRAM stack "
                         f"({row['a_after']:#x}/{row['x_after']:#x}/{row['y_after']:#x}, "
                         f"want {STACK_RESTORED_A:#x}/{STACK_RESTORED_X:#x}/{STACK_RESTORED_Y:#x})")
    if row["vdc_sr_after"] != row["vdc_sr_before"]:
        raise ValueError(f"{case}: VDC status register changed although the stack page is WRAM "
                         f"({row['vdc_sr_before']:#x}->{row['vdc_sr_after']:#x})")
    _final_state_ok(row, case)
    _stack_block_ok(row, case)
    return "stack_push_allowed_bytes_landed"

def check_safeabort_armed(row, case):
    """Whole transfer refused before TransferStart: no push/pop, write, or VDC read side effect."""
    _common(row, case)
    if row.get("armed") is not True or row.get("abort_calls") != 1 or row.get("abort_refusals") != 1:
        raise ValueError(f"{case}: abort hook not consulted/refused")
    if row["stack_guard_calls"] != 0 or row["guard_calls"] != 0:
        raise ValueError(f"{case}: entered TransferStart or per-byte path")
    _final_state_ok(row, case)
    if row["a_after"] != 0x12 or row["x_after"] != 0x34 or row["y_after"] != 0x07:
        raise ValueError(f"{case}: refusal corrupted A/X/Y")
    if row["before"]["mpr"] != row["after"]["mpr"]:
        raise ValueError(f"{case}: refusal changed MPR")
    if row["stack_wram_after"] != row["stack_wram_before"]:
        raise ValueError(f"{case}: refusal changed stack WRAM")
    if row["vdc_ar_after"] != row["vdc_ar_before"] or row["vdc_r7_after"] != row["vdc_r7_before"] or \
            row["vdc_sr_after"] != row["vdc_sr_before"] or row["a_after"] != 0x12 or row["x_after"] != 0x34 or row["y_after"] != 0x07:
        raise ValueError(f"{case}: abort left a VDC/register side effect")
    if row["dst_after"] != 0 or row["sp_after"] != row["sp_before"]:
        raise ValueError(f"{case}: abort wrote destination or stack")
    if row["vdc_read_buffer_after"] != row["vdc_read_buffer_before"]:
        raise ValueError(f"{case}: abort changed VDC read buffer")
    return "whole_instruction_refused_before_transferstart"

def mutation_safeabort_armed(row, case):
    mutations = (
        ("callback_not_called", {"abort_calls": 0}),
        ("callback_allowed", {"abort_refusals": 0}),
        ("transferstart_entered", {"stack_guard_calls": 1}),
        ("per_byte_path_entered", {"guard_calls": 1}),
        ("P_corruption", {"p_after": row["p_before"] ^ 1}),
        ("SP_corruption", {"sp_after": row["sp_after"] - 1}),
        ("PC_mismatch", {"stop_pc": row["stop_pc"] - 1}),
        ("A_corruption", {"a_after": row["a_after"] ^ 1}),
        ("MPR_change", {"after": {"mpr": [0] * 8}}),
        ("destination_write", {"dst_after": ALLOWED_VALUE}),
        ("stack_WRAM_write", {"stack_wram_after": [1, 0, 0]}),
        ("VDC_status_clear", {"vdc_sr_after": row["vdc_sr_before"] ^ 1}),
    )
    for name, changes in mutations:
        broken = copy.deepcopy(row); broken.update(changes)
        try: check_safeabort_armed(broken, case)
        except ValueError: continue
        raise ValueError(f"mutation test FAILED: {case} {name}")

def check_safeabort_allowed(row, case, null_hook_control):
    """Non-null allowing callback must preserve the null-hook baseline exactly."""
    _common(row, case)
    if row.get("armed") is not False or row.get("abort_calls") != 1 or row.get("abort_refusals") != 0:
        raise ValueError(f"{case}: allowing abort callback was not called/allowed")
    if type(null_hook_control) is not dict:
        raise ValueError(f"{case}: null-hook positive-control observation missing")
    # Compare independent real-core rows from identical guest/MPR/register setup.
    compare_fields = ("opcode", "dest", "src_value", "stop_pc", "sp_before", "sp_after",
                      "p_before", "p_after", "a_after", "x_after", "y_after",
                      "vdc_ar_before", "vdc_ar_after", "vdc_r_before", "vdc_r_after",
                      "vdc_r7_before", "vdc_r7_after", "vdc_sr_before", "vdc_sr_after",
                      "vdc_read_buffer_before", "vdc_read_buffer_after", "dst_before", "dst_after",
                      "src_after", "stack_wram_before", "stack_wram_after", "before", "after")
    for field in compare_fields:
        if row.get(field) != null_hook_control.get(field):
            raise ValueError(f"{case}: allowing callback changed {field} from null-hook control")
    verdict = check_stackff_unguarded(row, case)
    if row["dst_after"] != ALLOWED_VALUE:
        raise ValueError(f"{case}: allowing callback skipped the normal transfer")
    return verdict

def mutation_safeabort_allowed(row, case, null_hook_control):
    mutations = (("abort_calls", 0), ("abort_refusals", 1), ("dst_after", 0),
                 ("vdc_ar_after", 0), ("sp_after", row["sp_after"] - 1),
                 ("a_after", row["a_after"] ^ 1), ("stack_wram_after", [1, 0, 0]))
    for key, value in mutations:
        broken = copy.deepcopy(row); broken[key] = value
        try: check_safeabort_allowed(broken, case, null_hook_control)
        except ValueError: continue
        raise ValueError(f"mutation test FAILED: {case} {key}")


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
            # Issue #459 follow-up: the classifier must pin the traced post-refusal state.
            ("registers_restored_instead_of_io_pops",
             {"x_after": STACK_RESTORED_X, "a_after": STACK_RESTORED_A, "y_after": STACK_RESTORED_Y}),
            ("io_pop_values_wrong", {"x_after": 0x00, "a_after": 0x00, "y_after": 0x00}),
            ("status_register_not_cleared", {"vdc_sr_after": STACK_STATUS_PRESET}),
            ("p_changed", {"p_after": row["p_before"] ^ 0x01}),
            ("pc_wrong", {"stop_pc": GUEST_BASE}),
            ("mpr_changed", {"after": {"mpr": [0] * 8}}),
        ]
        check = check_stackff_armed
    elif variant == "stackwram":
        mutations = [
            ("stack_bytes_not_landed", {"stack_wram_after": [0, 0, 0]}),
            ("stack_refused_although_allowed", {"stack_guard_refusals": 1}),
            ("registers_not_restored",
             {"x_after": STACK_IO_POP_X, "a_after": STACK_IO_POP_A, "y_after": STACK_IO_POP_Y}),
            ("status_register_cleared", {"vdc_sr_after": STACK_IO_STATUS_CLEARED}),
            ("sp_unbalanced", {"sp_after": STACK_SP - 3}),
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
        null_hook_controls = {}
        for case in _cases():
            row = run_case(probe, bios, cue, case)
            op_name = case.split("-")[0]
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
                null_hook_controls[op_name] = row
            else:
                verdict = (check_safeabort_armed(row, case) if case.endswith("-armed") else
                           check_safeabort_allowed(row, case, null_hook_controls.get(op_name))) if variant == "safeabort" else check_stackwram_armed(row, case)
                if variant != "safeabort":
                    mutation_test(row, case)
                elif case.endswith("-armed"):
                    mutation_safeabort_armed(row, case)
                else:
                    mutation_safeabort_allowed(row, case, null_hook_controls.get(op_name))
            evidence.append((case, verdict, row))
            print(f"PASS {case}: {verdict}")
    for case, verdict, row in evidence:
        print("EVIDENCE " + json.dumps({"case": case, "observation_kind": "REAL_CORE_EXECUTION",
                                        "core_revision": PINNED_CORE_REVISION, "bios": "SYNTHETIC",
                                        "hook": "VERIFICATION_ONLY_PATCH", "verdict": verdict,
                                        "row": row}, sort_keys=True))
    print("PASS issue-367 TII/TIN/TIA/TDD/TAI: one-byte $FF pre-write refusal plus verification-only "
           "whole-instruction TransferStart abort, synthetic media")
    print("PASS issue-459 TransferStart A/X/Y pushes: the WRITE side effect is refused before it happens only "
          "when the stack page (MPR1) is mapped to $FF; pushes only, TransferStart only, synthetic media")
    print("BLOCKED issue-459: the refusal path is NOT a safe termination. TransferEnd unconditionally pops "
          "three stack slots, and a refused push leaves those three slots never written, so on a $FF stack "
          "page the core READS $2102/$2103/$2104 (VDC data read buffer LSB/MSB and the VDC status register), "
          "overwrites A/X/Y with the values read and clears the VDC status register (IRQ1 de-asserted). "
          "Measured on the real core: A/X/Y = $BE/$EF/$05 (original $12/$34/$07 lost), VDC status $05 -> $00, "
          "S balanced at $04, P unchanged, PC and MPR unchanged. The block transfer itself also still runs "
          "(destination byte $5A lands), so the refusal cannot stop the instruction either. A production "
          "mechanism is required (do not map the stack page to $FF, or a gate that also covers the TransferEnd "
          "pops); the VERIFICATION-ONLY patch used here is not a production mechanism and the gate stays "
          "BLOCKED. Nothing here changes BLOCKED_ABI or release_eligible.")
    print("LIMIT: one-byte transfers only; no mid-instruction stop measured; no device-originated write probe; "
          "block guard refuses only dest pages mapped to $FF (other banks not covered); refusal policy is defined "
          "inside the probe, not a production gate; refusal is silent and the source read is skipped; "
          "#459 scope: the TransferStart A/X/Y pushes only. Other pushes (PHA/PHP/JSR/IRQ), TransferEnd pops "
          "(reads from the $FF stack page, unguarded) and all other writes are NOT guarded; the TransferStart "
          "guard only applies to the stack page; a refused push still moves S and the following TransferEnd "
          "still pops the never-stored slots (measured above, see the #459 BLOCKED line); armed flag is set "
          "from the case name; no gate change; no BIOS.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        print(f"FAIL/BLOCKED issue-367 block write guard: {error}", file=sys.stderr)
        raise SystemExit(1) from error
