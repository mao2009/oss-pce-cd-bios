"""Real Geargrafx run of MA_SIN (slot $45, Issue #79) through the DEV ROM entry $E0CF.

A guest program replaces the reset code of the built DEV ROM. It sets MPR1 to work RAM, sets D=1 and
C=1 on purpose, SP=$FF, then loops over every 8-bit angle 0..255 with Y as the counter:
    TYA; JSR $E0CF; STA $2200,Y (low byte); TXA; STA $2300,Y (high byte); INY; BNE loop
so one run covers all 256 inputs. Y is the loop counter, so a Y clobbered by the routine would corrupt
the sequence (checked through the stored values). After the loop it records SP at $2401 and P at $2402.

Expectation: the project's own DEV spec (src/overrides/slot_45.s), NOT the reference System Card ABI
(UNVERIFIED): X:A = floor(32767*sin(2*pi*A/256)+0.5), signed 16-bit, X = high byte, A = low byte.

Checked: all 256 X:A values; the halt PC; SP balanced at $FF; MPR1 = $F8 and MPR7 = 0; a table mutant
(one table byte flipped in the ROM copy) is rejected. P bits are recorded, not asserted (the real core's
PLP handling is core-specific; see geargrafx_dev_sqrt.py). Work RAM outside $2200-$2402 is not checked.

Run: python3 tests/integration/geargrafx_dev_sin.py --rom build/debug/bios-dev/dev-only-not-compatible-syscard3.pce
Needs the pinned cc65 and Geargrafx core (make setup, TOOLS_DIR on Linux). Missing tools exit 3 (BLOCKED).
NOT YET RUN: this file has not been executed against the real core (see the PR's open items).
"""
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
OUT_LO, OUT_HI = 0x2200, 0x2300   # work RAM: low bytes at $2200+A, high bytes at $2300+A
SP_OUT, P_OUT = 0x2401, 0x2402
STEPS = 60000                     # 256 calls x a few dozen instructions, plus the guest loop
TABLE_PROBE = bytes([0x00, 0x24, 0x48, 0x6A])  # first four low bytes of T[0..3] in sin_lo (unique in ROM)


def oracle(a):
    """Independent oracle: floor(32767*sin(2*pi*a/256)+0.5)."""
    return math.floor(32767 * math.sin(2 * math.pi * a / 256) + 0.5)


def guest(reset, entry):
    """Reset-replacement code at CPU address `reset`. Returns (code, halt CPU address)."""
    code = bytearray([
        0x78,                    # SEI
        0xF8,                    # SED   (caller D=1: the routine must clear it internally)
        0x38,                    # SEC   (caller C=1)
        0xA9, 0xF8, 0x53, 0x02,  # LDA #$F8; TAM #$02 -> MPR1 = work RAM ($2000-$3FFF)
        0xA2, 0xFF, 0x9A,        # LDX #$FF; TXS  (SP = $FF)
        0xA0, 0x00,              # LDY #0 (loop counter = angle)
    ])
    loop = reset + len(code)
    code += bytes([
        0x98,                                              # loop: TYA   (A = angle)
        0x20, entry & 0xFF, entry >> 8,                    # JSR entry   (X:A = result)
        0x99, OUT_LO & 0xFF, OUT_LO >> 8,                  # STA $2200,Y (low byte)
        0x8A,                                              # TXA
        0x99, OUT_HI & 0xFF, OUT_HI >> 8,                  # STA $2300,Y (high byte)
        0xC8,                                              # INY
    ])
    branch_at = reset + len(code)                          # BNE loop (relative)
    code += bytes([0xD0, (loop - (branch_at + 2)) & 0xFF])
    code += bytes([
        0xBA,                                              # TSX
        0x8E, SP_OUT & 0xFF, SP_OUT >> 8,                  # STX $2401 (SP after the loop)
        0x08, 0x68,                                        # PHP; PLA
        0x8D, P_OUT & 0xFF, P_OUT >> 8,                    # STA $2402 (P after the loop)
    ])
    halt = reset + len(code)
    code += bytes([0x80, 0xFE])                            # BRA -2
    return bytes(code), halt


