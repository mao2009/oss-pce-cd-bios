#!/usr/bin/env python3
"""Issue #365: instruction-boundary opcode monitor for ST0/ST1/ST2 on the pinned real core.

Pinned real Geargrafx instruction-debugger core (local GPL-3.0 link, b49ae82a012eade566d72e9d61c9d95a5caa4da3;
no core code copied here), fully synthetic BIOS/CD, MPR0 fixed to $00 (never $FF).

The probe (geargrafx_st_boundary.cpp) single-steps one instruction at a time and reads the opcode at PC
BEFORE every step, including the entry instruction. ST0 ($03) / ST1 ($13) / ST2 ($23) are refused before
the step, and a resume attempt re-runs the same check and is refused again. The monitor is given no PC hint.

Modes:
* ``st0-entry``: ST0 is the entry instruction. Refused before any instruction runs (0 executed).
* ``st1-unknown`` / ``st2-unknown``: a JMP reaches the ST at $3E10; only the JMP executes, then refusal.
* ``stN-unguarded`` (MUTATION): same guests, monitor disabled. The ST must execute and land in the VDC,
  and the armed classifier must reject the run.
* ``pos-write``: positive control. An allowlisted RAM store runs unrefused and lands.

Scope: the check covers every instruction boundary of this single-stepped run (synthetic guests only).
It is NOT a general VDC write interceptor for a free-running guest, and it does not reason about data.
Nothing here releases a gate: slot $01 stays BLOCKED, release_eligible stays false, and no reference-BIOS
ABI or DEV ROM wiring is claimed.

Run manually (not wired into the Makefile integration target by this change):
    python3 tests/integration/geargrafx_st_boundary.py
"""
import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from geargrafx_api import compile_probe  # noqa: E402
from geargrafx_reference_guard import check_pos, run_mode, synthetic_media  # noqa: E402

PINNED_CORE_REVISION = "b49ae82a012eade566d72e9d61c9d95a5caa4da3"
ST_OPCODE = {"st0": 0x03, "st1": 0x13, "st2": 0x23}
ST_EXPECTED_PC = {"st0-entry": 0x3E00, "st1-unknown": 0x3E10, "st2-unknown": 0x3E10,
                  "st2-mpr2-unknown": 0x4010}
ST_EXECUTED_BEFORE_REFUSAL = {"st0-entry": 0, "st1-unknown": 1, "st2-unknown": 1,
                              "st2-mpr2-unknown": 3}
ARMED_MODES = tuple(ST_EXPECTED_PC)
MUTATIONS = {"st0-entry": ("st0-entry-no-entry-check",),
             "st1-unknown": ("st1-unguarded",),
             "st2-unknown": ("st2-unguarded",),
             "st2-mpr2-unknown": ("st2-mpr2-no-recheck", "st2-mpr2-wrong-opcode")}
POS_CONTROL = 0x3E50
MPR0_NON_FF = 0x00
RESUME_ATTEMPTS = 3
REFUSED_VERDICT = "refused_at_instruction_boundary_before_side_effect"
LANDED_VERDICT = "side_effect_landed_monitor_disabled"


def _kind(mode):
    return mode[:3]


def _vdc_landed(row, kind):
    if kind == "st0":
        return row["vdc_ar_before"] == 0 and row["vdc_ar_after"] == 0x07
    if kind == "st1":
        return row["vdc_r_before"] == 0 and (row["vdc_r_after"] & 0xFF) == 0x5A
    return row["vdc_r_before"] == 0 and (row["vdc_r_after"] >> 8) == 0xA5


def _common(row, mode):
    if row.get("exit", 0) != 0:
        raise ValueError(f"{mode} probe refused to run (exit {row.get('exit')}): {row.get('stderr', '')[-300:]}")
    if row.get("mode") != mode:
        raise ValueError(f"{mode} measured wrong mode: {row.get('mode')}")
    for side in ("before", "after"):
        mpr = row.get(side, {}).get("mpr", [])
        if type(mpr) is not list or len(mpr) != 8 or mpr[0] != MPR0_NON_FF:
            raise ValueError(f"{mode} MPR0 {side} not {MPR0_NON_FF:#x} (!= $FF): {mpr[:1]}")
    dynamic_mpr2 = mode == "st2-mpr2-unknown"
    if not dynamic_mpr2 and row.get("before", {}).get("mpr") != row.get("after", {}).get("mpr"):
        raise ValueError(f"{mode}: MPR configuration changed during the run")
    if dynamic_mpr2 and (row["before"]["mpr"][2], row["after"]["mpr"][2]) != (0, 0xF8):
        raise ValueError(f"{mode}: guest MPR2 remap was not observed")
    for name in ("vdc_ar_before", "vdc_ar_after", "vdc_r_before", "vdc_r_after", "stop_pc",
                 "instructions_executed"):
        if type(row.get(name)) is not int:
            raise ValueError(f"{mode} missing/non-integer {name}: {row.get(name)!r}")


