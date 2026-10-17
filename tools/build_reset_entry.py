#!/usr/bin/env python3
"""Build and verify the Issue #143 minimal reset-entry image (original code, DEVELOPMENT-ONLY).

Does not change the default DEV ROM. Only spec-sourced init is performed; see src/reset_entry.s.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

if __package__:
    from .build_rom import API_IMPLEMENTATION, BANK_BYTES, IMAGE_BYTES, ROOT, assemble_api_objects, verify_dev_rom
    from .environment import DEPENDENCIES, executable
else:
    from build_rom import API_IMPLEMENTATION, BANK_BYTES, IMAGE_BYTES, ROOT, assemble_api_objects, verify_dev_rom
    from environment import DEPENDENCIES, executable

# SEI CLD; LDA #$FF TAM #1; LDA #$F8 TAM #2; LDX #$FF TXS; BRA self; LDX #$EE BRA self
RESET_CODE = bytes.fromhex("78 d8 a9 ff 53 01 a9 f8 53 02 a2 ff 9a 80 fe a2 ee 80 fe")
SYMBOL_OFFSETS = {"reset_entry": 0, "reset_mpr_ready": 10, "reset_ready": 13,
                  "reset_unexpected_interrupt": 15}
UNKNOWN = ["mpr2_to_mpr6_layout", "csh_speed_policy", "stack_pointer_abi", "interrupt_abi",
           "final_system_card_layout"]


def verify_reset_entry(data: bytes, original: bytes) -> str:
    verify_dev_rom(original)
    if len(data) != IMAGE_BYTES:
        raise ValueError("reset entry image must have 262144 bytes")
    if data[:0x1000] != original[:0x1000]:
        raise ValueError("reset entry changed the API table/fallback/overlay area")
    if data[0x1000:0x1000 + len(RESET_CODE)] != RESET_CODE:
        raise ValueError("reset entry instructions differ")
    if data[0x1000 + len(RESET_CODE):0x1ff6] != b"\xff" * (0xff6 - len(RESET_CODE)):
        raise ValueError("unexpected bytes after the reset entry")
    trap = (0xf000 + SYMBOL_OFFSETS["reset_unexpected_interrupt"]).to_bytes(2, "little")
    if data[0x1ff6:0x1ffe] != trap * 4 or data[0x1ffe:0x2000] != b"\x00\xf0":
        raise ValueError("reset entry vectors differ")
    if data[BANK_BYTES:] != b"\xff" * (IMAGE_BYTES - BANK_BYTES):
        raise ValueError("reset entry modified unused ROM banks")
    return hashlib.sha256(data).hexdigest()


def parse_symbols(path: Path) -> dict:
    found = {}
    for address, name in re.findall(r"^al ([0-9A-Fa-f]{6}) \.([A-Za-z_][A-Za-z_0-9]*)$", path.read_text(), re.M):
        if name.startswith("reset_"):
            found[name] = int(address, 16)
    if found != {k: 0xf000 + v for k, v in SYMBOL_OFFSETS.items()}:
        raise ValueError(f"reset symbols/placement mismatch: {found}")
    return found


def build_reset_entry(original_path: Path, output: Path, mode="debug") -> Path:
    if mode not in ("debug", "release"):
        raise ValueError("MODE must be debug or release")
    original = original_path.read_bytes()
    verify_dev_rom(original, manifest=original_path.parent / 'manifest.json')
    ca65, ld65 = executable("ca65"), executable("ld65")
    output.mkdir(parents=True, exist_ok=True)
    objects = assemble_api_objects(output, mode)
    for source in (ROOT / "src/reset_entry.s",):
        obj = output / (source.stem + ".o")
        subprocess.run([ca65, str(source), "-o", str(obj), *(["-g"] if mode == "debug" else [])], check=True)
        objects.append(obj)
    linked, labels = output / "bank0.bin", output / "reset.lbl"
    subprocess.run([ld65, "-C", str(ROOT / "src/bank0.cfg"), "-o", str(linked), "-Ln", str(labels),
                    "-m", str(output / "reset.map"), *map(str, objects)], check=True)
    symbols = parse_symbols(labels)
    bank = linked.read_bytes()
    if len(bank) != BANK_BYTES:
        raise ValueError("reset entry bank is not 8 KiB")
    rom = output / "reset-entry-not-bios.pce"
    rom.write_bytes(bank + b"\xff" * (IMAGE_BYTES - BANK_BYTES))
    digest = verify_reset_entry(rom.read_bytes(), original)
    (output / "trace-labels.txt").write_text(
        "".join(f"{n} {v:04x}\n" for n, v in symbols.items()), encoding="ascii")
    (output / "manifest.json").write_text(json.dumps({
        "status": "development-reset-entry-not-compatible-bios", "mode": mode, "rom_sha256": digest,
        "rom_bytes": IMAGE_BYTES, "pinned_cc65": DEPENDENCIES["cc65"]["revision"],
        "symbols": symbols, "mpr0": 0xFF, "mpr1": 0xF8, "sp": 0xFF,
        "api_implementation": API_IMPLEMENTATION, "unknown": UNKNOWN, "release_eligible": False,
    }, indent=2, sort_keys=True) + "\n")
    return rom


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["debug", "release"], default="debug")
    args = parser.parse_args()
    base = ROOT / "build" / args.mode / "bios-dev" / "dev-only-not-compatible-syscard3.pce"
    rom = build_reset_entry(base, ROOT / "build" / args.mode / "reset-entry", args.mode)
    print(f"PASS minimal reset entry image (NOT complete BIOS): {rom}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"reset entry build FAIL/BLOCKED: {error}") from error
