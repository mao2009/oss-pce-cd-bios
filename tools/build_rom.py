#!/usr/bin/env python3
"""Build a DEV-ONLY System Card 3.0 API-entry diagnostic ROM.

Not a compatible BIOS. Every unimplemented API traps in a nonreturning loop,
with X carrying the API slot. Reset loops with X=$FF. No host-side HLE.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
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
        if not slot['candidate_name'] or '\n' in slot['candidate_name']:
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


def verify_bank(bank: bytes, allow_overrides: bool = False) -> None:
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
    if not allow_overrides:
        for slot, target in enumerate(targets):
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
    reset = bank[0x1FFE] | (bank[0x1FFF] << 8)
    if not (0xE000 <= reset < 0xFFF6):
        raise ValueError(f'Reset vector ${reset:04X} invalid')


def build(output: Path) -> Path:
    slots = load_slots()
    overridden = overrides()
    for binary in ('ca65', 'ld65'):
        if shutil.which(binary) is None:
            raise RuntimeError(f'BLOCKED: {binary} not installed; ROM build has NOT passed')
    output.mkdir(parents=True, exist_ok=True)
    generated = output / 'api_table.s'
    generated.write_text(render_table(slots, set(overridden)), encoding='utf-8')
    objects = []
    for src in [generated, ROOT / 'src' / 'boot.s', *[overridden[i] for i in sorted(overridden)]]:
        obj = output / (src.stem + '.o')
        subprocess.run(['ca65', str(src), '-o', str(obj)], check=True)
        objects.append(obj)
    first_bank = output / 'bank0.bin'
    subprocess.run(['ld65', '-C', str(ROOT / 'src' / 'bank0.cfg'), '-o', str(first_bank), *map(str, objects)], check=True)
    bank = first_bank.read_bytes()
    verify_bank(bank, allow_overrides=bool(overridden))
    target = output / 'dev-only-not-compatible-syscard3.pce'
    target.write_bytes(bank + b'\xFF' * (IMAGE_BYTES - BANK_BYTES))
    assert target.stat().st_size == IMAGE_BYTES
    print(f'DEV ONLY: ROM generated at {target}; not boot/retail verified')
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'out')
    parser.add_argument('--check-source', action='store_true')
    args = parser.parse_args()
    try:
        slots = load_slots()
        overridden = overrides()
        if args.check_source:
            text = render_table(slots, set(overridden))
            count = sum(line.startswith('    jmp api_slot_') for line in text.splitlines())
            assert count == 81
            print(f'PASS: 81 ordered entries, {81-len(overridden)} fail-closed stubs, {len(overridden)} override(s) (source-level only)')
        else:
            build(args.output)
        return 0
    except (ValueError, RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