def check_refused(row, mode):
    """Armed classifier: the ST must be refused at its instruction boundary, with no side effect."""
    _common(row, mode)
    if row.get("status") != "refused" or row.get("refused") is not True:
        raise ValueError(f"{mode}: monitor did not refuse (status={row.get('status')})")
    if row.get("armed") is not True or row.get("boundary_check") is not True:
        raise ValueError(f"{mode}: run is not armed")
    kind = _kind(mode)
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
    if row["vdc_ar_before"] != row["vdc_ar_after"] or row["vdc_r_before"] != row["vdc_r_after"]:
        raise ValueError(f"{mode}: VDC state changed before the refusal "
                         f"(AR {row['vdc_ar_before']:#x}->{row['vdc_ar_after']:#x}, "
                         f"R {row['vdc_r_before']:#x}->{row['vdc_r_after']:#x})")
    if _vdc_landed(row, kind):
        raise ValueError(f"{mode}: VDC side effect landed although the ST was refused")
    # PR #418's immutable historical rows predate fetch-map reporting.  New
    # mpr2 evidence requires it; current probe runs always emit all three fields.
    has_fetch = all(type(row.get(key)) is int for key in ("fetch_opcode", "fetch_page", "fetch_mpr"))
    if has_fetch and (row["fetch_opcode"] != ST_OPCODE[kind] or row["fetch_page"] != row["refused_pc"] >> 13):
        raise ValueError(f"{mode}: monitor opcode does not match the CPU fetch boundary")
    if mode == "st2-mpr2-unknown" and (not has_fetch or row["fetch_mpr"] != 0xF8):
        raise ValueError(f"{mode}: ST2 was not checked through remapped MPR2")
    return REFUSED_VERDICT


def check_unguarded(row, mode):
    """Mutation run (monitor disabled): the ST must execute and its VDC side effect must land."""
    _common(row, mode)
    if row.get("armed") is not False or row.get("refused") is not False:
        raise ValueError(f"{mode}: unguarded run reports armed/refused")
    if row.get("status") != "ok":
        raise ValueError(f"{mode}: unguarded run did not reach its self-loop (status={row.get('status')})")
    if not _vdc_landed(row, _kind(mode)):
        raise ValueError(f"{mode}: VDC side effect did not land with the monitor disabled")
    return LANDED_VERDICT


def check_bypassed_mutation(row, mode):
    """A real monitor mutation must execute ST and therefore cannot pass as refusal."""
    normalized = mode.replace("-no-entry-check", "-entry").replace("-no-recheck", "-unknown") \
        .replace("-wrong-opcode", "-unknown")
    checked = copy.deepcopy(row)
    checked["mode"] = normalized
    _common(checked, normalized)
    if row.get("armed") is not True or row.get("boundary_check") is not True:
        raise ValueError(f"{mode}: named mutation disabled the entire boundary monitor")
    if row.get("status") != "ok" or row.get("refused") is not False or not _vdc_landed(row, _kind(mode)):
        raise ValueError(f"{mode}: mutation did not execute the prohibited ST")
    return LANDED_VERDICT


