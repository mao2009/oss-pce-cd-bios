"""Real Geargrafx run of the DEV EX_JOYSNS entry (slot $21, candidate $E063).

DEV_EXPERIMENTAL, project-own contract — NOT the reference System Card EX_JOYSNS ABI.
Genuine ABI (inputs/outputs, work-RAM, multi-pad, trigger/delta state, timing) is
UNKNOWN/UNVERIFIED. release_eligible: false.

DEV contract (real I/O): reads pad 1 via $1000 (SEL=1 directions, SEL=0 buttons)
and returns A = (dir_nibble<<4) | (btn_nibble & $0F) with active-low bits as read.
X,Y preserved; MPR0 preserved; no work-RAM writes. This test proves the routine
performs real joypad port I/O on the pinned Geargrafx core under controlled input,
not reference-ABI compatibility.
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
    return api.compile_probe(folder, source_name="geargrafx_dev_joysns_probe.cpp", target_name="geargrafx-dev-joysns-probe")


def run_case(probe, rom, cue, keymask, steps=2048):
    r = subprocess.run([str(probe), str(rom), str(cue), str(steps), str(keymask)],
                       capture_output=True, text=True, timeout=120, check=True)
    for line in r.stdout.splitlines():
        if line.startswith("RESULT "):
            return json.loads(line[7:])
    raise ValueError("no RESULT line")


def expected_a(keymask):
    # active-low: 0 bit = pressed. SEL=1 nibble: Up,Right,Down,Left -> bits 0-3
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
    ("no-input", 0),
    ("run", GG_KEY_RUN),
    ("i", GG_KEY_I),
    ("ii", GG_KEY_II),
    ("select", GG_KEY_SELECT),
    ("up", GG_KEY_UP),
    ("down", GG_KEY_DOWN),
    ("left", GG_KEY_LEFT),
    ("right", GG_KEY_RIGHT),
    ("run+i", GG_KEY_RUN | GG_KEY_I),
    ("up+left+run", GG_KEY_UP | GG_KEY_LEFT | GG_KEY_RUN),
    ("all-dirs", GG_KEY_UP | GG_KEY_RIGHT | GG_KEY_DOWN | GG_KEY_LEFT),
]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rom", type=Path, required=True)
    args = ap.parse_args()
    data = args.rom.read_bytes()
    verify_dev_rom(data, manifest=args.rom.parent / "manifest.json")
    with tempfile.TemporaryDirectory(prefix="pce-dev-joysns-") as tmp:
        folder = Path(tmp)
        (folder / "d.bin").write_bytes(bytes(2352 * 32))
        cue = folder / "d.cue"
        cue.write_text('FILE "d.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
        probe = compile_probe(folder)
        failures = []
        for name, mask in CASES:
            res = run_case(probe, args.rom.resolve(), cue.resolve(), mask)
            want = expected_a(mask)
            if res["pc"] != res["halt"]:
                failures.append(f"{name}: did not halt pc={res['pc']} halt={res['halt']}")
            if res["a"] != want:
                failures.append(f"{name}: A=0x{res['a']:02X} want=0x{want:02X} mask=0x{mask:X}")
            if res["x"] != 0x5A or res["y"] != 0xA5:
                failures.append(f"{name}: X/Y not preserved {res['x']},{res['y']}")
            if res["sp_stored"] != 0xFF or res["sp"] != 0xFF:
                failures.append(f"{name}: SP not balanced {res['sp_stored']},{res['sp']}")
            if res["mpr"][1] != 0xF8 or res["mpr"][7] != 0 or res["mpr"][0] != 0xFF or res["mpr0_stored"] != 0xFF:
                failures.append(f"{name}: MPR not preserved {res['mpr']} mpr0_stored={res['mpr0_stored']}")
        if failures:
            for f in failures: print("FAIL", f, file=sys.stderr)
            raise SystemExit(1)
        print(f"PASS EX_JOYSNS DEV real-core: {len(CASES)} cases, real I/O read verified (DEV_EXPERIMENTAL, not ABI)")


if __name__ == "__main__":
    main()
