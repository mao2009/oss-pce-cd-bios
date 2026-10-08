"""Research ledger consistency checks; no CPU or loader emulation."""
import copy
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]


def validate_contract(contract):
    """Reject internal contradictions without promoting historical hypotheses."""
    def require(condition, message):
        if not condition:
            raise ValueError(message)

    require(contract['schema_version'] == 1, 'schema version')
    require(contract['release_approved'] is False, 'research cannot approve release')
    sources = contract['sources']
    for source in sources.values():
        require(source['reuse_mode'] == 'REFERENCE_ONLY'
                and source['adoption_status'] == 'PENDING', 'adoption boundary')
        require(source['url'].startswith('https://') and source['license_notice'], 'source notice')
        revision = source['revision']
        if revision is not None:
            require(re.fullmatch(r'[0-9a-f]{40}', revision) is not None
                    and revision in source['url'] and source['path'], 'pinned reference')
        else:
            require(source.get('accessed'), 'unversioned access date')
    claims = {}
    for claim in contract['claims']:
        require(claim['id'] not in claims, 'duplicate claim')
        claims[claim['id']] = claim
        require(claim['scope'] in ('cpu', 'geargrafx', 'diagnostic', 'historical', 'final_bios'), 'scope')
        require(claim['status'] in ('SOURCE_FACT', 'OBSERVED', 'UNKNOWN', 'HYPOTHESIS'), 'status')
        require(claim['limit'] and claim['references'], 'claim limits/references')
        require(all(ref in sources for ref in claim['references']), 'dangling reference')
        if claim['status'] == 'UNKNOWN':
            require(claim['value'] is None, 'UNKNOWN must not assert a value')
        else:
            require(claim['value'] is not None, 'known claim needs value')
        if claim['status'] in ('SOURCE_FACT', 'OBSERVED'):
            require(all(sources[ref]['revision'] is not None for ref in claim['references']), 'unversioned fact')
        if claim['status'] == 'OBSERVED':
            require(claim['scope'] == 'diagnostic', 'observation scope')
        if claim['scope'] in ('historical', 'final_bios'):
            require(claim['status'] in ('UNKNOWN', 'HYPOTHESIS'), 'historical promotion needs new evidence/schema')
    value = lambda name: claims[name]['value']
    geometry = value('mpr_geometry')
    bank = geometry['bank_bytes']
    require(bank == 8192 and geometry['mpr_count'] * bank == geometry['logical_bytes']
            and 256 * bank == geometry['physical_bytes'], 'MPR geometry')
    reset = value('reset_mapping')
    require(reset['other_mpr_defaults'] is None and reset['mpr7'] == 0, 'reset defaults')
    require(reset['byte_order'] == 'little', 'vector byte order')
    require(reset['vector_physical'] == reset['mpr7'] * bank + reset['vector_cpu'] % bank, 'reset translation')
    vectors = value('vectors')
    require(list(vectors.values()) == list(range(0xfff6, 0x10000, 2))
            and vectors['RESET'] == reset['vector_cpu'], 'vector extent')
    loader = value('syscard_loader')
    require(loader['payload_bytes'] % bank == 0
            and loader['accepted_input_bytes'] == [loader['payload_bytes'], loader['payload_bytes'] + loader['stripped_prefix_bytes']]
            and loader['stripped_prefix_bytes'] == 512, 'loader lengths')
    mapping = value('syscard_rom_mapping')
    require(mapping['bank_count'] * bank == loader['payload_bytes']
            and mapping['file_bank_modulus'] == mapping['bank_count'], 'ROM banks')
    for name in ('cd_ram', 'super_cd_ram'):
        ram = value(name)
        require(ram['physical_first'] == ram['bank_first'] * bank
                and ram['physical_last'] + 1 == (ram['bank_last'] + 1) * bank
                and ram['bytes'] == ram['physical_last'] + 1 - ram['physical_first'], 'RAM extent')
    io = value('cd_io')
    require(io['physical_first'] == io['bank'] * bank + io['offset_first']
            and io['physical_last'] == io['bank'] * bank + io['offset_last']
            and 0 <= io['offset_first'] <= io['offset_last'] < bank, 'IO translation')
    diagnostic = value('diagnostic_layout')
    require(diagnostic['image_bytes'] == loader['payload_bytes']
            and diagnostic['bank_bytes'] == bank
            and (diagnostic['padding_banks'] + 1) * bank == diagnostic['image_bytes'], 'diagnostic size')
    require(diagnostic['reset_vector_file_offset'] == reset['vector_physical']
            and diagnostic['vector_file_first'] == vectors['IRQ2_BRK'] % bank
            and diagnostic['mpr7'] == reset['mpr7'], 'diagnostic vectors')
    require(diagnostic['api_end_cpu_exclusive'] == diagnostic['api_base_cpu'] + diagnostic['api_count'] * diagnostic['api_stride']
            and diagnostic['bank0_cpu_base'] <= diagnostic['api_base_cpu'] < diagnostic['api_end_cpu_exclusive'] <= diagnostic['bank0_cpu_base'] + bank
            and diagnostic['bank0_cpu_base'] <= diagnostic['reset_target_cpu'] < diagnostic['bank0_cpu_base'] + bank, 'diagnostic placement')


