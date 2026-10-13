#!/usr/bin/env python3
"""Build and verify the original DEVELOPMENT-ONLY CD sector-read hardware diagnostic (Issue #5).

Separate from the base API diagnostic ROM and from the #4 startup ROM: it
implements no BIOS API, makes no System Card ABI claim and never enables release.
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
else:
    from build_rom import BANK_BYTES, IMAGE_BYTES, ROOT, load_slots, render_table, verify_image
    from environment import DEPENDENCIES, executable

BANK_BASE = 0xE000
CODE_BASE = 0xF000
CDB_BYTES = 6
REQUIRED_SYMBOLS = ("cd_entry", "cd_stack_ready", "cd_buffer_ready", "cd_command_sent", "cd_data_done",
                    "cd_halt_ok", "cd_halt_fail", "cd_unexpected_interrupt", "cd_cdb", "cd_select")
# Guest memory contract of src/cd_read_probe.s (diagnostic choices, not System Card allocation).
RESULT_ADDRESS = 0x2300
BUFFER_ADDRESS = 0x2800
BUFFER_BYTES = 6144
SECTOR_BYTES = 2048
EXPECTED_MPR = [0xff, 0xf8, 1, 2, 31, 3, 4, 0]
DEFAULT_LBA, DEFAULT_COUNT = 5, 1
ERROR_NAMES = {
    0: "OK", 1: "BUS_NOT_FREE_AFTER_RESET", 2: "SELECT_TIMEOUT", 3: "COMMAND_REQ_TIMEOUT",
    4: "DATA_TIMEOUT", 5: "PREMATURE_STATUS_PHASE", 6: "BAD_SCSI_STATUS_OR_MESSAGE",
    7: "STATUS_MESSAGE_TIMEOUT", 8: "BUS_NOT_FREED", 9: "INVALID_REQUEST", 10: "ACK_HANDSHAKE_TIMEOUT",
    0xff: "IN_PROGRESS",
}


def parse_symbols(path: Path) -> dict:
    symbols = {}
    for address, name in re.findall(r"^al ([0-9A-Fa-f]{6}) \.([A-Za-z_][A-Za-z_0-9]*)$", path.read_text(), re.M):
        if name.startswith("cd_"):
            value = int(address, 16)
            if name in symbols and symbols[name] != value:
                raise ValueError("contradictory cd label addresses")
            symbols[name] = value
    missing = [name for name in REQUIRED_SYMBOLS if name not in symbols]
    if missing or symbols["cd_entry"] != CODE_BASE:
        raise ValueError(f"cd diagnostic symbols/placement mismatch: missing={missing}")
    return {name: symbols[name] for name in REQUIRED_SYMBOLS}


def code_digest(data: bytes, symbols: dict) -> str:
    """SHA-256 of the diagnostic code, excluding the embedded READ(6) request (a test input)."""
    return hashlib.sha256(data[0x1000:request_offset(symbols)]).hexdigest()


def verify_cdread(data: bytes, original: bytes, symbols: dict, code_sha256=None) -> str:
    """Structural gate (+ optional pinned code digest); behaviour is judged only by real emulator execution."""
    verify_image(original)
    if code_sha256 is not None and code_digest(data, symbols) != code_sha256:
        raise ValueError("CD diagnostic code differs from the manifest digest")
    if len(data) != IMAGE_BYTES:
        raise ValueError("CD diagnostic must have 262144 bytes")
    if data[:0x1000] != original[:0x1000]:
        raise ValueError("CD diagnostic changed the API table/fallback/reserved overlay")
    end = symbols["cd_cdb"] + CDB_BYTES - BANK_BASE
    if not 0x1000 < end <= 0x1ff6:
        raise ValueError("CD diagnostic code does not fit before the vectors")
    if data[end:0x1ff6] != b"\xff" * (0x1ff6 - end):
        raise ValueError("unexpected bytes after the CD diagnostic")
    irq = symbols["cd_unexpected_interrupt"].to_bytes(2, "little")
    if data[0x1ff6:0x1ffe] != irq * 4 or data[0x1ffe:0x2000] != symbols["cd_entry"].to_bytes(2, "little"):
        raise ValueError("CD diagnostic IRQ/reset vectors do not match its handlers")
    if data[BANK_BYTES:] != b"\xff" * (IMAGE_BYTES - BANK_BYTES):
        raise ValueError("CD diagnostic modified unused ROM banks")
    return hashlib.sha256(data).hexdigest()


def request_offset(symbols: dict) -> int:
    return symbols["cd_cdb"] - BANK_BASE


def read_request(data: bytes, symbols: dict) -> tuple:
    """(lba, count) encoded in the READ(6) CDB embedded in the ROM."""
    cdb = data[request_offset(symbols):request_offset(symbols) + CDB_BYTES]
    return ((cdb[1] & 0x1F) << 16) | (cdb[2] << 8) | cdb[3], cdb[4]


def with_request(data: bytes, symbols: dict, lba: int, count: int) -> bytes:
    """Return a copy whose embedded READ(6) asks for another LBA/count (test input only)."""
    if not (0 <= lba < 1 << 21 and 0 <= count < 256):
        raise ValueError("READ(6) LBA is 21 bits, count 8 bits")
    patched = bytearray(data)
    at = request_offset(symbols)
    if patched[at] != 0x08:
        raise ValueError("embedded CDB is not READ(6)")
    patched[at + 1:at + 5] = bytes(((lba >> 16) & 0x1F, (lba >> 8) & 0xFF, lba & 0xFF, count))
    return bytes(patched)


def build_cdread(original_path: Path, output: Path, mode="debug") -> Path:
    if mode not in ("debug", "release"):
        raise ValueError("MODE must be debug or release")
    original = original_path.read_bytes()
    verify_image(original)
    ca65, ld65 = executable("ca65"), executable("ld65")
    for tool in (ca65, ld65):
        version = subprocess.run([tool, "--version"], check=True, capture_output=True, text=True)
        if DEPENDENCIES["cc65"]["revision"][:9] not in version.stdout + version.stderr:
            raise ValueError("CD diagnostic compiler/linker does not report pinned cc65 build ID")
    output.mkdir(parents=True, exist_ok=True)
    table = output / "api_table.s"
    table.write_text(render_table(load_slots(), set()), encoding="utf-8")
    objects = []
    for source in (table, ROOT / "src/cd_read_probe.s"):
        obj = output / (source.stem + ".o")
        subprocess.run([ca65, str(source), "-o", str(obj), *(['-g'] if mode == "debug" else [])], check=True)
        objects.append(obj)
    linked = output / "bank0.bin"
    labels = output / "cdread.lbl"
    subprocess.run([ld65, "-C", str(ROOT / "src/bank0.cfg"), "-o", str(linked),
                    "-Ln", str(labels), "-m", str(output / "cdread.map"), *map(str, objects)], check=True)
    symbols = parse_symbols(labels)
    bank = linked.read_bytes()
    if len(bank) != BANK_BYTES:
        raise ValueError("CD diagnostic bank is not 8 KiB")
    rom = output / "cdread-probe-not-bios.pce"
    rom.write_bytes(bank + b"\xff" * (IMAGE_BYTES - BANK_BYTES))
    data = rom.read_bytes()
    digest = verify_cdread(data, original, symbols)
    if read_request(data, symbols) != (DEFAULT_LBA, DEFAULT_COUNT):
        raise ValueError("embedded default READ(6) request differs from the documented default")
    (output / "trace-labels.txt").write_text(
        "".join(f"{name} {value:04x}\n" for name, value in symbols.items()), encoding="ascii")
    code_end = symbols["cd_cdb"] + CDB_BYTES - BANK_BASE
    manifest = {
        "status": "development-cd-read-diagnostic-not-compatible-bios",
        "mode": mode, "rom_sha256": digest, "rom_bytes": IMAGE_BYTES,
        "code_bytes": code_end - 0x1000,
        "code_sha256_excluding_request": code_digest(data, symbols),
        "original_api_rom_sha256": hashlib.sha256(original).hexdigest(),
        "pinned_cc65": DEPENDENCIES["cc65"]["revision"],
        "symbols": symbols, "expected_mpr": EXPECTED_MPR,
        "result_address": RESULT_ADDRESS, "buffer_address": BUFFER_ADDRESS,
        "default_request": {"lba": DEFAULT_LBA, "count": DEFAULT_COUNT},
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
    rom = build_cdread(base, ROOT / "build" / args.mode / "cdread-probe", args.mode)
    print(f"PASS original CD read diagnostic (NOT BIOS, CD_READ API NOT_IMPLEMENTED): {rom}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"cdread build FAIL/BLOCKED: {error}") from error
