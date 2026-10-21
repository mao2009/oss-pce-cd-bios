"""Multi-input real-core check of the DEV ROM EX_GETVER entry (slot $1E, Issue #433).

The $1E contract evidence so far used a single caller vector (A=$11). This driver calls the DEV ROM
entry $E05A with several caller A values, both carry values, several caller X/Y presets and several
stack pointers, and checks the project's own DEV contract on every run:

  - A preserved (the callee only writes X and Y)
  - X = 3 (major version) and Y = 0 (minor version), whatever the caller's X/Y were
  - P: Z=1, N=0, I=1 (the caller's SEI), D=0 (the caller's CLD), C unchanged across the call
    (the callee's LDX/LDY touch neither C nor D; V and T are recorded, not asserted)
  - SP balanced, MPR1 = $F8 and MPR7 = 0 unchanged, return PC = the guest's halt loop

Run: python3 tests/integration/dev_getver_multi_input.py --rom build/debug/bios-dev/dev-only-not-compatible-syscard3.pce
      (or through `make dev-integration`, which CI runs)
The expectation is this project's own DEV-only contract, NOT the reference System Card ABI, which stays
UNVERIFIED. Missing tools exit 3 (BLOCKED).
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
import geargrafx_dev_bcd as t  # noqa: E402

BLOCKED_EXIT = 3
STORE = 0x2200
SLOT_EX_GETVER = 0x1E
A_VALUES = (0x00, 0x01, 0x11, 0x42, 0x7F, 0x80, 0xAA, 0xFF)
XY_PRESETS = ((0x22, 0x33), (0x00, 0xFF))
SP_VALUES = (0xFF, 0x80, 0xC0)
CARRY_VALUES = (0, 1)
EXPECTED_X = 3  # major version
EXPECTED_Y = 0  # minor version
# asserted P bits: C(0), Z(1), I(2), D(3), N(7). B(4) is always 1 in a PHP image and T(5) is a core
# constant 0; V(6) keeps the caller's PRNG-seeded value and is recorded only.
P_MASK = 0x8F
P_EXPECTED = 0x06  # Z=1, I=1, D=0, N=0; C is added per case


def guest(reset, entry, value, x, y, sp, carry):
    """Reset-replacement code (CPU address `reset`) that calls the $1E entry once.

    SEI, CLD (I=1, D=0 for the caller's own P), MPR1=work RAM, SP=sp, X/Y/A preset, the caller's carry,
    JSR entry, store A/P/X/Y at $2200-$2203, halt. Returns (code, halt CPU address)."""
    code = bytes([0x78, 0xD8,                       # SEI, CLD
                  0xA9, 0xF8, 0x53, 0x02,           # LDA #$F8; TAM #$02 -> MPR1 = work RAM ($2000-$3FFF)
                  0xA2, sp, 0x9A,                  # LDX #sp; TXS
                  0xA2, x, 0xA0, y,                # LDX #x; LDY #y
                  0xA9, value,                     # LDA #value (the caller accumulator)
                  0x38 if carry else 0x18,         # SEC / CLC (the caller's carry before the call)
                  0x20, entry & 0xFF, entry >> 8,  # JSR entry
                  0x08,                            # PHP (flags after the call)
                  0x8D, STORE & 0xFF, STORE >> 8,  # STA $2200 (A)
                  0x68,                            # PLA
                  0x8D, (STORE + 1) & 0xFF, (STORE + 1) >> 8,  # STA $2201 (P)
                  0x8E, (STORE + 2) & 0xFF, (STORE + 2) >> 8,  # STX $2202
                  0x8C, (STORE + 3) & 0xFF, (STORE + 3) >> 8])  # STY $2203
    halt = reset + len(code)
    return code + bytes([0x80, 0xFE]), halt  # BRA -2


def check(label, rows, halt, expected_a, carry, sp):
    """Check the guest's observation in the cold and the warm phase. Returns the recorded P bits."""
    for phase in ("cold", "warm"):
        row = next((r for r in rows if r["phase"] == phase), None)
        if row is None:
            raise ValueError(f"{label}: no {phase} observation")
        if row["pc"] != halt:
            raise ValueError(f"{label}: {phase} guest did not reach its halt loop "
                             f"(pc ${row['pc']:04X}, want ${halt:04X})")
        if row["sp"] != sp:
            raise ValueError(f"{label}: {phase} stack not balanced (SP ${row['sp']:02X}, want ${sp:02X})")
        if row["mpr"][1] != 0xF8 or row["mpr"][7] != 0:
            raise ValueError(f"{label}: {phase} MPR changed (mpr={row['mpr']})")
        stored = bytes.fromhex(row["work_ram_0200_hex"])[:4]
        observed_a, p, observed_x, observed_y = stored
        if observed_a != expected_a:
            raise ValueError(f"{label}: {phase} A observed ${observed_a:02X}, expected ${expected_a:02X} "
                             "(the callee must preserve the caller's accumulator)")
        if (observed_x, observed_y) != (EXPECTED_X, EXPECTED_Y):
            raise ValueError(f"{label}: {phase} X/Y observed (${observed_x:02X}, ${observed_y:02X}), expected "
                             f"(${EXPECTED_X:02X}, ${EXPECTED_Y:02X})")
        if p & P_MASK != P_EXPECTED | carry:
            raise ValueError(f"{label}: {phase} P observed ${p:02X}, expected ${P_EXPECTED | carry:02X} "
                             f"on the asserted bits (C/Z/I/D/N)")
    return stored[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rom", type=Path, required=True)
    args = parser.parse_args()
    data = args.rom.read_bytes()
    verify_dev_rom(data, manifest=args.rom.parent / "manifest.json")
    reset = int.from_bytes(data[0x1FFE:0x2000], "little")
    probe = api.compile_probe(args.rom.parent)
    entry = t.entry_address(SLOT_EX_GETVER)
    # the DEV ROM table entry for slot $1E must point at the override, not at a stub
    table_target = int.from_bytes(data[SLOT_EX_GETVER * 3 + 1:SLOT_EX_GETVER * 3 + 3], "little")
    if table_target < 0xE000:
        raise ValueError(f"slot $1E table entry ${table_target:04X} is not in the DEV ROM window")
    cases, failures = [], []
    with tempfile.TemporaryDirectory(prefix="pce-dev-getver-") as tmp:
        folder = Path(tmp)
        (folder / "original.bin").write_bytes(bytes(2352 * 32))
        cue = folder / "original.cue"
        cue.write_text('FILE "original.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
        for value in A_VALUES:
            for carry in CARRY_VALUES:
                for x, y in XY_PRESETS:
                    for sp in SP_VALUES:
                        label = f"getver-a{value:02x}-c{carry}-x{x:02x}y{y:02x}-sp{sp:02x}"
                        try:
                            code, halt = guest(reset, entry, value, x, y, sp, carry)
                            variant = bytearray(data)
                            variant[reset - 0xE000:reset - 0xE000 + len(code)] = code
                            case_rom = folder / f"{label}.pce"
                            case_rom.write_bytes(variant)
                            rows = t.run_probe(probe, case_rom.resolve(), cue)
                            p = check(label, rows, halt, value, carry, sp)
                        except (ValueError, OSError, subprocess.SubprocessError) as error:
                            failures.append(f"{label}: {error}")
                            continue
                        cases.append({"label": label, "a_in": value, "carry_in": carry, "x_in": x, "y_in": y,
                                      "sp": sp, "p_recorded": p, "a_out": value, "x_out": EXPECTED_X,
                                      "y_out": EXPECTED_Y})
    evidence = {"scope": "DEV ROM real-core multi-input run of project-own EX_GETVER ($1E), NOT System Card ABI",
                "rom_sha256": hashlib.sha256(data).hexdigest(),
                "entry": f"${entry:04X}", "table_target": f"${table_target:04X}",
                "conditions": {"a_values": len(A_VALUES), "carry": len(CARRY_VALUES), "xy": len(XY_PRESETS),
                               "sp": len(SP_VALUES)},
                "cases": cases, "failures": failures}
    (args.rom.parent / "geargrafx-dev-getver-evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
    if failures:
        for failure in failures:
            print(f"FAIL {failure}", file=sys.stderr)
        print(f"FAIL DEV ROM EX_GETVER $1E multi-input: {len(cases)} passed, {len(failures)} failed")
        raise SystemExit(1)
    print(f"PASS real Geargrafx DEV ROM: EX_GETVER slot $1E entry ${entry:04X} "
          f"{len(cases)} cases ({len(A_VALUES)} A values x {len(CARRY_VALUES)} carry x {len(XY_PRESETS)} X/Y "
          f"x {len(SP_VALUES)} SP; cold and warm); A preserved, X=3, Y=0, P (C/Z/I/D/N), SP, MPR1/MPR7 checked")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        status = "BLOCKED" if "BLOCKED" in str(error) else "FAIL"
        print(f"DEV GETVER multi-input {status}: {error}", file=sys.stderr)
        raise SystemExit(BLOCKED_EXIT if status == "BLOCKED" else 1) from error
