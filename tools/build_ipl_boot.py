#!/usr/bin/env python3
"""Build the DEV-only IPL boot ROM for Issue #668.

NOT a compatible System Card BIOS. NOT a BIOS ABI implementation. NOT release eligible. NOT DEV_CALLABLE.

The ROM is linked from the same original reset code as the experimental image
(src/experimental_bios_boot.s + src/cd_load_exec_probe.s) with ONLY the host-derived selection record
(track, INDEX 01 LBA, kind) embedded. Unlike tools/build_experimental_bios_boot.py, it does not require
the selected sector to equal any payload known at ROM build time, so the same ROM is valid for any disc
whose selected sector the guest validates itself (PCEX header, length, entry, checksum) before copying
to RAM and executing it. The ROM is valid only for discs whose INDEX 01 is the embedded LBA; the host
checks that for every mounted disc in the integration test.
"""
import hashlib
import json
import subprocess
from pathlib import Path

if __package__:
    from .build_experimental_bios_boot import REQUIRED, _experimental_boot_units, parse_needed_labels
    from .build_rom import BANK_BYTES, IMAGE_BYTES, ROOT, assemble_api_objects, verify_dev_rom
    from .environment import DEPENDENCIES, executable
    from .experimental_track_selection import selection_from_cue, verify_media_identity
else:
    from build_experimental_bios_boot import REQUIRED, _experimental_boot_units, parse_needed_labels
    from build_rom import BANK_BYTES, IMAGE_BYTES, ROOT, assemble_api_objects, verify_dev_rom
    from environment import DEPENDENCIES, executable
    from experimental_track_selection import selection_from_cue, verify_media_identity

ROM_NAME = "dev-ipl-boot-not-compatible.pce"


def build_ipl_rom(original_path: Path, output: Path, mode="debug", cue_path=None):
    """Return (rom_path, rom_symbols). The ROM does not depend on any payload bytes."""
    if mode not in ("debug", "release"):
        raise ValueError("MODE must be debug or release")
    if cue_path is None:
        raise ValueError("IPL boot requires the exact mounted synthetic CUE")
    selected = selection_from_cue(cue_path, cue_path)
    verify_media_identity(selected["media_identity"], cue_path)
    original = original_path.read_bytes()
    verify_dev_rom(original, manifest=original_path.parent / "manifest.json")
    ca65, ld65 = executable("ca65"), executable("ld65")
    for tool in (ca65, ld65):
        version = subprocess.run([tool, "--version"], check=True, capture_output=True, text=True)
        if DEPENDENCIES["cc65"]["revision"][:9] not in version.stdout + version.stderr:
            raise ValueError("IPL boot compiler/linker does not report pinned cc65 build ID")
    output.mkdir(parents=True, exist_ok=True)
    debug_flags = ["-g"] if mode == "debug" else []
    objects = assemble_api_objects(output, mode)
    for source, defines in _experimental_boot_units(selected):
        obj = output / (source.stem + ".o")
        subprocess.run([ca65, str(source), *defines, "-o", str(obj), *debug_flags], check=True)
        objects.append(obj)
    bank, labels = output / "bank0.bin", output / "ipl-boot.lbl"
    subprocess.run([ld65, "-C", str(ROOT / "src/bank0.cfg"), "-o", str(bank), "-Ln", str(labels),
                    "-m", str(output / "ipl-boot.map"), *map(str, objects)], check=True)
    rom_symbols = parse_needed_labels(labels, REQUIRED)
    missing = [name for name in REQUIRED if name not in rom_symbols]
    if missing:
        raise ValueError(f"IPL boot symbols missing: {missing}")
    bank_bytes = bank.read_bytes()
    if len(bank_bytes) != BANK_BYTES:
        raise ValueError("IPL boot bank is not 8 KiB")
    rom = output / ROM_NAME
    rom.write_bytes(bank_bytes + b"\xff" * (IMAGE_BYTES - BANK_BYTES))
    data = rom.read_bytes()
    if len(data) != IMAGE_BYTES or data[:0x1000] != original[:0x1000] \
            or data[BANK_BYTES:] != b"\xff" * (IMAGE_BYTES - BANK_BYTES):
        raise ValueError("IPL boot image changed the API table, size, or unused banks")
    reset_vector = int.from_bytes(data[0x1FFE:0x2000], "little")
    if reset_vector != rom_symbols["experimental_bios_reset"] or rom_symbols["experimental_bios_init"] != reset_vector + 1:
        raise ValueError("reset vector is not the experimental one-byte SEI followed by init")
    if data[reset_vector & 0x1FFF] != 0x78:
        raise ValueError("reset must start with SEI")
    verify_media_identity(selected["media_identity"], cue_path)
    manifest = {
        "status": "DEV-ONLY-IPL-BOOT-not-compatible-not-a-bios-abi",
        "mode": mode, "rom_name": ROM_NAME, "rom_sha256": hashlib.sha256(data).hexdigest(), "rom_bytes": IMAGE_BYTES,
        "payload_embedded_in_rom": False,
        "selection_contract": {"host_side_cue_analysis": True, "guest_toc": "NOT_IMPLEMENTED",
                               "track": selected["track"], "lba": selected["lba"], "kind": selected["kind"]},
        "guest_validation": ["PCEX magic", "length 1..255", "entry inside code", "checksum", "fixed copy to $3000"],
        "fixed_load_address": 0x3000, "multi_sector": "NOT_IMPLEMENTED",
        "symbols": rom_symbols, "pinned_cc65": DEPENDENCIES["cc65"]["revision"],
        "cd_read_api_slot_03": "NOT_IMPLEMENTED", "system_card_abi": "NO_CLAIM", "release_eligible": False,
        "dev_callable": False, "private_media_used": False,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return rom, rom_symbols
