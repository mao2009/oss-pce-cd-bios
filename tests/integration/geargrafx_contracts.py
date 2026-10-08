"""Observe original CPU experiments; no claim of an implemented BIOS ABI."""
import hashlib
import json
import subprocess

from diagnostics import MPR, build_probe_images


def execute(probe, rom, cue, labels, irq='none'):
    result = subprocess.run([str(probe), str(rom), str(cue), '512', str(labels), irq],
                            check=True, capture_output=True, text=True, timeout=30)
    records = {}
    for prefix in ('OBS', 'TRACE', 'CAP', 'BANK'):
        records[prefix] = [json.loads(line[len(prefix)+1:]) for line in result.stdout.splitlines()
                           if line.startswith(prefix+' ')]
    if len(records['CAP']) != 1 or records['CAP'][0].get('instruction_step') is not True:
        raise ValueError('real instruction stepping capability missing')
    if records['CAP'][0].get('cdrom_hardware') is not True or records['CAP'][0].get('cdrom_media') is not True:
        raise ValueError('contract probe did not use System Card/CD path')
    if [r['phase'] for r in records['OBS']] != ['loaded', 'cold', 'nonreturning', 'warm']:
        raise ValueError('missing contract execution observations')
    records['rom_sha256'] = hashlib.sha256(rom.read_bytes()).hexdigest()
    return records


def trace(records, cycle, phase):
    rows = [r for r in records['TRACE'] if r['cycle'] == cycle and r['phase'] == phase]
    if len(rows) != 1:
        raise ValueError(f'missing/duplicate actual {cycle} {phase} checkpoint')
    return rows[0]


def check_terminal(records, pc, expected_ram, mpr=MPR):
    for row in records['OBS'][1:]:
        if row['pc'] != pc or row['mpr'] != mpr or row['sp'] != 255:
            raise ValueError('terminal PC/explicit MPR/stack mismatch')
        if bytes.fromhex(row['work_ram_0200_hex'])[:len(expected_ram)] != expected_ram:
            raise ValueError('real RAM checkpoint mismatch')


def check_call(records, symbols):
    check_terminal(records, symbols['call_halt'], bytes((0x11, 0x22, 0x33, 0xff, 0x33)))
    for cycle in ('cold', 'warm'):
        before = trace(records, cycle, 'call_before')
        entry = trace(records, cycle, 'call_entry')
        after = trace(records, cycle, 'call_return')
        restore = trace(records, cycle, 'restore_mpr')
        if (before['a'], before['x'], before['y'], before['sp']) != (0x11,0x22,0x33,255):
            raise ValueError('explicit caller register inputs missing')
        if entry['sp'] != 253 or after['sp'] != 255:
            raise ValueError('JSR/RTS stack is not balanced')
        for register in ('a','x','y','p'):
            if before[register] != after[register]:
                raise ValueError(f'original procedure did not restore {register}')
        if before['mpr'] != after['mpr'] or after['mpr'] != MPR:
            raise ValueError('original procedure did not restore MPR')
        if restore['mpr'][2] != 3 or restore['a'] != 1:
            raise ValueError('temporary guest bank switch not observed')
        pushed = bytes.fromhex(entry['stack_01f8_hex'])
        if int.from_bytes(pushed[6:8], 'little') != symbols['call_before']+2:
            raise ValueError('actual JSR return address bytes mismatch')


def check_irq(records, symbols, irq):
    for row in records['OBS'][1:]:
        if row['pc'] != symbols['irq_wait'] or row['sp'] != 255 or row['mpr'] != MPR:
            raise ValueError('IRQ fixture did not resume with balanced stack/mapping')
        if bytes.fromhex(row['work_ram_0200_hex'])[16] != 0x77:
            raise ValueError('IRQ guest RAM marker not observed')
    for cycle in ('cold','warm'):
        before = trace(records, cycle, 'irq_asserted')
        entry = trace(records, cycle, 'irq_entry')
        after = trace(records, cycle, 'irq_resumed')
        if before['p'] & 4 or not entry['p'] & 4 or entry['sp'] != 252 or after['sp'] != 255:
            raise ValueError('IRQ/RTI P/SP transitions missing')
        if entry['irr'] & (2 if irq == 'irq1' else 1) == 0:
            raise ValueError('requested actual interrupt source not observed')
        for register in ('a','x','y','p','mpr'):
            if before[register] != after[register]:
                raise ValueError(f'original IRQ procedure did not restore {register}')
        stack = bytes.fromhex(entry['stack_01f8_hex'])
        if stack[5] != before['p'] or int.from_bytes(stack[6:8], 'little') != symbols['irq_wait']:
            raise ValueError('actual interrupt stacked P/PC mismatch')


