#!/usr/bin/env python3
"""Build an opt-in experimental 256 KiB System-Card-sized DEV image.

NOT a compatible System Card BIOS. NOT a BIOS ABI implementation. NOT release eligible.
The image has original reset code that validates a host-built selection record (derived from an
original synthetic CUE's INDEX 01), then reuses this repository's synthetic-only READ(6), RAM-copy,
and payload-handoff diagnostics. It does not parse a TOC and does not consume private ROM media.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

if __package__:
    from .build_rom import API_IMPLEMENTATION, BANK_BYTES, IMAGE_BYTES, ROOT, assemble_api_objects, verify_dev_rom
    from .build_cdload_exec import DISC_SECTORS, EXEC_ADDRESS, EXPECTED_MPR, SECTOR_BYTES, encode_sector, parse_labels
    from .cue_analysis import analyze, user_sector
    from .environment import DEPENDENCIES, executable
    from .experimental_track_selection import assembler_defines, selection_from_cue, verify_media_identity
else:
    from build_rom import API_IMPLEMENTATION, BANK_BYTES, IMAGE_BYTES, ROOT, assemble_api_objects, verify_dev_rom
    from build_cdload_exec import DISC_SECTORS, EXEC_ADDRESS, EXPECTED_MPR, SECTOR_BYTES, encode_sector, parse_labels
    from cue_analysis import analyze, user_sector
    from environment import DEPENDENCIES, executable
    from experimental_track_selection import assembler_defines, selection_from_cue, verify_media_identity

MS_RES = 0x3F40
EXPERIMENTAL_BOOT_FLAG = "EXPERIMENTAL_BIOS_BOOT"
ROM_NAME = "experimental-dev-only-not-compatible-syscard-sized.pce"
REQUIRED = ("experimental_bios_reset", "experimental_bios_init", "experimental_bios_selection_halt", "experimental_selection", "cd_entry",
            "cd_buffer_ready", "cd_data_done", "cd_halt_ok", "cd_halt_fail", "cd_unexpected_interrupt", "cd_cdb",
             "cd_payload_return", "cd_set_read_lba", "cd_entry_selected", "cd_base_proto_entry")
# Experimental CD_BASE proto wiring (slot $02 only, this image only; normal ROM untouched).
# api_slot_02 fallback (5 bytes: LDX #$02; JMP api_unimplemented) is replaced by
# JMP cd_base_proto_entry + NOP pad. Whole-image diff is asserted to be exactly
# those 5 bytes. NOT a genuine ABI, NOT compatible, release_eligible=False.
SLOT02_PATCH_LEN = 5
_PROTO_PATCH_LABELS = ("api_slot_02", "api_unimplemented", "cd_base_proto_entry")
PAYLOAD_LABELS = ("payload_entry", "payload_done")


def parse_needed_labels(path: Path, names) -> dict:
    found = {}
    for address, name in re.findall(r"^al ([0-9A-Fa-f]{6}) \.([A-Za-z_][A-Za-z_0-9]*)$", path.read_text(), re.M):
        if name in names:
            value = int(address, 16)
            if found.setdefault(name, value) != value:
                raise ValueError(f"contradictory addresses for label {name}")
    return found


def _experimental_boot_units(selected):
    """Link units for an already-derived record; kept private to this builder."""
    return [
        (ROOT / "src/experimental_bios_boot.s", assembler_defines(selected)),
        (ROOT / "src/cd_load_exec_probe.s", ["-D", EXPERIMENTAL_BOOT_FLAG]),
        (ROOT / "src/cd_base_proto.s", []),
    ]


def _patch_slot02_to_proto(bank: bytes, slot02_addr: int, unimpl_addr: int, proto_addr: int) -> bytes:
    """Return bank with api_slot_02 fallback replaced by JMP proto+NOPs. Fail-closed."""
    for name, addr in (("api_slot_02", slot02_addr), ("api_unimplemented", unimpl_addr),
                        ("cd_base_proto_entry", proto_addr)):
        if type(addr) is not int or not 0xE000 <= addr <= 0xFFFF:
            raise ValueError(f"{name} address {addr!r} outside BIOS window $E000-$FFFF")
    if not 0xE100 <= slot02_addr <= 0xFFF6 - SLOT02_PATCH_LEN:
        raise ValueError(f"api_slot_02 at ${slot02_addr:04X} outside FALLBACK/code area")
    if not 0xE100 <= proto_addr <= 0xFFF6:
        raise ValueError(f"cd_base_proto_entry at ${proto_addr:04X} outside bank0 body")
    off = slot02_addr - 0xE000
    if off + SLOT02_PATCH_LEN > len(bank):
        raise ValueError("api_slot_02 offset outside bank")
    before = bytes((0xA2, 0x02, 0x4C, unimpl_addr & 0xFF, (unimpl_addr >> 8) & 0xFF))
    if bytes(bank[off:off + SLOT02_PATCH_LEN]) != before:
        raise ValueError(f"pre-patch bytes at ${slot02_addr:04X} are not the slot-02 fallback sequence")
    after = bytes((0x4C, proto_addr & 0xFF, (proto_addr >> 8) & 0xFF, 0xEA, 0xEA))
    patched = bytearray(bank)
    patched[off:off + SLOT02_PATCH_LEN] = after
    return bytes(patched)


def experimental_boot_units(cue_path):
    """Opt-in link units; derive the record from the exact CUE to be mounted."""
    if cue_path is None:
        raise ValueError("experimental boot requires the exact mounted synthetic CUE")
    selected = selection_from_cue(cue_path, cue_path)
    return _experimental_boot_units(selected)


def build_experimental_payload(output: Path, mode="debug") -> tuple[bytes, dict]:
    """Build the standalone synthetic payload before its final medium is selected."""
    if mode not in ("debug", "release"):
        raise ValueError("MODE must be debug or release")
    ca65, ld65 = executable("ca65"), executable("ld65")
    debug_flags = ["-g"] if mode == "debug" else []
    output.mkdir(parents=True, exist_ok=True)
    payload_obj = output / "cd_load_exec_payload.o"
    subprocess.run([ca65, str(ROOT / "src/cd_load_exec_payload.s"), "-o", str(payload_obj), *debug_flags], check=True)
    payload_bin, payload_labels = output / "payload.bin", output / "payload.lbl"
    subprocess.run([ld65, "-C", str(ROOT / "src/cd_load_exec_payload.cfg"), "-o", str(payload_bin),
                    "-Ln", str(payload_labels), str(payload_obj)], check=True)
    code, symbols = payload_bin.read_bytes(), parse_labels(payload_labels)
    if symbols.get("payload_entry") != EXEC_ADDRESS or not 1 <= len(code) <= 255:
        raise ValueError("payload must start at EXEC_ADDRESS and be 1..255 bytes")
    return code, symbols


def build_experimental_bios_boot(original_path: Path, output: Path, mode="debug", cue_path=None, payload_code=None, payload_symbols=None) -> Path:
    if mode not in ("debug", "release"):
        raise ValueError("MODE must be debug or release")
    if cue_path is None:
        raise ValueError("experimental boot requires the exact mounted synthetic CUE")
    if type(payload_code) is not bytes or not 1 <= len(payload_code) <= 255:
        raise ValueError("experimental boot requires a finalized 1..255-byte payload before media selection")
    selected = selection_from_cue(cue_path, cue_path)
    # The selection is made only after media finalization.  Recheck immediately
    # before producing a ROM that embeds it, rather than trusting pathname equality.
    verify_media_identity(selected["media_identity"], cue_path)
    if user_sector(cue_path, analyze(cue_path), selected["lba"]) != encode_sector(selected["lba"], payload_code):
        raise ValueError("finalized selected sector does not contain the experimental ROM payload")
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
    for source, defines in _experimental_boot_units(selected):
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
    # Experimental slot-$02 wiring: patch api_slot_02 fallback to JMP proto.
    # Pre-patch bank must match the normal DEV ROM in the API area, proving the
    # normal ROM is untouched and only this experimental image diverges.
    if bank_bytes[:0x1000] != original[:0x1000]:
        raise ValueError("experimental pre-patch bank changed the API table/override area")
    patch_addrs = parse_needed_labels(labels, _PROTO_PATCH_LABELS)
    if any(n not in patch_addrs for n in _PROTO_PATCH_LABELS):
        raise ValueError(f"experimental slot-02 patch symbols missing: {_PROTO_PATCH_LABELS}")
    patched_bank = _patch_slot02_to_proto(bank_bytes, patch_addrs["api_slot_02"],
                                            patch_addrs["api_unimplemented"],
                                            patch_addrs["cd_base_proto_entry"])
    # Whole-image diff vs the normal DEV ROM must be exactly the 5-byte patch
    # window plus the experimental BOOT area (>= $F000, offset >= 0x1000).
    # The API-area diff is asserted byte-exact below; BOOT divergence is
    # expected (experimental reset/loader/proto) and covered by the vector/SEI
    # checks that follow.
    slot02_off = patch_addrs["api_slot_02"] - 0xE000
    api_diff = [i for i in range(0x1000) if patched_bank[i] != original[i]]
    if set(api_diff) != set(range(slot02_off, slot02_off + SLOT02_PATCH_LEN)):
        raise ValueError("experimental slot-02 patch diff is not exactly the 5-byte fallback window")
    rom = output / ROM_NAME
    rom.write_bytes(patched_bank + b"\xff" * (IMAGE_BYTES - BANK_BYTES))
    data = rom.read_bytes()
    if len(data) != IMAGE_BYTES or data[BANK_BYTES:] != b"\xff" * (IMAGE_BYTES - BANK_BYTES):
        raise ValueError("experimental image changed size or unused banks")
    if data[:slot02_off] != original[:slot02_off] or data[slot02_off + SLOT02_PATCH_LEN:0x1000] != original[slot02_off + SLOT02_PATCH_LEN:0x1000]:
        raise ValueError("experimental image changed the API area outside the slot-02 patch window")
    reset_vector = int.from_bytes(data[0x1FFE:0x2000], "little")
    if reset_vector != rom_symbols["experimental_bios_reset"] or rom_symbols["experimental_bios_init"] != reset_vector + 1:
        raise ValueError("reset vector is not the experimental one-byte SEI followed by init")
    if data[reset_vector & 0x1FFF] != 0x78:
        raise ValueError("experimental reset must start with SEI")

    if payload_symbols is None:
        code, pay_symbols = build_experimental_payload(output, mode)
        if code != payload_code:
            raise ValueError("experimental ROM payload differs from the finalized media payload")
    else:
        # Custom proto-test payload (Agent-A): entry must be EXEC, 1..255 bytes,
        # symbols must carry payload_entry/payload_done. Media containment was
        # already verified via user_sector above; no standard-payload equality.
        if set(payload_symbols) != set(PAYLOAD_LABELS):
            raise ValueError(f"custom payload symbols must be {PAYLOAD_LABELS}")
        if payload_symbols.get("payload_entry") != EXEC_ADDRESS:
            raise ValueError("custom payload_entry must equal EXEC_ADDRESS")
        for name in PAYLOAD_LABELS:
            addr = payload_symbols[name]
            if type(addr) is not int or not EXEC_ADDRESS <= addr < EXEC_ADDRESS + 0x800:
                raise ValueError(f"custom {name} ${addr!r} outside payload window")
        code, pay_symbols = payload_code, dict(payload_symbols)
    symbols = {name: rom_symbols[name] for name in REQUIRED}
    symbols.update({name: pay_symbols[name] for name in PAYLOAD_LABELS})
    # The shared integration judge uses semantic trace names; these aliases point to this image's new reset labels.
    trace_symbols = dict(symbols)
    trace_symbols.update({"dev_boot_entry": symbols["experimental_bios_init"],
                          "dev_selection_halt": symbols["experimental_bios_selection_halt"]})
    (output / "trace-labels.txt").write_text("".join(f"{name} {value:04x}\n" for name, value in trace_symbols.items()), encoding="ascii")
    # Building can take long enough for an external writer to replace a test
    # input.  The integration performs the final mount-adjacent check as well.
    verify_media_identity(selected["media_identity"], cue_path)
    manifest = {
        "status": "EXPERIMENTAL-DEV-ONLY-not-compatible-not-a-bios-abi",
        "mode": mode, "rom_name": ROM_NAME, "rom_sha256": hashlib.sha256(data).hexdigest(), "rom_bytes": IMAGE_BYTES,
        "payload_sha256": hashlib.sha256(code).hexdigest(), "payload_bytes": len(code), "payload_checksum": sum(code) & 0xFF,
        "payload_entry_offset": 0, "exec_address": EXEC_ADDRESS,
        "selection_contract": {"host_side_cue_analysis": True, "guest_toc": "NOT_IMPLEMENTED", **selected},
        "default_request": {"lba": selected["lba"], "count": 1}, "sector_bytes": SECTOR_BYTES,
        "symbols": symbols, "trace_symbols": trace_symbols, "expected_mpr": EXPECTED_MPR,
        "pinned_cc65": DEPENDENCIES["cc65"]["revision"], "api_implementation": API_IMPLEMENTATION,
        "cd_read_api_slot_03": "NOT_IMPLEMENTED", "system_card_abi": "NO_CLAIM", "release_eligible": False,
        "dev_callable": False, "private_media_used": False,
        "cd_base_proto": {"contract": "CD_BASE_PROTO_V1-experimental-only-not-genuine-abi",
                            "slot": "$02", "entry": "$E006",
                            "proto_entry": f"${patch_addrs['cd_base_proto_entry']:04X}",
                            "patch": {"target": "api_slot_02", "length_bytes": SLOT02_PATCH_LEN,
                                      "before": "ldx #$02; jmp api_unimplemented",
                                      "after": f"jmp ${patch_addrs['cd_base_proto_entry']:04X} + NOP pad"},
                            "assumption": ("EXPERIMENTAL processing: LBA/index/state/MPR/RAM handling "
                                           "per src/cd_base_proto.s; NOT genuine System Card CD_BASE, "
                                           "NOT an ABI claim, NOT compatibility evidence."),
                            "reference_jsr": "0/81", "abi_verified": "0/81",
                            "state": "$2400-$2407 owned, $2408-$240A scratch"},
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return rom


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["debug", "release"], default="debug")
    parser.add_argument("--cue", type=Path, required=True,
                        help="the exact synthetic CUE that will be mounted for this image")
    args = parser.parse_args()
    base = ROOT / "build" / args.mode / "bios-dev" / "dev-only-not-compatible-syscard3.pce"
    output = ROOT / "build" / args.mode / "experimental-bios-boot"
    payload, _ = build_experimental_payload(output, args.mode)
    rom = build_experimental_bios_boot(base, output, args.mode, args.cue, payload)
    print(f"PASS experimental DEV-only 256 KiB image (NOT compatible, NOT a BIOS ABI): {rom}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"experimental-bios-boot build FAIL/BLOCKED: {error}") from error
