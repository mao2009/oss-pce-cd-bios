#!/usr/bin/env python3
"""Issue #365: pre-execution (EXECUTE breakpoint) blocking of ST0/ST1/ST2 on the pinned core.

Pinned real Geargrafx debugger core (local GPL-3.0 link, b49ae82a012eade566d72e9d61c9d95a5caa4da3;
no core code copied into this repo), fully synthetic BIOS/CD, MPR0 fixed to $00 (!= $FF).
Synthetic guest per preblock mode (issue #198 layout plus a leading NOP at $3E00):

* st0: NOP $3E00 / ST0 #$07 at $3E01 / BRA * at $3E03
* st1: NOP $3E00 / ST0 #$00 at $3E01 / ST1 #$5A at $3E03 / BRA * at $3E05
* st2: NOP $3E00 / ST0 #$00 at $3E01 / ST2 #$A5 at $3E03 / BRA * at $3E05

Modes (probe: geargrafx_reference_jsr.cpp):

* ``stN-preblock``: EXECUTE breakpoint armed on the ST opcode PC. Armed run must stop with
  PC == ST PC, the breakpoint flag raised, the VDC readback (AR / R[0]) unchanged and no guest
  RAM store. Only the stop before the known ST PC is observed: no VDC side effect at the stop.
  The probe is not resumed, so refusal-before-resume is NOT tested.
* ``stN-preblock-disarmed``: MUTATION of the blocking mechanism (same guest, breakpoint NOT
  armed). The side effect must land and the armed classifier must reject this run.
* ``pos-write``: positive control, allowlisted page-1 RAM store must land unflagged.

Scope: prevention for the statically known ST PC only. Not a general VDC write interceptor.
Nothing here releases a gate (slot $01 stays BLOCKED; MODEL_ELIGIBLE is not execution permission).
"""
import copy
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from geargrafx_api import compile_probe  # noqa: E402
from geargrafx_reference_guard import check_pos, run_mode, synthetic_media  # noqa: E402

ST_MODES = ("st0", "st1", "st2")
# Preblock layout: one NOP at $3E00 (the breakpoint is checked at the end of the previous
# instruction, so the ST must not be the entry instruction), then the ST opcode.
ST_PC = {"st0": 0x3E01, "st1": 0x3E03, "st2": 0x3E03}      # the ST opcode (must not run)
STOP_AFTER_ST = {"st0": 0x3E03, "st1": 0x3E05, "st2": 0x3E05}  # BRA * after the ST
REG_BEFORE = 0x0000
ST0_AR_AFTER = 0x0007
ST1_R_LO_AFTER = 0x5A
ST2_R_HI_AFTER = 0xA5
MPR0_NON_FF = 0x00
POS_CONTROL = 0x3E50
PREBLOCK_VERDICT = "preblocked_before_side_effect"
LANDED_VERDICT = "side_effect_landed_no_preblock"


def _vdc_changed(row, mode):
    if mode == "st0":
        return row["vdc_ar_after"] == ST0_AR_AFTER and row["vdc_ar_before"] == REG_BEFORE
    if mode == "st1":
        return (row["vdc_r_after"] & 0xFF) == ST1_R_LO_AFTER and row["vdc_r_before"] == REG_BEFORE
    return (row["vdc_r_after"] >> 8) == ST2_R_HI_AFTER and row["vdc_r_before"] == REG_BEFORE


def _common(row, mode, probe_mode):
    if row.get("exit", 0) != 0:
        raise ValueError(f"{probe_mode} probe refused to run (exit {row.get('exit')}): {row.get('stderr', '')[-300:]}")
    if row.get("status") != "ok":
        raise ValueError(f"{probe_mode} observation invalid: {row.get('status')} {row.get('reason')}")
    if row.get("mode") != mode:
        raise ValueError(f"{probe_mode} measured wrong mode: {row.get('mode')}")
    if row.get("preblock") is not True:
        raise ValueError(f"{probe_mode} missing preblock observation")
    for side in ("before", "after"):
        mpr = row.get(side, {}).get("mpr", [])
        if type(mpr) is not list or len(mpr) != 8 or mpr[0] != MPR0_NON_FF:
            raise ValueError(f"{probe_mode} MPR0 {side} not {MPR0_NON_FF:#x} (!= $FF): {mpr[:1]}")
    for name in ("stop_pc", "vdc_ar_before", "vdc_ar_after", "vdc_r_before", "vdc_r_after"):
        if type(row.get(name)) is not int:
            raise ValueError(f"{probe_mode} missing/non-integer {name}: {row.get(name)!r}")
    if type(row.get("detected")) is not bool or type(row.get("execbp_hit")) is not bool:
        raise ValueError(f"{probe_mode} missing detected/execbp_hit flags")


