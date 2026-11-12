"""Build a custom experimental image with the CD_READ link helper (Agent-B G5 P0).

NOT a compatible System Card BIOS. NOT a BIOS ABI. NOT release eligible.
Experimental-only image builder for synthetic-media real-core tests. Does NOT
modify tools/build_experimental_bios_boot.py, the normal DEV ROM, or any formal
ABI registry. The normal ROM is byte-identical outside the experimental BOOT area.

The image reuses the experimental boot + probe objects and additionally links
src/cd_read_link.s (CD_READ_LINK_V1) into BOOT. Payloads call the link by direct
JSR to its ROM address (NOT a BIOS slot entry). Payloads are custom (<256 bytes)
and stored at the selected INDEX01 sector via encode_sector, like the proto tests.
"""
import hashlib
import json
from pathlib import Path
import re
import subprocess

if __package__:
    from .build_rom import API_IMPLEMENTATION, BANK_BYTES, IMAGE_BYTES, ROOT, assemble_api_objects, verify_dev_rom
    from .build_cdload_exec import EXEC_ADDRESS, EXPECTED_MPR, SECTOR_BYTES, encode_sector, parse_labels
    from .cue_analysis import analyze, user_sector
    from .environment import DEPENDENCIES, executable
    from .experimental_track_selection import assembler_defines, selection_from_cue, verify_media_identity
    from .build_experimental_bios_boot import ROM_NAME, parse_needed_labels
else:
    from build_rom import API_IMPLEMENTATION, BANK_BYTES, IMAGE_BYTES, ROOT, assemble_api_objects, verify_dev_rom
    from build_cdload_exec import EXEC_ADDRESS, EXPECTED_MPR, SECTOR_BYTES, encode_sector, parse_labels
    from cue_analysis import analyze, user_sector
    from environment import DEPENDENCIES, executable
    from experimental_track_selection import assembler_defines, selection_from_cue, verify_media_identity
    from build_experimental_bios_boot import ROM_NAME, parse_needed_labels

LINK_REQUIRED = ("experimental_bios_reset", "experimental_bios_init",
                 "experimental_bios_selection_halt", "experimental_selection",
                 "cd_entry", "cd_buffer_ready", "cd_data_done",
                 "cd_halt_ok", "cd_halt_fail", "cd_unexpected_interrupt",
                 "cd_cdb", "cd_payload_return", "cd_set_read_lba",
                 "cd_entry_selected", "cd_read_link_entry")
PAYLOAD_LABELS = ("payload_entry", "payload_done")


