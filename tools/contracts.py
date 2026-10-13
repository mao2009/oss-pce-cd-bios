"""Validate research bookkeeping, never certify a BIOS implementation."""
import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIELDS = {'arguments', 'returns', 'clobbered', 'preserved', 'stack', 'mpr',
          'ram', 'flags', 'irq', 'errors', 'synchronization'}
CLAIM_STATUSES = {'UNKNOWN', 'HYPOTHESIS'}


def validate_contracts(data, inventory, inventory_text):
    """Return normally for consistent unexecuted research; raise ValueError otherwise.

    This schema deliberately cannot represent VERIFIED. Future implementation
    work must introduce an evidence schema before promoting any claim.
    """
    def require(ok, message):
        if not ok:
            raise ValueError(message)

    require(isinstance(data, dict) and data.get('schema_version') == 1,
            'unsupported contract schema')
    require(data.get('inventory') == 'spec/api_slots.json' and
            data.get('source_inventory') == 'docs/api-inventory.md', 'inventory references')
    require(isinstance(data.get('target'), str) and data['target'], 'target required')
    require(inventory.get('schema_version') == 1, 'unsupported inventory schema')
    slots = inventory.get('slots', [])
    require(len(slots) == 81 and [s.get('id') for s in slots] == list(range(81)),
            'inventory must contain 81 ordered slots')
    sources = data.get('sources')
    require(isinstance(sources, dict) and sources, 'sources required')
    for key, source in sources.items():
        require(isinstance(source, dict), f'{key}: source object required')
        require(all(isinstance(source.get(f), str) and source[f]
                    for f in ('repository', 'revision', 'path', 'url', 'sha256', 'kind')),
                f'{key}: incomplete source')
        require(re.fullmatch(r'[0-9a-f]{40}', source['revision']) is not None and
                re.fullmatch(r'[0-9a-f]{64}', source['sha256']) is not None,
                f'{key}: source pin/hash invalid')
        require(source['repository'].startswith('https://github.com/') and
                source['url'] == f"{source['repository']}/blob/{source['revision']}/{source['path']}",
                f'{key}: source URL must match pin/path')
        require(source['kind'] in {'CALLER_SOURCE', 'HLE_SOURCE', 'EMULATOR_SOURCE'} and
                source.get('provenance_status') == 'PENDING', f'{key}: invalid source status')

    def refs(value, context, required=False):
        require(isinstance(value, list) and all(isinstance(x, str) for x in value),
                f'{context}: source references must be a list')
        require(len(value) == len(set(value)) and all(x in sources for x in value),
                f'{context}: invalid source reference')
        require(not required or bool(value), f'{context}: source required')

    md_rows = re.findall(r'^\| `\$([0-9A-F]{2})` \| `\$([0-9A-F]{4})` \| `([^`]+)`',
                         inventory_text, re.MULTILINE)
    require(len(md_rows) == 81, 'Markdown inventory must contain 81 main rows')
    apis = data.get('apis')
    require(isinstance(apis, list) and len(apis) == 81, 'contracts must contain 81 APIs')
    for i, (api, slot, md) in enumerate(zip(apis, slots, md_rows)):
        require(isinstance(api, dict), f'slot {i}: API object required')
        require(type(api.get('slot')) is int and api['slot'] == i and
                api.get('candidate_name') == slot.get('candidate_name'), f'slot {i}: inventory mismatch')
        entry = f'0x{0xE000 + 3*i:04X}'
        require(api.get('candidate_entry') == entry and int(md[0], 16) == i and
                '0x'+md[1] == entry and md[2] == api['candidate_name'], f'slot {i}: entry/name mismatch')
        require(api.get('mapping_status') == 'HYPOTHESIS' and
                api.get('provenance_status') == 'PENDING' and
                api.get('implementation_status') == 'NOT_STARTED', f'slot {i}: invalid research status')
        require(api.get('confidence') in {'UNKNOWN', 'SOURCE_SUPPORTED'}, f'slot {i}: confidence')
        require(api.get('verification') == {'status': 'NOT_RUN', 'executed_callee': False, 'evidence': []},
                f'slot {i}: no executed callee; verification must be NOT_RUN')
        refs(api.get('sources'), f'slot {i}')
        refs(api.get('mapping_sources'), f'slot {i} mapping')
        require(set(api['mapping_sources']) <= set(api['sources']), f'slot {i}: mapping sources missing')
        observations = api.get('name_observations')
        require(isinstance(observations, list), f'slot {i}: name observations required')
        for obs in observations:
            require(isinstance(obs, dict) and isinstance(obs.get('name'), str) and obs['name'] and
                    isinstance(obs.get('locator'), str) and obs['locator'] and
                    obs.get('status') == 'HYPOTHESIS' and obs.get('entry') in {entry, 'UNKNOWN'},
                    f'slot {i}: invalid name observation')
            refs([obs.get('source')], f'slot {i} name', True)
            require(obs['source'] in api['sources'], f'slot {i}: observation source missing')
        contract = api.get('contract')
        require(isinstance(contract, dict) and set(contract) == FIELDS, f'slot {i}: contract fields')
        for field, claim in contract.items():
            context = f'slot {i} {field}'
            require(isinstance(claim, dict) and set(claim) <=
                    {'status', 'value', 'sources', 'confidence', 'registers'}, f'{context}: claim fields')
            require(claim.get('status') in CLAIM_STATUSES, f'{context}: invalid claim status')
            require(isinstance(claim.get('value'), str) and claim['value'], f'{context}: value required')
            refs(claim.get('sources'), context, claim['status'] == 'HYPOTHESIS')
            require(set(claim['sources']) <= set(api['sources']), f'{context}: API source union incomplete')
            if claim['status'] == 'UNKNOWN':
                require(claim['value'] == 'UNKNOWN' and claim.get('confidence') == 'UNKNOWN' and
                        claim['sources'] == [] and not claim.get('registers'), f'{context}: invalid UNKNOWN')
            else:
                require(claim['value'] != 'UNKNOWN' and claim.get('confidence') == 'SOURCE_SUPPORTED',
                        f'{context}: invalid hypothesis')
            if 'registers' in claim:
                registers = claim['registers']
                require(field in {'preserved', 'clobbered'} and isinstance(registers, list) and
                        all(r in {'A', 'X', 'Y', 'S', 'P'} for r in registers) and
                        len(registers) == len(set(registers)), f'{context}: register list invalid')
        preserved = contract['preserved']
        clobbered = contract['clobbered']
        require(not (set(preserved.get('registers', [])) & set(clobbered.get('registers', []))),
                f'slot {i}: contradictory preserved/clobbered registers')
        require(not (preserved['status'] != 'UNKNOWN' and clobbered['status'] != 'UNKNOWN' and
                     preserved['value'] == clobbered['value']), f'slot {i}: contradictory state claims')


def load_contracts(path=ROOT / 'spec/api_contracts.json'):
    data = json.loads(Path(path).read_text())
    validate_contracts(data, json.loads((ROOT / 'spec/api_slots.json').read_text()),
                       (ROOT / 'docs/api-inventory.md').read_text())
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path', nargs='?', type=Path, default=ROOT / 'spec/api_contracts.json')
    args = parser.parse_args()
    try:
        data = load_contracts(args.path)
    except (ValueError, KeyError, TypeError, OSError) as error:
        parser.exit(1, f'Invalid contracts: {error}\n')
    print(f"PASS: {len(data['apis'])} candidate contracts consistent; BIOS verification NOT_RUN")


if __name__ == '__main__':
    main()
