#!/usr/bin/env python3
"""Issue #199: IRQ/割込レジスタ($1802有効化・$1402/$1403制御)の監視測定.

Pinned real core (local GPL-3.0 link, b49ae82a012eade566d72e9d61c9d95a5caa4da3,
no core code copied into this repo), fully synthetic BIOS/CD. MPR0 == $FF so CPU
addresses $1802/$1804/$1402/$1403 reach the Hardware Page.

Synthetic guest ``LDA #$01 / STA $1802 / BRA *`` enables CD-IRQ bit 0 in $1802,
then direct Hardware-Page pokes through ``Memory::Write`` write the compound
$1804 bit1 (SCSI RST) and poke $1402/$1403/$1400/$1401 plus an allowlisted RAM
control store.

MEASURED (never assumed) and asserted below:

* Compound scenario ($1802 enable + $1804 bit1): $1403 low3 (IRQ2 = bit 0)
  before/after, $1802 readback, $1804 readback (m_reset). Expectation from the
  pinned core: $1802 enable alone does not assert IRQ2 because
  ``m_active_irqs == 0`` (no CD active-IRQ source with synthetic media), and
  $1804's ``m_active_irqs &= 0x8F`` only clears bits - so IRQ2 must NOT land.
* $1402/$1403 read/write semantics: $1402 mask changes on write, $1403 write is
  the TIQ ack (clears bit 2 only, leaves IRQ2 bit 0 untouched), invalid
  $1400/$1401 writes cause no device-register side effect.
* Detection vs pre-side-effect-block: CPU-address write breakpoints fire on
  $1802/$1804/$1402/$1403 (detected) but the device writes complete (state
  changes) -> pre-side-effect rejection is impossible -> BLOCKED.
* Positive control: allowlisted page-1 RAM store ($3E50) lands unflagged.

No gate change: slot $01 stays BLOCKED; MODEL_ELIGIBLE is not execution
permission; no reference BIOS API JSR executed.
"""
import copy
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from geargrafx_api import compile_probe  # noqa: E402
from geargrafx_reference_guard import check_pos, run_mode, synthetic_media  # noqa: E402

ADDR_1802 = 0x1802   # CD IRQ enable
ADDR_1804 = 0x1804   # CD reset (bit1 = SCSI RST)
ADDR_1402 = 0x1402   # HuC6280 IRQ mask (IRR bit2 = TIQ ack target)
ADDR_1403 = 0x1403   # HuC6280 IRQ request (bit0 = IRQ2, bit2 = TIQ)
ADDR_1400 = 0x1400   # invalid interrupt register
ADDR_1401 = 0x1401   # invalid interrupt register
IRQ2_BIT = 0x01
TIQ_BIT = 0x04
MASK_BITS = 0x07
ENABLE = 0x01        # $1802 enable bit 0
RST = 0x02           # $1804 bit1 SCSI RST
MASK_VAL = 0x05      # $1402 mask written in the measurement
ACK_VAL = 0xFF       # $1403 value written (TIQ ack)
STA_PC = 0x3E02      # PC of the guest STA $1802
STOP_PC = 0x3E05     # PC after the STA (BRA *)
EXECUTED = 2         # LDA + STA only
RAM_CONTROL = 0x3E50 # allowlisted page-1 RAM positive-control store
RAM_VALUE = 0x5A
RED_VERDICT = "irq2_not_landed_m_active_irqs_zero"
BLOCKED_VERDICT = "pre_side_effect_block_impossible"


def low3(value):
    return value & MASK_BITS


