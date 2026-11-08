#!/usr/bin/env python3
"""Issue #365: instruction-boundary opcode monitor for ST0/ST1/ST2 on the pinned real core.

Pinned real Geargrafx instruction-debugger core (local GPL-3.0 link, b49ae82a012eade566d72e9d61c9d95a5caa4da3;
no core code copied here), fully synthetic BIOS/CD, MPR0 fixed to $00 (never $FF).

The probe (geargrafx_st_boundary.cpp) single-steps one instruction at a time and resolves the opcode at
the current PC BEFORE every step (including the entry instruction) through the core's side-effect-free
peek (Memory::TryPeek). ST0 ($03) / ST1 ($13) / ST2 ($23) are refused before the step, an unsafe fetch
fails closed (no step is issued), and a resume attempt re-runs the same boundary check and is refused
again. The monitor is given no PC hint: every boundary of the single-stepped run is checked - entry,
branch, JSR/RTS return, MPR-remapped, page/bank-boundary, ROM-bank, unused-bank and IRQ-handler heads.

Classifier design (composite conditions, never a single stop-PC number):
* Executed-instruction count: the ST itself must not run (budget/executed values differ per guest) and
  a hardware-page stop must precede any step at the unsafe PC.
* VDC status register (SR): a safe armed run leaves SR unchanged; the Memory::Read fetch mutation
  lands a status clear (0x3F -> 0x00 with the probe's sentinel preset) BEFORE any refusal. AR/R are
  compared as well, and SR is read from the probe's state struct (host-side), never via a CPU fetch.
* Stop PC: for fixed-guest ST refusals the PC is deterministic and exact-matched; for dynamic
  hardware-page entries (fixed-JMP entry and the BBR3 data-dependent branch below, whose outcome
  varies with the patched zp bit) only hardware-page membership plus MPR-mapping consistency is
  required. A measured stop PC ($4000 for the fixed-JMP entry, $4002 for the taken BBR3) is
  recorded as evidence, never treated as a fixed expectation. A stop inside the hardware page
  with no TryPeek failure, or with a step already issued, is FAIL, not PASS.
* Resume: each resume attempt re-enters through the same guarded execution entry as the initial
  run (the probe's guarded_execute, the only path that invokes the core). No-resume is proven
  by execution count, PC, VDC AR/R/SR and RAM remaining unchanged across three attempts.

Modes (17 armed families + positive control):
* ``stN-entry``: ST is the entry instruction. Refused before any instruction runs (0 executed).
* ``stN-unknown`` / ``stN-unknown-far``: a JMP reaches the ST at an unknown PC (monitor has no hint).
* ``st1-branch-unknown``: a taken BNE (data-dependent branch) reaches the ST at $3E10.
* ``st0-jsr-rts``: a JSR subroutine returns; ST0 waits right after RTS.
* ``st1-page-boundary``: ST1 opcode at $1FFF (operand would cross into page 1 / WRAM).
* ``st0-rom-page0``: ST0 opcode at $0000 (ROM bank 0 through MPR0=$00).
* ``st2-mpr2-unknown``: guest TAM changes MPR2, then JMP reaches ST2 in page 2.
* ``st0-hwpage-fetch-blocked``: guest TAM maps page 2 to $FF; fetch at $4000 must fail closed.
  This is the fixed-JMP dynamic-MMIO-entry case: the branch outcome is fixed here.
* ``st0-bbr3-hwpage-taken`` / ``st0-bbr3-hwpage-skip``: a genuine BBR3 (opcode $3F, branch
  if the zp bit is clear) data-dependent branch with two conditions on the WRAM zp byte at
  CPU $2010 (PCE zero page base $2000). Taken it lands at $4002 (hardware page) and the fetch
  must fail closed; skipped it falls through to ST0 at $3F83 which must be refused. The
  branch outcome selects which classifier family applies; no fixed landing PC is required.
* ``st1-unused-bank``: guest TAM maps page 3 to unused bank $88; ST1 fetched from that bank.
* ``irq2-handler-st0-survey`` / ``irq2-core-stop-survey``: IRQ2 accepted at an instruction boundary;
  the handler-head ST0 is refused. The two differ only in the core debugger stop_on_irq flag.
* ``pos-write``: positive control. An allowlisted RAM store runs unrefused and lands.
Mutation suffixes (9 kinds, 22 runs): -unguarded, -no-entry-check, -no-recheck (MPR2 recheck only),
-wrong-opcode (ST2 omission only: single-opcode-list demo), -knownpc-only, -resume-allowed,
-unsafe-read, -peek-fail-step, -no-hazard-latch.

Scope: the check covers every instruction boundary of this single-stepped run (synthetic guests only).
It is NOT a general VDC write interceptor for a free-running guest, and it does not reason about data.
Nothing here releases a gate: slot $01 stays BLOCKED, release_eligible stays false, and no reference-BIOS
ABI or DEV ROM wiring is claimed.

Run: python3 tests/integration/geargrafx_st_boundary.py  (wired into `make integration` / `make test-real-core`)
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
import jsr_observation  # noqa: E402

PINNED_CORE_REVISION = "b49ae82a012eade566d72e9d61c9d95a5caa4da3"
ST_OPCODE = {"st0": 0x03, "st1": 0x13, "st2": 0x23}
POS_CONTROL = 0x3E50
MPR0_NON_FF = 0x00
RESUME_ATTEMPTS = 3
REFUSED_VERDICT = "refused_at_instruction_boundary_before_side_effect"
HWPAGE_BLOCKED_VERDICT = "hardware_page_fetch_failed_closed_before_step"
HAZARD_VERDICT = "unsafe_fetch_side_effect_landed_mutation_killed"
LANDED_VERDICT = "side_effect_landed_monitor_disabled"

# --- Fixed-guest ST refusal families: deterministic stop PC + execution budget. -----------------
# (Each value states a measured invariant of the single-stepped synthetic guest; the probe emits the
# same expectations in `expected_st_pc`, and every check below cross-checks both independently.)
ST_EXPECTED_PC = {
    "st0-entry": 0x3E00,
    "st1-entry": 0x3E00,
    "st2-entry": 0x3E00,
    "st1-unknown": 0x3E10,
    "st2-unknown": 0x3E10,
    "st1-branch-unknown": 0x3E10,
    "st0-jsr-rts": 0x3E03,
    "st1-page-boundary": 0x1FFF,
    "st0-rom-page0": 0x0000,
    "st1-unknown-far": 0x3E20,
    "st2-mpr2-unknown": 0x4010,
    "st1-unused-bank": 0x6010,
    "st0-bbr3-hwpage-skip": 0x3F83,
    "irq2-handler-st0-survey": 0x3F80,
    "irq2-core-stop-survey": 0x3F80,
}
ST_EXECUTED_BEFORE_REFUSAL = {
    "st0-entry": 0,
    "st1-entry": 0,
    "st2-entry": 0,
    "st1-unknown": 1,
    "st2-unknown": 1,
    "st1-branch-unknown": 2,
    "st0-jsr-rts": 3,
    "st1-page-boundary": 1,
    "st0-rom-page0": 1,
    "st1-unknown-far": 1,
    "st2-mpr2-unknown": 3,
    "st1-unused-bank": 3,
    "st0-bbr3-hwpage-skip": 4,
    "irq2-handler-st0-survey": 2,
    "irq2-core-stop-survey": 2,
}
# Guest-remapped MPR transitions (page, before, after); all other modes keep MPR stable.
MPR_TRANSITION = {
    "st2-mpr2-unknown": (2, 0x00, 0xF8),
    "st0-hwpage-fetch-blocked": (2, 0x00, 0xFF),
    "st0-bbr3-hwpage-taken": (2, 0x00, 0xFF),
    "st0-bbr3-hwpage-skip": (2, 0x00, 0xFF),
    "st1-unused-bank": (3, 0x00, 0x88),
}
# Modes whose ST fetch must resolve through the guest-remapped page. (The BBR3-skip ST sits in
# page 1 WRAM; its MPR2 remap is part of the prologue but holds no ST fetch.)
TRANSITION_FETCH_MODES = ("st2-mpr2-unknown", "st1-unused-bank")

# --- Dynamic hardware-page entries: composite classification, no fixed PC. -------------------
# Two shapes land in the hardware page: the fixed-JMP entry and the BBR3 data-dependent branch
# (whose outcome varies with the host-patched zp bit). Only page membership + MPR consistency
# is required; measured stop PCs ($4000 / $4002) are evidence, never expectations.
HWPAGE_MODE = "st0-hwpage-fetch-blocked"
BBR3_TAKEN_MODE = "st0-bbr3-hwpage-taken"
HWPAGE_EXPECTED_EXECUTED = {HWPAGE_MODE: 3, BBR3_TAKEN_MODE: 4}
HWPAGE_PAGE = 2  # CPU page 2 (addresses $4000-$5FFF)
HWPAGE_BANK = 0xFF
SR_SENTINEL_BEFORE = 0x3F
AR_SENTINEL_BEFORE = 5
# Reset value of the VDC read buffer on the pinned core (measured landmark; armed runs must
# leave it untouched). Anchors the fabrication check for fabricated buffer pairs.
READ_BUFFER_RESET = 0xFFFF

ARMED_MODES = tuple(ST_EXPECTED_PC) + (HWPAGE_MODE, BBR3_TAKEN_MODE)
IRQ_SURVEY_MODES = ("irq2-handler-st0-survey", "irq2-core-stop-survey")

# Mutation kinds (9, 22 runs): each keeps the monitor armed unless its name says otherwise, and must make the
# prohibited effect observable; the armed classifier must then reject the mutated run.
# (Known limitation: -knownpc-only on an already-known PC stays armed; it is only run on non-listed
# PCs. -resume-allowed on the branch case proves the latch, not the PC list.)
MUTATIONS = {
    "st0-entry": ("st0-entry-no-entry-check",),
    "st1-entry": ("st1-entry-no-entry-check",),
    "st2-entry": ("st2-entry-no-entry-check",),
    "st1-unknown": ("st1-unknown-unguarded",),
    "st2-unknown": ("st2-unknown-unguarded",),
    "st1-branch-unknown": ("st1-branch-unknown-resume-allowed",),
    "st0-jsr-rts": ("st0-jsr-rts-knownpc-only",),
    "st1-page-boundary": ("st1-page-boundary-knownpc-only",),
    "st0-rom-page0": ("st0-rom-page0-knownpc-only",),
    "st1-unknown-far": ("st1-unknown-far-knownpc-only",),
    "st2-mpr2-unknown": ("st2-mpr2-unknown-no-recheck", "st2-mpr2-unknown-wrong-opcode"),
    HWPAGE_MODE: (HWPAGE_MODE + "-unsafe-read", HWPAGE_MODE + "-peek-fail-step",
                   HWPAGE_MODE + "-no-hazard-latch"),
    BBR3_TAKEN_MODE: (BBR3_TAKEN_MODE + "-unsafe-read", BBR3_TAKEN_MODE + "-peek-fail-step",
                      BBR3_TAKEN_MODE + "-no-hazard-latch"),
    "st0-bbr3-hwpage-skip": ("st0-bbr3-hwpage-skip-knownpc-only",),
    "st1-unused-bank": ("st1-unused-bank-knownpc-only",),
    "irq2-handler-st0-survey": ("irq2-handler-st0-survey-knownpc-only",),
    "irq2-core-stop-survey": ("irq2-core-stop-survey-knownpc-only",),
}
MUTATION_SUFFIXES = ("-no-entry-check", "-knownpc-only", "-resume-allowed",
                     "-peek-fail-step", "-wrong-opcode", "-no-recheck", "-unguarded",
                     "-no-hazard-latch")
HAZARD_SUFFIXES = ("-unsafe-read", "-peek-fail-step", "-no-hazard-latch")


def _base_of(mode):
    for suffix in MUTATION_SUFFIXES + ("-unsafe-read",):
        if mode.endswith(suffix):
            return mode[: -len(suffix)]
    return mode


def _expected_kind(mode):
    """Expected ST kind from the **mode** (never from untrusted row data)."""
    base = _base_of(mode)
    # IRQ surveys refuse the handler-head ST0; the mode name carries no "st0" prefix.
    if base.startswith("irq2-"):
        return "st0"
    return base[:3]


def _st_kind_of(row, mode):
    """Legacy alias: kept for mutation helpers; kind is always derived from the mode."""
    return _expected_kind(mode)


def _vdc_landed(row, kind):
    if kind == "st0":
        return row["vdc_ar_before"] == 0 and row["vdc_ar_after"] == 0x07
    if kind == "st1":
        return row["vdc_r_before"] == 0 and (row["vdc_r_after"] & 0xFF) == 0x5A
    return row["vdc_r_before"] == 0 and (row["vdc_r_after"] >> 8) == 0xA5


def _common(row, mode, historical=False):
    """Validate the evidence envelope. Live runs require the full probe surface (fetch/SR/RAM)."""
    # Fail-closed exit validation: only the integer 0 is accepted. A missing field, None, a
    # bool, or a numeric string must never be treated as success.
    exit_code = row.get("exit")
    if type(exit_code) is not int or exit_code != 0:
        raise ValueError(f"{mode} probe refused to run (exit {exit_code!r}): "
                         f"{str(row.get('stderr', ''))[-300:]}")
    if row.get("mode") != mode:
        raise ValueError(f"{mode} measured wrong mode: {row.get('mode')}")
    for side in ("before", "after"):
        mpr = row.get(side, {}).get("mpr", [])
        if type(mpr) is not list or len(mpr) != 8 or mpr[0] != MPR0_NON_FF:
            raise ValueError(f"{mode} MPR0 {side} not {MPR0_NON_FF:#x} (!= $FF): {mpr[:1]}")
    transition = MPR_TRANSITION.get(_base_of(mode))
    if transition is None:
        if row.get("before", {}).get("mpr") != row.get("after", {}).get("mpr"):
            raise ValueError(f"{mode}: MPR configuration changed during the run")
    else:
        page, before, after = transition
        got = (row["before"]["mpr"][page], row["after"]["mpr"][page])
        if got != (before, after):
            raise ValueError(f"{mode}: guest MPR{page} remap was not observed ({got})")
        # Only the remapped page may change; every other MPR entry must be stable.
        for other in range(8):
            if other != page and row["before"]["mpr"][other] != row["after"]["mpr"][other]:
                raise ValueError(f"{mode}: unexpected MPR{other} change")
    for name in ("vdc_ar_before", "vdc_ar_after", "vdc_r_before", "vdc_r_after", "stop_pc",
                 "instructions_executed", "refused_pc"):
        if type(row.get(name)) is not int:
            raise ValueError(f"{mode} missing/non-integer {name}: {row.get(name)!r}")
    for side in ("before", "after"):
        section = row.get(side, {})
        if not historical:
            pc = section.get("pc")
            if type(pc) is not int or not 0 <= pc <= 0xFFFF:
                raise ValueError(f"{mode} {side}.pc missing/non-16-bit: {pc!r}")
    if not historical:
        for name in ("vdc_read_buffer_before", "vdc_read_buffer_after"):
            if type(row.get(name)) is not int:
                raise ValueError(f"{mode} missing/non-integer {name}: {row.get(name)!r}")
        for name in ("vdc_sr_before", "vdc_sr_after", "fetch_opcode", "fetch_page", "fetch_mpr",
                     "fetch_safe", "fetch_failed", "fetch_blocked", "hazard_landed",
                     "fetch_open_stepped", "refused", "vector_read", "ram_unchanged_during_resume",
                     "ram_diff_truncated", "mmio_read_attempts", "instructions_executed",
                     "executed_at_refusal", "executed_after_resume", "resume_attempts", "resume_refused"):
            if name in ("fetch_safe", "fetch_failed", "fetch_blocked", "hazard_landed",
                        "fetch_open_stepped", "refused", "vector_read",
                        "ram_unchanged_during_resume", "ram_diff_truncated"):
                if type(row.get(name)) is not bool:
                    raise ValueError(f"{mode} missing/non-bool {name}: {row.get(name)!r}")
            elif type(row.get(name)) is not int:
                raise ValueError(f"{mode} missing/non-integer {name}: {row.get(name)!r}")
        if type(row.get("fetch_via")) is not str or row["fetch_via"] not in ("peek", "read"):
            raise ValueError(f"{mode} fetch_via must be peek/read: {row.get('fetch_via')!r}")
        # Hazard mutations intentionally exercise the unsafe fetch path; their dedicated checkers
        # validate the exact hazard shape. The armed hwpage runs are a TryPeek failure by design.
        if mode.endswith(HAZARD_SUFFIXES):
            return
        if row["fetch_via"] != "peek":
            raise ValueError(f"{mode} live armed run fetched via {row['fetch_via']!r}, not TryPeek")
        if _base_of(mode) not in HWPAGE_EXPECTED_EXECUTED:
            if row["fetch_failed"] is not False:
                raise ValueError(f"{mode} fetch failed on a safe-guest run")
            if row["fetch_blocked"] is not False:
                raise ValueError(f"{mode} fetch_blocked set on a safe-guest run")


def _fetch_mapping_ok(row, mode):
    fetch = {"fetch_opcode": row["fetch_opcode"], "fetch_page": row["fetch_page"],
             "fetch_mpr": row["fetch_mpr"]}
    if fetch["fetch_page"] != row["refused_pc"] >> 13:
        raise ValueError(f"{mode}: fetch page {fetch['fetch_page']}, expected CPU page "
                         f"{row['refused_pc'] >> 13}")
    if fetch["fetch_mpr"] != row["after"]["mpr"][fetch["fetch_page"]]:
        raise ValueError(f"{mode}: fetch MPR does not match the page's active MPR")
    if fetch["fetch_opcode"] != row["refused_opcode"]:
        raise ValueError(f"{mode}: fetched opcode does not match the refused ST opcode")


def _check_refused(row, mode, require_fetch=True):
    """Armed ST classifier: refused at its instruction boundary with no side effect."""
    _common(row, mode, historical=not require_fetch)
    # The probe's direct hazard signals reject even if SR happens to read unchanged.
    for flag in ("hazard_landed", "fetch_open_stepped", "fetch_blocked"):
        if require_fetch and row.get(flag) is True:
            raise ValueError(f"{mode}: {flag} was set on an armed refusal")
    if row.get("status") != "refused" or row.get("refused") is not True:
        raise ValueError(f"{mode}: monitor did not refuse (status={row.get('status')})")
    if row.get("armed") is not True or row.get("boundary_check") is not True:
        raise ValueError(f"{mode}: run is not armed")
    kind = _expected_kind(mode)
    if row.get("st_mode") != kind:
        raise ValueError(f"{mode}: st_mode {row.get('st_mode')!r} does not match the mode's ST "
                         f"{kind} (opcode identity must not be trusted from row data)")
    if row.get("refused_opcode") != ST_OPCODE[kind]:
        raise ValueError(f"{mode}: refused opcode {row.get('refused_opcode')!r}, expected {ST_OPCODE[kind]:#x}")
    if row["refused_pc"] != ST_EXPECTED_PC[mode] or row["stop_pc"] != ST_EXPECTED_PC[mode]:
        raise ValueError(f"{mode}: refused at {row['refused_pc']:#x} / stop {row['stop_pc']:#x}, "
                         f"expected {ST_EXPECTED_PC[mode]:#x}")
    if row["instructions_executed"] != ST_EXECUTED_BEFORE_REFUSAL[mode]:
        raise ValueError(f"{mode}: executed {row['instructions_executed']} instructions, expected "
                         f"{ST_EXECUTED_BEFORE_REFUSAL[mode]} (the ST itself must not run)")
    # detected_pc is the measured detection PC (never the plan expectation): it must agree with
    # the refused PC of this run. Live runs only; historical records predate the field.
    if require_fetch:
        if row.get("detected") is not True or row.get("detected_pc") != row["refused_pc"]:
            raise ValueError(f"{mode}: detected_pc {row.get('detected_pc')!r} does not match the "
                             f"refused PC {row['refused_pc']:#x}")
        if row.get("ram_unchanged_during_resume") is not True:
            raise ValueError(f"{mode}: WRAM changed during the resume attempts")
        # expected_st_pc is the probe's plan value (expectation, not a measurement): it must
        # agree with the driver's table so a wrong guest build cannot pass.
        if row.get("expected_st_pc") != ST_EXPECTED_PC[mode]:
            raise ValueError(f"{mode}: expected_st_pc {row.get('expected_st_pc')!r}, "
                             f"expected {ST_EXPECTED_PC[mode]:#x}")
        # Armed refuses run through TryPeek only and never target a $FF-mapped page.
        if row.get("fetch_unsafe_bank") is not False:
            raise ValueError(f"{mode}: refused fetch targeted the unsafe bank")
        if row.get("mmio_read_attempts") != 0:
            raise ValueError(f"{mode}: mmio_read_attempts {row.get('mmio_read_attempts')!r}, expected 0")
        # CPU stop-state held: the final architectural PC must equal the refusal PC.
        if row["after"]["pc"] != row["stop_pc"] or row["stop_pc"] != row["refused_pc"]:
            raise ValueError(f"{mode}: after.pc {row['after']['pc']!r} / stop {row['stop_pc']!r} / "
                             f"refused {row['refused_pc']!r} not held")
    if row.get("resume_attempts") != RESUME_ATTEMPTS or row.get("resume_refused") != RESUME_ATTEMPTS:
        raise ValueError(f"{mode}: resume not fully refused ({row.get('resume_refused')}/"
                         f"{row.get('resume_attempts')})")
    if row.get("executed_after_resume") != row["instructions_executed"] or \
            row.get("executed_at_refusal") != row["instructions_executed"]:
        raise ValueError(f"{mode}: a resumed run executed instructions past the refusal")
    # Side effects: VDC state (incl. SR) must be unchanged by the armed run.
    if row["vdc_ar_before"] != row["vdc_ar_after"] or row["vdc_r_before"] != row["vdc_r_after"]:
        raise ValueError(f"{mode}: VDC state changed before the refusal "
                         f"(AR {row['vdc_ar_before']:#x}->{row['vdc_ar_after']:#x}, "
                         f"R {row['vdc_r_before']:#x}->{row['vdc_r_after']:#x})")
    if not require_fetch:
        # Historical records carry no SR view; absence cannot prove a hazard either.
        if "vdc_sr_before" in row and row["vdc_sr_before"] != row.get("vdc_sr_after"):
            raise ValueError(f"{mode}: VDC SR changed before the refusal")
    elif row["vdc_sr_before"] != row["vdc_sr_after"]:
        raise ValueError(f"{mode}: VDC SR changed before the refusal "
                         f"({row['vdc_sr_before']:#x}->{row['vdc_sr_after']:#x})")
    if _vdc_landed(row, kind):
        raise ValueError(f"{mode}: VDC side effect landed although the ST was refused")
    if not require_fetch:
        return REFUSED_VERDICT
    _fetch_mapping_ok(row, mode)
    if row.get("fetch_safe") is not True:
        raise ValueError(f"{mode}: opcode was not obtained through a safe peek")
    if mode in TRANSITION_FETCH_MODES:
        page, _before, after = MPR_TRANSITION[mode]
        if row["fetch_page"] != page or row["fetch_mpr"] != after:
            raise ValueError(f"{mode}: ST fetch did not resolve through remapped page {page}")
    if mode in IRQ_SURVEY_MODES:
        _check_irq_survey(row, mode)
    return REFUSED_VERDICT


def _check_irq_survey(row, mode):
    """IRQ-acceptance side effects: only the architectural stack pushes may touch WRAM (net diff)."""
    if row.get("irq_accepted") is not True:
        raise ValueError(f"{mode}: IRQ2 was not accepted at an instruction boundary")
    # core_irq_stop reports the pinned core debugger's own stop notification (not a generic
    # IRQ-safety claim): the handler survey runs with stop_on_irq off, the core-stop survey
    # with it on.
    want_stop = (mode == "irq2-core-stop-survey")
    if row.get("core_irq_stop") is not want_stop:
        raise ValueError(f"{mode}: core_irq_stop {row.get('core_irq_stop')!r}, expected {want_stop}")
    if row.get("vector_read") is not True or row.get("observed_vector") != 0x3F80:
        raise ValueError(f"{mode}: IRQ vector was not measured "
                         f"(vector_read={row.get('vector_read')!r}, value={row.get('observed_vector')!r})")
    before_sp = row["before"].get("sp") if isinstance(row["before"], dict) else None
    after_sp = row["after"].get("sp") if isinstance(row["after"], dict) else None
    if type(before_sp) is not int or type(after_sp) is not int:
        raise ValueError(f"{mode}: sp before/after must be integers, got "
                         f"{before_sp!r}/{after_sp!r}")
    if before_sp != 0xFF:
        raise ValueError(f"{mode}: SP before acceptance is {before_sp:#x}, expected $FF")
    if after_sp != 0xFC:
        raise ValueError(f"{mode}: SP after acceptance is {after_sp:#x} ($FC = 3 pushes)")
    frame = row.get("stack_frame_layout") or []
    if type(row.get("stack_cpu")) is not list or row.get("stack_cpu") != [0x21FF, 0x21FE, 0x21FD]:
        raise ValueError(f"{mode}: stack_cpu {row.get('stack_cpu')!r}, expected "
                         f"[$21FF, $21FE, $21FD]")
    if len(frame) != 3 or any(type(v) is not int or not 0 <= v <= 0xFF for v in frame):
        raise ValueError(f"{mode}: stack_frame_layout malformed: {frame!r}")
    if frame[0] != 0x3E or frame[1] != 0x01:
        raise ValueError(f"{mode}: stack frame {frame!r} does not encode the return PC $3E01")
    # Ram-diff values must agree with the captured frame bytes (net-diff content check).
    frame_by_addr = {0x21FF: frame[0], 0x21FE: frame[1], 0x21FD: frame[2]}
    # Direct net-diff validation (never trust only_stack_net_diffs alone): each ram_diff entry
    # must be a well-typed value change confined to the architectural stack cells.
    if row.get("only_stack_net_diffs") is not True:
        raise ValueError(f"{mode}: only_stack_net_diffs is not True")
    if row.get("ram_diff_truncated") is not False:
        raise ValueError(f"{mode}: ram_diff_truncated is not False")
    ram_diff = row.get("ram_diff")
    if type(ram_diff) is not list:
        raise ValueError(f"{mode}: ram_diff is not a list: {ram_diff!r}")
    seen = set()
    for entry in ram_diff:
        address = entry.get("address") if isinstance(entry, dict) else None
        before = entry.get("before") if isinstance(entry, dict) else None
        after = entry.get("after") if isinstance(entry, dict) else None
        for name, value, limit in (("address", address, 0xFFFF), ("before", before, 0xFF),
                                   ("after", after, 0xFF)):
            if type(value) is not int or not 0 <= value <= limit:
                raise ValueError(f"{mode}: ram_diff.{name} malformed: {value!r}")
        if address in seen:
            raise ValueError(f"{mode}: ram_diff duplicate address {address:#x}")
        seen.add(address)
        if address not in (0x21FD, 0x21FE, 0x21FF):
            raise ValueError(f"{mode}: ram_diff outside the stack cells: {address:#x}")
        if before == after:
            raise ValueError(f"{mode}: ram_diff entry is not a change ({address:#x})")
        if after != frame_by_addr[address]:
            raise ValueError(f"{mode}: ram_diff.after {after:#x} does not match the captured "
                             f"stack frame byte {frame_by_addr[address]:#x} at {address:#x}")
    if row.get("ram_diff_count") != len(ram_diff):
        raise ValueError(f"{mode}: ram_diff_count {row.get('ram_diff_count')!r} != {len(ram_diff)}")
    # Net-diff completeness for this synthetic environment: the stack pushes land in the
    # two nonzero-valued cells, so $21FE and $21FF must have observable changes. (The $21FD
    # status byte pushes 0x00 over an initial 0x00 here, so its absence is legitimate. This
    # anchors the IRQ survey's own empty-diff coverage only; no generic IRQ-safety claim.)
    if 0x21FE not in seen or 0x21FF not in seen:
        raise ValueError(f"{mode}: missing required diff in the IRQ stack cells")


def check_refused(row, mode):
    """Validate live probe evidence; fetch opcode and mapping fields are mandatory."""
    return _check_refused(row, mode, require_fetch=True)


def check_refused_historical(row, mode):
    """Validate immutable pre-fetch-map records only; never used by live runs."""
    return _check_refused(row, mode, require_fetch=False)


def check_hardware_page_blocked(row, mode=HWPAGE_MODE):
    """Dynamic hardware-page-entry classifier (fixed-JMP entry and BBR3 branch): composite
    conditions, no fixed stop PC.

    A measured stop PC is recorded as evidence but never required to equal any constant: the branch
    outcome (and therefore the landing PC) varies with the entry shape. PASS requires ALL of:
    TryPeek failure at the fetch, fail-closed before any step at the unsafe PC, a stop PC inside the
    hardware page consistent with the MPR mapping, unchanged VDC state (SR/AR/R), and no-resume.
    Stopping inside the hardware page alone is explicitly NOT a pass.
    """
    _common(row, mode)
    if row.get("status") != "fetch_blocked" or row.get("fetch_blocked") is not True:
        raise ValueError(f"{mode}: monitor did not fail closed (status={row.get('status')})")
    for flag in ("refused", "hazard_landed", "fetch_open_stepped"):
        if row.get(flag) is not False:
            raise ValueError(f"{mode}: {flag}={row.get(flag)!r} on a fail-closed run")
    if row.get("armed") is not True or row.get("boundary_check") is not True:
        raise ValueError(f"{mode}: run is not armed")
    if row.get("fetch_via") != "peek" or row.get("fetch_safe") is not False or \
            row.get("fetch_failed") is not True or row.get("fetch_unsafe_bank") is not True:
        raise ValueError(f"{mode}: unsafe fetch was not a TryPeek failure (via={row.get('fetch_via')!r})")
    if row.get("fetch_page") != HWPAGE_PAGE or row.get("fetch_mpr") != HWPAGE_BANK:
        raise ValueError(f"{mode}: fetch page/bank {row.get('fetch_page')}/{row.get('fetch_mpr'):#x}, "
                         f"expected hardware page {HWPAGE_PAGE}/${HWPAGE_BANK:X}")
    if row["after"]["mpr"][HWPAGE_PAGE] != row["fetch_mpr"]:
        raise ValueError(f"{mode}: hardware-page mapping inconsistent")
    # The stop PC must lie inside the hardware page, but its exact value is measured evidence only.
    if not (HWPAGE_PAGE << 13) <= row["stop_pc"] < (HWPAGE_PAGE + 1) << 13:
        raise ValueError(f"{mode}: stop PC {row['stop_pc']:#x} is outside the hardware page")
    if row["after"]["pc"] != row["stop_pc"]:
        raise ValueError(f"{mode}: stop PC not held")
    # detected_pc must be the measured stop PC (where the guard actually refused), never a plan
    # expectation.
    if row.get("detected") is not True or row.get("detected_pc") != row["stop_pc"]:
        raise ValueError(f"{mode}: detected_pc {row.get('detected_pc')!r} does not match the "
                         f"measured stop PC {row['stop_pc']:#x}")
    if row.get("ram_unchanged_during_resume") is not True:
        raise ValueError(f"{mode}: WRAM changed during the resume attempts")
    # Armed runs must not perform any unsafe Read-based fetch (the probe's TryPeek path is
    # side-effect-free; a recorded attempt means the run took the Read path somewhere).
    if row.get("mmio_read_attempts") != 0:
        raise ValueError(f"{mode}: mmio_read_attempts {row.get('mmio_read_attempts')!r}, expected 0")
    # Executed count covers exactly the guest prologue; the unsafe fetch must never step.
    expected_executed = HWPAGE_EXPECTED_EXECUTED[mode]
    if row["instructions_executed"] != expected_executed:
        raise ValueError(f"{mode}: executed {row['instructions_executed']} instructions, expected "
                         f"{expected_executed} (a step at the unsafe PC must not issue)")
    if row.get("resume_attempts") != RESUME_ATTEMPTS or row.get("resume_refused") != RESUME_ATTEMPTS:
        raise ValueError(f"{mode}: resume not fully refused")
    if row.get("executed_at_refusal") != row["instructions_executed"] or \
            row.get("executed_after_resume") != row["instructions_executed"]:
        raise ValueError(f"{mode}: instructions executed after the fail-closed stop")
    # VDC: no forbidden-read side effect (status clear) may have landed.
    if row["vdc_sr_before"] != SR_SENTINEL_BEFORE or row["vdc_sr_after"] != SR_SENTINEL_BEFORE:
        raise ValueError(f"{mode}: VDC SR changed ({row['vdc_sr_before']:#x}->{row['vdc_sr_after']:#x}); "
                         f"a forbidden read landed before the stop")
    if row["vdc_ar_before"] != AR_SENTINEL_BEFORE or row["vdc_ar_after"] != AR_SENTINEL_BEFORE:
        raise ValueError(f"{mode}: VDC AR changed before the stop")
    if row["vdc_r_before"] != row["vdc_r_after"]:
        raise ValueError(f"{mode}: VDC R changed before the stop")
    # VDC read buffer: the probe's reset value is a measured landmark of the pinned core;
    # a forbidden fetch must neither change it nor be hidden by fabricating it.
    if row["vdc_read_buffer_before"] != READ_BUFFER_RESET or \
            row["vdc_read_buffer_after"] != READ_BUFFER_RESET:
        raise ValueError(f"{mode}: VDC read buffer changed "
                         f"({row['vdc_read_buffer_before']:#x}->{row['vdc_read_buffer_after']:#x})")
    return HWPAGE_BLOCKED_VERDICT


def check_unguarded(row, mode):
    """Mutation run (monitor disabled): the ST must execute and its VDC side effect must land."""
    _common(row, mode, historical="fetch_opcode" not in row)
    if row.get("armed") is not False or row.get("refused") is not False:
        raise ValueError(f"{mode}: unguarded run reports armed/refused")
    if row.get("status") != "ok":
        raise ValueError(f"{mode}: unguarded run did not reach its self-loop (status={row.get('status')})")
    if not _vdc_landed(row, _st_kind_of(row, mode)):
        raise ValueError(f"{mode}: VDC side effect did not land with the monitor disabled")
    return LANDED_VERDICT


def check_bypassed_mutation(row, mode):
    """A real monitor mutation must execute the ST and therefore cannot pass as refusal."""
    base = _base_of(mode)
    if base not in ARMED_MODES:
        raise ValueError(f"{mode}: unknown mutation base {base!r}")
    _common(row, mode, historical="fetch_opcode" not in row)
    if row.get("armed") is not True or row.get("boundary_check") is not True:
        raise ValueError(f"{mode}: named mutation disabled the entire boundary monitor")
    # resume-allowed keeps a refused flag but steps anyway; lapse is proven by the landed
    # side effect plus execution past the refusal boundary rather than by the flag value.
    if row.get("status") != "ok" or not _vdc_landed(row, _st_kind_of(row, mode)):
        raise ValueError(f"{mode}: mutation did not execute the prohibited ST")
    if row.get("refused") is not False and not mode.endswith("-resume-allowed"):
        raise ValueError(f"{mode}: mutation still refused the ST")
    expected = ST_EXECUTED_BEFORE_REFUSAL[base]
    if row["instructions_executed"] <= expected:
        raise ValueError(f"{mode}: ST did not execute "
                         f"(executed {row['instructions_executed']} <= {expected})")
    # Defect signatures: the latch failure must show zero resume refusals, and the PC-list
    # regression must fire on a non-listed PC.
    if mode.endswith("-resume-allowed") and row.get("resume_refused") != 0:
        raise ValueError(f"{mode}: resume-allowed run still refused resumes "
                         f"({row.get('resume_refused')})")
    if mode.endswith("-knownpc-only") and row["stop_pc"] == ST_EXPECTED_PC[base]:
        raise ValueError(f"{mode}: known-pc run stopped at its own ST PC")
    return LANDED_VERDICT


def check_unsafe_read_hazard(row, mode):
    """TryPeek->Memory::Read mutation: the fetch read itself is a forbidden MMIO access."""
    base = _base_of(mode)
    if base not in HWPAGE_EXPECTED_EXECUTED:
        raise ValueError(f"{mode}: hazard check unknown base {base!r}")
    _common(row, mode)
    if row.get("armed") is not True:
        raise ValueError(f"{mode}: hazard run is unarmed")
    if row.get("hazard_landed") is not True or row.get("fetch_via") != "read":
        raise ValueError(f"{mode}: hazard was not observed through the unsafe fetch")
    # Fetch-location / mapping integrity: the hazard read must be the hardware-page fetch.
    if row.get("fetch_page") != HWPAGE_PAGE or row.get("fetch_mpr") != HWPAGE_BANK:
        raise ValueError(f"{mode}: fetch page/bank {row.get('fetch_page')}/{row.get('fetch_mpr')!r}, "
                         f"expected hardware page {HWPAGE_PAGE}/${HWPAGE_BANK:X}")
    if row.get("fetch_unsafe_bank") is not True:
        raise ValueError(f"{mode}: fetch did not resolve through the unsafe bank")
    if row["after"]["mpr"][HWPAGE_PAGE] != HWPAGE_BANK:
        raise ValueError(f"{mode}: hardware-page mapping inconsistent")
    if not (HWPAGE_PAGE << 13) <= row["stop_pc"] < (HWPAGE_PAGE + 1) << 13:
        raise ValueError(f"{mode}: hazard stop {row['stop_pc']:#x} is outside the hardware page")
    if row["after"]["pc"] != row["stop_pc"]:
        raise ValueError(f"{mode}: stop PC not held")
    # The resume evidence: three resume attempts made through the guarded entry, all refused,
    # with execution count, PC and RAM unchanged (the latch keeps the device untouched).
    if row.get("resume_attempts") != RESUME_ATTEMPTS or row.get("resume_refused") != RESUME_ATTEMPTS:
        raise ValueError(f"{mode}: resume not fully refused")
    if row.get("executed_at_refusal") != row["instructions_executed"] or \
            row.get("executed_after_resume") != row["instructions_executed"]:
        raise ValueError(f"{mode}: instructions executed after the hazard stop")
    if row.get("ram_unchanged_during_resume") is not True:
        raise ValueError(f"{mode}: WRAM changed during the resume attempts")
    if row.get("fetch_safe") is not False:
        raise ValueError(f"{mode}: fetch_safe {row.get('fetch_safe')!r}, expected False")
    if row["instructions_executed"] != HWPAGE_EXPECTED_EXECUTED[base]:
        raise ValueError(f"{mode}: hazard run executed "
                         f"{row['instructions_executed']} (expected prologue "
                         f"{HWPAGE_EXPECTED_EXECUTED[base]})")
    # With the hazard latch the unsafe Read fires exactly once (first decision). Any resume
    # re-read means the latch is gone, so the checker's count is what proves it.
    if row.get("mmio_read_attempts") != 1:
        raise ValueError(f"{mode}: mmio_read_attempts {row.get('mmio_read_attempts')!r}, expected 1")
    if base == HWPAGE_MODE:
        # At $4000 the read hits the status register: 0x3F -> 0x00 is device-observed proof.
        if row["vdc_sr_before"] != SR_SENTINEL_BEFORE or row["vdc_sr_after"] != 0x00:
            raise ValueError(f"{mode}: SR hazard {row['vdc_sr_before']:#x}->{row['vdc_sr_after']:#x}, "
                             f"expected {SR_SENTINEL_BEFORE:#x}->0x00")
    else:
        # At the $4002 data port the read leaves SR untouched (measured); the hazard is proven
        # by the probe-decision hazard flag, the prologue execution count, and a stop inside
        # the hardware page where the unsafe fetch happened.
        if row["instructions_executed"] != HWPAGE_EXPECTED_EXECUTED[base]:
            raise ValueError(f"{mode}: hazard run executed "
                             f"{row['instructions_executed']} (expected prologue "
                             f"{HWPAGE_EXPECTED_EXECUTED[base]})")
        if not (HWPAGE_PAGE << 13) <= row["stop_pc"] < (HWPAGE_PAGE + 1) << 13:
            raise ValueError(f"{mode}: hazard stop {row['stop_pc']:#x} is outside the hardware page")
    return HAZARD_VERDICT


def check_peek_fail_step(row, mode):
    """Fail-open mutation: the peek failure steps anyway (one MMIO-space step) before stopping."""
    base = _base_of(mode)
    if base not in HWPAGE_EXPECTED_EXECUTED:
        raise ValueError(f"{mode}: fail-open check unknown base {base!r}")
    _common(row, mode)
    if row.get("armed") is not True:
        raise ValueError(f"{mode}: fail-open run is unarmed")
    if row.get("fetch_open_stepped") is not True or row.get("status") != "fetch_open_stepped":
        raise ValueError(f"{mode}: peek failure did not step anyway")
    # The fail-open path is still peek-based: the monitor's own Reads never fire there.
    if row.get("mmio_read_attempts") != 0:
        raise ValueError(f"{mode}: mmio_read_attempts {row.get('mmio_read_attempts')!r}, expected 0")
    # The failed fetch evidence must describe the hardware page (fixed-JMP $4000 or BBR3 $4002).
    if row.get("fetch_failed") is not True or row.get("fetch_unsafe_bank") is not True:
        raise ValueError(f"{mode}: fetch evidence does not describe a failed unsafe fetch")
    if row.get("fetch_via") != "peek":
        raise ValueError(f"{mode}: fail-open fetch was not a peek failure")
    if row.get("fetch_page") != HWPAGE_PAGE or row.get("fetch_mpr") != HWPAGE_BANK:
        raise ValueError(f"{mode}: fetch page/bank {row.get('fetch_page')}/{row.get('fetch_mpr')!r}, "
                         f"expected hardware page {HWPAGE_PAGE}/${HWPAGE_BANK:X}")
    if row["instructions_executed"] != HWPAGE_EXPECTED_EXECUTED[base] + 1:
        raise ValueError(f"{mode}: violation step count wrong "
                         f"(executed {row['instructions_executed']})")
    if base == HWPAGE_MODE:
        # At $4000 the CPU fetch also lands the status clear.
        if row["vdc_sr_before"] != SR_SENTINEL_BEFORE or row["vdc_sr_after"] != 0x00:
            raise ValueError(f"{mode}: SR not cleared by the stepped fetch "
                             f"({row['vdc_sr_before']:#x}->{row['vdc_sr_after']:#x})")
    # After the violation step the PC must sit inside the hardware page, held.
    if not (HWPAGE_PAGE << 13) <= row["stop_pc"] < (HWPAGE_PAGE + 1) << 13:
        raise ValueError(f"{mode}: stop PC {row['stop_pc']:#x} is outside the hardware page")
    if row["after"]["pc"] != row["stop_pc"]:
        raise ValueError(f"{mode}: stop PC not held")
    return HAZARD_VERDICT


def check_hazard_not_latched(row, mode):
    """No-hazard-latch mutation: resume attempts repeat the forbidden MMIO read (extra reads)."""
    base = _base_of(mode)
    if base not in HWPAGE_EXPECTED_EXECUTED:
        raise ValueError(f"{mode}: hazard-latch check unknown base {base!r}")
    _common(row, mode)
    if row.get("armed") is not True:
        raise ValueError(f"{mode}: run is unarmed")
    if row.get("hazard_landed") is not True or row.get("fetch_via") != "read":
        raise ValueError(f"{mode}: hazard was not observed through the unsafe fetch")
    # The repeated reads must be the hardware-page fetch itself, not an unrelated MMIO access.
    if row.get("fetch_page") != HWPAGE_PAGE or row.get("fetch_mpr") != HWPAGE_BANK:
        raise ValueError(f"{mode}: fetch page/bank {row.get('fetch_page')}/{row.get('fetch_mpr')!r}, "
                         f"expected hardware page {HWPAGE_PAGE}/${HWPAGE_BANK:X}")
    if row.get("fetch_unsafe_bank") is not True:
        raise ValueError(f"{mode}: fetch did not resolve through the unsafe bank")
    if row["after"]["mpr"][HWPAGE_PAGE] != HWPAGE_BANK:
        raise ValueError(f"{mode}: hardware-page mapping inconsistent")
    if not (HWPAGE_PAGE << 13) <= row["stop_pc"] < (HWPAGE_PAGE + 1) << 13:
        raise ValueError(f"{mode}: stop {row['stop_pc']:#x} is outside the hardware page")
    if row["after"]["pc"] != row["stop_pc"]:
        raise ValueError(f"{mode}: stop PC not held")
    if row["instructions_executed"] != HWPAGE_EXPECTED_EXECUTED[base]:
        raise ValueError(f"{mode}: executed {row['instructions_executed']} (expected "
                         f"{HWPAGE_EXPECTED_EXECUTED[base]})")
    attempts = row.get("mmio_read_attempts")
    # The unlatch re-reads exactly once per resume (initial + 3 attempts).
    if type(attempts) is not int or attempts != RESUME_ATTEMPTS + 1:
        raise ValueError(f"{mode}: mmio_read_attempts {attempts!r}, expected {RESUME_ATTEMPTS + 1}")
    # The latch-free run still refuses the step on every resume: count stays flat, reads repeat.
    if row.get("resume_attempts") != RESUME_ATTEMPTS or row.get("resume_refused") != RESUME_ATTEMPTS:
        raise ValueError(f"{mode}: resume not fully refused")
    if row.get("executed_at_refusal") != row["instructions_executed"] or \
            row.get("executed_after_resume") != row["instructions_executed"]:
        raise ValueError(f"{mode}: instructions executed during resume attempts")
    return HAZARD_VERDICT


_CHECK_BY_SUFFIX = (
    ("-no-hazard-latch", check_hazard_not_latched),
    ("-unsafe-read", check_unsafe_read_hazard),
    ("-peek-fail-step", check_peek_fail_step),
    ("-unguarded", check_unguarded),
)


def check_mutation(row, mode):
    for suffix, checker in _CHECK_BY_SUFFIX:
        if mode.endswith(suffix):
            return checker(row, mode)
    return check_bypassed_mutation(row, mode)


def mutation_test_armed(refused_row, landed_row, mode, historical=False):
    """Falsified armed observations and the disabled-monitor run must all be rejected by check_refused."""
    base = copy.deepcopy(refused_row)
    kind = _st_kind_of(refused_row, mode)
    other_op = 0x13 if kind != "st1" else 0x03
    mutations = [
        ("monitor_disabled_run_as_armed", copy.deepcopy(landed_row)),
        ("status_ok", {"status": "ok"}),
        ("refused_false", {"refused": False}),
        ("refused_opcode_wrong", {"refused_opcode": other_op}),
        ("executed_past_st", {"instructions_executed": base["instructions_executed"] + 1}),
        ("resume_bypass_one_step", {"executed_after_resume": base["instructions_executed"] + 1}),
        ("resume_not_refused", {"resume_refused": RESUME_ATTEMPTS - 1}),
        ("side_effect_landed", ({"vdc_ar_after": 0x07} if kind == "st0" else
                                {"vdc_r_after": 0x5A} if kind == "st1" else {"vdc_r_after": 0xA500})),
        ("mpr0_ff", {"before": {"mpr": [0xFF] + [0] * 7}, "after": {"mpr": [0xFF] + [0] * 7}}),
        ("sr_side_effect", {"vdc_sr_before": SR_SENTINEL_BEFORE, "vdc_sr_after": 0x00}),
        ("missing_executed", "instructions_executed"),
    ]
    if not historical:
        mutations.extend([
            ("fetch_via_read", {"fetch_via": "read"}),
            ("detected_pc_removed", {"detected_pc": None}),
            ("detected_pc_wrong", {"detected_pc": base["refused_pc"] + 1}),
        ])
    if all(type(base.get(key)) is int for key in ("fetch_opcode", "fetch_page", "fetch_mpr")):
        mutations.extend([
            ("fetch_opcode_missing", {"fetch_opcode": None}),
            ("fetch_page_missing", {"fetch_page": None}),
            ("fetch_mpr_missing", {"fetch_mpr": None}),
            ("fetch_opcode_mismatch", {"fetch_opcode": other_op}),
            ("fetch_page_mismatch", {"fetch_page": (ST_EXPECTED_PC[mode] >> 13) ^ 1}),
            ("fetch_mpr_mismatch", {"fetch_mpr": base["fetch_mpr"] ^ 1}),
        ])
    for name, overwrite in mutations:
        if name == "monitor_disabled_run_as_armed":
            broken = copy.deepcopy(landed_row)
            broken["mode"] = mode
            broken["armed"] = True
            broken["boundary_check"] = True
        else:
            broken = copy.deepcopy(base)
            if isinstance(overwrite, str):
                broken.pop(overwrite, None)
            else:
                broken.update(overwrite)
        try:
            (check_refused_historical if historical else check_refused)(broken, mode)
        except ValueError:
            continue  # falsified observation rejected
        raise ValueError(f"mutation test FAILED: {mode} {name} accepted by the armed classifier")


def mutation_test_hardware_page(safe_row, mode):
    """The composite hwpage classifier must reject hazard, stepped, out-of-page and tampered rows.

    Tamper cases are judged with the *same* mode as the safe row; the safe row is confirmed
    to pass first (so a checker that rejects everything cannot pass), and each case must fail
    for its own reason (matched by error text).
    """
    if mode not in HWPAGE_EXPECTED_EXECUTED:
        raise ValueError(f"mutation test {mode}: not a hardware-page mode")
    expected = HWPAGE_EXPECTED_EXECUTED[mode]
    if safe_row.get("mode") != mode:
        raise ValueError(f"mutation test {mode}: safe row is mode {safe_row.get('mode')!r}")
    check_hardware_page_blocked(copy.deepcopy(safe_row), mode)
    cases = [
        ("stop_pc_outside_page", {"stop_pc": 0x3FFF, "after": {"pc": 0x3FFF}, "detected_pc": 0x3FFF},
         "outside the hardware page"),
        ("detected_pc_wrong", {"detected_pc": None}, "detected_pc"),
        ("step_executed_at_unsafe_pc", {"instructions_executed": expected + 1,
                                        "fetch_open_stepped": True, "status": "fetch_open_stepped"},
         "did not fail closed"),
        ("sr_cleared", {"vdc_sr_after": 0x00}, "forbidden read landed"),
        ("sr_cleared_but_sr_row_invariant", {"vdc_sr_before": 0x00, "vdc_sr_after": 0x00,
                                             "hazard_landed": True}, "fail-closed"),
        ("mpr_mismatch", {"after": {"mpr": [0, 0xF8, 0x00, 0, 0, 0, 0, 0]}}, "remap"),
        ("fetch_via_read", {"fetch_via": "read"}, "TryPeek"),
        ("resumed_step", {"executed_after_resume": expected + 1}, "after the fail-closed"),
        ("rb_after_changed", {"vdc_read_buffer_after": safe_row["vdc_read_buffer_before"] ^ 0xFF},
         "read buffer"),
        ("rb_before_changed", {"vdc_read_buffer_before": safe_row["vdc_read_buffer_after"] ^ 0xFF},
         "read buffer"),
        ("rb_uniformly_tampered", {"vdc_read_buffer_before": 0x0000, "vdc_read_buffer_after": 0x0000},
         "read buffer"),
        ("missing_fetch_failed", "fetch_failed", "non-bool"),
    ]
    for name, overwrite, reason in cases:
        broken = copy.deepcopy(safe_row)
        if isinstance(overwrite, str):
            broken.pop(overwrite, None)
        else:
            for key, value in overwrite.items():
                if key == "after" and isinstance(value, dict):
                    broken["after"].update(value)
                else:
                    broken[key] = value
        try:
            check_hardware_page_blocked(broken, mode)
        except ValueError as error:
            if reason not in str(error):
                raise ValueError(f"mutation test {mode} {name}: rejected for the wrong reason "
                                 f"({error}); expected {reason!r}") from error
            continue
        raise ValueError(f"mutation test FAILED: {mode} {name} accepted by the hwpage classifier")


def to_jsr_observation(row):
    """Map one probe row to tools/jsr_observation.py v1. Returns None for hazard rows: schema v1
    has no hazard outcome, so hazard rows are excluded from the JSR view rather than being
    misreported as a boundary refusal ("ok" would claim success, "rejected" would claim a
    pre-side-effect refusal; neither is true). The EVIDENCE line is authoritative for hazards.
    IRQ is OBSERVED only for an accepted IRQ-survey IRQ (irq_accepted True in a survey mode
    with well-typed irr/idr); otherwise UNKNOWN -- mere field presence is not observation."""
    if row.get("hazard_landed") is True or row.get("fetch_open_stepped") is True:
        return None
    mapped = copy.deepcopy(row)
    mapped["status"] = "rejected" if row["status"] in ("refused", "fetch_blocked") else "ok"
    observation = jsr_observation.from_probe_row(
        mapped, {"kind": "REAL_CORE_EXECUTION", "core_revision": PINNED_CORE_REVISION,
                 "bios": "SYNTHETIC", "real_core_claim": True})
    survey = _base_of(row.get("mode", "")) in IRQ_SURVEY_MODES
    accepted = (survey and row.get("irq_accepted") is True
                and type(row.get("irr")) is int and type(row.get("idr")) is int
                and 0 <= row["irr"] <= 0xFF and 0 <= row["idr"] <= 0xFF)
    if accepted:
        observation["irq"] = {"status": "OBSERVED", "mask": int(row["idr"]),
                              "pending": int(row["irr"])}
        jsr_observation.validate_observation(observation)
    return observation


def main():
    with tempfile.TemporaryDirectory(prefix="pce-st-boundary-") as tmp:
        directory = Path(tmp)
        bios, cue = synthetic_media(directory)
        probe = compile_probe(directory, source_name="geargrafx_st_boundary.cpp",
                              target_name="st-boundary-probe")
        verdicts = {}
        hwp_safe_row = None
        for mode in ARMED_MODES:
            if mode in HWPAGE_EXPECTED_EXECUTED:
                refused_row = run_mode(probe, bios, cue, mode, 0)
                verdicts[mode] = check_hardware_page_blocked(refused_row, mode)
                hwp_safe_row = refused_row
            else:
                refused_row = run_mode(probe, bios, cue, mode, 0)
                verdicts[mode] = check_refused(refused_row, mode)
            mutations = []
            for mutation in MUTATIONS[mode]:
                landed_row = run_mode(probe, bios, cue, mutation, 0)
                verdicts[mutation] = check_mutation(landed_row, mutation)
                mutations.append((mutation, verdicts[mutation], landed_row))
            if mode in HWPAGE_EXPECTED_EXECUTED:
                for mutation, _verdict, hazard_row in mutations:
                    try:
                        check_hardware_page_blocked({**hazard_row, "mode": mode}, mode)
                    except ValueError:
                        continue
                    raise ValueError(f"mutation test FAILED: {mutation} accepted as fail-closed")
                mutation_test_hardware_page(refused_row, mode)
            else:
                for mutation, _verdict, landed_row in mutations:
                    try:
                        check_refused({**landed_row, "mode": mode}, mode)
                    except ValueError:
                        continue
                    raise ValueError(f"mutation test FAILED: {mode} {mutation} accepted as refused")
                mutation_test_armed(refused_row, mutations[0][2], mode)
            for case, verdict, row in [(mode, verdicts[mode], refused_row), *mutations]:
                print("EVIDENCE " + json.dumps({"case": case, "observation_kind": "REAL_CORE_EXECUTION",
                                                "core_revision": PINNED_CORE_REVISION, "bios": "SYNTHETIC",
                                                "execution_mode": "debug-single-step", "verdict": verdict,
                                                "measured_stop_pc": row["stop_pc"], "row": row}, sort_keys=True))
                jsr = to_jsr_observation(row)
                if jsr is None:
                    print(f"JSR_OBSERVATION_EXCLUDED {case} hazard rows have no jsr-observation v1 "
                          f"outcome (EVIDENCE is authoritative)")
                else:
                    print("JSR_OBSERVATION " + json.dumps(jsr, sort_keys=True))
            print(f"ARMED {mode}: status={refused_row['status']} stop={refused_row['stop_pc']:#x} "
                  f"executed={refused_row['instructions_executed']} "
                  f"resume_refused={refused_row['resume_refused']}/{refused_row['resume_attempts']} "
                  f"sr={refused_row['vdc_sr_before']:#x}->{refused_row['vdc_sr_after']:#x} -> {verdicts[mode]}")
            for mutation, verdict, row in mutations:
                print(f"MUTATION {mutation}: status={row['status']} stop={row['stop_pc']:#x} "
                      f"executed={row['instructions_executed']} "
                      f"sr={row['vdc_sr_before']:#x}->{row['vdc_sr_after']:#x} -> {verdict}")
        check_pos(run_mode(probe, bios, cue, "pos-write", POS_CONTROL), POS_CONTROL)
        assert hwp_safe_row is not None
        print("PASS issue-365 ST0/ST1/ST2 instruction-boundary monitor (synthetic ROM, pinned core):")
        print(f"  armed: {len(ARMED_MODES)} modes refused/failed-closed before side effect, "
              f"resume refused {RESUME_ATTEMPTS}/{RESUME_ATTEMPTS}")
        print(f"  mutation: hazard/bypass runs made the prohibited effect observable, "
              f"armed classifiers rejected every mutated run")
        print(f"  positive control: allowlisted store ${POS_CONTROL:04X} ran unrefused and landed")
        print("LIMIT: synthetic single-stepped guests only; not a general VDC write interceptor; no gate change.")
        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, KeyError, TypeError, subprocess.TimeoutExpired) as error:
        print(f"FAIL issue-365 ST boundary monitor: {error}", file=sys.stderr)
        raise SystemExit(1) from error
