"""Run diagnostic System Card ROMs with official pinned Geargrafx C++ core.

No CPU simulation, commercial disc or proprietary firmware. Debug instruction
stepping reads PC/X/MPR; synthetic reset JMPs exercise candidate API entries.
The GPL-linked probe executable is local-only and excluded from CI artifacts.
"""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


class InvalidObservation(ValueError):
    """A probe observation that fails validation (missing, malformed, or not the expected protocol).

    Judged BLOCKED (no valid evidence). A valid observation that contradicts the expectation is a plain
    ValueError and is judged FAIL by the preside probe.
    """


def require_int(value, label, low, high):
    """Raise InvalidObservation unless value is an int (bool is refused) in low..high."""
    if type(value) is not int or not low <= value <= high:
        raise InvalidObservation(f"{label} must be an integer in {low:#x}..{high:#x}, got {value!r}")
    return value


def require_byte(value, label):
    """A byte as read from the emulated machine: an integer 0..0xFF."""
    return require_int(value, label, 0, 0xFF)


def require_word(value, label):
    """A 16-bit value (CPU address, program counter, VDC data word): an integer 0..0xFFFF."""
    return require_int(value, label, 0, 0xFFFF)


def require_count(value, label):
    """A non-negative counter (instructions executed). Only the type and sign are checked here."""
    return require_int(value, label, 0, 0xFFFFFFFF)


def require_bool(value, label):
    if type(value) is not bool:
        raise InvalidObservation(f"{label} must be a boolean, got {value!r}")
    return value


def require_section(row, key, label):
    """The named section of an observation (e.g. "before") must be an object."""
    section = row.get(key)
    if type(section) is not dict:
        raise InvalidObservation(f"{label}: section {key!r} is not an object: {section!r}")
    return section


def require_mpr_bytes(snapshot, label):
    """Raise InvalidObservation unless snapshot is a list of 8 bytes. Returns the snapshot."""
    if type(snapshot) is not list or len(snapshot) != 8:
        raise InvalidObservation(f"{label} MPR snapshot is not a list of 8 bytes: {snapshot!r}")
    for index, value in enumerate(snapshot):
        require_byte(value, f"{label} MPR[{index}]")
    return snapshot



ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
from environment import DEPENDENCIES, dependency_dir
from build_rom import verify_dev_rom


class ProbeBuildError(subprocess.CalledProcessError, ValueError):
    """A probe build command failed: judged BLOCKED (no probe was built), never an uncaught crash.

    Subclasses CalledProcessError (command, return code, stderr) and ValueError, so every caller that
    already catches either keeps working.
    """

    def __init__(self, returncode, cmd, stderr=''):
        super().__init__(returncode, cmd, stderr=stderr)
        self.stderr_tail = '\n'.join((stderr or '').splitlines()[-20:])

    def __str__(self):
        return f"BLOCKED: probe build failed (exit {self.returncode}): {' '.join(map(str, self.cmd))}\n{self.stderr_tail}"


def build_command(cmd, **kwargs):
    """Run one build command with captured output; a failure raises ProbeBuildError."""
    try:
        return subprocess.run(cmd, check=True, capture_output=True, text=True, **kwargs)
    except subprocess.CalledProcessError as error:
        raise ProbeBuildError(error.returncode, error.cmd, error.stderr) from error


def compile_probe(output, source_name='geargrafx_api_probe.cpp', target_name='geargrafx-api-probe'):
    if not shutil.which('g++'):
        raise ValueError('BLOCKED: missing g++')
    source = dependency_dir('geargrafx').with_name(dependency_dir('geargrafx').name + '-debugger')
    make_dir = source / 'platforms/libretro'
    # Ask upstream's pinned Makefile for its object list; do not copy core code.
    objects = build_command(['make', '-s', '--no-print-directory', '-C', str(make_dir),
                             '--eval', 'probe-objects:;@echo $(OBJECTS)', 'probe-objects']).stdout.split()
    objects = [(make_dir / p).resolve() for p in objects]
    if not objects or not all(p.is_file() for p in objects):
        raise ValueError('BLOCKED: official Geargrafx objects missing; run make setup')
    target = output / target_name
    result = build_command(['g++', '-std=c++11', '-O0', '-fno-rtti', '-D__LIBRETRO__',
                    '-DGG_DISABLE_VGMRECORDER', '-DGG_ENABLE_CDROM_CUEBIN_READAHEAD',
                    '-I' + str(source / 'src'), '-I' + str(make_dir),
                    '-I' + str(source / 'platforms/shared/dependencies/miniz'),
                    '-I' + str(source / 'platforms/shared/dependencies/json'), str(Path(__file__).with_name(source_name)),
                    *map(str, objects), '-pthread', '-lm', '-o', str(target)])
    if result.stderr:  # compiler warnings on success stay visible
        sys.stderr.write(result.stderr)
    return target


def run_probe(probe, rom, cue):
    result = subprocess.run([str(probe), str(rom), str(cue)], timeout=30,
                            check=True, capture_output=True, text=True)
    observations = [json.loads(line[4:]) for line in result.stdout.splitlines() if line.startswith('OBS ')]
    caps = [json.loads(line[4:]) for line in result.stdout.splitlines() if line.startswith('CAP ')]
    if len(caps) != 1 or caps[0].get('instruction_step') is not True or caps[0].get('cdrom_hardware') is not True or caps[0].get('cdrom_media') is not True:
        raise ValueError('instruction debugger/System Card capability missing')
    banks = [json.loads(line[5:]) for line in result.stdout.splitlines() if line.startswith('BANK ')]
    if observations and (not observations[0]['p'] & 4 or observations[0]['p'] & 8):
        raise ValueError('reset I/D flags do not match source-backed CPU contract')
    if [r['phase'] for r in observations] != ['loaded', 'cold', 'nonreturning', 'warm'] or len(banks) != 1:
        raise ValueError('missing real core observations')
    return observations, banks[0]


