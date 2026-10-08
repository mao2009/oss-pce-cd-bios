#!/usr/bin/env python3
"""Build a DEV-ONLY System Card 3.0 API-entry diagnostic ROM.

Not a compatible BIOS. Every unimplemented API traps in a nonreturning loop,
with X carrying the API slot. Reset loops with X=$FF. No host-side HLE.
"""
from __future__ import annotations

import argparse
import json
import hashlib
import os
import subprocess
import sys
from pathlib import Path

if __package__:
    from .environment import ROOT, DEPENDENCIES, executable
else:
    from environment import ROOT, DEPENDENCIES, executable
SLOTS_FILE = ROOT / 'spec' / 'api_slots.json'
FIRST_ENTRY = 0xE000
LAST_SLOT = 0x50
BANK_BYTES = 8192
IMAGE_BYTES = 256 * 1024


def load_slots(path: Path = SLOTS_FILE) -> list[dict]:
    payload = json.loads(path.read_text(encoding='utf-8'))
    slots = payload['slots']
    if payload.get('schema_version') != 1 or len(slots) != LAST_SLOT + 1:
        raise ValueError('Expected schema_version=1 and exactly 81 main API slots')
    for i, slot in enumerate(slots):
        if slot.get('id') != i or not isinstance(slot.get('candidate_name'), str):
            raise ValueError(f'Missing/out-of-order API slot {i:02X}')
        if not slot['candidate_name'] or any(c in slot['candidate_name'] for c in '\n\r\x00'):
            raise ValueError(f'Unsafe name at {i:02X}')
    return slots


def overrides(root: Path = ROOT) -> dict[int, Path]:
    result = {}
    folder = root / 'src' / 'overrides'
    for path in folder.glob('slot_*.s') if folder.exists() else ():
        try:
            num = int(path.stem.removeprefix('slot_'), 16)
        except ValueError as error:
            raise ValueError(f'Invalid override filename: {path.name}') from error
        if num < 0 or num > LAST_SLOT or path.stem != f'slot_{num:02X}':
            raise ValueError(f'Invalid override slot: {path.name}')
        if num in result:
            raise ValueError(f'Duplicate override {num:02X}')
        result[num] = path
    return result


def render_table(slots: list[dict], overridden: set[int]) -> str:
    lines = [
        '; GENERATED -- never edit directly. See spec/api_slots.json and tools/build_rom.py.',
        '; 3-byte JMP entries at $E000 + slot*3. Candidate mapping; real boot unverified.',
        '.setcpu "huc6280"',
        '.segment "APITABLE"',
    ]
    for slot in slots:
        i = slot['id']
        lines += [f'    ; ${i:02X} {slot["candidate_name"]}', f'    jmp api_slot_{i:02X}']
    lines += ['', '.segment "FALLBACK"', '; Each unimplemented call: X=slot, then nonreturning diagnostic loop.']
    for slot in slots:
        i = slot['id']
        if i not in overridden:
            lines += [f'.export api_slot_{i:02X}', f'api_slot_{i:02X}:', f'    ldx #${i:02X}', '    jmp api_unimplemented']
        else:
            lines += [f'.import api_slot_{i:02X}']
    lines += ['api_unimplemented:', '@halt:', '    bra @halt', '']
    return '\n'.join(lines)


def verify_bank(bank: bytes, overridden: set[int] | None = None) -> None:
    if len(bank) != BANK_BYTES:
        raise ValueError(f'Expected {BANK_BYTES} bytes in first bank, got {len(bank)}')
    targets = []
    for slot in range(LAST_SLOT + 1):
        offset = 3 * slot
        if bank[offset] != 0x4C:  # JMP absolute
            raise ValueError(f'API ${slot:02X} at ${FIRST_ENTRY + offset:04X} is not JMP abs')
        target = bank[offset + 1] | (bank[offset + 2] << 8)
        if not 0xE100 <= target < 0xFFF6:
            raise ValueError(f'API ${slot:02X} target ${target:04X} outside allowed bank0 body')
        targets.append(target)
    if len(set(targets)) != len(targets):
        raise ValueError('Main API slots share implementation entry points; use per-slot trampolines')
    overridden = overridden or set()
    if not overridden <= set(range(81)):
        raise ValueError('override IDs outside 81-slot table')
    for slot, target in enumerate(targets):
        if slot in overridden:
            continue
        at = target - FIRST_ENTRY
        if bank[at:at+2] != bytes((0xA2, slot)):  # LDX #$slot
            raise ValueError(f'API ${slot:02X} lacks unique debug marker')
        if bank[at+2] != 0x4C:
            raise ValueError(f'API ${slot:02X} does not JMP to diagnostic handler')
        handler = bank[at+3] | (bank[at+4] << 8)
        if not 0xE100 <= handler < 0xFFF6:
            raise ValueError(f'API ${slot:02X} handler points outside ROM0')
        pos = handler - FIRST_ENTRY
        if bank[pos:pos+2] != b'\x80\xfe':  # BRA -2 (nonreturning)
            raise ValueError(f'API ${slot:02X} handler does not stop in BRA loop')
    for offset in range(0x1ff6, 0x2000, 2):
        vector = int.from_bytes(bank[offset:offset+2], 'little')
        if not 0xE100 <= vector <= 0xFFF1:
            raise ValueError('diagnostic vector outside body')
    reset = bank[0x1FFE] | (bank[0x1FFF] << 8)
    if not (0xE000 <= reset < 0xFFF6):
        raise ValueError(f'Reset vector ${reset:04X} invalid')
    if any(int.from_bytes(bank[p:p+2], 'little') != reset for p in range(0x1ff6, 0x2000, 2)):
        raise ValueError('all development interrupt vectors must reach diagnostic reset')
    at = reset - FIRST_ENTRY
    if bank[at:at+5] != bytes.fromhex('78 a2 ff 80 fe'):
        raise ValueError('Reset diagnostic must SEI, LDX FF, BRA self')


