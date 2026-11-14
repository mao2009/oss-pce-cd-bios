"""Real Geargrafx run of the DEV EX_SCRSIZ entry (slot $23, candidate $E069).

DEV_EXPERIMENTAL, project-own contract — NOT the reference System Card EX_SCRSIZ ABI.
Genuine ABI is UNKNOWN/UNVERIFIED. release_eligible: false.

DEV contract (real VDC I/O): programs VDC MWR (R09) low byte from A and high byte $00 via MPR0 I/O window (X preserved/unused).
Returns with A,X,Y,MPR0 preserved, stack balanced, VDC AR left at $09.
This proves real VDC register processing on pinned Geargrafx, not ABI compatibility.
"""
import argparse
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


def compile_probe(folder):
    return api.compile_probe(folder, source_name="geargrafx_dev_scrsiz_probe.cpp", target_name="geargrafx-dev-scrsiz-probe")


def run_case(probe, rom, cue, lo, hi, steps=2048):
    r = subprocess.run([str(probe), str(rom), str(cue), str(steps), str(lo), str(hi)],
                       capture_output=True, text=True, timeout=120, check=True)
    for line in r.stdout.splitlines():
        if line.startswith("RESULT "):
            return json.loads(line[7:])
    raise ValueError("no RESULT line\n" + r.stderr)


# MWR (R09) writable mask is 0x00FF on HuC6270; high byte is not stored.
CASES = [
    ("zero", 0x00, 0x5A),
    ("typical-32x32", 0x11, 0x00),
    ("typical-64x32", 0x31, 0xA5),
    ("pattern", 0x5A, 0x12),
    ("all-low-bits", 0xFF, 0x34),
    ("mixed-preserve-x", 0x34, 0x12),
]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rom", type=Path, required=True)
    args = ap.parse_args()
    data = args.rom.read_bytes()
    verify_dev_rom(data, manifest=args.rom.parent / "manifest.json")
    with tempfile.TemporaryDirectory(prefix="pce-dev-scrsiz-") as tmp:
        folder = Path(tmp)
        (folder / "d.bin").write_bytes(bytes(2352 * 32))
        cue = folder / "d.cue"
        cue.write_text('FILE "d.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
        probe = compile_probe(folder)
        failures = []
        for name, lo, hi in CASES:
            res = run_case(probe, args.rom.resolve(), cue.resolve(), lo, hi)
            want_r9 = lo & 0xFF  # MWR writable mask 0x00FF
            if res["pc"] != res["halt"]:
                failures.append(f"{name}: did not halt pc={res['pc']} halt={res['halt']}")
            if (res["vdc_r9"] & 0xFFFF) != want_r9:
                failures.append(f"{name}: VDC R9=0x{res['vdc_r9']:04X} want=0x{want_r9:04X}")
            if res["vdc_ar"] != 9:
                failures.append(f"{name}: VDC AR={res['vdc_ar']} want=9")
            if res["a"] != lo or res["x"] != hi or res["y"] != 0xA5:
                failures.append(f"{name}: A/X/Y not preserved got {res['a']},{res['x']},{res['y']}")
            if res["sp_stored"] != 0xFF or res["sp"] != 0xFF:
                failures.append(f"{name}: SP not balanced")
            # MPR0 initial is $F8 (not $FF) so a missing restore is detectable.
            if res["mpr"][1] != 0xF8 or res["mpr"][7] != 0 or res["mpr"][0] != 0xF8 or res["mpr0_stored"] != 0xF8:
                failures.append(f"{name}: MPR not preserved {res['mpr']} mpr0_stored={res['mpr0_stored']}")
        if failures:
            for f in failures: print("FAIL", f, file=sys.stderr)
            raise SystemExit(1)
        print(f"PASS EX_SCRSIZ DEV real-core: {len(CASES)} cases, real VDC MWR I/O verified (DEV_EXPERIMENTAL, not ABI)")


if __name__ == "__main__":
    main()
