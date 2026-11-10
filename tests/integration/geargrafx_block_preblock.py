#!/usr/bin/env python3
"""Issue #742: whole-instruction preblock for TII/TDD/TIN/TAI/TIA on the pinned real core.

Pinned real Geargrafx instruction-debugger core (local GPL-3.0 link, b49ae82a012eade566d72e9d61c9d95a5caa4da3;
no core code copied here), fully synthetic BIOS/CD, MPR0 fixed to $00 (never $FF).

The probe (geargrafx_block_preblock.cpp) single-steps one instruction at a time and resolves the opcode at
the current PC BEFORE every step (including the entry instruction) through the core's side-effect-free
peek (Memory::TryPeek). Any block-transfer opcode ($73/$C3/$D3/$E3/$F3) is refused before its first
step: no source byte is read and no destination byte is written. The 7 opcode+operand bytes are
captured side-effect-free as refusal evidence. A resume attempt re-runs the same boundary check and
is refused again. The monitor is given no PC hint: every boundary of the single-stepped run is checked.

Classifier design (composite conditions, never a single stop-PC number):
* Executed-instruction count: the transfer itself must not start (exact setup counts per family).
* VDC AR/R/SR: an armed run leaves them unchanged (sentinels preset for VDC-touching families).
* Stop PC: the refused PC is exact-matched against the independent family expectation.
* RAM: no net WRAM diff except the 3 architectural stack pushes for the IRQ-handler family.
* MPR: unchanged except the guest-driven TAM the family plan requires (recorded, never trusted).
* Operands: 7 bytes captured, complete, opcode matches the refused opcode.
* Resume: 3/3 refused, no counter/PC/VDC/RAM change across attempts.

Modes (7 armed families + positive control):
* ``tii-entry``: TII at entry, 0 executed before refusal.
* ``tdd-unknown``: JMP reaches TDD at unknown $3E10.
* ``tin-unknown-far``: JMP reaches TIN at unknown $3E20.
* ``tia-wrap-forbidden``: guest maps MPR2=$FF; TIA dest alternates into the VDC page.
* ``tai-mpr2-unknown``: guest TAM sets MPR2=$F8; TAI at $4010 through page 2.
* ``tii-hwpage-fetch-blocked``: guest TAM sets MPR2=$FF; fetch at $4000 fails closed.
* ``block-irq-handler``: IRQ2 accepted at an instruction boundary; handler-head TII refused.
* ``pos-write``: positive control, allowlisted RAM store lands unflagged.
Mutation suffixes: -unguarded, -no-entry-check, -no-recheck, -wrong-opcode (TAI omitted),
-knownpc-only, -resume-allowed.

Run: python3 tests/integration/geargrafx_block_preblock.py  (wired into `make block-preblock`)
"""
import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "tools"))
from geargrafx_api import compile_probe  # noqa: E402
from geargrafx_reference_guard import check_pos, run_mode, synthetic_media  # noqa: E402

PINNED_CORE_REVISION = "b49ae82a012eade566d72e9d61c9d95a5caa4da3"
POS_CONTROL = 0x3E50
RESUME_ATTEMPTS = 3
REFUSED_VERDICT = "refused_at_instruction_boundary_before_side_effect"
HWPAGE_BLOCKED_VERDICT = "hardware_page_fetch_failed_closed_before_step"
LANDED_VERDICT = "side_effect_landed_monitor_disabled"

BLOCK_OPCODE = {"tii": 0x73, "tdd": 0xC3, "tin": 0xD3, "tai": 0xF3, "tia": 0xE3}
# Linear fixture fill: F(addr) = (0x20 + (addr - 0x3E40)) & 0xFF over $3E40-$3E9F,
# dst $3E90-$3E93 preset to $CC. F(80..83) = 60..63, F(7D..7F) = 5D..5F.
PATTERN = [0x60, 0x61, 0x62, 0x63]
DST_PRESET = 0xCC

BLOCK_EXPECTED_PC = {
    "tii-entry": 0x3E00,
    "tdd-unknown": 0x3E10,
    "tin-unknown-far": 0x3E20,
    "tia-wrap-forbidden": 0x3E04,
    "tai-mpr2-unknown": 0x4010,
    "tii-hwpage-fetch-blocked": 0x4000,
    "block-irq-handler": 0x3F80,
}
BLOCK_EXECUTED_BEFORE_REFUSAL = {
    "tii-entry": 0,
    "tdd-unknown": 1,
    "tin-unknown-far": 1,
    "tia-wrap-forbidden": 2,
    "tai-mpr2-unknown": 3,
    "tii-hwpage-fetch-blocked": 3,
    "block-irq-handler": 2,
}
BLOCK_OP = {
    "tii-entry": 0x73,
    "tdd-unknown": 0xC3,
    "tin-unknown-far": 0xD3,
    "tia-wrap-forbidden": 0xE3,
    "tai-mpr2-unknown": 0xF3,
    "tii-hwpage-fetch-blocked": 0x73,
    "block-irq-handler": 0x73,
}
# Expected landed transfer trace per base mode: (address, before, after).
# before comes from the linear fixture ($3E40-$3E9F fill, dst $3E90-$3E93
# preset to $CC); after is the transferred pattern byte. The probe emits a
# physical-WRAM net diff, so a landed run must contain every triple below
# (an IRQ run additionally carries the three architectural stack pushes).
BLOCKPRE_LANDED_TRACE = {
    "tii-entry": [((0x3E90 + i), 0xCC, value) for i, value in enumerate(PATTERN)],
    "tdd-unknown": [(0x3E90, 0xCC, 0x60), (0x3E8F, 0x6F, 0x5F),
                    (0x3E8E, 0x6E, 0x5E), (0x3E8D, 0x6D, 0x5D)],
    "tin-unknown-far": [(0x3E90, 0xCC, 0x63)],
    "tai-mpr2-unknown": [(0x3E90, 0xCC, 0x60), (0x3E91, 0xCC, 0x61),
                         (0x3E92, 0xCC, 0x60), (0x3E93, 0xCC, 0x61)],
    "block-irq-handler": [((0x3E90 + i), 0xCC, value) for i, value in enumerate(PATTERN)],
}
HWPAGE_MODES = ("tii-hwpage-fetch-blocked",)
ARMED_MODES = tuple(BLOCK_EXPECTED_PC)
MUTATIONS = {
    "tii-entry": ("-unguarded", "-no-entry-check", "-resume-allowed"),
    "tdd-unknown": ("-unguarded", "-resume-allowed"),
    "tin-unknown-far": ("-unguarded", "-knownpc-only"),
    "tia-wrap-forbidden": ("-unguarded", "-knownpc-only"),
    "tai-mpr2-unknown": ("-unguarded", "-no-recheck", "-wrong-opcode"),
    "tii-hwpage-fetch-blocked": ("-unguarded",),
    "block-irq-handler": ("-unguarded",),
}


