#!/usr/bin/env python3
"""Issue #198: ST0/ST1/ST2 ($03/$13/$23) ``WriteFromCPU`` bypass - real-core measurement.

Pinned real Geargrafx core (local GPL-3.0 link, b49ae82a012eade566d72e9d61c9d95a5caa4da3;
no core code copied into this repo), fully synthetic BIOS/CD. The synthetic guest
runs one ST opcode path per mode from page-1 work RAM:

* ``st0``: ``ST0 #$07`` at $3E00, then ``BRA *``  (VDC address register write)
* ``st1``: ``ST0 #$00`` / ``ST1 #$5A`` / ``BRA *`` (VDC data register LSB write)
* ``st2``: ``ST0 #$00`` / ``ST2 #$A5`` / ``BRA *`` (VDC data register MSB write)

MPR0 is fixed to $00 (**!= $FF**, the Hardware Page is unreachable) for every path
- this is the mandatory MPR0 != $FF negative case: reachability denial that
mitigates STA $1804 (PR #194/#200) does NOT mitigate ST0/ST1/ST2.

MEASURED (never assumed), read back through the core's public API
(``GeargrafxCore::GetHuC6270_1()->GetState()``: AR address register, R data
registers) after the core stopped:

* side effect land - the VDC register state actually changes (AR / R[0]);
* detection - (a) CPU-address write breakpoint / ``map_write`` never fire (ST
  bypasses ``Memory::Write``); (b) the ``HUC6270_REGISTER`` breakpoint exists
  for ST1/ST2 data writes but only sets a flag AFTER the register write has
  completed inside the instruction - ST0's address-register case has no
  ``CheckMemoryBreakpoints`` call site at all, so even an armed breakpoint never
  fires for it; (c) an execute breakpoint needs the statically known ST PC
  (#197 constraint) and is not a general interceptor;
* pre-side-effect prevention - impossible on this core: the stop happens at the
  instruction boundary AFTER the ST executed (the instruction count includes the
  ST), so the side effect lands before any stop. For ST0 there is not even a
  detection flag.

Result per path: **BLOCKED / 実装不可** - no pre-side-effect interception point
exists on the pinned core; the only working mitigation is keeping the path
unreachable (which for ST means the guest must never execute ST0/ST1/ST2,
enforced outside this probe). Nothing here releases a gate: slot $01 stays
BLOCKED, MODEL_ELIGIBLE is not execution permission.

Positive control: the allowlisted page-1 RAM store (pos-write, $2000-$3FFF)
lands unflagged, so the guard is not a blanket drop.
"""
import copy
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from geargrafx_api import compile_probe  # noqa: E402
from geargrafx_reference_guard import check_pos, run_mode, synthetic_media  # noqa: E402

# Synthetic guest layout (all CPU addresses, page 1 = WRAM, MPR1 = $F8):
#   st0: $3E00 ST0 #$07, $3E02 BRA *
#   st1: $3E00 ST0 #$00, $3E02 ST1 #$5A, $3E04 BRA *
#   st2: $3E00 ST0 #$00, $3E02 ST2 #$A5, $3E04 BRA *
ST_OP_PC = {"st0": 0x3E00, "st1": 0x3E02, "st2": 0x3E02}
STOP_PC = {"st0": 0x3E02, "st1": 0x3E04, "st2": 0x3E04}
ST_INSTRUCTIONS = {"st0": 1, "st1": 2, "st2": 2}  # the ST itself is included
VDC_REG = 0  # VDC register selected by the leading ST0 (0 = MAWR, mask 0xFFFF)
ST0_AR_AFTER = 0x0007  # ST0 #$07 -> address register = 7
ST1_R_AFTER_LO = 0x5A  # ST1 #$5A -> R[0] low byte
ST2_R_AFTER_HI = 0xA5  # ST2 #$A5 -> R[0] high byte
REG_BEFORE = 0x0000  # post-reset VDC registers are zeroed
MPR0_NON_FF = 0x00  # MPR0 fixed to $00: Hardware Page unreachable, ST still lands
POS_CONTROL = 0x3E50  # allowlisted page-1 RAM positive-control store
ST_MODES = ("st0", "st1", "st2")
BLOCKED_VERDICT = "blocked_writefromcpu_bypass"  # pre-side-effect prevention impossible


def expected_vdc_after(mode):
    if mode == "st0":
        return ("ar", ST0_AR_AFTER)
    if mode == "st1":
        return ("r_lo", ST1_R_AFTER_LO)
    return ("r_hi", ST2_R_AFTER_HI)


