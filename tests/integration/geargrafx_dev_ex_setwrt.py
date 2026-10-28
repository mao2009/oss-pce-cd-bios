"""Real Geargrafx E2E test for the project-own EX_SETWRT slot $3A contract.

The guest calls the assembled DEV-ROM table entry with valid and rejected MAWR
addresses.  The debugger-backed probe observes A/X/Y/P/SP/MPR, work RAM, and
the actual HuC6270 MAWR register (R0).  This is real-core evidence only for
this project's DEV contract, not for the proprietary System Card ABI.

Run: python3 tests/integration/geargrafx_dev_ex_setwrt.py --rom build/debug/bios-dev/dev-only-not-compatible-syscard3.pce
Missing pinned tools are BLOCKED (exit 3); a missing observable is never PASS.
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
SLOT = 0x3A
STORE = 0x2200
P_INPUT = 0xCD  # N,V,D,I,C set; the valid return must preserve all but C.
P_MASK = 0xEF   # B is not a stored CPU status flag.


def guest(reset, entry, low, high, init):
    """Set RAM stack/MPR1, a non-zero initial VDC MAWR (init), exact input P, call entry, then dump A/P/X/Y."""
    code = bytes([
        0x78, 0xD8,                         # SEI; CLD
        0xA9, 0xF8, 0x53, 0x02,             # MPR1 = work RAM (stack $2100)
        0xA9, 0xFF, 0x53, 0x01,             # MPR0 = $FF: VDC ports at $0000-$0003
        0x9C, 0x00, 0x00,                   # ST0 = 0 (select MAWR)
        0xA9, init & 0xFF, 0x8D, 0x02, 0x00,        # ST1 = initial MAWR low
        0xA9, init >> 8, 0x8D, 0x03, 0x00,          # ST2 = initial MAWR high
        0xA9, 0x00, 0x53, 0x01,             # MPR0 = 0 again (guest default)
        0xA2, 0xFF, 0x9A,                   # SP = $FF
        0xA9, P_INPUT, 0x48,                # push requested caller status
        0xA2, high, 0xA0, 0x5A, 0xA9, low,  # X/Y/A inputs
        0x28,                               # PLP: restore exact caller P after loads
        0x20, entry & 0xFF, entry >> 8,     # JSR slot $3A table entry
        0x08, 0x8D, STORE & 0xFF, STORE >> 8,              # PHP; STA output A
        0x68, 0x8D, (STORE + 1) & 0xFF, (STORE + 1) >> 8,  # PLA; STA output P
        0x8E, (STORE + 2) & 0xFF, (STORE + 2) >> 8,        # STX output X
        0x8C, (STORE + 3) & 0xFF, (STORE + 3) >> 8,        # STY output Y
    ])
    halt = reset + len(code)
    return code + bytes([0x80, 0xFE]), halt


def run_probe(probe, rom, cue):
    result = subprocess.run([str(probe), str(rom), str(cue), "256"], check=True,
                            capture_output=True, text=True, timeout=120)
    rows = [json.loads(line[4:]) for line in result.stdout.splitlines() if line.startswith("OBS ")]
    caps = [json.loads(line[4:]) for line in result.stdout.splitlines() if line.startswith("CAP ")]
    if len(caps) != 1 or caps[0].get("instruction_step") is not True or caps[0].get("cdrom_media") is not True:
        raise ValueError("BLOCKED: instruction debugger/System Card capability missing")
    if [row.get("phase") for row in rows] != ["loaded", "cold", "nonreturning", "warm"]:
        raise ValueError("BLOCKED: missing real-core observations")
    return next(row for row in rows if row["phase"] == "cold")


def check(label, row, halt, low, high, init):
    required = ("a", "x", "y", "p", "sp", "mpr", "work_ram_hex", "vdc_ar", "vdc_r0")
    if any(key not in row for key in required):
        raise ValueError("BLOCKED: probe does not expose CPU/RAM/MPR/VDC state")
    if row["pc"] != halt:
        raise ValueError(f"{label}: guest did not reach halt (${row['pc']:04X}, want ${halt:04X})")
    if row["sp"] != 0xFF or row["mpr"][0] != 0 or row["mpr"][1] != 0xF8 or row["mpr"][7] != 0:
        raise ValueError(f"{label}: stack/MPR not restored (sp={row['sp']}, mpr={row['mpr']})")
    ram = bytes.fromhex(row["work_ram_hex"])
    observed = (ram[STORE - 0x2000], ram[STORE - 0x1FFF], ram[STORE - 0x1FFE], ram[STORE - 0x1FFD])
    # P is evaluated separately so its bit-level contract is clear in evidence.
    if observed[0] != low or observed[2:] != (high, 0x5A):
        raise ValueError(f"{label}: A/X/Y RAM dump {observed} does not preserve inputs")
    p = observed[1]
    if high < 0x80:
        # The expected MAWR must differ from the non-zero initial value, or a no-op would pass.
        if init == (high << 8) | low:
            raise ValueError(f"{label}: initial MAWR equals the expected value; the case cannot detect a no-op")
        if row["vdc_ar"] != 0 or row["vdc_r0"] != (high << 8) | low:
            raise ValueError(f"{label}: MAWR VDC R0={row['vdc_r0']:#06x}, AR={row['vdc_ar']:#x}")
        want_p = P_INPUT & ~1
        if (p & P_MASK) != want_p:
            raise ValueError(f"{label}: P=${p:02X}, want preserved N/V/D/I and C=0 (${want_p:02X})")
    else:
        # Reject must leave MAWR at the non-zero initial value (a reset-to-0 fake changes it).
        if row["vdc_ar"] != 0 or row["vdc_r0"] != init:
            raise ValueError(f"{label}: rejected address changed VDC (AR={row['vdc_ar']}, R0={row['vdc_r0']:#x}, "
                             f"want R0={init:#x})")
        if not p & 1:
            raise ValueError(f"{label}: rejected address did not return C=1 (P=${p:02X})")
    return {"input": f"{high:02X}{low:02X}", "a": observed[0], "x": observed[2], "y": observed[3],
            "p": p, "sp": row["sp"], "mpr": row["mpr"], "vdc_ar": row["vdc_ar"], "mawr": row["vdc_r0"]}


INITIAL_MAWR = (0x1357, 0x2468, 0x3579)  # non-zero, distinct from every expected MAWR used below


def one_case(probe, data, folder, cue, reset, entry, label, low, high, init):
    code, halt = guest(reset, entry, low, high, init)
    variant = bytearray(data)
    variant[reset - 0xE000:reset - 0xE000 + len(code)] = code
    rom = folder / f"{label}.pce"
    rom.write_bytes(variant)
    return check(label, run_probe(probe, rom, cue), halt, low, high, init)


def expect_rejected(probe, data, folder, cue, reset, entry, label, low, high, init, reason):
    """A mutant passes only if it is rejected for the expected reason (not for a halt or harness failure)."""
    try:
        one_case(probe, data, folder, cue, reset, entry, label, low, high, init)
    except ValueError as error:
        message = str(error)
        if "BLOCKED:" in message:
            raise
        if reason not in message:
            raise ValueError(f"mutant {label} rejected for the wrong reason: {message!r} (want {reason!r})")
        return message
    raise ValueError(f"mutant {label} unexpectedly passed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rom", type=Path, required=True)
    args = parser.parse_args()
    data = args.rom.read_bytes()
    verify_dev_rom(data, manifest=args.rom.parent / "manifest.json")
    reset = int.from_bytes(data[0x1FFE:0x2000], "little")
    entry = 0xE000 + 3 * SLOT
    if data[3 * SLOT] != 0x4C:
        raise ValueError("slot $3A is not a JMP table entry")
    probe = api.compile_probe(args.rom.parent)
    evidence = {"scope": "DEV ROM real-core EX_SETWRT, not System Card ABI compatibility",
                "rom_sha256": hashlib.sha256(data).hexdigest(), "cases": [], "negative_cases": []}
    with tempfile.TemporaryDirectory(prefix="pce-dev-setwrt-") as tmp:
        folder = Path(tmp)
        (folder / "original.bin").write_bytes(bytes(2352 * 32))
        cue = folder / "original.cue"
        cue.write_text('FILE "original.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
        cases = ((0x00, 0x00), (0x34, 0x12), (0xFF, 0x7F), (0x34, 0x80), (0xAB, 0xFF))
        for index, (low, high) in enumerate(cases):
            init = INITIAL_MAWR[index % len(INITIAL_MAWR)]
            evidence["cases"].append(one_case(probe, data, folder, cue, reset, entry,
                                                f"setwrt-{high:02x}{low:02x}", low, high, init))
        # Each mutant edits instructions inside the slot $3A body only. The real core must reject every one.
        body = data[3 * SLOT + 1] | data[3 * SLOT + 2] << 8
        window = body - 0xE000, body - 0xE000 + 0x60
        mutants = (
            ("low_write_nopped", [(bytes([0x8D, 0x02, 0x00]), bytes([0xEA] * 3))], 0x34, 0x12),
            ("high_write_nopped", [(bytes([0x8E, 0x03, 0x00]), bytes([0xEA] * 3))], 0x34, 0x12),
            ("all_writes_nopped", [(bytes([0x8D, 0x02, 0x00]), bytes([0xEA] * 3)),
                                   (bytes([0x8E, 0x03, 0x00]), bytes([0xEA] * 3))], 0x34, 0x12),
            ("lo_hi_swapped", [(bytes([0x8D, 0x02, 0x00]), bytes([0x8D, 0x03, 0x00])),
                               (bytes([0x8E, 0x03, 0x00]), bytes([0x8E, 0x02, 0x00]))], 0x34, 0x12),
            ("reset_to_zero_fake", [(bytes([0x8D, 0x02, 0x00]), bytes([0x9C, 0x02, 0x00])),
                                    (bytes([0x8E, 0x03, 0x00]), bytes([0x9C, 0x03, 0x00]))], 0x34, 0x12),
            ("wrong_register_selected", [(bytes([0x8D, 0x02, 0x00]), bytes([0x8D, 0x00, 0x00]))], 0x34, 0x12),
            ("reject_branch_inverted", [(bytes([0xB0, 0x22]), bytes([0x90, 0x22]))], 0x34, 0x80),
        )
        for name, edits, low, high in mutants:
            mutant = bytearray(data)
            for pattern, patch in edits:
                at = data.find(pattern, *window)
                if at < 0 or data.find(pattern, at + 1, window[1]) >= 0:
                    raise ValueError(f"mutant {name}: pattern {pattern.hex()} not unique in the slot $3A body")
                mutant[at:at + len(patch)] = patch
            reason = "rejected address changed VDC" if high >= 0x80 else "MAWR VDC R0="
            evidence["negative_cases"].append({"case": name, "rejected_by": expect_rejected(
                probe, bytes(mutant), folder, cue, reset, entry, f"mut-{name}", low, high, INITIAL_MAWR[0],
                reason)})
    (args.rom.parent / "geargrafx-dev-ex-setwrt-evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print(f"PASS real Geargrafx DEV ROM: EX_SETWRT $3A {len(evidence['cases'])} valid/rejected MAWR cases with "
          f"non-zero initial MAWR; A/X/Y/P, SP, MPR, RAM and HuC6270 MAWR observed; "
          f"{len(evidence['negative_cases'])} mutants rejected")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        status = "BLOCKED" if "BLOCKED:" in str(error) else "FAIL"
        print(f"DEV EX_SETWRT integration {status}: {error}", file=sys.stderr)
        raise SystemExit(BLOCKED_EXIT if status == "BLOCKED" else 1) from error
