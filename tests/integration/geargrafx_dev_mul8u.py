"""Real Geargrafx run of MA_MUL8U (slot $3F, Issue #436) through the DEV ROM entry $E0BD.

A guest program replaces the reset code of the built DEV ROM. It sets MPR1 to work RAM, SP=$FF, sets D=1 and C=1
on purpose (the routine must still multiply in binary and restore the caller's flags), preloads X=multiplier and
Y=$A5, JSRs $E0BD with A=multiplicand, then stores A, X, P, Y and SP at $2200-$2205 and halts.
The expectation is this project's own spec (src/overrides/slot_3F.s), NOT the reference System Card ABI, which
stays UNVERIFIED. Every 8-bit pair is a valid input, so there is no invalid-input error path to test; the cases
are edge values, carry-heavy values and a few mixed values.
Run: python3 tests/integration/geargrafx_dev_mul8u.py --rom build/debug/bios-dev/dev-only-not-compatible-syscard3.pce
Needs the pinned cc65 and Geargrafx core (make setup, TOOLS_DIR on Linux). Missing tools exit 3 (BLOCKED).
"""
import argparse
import hashlib
import json
import subprocess
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
SLOT_MA_MUL8U = 0x3F
STORE = 0x2200  # $2200 A, $2201 X, $2202 P after, $2203 Y, $2205 SP after ($2204 unused)
CASES = [(0, 0), (0, 255), (255, 0), (1, 1), (1, 255), (255, 1), (255, 255), (2, 128), (128, 2),
         (16, 16), (15, 17), (170, 85), (7, 9), (100, 100), (200, 3), (37, 91)]
Y_PRESET = 0xA5


def guest(reset, entry, a, x, sp=0xFF):
    """Reset-replacement code at CPU address `reset`. Returns (code, halt CPU address)."""
    code = bytes([0x78,                   # SEI
                  0xA9, 0xF8, 0x53, 0x02,  # LDA #$F8; TAM #$02 -> MPR1 = work RAM ($2000-$3FFF)
                  0xA2, sp, 0x9A,          # LDX #sp; TXS (caller SP; the JSR then pushes 2 bytes)
                  0xF8, 0x38, 0xB8,        # SED (caller D=1 on purpose), SEC (caller C=1), CLV (V=0)
                  0xA2, x,                 # LDX #x (multiplier)
                  0xA0, Y_PRESET,          # LDY #$A5
                  0xA9, a,                 # LDA #a (multiplicand); this LDA sets N/Z at entry
                  0x20, entry & 0xFF, entry >> 8,  # JSR entry
                  0x8D, STORE & 0xFF, STORE >> 8,              # STA $2200
                  0x8E, (STORE + 1) & 0xFF, (STORE + 1) >> 8,  # STX $2201
                  0x08, 0x68, 0x8D, (STORE + 2) & 0xFF, (STORE + 2) >> 8,  # PHP; PLA; STA $2202 (P after)
                  0x8C, (STORE + 3) & 0xFF, (STORE + 3) >> 8,  # STY $2203
                  0xBA, 0x8E, (STORE + 5) & 0xFF, (STORE + 5) >> 8])  # TSX; STX $2205 (SP after)
    halt = reset + len(code)
    return code + bytes([0x80, 0xFE]), halt  # BRA -2


class Mismatch(ValueError):
    """The guest ran to its halt loop but returned a value that differs from the spec."""


def expected(a, x):
    product = a * x
    return product & 0xFF, product >> 8


def expected_p(a):
    """P at JSR entry: I=1 (SEI), D=1 (SED), C=1 (SEC), V=0 (CLV), N/Z from LDA #a. Restored by the routine."""
    return 0x04 | 0x08 | 0x01 | (0x80 if a & 0x80 else 0) | (0x02 if a == 0 else 0)


def check(label, rows, halt, a, x, sp=0xFF):
    cold = next(r for r in rows if r["phase"] == "cold")
    if cold["pc"] != halt:
        raise ValueError(f"{label}: guest did not reach its halt loop (pc ${cold['pc']:04X}, want ${halt:04X})")
    if cold["sp"] != sp:
        raise ValueError(f"{label}: stack not balanced (SP ${cold['sp']:02X}, want ${sp:02X})")
    if cold["mpr"][1] != 0xF8 or cold["mpr"][7] != 0:
        raise ValueError(f"{label}: MPR changed (mpr={cold['mpr']})")
    s = bytes.fromhex(cold["work_ram_0200_hex"])[:6]
    p_after, y, sp_after = s[2], s[3], s[5]
    lo, hi = expected(a, x)
    # P at entry is fixed by the guest: SEI, SED, SEC, CLV, then LDA #a sets N/Z. T/B (bits 4-5) are masked.
    observed = (s[0], s[1], p_after & 0xCF, y, sp_after)
    want = (lo, hi, expected_p(a), Y_PRESET, sp)
    if observed != want:
        raise Mismatch(f"{label}: A/X/P/Y/SP observed {observed}, expected {want}")


