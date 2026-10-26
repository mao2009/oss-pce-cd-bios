"""Real Geargrafx run of MA_COS (slot $46, Issue #80) through the DEV ROM entry $E0D2.

A guest program replaces the reset code of the built DEV ROM. It sets MPR1 to work RAM,
SP=$FF (or $06 for the boundary), sets D=1 and C=1 on purpose (the routine must still compute in
binary and restore the caller's flags), preloads X=$5A and Y=$A5, JSRs $E0D2 with A = angle index,
then stores A (result low), X (result high), P, Y and SP at $2200-$2205 and halts.
The expectation is this project's own spec (src/overrides/slot_46.s): Q15 cosine,
X:A = round(32767 * cos(2*pi*A/256)). It is NOT the reference System Card MA_COS ABI (UNVERIFIED).
Every angle index 0..255 is valid. The 256-angle check is the Python model
(tests/host/test_ma_cos_dev.py); this run covers a representative subset on the real core.
Run: python3 tests/integration/geargrafx_dev_cos.py --rom build/debug/bios-dev/dev-only-not-compatible-syscard3.pce
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
SLOT_MA_COS = 0x46
STORE = 0x2200  # $2200 A(result lo), $2201 X(result hi), $2202 P after, $2203 Y, $2205 SP after
# Angle indices covering each quadrant, quadrant boundaries and the wrap point.
CASES = [0, 1, 31, 32, 33, 63, 64, 65, 96, 127, 128, 129, 160, 191, 192, 193, 224, 254, 255]
Y_PRESET = 0xA5
X_PRESET = 0x5A


def q15(k):
    return math.floor(32767 * math.cos(2 * math.pi * k / 256) + 0.5)


def guest(reset, entry, a, sp=0xFF):
    """Reset-replacement code at CPU address `reset`. Returns (code, halt CPU address)."""
    code = bytes([0x78, 0xF8, 0x38,           # SEI; SED (caller D=1); SEC (caller C=1)
                  0xA9, 0xF8, 0x53, 0x02,     # LDA #$F8; TAM #$02 -> MPR1 = work RAM ($2000-$3FFF)
                  0xA2, sp, 0x9A,             # LDX #sp; TXS (caller SP; JSR then pushes 2 bytes)
                  0xA2, X_PRESET,             # LDX #$5A (input X is ignored by MA_COS)
                  0xA0, Y_PRESET,             # LDY #$A5
                  0xA9, a,                    # LDA #a (angle index)
                  0x20, entry & 0xFF, entry >> 8,  # JSR entry
                  0x8D, STORE & 0xFF, STORE >> 8,              # STA $2200 (result lo)
                  0x8E, (STORE + 1) & 0xFF, (STORE + 1) >> 8,  # STX $2201 (result hi)
                  0x08, 0x68, 0x8D, (STORE + 2) & 0xFF, (STORE + 2) >> 8,  # PHP; PLA; STA $2202
                  0x8C, (STORE + 3) & 0xFF, (STORE + 3) >> 8,  # STY $2203
                  0xBA, 0x8E, (STORE + 5) & 0xFF, (STORE + 5) >> 8])  # TSX; STX $2205 (SP after)
    halt = reset + len(code)
    return code + bytes([0x80, 0xFE]), halt  # BRA -2


def check(label, rows, halt, a, sp=0xFF):
    cold = next(r for r in rows if r["phase"] == "cold")
    if cold["pc"] != halt:
        raise ValueError(f"{label}: guest did not reach its halt loop (pc ${cold['pc']:04X}, want ${halt:04X})")
    if cold["sp"] != sp:
        raise ValueError(f"{label}: stack not balanced (SP ${cold['sp']:02X}, want ${sp:02X})")
    if cold["mpr"][1] != 0xF8 or cold["mpr"][7] != 0:
        raise ValueError(f"{label}: MPR changed (mpr={cold['mpr']})")
    s = bytes.fromhex(cold["work_ram_0200_hex"])[:6]
    lo, hi, _, y, _, sp_after = s[0], s[1], s[2], s[3], s[4], s[5]
    want = q15(a) & 0xFFFF
    observed = (lo | (hi << 8), y, sp_after)
    if observed != (want, Y_PRESET, sp):
        raise ValueError(f"{label}: angle ${a:02X}: X:A/Y/SP observed {observed}, "
                         f"expected {(want, Y_PRESET, sp)}")


def run_case(probe, base, folder, cue, reset, label, entry, a, sp=0xFF, count=2048):
    code, halt = guest(reset, entry, a, sp)
    variant = bytearray(base)
    variant[reset - 0xE000:reset - 0xE000 + len(code)] = code
    rom = folder / f"{label}.pce"
    rom.write_bytes(variant)
    rows = dev.run_probe(probe, rom.resolve(), cue, count=count)
    check(label, rows, halt, a, sp)


def main():
    parser = __import__("argparse").ArgumentParser(description=__doc__)
    parser.add_argument("--rom", type=Path, required=True)
    parser.add_argument("--full", action="store_true", help="all 256 angle indices at SP=$FF (manual sweep)")
    args = parser.parse_args()
    data = args.rom.read_bytes()
    verify_dev_rom(data, manifest=args.rom.parent / "manifest.json")
    reset = int.from_bytes(data[0x1FFE:0x2000], "little")
    entry = dev.entry_address(SLOT_MA_COS)
    table = 3 * SLOT_MA_COS  # the DEV API table is 3-byte JMPs from $E000; $E0D2 is this JMP
    body = data[table + 1] | data[table + 2] << 8
    if data[table] != 0x4C or not 0xE000 <= body < 0xFFF6:
        raise ValueError("slot $46 table entry is not a JMP into the DEV ROM body")
    probe = api.compile_probe(args.rom.parent)
    evidence = {"scope": "DEV ROM real-core run of project-own MA_COS, NOT System Card ABI compatibility",
                "contract": "A = angle index (256 per turn); X:A = round(32767*cos(2*pi*A/256)) signed Q15",
                "precondition": "caller SP >= $06 (entry S >= $04)",
                "rom_sha256": hashlib.sha256(data).hexdigest(), "geargrafx_revision": None,
                "cases": []}
    from environment import DEPENDENCIES  # noqa: E402
    evidence["geargrafx_revision"] = DEPENDENCIES["geargrafx"]["revision"]
    with tempfile.TemporaryDirectory(prefix="pce-dev-cos-") as tmp:
        folder = Path(tmp)
        (folder / "original.bin").write_bytes(bytes(2352 * 32))
        cue = folder / "original.cue"
        cue.write_text('FILE "original.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
        angles = list(range(256)) if args.full else CASES
        for a in angles:
            run_case(probe, data, folder, cue, reset, f"cos-{a:02x}", entry, a)
            evidence["cases"].append({"angle": a, "expected_q15": q15(a), "sp": 0xFF})
        # Precondition boundary: caller SP=$06 gives entry S=$04.
        for a in (0, 64, 255):
            run_case(probe, data, folder, cue, reset, f"lowsp-{a:02x}", entry, a, sp=0x06)
            evidence["cases"].append({"angle": a, "expected_q15": q15(a), "sp": 0x06})
    (args.rom.parent / "geargrafx-dev-cos-evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
    total = len(angles) + 3
    print(f"PASS real Geargrafx DEV ROM: MA_COS slot $46 {total} cases ({len(angles)} at SP=$FF, "
          f"3 at SP=$06 boundary); X:A=Q15 cosine of A, Y and SP checked; MPR1/MPR7 checked")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        status = "BLOCKED" if "BLOCKED" in str(error) else "FAIL"
        print(f"DEV COS integration {status}: {error}", file=sys.stderr)
        raise SystemExit(BLOCKED_EXIT if status == "BLOCKED" else 1) from error
