"""Full 0..255 x 18-condition real-core sweep of EX_BCDBIN through the DEV ROM entry (slot $3D).

This is the in-repo driver for the full-domain evidence of Issue #442 (docs/evidence/dev-callable-3d.md).
It replaces the out-of-tree script that the original evidence document could not ship.

Run: make dev-bcdbin-sweep
  or: python3 tests/integration/dev_bcdbin_full_sweep.py [path-to-dev-rom.pce] [--jobs N]
The repository root is taken from this file's own location, so the script runs from any checkout
(it is not tied to a particular working directory).

Conditions (18 per input):
  - the caller's carry flag before the JSR: 0 (CLC) or 1 (SEC)
  - the caller's X/Y pair: ($5A,$A5), ($00,$FF) or ($FF,$00)
  - the caller's stack pointer: $FF, $80 or $C0
Every one of the 256 input values is executed under all 18 conditions: 256 x 18 = 4608 runs.

Checked per run: A and the output carry (P bit 0) against the contract, X and Y preserved, the return PC,
the stack pointer balanced (equal to the caller's SP), MPR1 = $F8 and MPR7 = 0.
N/Z/V are unspecified by the DEV contract (docs/ex-bcdbin.md) and are recorded but NOT checked. They are
recorded per phase ("nzv_unchecked_cold" and "nzv_unchecked_warm") because the probe's "warm" phase is
preceded by core.ResetMedia(true), which resets the CPU only when the synthetic CD media already reports
IsReady(). That readiness comes from an asynchronous TOC read, so it is host-timing dependent: the warm
phase either re-runs the guest (CPU reset) or merely continues (no reset). Every CHECKED item is the same
in both cases; only these uncontracted flag bits can differ between the phases and between two runs of the
sweep. The "cold" record is stable across runs.

The expectation is this project's own DEV-only contract, NOT the reference System Card ABI, which stays
UNVERIFIED. Missing tools exit 3 (BLOCKED).
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_rom import verify_dev_rom  # noqa: E402
import geargrafx_api as api  # noqa: E402
import geargrafx_dev_bcd as t  # noqa: E402

BLOCKED_EXIT = 3
STORE = 0x2200
SLOT_EX_BCDBIN = 0x3D
XY_PAIRS = ((0x5A, 0xA5), (0x00, 0xFF), (0xFF, 0x00))
SP_VALUES = (0xFF, 0x80, 0xC0)
CARRY_VALUES = (0, 1)


def guest(reset, entry, value, x, y, sp, carry):
    """Reset-replacement code (CPU address `reset`) with the caller's condition preset.

    SEI, CLD (D=0 is the routine's precondition), MPR1=work RAM, SP=sp, X/Y/A preset, the caller's carry
    flag, JSR entry, store A/P/X/Y at $2200-$2203, halt. Returns (code, halt CPU address)."""
    code = bytes([0x78, 0xD8,                       # SEI, CLD
                  0xA9, 0xF8, 0x53, 0x02,           # LDA #$F8; TAM #$02 -> MPR1 = work RAM ($2000-$3FFF)
                  0xA2, sp, 0x9A,                  # LDX #sp; TXS
                  0xA2, x, 0xA0, y,                # LDX #x; LDY #y
                  0xA9, value,                     # LDA #value
                  0x38 if carry else 0x18,         # SEC / CLC (the caller's carry before the call)
                  0x20, entry & 0xFF, entry >> 8,  # JSR entry
                  0x08,                            # PHP (flags after the call)
                  0x8D, STORE & 0xFF, STORE >> 8,  # STA $2200
                  0x68,                            # PLA
                  0x8D, (STORE + 1) & 0xFF, (STORE + 1) >> 8,  # STA $2201 (P)
                  0x8E, (STORE + 2) & 0xFF, (STORE + 2) >> 8,  # STX $2202
                  0x8C, (STORE + 3) & 0xFF, (STORE + 3) >> 8])  # STY $2203
    halt = reset + len(code)
    return code + bytes([0x80, 0xFE]), halt  # BRA -2


def check(label, rows, halt, expected_a, expected_c, expected_x, expected_y, sp):
    """Check the guest's result in both the cold and the warm phase (the probe runs both).

    Returns the N/V/T/Z bits of the stored P, per phase. They are RECORDED, NOT CHECKED: the DEV
    contract leaves N/Z/V undefined (docs/ex-bcdbin.md). The two phases are kept apart on purpose:
    the probe's "warm" phase is preceded by core.ResetMedia(true), which only resets the CPU when the
    CD media already reports IsReady(). Whether that happens depends on the asynchronous TOC read of
    the synthetic media, so the warm phase may either re-run the guest (the CPU was reset) or simply
    continue (the CPU was not reset). The checked items (A, C, X, Y, return PC, SP, MPR) are identical
    in both cases; only these uncontracted flag bits can differ between the two phases and between two
    runs of the sweep.
    """
    nzv = {}
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
        observed = (stored[0], stored[1] & 1, stored[2], stored[3])
        if observed != (expected_a, expected_c, expected_x, expected_y):
            raise ValueError(f"{label}: {phase} A/C/X/Y observed {observed}, expected "
                             f"{(expected_a, expected_c, expected_x, expected_y)}")
        nzv[phase] = stored[1] & 0xE2  # N/V/T/Z: recorded, not checked (unspecified by the DEV contract)
    return nzv


def cases():
    """All (input, carry, x, y, sp) conditions, in a deterministic order."""
    for value in range(256):
        for carry in CARRY_VALUES:
            for x, y in XY_PAIRS:
                for sp in SP_VALUES:
                    yield value, carry, x, y, sp


def run_case(data, reset, entry, cue, probe, case_rom, value, carry, x, y, sp):
    """Run one condition on the real core. Returns (result dict, error string)."""
    label = f"bcdbin-{value:02x}-c{carry}-x{x:02x}y{y:02x}-sp{sp:02x}"
    want_a, want_c = t.expected_bcdbin(value)
    try:
        code, halt = guest(reset, entry, value, x, y, sp, carry)
        variant = bytearray(data)
        variant[reset - 0xE000:reset - 0xE000 + len(code)] = code
        case_rom.write_bytes(variant)
        rows = t.run_probe(probe, case_rom.resolve(), cue)
        nzv = check(label, rows, halt, want_a, want_c, x, y, sp)
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        return None, f"{label}: {error}"
    return {"label": label, "input": value, "carry_in": carry, "x": x, "y": y, "sp": sp,
            "expected_a": want_a, "expected_c": want_c,
            "nzv_unchecked_cold": nzv["cold"], "nzv_unchecked_warm": nzv["warm"],
            "range": "valid" if want_c == 0 else "refusal"}, None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rom", nargs="?", type=Path,
                        default=ROOT / "build" / "debug" / "bios-dev" / "dev-only-not-compatible-syscard3.pce")
    parser.add_argument("--jobs", type=int, default=min(8, os.cpu_count() or 1),
                        help="parallel probe processes (each probe is its own process)")
    args = parser.parse_args()
    rom = args.rom.resolve()
    data = rom.read_bytes()
    verify_dev_rom(data, manifest=rom.parent / "manifest.json")
    reset = int.from_bytes(data[0x1FFE:0x2000], "little")
    probe = api.compile_probe(rom.parent)
    entry = t.entry_address(SLOT_EX_BCDBIN)
    results, failures = [], []
    with tempfile.TemporaryDirectory(prefix="pce-dev-bcdbin-full-") as tmp:
        folder = Path(tmp)
        (folder / "original.bin").write_bytes(bytes(2352 * 32))
        cue = folder / "original.cue"
        cue.write_text('FILE "original.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
        # one ROM file per worker thread: the pool hands cases to whichever thread is free, so a
        # case index cannot be used to pin a file to a thread (two threads would write the same
        # file and the probe could read a half-written image).
        local = threading.local()

        def worker(case):
            case_rom = getattr(local, "rom", None)
            if case_rom is None:
                case_rom = folder / f"case-{threading.get_ident()}.pce"
                local.rom = case_rom
            return run_case(data, reset, entry, cue, probe, case_rom, *case)

        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            for result, error in pool.map(worker, cases()):
                (failures if error else results).append(error or result)
    results.sort(key=lambda r: r["label"])
    failures.sort()
    summary = {
        "scope": "DEV ROM real-core full sweep, project-own EX_BCDBIN, NOT System Card ABI",
        "rom_sha256": hashlib.sha256(data).hexdigest(),
        "inputs_run": len(results) + len(failures), "domain": "0..255",
        "conditions_per_input": len(CARRY_VALUES) * len(XY_PAIRS) * len(SP_VALUES),
        "valid_runs": sum(r["range"] == "valid" for r in results),
        "refusal_runs": sum(r["range"] == "refusal" for r in results),
        "failures": len(failures), "failed_labels": failures,
        "checked_items": "A, output carry (P bit 0), X, Y, return PC, SP, MPR1, MPR7 (cold and warm phases)",
        "unchecked_recorded_items": "nzv_unchecked_cold / nzv_unchecked_warm: the N/V/T/Z bits of the stored P",
        "unchecked_items_note": "N/Z/V are undefined by the DEV contract and are NOT checked. The recorded bits are "
                                "not byte-stable between two runs of the sweep: the pinned core's asynchronous state "
                                "(synthetic CD media TOC readiness, and the CPU reset that core.ResetMedia(true) only "
                                "performs when the media reports IsReady()) decides whether the guest re-runs, and the "
                                "V bit follows that path. Every CHECKED item above is identical between runs.",
    }
    (rom.parent / "dev-bcdbin-full-sweep.json").write_text(
        json.dumps({"summary": summary, "results": results, "failures": failures}, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    print(f"{'PASS' if not failures else 'FAIL'} DEV ROM EX_BCDBIN $3D full domain: "
          f"{summary['inputs_run']} runs ({summary['conditions_per_input']} conditions x 256 inputs), "
          f"{len(failures)} failures")
    sys.exit(0 if not failures else 1)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        status = "BLOCKED" if "BLOCKED" in str(error) else "FAIL"
        print(f"DEV BCDBIN full sweep {status}: {error}", file=sys.stderr)
        raise SystemExit(BLOCKED_EXIT if status == "BLOCKED" else 1) from error