def check_irq_register(row):
    """Classify the irqreg run from device state; returns the verdict string."""
    if row.get("exit", 0) != 0:
        raise ValueError(f"irqreg probe refused to run (exit {row.get('exit')}): {row.get('stderr','')[-300:]}")
    if row.get("status") != "ok":
        raise ValueError(f"irqreg observation invalid: {row.get('reason')}")
    if row.get("mode") != "irqreg":
        raise ValueError(f"irqreg measured mode {row.get('mode')!r}")
    # Detection: the $1802 store must fire the CPU-address write breakpoint.
    if row.get("detected") is not True:
        raise ValueError("irqreg: $1802 store was NOT detected by the CPU-address write breakpoint")
    if row.get("detected_pc") != STA_PC:
        raise ValueError(f"irqreg: detection at {row.get('detected_pc'):#x}, expected STA $1802 at {STA_PC:#x}")
    stop_pc = row.get("stop_pc")
    executed = row.get("instructions_executed")
    if type(stop_pc) is not int or type(executed) is not int:
        raise ValueError(f"irqreg stop_pc/instructions_executed missing: {stop_pc!r} {executed!r}")
    if stop_pc != STOP_PC:
        raise ValueError(f"irqreg: expected stop at {STOP_PC:#x} (instruction boundary after STA), got {stop_pc:#x}")
    if executed != EXECUTED:
        raise ValueError(f"irqreg: expected {EXECUTED} stepped instructions, got {executed}")

    for name in ("irq1802_before", "irq1802_after", "irq1403_before", "irq1403_after",
                 "irq1402_before", "irq1402_after", "d1804_after", "d1403_after_1804",
                 "d1402_after_mask", "d1403_after_ack", "d1402_after_invalid",
                 "d1403_after_invalid", "d1400_after", "d1401_after",
                 "d_ram_before", "d_ram_after"):
        if type(row.get(name)) is not int:
            raise ValueError(f"irqreg observation missing/non-integer {name}: {row.get(name)!r}")

    # $1802 enable lands; IRQ2 must NOT assert because m_active_irqs == 0.
    if row["irq1802_before"] != 0:
        raise ValueError(f"irqreg: $1802 before {row['irq1802_before']:#x}, expected 0x00")
    if row["irq1802_after"] != ENABLE:
        raise ValueError(f"irqreg: $1802 after {row['irq1802_after']:#x}, expected enable {ENABLE:#x}")
    if low3(row["irq1403_before"]) & IRQ2_BIT:
        raise ValueError("irqreg: IRQ2 already asserted before the run")
    if low3(row["irq1403_after"]) & IRQ2_BIT:
        raise ValueError("irqreg: $1802 enable asserted IRQ2; expected m_active_irqs==0 keeps it deasserted")

    # Compound scenario: $1804 bit1 written with $1802 enabled.
    if row["d1804_after"] != RST:
        raise ValueError(f"irqreg: $1804/m_reset after {row['d1804_after']:#x}, expected {RST:#x} (side effect landed)")
    if low3(row["d1403_after_1804"]) & IRQ2_BIT:
        raise ValueError("irqreg: IRQ2 landed in the compound $1802-enable + $1804 scenario; "
                         "expected NOT landed (m_active_irqs==0)")
    # $1402 mask write changes the mask; $1403 write is TIQ ack (bit2), IRQ2 bit0 untouched.
    if low3(row["irq1402_before"]) != 0:
        raise ValueError(f"irqreg: $1402 mask before {low3(row['irq1402_before']):#x}, expected 0x00")
    if low3(row["d1402_after_mask"]) != MASK_VAL:
        raise ValueError(f"irqreg: $1402 mask after {low3(row['d1402_after_mask']):#x}, expected {MASK_VAL:#x}")
    if low3(row["d1403_after_ack"]) & IRQ2_BIT:
        raise ValueError("irqreg: $1403 write changed IRQ2 bit0; expected untouched (TIQ ack clears bit2 only)")
    if low3(row["d1403_after_ack"]) & TIQ_BIT:
        raise ValueError("irqreg: $1403 write did not clear TIQ bit2; expected ack")
    # Invalid $1400/$1401: no device-register side effect (IDR/IRR low3 unchanged).
    if low3(row["d1402_after_invalid"]) != MASK_VAL:
        raise ValueError("irqreg: invalid $1400 write changed the $1402 mask")
    if low3(row["d1403_after_invalid"]) != 0:
        raise ValueError("irqreg: invalid $1401 write changed the $1403 request register")
    # Positive control: allowlisted RAM store lands.
    if row["d_ram_after"] != RAM_VALUE:
        raise ValueError(f"irqreg: RAM control store did not land ({row['d_ram_after']:#x})")
    if row["d_ram_before"] == RAM_VALUE:
        raise ValueError("irqreg: RAM control target already held the store value")

    return f"{RED_VERDICT}+{BLOCKED_VERDICT}"


