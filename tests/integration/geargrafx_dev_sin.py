"""Real Geargrafx run of MA_SIN (slot $45, Issue #79) through the DEV ROM entry $E0CF.

The probe (tests/integration/geargrafx_api_probe.cpp) caps its instruction count at 2048 per phase, so the
256 angles run as 16 batches of 16. Each batch is one run: a guest replaces the reset code of the built DEV
ROM, sets MPR1 to work RAM, sets SP=$FF, then loops over 16 consecutive angles base..base+15 with Y as the
counter:
    CLD; LDY #0; loop: TYA; CLC; ADC #base; SED; SEC; JSR $E0CF; CLD;
         STA $2200,Y (low byte); TXA; STA $2300,Y (high byte); INY; CPY #16; BNE loop
SED and SEC are set before each call on purpose: the routine must restore the caller's flags and must not
depend on D or C. After the loop the guest records SP at $2401 and P at $2402 and halts.

Expectation: the project's own DEV spec (src/overrides/slot_45.s), NOT the reference System Card ABI
(UNVERIFIED): X:A = floor(32767*sin(2*pi*A/256)+0.5), signed 16-bit, X = high byte, A = low byte.

Checked per batch: the halt PC (the loop finished), SP balanced at $FF, MPR1 = $F8 and MPR7 = 0, and all 16
X:A values. Over the 16 batches that is all 256 angles. A table mutant (sin_lo[10] flipped in the ROM copy,
angle 10 and 138) must be rejected by the batch that contains angle 10. P is recorded, not asserted (the
real core's PLP handling is core-specific; see geargrafx_dev_sqrt.py). Work RAM outside $2200-$2402 is not
checked.

Run: python3 tests/integration/geargrafx_dev_sin.py --rom build/debug/bios-dev/dev-only-not-compatible-syscard3.pce
Needs the pinned cc65 and Geargrafx core (make setup, TOOLS_DIR on Linux). Missing tools exit 3 (BLOCKED).
"""
import argparse
import hashlib
import json
import math
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_rom import verify_dev_rom  # noqa: E402
import geargrafx_api as api  # noqa: E402
import geargrafx_dev_bcd as dev  # noqa: E402  (entry_address, run_probe: the shared DEV-entry runner)

BLOCKED_EXIT = 3
SLOT_MA_SIN = 0x45
BATCH = 16                        # angles per run; 16 batches cover 0..255
OUT_LO, OUT_HI = 0x2200, 0x2300   # work RAM: low bytes at $2200+i, high bytes at $2300+i (i = 0..15)
P_PRE_OUT, SP_OUT, P_POST_OUT = 0x2400, 0x2401, 0x2402
STEPS = 2048                      # the probe's maximum per-phase count
TABLE_PROBE = bytes([0x00, 0x24, 0x48, 0x6A])  # first four low bytes of T[0..3] in sin_lo (unique in ROM)
MUTANT_ANGLE = 10                 # sin_lo[10] is used by angles 10 and 138; batch 0 contains 10


def oracle(a):
    """Independent oracle: floor(32767*sin(2*pi*a/256)+0.5)."""
    return math.floor(32767 * math.sin(2 * math.pi * a / 256) + 0.5)


