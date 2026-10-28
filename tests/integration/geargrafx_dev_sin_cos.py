"""Real-core MA_SIN/MA_COS mutual check, run by ``make dev-integration`` (CI job api-source).

Needs a ROM with both project-owned overrides (slot $45 MA_SIN, slot $46 MA_COS); a ROM missing
either slot FAILS (exit 1). It checks SIN(a + 64) == COS(a) on the pinned Geargrafx core for 22
representative/SP-boundary angles (19 with SP=$FF, 3 with SP=$06), against an independent math
oracle (math.cos, not the table). It does not establish System Card ABI compatibility.
"""
import argparse
import math
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_rom import verify_dev_rom  # noqa: E402
import geargrafx_api as api  # noqa: E402
import geargrafx_dev_bcd as dev  # noqa: E402

BLOCKED_EXIT = 3
SIN_SLOT, COS_SLOT = 0x45, 0x46
CASES = [0, 1, 31, 32, 33, 63, 64, 65, 96, 127, 128, 129, 160, 191, 192, 193, 224, 254, 255]
LOW_SP_CASES = [0, 64, 255]
OUT = 0x2200


def q15_cos(a):
    return math.floor(32767 * math.cos(2 * math.pi * a / 256) + 0.5) & 0xFFFF


def guest(reset, sin_entry, cos_entry, angle, sp):
    """Call SIN(angle+64), then COS(angle), and save both X:A words and final SP."""
    sin_angle = (angle + 64) & 0xFF
    code = bytes([
        0x78, 0xF8, 0x38, 0xB8,                    # SEI; SED; SEC; CLV
        0xA9, 0xF8, 0x53, 0x02,                    # MPR1 = work RAM
        0xA2, sp, 0x9A,                            # caller SP
        0xA9, sin_angle, 0x20, sin_entry & 0xFF, sin_entry >> 8,
        0x8D, OUT & 0xFF, OUT >> 8, 0x8E, (OUT + 1) & 0xFF, (OUT + 1) >> 8,      # SIN A, X
        0xA9, angle, 0x20, cos_entry & 0xFF, cos_entry >> 8,
        0x8D, (OUT + 2) & 0xFF, (OUT + 2) >> 8, 0x8E, (OUT + 3) & 0xFF, (OUT + 3) >> 8,  # COS A, X
        0xBA, 0x8E, (OUT + 4) & 0xFF, (OUT + 4) >> 8,                 # final SP
    ])
    halt = reset + len(code)
    return code + bytes([0x80, 0xFE]), halt


def run_case(probe, data, folder, cue, reset, sin_entry, cos_entry, angle, sp):
    code, halt = guest(reset, sin_entry, cos_entry, angle, sp)
    image = bytearray(data)
    image[reset - 0xE000:reset - 0xE000 + len(code)] = code
    rom = folder / f"sin-cos-{angle:02x}-{sp:02x}.pce"
    rom.write_bytes(image)
    cold = next((row for row in dev.run_probe(probe, rom.resolve(), cue, count=2048)
                 if row["phase"] == "cold"), None)
    if cold is None:
        raise ValueError(f"angle ${angle:02X}: no cold-phase row from the probe")
    if cold["pc"] != halt or cold["sp"] != sp:
        raise ValueError(f"angle ${angle:02X}: guest/stack failure")
    if cold["mpr"][1] != 0xF8 or cold["mpr"][7] != 0:
        raise ValueError(f"angle ${angle:02X}: MPR changed")
    wram = bytes.fromhex(cold["work_ram_0200_hex"])
    sine = wram[0] | wram[1] << 8
    cosine = wram[2] | wram[3] << 8
    if wram[4] != sp or sine != cosine or cosine != q15_cos(angle):
        raise ValueError(f"angle ${angle:02X}: SIN=${sine:04X}, COS=${cosine:04X}, "
                         f"want=${q15_cos(angle):04X}, SP=${wram[4]:02X}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rom", type=Path, required=True)
    args = parser.parse_args()
    data = args.rom.read_bytes()
    verify_dev_rom(data, manifest=args.rom.parent / "manifest.json")
    entries = []
    for slot in (SIN_SLOT, COS_SLOT):
        table = slot * 3
        target = data[table + 1] | data[table + 2] << 8
        if data[table] != 0x4C or not 0xE000 <= target < 0xFFF6:
            raise ValueError(f"required MA_{'SIN' if slot == SIN_SLOT else 'COS'} slot ${slot:02X} is not an override")
        entries.append(target)
    reset = int.from_bytes(data[0x1FFE:0x2000], "little")
    probe = api.compile_probe(args.rom.parent)
    with tempfile.TemporaryDirectory(prefix="pce-dev-sin-cos-") as tmp:
        folder = Path(tmp)
        (folder / "original.bin").write_bytes(bytes(2352 * 32))
        cue = folder / "original.cue"
        cue.write_text('FILE "original.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
        for angle in CASES:
            run_case(probe, data, folder, cue, reset, *entries, angle, 0xFF)
        for angle in LOW_SP_CASES:
            run_case(probe, data, folder, cue, reset, *entries, angle, 0x06)
    print("PASS real Geargrafx DEV ROM: MA_SIN/MA_COS shift identity, 22 vectors (19 SP=$FF, 3 SP=$06)")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, RuntimeError) as error:
        print(f"DEV SIN/COS integration {'BLOCKED' if 'BLOCKED' in str(error) else 'FAIL'}: {error}", file=sys.stderr)
        raise SystemExit(BLOCKED_EXIT if "BLOCKED" in str(error) else 1) from error
