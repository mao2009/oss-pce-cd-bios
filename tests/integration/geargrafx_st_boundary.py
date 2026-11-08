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
* Stop PC: for fixed-guest ST refusals the PC is deterministic and exact-matched; for the dynamic
  hardware-page entry (BBR3-class branch into MMIO: the branch outcome depends on the VDC read
  buffer, so the landing PC varies) only hardware-page membership plus MPR-mapping consistency is
  required. A measured stop PC (e.g. $4002 observed in one run) is recorded as evidence, never
  treated as a fixed expectation. A stop inside the hardware page with no TryPeek failure, or with
  a step already issued, is FAIL, not PASS.

Modes (15 armed families + positive control):
* ``stN-entry``: ST is the entry instruction. Refused before any instruction runs (0 executed).
* ``stN-unknown`` / ``stN-unknown-far``: a JMP reaches the ST at an unknown PC (monitor has no hint).
* ``st1-branch-unknown``: a taken BNE (data-dependent branch) reaches the ST at $3E10.
* ``st0-jsr-rts``: a JSR subroutine returns; ST0 waits right after RTS.
* ``st1-page-boundary``: ST1 opcode at $1FFF (operand would cross into page 1 / WRAM).
* ``st0-rom-page0``: ST0 opcode at $0000 (ROM bank 0 through MPR0=$00).
* ``st2-mpr2-unknown``: guest TAM changes MPR2, then JMP reaches ST2 in page 2.
* ``st0-hwpage-fetch-blocked``: guest TAM maps page 2 to $FF; fetch at $4000 must fail closed.
  This is the dynamic-MMIO-entry (BBR3-class) case: classified by composite conditions only.
* ``st1-unused-bank``: guest TAM maps page 3 to unused bank $88; ST1 fetched from that bank.
* ``irq2-handler-st0-survey`` / ``irq2-core-stop-survey``: IRQ2 accepted at an instruction boundary;
  the handler-head ST0 is refused. The two differ only in the core debugger stop_on_irq flag.
* ``pos-write``: positive control. An allowlisted RAM store runs unrefused and lands.
Mutation suffixes (8 kinds, 17 runs): -unguarded, -no-entry-check, -no-recheck (MPR2 recheck only),
-wrong-opcode (ST2 omission only: single-opcode-list demo), -knownpc-only, -resume-allowed,
-unsafe-read, -peek-fail-step.

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
    "irq2-handler-st0-survey": 2,
    "irq2-core-stop-survey": 2,
}
# Guest-remapped MPR transitions (page, before, after); all other modes keep MPR stable.
MPR_TRANSITION = {
    "st2-mpr2-unknown": (2, 0x00, 0xF8),
    "st0-hwpage-fetch-blocked": (2, 0x00, 0xFF),
    "st1-unused-bank": (3, 0x00, 0x88),
}

# --- Dynamic hardware-page entry (BBR3-class): composite classification, no fixed PC. -------------
HWPAGE_MODE = "st0-hwpage-fetch-blocked"
HWPAGE_EXPECTED_EXECUTED = 3  # LDA + TAM + JMP; the unsafe fetch itself must never step
HWPAGE_PAGE = 2  # CPU page 2 (addresses $4000-$5FFF)
HWPAGE_BANK = 0xFF
SR_SENTINEL_BEFORE = 0x3F
AR_SENTINEL_BEFORE = 5

ARMED_MODES = tuple(ST_EXPECTED_PC) + (HWPAGE_MODE,)
IRQ_SURVEY_MODES = ("irq2-handler-st0-survey", "irq2-core-stop-survey")

# Mutation kinds (8, 17 runs): each keeps the monitor armed unless its name says otherwise, and must make the
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
    HWPAGE_MODE: (HWPAGE_MODE + "-unsafe-read", HWPAGE_MODE + "-peek-fail-step"),
    "st1-unused-bank": ("st1-unused-bank-knownpc-only",),
    "irq2-handler-st0-survey": ("irq2-handler-st0-survey-knownpc-only",),
    "irq2-core-stop-survey": ("irq2-core-stop-survey-knownpc-only",),
}
MUTATION_SUFFIXES = ("-no-entry-check", "-knownpc-only", "-resume-allowed",
                     "-peek-fail-step", "-wrong-opcode", "-no-recheck", "-unguarded")
HAZARD_SUFFIXES = ("-unsafe-read", "-peek-fail-step")


def _base_of(mode):
    for suffix in MUTATION_SUFFIXES + ("-unsafe-read",):
        if mode.endswith(suffix):
            return mode[: -len(suffix)]
    return mode


def _st_kind_of(row, mode):
    kind = row.get("st_mode") or ""
    if kind in ST_OPCODE:
        return kind
    base = _base_of(mode)
    return base[:3] if base[:3] in ST_OPCODE else "st0"