def guest(reset, entry, base):
    """Reset-replacement code at CPU address `reset` for the batch of angles base..base+BATCH-1.
    Returns (code, halt CPU address)."""
    code = bytearray([
        0x78,                    # SEI
        0xD8,                    # CLD
        0xA9, 0xF8, 0x53, 0x02,  # LDA #$F8; TAM #$02 -> MPR1 = work RAM ($2000-$3FFF)
        0xA2, 0xFF, 0x9A,        # LDX #$FF; TXS  (SP = $FF)
        0xA0, 0x00,              # LDY #0 (batch index)
    ])
    loop = reset + len(code)
    code += bytes([
        0x98,                                              # loop: TYA
        0x18,                                              # CLC
        0x69, base & 0xFF,                                 # ADC #base  (A = angle, D=0 here)
        0xF8,                                              # SED  (caller D=1 before the call)
        0x38,                                              # SEC  (caller C=1 before the call)
        0x48,                                              # PHA (save angle)
        0x08,                                              # PHP  (P before JSR)
        0x68,                                              # PLA
        0x8D, P_PRE_OUT & 0xFF, P_PRE_OUT >> 8,            # STA $2400 (P before JSR)
        0x68,                                              # PLA (restore angle)
        0x20, entry & 0xFF, entry >> 8,                    # JSR entry (X:A = result)
        0x99, OUT_LO & 0xFF, OUT_LO >> 8,                  # STA $2200,Y (low byte; no P effect)
        0x08,                                              # PHP  (P immediately after JSR, before CLD)
        0x68,                                              # PLA
        0x8D, P_POST_OUT & 0xFF, P_POST_OUT >> 8,          # STA $2402 (P after JSR)
        0xD8,                                              # CLD
        0x8A,                                              # TXA
        0x99, OUT_HI & 0xFF, OUT_HI >> 8,                  # STA $2300,Y (high byte)
        0xC8,                                              # INY
        0xC0, BATCH,                                       # CPY #BATCH
    ])
    branch_at = reset + len(code)                          # BNE loop (relative)
    code += bytes([0xD0, (loop - (branch_at + 2)) & 0xFF])
    code += bytes([
        0xBA,                                              # TSX
        0x8E, SP_OUT & 0xFF, SP_OUT >> 8,                  # STX $2401 (SP after the loop)
    ])
    halt = reset + len(code)
    code += bytes([0x80, 0xFE])                            # BRA -2
    return bytes(code), halt


def check(label, observation, halt, work_ram, base):
    """Raise ValueError on the first failed check. Returns the P byte after the loop (recorded only)."""
    if observation["pc"] != halt:
        raise ValueError(f"{label}: guest did not reach its halt loop (pc ${observation['pc']:04X}, want ${halt:04X})")
    if observation["mpr"][1] != 0xF8 or observation["mpr"][7] != 0:
        raise ValueError(f"{label}: MPR changed (mpr={observation['mpr']})")
    if work_ram[SP_OUT - 0x2000] != 0xFF:
        raise ValueError(f"{label}: stack not balanced (SP after loop ${work_ram[SP_OUT - 0x2000]:02X}, want $FF)")
    bad = []
    for i in range(BATCH):
        angle = (base + i) & 0xFF
        got = (work_ram[OUT_HI - 0x2000 + i] << 8) | work_ram[OUT_LO - 0x2000 + i]
        want = oracle(angle) & 0xFFFF
        if got != want:
            bad.append((angle, got, want))
    if bad:
        angle, got, want = bad[0]
        raise ValueError(f"{label}: {len(bad)} of {BATCH} angles wrong; first angle ${angle:02X}: "
                         f"X:A ${got:04X}, want ${want:04X}")
    # P preservation: effective bits C(0x01) Z(0x02) I(0x04) D(0x08) V(0x40) N(0x80) must match.
    # T(bit5) and B(bit4) are core-specific and are not asserted.
    P_MASK = 0xCF
    p_pre = work_ram[P_PRE_OUT - 0x2000]
    p_post = work_ram[P_POST_OUT - 0x2000]
    if (p_pre & P_MASK) != (p_post & P_MASK):
        raise ValueError(f"{label}: P not preserved (pre ${p_pre:02X}, post ${p_post:02X}, "
                         f"mask ${P_MASK:02X})")
    return p_pre, p_post


