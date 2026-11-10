"""Real Geargrafx run of the DEV EX_BGON entry (slot $2E, candidate $E08A, Issue #56).

Each run presets VDC CR to a case pattern, calls the slot once and checks the
project-own contract (src/overrides/slot_2E.s): CR low gains bit 7 (BB), every
other CR bit is unchanged (checked against the probe's independent vdc_cr
observation of GetState()->R[5], NOT only the guest port readback), A/X/Y/P
preserved (C=0), VDC AR left at 5 (CR select), stack balanced (SP=$FF and the
$07 boundary), MPR0/MPR1/MPR7 intact and the RAM sentinel untouched.
NOT the reference System Card EX_BGON. ABI stays UNVERIFIED.

Four mutants that must each be rejected on the real core: BB not set
(ORA #$80 -> ORA #$00), spurious SB (ORA #$80 -> ORA #$C0), other bits
destroyed (ORA #$80 -> ORA #$FF), CR high byte rewritten (STA $0002 -> STA $0003).
Run: python3 tests/integration/geargrafx_dev_bgon.py --rom build/<mode>/bios-dev/dev-only-not-compatible-syscard3.pce
Needs the pinned cc65 and Geargrafx core (TOOLS_DIR). Missing tools exit 3 (BLOCKED).
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
import geargrafx_dev_bcd as dev  # noqa: E402
import geargrafx_dev_cr as cr  # noqa: E402

BLOCKED_EXIT = 3
SLOT = 0x2E
SET_MASK = 0x80
CLEAR_MASK = 0x00
MUTANTS = [
    ("bb_not_set", bytes([0x09, 0x80]), bytes([0x09, 0x00])),   # ORA #$80 -> ORA #$00
    ("sb_spurious", bytes([0x09, 0x80]), bytes([0x09, 0xC0])),  # ORA #$80 -> ORA #$C0
    ("other_bits", bytes([0x09, 0x80]), bytes([0x09, 0xFF])),   # ORA #$80 -> ORA #$FF
    ("high_rewritten", bytes([0x8D, 0x02, 0x00]), bytes([0x8D, 0x03, 0x00])),  # STA $0002 -> STA $0003
]
MUTANT_CASE = (("ex-kept", 0x30, 0x00), (0x12, 0x34, 0x56, 0x34, 0xFF))
CASES = [(("lo-clear", 0x00, 0x00), (0x12, 0x34, 0x56, 0x34, 0xFF), True),
         (("sb-kept", 0x40, 0xA5), (0x12, 0x34, 0x56, 0x34, 0xFF), True),
         (("ex-kept", 0x30, 0x00), (0x12, 0x34, 0x56, 0x34, 0xFF), True),
         (("uninit", 0x00, 0x00), (0x12, 0x34, 0x56, 0x34, 0xFF), False),
         (("sp07", 0x40, 0xA5), (0xA5, 0x5A, 0x3C, 0xB4, 0x07), True)]


def run(probe, rom_bytes, folder, cue, reset, entry, label, pattern, regs, seed_shadow=True):
    """One real-core run for a (CR pattern, register preset) pair."""
    cr_low, cr_high = pattern
    a, x, y, p, sp = regs
    code, halt = cr.guest(reset, entry, cr_low, cr_high, a, x, y, p, sp, seed_shadow=seed_shadow)
    variant = bytearray(rom_bytes)
    variant[reset - 0xE000:reset - 0xE000 + len(code)] = code
    rom = folder / f"{label}.pce"
    rom.write_bytes(variant)
    rows = dev.run_probe(probe, rom.resolve(), cue, count=cr.RUN_STEPS)
    return cr.check(label, rows, halt, a, x, y, p, sp, cr_low, cr_high, SET_MASK, CLEAR_MASK,
                    seed_shadow=seed_shadow)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rom", type=Path, required=True)
    args = parser.parse_args()
    data = args.rom.read_bytes()
    verify_dev_rom(data, manifest=args.rom.parent / "manifest.json")
    reset = int.from_bytes(data[0x1FFE:0x2000], "little")
    entry = dev.entry_address(SLOT)
    table = 3 * SLOT
    body = data[table + 1] | data[table + 2] << 8
    if data[table] != 0x4C or not 0xE000 <= body < 0xFFF6:
        raise ValueError(f"slot ${SLOT:02X} table entry is not a JMP into the DEV ROM body")
    probe = api.compile_probe(args.rom.parent)
    from environment import DEPENDENCIES  # noqa: E402
    evidence = {"scope": "DEV ROM real-core run of project-own EX_BGON, NOT System Card ABI compatibility",
                "rom_sha256": hashlib.sha256(data).hexdigest(),
                "geargrafx_revision": DEPENDENCIES["geargrafx"]["revision"],
                "runs": [], "negative_cases": []}
    with tempfile.TemporaryDirectory(prefix="pce-dev-bgon-") as tmp:
        folder = Path(tmp)
        (folder / "original.bin").write_bytes(bytes(2352 * 32))
        cue = folder / "original.cue"
        cue.write_text('FILE "original.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
        failures = []
        for (name, cr_low, cr_high), regs, seeded in CASES:
            try:
                evidence["runs"].append(run(probe, data, folder, cue, reset, entry,
                                            f"bgon-{name}", (cr_low, cr_high), regs,
                                            seed_shadow=seeded))
            except ValueError as error:
                # RED record, not a crash: keep going so mutants are still exercised.
                failures.append({"case": name, "error": str(error)})
        evidence["positive_failures"] = failures
        code_at = body - 0xE000  # CPU $E000 is file offset 0 of bank 0 (as in the MA_SQRT harness)
        (mname, mlo, mhi), mregs = MUTANT_CASE
        for name, find, replace in MUTANTS:
            at = data.find(find, code_at, code_at + 0x60)
            if at < 0:
                raise ValueError(f"mutant {name}: pattern {find.hex()} not found in slot $2E body")
            mutant = bytearray(data)
            mutant[at:at + len(replace)] = replace
            try:
                run(probe, bytes(mutant), folder, cue, reset, entry, f"mut-{name}",
                    (mlo, mhi), mregs)
            except ValueError as error:
                if not any(key in str(error) for key in ("A/P/X/Y observed", "stack not balanced",
                                                         "RAM sentinel", "did not reach its halt loop",
                                                         "MPR changed", "MPR0 not restored", "CR $",
                                                         "VDC CR", "VDC AR", "shadow ")):
                    raise  # only a contract mismatch counts as a rejection (a crash is not a detection)
                evidence["negative_cases"].append({"case": name, "rejected_by": str(error)})
            else:
                raise ValueError(f"mutant {name} passed all checks (not detected)")
    (args.rom.parent / "geargrafx-dev-bgon-evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
    if failures:
        print(f"RED real Geargrafx DEV ROM: EX_BGON slot $2E: {len(failures)}/{len(CASES)} positive cases "
              f"failed the contract; {len(evidence['negative_cases'])}/{len(MUTANTS)} mutants rejected")
        raise SystemExit(1)
    print(f"PASS real Geargrafx DEV ROM: EX_BGON slot $2E: {len(CASES)} runs set BB, keep other CR bits, "
          f"preserve A/X/Y/P, balance SP=$FF/$07, keep MPRs and the sentinel; "
          f"{len(evidence['negative_cases'])}/{len(MUTANTS)} mutants rejected")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        status = "BLOCKED" if "BLOCKED" in str(error) else "FAIL"
        print(f"DEV EX_BGON integration {status}: {error}", file=sys.stderr)
        raise SystemExit(BLOCKED_EXIT if status == "BLOCKED" else 1) from error