def _int_fields(row, names):
    for name in names:
        value = row.get(name)
        if type(value) is not int:
            raise ValueError(f"{row.get('mode')}: {name} missing/non-integer: {value!r}")


def _require_ok_exit(row, mode):
    """Fail-closed exit validation: only the integer 0 is accepted.

    A missing field, None, a bool, or a numeric string must never be
    treated as success (``row.get("exit", 0)`` would accept missing as 0
    and ``False == 0`` would accept False as 0).
    """
    exit_code = row.get("exit")
    if type(exit_code) is not int or exit_code != 0:
        raise ValueError(f"{mode}: probe refused to run (exit {exit_code!r})")


def _require_mpr_bytes(mpr, mode, label):
    if type(mpr) is not list or len(mpr) != 8:
        raise ValueError(f"{mode}: {label} MPR is not an 8-entry list: {mpr!r}")
    for value in mpr:
        if type(value) is not int or not 0 <= value <= 0xFF:
            raise ValueError(f"{mode}: {label} MPR entry not a byte: {value!r}")
    return mpr


# Measured synthetic-environment mapping (tests/integration/geargrafx_block_preblock.cpp:
# host sets MPR1=$F8, MPR0/MPR2-MPR7=$00; only the guest TAM changes MPR2).
MPR_INITIAL = [0x00, 0xF8, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]
MPR_AFTER_TIA_WRAP = [0x00, 0xF8, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x00]
MPR_AFTER_TAI_MPR2 = [0x00, 0xF8, 0xF8, 0x00, 0x00, 0x00, 0x00, 0x00]


def _check_mpr_transition(row, mode, expected_before, expected_after):
    """Validate the full 8-byte MPR before/after against the measured plan.

    Only the guest-driven TAM may change an entry (MPR2 for the wrap/mpr2/hwpage
    families); every other MPR entry must be stable and byte-typed.
    """
    before = row.get("before", {}).get("mpr") if isinstance(row.get("before"), dict) else None
    after = row.get("after", {}).get("mpr") if isinstance(row.get("after"), dict) else None
    _require_mpr_bytes(before, mode, "before")
    _require_mpr_bytes(after, mode, "after")
    if before != expected_before:
        raise ValueError(f"{mode}: MPR before {before} != measured {expected_before}")
    if after != expected_after:
        raise ValueError(f"{mode}: MPR after {after} != measured {expected_after}")
    return before, after


