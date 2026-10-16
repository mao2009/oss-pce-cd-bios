#!/usr/bin/env python3
"""Build the original DEVELOPMENT-ONLY CD_RESET bus-reset diagnostic (Issue #10, B2).

Separate from the #5 read diagnostic ROM (reused as-is for its judge and Geargrafx frontend),
from the base API ROM and from the #4 startup ROM. It implements no BIOS API and makes no
System Card CD_RESET claim; release stays disabled.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

if __package__:
    from .build_cdread import (CDB_BYTES, DEFAULT_LBA, DEFAULT_COUNT, EXPECTED_MPR, HALT_CONFIRM_STEPS,
                               BUFFER_ADDRESS, RESULT_ADDRESS, code_digest, read_request, verify_cdread)
    from .build_rom import BANK_BYTES, IMAGE_BYTES, ROOT, assemble_api_objects, verify_dev_rom
    from .environment import DEPENDENCIES, executable
else:
    from build_cdread import (CDB_BYTES, DEFAULT_LBA, DEFAULT_COUNT, EXPECTED_MPR, HALT_CONFIRM_STEPS,
                              BUFFER_ADDRESS, RESULT_ADDRESS, code_digest, read_request, verify_cdread)
    from build_rom import BANK_BYTES, IMAGE_BYTES, ROOT, assemble_api_objects, verify_dev_rom
    from environment import DEPENDENCIES, executable

SOURCE = ROOT / "src/cd_reset_probe.s"
REQUIRED_SYMBOLS = ("cd_entry", "cd_stack_ready", "cd_buffer_ready", "cd_reset_done", "cd_command_sent",
                    "cd_data_done", "cd_halt_ok", "cd_halt_fail", "cd_unexpected_interrupt", "cd_cdb", "cd_select")


def parse_symbols(path: Path) -> dict:
    symbols = {}
    for address, name in re.findall(r"^al ([0-9A-Fa-f]{6}) \.([A-Za-z_][A-Za-z_0-9]*)$", path.read_text(), re.M):
        if name.startswith("cd_"):
            value = int(address, 16)
            if name in symbols and symbols[name] != value:
                raise ValueError("contradictory cd label addresses")
            symbols[name] = value
    missing = [name for name in REQUIRED_SYMBOLS if name not in symbols]
    if missing or symbols["cd_entry"] != 0xF000:
        raise ValueError(f"cd_reset symbols/placement mismatch: missing={missing}")
    return {name: symbols[name] for name in REQUIRED_SYMBOLS}


def build_cdreset(original_path: Path, output: Path, mode="debug") -> Path:
    if mode not in ("debug", "release"):
        raise ValueError("MODE must be debug or release")
    original = original_path.read_bytes()
    verify_dev_rom(original)
    ca65, ld65 = executable("ca65"), executable("ld65")
    for tool in (ca65, ld65):
        version = subprocess.run([tool, "--version"], check=True, capture_output=True, text=True)
        if DEPENDENCIES["cc65"]["revision"][:9] not in version.stdout + version.stderr:
            raise ValueError("CD_RESET diagnostic compiler/linker does not report pinned cc65 build ID")
    output.mkdir(parents=True, exist_ok=True)
    objects = assemble_api_objects(output, mode)
    for source in (SOURCE,):
        obj = output / (source.stem + ".o")
        subprocess.run([ca65, str(source), "-o", str(obj), *(['-g'] if mode == "debug" else [])], check=True)
        objects.append(obj)
    linked = output / "bank0.bin"
    labels = output / "cdreset.lbl"
    subprocess.run([ld65, "-C", str(ROOT / "src/bank0.cfg"), "-o", str(linked),
                    "-Ln", str(labels), "-m", str(output / "cdreset.map"), *map(str, objects)], check=True)
    symbols = parse_symbols(labels)
    bank = linked.read_bytes()
    if len(bank) != BANK_BYTES:
        raise ValueError("CD_RESET diagnostic bank is not 8 KiB")
    rom = output / "cdreset-probe-not-bios.pce"
    rom.write_bytes(bank + b"\xff" * (IMAGE_BYTES - BANK_BYTES))
    data = rom.read_bytes()
    digest = verify_cdread(data, original, symbols)
    if read_request(data, symbols) != (DEFAULT_LBA, DEFAULT_COUNT):
        raise ValueError("embedded default READ(6) request differs from the documented default")
    (output / "trace-labels.txt").write_text(
        "".join(f"{name} {value:04x}\n" for name, value in symbols.items()), encoding="ascii")
    code_end = symbols["cd_cdb"] + CDB_BYTES - 0xE000
    manifest = {
        "status": "development-cd-reset-bus-diagnostic-not-compatible-bios",
        "mode": mode, "rom_sha256": digest, "rom_bytes": IMAGE_BYTES,
        "code_bytes": code_end - 0x1000,
        "code_sha256_excluding_request": code_digest(data, symbols),
        "original_api_rom_sha256": hashlib.sha256(original).hexdigest(),
        "pinned_cc65": DEPENDENCIES["cc65"]["revision"],
        "symbols": symbols, "expected_mpr": EXPECTED_MPR,
        "result_address": RESULT_ADDRESS, "buffer_address": BUFFER_ADDRESS,
        "halt_confirm_steps": HALT_CONFIRM_STEPS,
        "default_request": {"lba": DEFAULT_LBA, "count": DEFAULT_COUNT},
        "api_implementation": "NONE", "cd_reset_api_slot_01": "NOT_IMPLEMENTED",
        "release_eligible": False,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return rom


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["debug", "release"], default="debug")
    args = parser.parse_args()
    base = ROOT / "build" / args.mode / "bios-dev" / "dev-only-not-compatible-syscard3.pce"
    rom = build_cdreset(base, ROOT / "build" / args.mode / "cdreset-probe", args.mode)
    print(f"PASS original CD_RESET bus diagnostic (NOT BIOS, CD_RESET API NOT_IMPLEMENTED): {rom}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"cdreset build FAIL/BLOCKED: {error}") from error
