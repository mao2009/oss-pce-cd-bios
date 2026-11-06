"""Issue #670 Phase 1: measure MPR2-3 bank selection and readback on the pinned Geargrafx core.

Synthetic ROM only (tests/integration/mpr_bank_probe.s). No commercial media, no reference BIOS, no JSR gate.
Prints the decoded table for cold and warm runs and asserts the expectations; exit non-zero on mismatch.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from environment import executable
from geargrafx_api import compile_probe
from geargrafx_cdread import execute

BANKS = list(range(0x80, 0x88))
RES_BASE = 0x2800


def build_rom(out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    src = Path(__file__).resolve().parent / "mpr_bank_probe.s"
    cfg = Path(__file__).resolve().parent / "mpr_bank_probe.cfg"
    obj, rom, lbl = out / "mpr_bank_probe.o", out / "mpr-bank-probe.pce", out / "mpr-bank-probe.lbl"
    subprocess.run([executable("ca65"), str(src), "-o", str(obj), "-g"], check=True)
    subprocess.run([executable("ld65"), "-C", str(cfg), "-o", str(rom), "-Ln", str(lbl), str(obj)], check=True)
    if rom.stat().st_size != 0x2000:
        raise ValueError("probe ROM is not 8 KiB")
    return rom, lbl


def labels_from(lbl: Path, out: Path) -> Path:
    names = []
    for line in lbl.read_text().splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[0] == "al":
            addr, name = int(parts[1], 16), parts[2].lstrip(".")
            names.append(f"{name} {addr:04x}")
    path = out / "trace-labels.txt"
    path.write_text("\n".join(names) + "\n", encoding="ascii")
    return path


def decode(run) -> dict:
    buf = bytes.fromhex(run["buffer_hex"])
    res = buf[:32]  # the probe dump starts at $2800 = RES_BASE
    rows = []
    for i, bank in enumerate(BANKS):
        rows.append({"bank": f"{bank:02X}", "mpr2_readback": f"{res[i]:02X}",
                     "val_4000": f"{res[8 + i]:02X}", "val_5FFF": f"{res[16 + i]:02X}"})
    return {
        "cycle": run["cycle"], "reason": run["reason"], "pc": f"{run['pc']:04X}",
        "a": run["a"], "x": run["x"], "y": run["y"], "p": run["p"], "sp": run["sp"],
        "mpr": [f"{m:02X}" for m in run["mpr"]],
        "banks": rows,
        "boundary": {"5FFF_bank80": f"{res[24]:02X}", "6000_bank81": f"{res[25]:02X}", "mpr3_readback": f"{res[26]:02X}"},
        "alias": {"bank80_view_4002": f"{res[27]:02X}", "bank88_view_4002": f"{res[28]:02X}", "mpr2_when_88": f"{res[29]:02X}"},
        "sentinel": f"{res[31]:02X}",
    }


def check(d: dict) -> list:
    errs = []
    if d["reason"] != "cd_halt_ok":
        errs.append(f"{d['cycle']}: stopped with {d['reason']}, expected cd_halt_ok after the probe program")
    for i, row in enumerate(d["banks"]):
        bank = BANKS[i]
        if row["mpr2_readback"] != f"{bank:02X}":
            errs.append(f"{d['cycle']}: TMA readback {row['mpr2_readback']} != {bank:02X}")
        if row["val_4000"] != f"{bank ^ 0x5A:02X}":
            errs.append(f"{d['cycle']}: bank {bank:02X} $4000 {row['val_4000']} != {bank ^ 0x5A:02X}")
        if row["val_5FFF"] != f"{bank ^ 0xA5:02X}":
            errs.append(f"{d['cycle']}: bank {bank:02X} $5FFF {row['val_5FFF']} != {bank ^ 0xA5:02X}")
    if d["boundary"]["5FFF_bank80"] != "11":
        errs.append(f"{d['cycle']}: boundary $5FFF bank80 {d['boundary']['5FFF_bank80']} != 11")
    if d["boundary"]["6000_bank81"] != "22":
        errs.append(f"{d['cycle']}: boundary $6000 bank81 {d['boundary']['6000_bank81']} != 22")
    if d["boundary"]["mpr3_readback"] != "81":
        errs.append(f"{d['cycle']}: MPR3 readback {d['boundary']['mpr3_readback']} != 81")
    # Alias observations at ONE offset ($4002) only: bank $88 writes were not retained and bank $80 saw no $33.
    if d["alias"]["bank80_view_4002"] != "00":
        errs.append(f"{d['cycle']}: bank80 view of $4002 {d['alias']['bank80_view_4002']} != 00 (alias observed)")
    if d["alias"]["bank88_view_4002"] != "FF":
        errs.append(f"{d['cycle']}: bank88 view of $4002 {d['alias']['bank88_view_4002']} != FF (write retained)")
    if d["sentinel"] != "C3":
        errs.append(f"{d['cycle']}: sentinel {d['sentinel']} != C3 (program did not reach halt)")
    return errs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("debug", "release"), default="debug")
    args = parser.parse_args()
    out = ROOT / "build" / args.mode / "mpr-bank-probe"
    rom, lbl = build_rom(out)
    labels = labels_from(lbl, out)
    probe = compile_probe(out, "geargrafx_cdread_probe.cpp", "geargrafx-mpr-bank-probe")
    records = execute(probe, rom, None, labels, 200_000, None)
    decoded = [decode(run) for run in records["RUN"]]
    errors = [e for d in decoded for e in check(d)]
    result = {"scope": "synthetic MPR/bank probe; DEV only", "rom_sha256": records["rom_sha256"],
              "runs": decoded, "errors": errors, "status": "PASS" if not errors else "FAIL"}
    (out / "mpr-bank-probe.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    if errors:
        raise SystemExit("FAIL: " + "; ".join(errors[:5]))
    print("PASS mpr/bank probe on pinned Geargrafx (cold+warm)")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"mpr-bank-probe FAIL/BLOCKED: {error}") from error
