"""Real Geargrafx run of MA_SQRT (slot $44, Issue #78) through the DEV ROM entry $E0CC.

A guest program replaces the reset code of the built DEV ROM. It sets MPR1 to work RAM,
SP=$FF, sets D=1 and C=1 on purpose (the routine must still compute in binary and restore
the caller's flags), preloads Y=$A5, JSRs $E0CC with X:A = input (X high, A low), then
stores A, X, P, Y and SP at $2200-$2205 and halts.
The expectation is this project's own spec (src/overrides/slot_44.s), NOT the reference
System Card ABI, which stays UNVERIFIED. Every input 0..65535 is valid; there is no
invalid-input error path.
Scope: real-core cases at caller SP=$FF plus SP=$0C boundary (entry S=$0A, the lowest that
fits the 10 pushes without wrap). This is NOT an exhaustive test of the assembled routine;
the 65,536-input check is the Python model (tests/host/test_ma_sqrt_dev.py).
Run: python3 tests/integration/geargrafx_dev_sqrt.py --rom build/debug/bios-dev/dev-only-not-compatible-syscard3.pce
Needs the pinned cc65 and Geargrafx core (make setup, TOOLS_DIR on Linux). Missing tools exit 3 (BLOCKED).
"""
import hashlib
import json
import math
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
SLOT_MA_SQRT = 0x44
STORE = 0x2200  # $2200 A(root lo), $2201 X(root hi=0), $2202 P after, $2203 Y, $2205 SP after
CASES = [0, 1, 2, 3, 4, 15, 16, 17, 254, 255, 256, 257, 1023, 1024, 1025,
         32767, 32768, 65024, 65025, 65026, 65534, 65535]
Y_PRESET = 0xA5


def guest(reset, entry, x, a, sp=0xFF):
    """Reset-replacement code at CPU address `reset`. Returns (code, halt CPU address)."""
    code = bytes([0x78,                   # SEI (caller I=1)
                  0xA9, 0xF8, 0x53, 0x02,  # LDA #$F8; TAM #$02 -> MPR1 = work RAM ($2000-$3FFF)
                  0xA2, sp, 0x9A,          # LDX #sp; TXS (caller SP; JSR then pushes 2 bytes)
                  0xF8, 0x38, 0xB8,        # SED (caller D=1 on purpose), SEC (caller C=1), CLV (V=0)
                  0xA2, x,                 # LDX #x (input high)
                  0xA0, Y_PRESET,          # LDY #$A5
                  0xA9, a,                 # LDA #a (input low; sets N/Z at entry)
                  0x20, entry & 0xFF, entry >> 8,  # JSR entry
                  0x8D, STORE & 0xFF, STORE >> 8,              # STA $2200 (A = root lo)
                  0x8E, (STORE + 1) & 0xFF, (STORE + 1) >> 8,  # STX $2201 (X = root hi)
                  0x08, 0x68, 0x8D, (STORE + 2) & 0xFF, (STORE + 2) >> 8,  # PHP; PLA; STA $2202 (P after)
                  0x8C, (STORE + 3) & 0xFF, (STORE + 3) >> 8,  # STY $2203 (Y)
                  0xBA, 0x8E, (STORE + 5) & 0xFF, (STORE + 5) >> 8])  # TSX; STX $2205 (SP after)
    halt = reset + len(code)
    return code + bytes([0x80, 0xFE]), halt  # BRA -2


def check(label, rows, halt, x, a, sp=0xFF):
    cold = next(r for r in rows if r["phase"] == "cold")
    if cold["pc"] != halt:
        raise ValueError(f"{label}: guest did not reach its halt loop (pc ${cold['pc']:04X}, want ${halt:04X})")
    if cold["sp"] != sp:
        raise ValueError(f"{label}: stack not balanced (SP ${cold['sp']:02X}, want ${sp:02X})")
    if cold["mpr"][1] != 0xF8 or cold["mpr"][7] != 0:
        raise ValueError(f"{label}: MPR changed (mpr={cold['mpr']})")
    s = bytes.fromhex(cold["work_ram_0200_hex"])[:6]
    root_lo, root_hi, p_after, y, _, sp_after = s[0], s[1], s[2], s[3], s[4], s[5]
    n = (x << 8) | a
    want_root = math.isqrt(n)
    # P at entry: SEI, SED, SEC, CLV, then LDA #a sets N/Z. Restored by the routine (PLP). T/B masked.
    p_entry = 0x0D | (0x80 if a & 0x80 else 0) | (0x02 if a == 0 else 0)
    observed = (root_lo, root_hi, p_after & 0xCF, y, sp_after)
    want = (want_root & 0xFF, want_root >> 8, p_entry, Y_PRESET, sp)
    if observed != want:
        raise ValueError(f"{label}: input ${n:04X}: A/X/P/Y/SP observed {observed}, expected {want}")