def check(label, observation, halt, work_ram):
    if observation["pc"] != halt:
        raise ValueError(f"{label}: guest did not reach its halt loop (pc ${observation['pc']:04X}, want ${halt:04X})")
    if observation["mpr"][1] != 0xF8 or observation["mpr"][7] != 0:
        raise ValueError(f"{label}: MPR changed (mpr={observation['mpr']})")
    if work_ram[SP_OUT - 0x2000] != 0xFF:
        raise ValueError(f"{label}: stack not balanced (SP after loop ${work_ram[SP_OUT - 0x2000]:02X}, want $FF)")
    bad = []
    for a in range(256):
        lo = work_ram[OUT_LO - 0x2000 + a]
        hi = work_ram[OUT_HI - 0x2000 + a]
        got = ((hi << 8) | lo)
        want = oracle(a) & 0xFFFF
        if got != want:
            bad.append((a, got, want))
    if bad:
        a, got, want = bad[0]
        raise ValueError(f"{label}: {len(bad)} of 256 angles wrong; first angle ${a:02X}: X:A ${got:04X}, want ${want:04X}")
    return {"p_after": work_ram[P_OUT - 0x2000]}


def run_once(probe, rom_bytes, folder, cue, reset, entry, label):
    code, halt = guest(reset, entry)
    variant = bytearray(rom_bytes)
    variant[reset - 0xE000:reset - 0xE000 + len(code)] = code
    rom = folder / f"{label}.pce"
    rom.write_bytes(variant)
    rows = dev.run_probe(probe, rom.resolve(), cue, count=STEPS)
    cold = next(r for r in rows if r["phase"] == "cold")
    work_ram = bytes.fromhex(cold["work_ram_hex"])
    return cold, halt, work_ram


def main():
    parser = __import__("argparse").ArgumentParser(description=__doc__)
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
    evidence = {"scope": "DEV ROM real-core run of project-own MA_SIN, NOT System Card ABI compatibility",
                "rom_sha256": hashlib.sha256(data).hexdigest(), "cases": "all 256 angles in one run",
                "negative_cases": [], "geargrafx_revision": None}
    sys.path.insert(0, str(ROOT / "tools"))
    from environment import DEPENDENCIES  # noqa: E402
    evidence["geargrafx_revision"] = DEPENDENCIES["geargrafx"]["revision"]
    with tempfile.TemporaryDirectory(prefix="pce-dev-sin-") as tmp:
        folder = Path(tmp)
        (folder / "original.bin").write_bytes(bytes(2352 * 32))
        cue = folder / "original.cue"
        cue.write_text('FILE "original.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
        cold, halt, work_ram = run_once(probe, data, folder, cue, reset, entry, "sin-all")
        info = check("sin-all", cold, halt, work_ram)
        evidence["p_after_recorded_not_asserted"] = info["p_after"]

        # Negative: one wrong table byte in the ROM copy (sin_lo[10]) must be rejected.
        pos = data.find(TABLE_PROBE)
        if pos < 0 or data.find(TABLE_PROBE, pos + 1) >= 0:
            raise ValueError("sin_lo table probe is not unique in the ROM")
        mutant = bytearray(data)
        mutant[pos + 10] ^= 0x01
        cold_m, halt_m, work_m = run_once(probe, bytes(mutant), folder, cue, reset, entry, "sin-table-mutant")
        try:
            check("sin-table-mutant", cold_m, halt_m, work_m)
        except ValueError as error:
            if "angles wrong" not in str(error):
                raise ValueError(f"table mutant failed for an unrelated reason: {error}") from error
            evidence["negative_cases"].append({"case": "sin_lo[10] flipped", "rejected": True, "reason": str(error)})
        else:
            raise ValueError("table mutant (sin_lo[10] flipped) passed the real-core check")

    (args.rom.parent / "geargrafx-dev-sin-evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print("PASS real Geargrafx DEV ROM: MA_SIN slot $45, 256 angles in one run; X:A = Q15 sine, "
          "SP and MPR1/MPR7 checked; table mutant rejected")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, RuntimeError) as error:
        status = "BLOCKED" if "BLOCKED" in str(error) else "FAIL"
        print(f"DEV SIN integration {status}: {error}", file=sys.stderr)
        raise SystemExit(BLOCKED_EXIT if status == "BLOCKED" else 1) from error