def check_st(row, mode):
    """Classify one measured ST path run; returns the per-path verdict."""
    if mode not in ST_MODES:
        raise ValueError(f"unexpected ST mode {mode!r}")
    if row.get("exit", 0) != 0:
        raise ValueError(f"{mode} probe refused to run (exit {row.get('exit')}): {row.get('stderr','')[-300:]}")
    if row.get("status") != "ok":
        raise ValueError(f"{mode} observation invalid: {row.get('reason')}")
    if row.get("mode") != mode:
        raise ValueError(f"{mode} measured wrong mode: {row.get('mode')}")
    if row.get("st_mode") != mode:
        raise ValueError(f"{mode} probe st_mode mismatch: {row.get('st_mode')}")
    # The MPR0 != $FF negative case: Hardware Page unreachable for the whole run.
    before_mpr = row.get("before", {}).get("mpr", [])
    after_mpr = row.get("after", {}).get("mpr", [])
    if type(before_mpr) is not list or len(before_mpr) != 8:
        raise ValueError(f"{mode} before MPR snapshot invalid: {before_mpr}")
    if type(after_mpr) is not list or len(after_mpr) != 8:
        raise ValueError(f"{mode} after MPR snapshot invalid: {after_mpr}")
    if before_mpr[0] != MPR0_NON_FF:
        raise ValueError(f"{mode} MPR0 before {before_mpr[0]:#x}, expected {MPR0_NON_FF:#x} (!= $FF)")
    if after_mpr[0] != MPR0_NON_FF:
        raise ValueError(f"{mode} MPR0 after {after_mpr[0]:#x}, expected {MPR0_NON_FF:#x} (!= $FF)")
    stop_pc = row.get("stop_pc")
    executed = row.get("instructions_executed")
    detected = row.get("detected")
    detected_pc = row.get("detected_pc")
    vdc_ar_before = row.get("vdc_ar_before")
    vdc_ar_after = row.get("vdc_ar_after")
    vdc_r_before = row.get("vdc_r_before")
    vdc_r_after = row.get("vdc_r_after")
    for name, value in (("stop_pc", stop_pc), ("instructions_executed", executed),
                        ("detected_pc", detected_pc),
                        ("vdc_ar_before", vdc_ar_before), ("vdc_ar_after", vdc_ar_after),
                        ("vdc_r_before", vdc_r_before), ("vdc_r_after", vdc_r_after)):
        if type(value) is not int:
            raise ValueError(f"{mode} observation missing/non-integer {name}: {value!r}")
    if type(detected) is not bool:
        raise ValueError(f"{mode} observation missing/non-boolean detected: {detected!r}")
    # The ST opcode PC must be where detection (if any) is attributed.
    if detected_pc != (0 if mode == "st0" else ST_OP_PC[mode]):
        raise ValueError(f"{mode} detected_pc {detected_pc:#x}, expected the ST opcode at "
                         f"{ST_OP_PC[mode]:#x} (0 when nothing can fire)")
    if stop_pc != STOP_PC[mode]:
        raise ValueError(f"{mode} stop PC {stop_pc:#x}, expected {STOP_PC[mode]:#x} (the BRA right "
                         f"after the ST: stop only at the instruction boundary)")
    if executed != ST_INSTRUCTIONS[mode]:
        raise ValueError(f"{mode} executed {executed} instructions, expected "
                         f"{ST_INSTRUCTIONS[mode]} (the ST itself has run)")
    # Side effect must have LANDed before the stop: real VDC state change.
    kind, expected = expected_vdc_after(mode)
    if mode == "st0":
        # ST0 writes the address register; no CheckMemoryBreakpoints call site exists,
        # so even an armed HUC6270_REGISTER breakpoint can never flag it.
        if detected is not False:
            raise ValueError(f"{mode}: detection flag raised although ST0's address-register "
                             f"case has no interception site")
        if vdc_ar_before != REG_BEFORE or vdc_ar_after != ST0_AR_AFTER:
            raise ValueError(f"{mode}: address register {vdc_ar_before:#x} -> {vdc_ar_after:#x}, "
                             f"expected {REG_BEFORE:#x} -> {ST0_AR_AFTER:#x} (side effect did not land)")
    else:
        # ST1/ST2: the HUC6270_REGISTER flag rises, but the data-register write
        # completed inside the same instruction - the flag cannot pre-trap it.
        if detected is not True:
            raise ValueError(f"{mode}: HUC6270_REGISTER write breakpoint did not fire on the ST")
        if detected_pc != ST_OP_PC[mode]:
            raise ValueError(f"{mode} detection attributed to {detected_pc:#x}, expected the ST at "
                             f"{ST_OP_PC[mode]:#x}")
        if kind == "r_lo":
            if vdc_r_before != REG_BEFORE or (vdc_r_after & 0xFF) != ST1_R_AFTER_LO:
                raise ValueError(f"{mode}: R[{VDC_REG}] {vdc_r_before:#06x} -> {vdc_r_after:#06x}, "
                                 f"expected low byte {REG_BEFORE:#x} -> {ST1_R_AFTER_LO:#x} (side effect did not land)")
        else:
            if vdc_r_before != REG_BEFORE or (vdc_r_after >> 8) != ST2_R_AFTER_HI:
                raise ValueError(f"{mode}: R[{VDC_REG}] {vdc_r_before:#06x} -> {vdc_r_after:#06x}, "
                                 f"expected high byte {REG_BEFORE:#x} -> {ST2_R_AFTER_HI:#x} (side effect did not land)")
    return BLOCKED_VERDICT