def check_armed(row, mode):
    """Validate a refused whole-instruction preblock run."""
    _require_ok_exit(row, mode)
    # Strict integer evidence first: later comparisons format these with #x,
    # so a missing/wrong-typed field must fail here with a clear ValueError
    # instead of a TypeError from the format machinery.
    _int_fields(row, ("refused_opcode", "fetch_opcode", "refused_pc", "stop_pc",
                       "instructions_executed", "executed_at_refusal",
                       "executed_after_resume", "resume_attempts", "resume_refused"))
    if row.get("status") != "refused" or row.get("reason") != "block_opcode_at_instruction_boundary":
        raise ValueError(f"{mode}: not a block refusal: {row.get('status')}/{row.get('reason')}")
    if row.get("mode") != mode:
        raise ValueError(f"{mode}: measured wrong mode {row.get('mode')!r}")
    if row.get("armed") is not True:
        raise ValueError(f"{mode}: not an armed run")
    expected_op = BLOCK_OP[mode]
    if row.get("refused_opcode") != expected_op or row.get("fetch_opcode") != expected_op:
        raise ValueError(f"{mode}: refused opcode {row.get('refused_opcode'):#x} "
                         f"(fetch {row.get('fetch_opcode'):#x}), expected {expected_op:#x}")
    if row.get("refused_pc") != BLOCK_EXPECTED_PC[mode]:
        raise ValueError(f"{mode}: refused PC {row.get('refused_pc'):#x}, "
                         f"expected {BLOCK_EXPECTED_PC[mode]:#x}")
    if row.get("stop_pc") != BLOCK_EXPECTED_PC[mode]:
        raise ValueError(f"{mode}: stop PC {row.get('stop_pc'):#x} is not the refused PC")
    if row.get("instructions_executed") != BLOCK_EXECUTED_BEFORE_REFUSAL[mode]:
        raise ValueError(f"{mode}: executed {row.get('instructions_executed')}, "
                         f"expected {BLOCK_EXECUTED_BEFORE_REFUSAL[mode]}")
    if row.get("fetch_safe") is not True or row.get("fetch_failed") is not False:
        raise ValueError(f"{mode}: fetch was not a safe peek")
    operands = row.get("operand_bytes")
    if not isinstance(operands, list) or len(operands) != 7:
        raise ValueError(f"{mode}: operand evidence missing/incomplete: {operands!r}")
    if row.get("operand_complete") is not True:
        raise ValueError(f"{mode}: operand capture incomplete: missing evidence, not PASS")
    if operands[0] != expected_op:
        raise ValueError(f"{mode}: operand byte 0 {operands[0]:#x} != refused opcode")
    _int_fields(row, ("stop_pc", "executed_at_refusal", "vdc_ar_before", "vdc_ar_after",
                      "vdc_sr_before", "vdc_sr_after", "vdc_r_before", "vdc_r_after",
                      "vdc_read_buffer_before", "vdc_read_buffer_after"))
    if row.get("vdc_ar_before") != row.get("vdc_ar_after"):
        raise ValueError(f"{mode}: VDC AR changed {row.get('vdc_ar_before'):#x} -> "
                         f"{row.get('vdc_ar_after'):#x}: side effect before stop")
    if row.get("vdc_sr_before") != row.get("vdc_sr_after"):
        raise ValueError(f"{mode}: VDC SR changed: side effect before stop")
    if row.get("vdc_r_before") != row.get("vdc_r_after"):
        raise ValueError(f"{mode}: VDC R0 changed: side effect before stop")
    if row.get("vdc_read_buffer_before") != row.get("vdc_read_buffer_after"):
        raise ValueError(f"{mode}: VDC READ_BUFFER changed "
                         f"{row.get('vdc_read_buffer_before'):#x} -> "
                         f"{row.get('vdc_read_buffer_after'):#x}: side effect before stop")
    if row.get("executed_at_refusal") != BLOCK_EXECUTED_BEFORE_REFUSAL[mode]:
        raise ValueError(f"{mode}: executed_at_refusal mismatch")
    # No transfer bytes may have moved: WRAM net diff must be empty (except the
    # 3 architectural stack pushes for the IRQ-handler family).
    if mode == "block-irq-handler":
        diffs = row.get("ram_diff")
        if not isinstance(diffs, list):
            raise ValueError(f"{mode}: ram_diff missing")
        for entry in diffs:
            if not isinstance(entry, dict) or entry.get("address") not in (0x21FF, 0x21FE, 0x21FD):
                raise ValueError(f"{mode}: non-stack RAM diff: transfer bytes moved")
        if row.get("only_stack_net_diffs") is not True:
            raise ValueError(f"{mode}: stack-only flag not set")
        if row.get("irq_accepted") is not True or row.get("observed_vector") != 0x3F80:
            raise ValueError(f"{mode}: IRQ acceptance not observed")
        if row.get("vector_read") is not True:
            raise ValueError(f"{mode}: IRQ vector claimed but never read")
        if row.get("handler_steps") != 0:
            raise ValueError(f"{mode}: handler stepped despite refusal")
    elif row.get("ram_diff") != []:
        raise ValueError(f"{mode}: RAM diff {row.get('ram_diff')}: transfer bytes moved")
    if mode != "block-irq-handler":
        # No handler ran outside the IRQ family: the step counter must read
        # exactly 0 (missing/wrong-type is missing evidence, never a pass).
        steps = row.get("handler_steps")
        if type(steps) is not int:
            raise ValueError(f"{mode}: handler_steps missing/non-integer: {steps!r}")
        if steps != 0:
            raise ValueError(f"{mode}: handler stepped despite refusal")
    # MPR: unchanged except the guest-driven TAM the family plan requires.
    # Full 8-byte before/after validation: only the expected MPR2 change is
    # allowed; any other MPR change is a side effect before the stop.
    if mode == "tia-wrap-forbidden":
        _check_mpr_transition(row, mode, MPR_INITIAL, MPR_AFTER_TIA_WRAP)
    elif mode == "tai-mpr2-unknown":
        _check_mpr_transition(row, mode, MPR_INITIAL, MPR_AFTER_TAI_MPR2)
    elif mode == "block-irq-handler":
        _check_mpr_transition(row, mode, MPR_INITIAL, MPR_INITIAL)
    else:
        _check_mpr_transition(row, mode, MPR_INITIAL, MPR_INITIAL)
    # Resume refusals through the same guarded entry.
    if row.get("resume_attempts") != RESUME_ATTEMPTS or row.get("resume_refused") != RESUME_ATTEMPTS:
        raise ValueError(f"{mode}: resume {row.get('resume_refused')}/{row.get('resume_attempts')}")
    if row.get("executed_after_resume") != row.get("executed_at_refusal"):
        raise ValueError(f"{mode}: execution advanced across resume")
    if row.get("ram_unchanged_during_resume") is not True:
        raise ValueError(f"{mode}: RAM changed across resume")
    return REFUSED_VERDICT