def _vdc_landed(row, kind):
    if kind == "st0":
        return row["vdc_ar_before"] == 0 and row["vdc_ar_after"] == 0x07
    if kind == "st1":
        return row["vdc_r_before"] == 0 and (row["vdc_r_after"] & 0xFF) == 0x5A
    return row["vdc_r_before"] == 0 and (row["vdc_r_after"] >> 8) == 0xA5


def _common(row, mode, historical=False):
    """Validate the evidence envelope. Live runs require the full probe surface (fetch/SR/RAM)."""
    if row.get("exit", 0) != 0:
        raise ValueError(f"{mode} probe refused to run (exit {row.get('exit')}): {row.get('stderr', '')[-300:]}")
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
    if not historical:
        for name in ("vdc_sr_before", "vdc_sr_after", "fetch_opcode", "fetch_page", "fetch_mpr",
                     "fetch_safe", "fetch_failed", "fetch_blocked", "hazard_landed",
                     "fetch_open_stepped", "refused", "instructions_executed",
                     "executed_at_refusal", "executed_after_resume", "resume_attempts", "resume_refused"):
            if name in ("fetch_safe", "fetch_failed", "fetch_blocked", "hazard_landed",
                        "fetch_open_stepped", "refused"):
                if type(row.get(name)) is not bool:
                    raise ValueError(f"{mode} missing/non-bool {name}: {row.get(name)!r}")
            elif type(row.get(name)) is not int:
                raise ValueError(f"{mode} missing/non-integer {name}: {row.get(name)!r}")
        if type(row.get("fetch_via")) is not str or row["fetch_via"] not in ("peek", "read"):
            raise ValueError(f"{mode} fetch_via must be peek/read: {row.get('fetch_via')!r}")
        # Hazard mutations intentionally exercise the unsafe fetch path; their dedicated checkers
        # validate the exact hazard shape. The armed hwpage run is a TryPeek failure by design.
        if mode.endswith(HAZARD_SUFFIXES):
            return
        if row["fetch_via"] != "peek":
            raise ValueError(f"{mode} live armed run fetched via {row['fetch_via']!r}, not TryPeek")
        if _base_of(mode) != HWPAGE_MODE:
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
    kind = _st_kind_of(row, mode)
    if row.get("refused_opcode") != ST_OPCODE[kind]:
        raise ValueError(f"{mode}: refused opcode {row.get('refused_opcode')!r}, expected {ST_OPCODE[kind]:#x}")
    if row["refused_pc"] != ST_EXPECTED_PC[mode] or row["stop_pc"] != ST_EXPECTED_PC[mode]:
        raise ValueError(f"{mode}: refused at {row['refused_pc']:#x} / stop {row['stop_pc']:#x}, "
                         f"expected {ST_EXPECTED_PC[mode]:#x}")
    if row["instructions_executed"] != ST_EXECUTED_BEFORE_REFUSAL[mode]:
        raise ValueError(f"{mode}: executed {row['instructions_executed']} instructions, expected "
                         f"{ST_EXECUTED_BEFORE_REFUSAL[mode]} (the ST itself must not run)")
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
    if mode in MPR_TRANSITION:
        page, _before, after = MPR_TRANSITION[mode]
        if row["fetch_page"] != page or row["fetch_mpr"] != after:
            raise ValueError(f"{mode}: ST fetch did not resolve through remapped page {page}")
    if mode in IRQ_SURVEY_MODES:
        _check_irq_survey(row, mode)
    return REFUSED_VERDICT


def _check_irq_survey(row, mode):
    """IRQ-acceptance side effects: only the architectural stack pushes may touch WRAM."""
    if row.get("irq_accepted") is not True:
        raise ValueError(f"{mode}: IRQ2 was not accepted at an instruction boundary")
    if row.get("observed_vector") != 0xFFF6:
        raise ValueError(f"{mode}: observed IRQ vector {row.get('observed_vector')!r}, expected $FFF6")
    if row["after"]["sp"] != 0xFC:
        raise ValueError(f"{mode}: SP after acceptance is {row['after']['sp']:#x} ($FC = 3 pushes)")
    if row.get("only_stack_writes") is not True:
        raise ValueError(f"{mode}: non-stack WRAM changed during IRQ acceptance")


def check_refused(row, mode):
    """Validate live probe evidence; fetch opcode and mapping fields are mandatory."""
    return _check_refused(row, mode, require_fetch=True)


def check_refused_historical(row, mode):
    """Validate immutable pre-fetch-map records only; never used by live runs."""
    return _check_refused(row, mode, require_fetch=False)


