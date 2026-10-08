"""Original System Card CPU probes, distinct from BIOS services and release ROMs."""
import hashlib
import json
from pathlib import Path
import re
import subprocess

if __package__:
    from .environment import ROOT, DEPENDENCIES, executable
    from .build_rom import verify_image
else:
    from environment import ROOT, DEPENDENCIES, executable
    from build_rom import verify_image

MARKERS = {1: (0x11, 0x1f), 2: (0x22, 0x2f), 3: (0x33, 0x3f),
           4: (0x44, 0x4f), 31: (0xa1, 0xaf)}
MPR = [0xff, 0xf8, 1, 2, 31, 3, 4, 0]


def parse_labels(text):
    result = {}
    for address, name in re.findall(r'^al ([0-9A-Fa-f]{6}) \.([A-Za-z_][A-Za-z_0-9]*)$', text, re.M):
        value = int(address, 16)
        if name in result and result[name] != value:
            raise ValueError('contradictory diagnostic symbol')
        result[name] = value
    required = {'bank_probe', 'bank_halt', 'call_probe', 'call_before', 'call_entry', 'call_return',
                'call_rts', 'restore_mpr', 'call_halt', 'irq_probe', 'irq_wait', 'irq_entry', 'irq_exit', 'ram_probe', 'ram_halt', 'mirror_read', 'mirror_restore', 'api_call_probe', 'api_jsr', 'api_call_return', 'diagnostic_end'}
    if not required <= result.keys() or any(not 0xe800 <= result[n] < 0xf000 for n in required):
        raise ValueError('diagnostic labels missing or outside isolated overlay')
    return {name: result[name] for name in sorted(required)}


def build_probe_images(base, output, mode='debug'):
    verify_image(base)
    if mode not in ('debug', 'release'):
        raise ValueError('MODE must be debug or release')
    output = Path(output); output.mkdir(parents=True, exist_ok=True)
    ca65, ld65 = executable('ca65'), executable('ld65')
    for tool in (ca65, ld65):
        version = subprocess.run([tool, '--version'], capture_output=True, text=True, check=True)
        if DEPENDENCIES['cc65']['revision'][:9] not in version.stdout + version.stderr:
            raise ValueError('diagnostic compiler is not pinned')
    obj = output / 'contract-probe.o'
    subprocess.run([ca65, str(ROOT / 'tests/fixtures/contract-probe.s'), '-o', str(obj),
                    *(['-g'] if mode == 'debug' else [])], check=True)
    overlay = output / 'overlay.bin'; labels = output / 'contract-probe.lbl'
    subprocess.run([ld65, '-C', str(ROOT / 'tests/fixtures/contract-probe.cfg'), '-o', str(overlay),
                    '-Ln', str(labels), '-m', str(output / 'contract-probe.map'), str(obj)], check=True)
    symbols = parse_labels(labels.read_text())
    code = overlay.read_bytes()
    if len(code) != 8192 or code[:0x800] != b'\xff'*0x800 or code[0x1000:] != b'\xff'*0x1000:
        raise ValueError('diagnostic code escaped reserved test overlay')
    traces = output / 'trace-labels.txt'
    traces.write_text(''.join(f'{name} {address:04x}\n' for name, address in sorted(symbols.items())))
    images = {}
    for name in ('bank', 'call', 'irq1', 'irq2', 'ram', 'api_jsr_01', 'api_jsr_03', 'api_jsr_1E'):
        data = bytearray(base)
        data[0x800:0x1000] = code[0x800:0x1000]
        for bank, (first, last) in MARKERS.items():
            data[bank*8192] = first; data[(bank+1)*8192-1] = last
        entry = symbols[('irq' if name.startswith('irq') else 'api_call' if name.startswith('api_jsr') else name) + '_probe']
        data[0x1ffe:0x2000] = entry.to_bytes(2, 'little')
        if name.startswith('irq'):
            offset = 0x1ff8 if name == 'irq1' else 0x1ff6
            data[offset:offset+2] = symbols['irq_entry'].to_bytes(2, 'little')
        if name.startswith('api_jsr'):
            at = symbols['api_jsr']-0xe000
            if data[at:at+3] != bytes.fromhex('20 03 e0'):
                raise ValueError('API diagnostic JSR instruction mismatch')
            target = 0xe000+3*int(name[-2:],16)
            data[at+1:at+3] = target.to_bytes(2,'little')
        path = output / f'{name}-cpu-probe-not-bios.pce'
        path.write_bytes(data); images[name] = path
    manifest = {'status': 'original-cpu-experiments-not-bios-abi', 'mode': mode,
                'labels': symbols, 'explicit_mpr': MPR, 'cc65_revision': DEPENDENCIES['cc65']['revision'],
                'images': {name: {'bytes': path.stat().st_size, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
                           for name, path in images.items()}, 'api_implementation': 'NONE'}
    (output/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    return images, symbols, traces