def _require_landed_trace(row, mode, expected=None):
    """The landed transfer bytes must be present with exact before/after.

    Non-empty is not enough: stack-only diffs, wrong destinations, or
    forged values never count as a landed transfer. IRQ runs may carry the
    three architectural stack pushes in addition to the transfer triples.
    For tia-wrap the expected trace is the measured single stack-cell delta
    (the transfer itself lands on the VDC, checked by the caller).
    """
    if expected is None:
        expected = BLOCKPRE_LANDED_TRACE[mode]
    diffs = row.get("ram_diff")
    if not isinstance(diffs, list) or not diffs:
        raise ValueError(f"{mode}: landed run left no RAM trace")
    if row.get("ram_diff_truncated") is not False:
        raise ValueError(f"{mode}: truncated diff is not a complete trace")
    if type(row.get("ram_diff_count")) is not int:
        raise ValueError(f"{mode}: ram_diff_count missing/non-integer")
    if row.get("ram_diff_count") != len(diffs):
        raise ValueError(f"{mode}: ram_diff_count does not match entries")
    seen = set()
    for entry in diffs:
        if not isinstance(entry, dict):
            raise ValueError(f"{mode}: malformed ram_diff entry: {entry!r}")
        address, before, after = entry.get("address"), entry.get("before"), entry.get("after")
        if type(address) is not int or not 0x2000 <= address <= 0x3FFF:
            raise ValueError(f"{mode}: ram_diff address out of WRAM: {address!r}")
        if type(before) is not int or not 0 <= before <= 0xFF:
            raise ValueError(f"{mode}: ram_diff before not a byte: {before!r}")
        if type(after) is not int or not 0 <= after <= 0xFF:
            raise ValueError(f"{mode}: ram_diff after not a byte: {after!r}")
        if before == after:
            raise ValueError(f"{mode}: ram_diff entry is not a change: {address:#x}")
        seen.add((address, before, after))
    for triple in expected:
        if triple not in seen:
            raise ValueError(f"{mode}: expected transfer byte missing: "
                             f"${triple[0]:04X} ${triple[1]:02X}->${triple[2]:02X}")
    # Stack cells alone are never a transfer: the transfer triples above are
    # mandatory even when an IRQ run also pushes the stack.
    return seen


def check_unguarded_landed(row, mode):
    """Validate the sensitivity control: bypassed/unmonitored, the transfer lands.

    Pure -unguarded runs have armed=false; bypass mutations (-no-entry-check,
    -no-recheck, -wrong-opcode, -knownpc-only, -resume-allowed) run with the
    monitor armed but a check removed. Either way the transfer must land and
    the run must halt cleanly: that is the sensitivity proof. A timed-out,
    refused, or fetch-blocked run is never a landed proof, no matter what the
    VDC/RAM trace shows.
    """
    _require_ok_exit(row, mode)
    # Common landed-run gates first: only a clean halt counts. In particular
    # budget_exhausted and fetch_blocked can never pass as landed.
    if not isinstance(row.get("status"), str):
        raise ValueError(f"{mode}: status missing/non-string: {row.get('status')!r}")
    if row.get("status") != "ok":
        raise ValueError(f"{mode}: landed run did not halt cleanly: {row.get('status')!r}")
    if row.get("reason") != "":
        if not isinstance(row.get("reason"), str):
            raise ValueError(f"{mode}: halt reason missing/non-string: {row.get('reason')!r}")
        raise ValueError(f"{mode}: unexpected halt reason: {row.get('reason')!r}")
    if row.get("refused") is not False:
        raise ValueError(f"{mode}: bypassed run was refused: monitor still armed")
    full = row.get("mode")
    # Only the probe's own case ids are accepted: the base mode itself (host
    # fixtures) or base plus one of its declared mutation suffixes. A bare
    # prefix match would accept forged ids like "tii-entry-evil".
    allowed = {mode} | {mode + suffix for suffix in MUTATIONS.get(mode, ())}
    if not isinstance(full, str) or full not in allowed:
        raise ValueError(f"{mode}: measured wrong mode {full!r}")
    if row.get("armed") is not (not full.endswith("-unguarded")):
        raise ValueError(f"{mode}: armed flag inconsistent with {full!r}")
    _int_fields(row, ("instructions_executed", "stop_pc"))
    if row["instructions_executed"] <= BLOCK_EXECUTED_BEFORE_REFUSAL[mode]:
        raise ValueError(f"{mode}: landed run stopped at the boundary anyway")
    # The transfer is one 7-byte block instruction: it runs through and the
    # trailing BRA * halts exactly 7 past the refused PC.
    if row["stop_pc"] != BLOCK_EXPECTED_PC[mode] + 7:
        raise ValueError(f"{mode}: unexpected halt PC ${row['stop_pc']:04X}")
    if mode == "tia-wrap-forbidden":
        # The forbidden-destination wrap lands on the VDC, not RAM. Measured
        # contract (pinned core, preset AR=5/SR=0x3F): the transfer moves AR
        # 5 -> 2, SR stays 0x3F, R and the read buffer are unchanged, and the
        # only WRAM delta is the single stack cell 0x21FE 0x00 -> 0xFF.
        _int_fields(row, ("vdc_ar_before", "vdc_ar_after",
                          "vdc_sr_before", "vdc_sr_after",
                          "vdc_r_before", "vdc_r_after",
                          "vdc_read_buffer_before", "vdc_read_buffer_after"))
        if row.get("vdc_ar_before") != 5 or row.get("vdc_ar_after") != 2:
            raise ValueError(f"{mode}: VDC AR not the measured 5 -> 2 transfer trace")
        if row.get("vdc_sr_before") != 0x3F or row.get("vdc_sr_after") != 0x3F:
            raise ValueError(f"{mode}: VDC SR disturbed without a step")
        if row.get("vdc_r_before") != row.get("vdc_r_after"):
            raise ValueError(f"{mode}: VDC R0 changed without any step")
        if row.get("vdc_read_buffer_before") != row.get("vdc_read_buffer_after"):
            raise ValueError(f"{mode}: VDC READ_BUFFER changed without any step")
        _require_landed_trace(row, mode, [(0x21FE, 0x00, 0xFF)])
        if len(row["ram_diff"]) != 1:
            raise ValueError(f"{mode}: extra RAM change unrelated to the VDC transfer")
        return LANDED_VERDICT
    _require_landed_trace(row, mode)
    landed = {(d["address"], d["after"]) for d in row["ram_diff"]
              if isinstance(d, dict) and "address" in d and "after" in d}
    if mode in ("tii-entry", "block-irq-handler"):
        for i, value in enumerate(PATTERN):
            if (0x3E90 + i, value) not in landed:
                raise ValueError(f"{mode}: dest byte ${0x3E90 + i:04X} != pattern: {landed}")
    elif mode == "tdd-unknown":
        for addr, value in ((0x3E90, 0x60), (0x3E8F, 0x5F), (0x3E8E, 0x5E), (0x3E8D, 0x5D)):
            if (addr, value) not in landed:
                raise ValueError(f"{mode}: TDD dest ${addr:04X} != {value:#x}: {landed}")
    elif mode == "tin-unknown-far":
        if (0x3E90, 0x63) not in landed:
            raise ValueError(f"{mode}: TIN fixed dest did not land last byte: {landed}")
    elif mode == "tai-mpr2-unknown":
        for addr, value in ((0x3E90, 0x60), (0x3E91, 0x61), (0x3E92, 0x60), (0x3E93, 0x61)):
            if (addr, value) not in landed:
                raise ValueError(f"{mode}: TAI dest ${addr:04X} != {value:#x}: {landed}")
    return LANDED_VERDICT