def mutation_test_armed(refused_row, landed_row, mode):
    """Falsified armed observations and the disabled-monitor run must all be rejected by check_refused."""
    base = copy.deepcopy(refused_row)
    kind = _kind(mode)
    other_op = 0x13 if kind != "st1" else 0x03
    mutations = [
        ("monitor_disabled_run_as_armed", copy.deepcopy(landed_row)),
        ("status_ok", {"status": "ok"}),
        ("refused_false", {"refused": False}),
        ("refused_opcode_wrong", {"refused_opcode": other_op}),
        ("refused_pc_off_by_one", {"refused_pc": ST_EXPECTED_PC[mode] + 1}),
        ("executed_past_st", {"instructions_executed": ST_EXECUTED_BEFORE_REFUSAL[mode] + 1}),
        ("resume_bypass_one_step", {"executed_after_resume": base["instructions_executed"] + 1}),
        ("resume_not_refused", {"resume_refused": RESUME_ATTEMPTS - 1}),
        ("side_effect_landed", ({"vdc_ar_after": 0x07} if kind == "st0" else
                                {"vdc_r_after": 0x5A} if kind == "st1" else {"vdc_r_after": 0xA500})),
        ("mpr0_ff", {"before": {"mpr": [0xFF] + [0] * 7}, "after": {"mpr": [0xFF] + [0] * 7}}),
    ]
    for name, overwrite in mutations:
        if name == "monitor_disabled_run_as_armed":
            broken = overwrite
        else:
            broken = copy.deepcopy(base)
            broken.update(overwrite)
        try:
            check_refused(broken, mode)
        except ValueError:
            continue  # falsified observation rejected
        raise ValueError(f"mutation test FAILED: {mode} {name} accepted by the armed classifier")


def main():
    with tempfile.TemporaryDirectory(prefix="pce-st-boundary-") as tmp:
        directory = Path(tmp)
        bios, cue = synthetic_media(directory)
        probe = compile_probe(directory, source_name="geargrafx_st_boundary.cpp",
                              target_name="st-boundary-probe")
        verdicts = {}
        for mode in ARMED_MODES:
            kind = _kind(mode)
            refused_row = run_mode(probe, bios, cue, mode, 0)
            verdicts[mode] = check_refused(refused_row, mode)
            mutations = []
            for mutation in MUTATIONS[mode]:
                landed_row = run_mode(probe, bios, cue, mutation, 0)
                verdicts[mutation] = (check_unguarded(landed_row, mutation)
                                      if mutation.endswith("-unguarded") else check_bypassed_mutation(landed_row, mutation))
                mutations.append((mutation, verdicts[mutation], landed_row))
                broken = copy.deepcopy(landed_row); broken["mode"] = mode
                with_error = False
                try:
                    check_refused(broken, mode)
                except ValueError:
                    with_error = True
                if not with_error:
                    raise ValueError(f"mutation test FAILED: {mode} {mutation} accepted as refused")
            mutation_test_armed(refused_row, mutations[0][2], mode)
            for case, verdict, row in [(mode, verdicts[mode], refused_row), *mutations]:
                print("EVIDENCE " + json.dumps({"case": case, "observation_kind": "REAL_CORE_EXECUTION",
                                                "core_revision": PINNED_CORE_REVISION, "bios": "SYNTHETIC",
                                                "verdict": verdict, "row": row}, sort_keys=True))
            print(f"ARMED {mode}: status={refused_row['status']} refused_pc={refused_row['refused_pc']:#x} "
                  f"opcode={refused_row['refused_opcode']:#x} executed={refused_row['instructions_executed']} "
                  f"resume_refused={refused_row['resume_refused']}/{refused_row['resume_attempts']} "
                  f"vdc_ar={refused_row['vdc_ar_before']:#x}->{refused_row['vdc_ar_after']:#x} "
                  f"vdc_r={refused_row['vdc_r_before']:#x}->{refused_row['vdc_r_after']:#x} -> {verdicts[mode]}")
            for mutation, verdict, row in mutations:
                print(f"MUTATION {mutation}: status={row['status']} stop_pc={row['stop_pc']:#x} "
                      f"vdc_ar={row['vdc_ar_before']:#x}->{row['vdc_ar_after']:#x} "
                      f"vdc_r={row['vdc_r_before']:#x}->{row['vdc_r_after']:#x} -> {verdict}")
        check_pos(run_mode(probe, bios, cue, "pos-write", POS_CONTROL), POS_CONTROL)
    print("PASS issue-365 ST0/ST1/ST2 instruction-boundary monitor (synthetic ROM, pinned core):")
    print(f"  armed: {len(ARMED_MODES)} cases refused before side effect, resume refused {RESUME_ATTEMPTS}/{RESUME_ATTEMPTS}")
    print(f"  mutation: monitor disabled -> side effect lands, armed classifier rejects it")
    print(f"  positive control: allowlisted store ${POS_CONTROL:04X} ran unrefused and landed")
    print("LIMIT: synthetic single-stepped guests only; not a general VDC write interceptor; no gate change.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        print(f"FAIL issue-365 ST boundary monitor: {error}", file=sys.stderr)
        raise SystemExit(1) from error
