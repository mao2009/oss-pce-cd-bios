#!/usr/bin/env python3
"""Build the DEVELOPMENT-ONLY internal boot path (Issue #501). NOT compatible, NOT a BIOS, NOT DEV_CALLABLE.

reset -> src/dev_boot_internal.s -> cd_media_state (READ(6) LBA 0, result $3F40) -> cd_load_exec_probe.s
(READ(6) LBA 5, RAM $3000, jump) -> payload marker -> cd_halt_ok. Only the reset vector and the result-block
address differ from the Issue #14/#5 experiment build (tools/build_cdload_exec.py); that build is unchanged.
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

MS_RES = 0x3F40                      # cd_media_state result block (see src/dev_boot_internal.s allocation table)
ROM_NAME = "dev-only-not-compatible-dev-boot-internal.pce"
REQUIRED = ("dev_boot_reset", "dev_boot_entry", "dev_media_halt", "cd_entry", "cd_buffer_ready", "cd_data_done", "cd_halt_ok",
            "cd_halt_fail", "cd_unexpected_interrupt", "cd_cdb", "cd_payload_return", "cd_media_state", "ms_done")
PAYLOAD_LABELS = ("payload_entry", "payload_done")


def parse_needed_labels(path: Path, names) -> dict:
    """Like parse_labels, but only for `names`: the two linked units both define helpers such as wait_phase."""
    found = {}
    for address, name in re.findall(r"^al ([0-9A-Fa-f]{6}) \.([A-Za-z_][A-Za-z_0-9]*)$", path.read_text(), re.M):
        if name not in names:
            continue
        value = int(address, 16)
        if found.setdefault(name, value) != value:
            raise ValueError(f"contradictory addresses for label {name}")
    return found


def build_dev_boot_internal(original_path: Path, output: Path, mode="debug") -> Path:
    if mode not in ("debug", "release"):
        raise ValueError("MODE must be debug or release")
    original = original_path.read_bytes()
    verify_dev_rom(original, manifest=original_path.parent / "manifest.json")
    ca65, ld65 = executable("ca65"), executable("ld65")
    for tool in (ca65, ld65):
        version = subprocess.run([tool, "--version"], check=True, capture_output=True, text=True)
        if DEPENDENCIES["cc65"]["revision"][:9] not in version.stdout + version.stderr:
            raise ValueError("internal boot compiler/linker does not report pinned cc65 build ID")
    output.mkdir(parents=True, exist_ok=True)
    debug_flags = ["-g"] if mode == "debug" else []

    objects = assemble_api_objects(output, mode)
    units = [
        (ROOT / "src/dev_boot_internal.s", []),
        (ROOT / "src/cd_load_exec_probe.s", ["-D", "DEV_BOOT_INTERNAL"]),
        (ROOT / "src/cd_media_state.s", ["-D", f"MS_RES=${MS_RES:04X}"]),
    ]
    for source, defines in units:
        obj = output / (source.stem + ".o")
        subprocess.run([ca65, str(source), *defines, "-o", str(obj), *debug_flags], check=True)
        objects.append(obj)
    bank = output / "bank0.bin"
    rom_labels = output / "dev-boot.lbl"
    subprocess.run([ld65, "-C", str(ROOT / "src/bank0.cfg"), "-o", str(bank), "-Ln", str(rom_labels),
                    "-m", str(output / "dev-boot.map"), *map(str, objects)], check=True)
    rom_symbols = parse_needed_labels(rom_labels, REQUIRED)
    missing = [name for name in REQUIRED if name not in rom_symbols]
    if missing:
        raise ValueError(f"internal boot symbols missing: {missing}")
    bank_bytes = bank.read_bytes()
    if len(bank_bytes) != BANK_BYTES:
        raise ValueError("internal boot bank is not 8 KiB")
    rom = output / ROM_NAME
    rom.write_bytes(bank_bytes + b"\xff" * (IMAGE_BYTES - BANK_BYTES))
    data = rom.read_bytes()
    if data[:0x1000] != original[:0x1000] or data[BANK_BYTES:] != b"\xff" * (IMAGE_BYTES - BANK_BYTES):
        raise ValueError("internal boot changed the API table or the unused banks")
    reset_vector = int.from_bytes(data[0x1FFE:0x2000], "little")  # $FFFE inside bank0 ($E000-$FFFF)
    if reset_vector != rom_symbols["dev_boot_reset"] or rom_symbols["dev_boot_entry"] != reset_vector + 1:
        raise ValueError(f"reset vector {reset_vector:#x} is not dev_boot_reset followed by dev_boot_entry")
    if data[reset_vector & 0x1FFF] != 0x78:  # SEI
        raise ValueError("dev_boot_reset must be a single SEI")

    payload_obj = output / "cd_load_exec_payload.o"
    subprocess.run([ca65, str(ROOT / "src/cd_load_exec_payload.s"), "-o", str(payload_obj), *debug_flags], check=True)
    payload_bin, payload_labels = output / "payload.bin", output / "payload.lbl"
    subprocess.run([ld65, "-C", str(ROOT / "src/cd_load_exec_payload.cfg"), "-o", str(payload_bin),
                    "-Ln", str(payload_labels), str(payload_obj)], check=True)
    code = payload_bin.read_bytes()
    pay_symbols = parse_labels(payload_labels)
    if pay_symbols.get("payload_entry") != EXEC_ADDRESS or not 1 <= len(code) <= 255:
        raise ValueError("payload must start at EXEC_ADDRESS and be 1..255 bytes")
    if not 0 <= pay_symbols["payload_done"] - EXEC_ADDRESS < len(code):
        raise ValueError("payload_done label outside the payload")

    symbols = {name: rom_symbols[name] for name in REQUIRED}
    symbols.update({name: pay_symbols[name] for name in PAYLOAD_LABELS})
    (output / "trace-labels.txt").write_text(
        "".join(f"{name} {value:04x}\n" for name, value in symbols.items()), encoding="ascii")
    manifest = {
        "status": "development-only-internal-boot-not-compatible-not-dev-callable",
        "issue": 501, "mode": mode, "rom_name": ROM_NAME,
        "rom_sha256": hashlib.sha256(data).hexdigest(), "rom_bytes": IMAGE_BYTES,
        "payload_sha256": hashlib.sha256(code).hexdigest(), "payload_bytes": len(code),
        "payload_checksum": sum(code) & 0xFF, "payload_entry_offset": 0, "exec_address": EXEC_ADDRESS,
        "ms_result_address": MS_RES, "cd_load_result_address": 0x2300,
        "default_request": {"lba": DEFAULT_LBA, "count": 1}, "sector_bytes": SECTOR_BYTES,
        "media_probe_lba": 0, "symbols": symbols, "expected_mpr": EXPECTED_MPR,
        "pinned_cc65": DEPENDENCIES["cc65"]["revision"], "api_implementation": API_IMPLEMENTATION,
        "cd_read_api_slot_03": "NOT_IMPLEMENTED", "final_system_card_layout": "UNKNOWN",
        "release_eligible": False, "dev_callable": False,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return rom


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["debug", "release"], default="debug")
    args = parser.parse_args()
    base = ROOT / "build" / args.mode / "bios-dev" / "dev-only-not-compatible-syscard3.pce"
    rom = build_dev_boot_internal(base, ROOT / "build" / args.mode / "dev-boot-internal", args.mode)
    print(f"PASS DEV-only internal boot ROM (NOT compatible, NOT BIOS, NOT DEV_CALLABLE): {rom}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"dev-boot-internal build FAIL/BLOCKED: {error}") from error
