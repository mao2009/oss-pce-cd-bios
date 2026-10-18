"""Validate research bookkeeping, never certify a BIOS implementation."""
import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIELDS = {'arguments', 'returns', 'clobbered', 'preserved', 'stack', 'mpr',
          'ram', 'flags', 'irq', 'errors', 'synchronization'}
# Adjudication categories for every contract claim. VERIFIED is deliberately
# absent: it can only be earned by an executed reference-ROM callee, which no
# slot has (verification stays NOT_RUN / executed_callee false).
CLAIM_STATUSES = {'UNKNOWN', 'HYPOTHESIS', 'OBSERVED', 'BLOCKED'}
EMULATOR_SOURCE_KIND = 'EMULATOR_SOURCE'

# Source-kind semantics (Issue #209 / MEDIUM-1). The source kind alone never
# proves callee execution or evidence strength; it only classifies provenance.
#
#   CALLER_SOURCE: HuCC SDK 等 caller 側仕様。callee の実行を保証しない。
#       HYPOTHESIS = caller 側の期待のみ；callee ABI/実行は未観測。
#   HLE_SOURCE: Hu-Go 等の HLE エミュレータ。実機ファームウェアの証拠ではない。
#       HYPOTHESIS = HLE エミュレータ挙動のみ；実機ファームウェアとは無関係。
#   EMULATOR_SOURCE: 実コア（Geargrafx ピンrev）での実測。ハードウェアレベルの観測。
#       HYPOTHESIS = 実コア実測ありだが callee ABI/実行は未確認。
#       callee execution なしでは OBSERVED に昇格しない（callee ABI は HYPOTHESIS のまま）。
SOURCE_KIND_MEANINGS = {
    'CALLER_SOURCE': 'HuCC SDK 等 caller 側仕様。callee の実行を保証しない。',
    'HLE_SOURCE': 'Hu-Go 等の HLE エミュレータ。実機ファームウェアの証拠ではない。',
    'EMULATOR_SOURCE': '実コア（Geargrafx ピンrev）での実測。ハードウェアレベルの観測。',
}


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
        source_kinds = {key: source['kind'] for key, source in sources.items()}
        executed_callee = bool(api.get('verification', {}).get('executed_callee'))
        for field, claim in contract.items():
            context = f'slot {i} {field}'
            require(isinstance(claim, dict) and set(claim) <=
                    {'status', 'value', 'sources', 'confidence', 'registers'}, f'{context}: claim fields')
            adjudicate_claim(claim, source_kinds, field=field, context=context,
                             executed_callee=executed_callee)
            refs(claim.get('sources'), context,
                 claim['status'] in {'HYPOTHESIS', 'OBSERVED'})
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


def adjudicate_claim(claim, source_kinds, *, field, context, executed_callee=False):
    """Pure fail-closed adjudication of one contract claim's classification.

    Deterministic and side-effect free: synthetic claim dicts can be passed
    directly from host tests. Returns None when consistent, raises ValueError
    otherwise. This is the classification rule behind OBSERVED/HYPOTHESIS/
    UNKNOWN/BLOCKED; it rejects any status outside the set (including VERIFIED)
    so an unexecuted callee can never be promoted.

    Source-kind semantics (Issue #209 / MEDIUM-1):
      CALLER_SOURCE: HuCC SDK 等 caller 側仕様。callee の実行を保証しない。
          HYPOTHESIS = caller 側の期待のみ；callee ABI/実行は未観測。
      HLE_SOURCE: Hu-Go 等の HLE エミュレータ。実機ファームウェアの証拠ではない。
          HYPOTHESIS = HLE エミュレータ挙動のみ；実機ファームウェアとは無関係。
      EMULATOR_SOURCE: 実コア（Geargrafx ピンrev）での実測。ハードウェアレベルの観測。
          HYPOTHESIS = 実コア実測ありだが callee ABI/実行は未確認。
          callee execution なしでは OBSERVED に昇格しない（callee ABI は HYPOTHESIS のまま）。

    Rules:
      UNKNOWN     - value exactly 'UNKNOWN', no sources, confidence UNKNOWN, no registers
      HYPOTHESIS  - caller/HLE/emulator source text only, non-empty sources, confidence SOURCE_SUPPORTED
                    (source kind does not by itself imply callee execution or evidence strength)
      OBSERVED    - requires at least one EMULATOR_SOURCE (real-core measurement);
                    callee-behavior claims additionally require executed_callee
      BLOCKED     - value exactly 'UNKNOWN', confidence UNKNOWN, no registers;
                    sources may cite the blocking evidence
    """
    def require(ok, message):
        if not ok:
            raise ValueError(message)

    status = claim.get('status')
    require(status in CLAIM_STATUSES,
            f'{context}: unsupported claim status {status!r}; VERIFIED is not represented')
    value = claim.get('value')
    require(isinstance(value, str) and value, f'{context}: claim value required')
    sources = claim.get('sources', [])
    require(isinstance(sources, list) and all(isinstance(x, str) for x in sources),
            f'{context}: claim sources must be a list of strings')
    require(len(sources) == len(set(sources)), f'{context}: duplicate claim sources')
    require(set(sources) <= set(source_kinds), f'{context}: claim sources must be known sources')
    confidence = claim.get('confidence')
    registers = claim.get('registers')

    if status == 'UNKNOWN':
        require(value == 'UNKNOWN' and confidence == 'UNKNOWN' and sources == [] and not registers,
                f'{context}: invalid UNKNOWN claim')
    elif status == 'HYPOTHESIS':
        # HYPOTHESIS 受理条件（各 source kind 共通）: value != 'UNKNOWN',
        # confidence == 'SOURCE_SUPPORTED', かつ非空の sources。
        #   CALLER_SOURCE: caller 側の期待のみ（callee の実行を保証しない）。
        #   HLE_SOURCE:    HLE エミュレータ挙動のみ（実機ファームウェアの証拠ではない）。
        #   EMULATOR_SOURCE: 実コア実測だが callee ABI/実行は未確認；
        #                    callee execution なしでは OBSERVED に昇格しない
        #                    （callee ABI は HYPOTHESIS のまま）。
        require(value != 'UNKNOWN' and confidence == 'SOURCE_SUPPORTED' and sources,
                f'{context}: invalid HYPOTHESIS claim')
    elif status == 'OBSERVED':
        require(value != 'UNKNOWN' and confidence == 'SOURCE_SUPPORTED' and sources,
                f'{context}: invalid OBSERVED claim')
        require(any(source_kinds[source] == EMULATOR_SOURCE_KIND for source in sources),
                f'{context}: OBSERVED requires a real-core EMULATOR_SOURCE measurement')
        require(executed_callee,
                f'{context}: OBSERVED callee behavior requires an executed callee (verification is NOT_RUN)')
    elif status == 'BLOCKED':
        require(value == 'UNKNOWN' and confidence == 'UNKNOWN' and not registers,
                f'{context}: invalid BLOCKED claim')


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