class RomContractTests(unittest.TestCase):
    def setUp(self):
        self.contract = json.loads((ROOT / 'spec/rom-contract.json').read_text())

    def mutate_claim(self, name, key, value):
        claim = next(c for c in self.contract['claims'] if c['id'] == name)
        claim['value'][key] = value

    def test_current_ledger_is_consistent(self):
        validate_contract(self.contract)

    def test_contradictions_are_rejected(self):
        cases = [('mpr_geometry', 'mpr_count', 7), ('reset_mapping', 'other_mpr_defaults', 0),
                 ('reset_mapping', 'vector_physical', 262142),
                 ('syscard_loader', 'accepted_input_bytes', [262144]),
                 ('syscard_rom_mapping', 'file_bank_modulus', 64),
                 ('cd_ram', 'physical_last', 1114112),
                 ('super_cd_ram', 'bytes', 262144), ('cd_io', 'physical_first', 6144),
                 ('diagnostic_layout', 'reset_vector_file_offset', 262142),
                 ('diagnostic_layout', 'api_end_cpu_exclusive', 57586)]
        original = copy.deepcopy(self.contract)
        for name, key, value in cases:
            with self.subTest(name=name, key=key):
                self.contract = copy.deepcopy(original)
                self.mutate_claim(name, key, value)
                with self.assertRaises(ValueError):
                    validate_contract(self.contract)

    def test_unknown_cannot_smuggle_capacity_or_observation(self):
        for changes in ({'value': 262144}, {'status': 'OBSERVED', 'value': 262144}):
            with self.subTest(changes=changes):
                data = copy.deepcopy(self.contract)
                next(c for c in data['claims'] if c['id'] == 'historical_system_card_1').update(changes)
                with self.assertRaises(ValueError):
                    validate_contract(data)

    def test_reference_and_permission_failures(self):
        for mutation in ('dangling', 'revision', 'notice', 'adopt', 'duplicate', 'unversioned_fact', 'release'):
            with self.subTest(mutation=mutation):
                data = copy.deepcopy(self.contract)
                if mutation == 'dangling':
                    data['claims'][0]['references'] = ['missing']
                elif mutation == 'duplicate':
                    data['claims'].append(copy.deepcopy(data['claims'][0]))
                elif mutation == 'unversioned_fact':
                    data['claims'][0]['references'] = ['huc-wiki']
                elif mutation == 'release':
                    data['release_approved'] = True
                else:
                    key, value = {'revision': ('revision', 'main'), 'notice': ('license_notice', ''),
                                  'adopt': ('adoption_status', 'APPROVED')}[mutation]
                    data['sources']['gear-loader'][key] = value
                with self.assertRaises(ValueError):
                    validate_contract(data)


if __name__ == '__main__':
    unittest.main()
