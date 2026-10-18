#!/usr/bin/env python3
"""Issue #179: can a guest `STA $1804` be rejected BEFORE its CD-ROM side effect?

Runs the pinned real Geargrafx instruction-debugger core (local GPL-3.0 link,
revision b49ae82a012eade566d72e9d61c9d95a5caa4da3; no code copied into this
repo) with a fully synthetic BIOS/CD. The synthetic guest executes
``LDA #$02 / STA $1804 / BRA *`` from page-1 work RAM with MPR0 == $FF, so CPU
address $1804 reaches the CD-ROM reset register while the CPU-address write
breakpoint covering page 0 (which includes $1804) and ``stop_on_breakpoint`` are
armed.

MEASURED (never assumed) and asserted below:
  * detection    - the write breakpoint fires on the STA itself
                   (``detected`` is True and ``detected_pc`` == $3E02);
  * stop point   - ``stop_pc`` / ``instructions_executed`` of the stopped run;
  * device state - the $1804 readback (CdRom ``m_reset``; docs/cd-reset-contract.md
                   says $02 asserted / $00 released) before vs after the stop.

Verdict rule (falsifiable):
  * device state unchanged AND the run stopped at/before the STA -> a
    pre-side-effect trap exists (classified ``pretrap_possible``);
  * device state changed although the breakpoint fired -> the register write
    completes INSIDE the storing instruction, before any stop can happen, so
    pre-side-effect rejection is IMPOSSIBLE on this core -> BLOCKED.

This run classifies the SECOND case: ``Memory::Write`` only raises the
breakpoint flag and keeps executing (the Hardware Page branch ignores the
``map_write`` deny), and the core stops at the next instruction boundary.
Detection alone is never treated as acceptance: nothing here releases a gate
(slot $01 stays BLOCKED; MODEL_ELIGIBLE is not execution permission).

Positive control: the existing allowlisted page-1 RAM store (pos-write,
$2000-$3FFF) must land unflagged.

ST0/ST1/ST2 (WriteFromCPU bypass), the IRQ2 enable path ($1802), dynamic MPR
changes and physical bank aliases are outside this single-path measurement and
remain unguaranteed.
"""
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from geargrafx_api import compile_probe  # noqa: E402
from geargrafx_reference_guard import check_pos, run_mode, synthetic_media  # noqa: E402

MMIO_ADDR = 0x1804
STA_PC = 0x3E02            # LDA #$02 at $3E00-$3E01; STA $1804 at $3E02-$3E04
STOP_AFTER_STA_PC = 0x3E05  # BRA * -- reached only once the STA has completed
RST_DEASSERTED = 0x00      # $1804 readback before (after) the pulse
RST_ASSERTED = 0x02        # $1804 readback while SCSI RST is asserted
IRQ2_BIT = 0x01            # interrupt request register ($1403) bit 0
POS_CONTROL = 0x3E50       # allowlisted page-1 RAM positive-control store


