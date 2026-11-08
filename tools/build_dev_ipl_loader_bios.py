#!/usr/bin/env python3
"""Build the DEV-only 256 KiB BIOS image that runs the Issue #670 multi-sector IPL loader from RESET (Issue #682).

NOT a compatible System Card BIOS. NOT a BIOS ABI. NOT CD_READ. NOT release eligible. Synthetic media only.

The loader source is tests/integration/ipl_loader_rom.s, UNCHANGED from the 8 KiB experiment (Issue #670). Only
the link layout differs: its CODE segment is placed at $F000 and its VECT segment at the bank-0 vectors, inside the
same 8 KiB bank-0 window that tools/build_rom.py uses for the DEV API table. Bank 0 is the API table + overrides +
loader; the remaining 248 KiB are $FF padding, exactly like the #668 image. The image is loaded through the
pinned Geargrafx BIOS path (LoadBiosFromBuffer), not as a HuCard.

The embedded value is the host-derived 16-bit INDEX 01 LBA of the mounted synthetic track (IPL_SEL). Payload bytes
are NOT embedded: they are read from the disc by the guest and verified by the guest (header, sums).
"""
import hashlib
import json
import subprocess
from pathlib import Path

if __package__:
    from .build_rom import BANK_BYTES, IMAGE_BYTES, ROOT, assemble_api_objects, verify_dev_rom
    from .environment import DEPENDENCIES, executable
else:
    from build_rom import BANK_BYTES, IMAGE_BYTES, ROOT, assemble_api_objects, verify_dev_rom
    from environment import DEPENDENCIES, executable

LOADER_SOURCE = ROOT / "tests/integration/ipl_loader_rom.s"
BANK0_CFG = ROOT / "src/bank0.cfg"
ROM_NAME = "dev-ipl-loader-bios-not-compatible.pce"
REQUIRED = ("reset", "fail", "cd_halt_fail", "cd_unexpected_interrupt", "irq_stub", "sig")


def bank0_cfg_text(out: Path) -> Path:
    """bank0.cfg with the loader's CODE and VECT segments placed in the same bank-0 window (no other change)."""
    text = BANK0_CFG.read_text(encoding="utf-8")
    needle = "    BOOT: load = ROM0, type = ro, start = $F000;\n"
    if text.count(needle) != 1:
        raise ValueError("bank0.cfg layout changed: expected exactly one BOOT segment line")
    text = text.replace(needle, "    CODE: load = ROM0, type = ro, start = $F000;\n")
    vectors = "    VECTORS: load = ROM0, type = ro, start = $FFF6;\n"
    if text.count(vectors) != 1:
        raise ValueError("bank0.cfg layout changed: expected exactly one VECTORS segment line")
    text = text.replace(vectors, "    VECT: load = ROM0, type = ro, start = $FFF6;\n")
    cfg = out / "bank0-ipl-loader.cfg"
    cfg.write_text(text, encoding="utf-8")
    return cfg


def parse_labels(path: Path, names) -> dict:
    import re
    found = {}
    for address, name in re.findall(r"^al ([0-9A-Fa-f]{6}) \.([A-Za-z_][A-Za-z_0-9]*)$", path.read_text(), re.M):
        if name in names:
            found.setdefault(name, int(address, 16))
    return found


def loader_manifest(sel: int, mode, variant, defines, data: bytes, symbols, rom_name: str,
                    private_input: bool = False) -> dict:
    """Manifest for a loader ROM. Synthetic builds keep the exact SEL value;
    private (commercial-derived) builds redact it and set private_media_used,
    so a manifest that escaped build/ could not leak the derived LBA nor be
    mistaken for a synthetic-media build."""
    manifest = {
        "status": ("PUBLIC-IPL-FORMAT-LOADER-BIOS-256KIB-synthetic-not-compatible-not-a-bios-abi"
                   if "IPL_PUBLIC" in defines else "DEV-ONLY-IPL-LOADER-BIOS-256KIB-not-compatible-not-a-bios-abi"),
        "ipl_format": "public" if "IPL_PUBLIC" in defines else "dev",
        "mode": mode, "variant": variant, "rom_name": rom_name,
        "rom_sha256": hashlib.sha256(data).hexdigest(), "rom_bytes": IMAGE_BYTES,
        "loader_source": "tests/integration/ipl_loader_rom.s (unchanged, Issue #670)",
        "defines": list(defines), "symbols": symbols, "pinned_cc65": DEPENDENCIES["cc65"]["revision"],
        "pinned_geargrafx": DEPENDENCIES["geargrafx"]["revision"],
        "assumption": "the reset word at file offset $1FFE is the power-on entry in bank 0 at $E000 for the "
                      "Geargrafx BIOS load path; the probe's SEI check fails loudly if this does not hold",
        "payload_embedded_in_rom": False, "system_card_abi": "NO_CLAIM", "release_eligible": False,
        "dev_callable": False,
    }
    if private_input:
        manifest["ipl_sel_lba_redacted"] = True
        manifest["private_media_used"] = True
    else:
        manifest["ipl_sel_lba"] = sel
        manifest["private_media_used"] = False
    return manifest


