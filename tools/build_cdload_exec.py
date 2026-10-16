#!/usr/bin/env python3
"""Build the DEVELOPMENT-ONLY CD load-and-execute experiment (Issues #14 and #5).

Stage 0 ROM (src/cd_load_exec_probe.s) reads one synthetic CD data sector with READ(6), validates a
small header, copies the code to RAM at $3000 and jumps there. The payload (src/cd_load_exec_payload.s)
is assembled separately and stored in that sector. Not a BIOS, not CD_READ, not a System Card ABI.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

if __package__:
    from .build_rom import BANK_BYTES, IMAGE_BYTES, ROOT, load_slots, render_table, verify_image
    from .environment import DEPENDENCIES, executable
    from .make_cd_image import BIN_NAME, CUE_NAME, RAW_BYTES, build_image, sector_payload
else:
    from build_rom import BANK_BYTES, IMAGE_BYTES, ROOT, load_slots, render_table, verify_image
    from environment import DEPENDENCIES, executable
    from make_cd_image import BIN_NAME, CUE_NAME, RAW_BYTES, build_image, sector_payload

BANK_BASE = 0xE000
CODE_BASE = 0xF000
EXEC_ADDRESS = 0x3000        # EXEC in src/cd_load_exec_probe.s (work RAM via MPR1)
HEADER = b"PCEX"
HDR_BYTES = 8                # "PCEX", entry offset, code length, code checksum, reserved
SECTOR_BYTES = 2048
USER_OFFSET = 16             # user data starts 16 bytes into a MODE1/2352 frame
DEFAULT_LBA = 5
DISC_SECTORS = 16            # make_cd_image.DEFAULT_SECTORS
EXPECTED_MPR = [0xff, 0xf8, 1, 2, 31, 3, 4, 0]
ROM_LABELS = ("cd_entry", "cd_buffer_ready", "cd_data_done", "cd_halt_ok", "cd_halt_fail",
              "cd_unexpected_interrupt", "cd_cdb", "cd_payload_return")
PAYLOAD_LABELS = ("payload_entry", "payload_done")
ERROR_NAMES = {
    0: "OK", 1: "BUS_NOT_FREE_AFTER_RESET", 2: "SELECT_TIMEOUT", 3: "COMMAND_REQ_TIMEOUT",
    4: "DATA_TIMEOUT", 5: "PREMATURE_STATUS_PHASE", 6: "BAD_SCSI_STATUS_OR_MESSAGE",
    7: "STATUS_MESSAGE_TIMEOUT", 8: "BUS_NOT_FREED", 9: "INVALID_REQUEST", 10: "ACK_HANDSHAKE_TIMEOUT",
    20: "BAD_MAGIC", 21: "BAD_ENTRY_OFFSET", 22: "BAD_LENGTH", 23: "BAD_CHECKSUM",
    30: "PAYLOAD_MARKER_MISMATCH", 0xFF: "IN_PROGRESS",
}


def parse_labels(path: Path) -> dict:
    """Map label name -> address from an ld65 -Ln file (`al XXXXXX .name`)."""
    labels = {}
    for address, name in re.findall(r"^al ([0-9A-Fa-f]{6}) \.([A-Za-z_][A-Za-z_0-9]*)$", path.read_text(), re.M):
        value = int(address, 16)
        if labels.setdefault(name, value) != value:
            raise ValueError(f"contradictory addresses for label {name}")
    return labels


def encode_sector(lba: int, code: bytes, entry: int = 0) -> bytes:
    """2048-byte user data: header + code, rest of the sector is the generator's unique pattern."""
    if not 1 <= len(code) <= 255 or HDR_BYTES + len(code) > SECTOR_BYTES:
        raise ValueError("payload length must be 1..255 bytes and fit the sector")
    header = HEADER + bytes((entry, len(code), sum(code) & 0xFF, 0))
    body = header + code
    return body + sector_payload(lba)[len(body):]


def build_exec_disc(directory: Path, lba: int, code: bytes, entry: int = 0) -> Path:
    """Write a synthetic CUE/BIN (16 reference sectors) with the encoded payload sector at `lba`."""
    build_image(directory, DISC_SECTORS)
    sector = encode_sector(lba, code, entry)
    bin_path = directory / BIN_NAME
    raw = bytearray(bin_path.read_bytes())
    at = lba * RAW_BYTES + USER_OFFSET
    raw[at:at + SECTOR_BYTES] = sector
    bin_path.write_bytes(raw)
    (directory / "exec-disc-manifest.json").write_text(json.dumps({
        "lba": lba, "sector_sha256": hashlib.sha256(sector).hexdigest(),
        "payload_sha256": hashlib.sha256(code).hexdigest(), "entry_offset": entry,
        "note": "disc-manifest.json from make_cd_image is NOT updated after this patch", "third_party_content": "NONE",
    }, indent=2, sort_keys=True) + "\n")
    return directory / CUE_NAME


