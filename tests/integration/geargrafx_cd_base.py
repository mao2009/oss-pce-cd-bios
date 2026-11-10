"""Issue #11: execute candidate CD_BASE entry $E006 of the DEV ROM on the pinned Geargrafx core.

Runs two synthetic fixtures (tools/cd_base_probe.py): the original INDEX01=150 disc and the
INDEX01=0 control (no pregap). Both jump from reset to $E006 and record whether an executable
callee exists. The DEV ROM has none (fail-closed fallback), so the judge reports every ledger
question BLOCKED for both fixtures. No System Card ROM is loaded or required.
"""
import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_rom import verify_dev_rom  # noqa: E402
from environment import DEPENDENCIES  # noqa: E402
from geargrafx_api import assert_execution, compile_probe, run_probe  # noqa: E402
import cd_base_probe  # noqa: E402

SLOT = 0x02


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rom', type=Path, required=True)
    args = parser.parse_args()
    data = args.rom.read_bytes()
    verify_dev_rom(data, manifest=args.rom.parent / 'manifest.json')
    output = args.rom.parent
    probe = compile_probe(output)
    runs = {}
    with tempfile.TemporaryDirectory(prefix='pce-cd-base-') as tmp:
        folder = Path(tmp)
        fixtures = (('index01_150', cd_base_probe.build_fixture, cd_base_probe.CUE_NAME,
                     cd_base_probe.INDEX01_LBA),
                    ('index01_0', cd_base_probe.build_zero_fixture, cd_base_probe.ZERO_CUE_NAME, 0))
        for name, build, cue_name, index01 in fixtures:
            fixture = build(folder)
            reset_at = int.from_bytes(data[0x1ffe:0x2000], 'little') - 0xe000
            target = int.from_bytes(data[SLOT*3+1:SLOT*3+3], 'little')
            handler = int.from_bytes(data[target-0xe000+3:target-0xe000+5], 'little')
            entry = 0xe000 + SLOT*3
            variant = bytearray(data)
            variant[reset_at:reset_at+6] = bytes((0x78, 0xa2, 0xff, 0x4c, entry & 255, entry >> 8))
            rom = folder / f'slot-02-{name}.pce'
            rom.write_bytes(variant)
            rows, _ = run_probe(probe, rom, folder / cue_name)
            assert_execution(rows, handler, SLOT)
            record = cd_base_probe.record_from_api_rows(rows, handler, index01=index01)
            verdict = cd_base_probe.judge(record)
            if {v['status'] for v in verdict.values()} != {'BLOCKED'}:
                raise ValueError(f'unexpected verdict for a nonreturning entry ({name}): {verdict}')
            runs[name] = {'fixture_track': fixture['tracks'][0], 'observations': rows,
                          'record': record, 'verdict': verdict}
    evidence = {'scope': 'presence of an executable CD_BASE callee in the DEV ROM; NOT a System Card ABI',
                'geargrafx_revision': DEPENDENCIES['geargrafx']['revision'],
                'rom_sha256': hashlib.sha256(data).hexdigest(), 'entry': f'${entry:04X}',
                'fallback_handler': f'${handler:04X}', 'runs': runs}
    (output / 'geargrafx-cd-base-evidence.json').write_text(json.dumps(evidence, indent=2) + '\n')
    print(f'PASS real Geargrafx: ${entry:04X} -> fallback ${handler:04X}, X=$02, nonreturning (cold/+32/warm) '
          f'with INDEX01=150 and INDEX01=0 control fixtures; ledger questions (1)-(6) BLOCKED')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError) as error:
        status = 'BLOCKED' if 'BLOCKED:' in str(error) else 'FAIL'
        raise SystemExit(f'CD_BASE integration {status}: {error}') from error