def check_resume_allowed_landed(row, mode):
    """Refusal flag raised at first, but the latch does not hold the stop: the
    initial run steps through and the transfer lands anyway."""
    _require_ok_exit(row, mode)
    if row.get("refused") is not True:
        raise ValueError(f"{mode}: resume-allowed never refused at first")
    if row.get("armed") is not True:
        raise ValueError(f"{mode}: resume-allowed run was not monitored")
    if row.get("status") != "ok":
        raise ValueError(f"{mode}: resume-allowed run did not halt cleanly: "
                         f"{row.get('status')!r}")
    if row.get("reason") != "block_opcode_at_instruction_boundary":
        raise ValueError(f"{mode}: unexpected resume-allowed reason: {row.get('reason')!r}")
    _int_fields(row, ("refused_pc", "stop_pc", "resume_refused", "resume_attempts",
                      "instructions_executed", "executed_at_refusal",
                      "executed_after_resume"))
    if row.get("refused_pc") != BLOCK_EXPECTED_PC[mode]:
        raise ValueError(f"{mode}: refusal PC mismatch")
    if row.get("resume_refused") != 0:
        raise ValueError(f"{mode}: resume unexpectedly refused")
    if row.get("resume_attempts") != RESUME_ATTEMPTS:
        raise ValueError(f"{mode}: resume attempts != {RESUME_ATTEMPTS}")
    if row.get("stop_pc") != BLOCK_EXPECTED_PC[mode] + 7:
        raise ValueError(f"{mode}: unexpected halt PC ${row.get('stop_pc'):04X}")
    if row.get("instructions_executed") <= BLOCK_EXECUTED_BEFORE_REFUSAL[mode]:
        raise ValueError(f"{mode}: resumed run did not execute past the boundary")
    # The refusal snapshot is taken when the initial run stops (before the 3
    # resume steps); the final count advances by exactly those 3 steps.
    if row.get("executed_at_refusal") != row.get("instructions_executed") - RESUME_ATTEMPTS:
        raise ValueError(f"{mode}: executed_at_refusal mismatch")
    if row.get("executed_after_resume") != row.get("instructions_executed"):
        raise ValueError(f"{mode}: executed_after_resume mismatch")
    _require_landed_trace(row, mode)
    if mode == "tii-entry":
        landed = {(d["address"], d["after"]) for d in row["ram_diff"]}
        for i, value in enumerate(PATTERN):
            if (0x3E90 + i, value) not in landed:
                raise ValueError(f"{mode}: resumed dest byte ${0x3E90 + i:04X} != pattern")
    elif mode == "tdd-unknown":
        landed = {(d["address"], d["after"]) for d in row["ram_diff"]}
        for addr, value in ((0x3E90, 0x60), (0x3E8F, 0x5F), (0x3E8E, 0x5E), (0x3E8D, 0x5D)):
            if (addr, value) not in landed:
                raise ValueError(f"{mode}: resumed TDD dest ${addr:04X} != {value:#x}")
    else:
        raise ValueError(f"{mode}: resume-allowed is only defined for tii/tdd")
    return LANDED_VERDICT