def run_case(probe, base, folder, cue, reset, label, entry, a, x, sp=0xFF):
    code, halt = guest(reset, entry, a, x, sp)
    variant = bytearray(base)
    variant[reset - 0xE000:reset - 0xE000 + len(code)] = code
    rom = folder / f"{label}.pce"
    rom.write_bytes(variant)
    rows = dev.run_probe(probe, rom.resolve(), cue)
    check(label, rows, halt, a, x, sp)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rom", type=Path, required=True)
    args = parser.parse_args()
    data = args.rom.read_bytes()
    verify_dev_rom(data, manifest=args.rom.parent / "manifest.json")
    reset = int.from_bytes(data[0x1FFE:0x2000], "little")
    entry = dev.entry_address(SLOT_MA_MUL8U)
    table = 3 * SLOT_MA_MUL8U  # the DEV API table is 3-byte JMPs from $E000; $E0BD is this JMP
    body = data[table + 1] | data[table + 2] << 8  # its target is the override body (api_slot_3F)
    if data[table] != 0x4C or not 0xE000 <= body < 0xFFF6:
        raise ValueError("slot $3F table entry is not a JMP into the DEV ROM body")
    probe = api.compile_probe(args.rom.parent)
    evidence = {"scope": "DEV ROM real-core run of project-own MA_MUL8U, NOT System Card ABI compatibility",
                "rom_sha256": hashlib.sha256(data).hexdigest(), "cases": [], "negative_cases": []}
    with tempfile.TemporaryDirectory(prefix="pce-dev-mul8u-") as tmp:
        folder = Path(tmp)
        (folder / "original.bin").write_bytes(bytes(2352 * 32))
        cue = folder / "original.cue"
        cue.write_text('FILE "original.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
        for a, x in CASES:
            run_case(probe, data, folder, cue, reset, f"mul-{a:02x}-{x:02x}", entry, a, x)
            lo, hi = expected(a, x)
            evidence["cases"].append({"a": a, "x": x, "sp": 0xFF, "expected_a": lo, "expected_x": hi})
        # Precondition boundary: caller SP=$07 gives SP=$05 at entry (the lowest value that does not wrap the 5 pushes).
        for a, x in ((255, 255), (37, 91)):
            run_case(probe, data, folder, cue, reset, f"lowsp-{a:02x}-{x:02x}", entry, a, x, sp=0x07)
            lo, hi = expected(a, x)
            evidence["cases"].append({"a": a, "x": x, "sp": 0x07, "expected_a": lo, "expected_x": hi})
        # Negative: the routine's CLD (second byte of the entry) replaced by NOP. With D=1 the ADCs then run in
        # BCD, so the real core must reject at least one case; a pass would mean the D=1 check is not effective.
        code_at = body - 0xE000
        if data[code_at] != 0x08 or data[code_at + 1] != 0xD8:
            raise ValueError("slot $3F body does not start with PHP; CLD")
        mutant = bytearray(data)
        mutant[code_at + 1] = 0xEA
        rejected = []
        for a, x in ((255, 255), (99, 99), (170, 85)):
            try:
                run_case(probe, bytes(mutant), folder, cue, reset, f"nocld-{a:02x}-{x:02x}", entry, a, x)
            except Mismatch as error:  # only a value mismatch counts; infrastructure errors must propagate
                rejected.append(str(error))
        if len(rejected) != 3:  # every mutant case must be a value mismatch, not a silent pass or an infrastructure error
            raise ValueError("mutant without CLD passed every D=1 case on the real core")
        evidence["negative_cases"].append({"case": "3F_without_CLD_D1", "rejected": len(rejected),
                                           "first": rejected[0]})
    (args.rom.parent / "geargrafx-dev-mul8u-evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
    total = len(CASES) + 2  # the two low-SP boundary cases (caller SP=$07, entry SP=$05)
    print(f"PASS real Geargrafx DEV ROM: MA_MUL8U slot $3F {total} cases ({len(CASES)} at SP=$FF, 2 at SP=$07 "
          f"boundary; precondition S>=$05 at entry); A/X product, Y, P kept (D=1,C=1 entry), SP, MPR checked; "
          f"no-CLD mutant rejected {len(rejected)}/3")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        status = "BLOCKED" if "BLOCKED" in str(error) else "FAIL"
        print(f"DEV MUL8U integration {status}: {error}", file=sys.stderr)
        raise SystemExit(BLOCKED_EXIT if status == "BLOCKED" else 1) from error
