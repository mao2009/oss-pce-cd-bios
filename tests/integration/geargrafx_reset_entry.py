"""Real Geargrafx cold/warm run of the Issue #143 reset entry (development-only)."""
import argparse
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from build_reset_entry import SYMBOL_OFFSETS, verify_reset_entry
from environment import DEPENDENCIES
from geargrafx_api import compile_probe
from geargrafx_contracts import execute, trace


def check(records, symbols):
    if records["OBS"][0]["pc"] != symbols["reset_entry"]:
        raise ValueError("guest did not load the reset vector")
    for row in records["OBS"][1:]:
        if (row["pc"], row["sp"]) != (symbols["reset_ready"], 0xff) or row["mpr"][:2] != [0xff, 0xf8] \
                or row["mpr"][7] != 0 or not row["p"] & 4 or row["p"] & 8:
            raise ValueError("reset entry PC/SP/MPR/flag checkpoint mismatch")
    for cycle in ("cold", "warm"):
        if trace(records, cycle, "reset_mpr_ready")["mpr"][:2] != [0xff, 0xf8]:
            raise ValueError("MPR0/MPR1 not set before stack init")
        if any(r["cycle"] == cycle and r["phase"] == "reset_unexpected_interrupt" for r in records["TRACE"]):
            raise ValueError("unexpected interrupt trap executed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("debug", "release"), default="debug")
    mode = parser.parse_args().mode
    base = (ROOT / "build" / mode / "bios-dev/dev-only-not-compatible-syscard3.pce").read_bytes()
    out = ROOT / "build" / mode / "reset-entry"
    rom = out / "reset-entry-not-bios.pce"
    payload = rom.read_bytes()
    digest = verify_reset_entry(payload, base)
    symbols = json.loads((out / "manifest.json").read_text())["symbols"]
    if symbols != {k: 0xf000 + v for k, v in SYMBOL_OFFSETS.items()}:
        raise ValueError("symbol manifest inconsistent")
    probe = compile_probe(out)
    with tempfile.TemporaryDirectory(prefix="pce-reset-entry-") as tmp:
        folder = Path(tmp)
        (folder / "d.bin").write_bytes(bytes(2352 * 32))
        cue = folder / "d.cue"
        cue.write_text('FILE "d.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
        labels = out / "trace-labels.txt"
        records = execute(probe, rom, cue, labels)
        check(records, symbols)
        negatives = {}
        for name, offset, new in (("mpr0", 3, 0xfe), ("mpr1", 7, 0xf0), ("sp", 11, 0xfe)):
            bad = bytearray(payload)
            bad[0x1000 + offset] = new
            path = folder / f"{name}.pce"
            path.write_bytes(bad)
            try:
                check(execute(probe, path, cue, labels), symbols)
            except ValueError:
                negatives[name] = "PASS_EXPECTED_FAILURE"
            else:
                raise ValueError(f"{name}: mutated ROM passed on the real core")
    (out / "reset-entry-evidence.json").write_text(json.dumps({
        "scope": "minimal reset entry ONLY; production BIOS/ABI/CD boot NOT_IMPLEMENTED",
        "rom_sha256": digest, "geargrafx_revision": DEPENDENCIES["geargrafx"]["revision"],
        "status": "PASS", "observations": records, "mutation_negatives": negatives,
    }, indent=2, sort_keys=True) + "\n")
    print("PASS real Geargrafx reset entry: cold/warm PC/SP/MPR0-1/MPR7/flags; mpr0/mpr1/sp mutations rejected")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError) as error:
        raise SystemExit(f"reset entry integration FAIL/BLOCKED: {error}") from error