def check_hwpage_armed(row, mode):
    """Validate the fail-closed fetch: no step issued at the unpeekable PC."""
    _require_ok_exit(row, mode)
    if row.get("mode") != mode:
        raise ValueError(f"{mode}: measured wrong mode {row.get('mode')!r}")
    if row.get("armed") is not True:
        raise ValueError(f"{mode}: not an armed run")
    if row.get("refused") is not False:
        raise ValueError(f"{mode}: hwpage fetch must fail closed, not refuse an opcode")
    if row.get("status") != "fetch_blocked" or row.get("fetch_blocked") is not True:
        raise ValueError(f"{mode}: fetch was not failed-closed: {row.get('status')}")
    if row.get("reason") != "unsafe_opcode_fetch_fail_closed":
        raise ValueError(f"{mode}: unexpected fail-closed reason: {row.get('reason')!r}")
    if row.get("fetch_failed") is not True or row.get("fetch_safe") is not False:
        raise ValueError(f"{mode}: fetch failure not observed")
    # Fail-closed rows never refuse an opcode: no capture, peek path recorded.
    _int_fields(row, ("refused_opcode",))
    if row.get("refused_opcode") != 0:
        raise ValueError(f"{mode}: fail-closed row captured an opcode")
    if not isinstance(row.get("operand_bytes"), list) or row.get("operand_bytes") != []:
        raise ValueError(f"{mode}: fail-closed row carries operand bytes")
    if row.get("operand_complete") is not False:
        raise ValueError(f"{mode}: fail-closed row claims a complete capture")
    if row.get("fetch_via") != "peek":
        raise ValueError(f"{mode}: unexpected fetch path: {row.get('fetch_via')!r}")
    _int_fields(row, ("stop_pc", "instructions_executed",
                      "executed_at_refusal", "executed_after_resume",
                      "resume_attempts", "resume_refused"))
    if row.get("stop_pc") != 0x4000:
        raise ValueError(f"{mode}: stop PC ${row.get('stop_pc'):04X}, expected $4000")
    if row.get("instructions_executed") != 3:
        raise ValueError(f"{mode}: executed {row.get('instructions_executed')}, expected 3")
    if row.get("vdc_ar_before") != 5 or row.get("vdc_ar_after") != 5:
        raise ValueError(f"{mode}: VDC AR sentinel disturbed")
    if row.get("vdc_sr_before") != 0x3F or row.get("vdc_sr_after") != 0x3F:
        raise ValueError(f"{mode}: VDC SR changed without any step")
    _int_fields(row, ("vdc_r_before", "vdc_r_after",
                      "vdc_read_buffer_before", "vdc_read_buffer_after"))
    if row.get("vdc_r_before") != row.get("vdc_r_after"):
        raise ValueError(f"{mode}: VDC R0 changed without any step")
    if row.get("vdc_read_buffer_before") != row.get("vdc_read_buffer_after"):
        raise ValueError(f"{mode}: VDC READ_BUFFER changed without any step")
    if row.get("ram_diff") != []:
        raise ValueError(f"{mode}: RAM changed without any step")
    _check_mpr_transition(row, mode, MPR_INITIAL, MPR_AFTER_TIA_WRAP)
    if row.get("resume_attempts") != RESUME_ATTEMPTS or row.get("resume_refused") != RESUME_ATTEMPTS:
        raise ValueError(f"{mode}: resume not refused")
    if row.get("executed_at_refusal") != BLOCK_EXECUTED_BEFORE_REFUSAL[mode]:
        raise ValueError(f"{mode}: executed_at_refusal mismatch")
    if row.get("executed_after_resume") != row.get("executed_at_refusal"):
        raise ValueError(f"{mode}: execution advanced across resume")
    if row.get("ram_unchanged_during_resume") is not True:
        raise ValueError(f"{mode}: RAM changed across resume")
    return HWPAGE_BLOCKED_VERDICT


