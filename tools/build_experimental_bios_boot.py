#!/usr/bin/env python3
"""Build an opt-in experimental 256 KiB System-Card-sized DEV image.

NOT a compatible System Card BIOS. NOT a BIOS ABI implementation. NOT release eligible.
The image has original reset code, then reuses this repository's synthetic-only media check,
READ(6), RAM-copy, and payload-handoff diagnostics. It does not consume private ROM media.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

if __package__:
    from .build_rom import API_IMPLEMENTATION, BANK_BYTES, IMAGE_BYTES, ROOT, assemble_api_objects, verify_dev_rom
    from .build_cdload_exec import DEFAULT_LBA, DISC_SECTORS, EXEC_ADDRESS, EXPECTED_MPR, SECTOR_BYTES, parse_labels
    from .environment import DEPENDENCIES, executable
else:
    from build_rom import API_IMPLEMENTATION, BANK_BYTES, IMAGE_BYTES, ROOT, assemble_api_objects, verify_dev_rom
    from build_cdload_exec import DEFAULT_LBA, DISC_SECTORS, EXEC_ADDRESS, EXPECTED_MPR, SECTOR_BYTES, parse_labels
    from environment import DEPENDENCIES, executable

MS_RES = 0x3F40
EXPERIMENTAL_BOOT_FLAG = "EXPERIMENTAL_BIOS_BOOT"
ROM_NAME = "experimental-dev-only-not-compatible-syscard-sized.pce"
REQUIRED = ("experimental_bios_reset", "experimental_bios_init", "experimental_bios_media_halt", "cd_entry",
            "cd_buffer_ready", "cd_data_done", "cd_halt_ok", "cd_halt_fail", "cd_unexpected_interrupt", "cd_cdb",
            "cd_payload_return", "cd_media_state", "ms_done")
PAYLOAD_LABELS = ("payload_entry", "payload_done")


def parse_needed_labels(path: Path, names) -> dict:
    found = {}
    for address, name in re.findall(r"^al ([0-9A-Fa-f]{6}) \.([A-Za-z_][A-Za-z_0-9]*)$", path.read_text(), re.M):
        if name in names:
            value = int(address, 16)
            if found.setdefault(name, value) != value:
                raise ValueError(f"contradictory addresses for label {name}")
    return found


def experimental_boot_units():
    """Opt-in link units. The default image never receives EXPERIMENTAL_BIOS_BOOT."""
    return [
        (ROOT / "src/experimental_bios_boot.s", []),
        (ROOT / "src/cd_load_exec_probe.s", ["-D", EXPERIMENTAL_BOOT_FLAG]),
        (ROOT / "src/cd_media_state.s", ["-D", f"MS_RES=${MS_RES:04X}"]),
    ]


def build_experimental_bios_boot(original_path: Path, output: Path, mode="debug") -> Path:
    if mode not in ("debug", "release"):
        raise ValueError("MODE must be debug or release")
    original = original_path.read_bytes()
    verify_dev_rom(original, manifest=original_path.parent / "manifest.json")
    ca65, ld65 = executable("ca65"), executable("ld65")
    for tool in (ca65, ld65):
        version = subprocess.run([tool, "--version"], check=True, capture_output=True, text=True)
        if DEPENDENCIES["cc65"]["revision"][:9] not in version.stdout + version.stderr:
            raise ValueError("experimental boot compiler/linker does not report pinned cc65 build ID")
    output.mkdir(parents=True, exist_ok=True)
    debug_flags = ["-g"] if mode == "debug" else []
    objects = assemble_api_objects(output, mode)
    for source, defines in experimental_boot_units():
        obj = output / (source.stem + ".o")
        subprocess.run([ca65, str(source), *defines, "-o", str(obj), *debug_flags], check=True)
        objects.append(obj)
    bank, labels = output / "bank0.bin", output / "experimental-boot.lbl"
    subprocess.run([ld65, "-C", str(ROOT / "src/bank0.cfg"), "-o", str(bank), "-Ln", str(labels),
                    "-m", str(output / "experimental-boot.map"), *map(str, objects)], check=True)
    rom_symbols = parse_needed_labels(labels, REQUIRED)
    missing = [name for name in REQUIRED if name not in rom_symbols]
    if missing:
        raise ValueError(f"experimental boot symbols missing: {missing}")
    bank_bytes = bank.read_bytes()
    if len(bank_bytes) != BANK_BYTES:
        raise ValueError("experimental boot bank is not 8 KiB")
    rom = output / ROM_NAME
    rom.write_bytes(bank_bytes + b"\xff" * (IMAGE_BYTES - BANK_BYTES))
    data = rom.read_bytes()
    if len(data) != IMAGE_BYTES or data[:0x1000] != original[:0x1000] or data[BANK_BYTES:] != b"\xff" * (IMAGE_BYTES - BANK_BYTES):
        raise ValueError("experimental image changed the API table, size, or unused banks")
    reset_vector = int.from_bytes(data[0x1FFE:0x2000], "little")
    if reset_vector != rom_symbols["experimental_bios_reset"] or rom_symbols["experimental_bios_init"] != reset_vector + 1:
        raise ValueError("reset vector is not the experimental one-byte SEI followed by init")
    if data[reset_vector & 0x1FFF] != 0x78:
        raise ValueError("experimental reset must start with SEI")

    payload_obj = output / "cd_load_exec_payload.o"
    subprocess.run([ca65, str(ROOT / "src/cd_load_exec_payload.s"), "-o", str(payload_obj), *debug_flags], check=True)
    payload_bin, payload_labels = output / "payload.bin", output / "payload.lbl"
    subprocess.run([ld65, "-C", str(ROOT / "src/cd_load_exec_payload.cfg"), "-o", str(payload_bin),
                    "-Ln", str(payload_labels), str(payload_obj)], check=True)
    code, pay_symbols = payload_bin.read_bytes(), parse_labels(payload_labels)
    if pay_symbols.get("payload_entry") != EXEC_ADDRESS or not 1 <= len(code) <= 255:
        raise ValueError("payload must start at EXEC_ADDRESS and be 1..255 bytes")
    symbols = {name: rom_symbols[name] for name in REQUIRED}
    symbols.update({name: pay_symbols[name] for name in PAYLOAD_LABELS})
    # The shared integration judge uses semantic trace names; these aliases point to this image's new reset labels.
    trace_symbols = dict(symbols)
    trace_symbols.update({"dev_boot_entry": symbols["experimental_bios_init"],
                          "dev_media_halt": symbols["experimental_bios_media_halt"]})
    (output / "trace-labels.txt").write_text("".join(f"{name} {value:04x}\n" for name, value in trace_symbols.items()), encoding="ascii")
    manifest = {
        "status": "EXPERIMENTAL-DEV-ONLY-not-compatible-not-a-bios-abi",
        "mode": mode, "rom_name": ROM_NAME, "rom_sha256": hashlib.sha256(data).hexdigest(), "rom_bytes": IMAGE_BYTES,
        "payload_sha256": hashlib.sha256(code).hexdigest(), "payload_bytes": len(code), "payload_checksum": sum(code) & 0xFF,
        "payload_entry_offset": 0, "exec_address": EXEC_ADDRESS, "ms_result_address": MS_RES,
        "default_request": {"lba": DEFAULT_LBA, "count": 1}, "sector_bytes": SECTOR_BYTES, "media_probe_lba": 0,
        "symbols": symbols, "trace_symbols": trace_symbols, "expected_mpr": EXPECTED_MPR,
        "pinned_cc65": DEPENDENCIES["cc65"]["revision"], "api_implementation": API_IMPLEMENTATION,
        "cd_read_api_slot_03": "NOT_IMPLEMENTED", "system_card_abi": "NO_CLAIM", "release_eligible": False,
        "dev_callable": False, "private_media_used": False,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return rom


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["debug", "release"], default="debug")
    args = parser.parse_args()
    base = ROOT / "build" / args.mode / "bios-dev" / "dev-only-not-compatible-syscard3.pce"
    rom = build_experimental_bios_boot(base, ROOT / "build" / args.mode / "experimental-bios-boot", args.mode)
    print(f"PASS experimental DEV-only 256 KiB image (NOT compatible, NOT a BIOS ABI): {rom}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"experimental-bios-boot build FAIL/BLOCKED: {error}") from error
