#!/usr/bin/env python3
"""Build a DEV-ONLY System Card 3.0 API-entry diagnostic ROM.

Not a compatible BIOS. Every unimplemented API traps in a nonreturning loop,
with X carrying the API slot. Reset loops with X=$FF. No host-side HLE.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import functools
import hashlib
import os
import re
import subprocess
import sys
import tempfile
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
EXIT_OK, EXIT_ERROR, EXIT_BLOCKED = 0, 2, 3  # BLOCKED = expected release refusal, never a build error
# What the DEV images (bios-dev and the diagnostic probes) actually implement. They link
# src/overrides/*.s through assemble_api_objects(), so "NONE" would be false. This is one
# diagnostic slot, not ABI compatibility; the ROM stays abi: UNVERIFIED / release_eligible: false.
API_IMPLEMENTATION = 'slot_1E EX_GETVER and slot_3D EX_BCDBIN (project-own, DEV only); 79 slots unimplemented'


class ReleaseBlocked(ValueError):
    """The release gate refused as designed (distinct from tool/build failures)."""


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


def assemble_api_objects(output: Path, mode: str = 'debug', root: Path = ROOT) -> list[Path]:
    """Assemble the generated API table plus every src/overrides/slot_XX.s object.

    Shared by the diagnostics that re-link bank0, so their API table and override
    area match the DEV ROM byte for byte. Returns objects in link order (generated
    table first, then overrides). Does not link or verify; the caller does that.
    """
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    ca65 = executable('ca65')
    chosen = overrides(root)
    generated = output / 'api_table.s'
    generated.write_text(render_table(load_slots(), set(chosen)), encoding='utf-8')
    objects = []
    for src in [generated, *[chosen[i] for i in sorted(chosen)]]:
        obj = output / (src.stem + '.o')
        debug = mode == 'debug' or src in chosen.values()
        subprocess.run([ca65, str(src), '-o', str(obj), *(['-g'] if debug else [])], check=True)
        objects.append(obj)
    return objects


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

REL2 = {'bra', 'bsr', 'bcc', 'bcs', 'beq', 'bne', 'bmi', 'bpl', 'bvc', 'bvs'}  # target = pc + 2 + rel8
NO_FALLTHROUGH = {'jmp', 'rts', 'rti', 'bra'}
DA65_LINE = re.compile(r'^\s+(?:\w+:\s+)?(\S+)\s*([^;]*?)\s*;\s([0-9A-F]{4})\s((?:[0-9A-F]{2}\s)+)')


def parse_dbg(text: str) -> dict[str, list[dict[str, str]]]:
    """Parse ld65 --dbgfile records into {kind: [fields]}."""
    records: dict[str, list[dict[str, str]]] = {}
    for line in text.splitlines():
        kind, _, rest = line.partition(chr(9))  # ld65 separates the kind from its fields with a tab
        fields = {k: v.strip('"') for k, v in re.findall(r'(\w+)=("[^"]*"|[^,]*)', rest)}
        records.setdefault(kind, []).append(fields)
    return records


@functools.lru_cache(maxsize=None)
def decode(raw: bytes) -> tuple[int, str, str]:
    """First instruction of `raw` per cc65's own da65 HuC6280 table: (length, mnemonic, operand).

    Reusing da65 (same pinned cc65 revision as ca65) avoids a hand-written length table that could
    become a verification blind spot. Undefined/truncated opcodes decode as '.byte'. Decoded at
    address 0 on purpose: da65 splits instructions at labels, so only a branch into its own operand
    bytes can make a valid instruction look short, and that is rejected anyway.
    """
    with tempfile.TemporaryDirectory() as tmp:
        binary = Path(tmp) / 'code.bin'
        binary.write_bytes(raw)
        text = subprocess.run([executable('da65'), '--cpu', 'huc6280', '--comments', '4', '--start-addr', '0',
                               str(binary)], capture_output=True, text=True, check=True).stdout
    for m in map(DA65_LINE.match, text.splitlines()):
        if m:
            return len(m[4].split()), m[1].lower(), m[2]
    raise ValueError('da65 produced no instruction')


def override_code(dbg: dict, slot: int) -> list[tuple[int, int]]:
    """(address, size) of every instruction span assembled from slot_XX.s, per ld65 debug info."""
    mods = {m['id'] for m in dbg.get('mod', []) if Path(m['name']).name == f'slot_{slot:02X}.o'}
    files = {f['id'] for f in dbg.get('file', []) if f.get('mod') in mods}
    segs = {s['id']: s for s in dbg.get('seg', [])}
    spans = {s['id']: s for s in dbg.get('span', [])}
    found = set()
    for line in dbg.get('line', []):
        if line.get('file') not in files:
            continue
        for sid in filter(None, line.get('span', '').split('+')):
            span = spans[sid]
            if 'type' in span:  # .byte/.word data, not an instruction
                continue
            seg = segs[span['seg']]
            found.add((seg['name'], int(seg['start'], 0) + int(span['start']), int(span['size'])))
    if any(name != 'OVERRIDES' for name, _, _ in found):
        raise ValueError(f'Override ${slot:02X} emits code outside the OVERRIDES segment')
    return sorted((address, size) for _, address, size in found)


def verify_overrides(bank: bytes, overridden: set[int], dbg: dict) -> None:
    """STRUCTURAL checks of linked override code. Does NOT prove API behaviour or ABI.

    Each override must be linked from its own slot_XX.s into OVERRIDES, be entered through the table
    JMP at its api_slot_XX label (which must be an instruction, not data/padding), keep every
    branch/JMP/JSR target inside $E000-$FFFF on a real instruction (or table/label entry), and never
    fall through into data or padding. Calls to addresses below $E000 are not checkable here.
    Instruction boundaries come from ld65 debug info and are cross-checked against the ROM bytes with da65.
    Indirect jumps and calls below $E000 are not statically checkable and are accepted.
    Limitation (fail-closed): only .byte/.word data carry a type in ld65 debug info, so raw .incbin/.res
    inside OVERRIDES look like instructions and are rejected; use .byte/.word tables instead.
    """
    labels = {s['name']: int(s['val'], 0) for s in dbg.get('sym', [])
              if s.get('type') == 'lab' and 'val' in s and s['name'].startswith('api_slot_')}
    code = {slot: override_code(dbg, slot) for slot in sorted(overridden)}
    starts = {a for spans in code.values() for a, _ in spans}
    entries = {bank[3 * i + 1] | (bank[3 * i + 2] << 8) for i in range(LAST_SLOT + 1)}  # per-slot targets
    valid = starts | entries | {FIRST_ENTRY + 3 * i for i in range(LAST_SLOT + 1)}
    for slot, spans in code.items():
        name = f'api_slot_{slot:02X}'
        if not spans:
            raise ValueError(f'Override ${slot:02X} contains no code')
        if labels.get(name) not in {a for a, _ in spans}:
            raise ValueError(f'Override ${slot:02X}: {name} is not the address of an instruction in slot_{slot:02X}.s')
        entry = bank[3 * slot + 1] | (bank[3 * slot + 2] << 8)
        if entry != labels[name]:
            raise ValueError(f'API ${slot:02X} table JMP ${entry:04X} != linked {name} ${labels[name]:04X}')
        body = {a for a, _ in spans}
        for address, size in spans:
            if not (0xE100 <= address and address + size <= 0xFFF6):
                raise ValueError(f'Override ${slot:02X} code ${address:04X} outside bank0 body')
        for address, size in spans:
            raw = bytes(bank[address - FIRST_ENTRY:address - FIRST_ENTRY + size])
            length, mnemonic, operand = decode(raw)
            # Debug info says where instructions are; the ROM bytes must decode to exactly that length.
            if mnemonic == '.byte':
                raise ValueError(f'Override ${slot:02X} ${address:04X}: undefined opcode ${raw[0]:02X} '
                                 'or instruction length mismatch (or branch into its own operand)')
            if length != size:
                raise ValueError(f'Override ${slot:02X} ${address:04X}: instruction length mismatch '
                                 f'(debug info: {size} bytes, ROM opcode {mnemonic} is {length})')
            target = None  # ponytail: 'jmp (abs)' / '(abs,x)' targets are data-dependent and not checked
            if mnemonic in ('jmp', 'jsr') and not operand.startswith('('):
                target = raw[1] | (raw[2] << 8)
            elif mnemonic in REL2 or mnemonic.startswith(('bbr', 'bbs')):
                target = address + size + (raw[-1] - 256 if raw[-1] & 0x80 else raw[-1])
            if target is not None and target >= FIRST_ENTRY and target not in valid:
                raise ValueError(f'Override ${slot:02X} ${address:04X} jumps to ${target:04X}: '
                                 'not an instruction/entry (data, padding or mid-instruction)')
            if mnemonic not in NO_FALLTHROUGH and address + size not in body:
                raise ValueError(f'Override ${slot:02X} ${address:04X} falls through into data or padding')


def verify_bank(bank: bytes, overridden: set[int] | None = None, dbg: dict | None = None,
                override_bodies: bool = True) -> None:
    """Validate the DEV layout of one bank. Never a System Card compatibility claim.

    `overridden` lists slots replaced by src/overrides/slot_XX.s. Their bodies are
    verified with ld65 debug info when `dbg` is given. If `dbg` is absent the default
    `override_bodies=True` refuses to trust an override blindly; `override_bodies=False`
    skips the body check entirely and is therefore unsound on untrusted input - it is
    only for verify_dev_rom_layout(), which pairs it with an independent rebuild.
    """
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
    if overridden:  # stubs and table are sound; now the override bodies (structure only)
        if dbg is not None:
            verify_overrides(bank, overridden, dbg)
        elif override_bodies:
            raise ValueError('override code cannot be verified without ld65 debug info')


def verify_dev_rom_layout(data: bytes, root: Path = ROOT) -> None:
    """LAYOUT check only: 81-slot JMP table, non-override stub markers, vectors and padding.

    Makes NO claim about override code bytes: any same-length byte substitution inside an
    override passes. Never use alone to accept an untrusted ROM - call verify_dev_rom(),
    which adds an independent rebuild comparison.
    """
    verify_image(data, set(overrides(root)), None, override_bodies=False)


def verify_dev_rom_structure(data: bytes, dbg: dict, root: Path = ROOT) -> None:
    """LAYOUT plus STRUCTURAL verification of override bodies using ld65 debug info.

    Detects length-changing edits, undefined opcodes, branch/JMP/JSR targets that leave the
    override or land mid-instruction, and fall-through into data. It does NOT prove byte
    authenticity: a same-length substitution that keeps every instruction boundary and does not
    fall out of the override (LDX #3 -> LDX #4, LDY #0 -> LDY #1, RTS -> RTI) passes. ld65 debug
    info is structural evidence only; never accept an untrusted ROM on this check alone.
    """
    verify_image(data, set(overrides(root)), dbg, override_bodies=True)


def _trusted_sources(root: Path) -> tuple:
    """Cache key describing the trusted inputs a DEV ROM rebuild reads.

    Covers this tool's spec file (build() always reads SLOTS_FILE = ROOT/spec/api_slots.json,
    never root/spec/) and every .s/.cfg under root/src. This is a caching fingerprint, not a
    security boundary: the source tree is inside the trust boundary. If a stat-visible change
    were missed, the worst case is comparing against a previously trusted build - it can never
    make an untrusted image pass.
    """
    inputs = [SLOTS_FILE, *(root / 'src').rglob('*.s'), *(root / 'src').rglob('*.cfg')]
    return tuple(sorted((str(path), path.stat().st_size, path.stat().st_mtime_ns)
                        for path in inputs if path.exists()))


@functools.lru_cache(maxsize=16)
def _trusted_dev_rom(root: Path, sources: tuple) -> bytes:
    """Rebuild the DEV ROM from trusted sources, memoized per (root, source fingerprint).

    The rebuild is ~17 ms, and the negative diagnostics re-verify one unchanged base ROM
    thousands of times, so caching keeps those loops fast without ever caching a result
    derived from `data`.
    """
    with tempfile.TemporaryDirectory(prefix='pce-dev-identity-') as tmp, \
            contextlib.redirect_stdout(io.StringIO()):
        return build(Path(tmp), root=root).read_bytes()


def verify_dev_rom(data: bytes, root: Path = ROOT, manifest: Path | None = None) -> None:
    """Accept a loaded DEV ROM only when it is byte-identical to an independent rebuild.

    Stage 1 runs the layout checks (fast, specific error messages). Stage 2 rebuilds the DEV
    ROM from the trusted source tree at `root` with build() and compares every byte of `data`
    against that rebuild. Debug and release builds emit identical bytes (asserted by the host
    tests), so the rebuild is a pure function of the trusted sources; the expectation never comes
    from `data` or from any manifest shipped beside it. Tampering the ROM, its manifest, or both
    is therefore rejected, including same-length instruction substitutions that
    verify_dev_rom_structure() cannot see.

    The rebuild is memoized per (root, source fingerprint); it is always built in debug mode and
    always uses this tool's spec/api_slots.json, regardless of `root`.

    `manifest`, when given and present, must be the manifest.json built next to this ROM: its
    rom_sha256 is cross-checked against the rebuild and a disagreement is rejected. Other
    manifest fields are descriptive only and never used to accept a ROM. When no manifest path
    is given, manifest-only edits are outside this function's scope, but they still cannot make
    tampered ROM bytes pass.
    """
    verify_dev_rom_layout(data, root)
    trusted = _trusted_dev_rom(root, _trusted_sources(root))
    if data != trusted:
        at = next(index for index, (got, want) in enumerate(zip(data, trusted)) if got != want)
        raise ValueError(f'DEV ROM is not byte-identical to an independent rebuild from trusted '
                         f'sources (first difference at offset ${at:04X}: ROM ${data[at]:02X}, '
                         f'rebuild ${trusted[at]:02X}); tampered or foreign input rejected')
    if manifest is not None and manifest.is_file():
        try:
            claimed = json.loads(manifest.read_text(encoding='utf-8'))['rom_sha256']
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ValueError(f'DEV ROM manifest is unreadable: {manifest}') from error
        if claimed != hashlib.sha256(trusted).hexdigest():
            raise ValueError('DEV ROM manifest rom_sha256 disagrees with the independent rebuild; '
                             'inconsistent manifest rejected (a manifest never makes a ROM trusted)')


def verify_image(data: bytes, overridden: set[int] | None = None, dbg: dict | None = None,
                 override_bodies: bool = True) -> None:
    """Validate this diagnostic layout only, never System Card compatibility."""
    if len(data) != IMAGE_BYTES:
        raise ValueError("DEV ROM must contain exactly 32 8 KiB banks without a header")
    verify_bank(data[:BANK_BYTES], overridden, dbg, override_bodies)
    if data[BANK_BYTES:] != b"\xff" * (IMAGE_BYTES - BANK_BYTES):
        raise ValueError("DEV ROM unused banks must be FF; bank ordering/padding mismatch")


def release_gate(overridden: set[int]) -> None:
    missing = sorted(set(range(81)) - overridden)
    if missing:
        raise ReleaseBlocked(f"BLOCKED: {len(missing)} unimplemented API slots: " + ",".join(f"{i:02X}" for i in missing))
    raise ReleaseBlocked("BLOCKED: override presence does not prove implementation; ROM/ABI/boot approval unavailable")


def _git(root: Path, *args: str) -> str | None:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_provenance(root: Path, overridden: dict[int, Path], includes: frozenset[Path] = frozenset()) -> dict:
    """Describe the sources actually assembled from `root`, not the tool's own checkout.

    A git SHA is recorded only when `root` is itself a repository top level;
    otherwise (copied/test trees, or a subdirectory of an unrelated repo) it is null.
    `includes` are the .include/.incbin files ca65 reported (--create-dep); they are hashed too.
    Not covered: the cc65 toolchain itself (see cc65_revision) and spec/api_slots.json's origin
    (always the tool checkout), so this is input tracking, not a full reproducible-build proof.
    """
    top = _git(root, "rev-parse", "--show-toplevel")
    tracked = top is not None and Path(top).resolve() == root.resolve()
    inputs = {'src/boot.s': root / 'src' / 'boot.s', 'src/bank0.cfg': root / 'src' / 'bank0.cfg',
              'spec/api_slots.json': SLOTS_FILE,
              **{f'src/overrides/{overridden[i].name}': overridden[i] for i in sorted(overridden)}}
    for path in sorted(includes):
        inputs[f'include:{path.relative_to(root) if path.is_relative_to(root) else path}'] = path
    return {"source_root_is_git_repo": tracked,
            "source_sha": _git(root, "rev-parse", "HEAD") if tracked else None,
            "working_tree_dirty": bool(_git(root, "status", "--porcelain")) if tracked else None,
            "source_sha256": {name: _sha256(path) for name, path in inputs.items()}}


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
    objects, includes = [], set()
    for src in [generated, root / 'src' / 'boot.s', *[overridden[i] for i in sorted(overridden)]]:
        obj = output / (src.stem + '.o')
        # -g never changes ROM bytes; overrides always get it because verification reads ld65 debug info.
        debug = mode == 'debug' or src in overridden.values()
        dep = obj.with_suffix('.d')  # ca65 lists every .include/.incbin it actually read
        subprocess.run([ca65, str(src), '-o', str(obj), '--create-dep', str(dep), *(['-g'] if debug else [])], check=True)
        if src != generated:
            includes |= {Path(d).resolve() for d in dep.read_text(encoding='utf-8').splitlines()[0].partition(':')[2].split()[1:]}
        objects.append(obj)
    first_bank = output / 'bank0.bin'
    subprocess.run([ld65, '-C', str(root / 'src' / 'bank0.cfg'), '-o', str(first_bank), '-m', str(output / 'dev.map'), '-Ln', str(output / 'dev.lbl'), '--dbgfile', str(output / 'dev.dbg'), *map(str, objects)], check=True)
    bank = first_bank.read_bytes()
    dbg = parse_dbg((output / 'dev.dbg').read_text(encoding='utf-8'))
    verify_bank(bank, set(overridden), dbg)
    target = output / 'dev-only-not-compatible-syscard3.pce'
    target.write_bytes(bank + b'\xFF' * (IMAGE_BYTES - BANK_BYTES))
    verify_image(target.read_bytes(), set(overridden), dbg)
    manifest = {**source_provenance(root, overridden, frozenset(includes)), "status": "development-diagnostic-not-compatible-bios", "mode": mode,
                "rom_bytes": IMAGE_BYTES, "rom_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                "cc65_revision": DEPENDENCIES["cc65"]["revision"],
                "overridden_slots": sorted(overridden), "unimplemented_slots": sorted(set(range(81)) - set(overridden)),
                "abi": "UNVERIFIED", "override_checks": "structural-only; behaviour and ABI not verified",
                "release_eligible": False}
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
            release_gate(set(overridden))  # always raises today; ReleaseBlocked -> exit 3
        elif args.check_source:
            text = render_table(slots, set(overridden))
            count = sum(line.startswith('    jmp api_slot_') for line in text.splitlines())
            assert count == 81
            print(f'PASS: 81 ordered entries, {81-len(overridden)} fail-closed stubs, {len(overridden)} override(s) (source-level only)')
        else:
            build(args.output or ROOT / 'build' / args.mode / 'bios-dev', args.mode)
        return EXIT_OK
    except ReleaseBlocked as exc:
        print(exc, file=sys.stderr)
        return EXIT_BLOCKED
    except (ValueError, RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return EXIT_ERROR


if __name__ == '__main__':
    raise SystemExit(main())