def mutation_test_armed(refused_row, landed_row, mode, check=check_armed):
    # Mode-aware MPR falsification: the wrap family requires MPR2==$FF and the
    # mpr2 family MPR2==$F8, so a blanket [0xFF]*8 would stay consistent there.
    if mode == "tia-wrap-forbidden":
        bad_mpr = [0x00, 0xF8, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]
    elif mode in ("tai-mpr2-unknown", "tii-hwpage-fetch-blocked"):
        bad_mpr = [0x00] * 8
    else:
        bad_mpr = [0xFF] * 8
    if check is check_hwpage_armed:
        mutations = [
            ("stop-pc", {"stop_pc": 0x4002}, [], "wrong stop accepted"),
            ("executed", {"instructions_executed": 4}, [], "wrong count accepted"),
            ("executed-after-resume", {"executed_after_resume": 4}, [],
             "resume advance accepted"),
            ("ram-across-resume", {"ram_unchanged_during_resume": False}, [],
             "resume RAM change accepted"),
            ("mode", {"mode": "tii-entry"}, [], "wrong mode accepted"),
            ("armed", {"armed": False}, [], "disarmed row accepted as armed"),
            ("refused", {"refused": True}, [], "opcode refusal accepted as fetch fail-closed"),
            ("reason", {"reason": "block_opcode_at_instruction_boundary"}, [],
             "wrong reason accepted"),
            ("fetch-safe", {"fetch_safe": True}, [], "safe fetch accepted as failed"),
            ("fetch-blocked", {"fetch_blocked": False}, [], "unblocked fetch accepted"),
            ("status-refused", {"status": "refused"}, [], "refused status accepted as fail-closed"),
            ("status-ok", {"status": "ok"}, [], "ok status accepted as fail-closed"),
            ("refused-opcode", {"refused_opcode": 0x73}, [],
             "captured opcode accepted as fail-closed"),
            ("operand-bytes", {"operand_bytes": [0x73, 0, 0, 0, 0, 0, 0]}, [],
             "operand capture accepted as fail-closed"),
            ("operand-complete", {"operand_complete": True}, [],
             "complete capture accepted as fail-closed"),
            ("fetch-via", {"fetch_via": "dma"}, [], "wrong fetch path accepted"),
            ("vdc-r", {"vdc_r_before": 0, "vdc_r_after": 1}, [],
             "VDC R0 side effect accepted"),
            ("vdc-changed", {"vdc_sr_before": 0x3F, "vdc_sr_after": 0x00}, [],
             "landed: VDC side effect accepted"),
            ("vdc-read-buffer-changed", {"vdc_read_buffer_before": 0xBEEF,
                                         "vdc_read_buffer_after": 0x1234}, [],
             "landed: VDC read-buffer side effect accepted"),
            ("ram-diff", {"ram_diff": [{"address": 0x3E90, "before": 0xCC, "after": 0x60}]},
             [], "landed: transfer bytes accepted"),
            ("resume-not-refused", {"resume_refused": RESUME_ATTEMPTS - 1}, [],
             "landed: failed resume accepted"),
            ("mpr-changed", {"after": {"mpr": bad_mpr, "a": 0, "x": 0, "y": 0,
                                       "p": 4, "sp": 0xFF, "pc": 0x4000}}, [],
             "landed: MPR change accepted"),
        ]
    else:
        mutations = [
        ("wrong-opcode", {"refused_opcode": 0xEA}, [], " landed: opcode mismatch accepted"),
        ("wrong-pc", {"refused_pc": 0x1234}, [], "landed: PC mismatch accepted"),
        ("vdc-changed", {"vdc_sr_before": 0x3F, "vdc_sr_after": 0x00}, [],
         "landed: VDC side effect accepted"),
        ("vdc-read-buffer-changed", {"vdc_read_buffer_before": 0xBEEF,
                                     "vdc_read_buffer_after": 0x1234}, [],
         "landed: VDC read-buffer side effect accepted"),
        ("ram-diff", {"ram_diff": [{"address": 0x3E90, "before": 0xCC, "after": 0x13}]},
         [], "landed: transfer bytes accepted"),
        ("resume-not-refused", {"resume_refused": RESUME_ATTEMPTS - 1}, [],
         "landed: failed resume accepted"),
        ("operand-incomplete", {"operand_complete": False}, [],
         "landed: missing operand evidence accepted"),
        ("mpr-changed", {"after": {"mpr": bad_mpr, "a": 0, "x": 0, "y": 0,
                                   "p": 4, "sp": 0xFF, "pc": 0x3E00}}, [],
         "landed: MPR change accepted"),
    ]
    for name, overwrite, remove, reason in mutations:
        broken = copy.deepcopy(refused_row)
        for key in remove:
            broken.pop(key, None)
        broken.update(overwrite)
        try:
            check(broken, mode)
        except ValueError:
            continue  # expected: falsified observation rejected
        raise ValueError(f"mutation test FAILED: {mode} {name} was accepted but must "
                         f"be rejected ({reason})")


def check_hwpage_unguarded_still_blocked(row, mode):
    """The fail-closed fetch does not depend on the monitor: even unguarded, no
    step is issued at an unpeekable hardware-page PC and no VDC side effect lands."""
    _require_ok_exit(row, mode)
    if row.get("mode") != mode + "-unguarded":
        raise ValueError(f"{mode}: measured wrong mode {row.get('mode')!r}")
    if row.get("armed") is not False:
        raise ValueError(f"{mode}: not an unguarded run")
    if row.get("refused") is not False:
        raise ValueError(f"{mode}: hwpage fetch must fail closed, not refuse an opcode")
    if row.get("status") != "fetch_blocked" or row.get("fetch_blocked") is not True:
        raise ValueError(f"{mode}: hwpage fetch was not failed-closed: {row.get('status')}")
    if row.get("reason") != "unsafe_opcode_fetch_fail_closed":
        raise ValueError(f"{mode}: unexpected fail-closed reason: {row.get('reason')!r}")
    if row.get("fetch_failed") is not True or row.get("fetch_safe") is not False:
        raise ValueError(f"{mode}: fetch failure not observed")
    # Fail-closed rows never refuse an opcode: no capture, peek path recorded.
    _int_fields(row, ("refused_opcode",))
    if row.get("refused_opcode") != 0:
        raise ValueError(f"{mode}: fail-closed row captured an opcode")
    if not isinstance(row.get("operand_bytes"), list) or row.get("operand_bytes") != []:
        raise ValueError(f"{mode}: fail-closed row carries operand bytes")
    if row.get("operand_complete") is not False:
        raise ValueError(f"{mode}: fail-closed row claims a complete capture")
    if row.get("fetch_via") != "peek":
        raise ValueError(f"{mode}: unexpected fetch path: {row.get('fetch_via')!r}")
    _int_fields(row, ("stop_pc", "instructions_executed",
                      "executed_at_refusal", "executed_after_resume",
                      "resume_attempts", "resume_refused"))
    if row.get("stop_pc") != 0x4000:
        raise ValueError(f"{mode}: stop PC ${row.get('stop_pc'):04X}, expected $4000")
    if row.get("instructions_executed") != 3:
        raise ValueError(f"{mode}: executed {row.get('instructions_executed')}, expected 3")
    _int_fields(row, ("vdc_ar_before", "vdc_ar_after", "vdc_sr_before", "vdc_sr_after",
                      "vdc_r_before", "vdc_r_after"))
    if row.get("vdc_ar_before") != row.get("vdc_ar_after"):
        raise ValueError(f"{mode}: VDC AR changed without any step")
    if row.get("vdc_sr_before") != row.get("vdc_sr_after"):
        raise ValueError(f"{mode}: VDC SR changed without any step")
    if row.get("vdc_r_before") != row.get("vdc_r_after"):
        raise ValueError(f"{mode}: VDC R0 changed without any step")
    _int_fields(row, ("vdc_read_buffer_before", "vdc_read_buffer_after"))
    if row.get("vdc_read_buffer_before") != row.get("vdc_read_buffer_after"):
        raise ValueError(f"{mode}: VDC READ_BUFFER changed without any step")
    if row.get("ram_diff") != []:
        raise ValueError(f"{mode}: RAM changed without any step")
    _check_mpr_transition(row, mode, MPR_INITIAL, MPR_AFTER_TIA_WRAP)
    if row.get("resume_attempts") != RESUME_ATTEMPTS or row.get("resume_refused") != RESUME_ATTEMPTS:
        raise ValueError(f"{mode}: resume not refused")
    if row.get("executed_after_resume") != row.get("executed_at_refusal"):
        raise ValueError(f"{mode}: execution advanced across resume")
    if row.get("ram_unchanged_during_resume") is not True:
        raise ValueError(f"{mode}: RAM changed across resume")
    return HWPAGE_BLOCKED_VERDICT