def check_mmio1804(row):
    """Classify the measured $1804 run; returns the verdict string."""
    if row.get("exit", 0) != 0:
        raise ValueError(f"mmio1804 probe refused to run (exit {row.get('exit')}): {row.get('stderr','')[-300:]}")
    if row.get("status") != "ok":
        raise ValueError(f"mmio1804 observation invalid: {row.get('reason')}")
    if row.get("mmio1804_addr") != MMIO_ADDR:
        raise ValueError(f"mmio1804 measured wrong address: {row.get('mmio1804_addr')}")
    # Detection must work first; without it nothing else is meaningful.
    if row.get("detected") is not True:
        raise ValueError("mmio1804: $1804 store was NOT detected by the CPU-address write breakpoint")
    if row.get("detected_pc") != STA_PC:
        raise ValueError(f"mmio1804: detection attributed to PC {row.get('detected_pc'):#x}, "
                         f"expected the STA at {STA_PC:#x}")

    stop_pc = row.get("stop_pc")
    executed = row.get("instructions_executed")
    rst_before = row.get("mmio_rst_before")
    rst_after = row.get("mmio_rst_after")
    irq2_before = row.get("mmio_irq2_before")
    irq2_after = row.get("mmio_irq2_after")
    for name, value in (("stop_pc", stop_pc), ("instructions_executed", executed),
                        ("mmio_rst_before", rst_before), ("mmio_rst_after", rst_after),
                        ("mmio_irq2_before", irq2_before), ("mmio_irq2_after", irq2_after)):
        if type(value) is not int:
            raise ValueError(f"mmio1804 observation missing/non-integer {name}: {value!r}")
    if rst_before != RST_DEASSERTED:
        raise ValueError(f"mmio1804: pre-run $1804 readback {rst_before:#x}, expected {RST_DEASSERTED:#x}")
    if irq2_before & IRQ2_BIT:
        raise ValueError(f"mmio1804: IRQ2 line already asserted before the run: {irq2_before:#x}")

    if rst_after == RST_DEASSERTED:
        # Device state did NOT change: pre-side-effect rejection would be proven.
        if stop_pc > STA_PC:
            raise ValueError(f"mmio1804: store unprevented but $1804 unchanged; stop_pc {stop_pc:#x}")
        return "pretrap_possible"
    if rst_after != RST_ASSERTED:
        raise ValueError(f"mmio1804: unexpected $1804 readback {rst_after:#x}, "
                         f"expected {RST_DEASSERTED:#x} or {RST_ASSERTED:#x}")

    # BLOCKED evidence: the device side effect landed although detection fired.
    # The core stopped only at the instruction boundary, AFTER the STA.
    if stop_pc != STOP_AFTER_STA_PC:
        raise ValueError(f"mmio1804: expected instruction-boundary stop at {STOP_AFTER_STA_PC:#x}, "
                         f"got {stop_pc:#x}")
    if executed != 2:
        raise ValueError(f"mmio1804: expected 2 stepped instructions (LDA+STA), got {executed}")
    # IRQ2 note (measured fact, not a hazard claim): with the CD IRQ enable
    # register ($1802) untouched at reset, $1804 alone does not assert the IRQ2
    # line, so irq2_after & IRQ2_BIT stays 0. The $1802-enable path is NOT
    # exercised here and remains unguaranteed; assert only that nothing
    # unexpected happened to the line.
    if irq2_after & IRQ2_BIT and not irq2_before & IRQ2_BIT:
        raise ValueError("mmio1804: IRQ2 asserted unexpectedly in the single-write scenario; "
                         "recorded facts need review")
    return "blocked_side_effect_before_stop"


def main():
    with tempfile.TemporaryDirectory(prefix="pce-mmio1804-") as tmp:
        directory = Path(tmp)
        bios, cue = synthetic_media(directory)
        probe = compile_probe(directory, source_name="geargrafx_reference_jsr.cpp",
                              target_name="mmio1804-trap-probe")
        # Negative case: prohibited $1804 store, stop_on_breakpoint armed.
        verdict = check_mmio1804(run_mode(probe, bios, cue, "mmio1804", MMIO_ADDR))
        # Positive control: allowlisted page-1 RAM store must land, unflagged.
        check_pos(run_mode(probe, bios, cue, "pos-write", POS_CONTROL), POS_CONTROL)
    print(f"PASS issue-179 $1804 measurement: detected on the STA and stopped at the "
          f"instruction boundary; verdict={verdict}; allowlisted ${POS_CONTROL:04X} control "
          "store landed; no gate change (slot $01 BLOCKED)")
    if verdict == "blocked_side_effect_before_stop":
        print("BLOCKED: pre-side-effect rejection is impossible on pinned core b49ae82a "
              "(Memory::Write completes the device write; the stop happens only at the "
              "instruction boundary). Detection alone is not acceptance.")
    else:
        print("NOTE: this core stopped before the $1804 side effect; the recorded "
              "BLOCKED evidence must be re-evaluated. Still no gate change here.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        print(f"FAIL issue-179 $1804 pre-trap: {error}", file=sys.stderr)
        raise SystemExit(1) from error