def run_case(probe, base, folder, cue, reset, label, entry, x, a, sp=0xFF, count=2048):
    code, halt = guest(reset, entry, x, a, sp)
    variant = bytearray(base)
    variant[reset - 0xE000:reset - 0xE000 + len(code)] = code
    rom = folder / f"{label}.pce"
    rom.write_bytes(variant)
    rows = dev.run_probe(probe, rom.resolve(), cue, count=count)
    check(label, rows, halt, x, a, sp)


def main():
    parser = __import__("argparse").ArgumentParser(description=__doc__)
    parser.add_argument("--rom", type=Path, required=True)
    args = parser.parse_args()
    data = args.rom.read_bytes()
    verify_dev_rom(data, manifest=args.rom.parent / "manifest.json")
    reset = int.from_bytes(data[0x1FFE:0x2000], "little")
    entry = dev.entry_address(SLOT_MA_SQRT)
    table = 3 * SLOT_MA_SQRT  # the DEV API table is 3-byte JMPs from $E000; $E0CC is this JMP
    body = data[table + 1] | data[table + 2] << 8  # its target is the override body (api_slot_44)
    if data[table] != 0x4C or not 0xE000 <= body < 0xFFF6:
        raise ValueError("slot $44 table entry is not a JMP into the DEV ROM body")
    probe = api.compile_probe(args.rom.parent)
    evidence = {"scope": "DEV ROM real-core run of project-own MA_SQRT, NOT System Card ABI compatibility",
                "sp_meaning": "caller SP before JSR; entry SP = caller SP - 2",
                "precondition": "S at routine entry >= $0A (10 bytes pushed by the routine)",
                "mpr_checked": "MPR1 and MPR7 only",
                "rom_sha256": hashlib.sha256(data).hexdigest(), "geargrafx_revision": None,
                "cases": [], "negative_cases": []}
    # Geargrafx revision from the pinned dependency.
    sys.path.insert(0, str(ROOT / "tools"))
    from environment import DEPENDENCIES  # noqa: E402
    evidence["geargrafx_revision"] = DEPENDENCIES["geargrafx"]["revision"]
    with tempfile.TemporaryDirectory(prefix="pce-dev-sqrt-") as tmp:
        folder = Path(tmp)
        (folder / "original.bin").write_bytes(bytes(2352 * 32))
        cue = folder / "original.cue"
        cue.write_text('FILE "original.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
        for n in CASES:
            run_case(probe, data, folder, cue, reset, f"sqrt-{n:04x}", entry, n >> 8, n & 0xFF)
            evidence["cases"].append({"input": n, "expected_root": math.isqrt(n), "sp": 0xFF})
        # Precondition boundary: caller SP=$0C gives entry S=$0A (the lowest that fits the 10 pushes without wrap).
        for n in (65535, 255, 65025):
            run_case(probe, data, folder, cue, reset, f"lowsp-{n:04x}", entry, n >> 8, n & 0xFF, sp=0x0C)
            evidence["cases"].append({"input": n, "expected_root": math.isqrt(n), "sp": 0x0C})
        # Negative: the routine's CLD (second byte of the entry) must be present.
        # With D=1 on entry, the SBCs would run in BCD and produce wrong results.
        code_at = body - 0xE000
        if data[code_at] != 0x08 or data[code_at + 1] != 0xD8:
            raise ValueError("slot $44 body does not start with PHP; CLD")
        # Runtime check: replace CLD with NOP. At least one of the 3 mutant cases
        # must produce a wrong result (BCD SBC differs from binary SBC).
        mutant = bytearray(data)
        mutant[code_at + 1] = 0xEA
        rejected = 0
        for n in (65535, 256, 17):
            try:
                run_case(probe, bytes(mutant), folder, cue, reset, f"nocld-{n:04x}", entry, n >> 8, n & 0xFF)
            except ValueError:
                rejected += 1
        if rejected == 0:
            raise ValueError("mutant without CLD passed all 3 cases (CLD removal not detected)")
        evidence["negative_cases"].append({"case": "44_without_CLD_D1", "rejected": rejected,
                                           "total": 3})
    (args.rom.parent / "geargrafx-dev-sqrt-evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
    total = len(CASES) + 3  # the three boundary cases (caller SP=$0C, entry S=$0A)
    print(f"PASS real Geargrafx DEV ROM: MA_SQRT slot $44 {total} cases ({len(CASES)} at SP=$FF, 3 at SP=$0C "
          f"boundary; precondition S>=$0A at entry); A/X=isqrt(input), Y, P kept (D=1,C=1 entry), SP, MPR1/MPR7 "
          f"checked; no-CLD mutant rejected {rejected}/3")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        status = "BLOCKED" if "BLOCKED" in str(error) else "FAIL"
        print(f"DEV SQRT integration {status}: {error}", file=sys.stderr)
        raise SystemExit(BLOCKED_EXIT if status == "BLOCKED" else 1) from error
