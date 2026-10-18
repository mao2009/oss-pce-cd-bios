#!/usr/bin/env python3
"""Issue #173: can the $1804 side effect be blocked BEFORE the store runs?

Pinned real core (local GPL-3.0 link, b49ae82a012eade566d72e9d61c9d95a5caa4da3,
no core code copied into this repo), same synthetic BIOS/CD and guest
``LDA #$02 / STA $1804 / BRA *`` (MPR0 == $FF) as PR #194.

RED (PR #194 measurement, re-measured here): mode ``mmio1804`` arms only the
CPU-address WRITE breakpoint - the store LANDS (``mmio_rst`` $00 -> $02, the
SCSI RST side effect) and the core stops only at the next boundary ($3E05).

GREEN (new): mode ``mmio1804-preblock`` adds an EXECUTE breakpoint on the STA at
$3E02. ``HuC6280::CheckBreakpoints()`` runs at the END of ``RunInstruction()``
with the PC of the instruction not yet fetched, so the flag rises BEFORE the STA
executes and the step monitor refuses to re-enter. Asserted by exact values:
``execbp_hit`` True, ``execbp_addr`` == $3E02, ``stop_pc`` == $3E02,
``instructions_executed`` == 1 (LDA only), ``detected`` False (the write
breakpoint never fired -> ``Memory::Write`` never saw the store),
``mmio_rst_before`` == ``mmio_rst_after`` == $00, IRQ2 register unchanged,
``ram_diff`` empty. The verdict is decided from the DEVICE readback and the
instruction count, never from ``execbp_hit`` alone.

Positive control: the allowlisted page-1 RAM store (pos-write) must land
unflagged, so the guard is not a blanket drop.

Honest limits (not hidden): the execute breakpoint must sit on the PC of the
prohibitable store - prevention for KNOWN guest code, not a general MMIO write
interceptor. ST0/ST1/ST2 (``WriteFromCPU`` bypass), the $1802 IRQ-enable path,
MPR changes and bank aliases stay unguaranteed (separate child issues of #173).
Nothing here releases a gate: slot $01 stays BLOCKED and MODEL_ELIGIBLE is not
execution permission.
"""
import copy
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from geargrafx_api import compile_probe  # noqa: E402
from geargrafx_reference_guard import check_pos, run_mode, synthetic_media  # noqa: E402
from geargrafx_mmio1804_trap import (  # noqa: E402
    MMIO_ADDR,
    RST_ASSERTED,
    RST_DEASSERTED,
    STA_PC,
    check_mmio1804,
)

STOP_AT_STA_PC = 0x3E02   # PC of the STA that must never execute
LDA_ONLY = 1              # LDA #$02 only; the STA must not be in the count
RED_VERDICT = "blocked_side_effect_before_stop"
GREEN_VERDICT = "preblocked_before_side_effect"
POS_CONTROL = 0x3E50      # allowlisted page-1 RAM positive-control store


def check_preblock(row):
    """Classify the ``mmio1804-preblock`` run from device + CPU state."""
    if row.get("exit", 0) != 0:
        raise ValueError(f"preblock probe refused to run (exit {row.get('exit')}): "
                         f"{row.get('stderr','')[-300:]}")
    if row.get("status") != "ok":
        raise ValueError(f"preblock observation invalid: {row.get('reason')}")
    if row.get("mode") != "mmio1804-preblock":
        raise ValueError(f"preblock measured mode {row.get('mode')!r}")
    if row.get("mmio1804_addr") != MMIO_ADDR:
        raise ValueError(f"preblock measured wrong address: {row.get('mmio1804_addr')}")
    if row.get("execbp_addr") != STA_PC:
        raise ValueError(f"preblock armed the execute breakpoint at "
                         f"{row.get('execbp_addr'):#x}, expected the STA at {STA_PC:#x}")
    # Core-side signal: the execute breakpoint must have raised before the STA.
    if row.get("execbp_hit") is not True:
        raise ValueError("preblock: execute breakpoint was not raised before the STA")
    # The write breakpoint never fired => Memory::Write never saw the store.
    if row.get("detected") is not False:
        raise ValueError(f"preblock: write breakpoint fired (detected_pc "
                         f"{row.get('detected_pc'):#x}) => the STA executed")
    if row.get("mpr_changed") is not False:
        raise ValueError("preblock: MPR changed during the run")
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
            raise ValueError(f"preblock observation missing/non-integer {name}: {value!r}")
    if stop_pc != STOP_AT_STA_PC:
        raise ValueError(f"preblock stop PC {stop_pc:#x}, expected {STOP_AT_STA_PC:#x} "
                         "on the STA that must not execute")
    if executed != LDA_ONLY:
        raise ValueError(f"preblock executed {executed} instructions, expected "
                         f"{LDA_ONLY} (LDA only, STA not executed)")
    # Device state, never the detection flag, decides the verdict.
    if rst_before != RST_DEASSERTED:
        raise ValueError(f"preblock: pre-run $1804 readback {rst_before:#x}, "
                         f"expected {RST_DEASSERTED:#x}")
    if rst_after != RST_DEASSERTED:
        raise ValueError(f"preblock: $1804 side effect landed before the stop "
                         f"({rst_before:#x} -> {rst_after:#x})")
    if irq2_before != irq2_after:
        raise ValueError(f"preblock: IRQ2 register changed "
                         f"({irq2_before:#x} -> {irq2_after:#x})")
    if row.get("ram_diff") != []:
        raise ValueError(f"preblock: unexpected RAM diff {row.get('ram_diff')}")
    return GREEN_VERDICT


