"""Actual Geargrafx System Card startup checkpoint (Issue #4, diagnostic-only).

Reads public CPU state from the pinned debugger-enabled core. No firmware HLE,
proprietary BIOS, production boot, or third-party code enters the ROM.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from build_startup import EXPECTED_MPR, MARKER, SYMBOL_OFFSETS, verify_startup
from environment import DEPENDENCIES
from geargrafx_api import compile_probe
from geargrafx_contracts import execute, trace


def check_evidence(records, symbols):
    if records["OBS"][0]["pc"] != symbols["startup_entry"]:
        raise ValueError("guest did not load the original startup reset vector")
    for row in records["OBS"][1:]:
        if (row["pc"] != symbols["startup_checkpoint"] or row["x"] != 0xb0
                or row["sp"] != 0xff or row["mpr"] != EXPECTED_MPR
                or bytes.fromhex(row["work_ram_0200_hex"])[:len(MARKER)] != MARKER):
            raise ValueError("actual System Card startup PC/X/SP/MPR/RAM checkpoint mismatch")
        if not (row["p"] & 4) or row["p"] & 8:
            raise ValueError("startup interrupt/decimal flags differ")
    for cycle in ("cold", "warm"):
        ready = trace(records, cycle, "startup_mpr_ready")
        stack = trace(records, cycle, "startup_stack_ready")
        write = trace(records, cycle, "startup_marker_write")
        checkpoint = trace(records, cycle, "startup_checkpoint")
        if ready["mpr"] != EXPECTED_MPR or stack["sp"] != 0xff:
            raise ValueError("guest never reached explicit MPR/stack setup")
        if write["a"] != 0x42 or write["mpr"] != EXPECTED_MPR:
            raise ValueError("guest marker write not observed with expected mapping")
        if checkpoint["pc"] != symbols["startup_checkpoint"]:
            raise ValueError("guest never reached startup checkpoint")
        if any(row["cycle"] == cycle and row["phase"] == "startup_unexpected_interrupt"
               for row in records["TRACE"]):
            raise ValueError("unexpected interrupt diagnostic was executed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("debug", "release"), default="debug")
    args = parser.parse_args()
    original_path = ROOT / "build" / args.mode / "bios-dev/dev-only-not-compatible-syscard3.pce"
    output = ROOT / "build" / args.mode / "startup-probe"
    rom = output / "startup-probe-not-bios.pce"
    original, payload = original_path.read_bytes(), rom.read_bytes()
    digest = verify_startup(payload, original)
    manifest = json.loads((output / "manifest.json").read_text())
    symbols = manifest["startup_symbols"]
    if symbols != {name: 0xf000 + offset for name, offset in SYMBOL_OFFSETS.items()}:
        raise ValueError("startup symbol manifest is inconsistent")
    probe = compile_probe(output)
    with tempfile.TemporaryDirectory(prefix="pce-original-startup-") as tmp:
        folder = Path(tmp)
        (folder / "original.bin").write_bytes(bytes(2352 * 32))
        cue = folder / "original.cue"
        cue.write_text('FILE "original.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
        records = execute(probe, rom, cue, output / "trace-labels.txt")
        check_evidence(records, symbols)
        # Deterministic negative: redirect the second store to $2200, so 'O'
        # overwrites 'B' even if uninitialized work RAM happened to contain 'B'.
        damaged = bytearray(payload)
        marker_offset = symbols["startup_marker_write"] - 0xe000
        if damaged[marker_offset:marker_offset+3] != b"\x8d\x00\x22":
            raise ValueError("symbol-qualified STA $2200 opcode mismatch")
        second_store = marker_offset + 5  # STA $2200 (3) then LDA #$4F (2)
        if damaged[second_store:second_store+3] != b"\x8d\x01\x22":
            raise ValueError("expected second marker store STA $2201")
        damaged[second_store+1] = 0  # STA $2200 with A='O', not 'B'
        invalid = folder / "missing-startup-marker-not-bios.pce"
        invalid.write_bytes(damaged)
        try:
            verify_startup(damaged, original)
        except ValueError:
            pass
        else:
            raise ValueError("invalid marker ROM passed fixture verifier")
        negative = execute(probe, invalid, cue, output / "trace-labels.txt")
        try:
            check_evidence(negative, symbols)
        except ValueError:
            pass
        else:
            raise ValueError("mutated startup ROM unexpectedly passed actual Geargrafx")
    evidence = {
        "scope": "original startup diagnostic ONLY; production BIOS/ABI/CD boot NOT_IMPLEMENTED",
        "rom_sha256": digest,
        "geargrafx_revision": DEPENDENCIES["geargrafx"]["revision"],
        "source_archive_sha256": DEPENDENCIES["geargrafx"]["sha256"],
        "loader": "LoadBiosFromBuffer(syscard=true) + original synthetic 32-sector CD",
        "status": "PASS",
        "observations": records, "missing_marker_negative": {
            "status": "PASS_EXPECTED_FAILURE",
            "rom_sha256": hashlib.sha256(damaged).hexdigest(),
            "observations": negative,
        },
        "bios_service_abi": "UNKNOWN", "cd_boot": "NOT_TESTED",
        "proprietary_content": "NONE",
    }
    (output / "startup-evidence.json").write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    print("PASS real Geargrafx System Card startup: cold/warm PC/SP/MPR/RAM, marker negative; NOT BIOS boot")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError) as error:
        raise SystemExit(f"startup integration FAIL/BLOCKED: {error}") from error