def run_batch(probe, rom_bytes, folder, cue, reset, entry, base, label):
    code, halt = guest(reset, entry, base)
    variant = bytearray(rom_bytes)
    variant[reset - 0xE000:reset - 0xE000 + len(code)] = code
    rom = folder / f"{label}.pce"
    rom.write_bytes(variant)
    rows = dev.run_probe(probe, rom.resolve(), cue, count=STEPS)
    cold = next(r for r in rows if r["phase"] == "cold")
    return cold, halt, bytes.fromhex(cold["work_ram_hex"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rom", type=Path, required=True)
    args = parser.parse_args()
    data = args.rom.read_bytes()
    verify_dev_rom(data, manifest=args.rom.parent / "manifest.json")
    reset = int.from_bytes(data[0x1FFE:0x2000], "little")
    entry = dev.entry_address(SLOT_MA_SIN)
    table = 3 * SLOT_MA_SIN
    body = data[table + 1] | data[table + 2] << 8
    if data[table] != 0x4C or not 0xE000 <= body < 0xFFF6:
        raise ValueError("slot $45 table entry is not a JMP into the DEV ROM body")
    probe = api.compile_probe(args.rom.parent)
    sys.path.insert(0, str(ROOT / "tools"))
    from environment import DEPENDENCIES  # noqa: E402
    evidence = {"scope": "DEV ROM real-core run of project-own MA_SIN, NOT System Card ABI compatibility",
                "rom_sha256": hashlib.sha256(data).hexdigest(),
                "geargrafx_revision": DEPENDENCIES["geargrafx"]["revision"],
                "batches": [], "angles_checked": 0, "negative_cases": []}
    with tempfile.TemporaryDirectory(prefix="pce-dev-sin-") as tmp:
        folder = Path(tmp)
        (folder / "original.bin").write_bytes(bytes(2352 * 32))
        cue = folder / "original.cue"
        cue.write_text('FILE "original.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
        for base in range(0, 256, BATCH):
            label = f"sin-{base:02x}"
            cold, halt, work_ram = run_batch(probe, data, folder, cue, reset, entry, base, label)
            p_pre, p_post = check(label, cold, halt, work_ram, base)
            evidence["batches"].append({"base": base, "angles": BATCH, "p_pre": p_pre, "p_post": p_post})
            evidence["angles_checked"] += BATCH
        if evidence["angles_checked"] != 256:
            raise ValueError("internal: not all 256 angles were checked")

        # Negative: one wrong table byte in the ROM copy (sin_lo[10]) must be rejected by batch 0.
        pos = data.find(TABLE_PROBE)
        if pos < 0 or data.find(TABLE_PROBE, pos + 1) >= 0:
            raise ValueError("sin_lo table probe is not unique in the ROM")
        mutant = bytearray(data)
        mutant[pos + MUTANT_ANGLE] ^= 0x01
        cold_m, halt_m, work_m = run_batch(probe, bytes(mutant), folder, cue, reset, entry, 0, "sin-table-mutant")
        try:
            check("sin-table-mutant", cold_m, halt_m, work_m, 0)
        except ValueError as error:
            if "angles wrong" not in str(error):
                raise ValueError(f"table mutant failed for an unrelated reason: {error}") from error
            evidence["negative_cases"].append({"case": "sin_lo[10] flipped", "rejected": True, "reason": str(error)})
        else:
            raise ValueError("table mutant (sin_lo[10] flipped) passed the real-core check")

        # Negative: break the PLP that restores the caller's P in slot $45.
        plp_rts = None
        for i in range(body - 0xE000, min(body - 0xE000 + 0x80, len(data) - 1)):
            if data[i] == 0x28 and data[i + 1] == 0x60:
                plp_rts = i
                break
        if plp_rts is None:
            raise ValueError("slot $45 PLP;RTS not found")
        pmutant = bytearray(data)
        pmutant[plp_rts] = 0xEA  # plp -> nop
        cold_p, halt_p, work_p = run_batch(probe, bytes(pmutant), folder, cue, reset, entry, 0, "sin-plp-mutant")
        try:
            check("sin-plp-mutant", cold_p, halt_p, work_p, 0)
        except ValueError as error:
            if "P not preserved" not in str(error) and "did not reach its halt loop" not in str(error):
                raise ValueError(f"PLP mutant failed for an unrelated reason: {error}") from error
            evidence["negative_cases"].append({"case": "slot_45_without_PLP", "rejected": True, "reason": str(error)})
        else:
            raise ValueError("PLP mutant (slot $45) passed the real-core check")

    (args.rom.parent / "geargrafx-dev-sin-evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print(f"PASS real Geargrafx DEV ROM: MA_SIN slot $45, all 256 angles in {len(evidence['batches'])} batches of "
          f"{BATCH}; X:A = Q15 sine, SP and MPR1/MPR7 checked each batch; P (mask 0xCF) preserved; "
          f"table and PLP mutants rejected")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, RuntimeError) as error:
        status = "BLOCKED" if "BLOCKED" in str(error) else "FAIL"
        print(f"DEV SIN integration {status}: {error}", file=sys.stderr)
        raise SystemExit(BLOCKED_EXIT if status == "BLOCKED" else 1) from error