def main():
    with tempfile.TemporaryDirectory(prefix="pce-block-preblock-") as tmp:
        directory = Path(tmp)
        bios, cue = synthetic_media(directory)
        probe = compile_probe(directory, source_name="geargrafx_block_preblock.cpp",
                              target_name="block-preblock-probe")
        verdicts = {}
        for mode in ARMED_MODES:
            refused_row = run_mode(probe, bios, cue, mode, 0)
            if mode in HWPAGE_MODES:
                verdicts[mode] = check_hwpage_armed(refused_row, mode)
            else:
                verdicts[mode] = check_armed(refused_row, mode)
            mutations = []
            for mutation in MUTATIONS[mode]:
                landed_row = run_mode(probe, bios, cue, mode + mutation, 0)
                if mutation == "-resume-allowed":
                    verdicts[mode + mutation] = check_resume_allowed_landed(landed_row, mode)
                elif mode in HWPAGE_MODES:
                    verdicts[mode + mutation] = check_hwpage_unguarded_still_blocked(landed_row, mode)
                else:
                    verdicts[mode + mutation] = check_unguarded_landed(landed_row, mode)
                mutations.append((mode + mutation, verdicts[mode + mutation], landed_row))
            for mutation, _verdict, hazard_row in mutations:
                try:
                    check_armed({**hazard_row, "mode": mode}, mode)
                except ValueError:
                    continue
                raise ValueError(f"mutation test FAILED: {mutation} accepted as refused")
            mutation_test_armed(refused_row, mutations[0][2], mode,
                                check_hwpage_armed if mode in HWPAGE_MODES else check_armed)
            for case, verdict, row in [(mode, verdicts[mode], refused_row), *mutations]:
                print("EVIDENCE " + json.dumps({"case": case, "observation_kind": "REAL_CORE_EXECUTION",
                                                "core_revision": PINNED_CORE_REVISION, "bios": "SYNTHETIC",
                                                "execution_mode": "debug-single-step", "verdict": verdict,
                                                "measured_stop_pc": row["stop_pc"], "row": row}, sort_keys=True))
            print(f"ARMED {mode}: status={refused_row['status']} stop={refused_row['stop_pc']:#x} "
                  f"executed={refused_row['instructions_executed']} "
                  f"resume_refused={refused_row['resume_refused']}/{refused_row['resume_attempts']} "
                  f"-> {verdicts[mode]}")
            for mutation, verdict, row in mutations:
                print(f"MUTATION {mutation}: status={row['status']} stop={row['stop_pc']:#x} "
                      f"executed={row['instructions_executed']} -> {verdict}")
        check_pos(run_mode(probe, bios, cue, "pos-write", POS_CONTROL), POS_CONTROL)
        print("PASS issue-742 block whole-instruction preblock (synthetic ROM, pinned core):")
        print(f"  armed: {len(ARMED_MODES)} modes refused before the first transfer byte, "
              f"resume refused {RESUME_ATTEMPTS}/{RESUME_ATTEMPTS}")
        print("  mutation: unguarded/no-entry-check/no-recheck/wrong-opcode/knownpc-only/"
              "resume-allowed runs made the transfer observable, armed classifier rejected all")
        print(f"  positive control: allowlisted ${POS_CONTROL:04X} store ran unrefused and landed")
        print("LIMIT: opcode-granularity preblock on synthetic guests; TIA/TIN/TDD/TAI/TII covered "
              "real-core, length-0/64K never executed (host-BLOCKED); no gate change.")
        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        print(f"FAIL issue-742 block preblock: {error}", file=sys.stderr)
        raise SystemExit(1) from error