def build_link_image(original_path, output, mode="debug",
                     cue_path=None, payload_code=None, payload_symbols=None):
    """Build an experimental image with the link helper in BOOT."""
    if mode not in ("debug", "release"):
        raise ValueError("MODE must be debug or release")
    if cue_path is None:
        raise ValueError("link image requires the exact mounted synthetic CUE")
    if type(payload_code) is not bytes or not 1 <= len(payload_code) <= 255:
        raise ValueError("link image requires a finalized 1..255-byte payload")
    if payload_symbols is None or set(payload_symbols) != set(PAYLOAD_LABELS):
        raise ValueError(f"custom payload symbols must be {PAYLOAD_LABELS}")
    if payload_symbols.get("payload_entry") != EXEC_ADDRESS:
        raise ValueError("custom payload_entry must equal EXEC_ADDRESS")
    selected = selection_from_cue(cue_path, cue_path)
    verify_media_identity(selected["media_identity"], cue_path)
    if user_sector(cue_path, analyze(cue_path), selected["lba"]) != encode_sector(selected["lba"], payload_code):
        raise ValueError("finalized selected sector does not contain the link-test payload")
    original = Path(original_path).read_bytes()
    verify_dev_rom(original, manifest=Path(original_path).parent / "manifest.json")
    ca65, ld65 = executable("ca65"), executable("ld65")
    for tool in (ca65, ld65):
        version = subprocess.run([tool, "--version"], check=True, capture_output=True, text=True)
        if DEPENDENCIES["cc65"]["revision"][:9] not in version.stdout + version.stderr:
            raise ValueError("link image compiler/linker does not report pinned cc65 build ID")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    debug_flags = ["-g"] if mode == "debug" else []
    objects = assemble_api_objects(output, mode)
    for source, defines in (
        (ROOT / "src/experimental_bios_boot.s", assembler_defines(selected)),
        (ROOT / "src/cd_load_exec_probe.s", ["-D", "EXPERIMENTAL_BIOS_BOOT"]),
        (ROOT / "src/cd_read_link.s", []),
    ):
        obj = output / (source.stem + ".o")
        subprocess.run([ca65, str(source), *defines, "-o", str(obj), *debug_flags], check=True)
        objects.append(obj)
    bank, labels = output / "bank0.bin", output / "link-boot.lbl"
    subprocess.run([ld65, "-C", str(ROOT / "src/bank0.cfg"), "-o", str(bank),
                    "-Ln", str(labels),
                    "-m", str(output / "link-boot.map"), *map(str, objects)], check=True)
    rom_symbols = parse_needed_labels(labels, LINK_REQUIRED)
    missing = [n for n in LINK_REQUIRED if n not in rom_symbols]
    if missing:
        raise ValueError(f"link image symbols missing: {missing}")
    bank_bytes = bank.read_bytes()
    if len(bank_bytes) != BANK_BYTES:
        raise ValueError("link image bank is not 8 KiB")
    if bank_bytes[:0x1000] != original[:0x1000]:
        raise ValueError("link image changed the API table/override area")
    rom = output / ROM_NAME
    rom.write_bytes(bank_bytes + b"\xff" * (IMAGE_BYTES - BANK_BYTES))
    data = rom.read_bytes()
    if len(data) != IMAGE_BYTES or data[BANK_BYTES:] != b"\xff" * (IMAGE_BYTES - BANK_BYTES):
        raise ValueError("link image changed size or unused banks")
    reset_vector = int.from_bytes(data[0x1FFE:0x2000], "little")
    if reset_vector != rom_symbols["experimental_bios_reset"] or rom_symbols["experimental_bios_init"] != reset_vector + 1:
        raise ValueError("reset vector is not the experimental one-byte SEI followed by init")
    if data[reset_vector & 0x1FFF] != 0x78:
        raise ValueError("experimental reset must start with SEI")
    symbols = {n: rom_symbols[n] for n in LINK_REQUIRED}
    symbols.update({n: payload_symbols[n] for n in PAYLOAD_LABELS})
    trace_symbols = dict(symbols)
    trace_symbols.update({"dev_boot_entry": symbols["experimental_bios_init"],
                          "dev_selection_halt": symbols["experimental_bios_selection_halt"]})
    (output / "trace-labels.txt").write_text(
        "".join(f"{n} {v:04x}\n" for n, v in trace_symbols.items()), encoding="ascii")
    verify_media_identity(selected["media_identity"], cue_path)
    manifest = {
        "status": "EXPERIMENTAL-DEV-ONLY-not-compatible-not-a-bios-abi",
        "mode": mode, "rom_name": ROM_NAME, "rom_sha256": hashlib.sha256(data).hexdigest(),
        "rom_bytes": IMAGE_BYTES,
        "payload_sha256": hashlib.sha256(payload_code).hexdigest(),
        "payload_bytes": len(payload_code),
        "payload_checksum": sum(payload_code) & 0xFF,
        "payload_entry_offset": 0, "exec_address": EXEC_ADDRESS,
        "selection_contract": {"host_side_cue_analysis": True, "guest_toc": "NOT_IMPLEMENTED", **selected},
        "default_request": {"lba": selected["lba"], "count": 1},
        "sector_bytes": SECTOR_BYTES,
        "symbols": symbols, "trace_symbols": trace_symbols, "expected_mpr": EXPECTED_MPR,
        "pinned_cc65": DEPENDENCIES["cc65"]["revision"],
        "api_implementation": API_IMPLEMENTATION,
        "cd_read_api_slot_03": "NOT_IMPLEMENTED", "system_card_abi": "NO_CLAIM",
        "release_eligible": False, "dev_callable": False, "private_media_used": False,
        "cd_read_link": {"contract": "CD_READ_LINK_V1-experimental-only-not-genuine-abi",
                         "entry": "cd_read_link_entry (direct JSR, NOT a slot)",
                         "reference_jsr": "0/81", "abi_verified": "0/81",
                         "inputs": "$2400-$2402 base HI/MID/LO, $20F2-$20F4 block LO/MID/HI, "
                                   "$20F5-$20F7 window LO/MID/HI, WINDOW_LEN=16",
                         "outputs": "LINK_RES $2320-$2329, $2800 2048 bytes on success"},
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return rom