def build_ipl_loader_bios(original_path: Path, output: Path, sel: int, mode="debug", variant="main", defines=(),
                          private_input: bool = False):
    """Return (rom_path, symbols). `sel` is the 16-bit INDEX 01 LBA embedded in the loader (IPL_SEL).

    `private_input=True` (commercial auto-loader only): the derived SEL is
    used to build the ROM but is redacted from the manifest on disk.
    Synthetic callers must leave it False (default), preserving the exact
    synthetic manifest schema."""
    if mode not in ("debug", "release"):
        raise ValueError("MODE must be debug or release")
    if not 0 < sel < 0xFFF0:
        raise ValueError("IPL_SEL must be a 16-bit LBA with headroom for the loader's checks")
    original = original_path.read_bytes()
    verify_dev_rom(original, manifest=original_path.parent / "manifest.json")
    ca65, ld65 = executable("ca65"), executable("ld65")
    for tool in (ca65, ld65):
        version = subprocess.run([tool, "--version"], check=True, capture_output=True, text=True)
        if DEPENDENCIES["cc65"]["revision"][:9] not in version.stdout + version.stderr:
            raise ValueError("loader BIOS compiler/linker does not report the pinned cc65 build ID")
    output.mkdir(parents=True, exist_ok=True)
    debug_flags = ["-g"] if mode == "debug" else []
    objects = assemble_api_objects(output, mode)
    loader_obj = output / f"ipl_loader_{variant}.o"
    subprocess.run([ca65, str(LOADER_SOURCE), "-D", f"IPL_SEL=${sel:04X}", *defines, "-o", str(loader_obj),
                    *debug_flags], check=True)
    objects.append(loader_obj)
    bank, labels = output / f"bank0-{variant}.bin", output / f"ipl-loader-bios-{variant}.lbl"
    cfg = bank0_cfg_text(output)
    subprocess.run([ld65, "-C", str(cfg), "-o", str(bank), "-Ln", str(labels),
                    "-m", str(output / f"ipl-loader-bios-{variant}.map"), *map(str, objects)], check=True)
    symbols = parse_labels(labels, REQUIRED)
    missing = [name for name in REQUIRED if name not in symbols]
    if missing:
        raise ValueError(f"loader BIOS symbols missing: {missing}")
    bank_bytes = bank.read_bytes()
    if len(bank_bytes) != BANK_BYTES:
        raise ValueError("loader BIOS bank 0 is not 8 KiB")
    rom = output / ROM_NAME.replace(".pce", f"-{variant}.pce")
    rom.write_bytes(bank_bytes + b"\xff" * (IMAGE_BYTES - BANK_BYTES))
    data = rom.read_bytes()
    if len(data) != IMAGE_BYTES:
        raise ValueError("loader BIOS image is not 256 KiB")
    if data[:0x1000] != original[:0x1000]:
        raise ValueError("loader BIOS changed the API table/override area ($E000-$EFFF)")
    if data[BANK_BYTES:] != b"\xff" * (IMAGE_BYTES - BANK_BYTES):
        raise ValueError("loader BIOS changed the unused banks")
    for name in REQUIRED:
        if name != "sig" and not 0xF000 <= symbols[name] < 0xFFF6:
            raise ValueError(f"loader symbol {name} at {symbols[name]:04x} is outside the code window $F000-$FFF5")
    if symbols["sig"] >= 0xFFF6 or symbols["sig"] < 0xF000:
        raise ValueError("loader signature table is outside the code window")
    vector_words = [int.from_bytes(data[off:off + 2], "little") for off in range(0x1FF6, 0x2000, 2)]
    if vector_words[:3] != [symbols["irq_stub"]] * 3 or vector_words[3] != symbols["irq_stub"] or \
            vector_words[4] != symbols["reset"]:
        raise ValueError("vector words do not match irq_stub x4 + reset")
    reset_vector = int.from_bytes(data[0x1FFE:0x2000], "little")
    if reset_vector != symbols["reset"]:
        raise ValueError(f"reset vector {reset_vector:04x} is not the loader reset")
    if data[reset_vector & 0x1FFF] != 0x78:
        raise ValueError("loader reset must start with SEI (the probe contract)")
    manifest = loader_manifest(sel, mode, variant, defines, data, symbols, rom.name,
                               private_input=private_input)
    (output / f"manifest-{variant}.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return rom, symbols