def assert_execution(observations, expected_pc, expected_x):
    for row in observations[1:]:
        if row['pc'] != expected_pc or row['x'] != expected_x or row['mpr'][7] != 0:
            raise ValueError(f'diagnostic checkpoint mismatch: {row}')
        if row['physical_pc'] != expected_pc - 0xe000:
            raise ValueError('bank0 physical mapping mismatch')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rom', type=Path, required=True)
    args = parser.parse_args()
    data = args.rom.read_bytes()
    verify_dev_rom(data, manifest=args.rom.parent / 'manifest.json')
    output = args.rom.parent
    probe = compile_probe(output)
    evidence = {'scope': 'synthetic-disc System Card diagnostic execution, NOT CD boot/API ABI compatibility',
                'geargrafx_revision': DEPENDENCIES['geargrafx']['revision'],
                'rom_sha256': hashlib.sha256(data).hexdigest(), 'observations': {}, 'negative_cases': [],
                'conditions': {'loader': 'LoadBiosFromBuffer(syscard=true) + LoadMedia(original.cue)',
                               'disc': '32 MODE1/2352 zero-filled sectors', 'instruction_debugger': True,
                               'geargrafx_profile': 'separate debugger-enabled source-identical build',
                               'source_archive_sha256': DEPENDENCIES['geargrafx']['sha256']},
                'api_exit': {'status': 'OBSERVED_ONE_SLOT',
                             'reason': 'slot $1E EX_GETVER returns the DEV-defined contract; the other 80 slots are nonreturning stubs'},
                'nmi': {'status': 'NOT_TESTED', 'reason': 'pinned Geargrafx HuC6280 has no NMI input or $FFFC dispatch'}}
    with tempfile.TemporaryDirectory(prefix='pce-api-probe-') as tmp:
        folder = Path(tmp)
        (folder / 'original.bin').write_bytes(bytes(2352 * 32))
        cue = folder / 'original.cue'
        cue.write_text('FILE "original.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
        reset = int.from_bytes(data[0x1ffe:0x2000], 'little')
        reset_at = reset - 0xe000
        if data[reset_at:reset_at+5] != bytes.fromhex('78 a2 ff 80 fe'):
            raise ValueError('reset diagnostic instruction contract mismatch')
        rows, bank = run_probe(probe, args.rom.resolve(), cue)
        assert_execution(rows, reset + 3, 255)
        if rows[0]['pc'] != reset or bank != {'mpr7': 1, 'physical_e000': 8192, 'byte_e000': 255}:
            raise ValueError('reset vector or padded bank mapping mismatch')
        evidence['observations']['reset'] = rows
        evidence['bank1'] = bank
        for slot in (0, 0x48, 0x50):
            target = int.from_bytes(data[slot*3+1:slot*3+3], 'little')
            handler = int.from_bytes(data[target-0xe000+3:target-0xe000+5], 'little')
            variant = bytearray(data)
            entry = 0xe000 + slot*3
            variant[reset_at:reset_at+6] = bytes((0x78, 0xa2, 0xff, 0x4c, entry & 255, entry >> 8))
            rom = folder / f'slot-{slot:02x}.pce'
            rom.write_bytes(variant)
            rows, _ = run_probe(probe, rom, cue)
            assert_execution(rows, handler, slot)
            evidence['observations'][f'slot_{slot:02X}'] = rows
            # Wrong target must fail the exact same real-core checkpoint assertion.
            bad = bytearray(variant)
            other = 1 if slot == 0 else 0
            bad[slot*3+1:slot*3+3] = data[other*3+1:other*3+3]
            rom.write_bytes(bad)
            rows, _ = run_probe(probe, rom, cue)
            assert_execution(rows, handler, other)
            try:
                assert_execution(rows, handler, slot)
            except ValueError:
                evidence['negative_cases'].append({'case': f'wrong_slot_target_{slot:02X}', 'observations': rows})
            else:
                raise ValueError('negative wrong-entry ROM unexpectedly passed')
        # Map candidate entry to bank1 FF padding via guest TAM, then bounded real stepping.
        bad = bytearray(data)
        bad[reset_at:reset_at+7] = bytes.fromhex('78 a9 01 53 80 80 fe')
        rom = folder / 'wrong-bank.pce'; rom.write_bytes(bad)
        rows, _ = run_probe(probe, rom, cue)
        if any(row['mpr'][7] != 1 for row in rows[1:]):
            raise ValueError('negative guest TAM did not select bank1')
        try:
            assert_execution(rows, reset + 3, 255)
        except ValueError:
            evidence['negative_cases'].append({'case': 'wrong_guest_mpr7', 'observations': rows})
        else:
            raise ValueError('negative bank ROM unexpectedly passed')
        from geargrafx_contracts import run_contracts
        evidence['cpu_contract_experiments'] = run_contracts(probe, data, output, cue)
    (output / 'geargrafx-api-evidence.json').write_text(json.dumps(evidence, indent=2) + '\n')
    print('PASS real Geargrafx System Card diagnostic: reset/cold/warm, slots 00/48/50, original 4 negatives; '
          '9 CPU/RAM/IRQ1/IRQ2/timer/JSR-slot experiments, 9 loader cases, 2 procedure + 3 interrupt negatives; '
          'slot 1E EX_GETVER returned its DEV-defined contract; A/X/Y/P/SP/MPR/RAM observed; NMI NOT_TESTED')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        status = 'BLOCKED' if 'BLOCKED:' in str(error) else 'FAIL'
        raise SystemExit(f'API integration {status}: {error}') from error