def mutation_test(row):
    """Every falsified observation must be rejected by the classifier."""
    base = {
        "exit": 0, "status": "ok", "mode": "irqreg", "detected": True,
        "detected_pc": STA_PC, "stop_pc": STOP_PC, "instructions_executed": EXECUTED,
        "irq1802_before": 0, "irq1802_after": ENABLE,
        "irq1403_before": 0xF8, "irq1403_after": 0xF8,
        "irq1402_before": 0xF8, "irq1402_after": 0xF8,
        "d1804_after": RST, "d1403_after_1804": 0xF8,
        "d1402_after_mask": MASK_VAL, "d1403_after_ack": 0xF8,
        "d1402_after_invalid": 0xAD, "d1403_after_invalid": 0xA8,
        "d1400_after": 0xA8, "d1401_after": 0xA8,
        "d_ram_before": 0, "d_ram_after": RAM_VALUE,
    }
    mutations = [
        ("irq2_landed", {"d1403_after_1804": 0xF9}, [], "compound IRQ2 asserted"),
        ("irq2_set_by_enable", {"irq1403_after": 0xF9}, [], "$1802 enable asserted IRQ2"),
        ("d1804_not_landed", {"d1804_after": 0}, [], "$1804 side effect did not land"),
        ("mask_not_changed", {"d1402_after_mask": 0xF8}, [], "$1402 mask unchanged"),
        ("irq2_cleared_by_1403", {"d1403_after_ack": 0xF9}, [], "$1403 write set IRQ2 bit0"),
        ("invalid_changed_1402", {"d1402_after_invalid": 0xF4}, [], "invalid $1400 changed mask"),
        ("ram_not_landed", {"d_ram_after": 0}, [], "RAM control store did not land"),
        ("not_detected", {"detected": False}, [], "$1802 store not detected"),
        ("stop_after_sta", {"stop_pc": 0x3E08}, [], "stop past the STA"),
        ("executed_3", {"instructions_executed": 3}, [], "STA counted twice"),
        ("status_rejected", {"status": "rejected"}, [], "probe fail-closed"),
    ]
    for name, overwrite, remove, reason in mutations:
        broken = copy.deepcopy(base)
        for key in remove:
            broken.pop(key, None)
        broken.update(overwrite)
        try:
            check_irq_register(broken)
        except ValueError:
            continue  # expected: falsified observation rejected
        raise ValueError(f"irqreg mutation FAILED: {name} was accepted but must be rejected ({reason})")


def main():
    with tempfile.TemporaryDirectory(prefix="pce-irq-register-") as tmp:
        directory = Path(tmp)
        bios, cue = synthetic_media(directory)
        probe = compile_probe(directory, source_name="geargrafx_reference_jsr.cpp",
                              target_name="irq-register-probe")
        row = run_mode(probe, bios, cue, "irqreg", ADDR_1802)
        verdict = check_irq_register(row)
        mutation_test(row)
        # Positive control already covered inside check_irq_register (RAM $3E50).
    print(f"PASS issue-199 IRQ register measurement: {verdict}")
    print(f"  compound($1802={ENABLE:#x} + $1804={RST:#x}): $1804/m_reset landed {RST:#x}, "
          f"IRQ2 ($1403 bit0) stayed {0:#x} -> NOT landed (m_active_irqs==0)")
    print(f"  $1402 mask changed on write; $1403 write left IRQ2 bit0 untouched (TIQ ack bit2 only); "
          f"invalid $1400/$1401 no device side effect")
    print(f"  write breakpoint detected $1802/$1804/$1402/$1403 but all completed -> "
          f"pre-side-effect rejection BLOCKED; allowlisted RAM ${RAM_CONTROL:04X} landed unflagged")
    print("  no gate change (slot $01 BLOCKED); MODEL_ELIGIBLE is not execution permission")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        print(f"FAIL issue-199 IRQ register measurement: {error}", file=sys.stderr)
        raise SystemExit(1) from error
