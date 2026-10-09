#!/usr/bin/env python3
"""Build and verify an original DEVELOPMENT-ONLY System Card startup checkpoint.

This deliberately does not change the base API diagnostic ROM, implement any
BIOS service, assert historical memory allocation, or enable release.
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

# Exact independently authored fixture bytes; intentionally not a generic BIOS verifier.
STARTUP_CODE = bytes.fromhex(
    "78 d8 d4 "
    "a9 ff 53 01 a9 f8 53 02 a9 01 53 04 a9 02 53 08 "
    "a9 1f 53 10 a9 03 53 20 a9 04 53 40 a9 00 53 80 "
    "a2 ff 9a "
    "a9 42 8d 00 22 a9 4f 8d 01 22 8d 02 22 "
    "a9 54 8d 03 22 a9 21 8d 04 22 "
    "a2 b0 80 fe a2 ee 80 fe"
)
SYMBOL_OFFSETS = {
    "startup_entry": 0,
    "startup_mpr_ready": 35,
    "startup_stack_ready": 38,
    "startup_marker_write": 40,
    "startup_checkpoint": len(STARTUP_CODE) - 6,
    "startup_unexpected_interrupt": len(STARTUP_CODE) - 4,
}
EXPECTED_MPR = [0xff, 0xf8, 1, 2, 31, 3, 4, 0]
MARKER = b"BOOT!"


def verify_startup(data: bytes, original: bytes) -> str:
    """Enforce only the tested diagnostic layout and keep API table unchanged."""
    verify_image(original)
    if len(data) != IMAGE_BYTES:
        raise ValueError("startup diagnostic must have 262144 bytes")
    if data[:0x1000] != original[:0x1000]:
        raise ValueError("startup diagnostic changed the API table/fallback/reserved overlay")
    code = data[0x1000:0x1000 + len(STARTUP_CODE)]
    if code != STARTUP_CODE:
        raise ValueError("startup reset/MPR/stack/marker/stop instructions differ")
    if data[0x1000 + len(STARTUP_CODE):0x1ff6] != b"\xff" * (0xff6 - len(STARTUP_CODE)):
        raise ValueError("unexpected bytes after the startup checkpoint")
    irq = (0xf000 + SYMBOL_OFFSETS["startup_unexpected_interrupt"]).to_bytes(2, "little")
    if data[0x1ff6:0x1ffe] != irq * 4 or data[0x1ffe:0x2000] != b"\x00\xf0":
        raise ValueError("startup IRQ/reset vectors do not match isolated diagnostic handlers")
    if data[BANK_BYTES:] != b"\xff" * (IMAGE_BYTES - BANK_BYTES):
        raise ValueError("startup diagnostic modified unused ROM banks")
    return hashlib.sha256(data).hexdigest()


def parse_symbols(path: Path) -> dict:
    symbols = {}
    for address, name in re.findall(r"^al ([0-9A-Fa-f]{6}) \.([A-Za-z_][A-Za-z_0-9]*)$", path.read_text(), re.M):
        if name.startswith("startup_"):
            if name in symbols:
                raise ValueError("duplicate startup label")
            symbols[name] = int(address, 16)
    expected = {key: 0xf000 + offset for key, offset in SYMBOL_OFFSETS.items()}
    if symbols != expected:
        raise ValueError(f"startup symbols/placement mismatch: {symbols}")
    return symbols


def build_startup(original_path: Path, output: Path, mode="debug") -> Path:
    if mode not in ("debug", "release"):
        raise ValueError("MODE must be debug or release")
    original = original_path.read_bytes()
    verify_image(original)
    ca65, ld65 = executable("ca65"), executable("ld65")
    for tool in (ca65, ld65):
        version = subprocess.run([tool, "--version"], check=True, capture_output=True, text=True)
        if DEPENDENCIES["cc65"]["revision"][:9] not in version.stdout + version.stderr:
            raise ValueError("startup compiler/linker does not report pinned cc65 build ID")
    output.mkdir(parents=True, exist_ok=True)
    table = output / "api_table.s"
    table.write_text(render_table(load_slots(), set()), encoding="utf-8")
    objects = []
    for source in (table, ROOT / "src/startup_probe.s"):
        obj = output / (source.stem + ".o")
        subprocess.run([ca65, str(source), "-o", str(obj), *(['-g'] if mode == "debug" else [])], check=True)
        objects.append(obj)
    linked = output / "bank0.bin"
    labels = output / "startup.lbl"
    subprocess.run([ld65, "-C", str(ROOT / "src/bank0.cfg"), "-o", str(linked),
                    "-Ln", str(labels), "-m", str(output / "startup.map"),
                    *map(str, objects)], check=True)
    symbols = parse_symbols(labels)
    bank = linked.read_bytes()
    if len(bank) != BANK_BYTES:
        raise ValueError("startup bank is not 8 KiB")
    rom = output / "startup-probe-not-bios.pce"
    rom.write_bytes(bank + b"\xff" * (IMAGE_BYTES - BANK_BYTES))
    digest = verify_startup(rom.read_bytes(), original)
    (output / "trace-labels.txt").write_text(
        "".join(f"{name} {value:04x}\n" for name, value in symbols.items()), encoding="ascii")
    manifest = {
        "status": "development-startup-checkpoint-not-compatible-bios",
        "mode": mode, "rom_sha256": digest, "rom_bytes": IMAGE_BYTES,
        "original_api_rom_sha256": hashlib.sha256(original).hexdigest(),
        "pinned_cc65": DEPENDENCIES["cc65"]["revision"],
        "startup_symbols": symbols, "expected_mpr": EXPECTED_MPR,
        "marker": MARKER.hex(), "api_implementation": "NONE",
        "final_system_card_layout": "UNKNOWN", "release_eligible": False,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return rom


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["debug", "release"], default="debug")
    args = parser.parse_args()
    base = ROOT / "build" / args.mode / "bios-dev" / "dev-only-not-compatible-syscard3.pce"
    output = ROOT / "build" / args.mode / "startup-probe"
    rom = build_startup(base, output, args.mode)
    print(f"PASS original diagnostic startup image (NOT BIOS): {rom}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"startup build FAIL/BLOCKED: {error}") from error
