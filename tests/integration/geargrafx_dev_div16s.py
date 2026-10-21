"""Real Geargrafx run of MA_DIV16S (slot $42, Issue #447) through the DEV ROM entry $E0C6.

A guest program replaces the reset code of the built DEV ROM. It sets MPR1 to work RAM, sets the caller SP, pushes the
divisor (low, then high), sets D=1 and C=1 on purpose, preloads Y=$A5, JSRs $E0C6 with X:A = dividend, then stores
X:A, P, Y and the remainder bytes (popped from the divisor slots) and SP at $2200-$2206 and halts.
The expectation is this project's own spec (src/overrides/slot_42.s and tools/ma_div16s_model.py), NOT the reference
BIOS MA_DIV16S ABI, which stays UNVERIFIED. Every 16-bit pair is a valid input; the divide-by-zero and -32768/-1 cases are
defined by the project contract and are checked as such.
Precondition under test: S at routine entry >= $07. The caller SP is set so that entry S is $07 at the lowest case.
Scope: 20 real-core cases plus the no-CLD mutant. This is NOT an exhaustive test of the assembled routine; the
host model is tests/host/test_ma_div16s.py.
Run: python3 tests/integration/geargrafx_dev_div16s.py --rom build/debug/bios-dev/dev-only-not-compatible-syscard3.pce
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
from ma_div16s_model import expected as model  # noqa: E402

BLOCKED_EXIT = 3
SLOT_MA_DIV16S = 0x42
# The shared DEV runner defaults to 400 instruction steps, which the 16-iteration restoring division does not fit in.
# The API probe accepts at most 2048 steps. The guest halts in a BRA loop after the call, so a larger budget only
# observes the same halted state.
CALL_STEPS = 2000
STORE = 0x2200  # $2200 quotient lo, $2201 quotient hi, $2202 P after, $2203 Y, $2204 rem hi, $2205 rem lo, $2206 SP after
CASES = [  # (dividend, divisor) as unsigned 16-bit encodings
    (0x0000, 0x0001), (0x0064, 0x0007), (0xFF9C, 0x0007), (0x0064, 0xFFF9), (0xFF9C, 0xFFF9),
    (0x7FFF, 0x0001), (0x8000, 0x0002), (0x8000, 0xFFFF), (0x0005, 0x0000), (0xFFFB, 0x0000),
    (0x0000, 0x0000), (0xFFFF, 0x0002), (0x0007, 0xFFFD), (0x0001, 0x0001), (0xFFFF, 0xFFFF),
    (0x7FFF, 0xFFFF), (0x03E8, 0x0003), (0xFC18, 0x0003), (0x1234, 0x0100), (0x8001, 0x0100),
]
# Inputs whose BCD interpretation differs from binary (checked on the real core: each mutant case mismatches).
MUTANT_CASES = [(0xFF9C, 0xFFF9), (0xFFFF, 0xFFFF), (0x9999, 0x0001)]
Y_PRESET = 0xA5
CALLER_SP = 0xFF
BOUNDARY_SP = 0x0B  # caller SP such that entry S = $0B - 4 = $07 (2 divisor pushes, then JSR pushes 2)


class Mismatch(ValueError):
    """The guest ran to its halt loop but returned a value that differs from the spec."""


def guest(reset, entry, dividend, divisor, sp=CALLER_SP):
    """Reset-replacement code at CPU address `reset`. Returns (code, halt CPU address)."""
    lo_a, hi_x = dividend & 0xFF, dividend >> 8
    d_lo, d_hi = divisor & 0xFF, divisor >> 8
    code = bytes([0x78,                   # SEI
                  0xA9, 0xF8, 0x53, 0x02,  # LDA #$F8; TAM #$02 -> MPR1 = work RAM ($2000-$3FFF)
                  0xA2, sp, 0x9A,          # LDX #sp; TXS (caller SP; divisor pushes and the JSR follow)
                  0xF8, 0x38,              # SED (caller D=1 on purpose), SEC (caller C=1)
                  0xA9, d_lo, 0x48,        # LDA #divisor low; PHA
                  0xA9, d_hi, 0x48,        # LDA #divisor high; PHA (high on top, as the contract requires)
                  0xA0, Y_PRESET,          # LDY #$A5
                  0xA9, lo_a,              # LDA #dividend low
                  0xA2, hi_x,              # LDX #dividend high
                  0x20, entry & 0xFF, entry >> 8,  # JSR entry
                  0x8D, STORE & 0xFF, STORE >> 8,              # STA $2200 (quotient low)
                  0x8E, (STORE + 1) & 0xFF, (STORE + 1) >> 8,  # STX $2201 (quotient high)
                  0x08, 0x68, 0x8D, (STORE + 2) & 0xFF, (STORE + 2) >> 8,  # PHP; PLA; STA $2202 (P after)
                  0x8C, (STORE + 3) & 0xFF, (STORE + 3) >> 8,  # STY $2203
                  0x68, 0x8D, (STORE + 4) & 0xFF, (STORE + 4) >> 8,  # PLA (remainder high); STA $2204
                  0x68, 0x8D, (STORE + 5) & 0xFF, (STORE + 5) >> 8,  # PLA (remainder low); STA $2205
                  0xBA, 0x8E, (STORE + 6) & 0xFF, (STORE + 6) >> 8])  # TSX; STX $2206 (SP after)
    halt = reset + len(code)
    return code + bytes([0x80, 0xFE]), halt  # BRA -2


def expected_p(q16, c, v):
    """P after the call: I=1 and D=1 kept from the caller (SEI, SED); C, V, N, Z from the contract."""
    return 0x04 | 0x08 | c | (v << 6) | (q16 >> 15) << 7 | (0x02 if q16 == 0 else 0)


def check(label, rows, halt, dividend, divisor, sp=CALLER_SP):
    cold = next(r for r in rows if r["phase"] == "cold")
    if cold["pc"] != halt:
        raise ValueError(f"{label}: guest did not reach its halt loop (pc ${cold['pc']:04X}, want ${halt:04X})")
    if cold["sp"] != sp:
        raise ValueError(f"{label}: stack not balanced (SP ${cold['sp']:02X}, want ${sp:02X})")
    if cold["mpr"][1] != 0xF8 or cold["mpr"][7] != 0:
        raise ValueError(f"{label}: MPR changed (mpr={cold['mpr']})")
    s = bytes.fromhex(cold["work_ram_0200_hex"])[:7]
    q16, r16, c, v, n, z = model(dividend, divisor)
    observed = (s[0], s[1], s[2] & 0xCF, s[3], s[4], s[5], s[6])
    want = (q16 & 0xFF, q16 >> 8, expected_p(q16, c, v) & 0xCF, Y_PRESET, r16 >> 8, r16 & 0xFF, sp)
    if observed != want:
        raise Mismatch(f"{label}: A/X/P/Y/remHI/remLO/SP observed {observed}, expected {want}")


def run_case(probe, base, folder, cue, reset, label, entry, dividend, divisor, sp=CALLER_SP):
    code, halt = guest(reset, entry, dividend, divisor, sp)
    variant = bytearray(base)
    variant[reset - 0xE000:reset - 0xE000 + len(code)] = code
    rom = folder / f"{label}.pce"
    rom.write_bytes(variant)
    rows = dev.run_probe(probe, rom.resolve(), cue, count=CALL_STEPS)
    check(label, rows, halt, dividend, divisor, sp)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rom", type=Path, required=True)
    args = parser.parse_args()
    data = args.rom.read_bytes()
    verify_dev_rom(data, manifest=args.rom.parent / "manifest.json")
    reset = int.from_bytes(data[0x1FFE:0x2000], "little")
    entry = dev.entry_address(SLOT_MA_DIV16S)
    table = 3 * SLOT_MA_DIV16S  # the DEV API table is 3-byte JMPs from $E000
    body = data[table + 1] | data[table + 2] << 8  # target of the JMP = the override body (api_slot_42)
    if data[table] != 0x4C or not 0xE000 <= body < 0xFFF6:
        raise ValueError("slot $42 table entry is not a JMP into the DEV ROM body")
    probe = api.compile_probe(args.rom.parent)
    evidence = {"scope": "DEV ROM real-core run of project-own MA_DIV16S, NOT the reference BIOS ABI",
                "sp_meaning": "caller SP before the divisor pushes; entry SP = caller SP - 4",
                "mpr_checked": "MPR1 and MPR7 only",
                "rom_sha256": hashlib.sha256(data).hexdigest(), "cases": [], "negative_cases": []}
    with tempfile.TemporaryDirectory(prefix="pce-dev-div16s-") as tmp:
        folder = Path(tmp)
        (folder / "original.bin").write_bytes(bytes(2352 * 32))
        cue = folder / "original.cue"
        cue.write_text('FILE "original.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
        for dividend, divisor in CASES:
            run_case(probe, data, folder, cue, reset, f"div-{dividend:04x}-{divisor:04x}", entry, dividend, divisor)
            evidence["cases"].append({"dividend": dividend, "divisor": divisor, "caller_sp": CALLER_SP})
        # Precondition boundary: caller SP=$0B gives entry S=$07, the lowest value that does not wrap the 7 pushes.
        for dividend, divisor in ((0xFF9C, 0x0007), (0x8000, 0xFFFF)):
            run_case(probe, data, folder, cue, reset, f"lowsp-{dividend:04x}-{divisor:04x}", entry, dividend, divisor,
                     sp=BOUNDARY_SP)
            evidence["cases"].append({"dividend": dividend, "divisor": divisor, "caller_sp": BOUNDARY_SP})
        # Negative: the routine's CLD (second byte of the entry) replaced by NOP. With D=1 the SBC/ADC run in BCD, so
        # every mutant case must differ from the spec; a case that still matches means the D=1 check is weak.
        code_at = body - 0xE000
        if data[code_at] != 0x08 or data[code_at + 1] != 0xD8:
            raise ValueError("slot $42 body does not start with PHP; CLD")
        mutant = bytearray(data)
        mutant[code_at + 1] = 0xEA
        rejected = []
        for dividend, divisor in MUTANT_CASES:
            try:
                run_case(probe, bytes(mutant), folder, cue, reset, f"nocld-{dividend:04x}-{divisor:04x}", entry,
                         dividend, divisor)
            except Mismatch as error:  # only a value mismatch counts; infrastructure errors must propagate
                rejected.append(str(error))
        if len(rejected) != len(MUTANT_CASES):
            raise ValueError("mutant without CLD matched the spec in at least one case "
                             f"(expected {len(MUTANT_CASES)} mismatches)")
        evidence["negative_cases"].append({"case": "42_without_CLD_D1", "rejected": len(rejected),
                                           "first": rejected[0]})
    (args.rom.parent / "geargrafx-dev-div16s-evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
    total = len(CASES) + 2  # two boundary cases at caller SP=$0B (entry S=$07)
    print(f"PASS real Geargrafx DEV ROM: MA_DIV16S slot $42 {total} cases ({len(CASES)} at caller SP=$FF, 2 at caller "
          f"SP=$0B boundary; precondition S>=$07 at entry); quotient, remainder, P (C/V/N/Z), Y, SP, MPR1/MPR7 checked; "
          f"no-CLD mutant rejected {len(rejected)}/{len(MUTANT_CASES)}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        status = "BLOCKED" if "BLOCKED" in str(error) else "FAIL"
        print(f"DEV DIV16S integration {status}: {error}", file=sys.stderr)
        sys.exit(BLOCKED_EXIT if status == "BLOCKED" else 1)