def check_hardware_page_blocked(row, mode=HWPAGE_MODE):
    """Dynamic-MMIO-entry (BBR3-class) classifier: composite conditions, no fixed stop PC.

    A measured stop PC is recorded as evidence but never required to equal any constant: the branch
    outcome (and therefore the landing PC) depends on the VDC read buffer. PASS requires ALL of:
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
    # Executed count covers exactly the guest prologue; the unsafe fetch must never step.
    if row["instructions_executed"] != HWPAGE_EXPECTED_EXECUTED:
        raise ValueError(f"{mode}: executed {row['instructions_executed']} instructions, expected "
                         f"{HWPAGE_EXPECTED_EXECUTED} (a step at the unsafe PC must not issue)")
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
    """TryPeek->Memory::Read mutation: the fetch read itself lands the SR clear (deadly demo)."""
    _common(row, mode)
    if row.get("armed") is not True:
        raise ValueError(f"{mode}: hazard run is unarmed")
    if row.get("hazard_landed") is not True or row.get("fetch_via") != "read":
        raise ValueError(f"{mode}: hazard was not observed through the unsafe fetch")
    if row["vdc_sr_before"] != SR_SENTINEL_BEFORE or row["vdc_sr_after"] != 0x00:
        raise ValueError(f"{mode}: SR hazard {row['vdc_sr_before']:#x}->{row['vdc_sr_after']:#x}, "
                         f"expected {SR_SENTINEL_BEFORE:#x}->0x00")
    return HAZARD_VERDICT


def check_peek_fail_step(row, mode):
    """Fail-open mutation: the peek failure steps anyway and the CPU fetch lands the hazard."""
    _common(row, mode)
    if row.get("armed") is not True:
        raise ValueError(f"{mode}: fail-open run is unarmed")
    if row.get("fetch_open_stepped") is not True or row.get("status") != "fetch_open_stepped":
        raise ValueError(f"{mode}: peek failure did not step anyway")
    if row["vdc_sr_before"] != SR_SENTINEL_BEFORE or row["vdc_sr_after"] != 0x00:
        raise ValueError(f"{mode}: SR not cleared by the stepped fetch "
                         f"({row['vdc_sr_before']:#x}->{row['vdc_sr_after']:#x})")
    return HAZARD_VERDICT


_CHECK_BY_SUFFIX = (
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
        mutations.extend([("fetch_via_read", {"fetch_via": "read"})])
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


def mutation_test_hardware_page(safe_row):
    """The composite hwpage classifier must reject hazard, stepped, out-of-page and tampered rows."""
    cases = [
        ("stop_pc_outside_page", {"stop_pc": 0x3FFF}),
        ("step_executed_at_unsafe_pc", {"instructions_executed": HWPAGE_EXPECTED_EXECUTED + 1,
                                        "fetch_open_stepped": True, "status": "fetch_open_stepped"}),
        ("sr_cleared", {"vdc_sr_after": 0x00}),
        ("sr_cleared_but_sr_row_invariant", {"vdc_sr_before": 0x00, "vdc_sr_after": 0x00,
                                             "hazard_landed": True}),
        ("mpr_mismatch", {"after": {"mpr": [0, 0xF8, 0x00, 0, 0, 0, 0, 0]}}),
        ("fetch_via_read", {"fetch_via": "read"}),
        ("resumed_step", {"executed_after_resume": HWPAGE_EXPECTED_EXECUTED + 1}),
        ("missing_fetch_failed", "fetch_failed"),
    ]
    for name, overwrite in cases:
        broken = copy.deepcopy(safe_row)
        if isinstance(overwrite, str):
            broken.pop(overwrite, None)
        else:
            broken.update(overwrite)
        try:
            check_hardware_page_blocked(broken)
        except ValueError:
            continue
        raise ValueError(f"mutation test FAILED: {HWPAGE_MODE} {name} accepted by the hwpage classifier")


def to_jsr_observation(row):
    """Map one probe row to tools/jsr_observation.py v1 (IRQ UNKNOWN unless actually observed)."""
    mapped = copy.deepcopy(row)
    # Only true pre-side-effect refusals are "rejected". Hazard rows (unsafe-read status reuses
    # "refused") ran with a landed side effect, so the JSR view must not claim a refusal; the
    # reason string retains the hazard shape. (The jsr schema carries no SR field: JSR alone
    # cannot judge hwpage safety; the driver evidence above is authoritative.)
    failed_closed = row["status"] in ("refused", "fetch_blocked") and \
        row.get("hazard_landed") is not True and row.get("fetch_open_stepped") is not True
    mapped["status"] = "rejected" if failed_closed else "ok"
    observation = jsr_observation.from_probe_row(
        mapped, {"kind": "REAL_CORE_EXECUTION", "core_revision": PINNED_CORE_REVISION,
                 "bios": "SYNTHETIC", "real_core_claim": True})
    if "irq_accepted" in row and "irr" in row and "idr" in row:
        observation["irq"] = {"status": "OBSERVED", "mask": int(row["idr"]) & 0xFF,
                              "pending": int(row["irr"]) & 0xFF}
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
            if mode == HWPAGE_MODE:
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
            if mode == HWPAGE_MODE:
                for mutation, _verdict, hazard_row in mutations:
                    try:
                        check_hardware_page_blocked({**hazard_row, "mode": mode}, mode)
                    except ValueError:
                        continue
                    raise ValueError(f"mutation test FAILED: {mutation} accepted as fail-closed")
                mutation_test_hardware_page(refused_row)
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
