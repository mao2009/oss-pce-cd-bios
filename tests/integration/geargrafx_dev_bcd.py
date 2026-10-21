"""Real Geargrafx run of EX_BCDBIN (slot $3D) and EX_BINBCD (slot $3C) through the DEV ROM entries (Issue #71/#70).

A guest program replaces the reset code of the built DEV ROM. It sets MPR1 to work RAM, preloads X/Y, JSRs the
candidate entry with one input in A, stores A/P/X/Y at $2200-$2203 and halts. The expectations are this project's
own specs (docs/ex-bcdbin.md, docs/ex-binbcd-internal.md), NOT the reference System Card ABI, which stays UNVERIFIED.
Run: python3 tests/integration/geargrafx_dev_bcd.py --rom build/debug/bios-dev/dev-only-not-compatible-syscard3.pce
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

BLOCKED_EXIT = 3
CALL_TIMEOUT_STEPS = 400  # the guest plus the routine take well under this many instructions
STORE = 0x2200  # guest writes A, P, X, Y here; the probe dumps work RAM from $2200
SLOT_EX_BCDBIN, SLOT_EX_BINBCD = 0x3D, 0x3C


def entry_address(slot):
    return 0xE000 + 3 * slot  # the DEV API table is 3-byte JMPs from $E000


def guest(reset, entry, value, x=0x5A, y=0xA5):
    """Reset-replacement code (CPU address `reset`): SEI CLD, MPR1=work RAM, SP=$FF, X/Y/A preset, JSR entry,
    store A/P/X/Y, halt. Returns (code, halt CPU address)."""
    code = bytes([0x78, 0xD8,              # SEI, CLD (D=0 is the routine's precondition)
                  0xA9, 0xF8, 0x53, 0x02,  # LDA #$F8; TAM #$02 -> MPR1 = work RAM ($2000-$3FFF)
                  0xA2, 0xFF, 0x9A,        # LDX #$FF; TXS
                  0xA2, x, 0xA0, y,        # LDX #x; LDY #y
                  0xA9, value,             # LDA #value
                  0x20, entry & 0xFF, entry >> 8,  # JSR entry
                  0x08,                    # PHP (flags after the call)
                  0x8D, STORE & 0xFF, STORE >> 8,  # STA $2200
                  0x68,                    # PLA
                  0x8D, (STORE + 1) & 0xFF, (STORE + 1) >> 8,  # STA $2201 (P)
                  0x8E, (STORE + 2) & 0xFF, (STORE + 2) >> 8,  # STX $2202
                  0x8C, (STORE + 3) & 0xFF, (STORE + 3) >> 8])  # STY $2203
    halt = reset + len(code)
    return code + bytes([0x80, 0xFE]), halt  # BRA -2


def expected_bcdbin(value):
    """docs/ex-bcdbin.md: packed BCD -> (A, C). Invalid (either nibble > 9): A unchanged, C=1."""
    tens, ones = value >> 4, value & 0x0F
    if tens > 9 or ones > 9:
        return value, 1
    return tens * 10 + ones, 0


def expected_binbcd(value):
    """docs/ex-binbcd-internal.md: binary 0..99 -> (packed BCD A, C). Out of range: A unchanged, C=1."""
    if value > 99:
        return value, 1
    return ((value // 10) << 4) | (value % 10), 0


def check(label, rows, halt, expected_a, expected_c, expected_x, expected_y):
    cold = next(r for r in rows if r["phase"] == "cold")
    if cold["pc"] != halt:
        raise ValueError(f"{label}: guest did not reach its halt loop (pc ${cold['pc']:04X}, want ${halt:04X})")
    if cold["sp"] != 0xFF:
        raise ValueError(f"{label}: stack not balanced (SP ${cold['sp']:02X})")
    if cold["mpr"][1] != 0xF8 or cold["mpr"][7] != 0:
        raise ValueError(f"{label}: MPR changed (mpr={cold['mpr']})")
    stored = bytes.fromhex(cold["work_ram_0200_hex"])[:4]
    observed = (stored[0], stored[1] & 1, stored[2], stored[3])
    if observed != (expected_a, expected_c, expected_x, expected_y):
        raise ValueError(f"{label}: A/C/X/Y observed {observed}, expected "
                         f"{(expected_a, expected_c, expected_x, expected_y)}")


def run_probe(probe, rom, cue, count=CALL_TIMEOUT_STEPS):
    """api.run_probe with an explicit instruction count (its fixed default of 16 is shorter than the guest)."""
    result = subprocess.run([str(probe), str(rom), str(cue), str(count)], timeout=120,
                            check=True, capture_output=True, text=True)
    observations = [json.loads(line[4:]) for line in result.stdout.splitlines() if line.startswith("OBS ")]
    caps = [json.loads(line[4:]) for line in result.stdout.splitlines() if line.startswith("CAP ")]
    if len(caps) != 1 or caps[0].get("instruction_step") is not True or caps[0].get("cdrom_media") is not True:
        raise ValueError("instruction debugger/System Card capability missing")
    if [r["phase"] for r in observations] != ["loaded", "cold", "nonreturning", "warm"]:
        raise ValueError("missing real core observations")
    return observations


def run_case(probe, rom_bytes, folder, cue, reset, label, entry, value, check_args):
    code, halt = guest(reset, entry, value)
    variant = bytearray(rom_bytes)
    variant[reset - 0xE000:reset - 0xE000 + len(code)] = code
    rom = folder / f"{label}.pce"
    rom.write_bytes(variant)
    rows = run_probe(probe, rom.resolve(), cue)
    check(label, rows, halt, *check_args)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rom", type=Path, required=True)
    args = parser.parse_args()
    data = args.rom.read_bytes()
    verify_dev_rom(data, manifest=args.rom.parent / "manifest.json")
    reset = int.from_bytes(data[0x1FFE:0x2000], "little")
    probe = api.compile_probe(args.rom.parent)
    evidence = {"scope": "DEV ROM real-core run of project-own EX_BCDBIN/EX_BINBCD, NOT System Card ABI compatibility",
                "rom_sha256": hashlib.sha256(data).hexdigest(), "cases": [], "negative_cases": []}
    with tempfile.TemporaryDirectory(prefix="pce-dev-bcd-") as tmp:
        folder = Path(tmp)
        (folder / "original.bin").write_bytes(bytes(2352 * 32))
        cue = folder / "original.cue"
        cue.write_text('FILE "original.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
        bcdbin_entry, binbcd_entry = entry_address(SLOT_EX_BCDBIN), entry_address(SLOT_EX_BINBCD)
        bcdbin_inputs = [0x00, 0x09, 0x10, 0x19, 0x90, 0x99, 0x0A, 0x0F, 0x1A, 0x9A, 0xA0, 0xFF]
        binbcd_inputs = [0, 1, 9, 10, 42, 99, 100, 128, 255]
        for value in bcdbin_inputs:
            a, c = expected_bcdbin(value)
            run_case(probe, data, folder, cue, reset, f"bcdbin-{value:02x}", bcdbin_entry, value,
                     (a, c, 0x5A, 0xA5))
            evidence["cases"].append({"slot": "3D", "input": value, "expected_a": a, "expected_c": c})
        for value in binbcd_inputs:
            a, c = expected_binbcd(value)
            run_case(probe, data, folder, cue, reset, f"binbcd-{value:02x}", binbcd_entry, value,
                     (a, c, 0x5A, 0xA5))
            evidence["cases"].append({"slot": "3C", "input": value, "expected_a": a, "expected_c": c})
        # Negative: the 3C entry called with the 3D input (0x42 is valid for both specs but gives different A).
        wrong = run_case_expect_fail(probe, data, folder, cue, reset, "wrong-entry", binbcd_entry, 0x42,
                                     expected_bcdbin(0x42) + (0x5A, 0xA5))
        evidence["negative_cases"].append({"case": "3C_entry_with_3D_expectation", "observed": wrong})
    (args.rom.parent / "geargrafx-dev-bcd-evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print(f"PASS real Geargrafx DEV ROM: EX_BCDBIN slot $3D {len(bcdbin_inputs)} inputs, EX_BINBCD slot $3C "
          f"{len(binbcd_inputs)} inputs; A/C/X/Y, SP, MPR checked; 1 wrong-entry negative detected")


def run_case_expect_fail(probe, data, folder, cue, reset, label, entry, value, check_args):
    try:
        run_case(probe, data, folder, cue, reset, label, entry, value, check_args)
    except ValueError as error:
        return str(error)
    raise ValueError(f"negative case {label} unexpectedly passed")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        status = "BLOCKED" if "BLOCKED" in str(error) else "FAIL"
        print(f"DEV BCD integration {status}: {error}", file=sys.stderr)
        raise SystemExit(BLOCKED_EXIT if status == "BLOCKED" else 1) from error
