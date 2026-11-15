"""Real Geargrafx sequential calls EX_JOYSNS slot-21 + EX_SCRSIZ slot-23 on one DEV ROM.

DEV_EXPERIMENTAL, project-own contracts - NOT reference System Card ABI.
Genuine ABI UNKNOWN/UNVERIFIED. release_eligible: false.

Proves on the SAME DEV ROM via the real API table (E063/E069):
- both overrides present, callable in either order (21->23 and 23->21)
- MPR0 restored to F8 after each call (no reseed between calls)
- X/Y/SP preserved across both calls
- JOYSNS real I/O result correct regardless of order
- SCRSIZ real VDC MWR write correct regardless of order
- no cross-API state carry that breaks either contract
Synthetic only. Pinned cc65/Geargrafx.
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

GG_KEY_I, GG_KEY_II, GG_KEY_SELECT, GG_KEY_RUN = 0x01, 0x02, 0x04, 0x08
GG_KEY_UP, GG_KEY_RIGHT, GG_KEY_DOWN, GG_KEY_LEFT = 0x10, 0x20, 0x40, 0x80

def compile_probe(folder):
    return api.compile_probe(folder, source_name="geargrafx_dev_2123_probe.cpp", target_name="geargrafx-dev-2123-probe")

def run_case(probe, rom, cue, keymask, lo, order, steps=4096):
    r = subprocess.run([str(probe), str(rom), str(cue), str(steps), str(keymask), str(lo), str(order)],
                       capture_output=True, text=True, timeout=120, check=True)
    for line in r.stdout.splitlines():
        if line.startswith("RESULT "):
            return json.loads(line[7:])
    raise ValueError("no RESULT line: " + r.stderr[-2000:])

def expected_a(keymask):
    dir_bits = 0x0F
    if keymask & GG_KEY_UP: dir_bits &= ~0x01
    if keymask & GG_KEY_RIGHT: dir_bits &= ~0x02
    if keymask & GG_KEY_DOWN: dir_bits &= ~0x04
    if keymask & GG_KEY_LEFT: dir_bits &= ~0x08
    btn_bits = 0x0F
    if keymask & GG_KEY_I: btn_bits &= ~0x01
    if keymask & GG_KEY_II: btn_bits &= ~0x02
    if keymask & GG_KEY_SELECT: btn_bits &= ~0x04
    if keymask & GG_KEY_RUN: btn_bits &= ~0x08
    return (dir_bits << 4) | btn_bits

CASES = [
    ("no-input-11", 0, 0x11),
    ("run-31", GG_KEY_RUN, 0x31),
    ("up-left-run-5a", GG_KEY_UP | GG_KEY_LEFT | GG_KEY_RUN, 0x5A),
    ("all-dirs-ff", GG_KEY_UP | GG_KEY_RIGHT | GG_KEY_DOWN | GG_KEY_LEFT, 0xFF),
]

def check_one(res, keymask, lo, order, label):
    errs = []
    want_joy = expected_a(keymask)
    joy_a = res["a1"] if order == 0 else res["a2"]
    scr_a = res["a2"] if order == 0 else res["a1"]
    pc = res["pc"]
    halt = res["halt"]
    if pc != halt:
        errs.append(label + ": no halt pc=%d halt=%d" % (pc, halt))
    if joy_a != want_joy:
        errs.append(label + ": JOY A=0x%02X want=0x%02X mask=0x%X" % (joy_a, want_joy, keymask))
    if scr_a != (lo & 0xFF):
        errs.append(label + ": SCR A=0x%02X want=0x%02X preserved" % (scr_a, lo & 0xFF))
    vdc_r9 = res["vdc_r9"]
    vdc_ar = res["vdc_ar"]
    if (vdc_r9 & 0xFFFF) != (lo & 0xFF):
        errs.append(label + ": VDC R9=0x%04X want=0x%04X" % (vdc_r9 & 0xFFFF, lo & 0xFF))
    if vdc_ar != 9:
        errs.append(label + ": VDC AR=%d want=9" % vdc_ar)
    xv = res["x"]
    yv = res["y"]
    if xv != 0x5A or yv != 0xA5:
        errs.append(label + ": X/Y not preserved %d/%d" % (xv, yv))
    for k in ("sp1", "sp2", "sp_f", "sp"):
        if res[k] != 0xFF:
            errs.append(label + ": %s=%d want=255" % (k, res[k]))
    for k in ("mpr1", "mpr2", "mpr_f"):
        if res[k] != 0xF8:
            errs.append(label + ": %s=0x%02X want=0xF8" % (k, res[k]))
    mpr = res["mpr"]
    if mpr[0] != 0xF8 or mpr[1] != 0xF8:
        errs.append(label + ": MPR=%s want F8/F8" % (mpr,))
    return errs

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rom", type=Path, required=True)
    args = ap.parse_args()
    data = args.rom.read_bytes()
    verify_dev_rom(data, manifest=args.rom.parent / "manifest.json")
    with tempfile.TemporaryDirectory(prefix="pce-dev-2123-") as tmp:
        folder = Path(tmp)
        (folder / "d.bin").write_bytes(bytes(2352 * 32))
        cue = folder / "d.cue"
        cue.write_text("FILE \"d.bin\" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n")
        probe = compile_probe(folder)
        failures = []
        for name, mask, lo in CASES:
            for order in (0, 1):
                label = "%s-o%d" % (name, order)
                try:
                    res = run_case(probe, args.rom.resolve(), cue.resolve(), mask, lo, order)
                except Exception as e:
                    failures.append(label + ": probe error %s" % e)
                    continue
                failures.extend(check_one(res, mask, lo, order, label))
        if failures:
            for f in failures:
                print("FAIL " + f, file=sys.stderr)
            raise SystemExit(1)
        print("PASS EX_JOYSNS+EX_SCRSIZ DEV sequential: %d cases x 2 orders, same-ROM table calls, MPR0/X/Y/SP preserved, real I/O + VDC verified (DEV_EXPERIMENTAL, not ABI)" % len(CASES))

if __name__ == "__main__":
    main()