def mutation_test_st(row, mode):
    """Every falsified ST observation must be rejected by the classifier."""
    base = copy.deepcopy(row)
    # (name, overwrite, remove, reason, skip_modes): mutations falsified for all
    # modes except those listed in skip_modes (where the mutated observation is
    # still the expected one for that mode).
    mutations = [
        ("mpr0=0xff", {"before": {"mpr": [0xFF] + [0] * 7}, "after": {"mpr": [0xFF] + [0] * 7}},
         [], "MPR0 == $FF (Hardware Page reachable) - not the measured configuration", ()),
        ("side_effect_not_landed", {}, ["vdc_ar_after", "vdc_r_after"],
         "VDC state unchanged", ()),
        ("stop_pc_after_st", {"stop_pc": STOP_PC[mode] + 2}, [],
         "stop past the ST (wrong instruction boundary)", ()),
        ("instructions_without_st", {"instructions_executed": ST_INSTRUCTIONS[mode] - 1}, [],
         "the ST instruction was not stepped", ()),
        ("detected_false", {"detected": False}, [],
         "st1/st2: HUC6270_REGISTER write breakpoint did not fire", ("st0",)),
        ("detected_true", {"detected": True}, [],
         "st0: detection flag must never rise (no interception site)", ("st1", "st2")),
        ("detected_pc_wrong", {"detected_pc": 0x1804}, [],
         "detection attributed to wrong PC", ()),
        ("status_rejected", {"status": "rejected"}, [],
         "probe failed closed unexpectedly", ()),
    ]
    for name, overwrite, remove, _reason, skip_modes in mutations:
        if mode in skip_modes:
            continue  # mutated observation stays valid for this mode
        broken = copy.deepcopy(base)
        for key in remove:
            broken.pop(key, None)
        broken.update(overwrite)
        try:
            check_st(broken, mode)
        except ValueError:
            continue  # expected: falsified observation rejected
        raise ValueError(f"mutation test FAILED: {mode} {name} was accepted but must be rejected")


def main():
    with tempfile.TemporaryDirectory(prefix="pce-st-bypass-") as tmp:
        directory = Path(tmp)
        bios, cue = synthetic_media(directory)
        probe = compile_probe(directory, source_name="geargrafx_reference_jsr.cpp",
                              target_name="st-bypass-probe")
        verdicts = {}
        for mode in ST_MODES:
            row = run_mode(probe, bios, cue, mode, 0)  # neg_addr unused for ST paths
            verdicts[mode] = check_st(row, mode)
            mutation_test_st(row, mode)
            kind, expected = expected_vdc_after(mode)
            print(f"RED {mode}: MPR0={row['before']['mpr'][0]:#x} (!= $FF) stop_pc={row['stop_pc']:#x} "
                  f"instructions={row['instructions_executed']} detected={row['detected']} "
                  f"detected_pc={row['detected_pc']:#x} vdc_before={row['vdc_r_before'] if mode != 'st0' else row['vdc_ar_before']:#x} "
                  f"vdc_after={row['vdc_r_after'] if mode != 'st0' else row['vdc_ar_after']:#x} "
                  f"-> side effect LANDed before the stop, pre-side-effect prevention IMPOSSIBLE ({verdicts[mode]})")
        # Positive control: allowlisted page-1 RAM store must land unflagged.
        check_pos(run_mode(probe, bios, cue, "pos-write", POS_CONTROL), POS_CONTROL)
    print("PASS issue-198 ST0/ST1/ST2 WriteFromCPU bypass measurement:")
    for mode in ST_MODES:
        print(f"  {mode}: {verdicts[mode]} - VDC side effect lands with MPR0={MPR0_NON_FF:#x} (!= $FF); "
              f"CPU-address write breakpoint/map_write never fire; "
              f"{'no interception site at all' if mode == 'st0' else 'HUC6270_REGISTER flag rises only after the register write completed'}; "
              f"stop at the instruction boundary AFTER the ST -> pre-side-effect prevention IMPOSSIBLE (BLOCKED, 実装不可)")
    print("  pos-write: allowlisted ${:04X} control store landed unflagged".format(POS_CONTROL))
    print("CONCLUSION: no pre-side-effect detection/prevention point exists for ST0/ST1/ST2 on pinned core "
          "b49ae82a; mitigation requires keeping the ST paths unreachable (guest must not execute them), "
          "enforced outside this probe. No gate change (slot $01 BLOCKED, MODEL_ELIGIBLE is not execution "
          "permission), no spec/api_contracts.json change.")
    print("LIMIT: execute-breakpoint pre-trapping (as in #197) needs the statically known ST PC: "
          "prevention for known code only, not a general MMIO/VDC write interceptor.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        print(f"FAIL issue-198 ST bypass: {error}", file=sys.stderr)
        raise SystemExit(1) from error
