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

# Evidence-entry schema (shape only). A source entry (repository/revision/path/url/sha256)
# only proves that source text exists; it is not execution evidence. An OBSERVED
# claim must additionally reference non-empty, well-formed evidence describing the
# execution conditions, the emulator core revision claimed for the run,
# the input type fed to the callee, the verification result and a sha256 evidence
# hash. Source references alone are not sufficient for OBSERVED.
EVIDENCE_FIELDS = ('execution_conditions', 'core_revision', 'input_type',
                   'verification_result', 'evidence_hash')
EVIDENCE_HASH_RE = re.compile(r'[0-9a-f]{64}\Z')
# Trust level of evidence entries: FORMAT_ONLY. validate_evidence checks shape
# only. Nothing here resolves evidence_hash to a stored artifact or
# checks core_revision against a pin, so a well-formed hash is NOT proof that the
# run happened. Do not read an accepted OBSERVED claim as VERIFIED.
EVIDENCE_TRUST_LEVEL = 'FORMAT_ONLY'
SOURCE_KINDS = frozenset({'CALLER_SOURCE', 'HLE_SOURCE', 'EMULATOR_SOURCE'})
CLAIM_KEYS = frozenset({'status', 'value', 'sources', 'confidence', 'registers', 'evidence'})


def validate_contracts(data, inventory, inventory_text):
    """Return normally for consistent unexecuted research; raise ValueError otherwise.

    This schema deliberately cannot represent VERIFIED. OBSERVED promotion
    additionally requires non-empty, format-valid evidence entries (FORMAT_ONLY; not authenticated); source
    code references alone are not execution evidence.
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
                    CLAIM_KEYS, f'{context}: claim fields')
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


def validate_evidence(evidence, context):
    """Validate one claim's evidence-entry structure (shape only).

    Each entry must name the execution conditions, the emulator core revision
    claimed for the run, the input type fed to the callee, the
    verification result and a 64-hex lowercase sha256 evidence hash.
    Trust level is EVIDENCE_TRUST_LEVEL (FORMAT_ONLY): shape is checked, the
    hash is not resolved to a stored artifact, so acceptance does not
    prove authenticity. It also does NOT check that the evidence supports the
    claim's contract field (e.g. nothing ties a $1804 readback entry to `stack`
    or `returns`); field/evidence semantic correspondence is unverified.
    Returns None when consistent, raises ValueError otherwise.
    """
    def require(ok, message):
        if not ok:
            raise ValueError(message)

    require(isinstance(evidence, list), f'{context}: claim evidence must be a list')
    for entry in evidence:
        require(isinstance(entry, dict) and set(entry) == set(EVIDENCE_FIELDS),
                f'{context}: evidence entry must contain exactly {sorted(EVIDENCE_FIELDS)}')
        require(all(isinstance(entry[field], str) and entry[field].strip() for field in EVIDENCE_FIELDS),
                f'{context}: evidence entry values must be non-empty strings')
        require(re.fullmatch(r'[0-9a-f]{40}', entry['core_revision']) is not None,
                f'{context}: core_revision must be a 40-hex lowercase git revision')
        require(EVIDENCE_HASH_RE.fullmatch(entry['evidence_hash']) is not None and
                len(set(entry['evidence_hash'])) > 1,
                f'{context}: evidence_hash must be a 64-hex lowercase sha256 digest')


def adjudicate_claim(claim, source_kinds, *, field, context, executed_callee=False):
    """Pure fail-closed adjudication of one contract claim's classification.

    Deterministic and side-effect free: synthetic claim dicts can be passed
    directly from host tests. Returns None when consistent, raises ValueError
    otherwise. This is the classification rule behind OBSERVED/HYPOTHESIS/
    UNKNOWN/BLOCKED; it rejects any status outside the set (including VERIFIED)
    so an unexecuted callee can never be promoted.

    Rules:
      UNKNOWN     - value exactly 'UNKNOWN', no sources, confidence UNKNOWN, no registers
      HYPOTHESIS  - caller/HLE source text only, non-empty sources, confidence SOURCE_SUPPORTED
      OBSERVED    - must not contain a `registers` key (even empty/None); requires at least one EMULATOR_SOURCE reference (necessary, not sufficient);
                    callee-behavior claims additionally require executed_callee;
                    and non-empty, format-valid evidence entries are required
                    (execution conditions, core revision, input type, verification
                    result, sha256 evidence hash). Trust level is FORMAT_ONLY: shape
                    is checked, authenticity and field correspondence are not.
      BLOCKED     - value exactly 'UNKNOWN', confidence UNKNOWN, no registers;
                    sources may cite the blocking evidence
    """
    def require(ok, message):
        if not ok:
            raise ValueError(message)

    # Input-type gate: nothing below may touch claim/source_kinds before their types
    # are known, so malformed input raises ValueError and never AttributeError/TypeError.
    require(isinstance(claim, dict), f'{context}: claim must be a dict')
    require(isinstance(source_kinds, dict), f'{context}: source_kinds must be a dict')
    require(all(isinstance(key, str) for key in source_kinds),
            f'{context}: source keys must be strings')
    require(all(isinstance(kind, str) and kind in SOURCE_KINDS for kind in source_kinds.values()),
            f'{context}: invalid source kind')
    require(isinstance(field, str), f'{context}: claim field must be a string')
    require(executed_callee is True or executed_callee is False,
            f'{context}: executed_callee must be a bool')
    require(all(isinstance(key, str) for key in claim), f'{context}: claim keys must be strings')
    require(isinstance(claim.get('status'), str), f'{context}: unsupported claim status (must be a string)')
    require(isinstance(claim.get('evidence', []), list), f'{context}: claim evidence must be a list')
    registers = claim.get('registers')
    require(registers is None or (isinstance(registers, list) and
                                  all(isinstance(r, str) and r in {'A', 'X', 'Y', 'S', 'P'} for r in registers) and
                                  len(registers) == len(set(registers))),
            f'{context}: registers must be a list of unique A/X/Y/S/P')
    require(set(claim) <= CLAIM_KEYS, f'{context}: unknown claim keys {sorted(set(claim) - CLAIM_KEYS)}')
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

    require(status == 'OBSERVED' or not claim.get('evidence'),
            f'{context}: only an OBSERVED claim may carry evidence')

    if status == 'UNKNOWN':
        require(value == 'UNKNOWN' and confidence == 'UNKNOWN' and sources == [] and not registers,
                f'{context}: invalid UNKNOWN claim')
    elif status == 'HYPOTHESIS':
        require(value != 'UNKNOWN' and confidence == 'SOURCE_SUPPORTED' and sources,
                f'{context}: invalid HYPOTHESIS claim')
    elif status == 'OBSERVED':
        require(value != 'UNKNOWN' and confidence == 'SOURCE_SUPPORTED' and sources,
                f'{context}: invalid OBSERVED claim')
        # Design: an OBSERVED claim has no `registers` key at all (not even [] or None).
        # Register lists belong to preserved/clobbered, which cannot be OBSERVED without
        # an executed callee.
        require('registers' not in claim, f'{context}: OBSERVED claim must not contain registers')
        require(any(source_kinds[source] == EMULATOR_SOURCE_KIND for source in sources),
                f'{context}: OBSERVED requires an EMULATOR_SOURCE source reference')
        require(executed_callee,
                f'{context}: OBSERVED callee behavior requires an executed callee (verification is NOT_RUN)')
        evidence = claim.get('evidence', [])
        require(isinstance(evidence, list) and bool(evidence),
                f'{context}: OBSERVED requires non-empty evidence entries (format-checked only)')
        validate_evidence(evidence, context)
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