def run_contracts(probe, base, output, cue):
    folder = output / 'contract-probes'
    images, symbols, labels = build_probe_images(base, folder)
    evidence = {'scope': 'original CPU/memory procedures only; no executed BIOS API callee',
                'cases': {}, 'negative_cases': [], 'loader_cases': [],
                'conditions': {'requested_checkpoint_instructions':512, 'capability_instruction_before_cold':1,
                               'total_instructions_to_cold':513, 'total_instructions_to_warm':512,
                               'extra_instructions':32,
                               'irq_injection':'public AssertIRQ1/AssertIRQ2 at original irq_wait, deassert on handler entry',
                               'ram':'physical working RAM offsets 0200..023F and stack 01F8..01FF',
                               'explicit_guest_mpr':MPR}, 'symbols':symbols}
    for name, rom in images.items():
        selected_labels = labels
        if name.startswith('api_jsr'):
            slot = int(name[-2:],16)
            selected_labels = folder/(name+'.trace-labels')
            selected_labels.write_text(labels.read_text()+f'api_slot_entry {0xe000+3*slot:04x}\n')
        records = execute(probe, rom, cue, selected_labels, name if name.startswith('irq') else 'none')
        if name == 'bank':
            expected = bytes.fromhex('11 1f 22 2f a1 af 33 3f 5a a5 22 11 4c') + bytes([symbols['bank_probe'] >> 8])
            check_terminal(records, symbols['bank_halt'], expected)
            for cycle in ('cold','warm'):
                if trace(records,cycle,'mirror_read')['mpr'][2] != 32:
                    raise ValueError('physical bank20 mirror not observed')
        elif name.startswith('api_jsr'):
            slot = int(name[-2:],16)
            address = int.from_bytes(base[3*slot+1:3*slot+3],'little')
            halt = int.from_bytes(base[address-0xe000+3:address-0xe000+5],'little')
            for row in records['OBS'][1:]:
                if (row['pc'],row['x'],row['sp']) != (halt,slot,253) or row['mpr'] != MPR:
                    raise ValueError('unimplemented JSR slot returned or lost its diagnostic state')
            for cycle in ('cold','warm'):
                before = trace(records,cycle,'api_jsr'); entry = trace(records,cycle,'api_slot_entry')
                if (before['a'],before['x'],before['y'],before['sp']) != (0x11,0x22,0x33,255):
                    raise ValueError('JSR slot caller register inputs missing')
                if (entry['a'],entry['x'],entry['y'],entry['sp']) != (0x11,0x22,0x33,253):
                    raise ValueError('actual API ingress registers/stack mismatch')
                if any(r['cycle']==cycle and r['phase']=='api_call_return' for r in records['TRACE']):
                    raise ValueError('unimplemented API falsely returned')
        elif name == 'ram':
            if records['CAP'][0]['card_ram_bytes'] != 196608:
                raise ValueError('Super CD RAM configuration not available')
            check_terminal(records, symbols['ram_halt'], bytes.fromhex('81 8f 91 9f 61 6f 71 7f'),
                           [0xff,0xf8,0x80,0x87,0x68,0x7f,4,0])
        elif name == 'call': check_call(records, symbols)
        else: check_irq(records, symbols, name)
        evidence['cases'][name] = records
    # Actual loader observations. Acceptance is explicitly not firmware validity.
    for name, data, expected in (
        ('raw_payload', base, True), ('prefix_512', bytes(512)+base, True),
        ('all_ff_payload_not_firmware', b'\xff'*len(base), True),
        ('empty', b'', False), ('128k', base[:131072], False),
        ('one_byte_short', base[:-1], False), ('one_byte_extra', base+b'\xff', False),
        ('prefix_511', bytes(511)+base, False), ('512k', base+base, False)):
        path = folder / (name+'.loader-input'); path.write_bytes(data)
        result = subprocess.run([str(probe),str(path),'--load-only'], check=True, capture_output=True,
                                text=True, timeout=30)
        rows = [json.loads(line[5:]) for line in result.stdout.splitlines() if line.startswith('LOAD ')]
        if len(rows) != 1 or rows[0] != {'accepted':expected, 'input_bytes':len(data), 'execution':'NOT_RUN'}:
            raise ValueError(f'actual loader result mismatch: {name}')
        evidence['loader_cases'].append({'case':name, 'sha256':hashlib.sha256(data).hexdigest(), **rows[0]})
        path.unlink()
    # Negative original-procedure variants, never replacement BIOS APIs.
    for name, address, before, after in (
        ('missing_rts', symbols['call_rts'], b'\x60', b'\x80\xfe'),
        ('wrong_mpr_restore', symbols['restore_mpr'], b'\x53\x04', b'\x53\x08')):
        data = bytearray(images['call'].read_bytes()); offset = address-0xe000
        if data[offset:offset+len(before)] != before:
            raise ValueError('negative diagnostic instruction contract mismatch')
        data[offset:offset+len(after)] = after
        path = folder/(name+'-not-bios.pce'); path.write_bytes(data)
        records = execute(probe,path,cue,labels)
        if name == 'missing_rts':
            if any(r['pc'] != symbols['call_rts'] or r['sp'] != 253 for r in records['OBS'][1:]):
                raise ValueError('missing RTS negative did not reach its expected nonreturning state')
        else:
            for cycle in ('cold','warm'):
                row = trace(records,cycle,'call_return')
                if row['sp'] != 255 or row['mpr'][2:4] != [3,1]:
                    raise ValueError('wrong MPR negative did not execute expected bad restore')
        try: check_call(records,symbols)
        except ValueError:
            evidence['negative_cases'].append({'case':name,'observations':records})
        else: raise ValueError(f'negative CPU procedure unexpectedly passed: {name}')
    return evidence