def verify_image(data: bytes, overridden: set[int] | None = None) -> None:
    """Validate this diagnostic layout only, never System Card compatibility."""
    if len(data) != IMAGE_BYTES:
        raise ValueError("DEV ROM must contain exactly 32 8 KiB banks without a header")
    verify_bank(data[:BANK_BYTES], overridden)
    if data[BANK_BYTES:] != b"\xff" * (IMAGE_BYTES - BANK_BYTES):
        raise ValueError("DEV ROM unused banks must be FF; bank ordering/padding mismatch")


def release_gate(overridden: set[int]) -> None:
    missing = sorted(set(range(81)) - overridden)
    if missing:
        raise ValueError(f"BLOCKED: {len(missing)} unimplemented API slots: " + ",".join(f"{i:02X}" for i in missing))
    raise ValueError("BLOCKED: override presence does not prove implementation; ROM/ABI/boot approval unavailable")


def build(output: Path, mode="debug", root: Path = ROOT) -> Path:
    slots = load_slots()
    if mode not in ("debug", "release"):
        raise ValueError("MODE must be debug or release")
    overridden = overrides(root)
    ca65, ld65 = executable("ca65"), executable("ld65")
    for tool in (ca65, ld65):
        v = subprocess.run([tool, "--version"], capture_output=True, text=True, check=True)
        if DEPENDENCIES["cc65"]["revision"][:9] not in v.stdout + v.stderr:
            raise ValueError("assembler/linker does not report pinned cc65 build ID")
    output.mkdir(parents=True, exist_ok=True)
    generated = output / 'api_table.s'
    generated.write_text(render_table(slots, set(overridden)), encoding='utf-8')
    objects = []
    for src in [generated, root / 'src' / 'boot.s', *[overridden[i] for i in sorted(overridden)]]:
        obj = output / (src.stem + '.o')
        subprocess.run([ca65, str(src), '-o', str(obj), *(['-g'] if mode == 'debug' else [])], check=True)
        objects.append(obj)
    first_bank = output / 'bank0.bin'
    subprocess.run([ld65, '-C', str(root / 'src' / 'bank0.cfg'), '-o', str(first_bank), '-m', str(output / 'dev.map'), '-Ln', str(output / 'dev.lbl'), *map(str, objects)], check=True)
    bank = first_bank.read_bytes()
    verify_bank(bank, overridden=set(overridden))
    target = output / 'dev-only-not-compatible-syscard3.pce'
    target.write_bytes(bank + b'\xFF' * (IMAGE_BYTES - BANK_BYTES))
    verify_image(target.read_bytes(), set(overridden))
    source_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                capture_output=True, text=True, check=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                               capture_output=True, text=True, check=True).stdout)
    manifest = {"source_sha": source_sha, "working_tree_dirty": dirty, "status": "development-diagnostic-not-compatible-bios", "mode": mode,
                "rom_bytes": IMAGE_BYTES, "rom_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                "cc65_revision": DEPENDENCIES["cc65"]["revision"],
                "overridden_slots": sorted(overridden), "unimplemented_slots": sorted(set(range(81)) - set(overridden)),
                "abi": "UNVERIFIED", "release_eligible": False}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f'DEV ONLY: ROM generated at {target}; not boot/retail verified')
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--mode', default=os.environ.get('MODE', 'debug'))
    parser.add_argument('--release-gate', action='store_true')
    parser.add_argument('--check-source', action='store_true')
    args = parser.parse_args()
    try:
        slots = load_slots()
        overridden = overrides()
        if args.release_gate:
            release_gate(set(overridden))
        elif args.check_source:
            text = render_table(slots, set(overridden))
            count = sum(line.startswith('    jmp api_slot_') for line in text.splitlines())
            assert count == 81
            print(f'PASS: 81 ordered entries, {81-len(overridden)} fail-closed stubs, {len(overridden)} override(s) (source-level only)')
        else:
            build(args.output or ROOT / 'build' / args.mode / 'bios-dev', args.mode)
        return 0
    except (ValueError, RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