def build_cdload_exec(original_path: Path, output: Path, mode="debug") -> Path:
    if mode not in ("debug", "release"):
        raise ValueError("MODE must be debug or release")
    original = original_path.read_bytes()
    verify_image(original)
    ca65, ld65 = executable("ca65"), executable("ld65")
    for tool in (ca65, ld65):
        version = subprocess.run([tool, "--version"], check=True, capture_output=True, text=True)
        if DEPENDENCIES["cc65"]["revision"][:9] not in version.stdout + version.stderr:
            raise ValueError("experiment compiler/linker does not report pinned cc65 build ID")
    output.mkdir(parents=True, exist_ok=True)
    debug_flags = ["-g"] if mode == "debug" else []

    # Stage 0 ROM, linked with the API table exactly as the Issue #5 diagnostic does.
    table = output / "api_table.s"
    table.write_text(render_table(load_slots(), set()), encoding="utf-8")
    objects = []
    for source in (table, ROOT / "src/cd_load_exec_probe.s"):
        obj = output / (source.stem + ".o")
        subprocess.run([ca65, str(source), "-o", str(obj), *debug_flags], check=True)
        objects.append(obj)
    bank = output / "bank0.bin"
    rom_labels = output / "cdload.lbl"
    subprocess.run([ld65, "-C", str(ROOT / "src/bank0.cfg"), "-o", str(bank), "-Ln", str(rom_labels),
                    "-m", str(output / "cdload.map"), *map(str, objects)], check=True)
    rom_symbols = parse_labels(rom_labels)
    missing = [name for name in ROM_LABELS if name not in rom_symbols]
    if missing or rom_symbols["cd_entry"] != CODE_BASE:
        raise ValueError(f"experiment symbols/placement mismatch: missing={missing}")
    bank_bytes = bank.read_bytes()
    if len(bank_bytes) != BANK_BYTES:
        raise ValueError("experiment bank is not 8 KiB")
    rom = output / "cdload-exec-probe-not-bios.pce"
    rom.write_bytes(bank_bytes + b"\xff" * (IMAGE_BYTES - BANK_BYTES))
    data = rom.read_bytes()
    if data[:0x1000] != original[:0x1000] or data[BANK_BYTES:] != b"\xff" * (IMAGE_BYTES - BANK_BYTES):
        raise ValueError("experiment changed the API table or the unused banks")

    # Payload: separately assembled, placed at EXEC_ADDRESS, stored in the data sector.
    payload_obj = output / "cd_load_exec_payload.o"
    subprocess.run([ca65, str(ROOT / "src/cd_load_exec_payload.s"), "-o", str(payload_obj), *debug_flags], check=True)
    payload_bin = output / "payload.bin"
    payload_labels = output / "payload.lbl"
    subprocess.run([ld65, "-C", str(ROOT / "src/cd_load_exec_payload.cfg"), "-o", str(payload_bin),
                    "-Ln", str(payload_labels), str(payload_obj)], check=True)
    code = payload_bin.read_bytes()
    pay_symbols = parse_labels(payload_labels)
    if pay_symbols.get("payload_entry") != EXEC_ADDRESS or not 1 <= len(code) <= 255:
        raise ValueError("payload must start at EXEC_ADDRESS and be 1..255 bytes")
    done = pay_symbols["payload_done"] - EXEC_ADDRESS
    if not 0 <= done < len(code):
        raise ValueError("payload_done label outside the payload")

    symbols = {name: rom_symbols[name] for name in ROM_LABELS}
    symbols.update({name: pay_symbols[name] for name in PAYLOAD_LABELS})
    (output / "trace-labels.txt").write_text(
        "".join(f"{name} {value:04x}\n" for name, value in symbols.items()), encoding="ascii")
    manifest = {
        "status": "development-cd-load-exec-experiment-not-compatible-bios",
        "mode": mode, "rom_sha256": hashlib.sha256(data).hexdigest(), "rom_bytes": IMAGE_BYTES,
        "payload_sha256": hashlib.sha256(code).hexdigest(), "payload_bytes": len(code),
        "payload_checksum": sum(code) & 0xFF, "payload_entry_offset": 0, "exec_address": EXEC_ADDRESS,
        "default_request": {"lba": DEFAULT_LBA, "count": 1}, "sector_bytes": SECTOR_BYTES,
        "symbols": symbols, "expected_mpr": EXPECTED_MPR, "pinned_cc65": DEPENDENCIES["cc65"]["revision"],
        "api_implementation": "NONE", "cd_read_api_slot_03": "NOT_IMPLEMENTED",
        "final_system_card_layout": "UNKNOWN", "release_eligible": False,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return rom


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["debug", "release"], default="debug")
    args = parser.parse_args()
    base = ROOT / "build" / args.mode / "bios-dev" / "dev-only-not-compatible-syscard3.pce"
    rom = build_cdload_exec(base, ROOT / "build" / args.mode / "cdload-exec", args.mode)
    print(f"PASS CD load-and-execute experiment ROM (NOT BIOS, CD_READ NOT_IMPLEMENTED): {rom}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"cdload-exec build FAIL/BLOCKED: {error}") from error