def check_armed(row, mode):
    """Acceptance classifier for the armed run: stopped BEFORE the ST, no VDC side effect."""
    _common(row, mode, f"{mode}-preblock")
    if row.get("armed") is not True:
        raise ValueError(f"{mode}: run not armed")
    if row["execbp_hit"] is not True:
        raise ValueError(f"{mode}: EXECUTE breakpoint flag not raised before the ST")
    if row.get("execbp_addr") != ST_PC[mode]:
        raise ValueError(f"{mode}: execbp_addr {row.get('execbp_addr')!r}, expected {ST_PC[mode]:#x}")
    if row["stop_pc"] != ST_PC[mode]:
        raise ValueError(f"{mode}: stop PC {row['stop_pc']:#x}, expected the ST at {ST_PC[mode]:#x}")
    if row["detected"] is not False:
        raise ValueError(f"{mode}: HUC6270 register breakpoint fired - the ST executed")
    if row["vdc_ar_before"] != REG_BEFORE or row["vdc_ar_after"] != REG_BEFORE:
        raise ValueError(f"{mode}: VDC AR changed {row['vdc_ar_before']:#x}->{row['vdc_ar_after']:#x} before stop")
    if row["vdc_r_before"] != REG_BEFORE or row["vdc_r_after"] != REG_BEFORE:
        raise ValueError(f"{mode}: VDC R[0] changed {row['vdc_r_before']:#x}->{row['vdc_r_after']:#x} before stop")
    if row.get("ram_diff"):
        raise ValueError(f"{mode}: guest RAM changed before stop: {row['ram_diff']}")
    return PREBLOCK_VERDICT


def check_disarmed(row, mode):
    """Mutation run: no breakpoint armed, so the ST must execute and the side effect must land."""
    _common(row, mode, f"{mode}-preblock-disarmed")
    if row.get("armed") is not False:
        raise ValueError(f"{mode}: disarmed run reports armed")
    if row["execbp_hit"] is not False:
        raise ValueError(f"{mode}: EXECUTE breakpoint flag raised with no breakpoint armed")
    if row["stop_pc"] != STOP_AFTER_ST[mode]:
        raise ValueError(f"{mode}: disarmed stop PC {row['stop_pc']:#x}, expected {STOP_AFTER_ST[mode]:#x}")
    if not _vdc_changed(row, mode):
        raise ValueError(f"{mode}: disarmed run shows no VDC side effect")
    return LANDED_VERDICT


def mutation_test_armed(row, mode):
    """Every falsified armed observation must be rejected by check_armed."""
    base = copy.deepcopy(row)
    mutations = [
        ("execbp_hit_false", {"execbp_hit": False}, []),
        ("execbp_addr_wrong", {"execbp_addr": 0x3E50}, []),
        ("stop_past_st", {"stop_pc": STOP_AFTER_ST[mode]}, []),
        ("vdc_side_effect_landed", {"vdc_ar_after": ST0_AR_AFTER} if mode == "st0" else {"vdc_r_after": 0x5A00 if mode == "st1" else 0xA500}, []),
        ("detected_true", {"detected": True}, []),
        ("ram_store_landed", {"ram_diff": [{"address": 0x3E50, "before": 0, "after": 0x5A}]}, []),
        ("mpr0_ff", {"before": {"mpr": [0xFF] + [0] * 7}}, []),
        ("status_rejected", {"status": "rejected"}, []),
        ("disarmed_run_as_armed", None, []),
    ]
    for name, overwrite, remove in mutations:
        broken = copy.deepcopy(base)
        if overwrite is None:
            broken = copy.deepcopy(DISARMED_ROWS[mode])  # mechanism mutation: breakpoint removed
        else:
            for key in remove:
                broken.pop(key, None)
            broken.update(overwrite)
        try:
            check_armed(broken, mode)
        except ValueError:
            continue  # falsified observation rejected
        raise ValueError(f"mutation test FAILED: {mode} {name} accepted by the armed classifier")


DISARMED_ROWS = {}


def main():
    with tempfile.TemporaryDirectory(prefix="pce-st-preblock-") as tmp:
        directory = Path(tmp)
        bios, cue = synthetic_media(directory)
        probe = compile_probe(directory, source_name="geargrafx_reference_jsr.cpp",
                              target_name="st-preblock-probe")
        for mode in ST_MODES:
            armed = run_mode(probe, bios, cue, f"{mode}-preblock", 0)
            verdict = check_armed(armed, mode)
            disarmed = run_mode(probe, bios, cue, f"{mode}-preblock-disarmed", 0)
            DISARMED_ROWS[mode] = disarmed
            landed = check_disarmed(disarmed, mode)
            mutation_test_armed(armed, mode)
            print(f"ARMED {mode}: execbp_hit={armed['execbp_hit']} execbp_addr={armed['execbp_addr']:#x} "
                  f"stop_pc={armed['stop_pc']:#x} vdc_ar={armed['vdc_ar_before']:#x}->{armed['vdc_ar_after']:#x} "
                  f"vdc_r={armed['vdc_r_before']:#x}->{armed['vdc_r_after']:#x} detected={armed['detected']} "
                  f"-> {verdict}")
            print(f"DISARMED {mode}: execbp_hit={disarmed['execbp_hit']} stop_pc={disarmed['stop_pc']:#x} "
                  f"vdc_ar={disarmed['vdc_ar_before']:#x}->{disarmed['vdc_ar_after']:#x} "
                  f"vdc_r={disarmed['vdc_r_before']:#x}->{disarmed['vdc_r_after']:#x} -> {landed}")
        check_pos(run_mode(probe, bios, cue, "pos-write", POS_CONTROL), POS_CONTROL)
    print("PASS issue-365 ST0/ST1/ST2 pre-execution breakpoint measurement:")
    print("  positive control: allowlisted page-1 RAM store ${:04X} landed unflagged".format(POS_CONTROL))
    print("  mutation: disarmed breakpoint -> side effect lands and armed classifier rejects it")
    print("LIMIT: prevention for the statically known ST PC only; not a general VDC write interceptor.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        print(f"FAIL issue-365 ST pre-execution: {error}", file=sys.stderr)
        raise SystemExit(1) from error
