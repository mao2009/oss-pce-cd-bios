#!/usr/bin/env python3
"""Build the original DEVELOPMENT-ONLY CD-DA observation diagnostic (Issue #15).

Separate from every other diagnostic ROM. It issues raw SCSI $D8/$DA/$D9/$DD on the CD bus so the
pinned Geargrafx CD-DA state can be observed; it implements no BIOS API and makes no CD_PLAY claim.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

if __package__:
    from .build_rom import BANK_BYTES, IMAGE_BYTES, ROOT, assemble_api_objects, verify_dev_rom
    from .environment import DEPENDENCIES, executable
else:
    from build_rom import BANK_BYTES, IMAGE_BYTES, ROOT, assemble_api_objects, verify_dev_rom
    from environment import DEPENDENCIES, executable

SOURCE = ROOT / "src/cdda_probe.s"
REQUIRED_SYMBOLS = ("cdda_entry", "cdda_cmd_done", "cdda_wait_done", "cdda_halt_ok", "cdda_halt_fail",
                    "cdda_unexpected_interrupt", "cdda_table")
# Fixture geometry the command table is written for (16 MODE1 data sectors, then audio track 02).
DATA_SECTORS, AUDIO_SECTORS, AUDIO_START_LBA = 16, 64, 16


def parse_symbols(path: Path) -> dict:
    symbols = {}
    for address, name in re.findall(r"^al ([0-9A-Fa-f]{6}) \.(cdda_[A-Za-z_0-9]*)$", path.read_text(), re.M):
        symbols[name] = int(address, 16)
    missing = [name for name in REQUIRED_SYMBOLS if name not in symbols]
    if missing or symbols["cdda_entry"] != 0xF000:
        raise ValueError(f"cdda symbols/placement mismatch: missing={missing}")
    return {name: symbols[name] for name in REQUIRED_SYMBOLS}


def verify_cdda(data: bytes, original: bytes, symbols: dict) -> str:
    verify_dev_rom(original)
    if len(data) != IMAGE_BYTES or data[:0x1000] != original[:0x1000]:
        raise ValueError("CD-DA diagnostic size or API table/fallback overlay differs")
    irq = symbols["cdda_unexpected_interrupt"].to_bytes(2, "little")
    if data[0x1ff6:0x1ffe] != irq * 4 or data[0x1ffe:0x2000] != symbols["cdda_entry"].to_bytes(2, "little"):
        raise ValueError("CD-DA diagnostic vectors do not match its handlers")
    if data[BANK_BYTES:] != b"\xff" * (IMAGE_BYTES - BANK_BYTES):
        raise ValueError("CD-DA diagnostic modified unused ROM banks")
    return hashlib.sha256(data).hexdigest()


def build_cdda(original_path: Path, output: Path, mode="debug") -> Path:
    if mode not in ("debug", "release"):
        raise ValueError("MODE must be debug or release")
    original = original_path.read_bytes()
    verify_dev_rom(original)
    ca65, ld65 = executable("ca65"), executable("ld65")
    for tool in (ca65, ld65):
        version = subprocess.run([tool, "--version"], check=True, capture_output=True, text=True)
        if DEPENDENCIES["cc65"]["revision"][:9] not in version.stdout + version.stderr:
            raise ValueError("CD-DA diagnostic compiler/linker does not report pinned cc65 build ID")
    output.mkdir(parents=True, exist_ok=True)
    objects = assemble_api_objects(output, mode)
    for source in (SOURCE,):
        obj = output / (source.stem + ".o")
        subprocess.run([ca65, str(source), "-o", str(obj), *(['-g'] if mode == "debug" else [])], check=True)
        objects.append(obj)
    linked, labels = output / "bank0.bin", output / "cdda.lbl"
    subprocess.run([ld65, "-C", str(ROOT / "src/bank0.cfg"), "-o", str(linked),
                    "-Ln", str(labels), "-m", str(output / "cdda.map"), *map(str, objects)], check=True)
    symbols = parse_symbols(labels)
    bank = linked.read_bytes()
    if len(bank) != BANK_BYTES:
        raise ValueError("CD-DA diagnostic bank is not 8 KiB")
    rom = output / "cdda-probe-not-bios.pce"
    rom.write_bytes(bank + b"\xff" * (IMAGE_BYTES - BANK_BYTES))
    digest = verify_cdda(rom.read_bytes(), original, symbols)
    (output / "trace-labels.txt").write_text(
        "".join(f"{name} {value:04x}\n" for name, value in symbols.items()), encoding="ascii")
    manifest = {
        "status": "development-cdda-observation-diagnostic-not-compatible-bios",
        "mode": mode, "rom_sha256": digest, "original_api_rom_sha256": hashlib.sha256(original).hexdigest(),
        "pinned_cc65": DEPENDENCIES["cc65"]["revision"], "symbols": symbols,
        "fixture": {"data_sectors": DATA_SECTORS, "audio_sectors": AUDIO_SECTORS, "audio_start_lba": AUDIO_START_LBA},
        "api_implementation": "NONE", "cd_play_api_slot_06": "NOT_IMPLEMENTED", "release_eligible": False,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return rom


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["debug", "release"], default="debug")
    args = parser.parse_args()
    base = ROOT / "build" / args.mode / "bios-dev" / "dev-only-not-compatible-syscard3.pce"
    rom = build_cdda(base, ROOT / "build" / args.mode / "cdda-probe", args.mode)
    print(f"PASS original CD-DA observation diagnostic (NOT BIOS, CD_PLAY API NOT_IMPLEMENTED): {rom}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"cdda build FAIL/BLOCKED: {error}") from error