def mutation_test_preblock(row):
    """Every falsified preblock observation must be rejected by the classifier."""
    mutations = [
        ("mmio_rst_after=0x02", {"mmio_rst_after": RST_ASSERTED}, [], "side effect landed"),
        ("mmio_rst_after=missing", {}, ["mmio_rst_after"], "device state not observed"),
        ("stop_pc=0x3E05", {"stop_pc": 0x3E05}, [], "stopped after the STA"),
        ("stop_pc=missing", {}, ["stop_pc"], "stop PC not observed"),
        ("instructions_executed=2", {"instructions_executed": 2}, [], "the STA was stepped"),
        ("execbp_hit=false", {"execbp_hit": False}, [], "flag not raised before the STA"),
        ("detected=true", {"detected": True}, [], "the store reached Memory::Write"),
        ("status=rejected", {"status": "rejected"}, [], "probe failed closed"),
        ("ram_diff_nonempty", {"ram_diff": [{"address": 0x2000, "before": 0,
                                             "after": 0x5A}]}, [],
         "the store reached RAM"),
    ]
    for name, overwrite, remove, reason in mutations:
        broken = copy.deepcopy(row)
        for key in remove:
            broken.pop(key, None)
        broken.update(overwrite)
        try:
            check_preblock(broken)
        except ValueError:
            continue  # expected: falsified observation rejected
        raise ValueError(f"preblock mutation FAILED: {name} was accepted but must "
                         f"be rejected ({reason})")


def main():
    with tempfile.TemporaryDirectory(prefix="pce-mmio1804-preblock-") as tmp:
        directory = Path(tmp)
        bios, cue = synthetic_media(directory)
        probe = compile_probe(directory, source_name="geargrafx_reference_jsr.cpp",
                              target_name="mmio1804-preblock-probe")
        # RED: write breakpoint only -> the device side effect lands before the stop.
        red = check_mmio1804(run_mode(probe, bios, cue, "mmio1804", MMIO_ADDR))
        if red != RED_VERDICT:
            raise ValueError(f"RED case changed: expected {RED_VERDICT!r}, got {red!r}")
        # GREEN: execute breakpoint on the STA -> stopped before the side effect.
        row = run_mode(probe, bios, cue, "mmio1804-preblock", MMIO_ADDR)
        verdict = check_preblock(row)
        mutation_test_preblock(row)
        # Positive control: the allowlisted page-1 RAM store must land unflagged.
        check_pos(run_mode(probe, bios, cue, "pos-write", POS_CONTROL), POS_CONTROL)
    print(f"PASS issue-173 $1804 preblock: RED={red} (write breakpoint only, side "
          f"effect landed), GREEN={verdict} (stop_pc={STOP_AT_STA_PC:#x}, "
          f"instructions_executed={LDA_ONLY}, $1804 stayed {RST_DEASSERTED:#04x}); "
          f"allowlisted ${POS_CONTROL:04X} control store landed; no gate change "
          "(slot $01 BLOCKED)")
    print("LIMIT: the execute breakpoint needs the store's PC (known guest code): "
          "prevention for known code, not a general MMIO write interceptor; "
          "ST0/ST1/ST2, $1802 IRQ enable, MPR and alias paths stay unguaranteed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        print(f"FAIL issue-173 $1804 preblock: {error}", file=sys.stderr)
        raise SystemExit(1) from error
