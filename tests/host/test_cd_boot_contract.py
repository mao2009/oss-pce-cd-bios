"""Rule checks for the CD_BOOT (slot $00) caller-evidence ledger rules, on a synthetic ledger.

The ledger document is withdrawn (owner ruling). The rules still apply to any ledger: the reference callee stays
BLOCKED_ABI, release_eligible=false, no UNKNOWN or HYPOTHESIS field is promoted to SOURCE_FACT, and the candidate
entry is never callable. The gate state for slot $00 is checked on the real gate.
"""
import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))

import reference_jsr_gate as gate  # noqa: E402

STATUSES = {'SOURCE_FACT', 'HYPOTHESIS', 'UNKNOWN', 'PROJECT_DEV_FACT'}
# These fields must stay at exactly this status. Promotion is a failure.
REQUIRED_STATUS = {
    'entry_address': 'SOURCE_FACT',
    'entry_jmp_table': 'HYPOTHESIS',
    'body_address_by_version': 'HYPOTHESIS',
    'stated_purpose': 'UNKNOWN',
    'returns_to_caller': 'HYPOTHESIS',
    'caller_stack_expectation': 'HYPOTHESIS',
    'callee_input_registers_a_x_y_p': 'UNKNOWN',
    'callee_return_registers_flags': 'UNKNOWN',
    'scratch_20F8_20FF': 'UNKNOWN',
    'callee_mpr_beyond_mpr7': 'UNKNOWN',
    'callee_stack_contract': 'UNKNOWN',
    'irq_state_on_entry_and_during_boot': 'UNKNOWN',
    'media_detection': 'UNKNOWN',
    'disc_absent_behavior': 'UNKNOWN',
    'boot_transfer_target': 'UNKNOWN',
    'stop_condition': 'UNKNOWN',
    'error_contract': 'UNKNOWN',
    'callable_reference': 'UNKNOWN',
    'reset_vs_slot_call': 'HYPOTHESIS',
}
FORBIDDEN_WORDS = ('VERIFIED', 'PASS', 'DEV_CALLABLE')
EXCLUDED_PATH_FRAGMENTS = ('include/huc/cdrom.asm', 'rom/', 'bios.c')


def qualifies_for_source_fact(src):
    """primary-sdk always qualifies. A project doc qualifies only if it is itself a verified primary source."""
    if src.get('tier') == 'primary-sdk':
        return True
    return src.get('tier') == 'project' and src.get('verified_primary') is True


def check_ledger(ledger):
    """Return a list of violations (empty means the ledger passes)."""
    errors = []
    if ledger.get('block_status') != 'BLOCKED_ABI':
        errors.append('block_status must stay BLOCKED_ABI')
    if ledger.get('release_eligible') is not False:
        errors.append('release_eligible must be false')
    if ledger.get('dev_callable', False) is not False:
        errors.append('dev_callable must be absent or false')
    text = json.dumps(ledger)
    for word in FORBIDDEN_WORDS:
        if word in text:
            errors.append(f'forbidden claim word present: {word}')
    for src in ledger['sources'].values():
        blob = json.dumps(src)
        if any(fragment in blob for fragment in EXCLUDED_PATH_FRAGMENTS):
            errors.append(f'excluded source referenced: {blob[:80]}')
        if 'licence' not in src:
            errors.append('source without licence entry')
    for entry in ledger['entries']:
        if entry.get('callable') != 'UNKNOWN':
            errors.append(f"slot {entry.get('slot')} entry must stay callable=UNKNOWN")
        if entry.get('returns_to_caller') != 'UNKNOWN':
            errors.append(f"slot {entry.get('slot')} returns_to_caller must stay UNKNOWN")
    fields = {f['id']: f for f in ledger['fields']}
    for field in ledger['fields']:
        fid, status = field['id'], field['status']
        if status not in STATUSES:
            errors.append(f'{fid}: unknown status {status}')
            continue
        for src in field.get('sources', []):
            if src not in ledger['sources']:
                errors.append(f'{fid}: dangling source {src}')
        if status == 'SOURCE_FACT':
            if not field.get('sources'):
                errors.append(f'{fid}: SOURCE_FACT without a source')
            elif not any(qualifies_for_source_fact(ledger['sources'].get(src, {}))
                         for src in field['sources']):
                errors.append(f'{fid}: SOURCE_FACT needs a primary-sdk or verified project source')
        if status == 'UNKNOWN':
            if field.get('value') is not None:
                errors.append(f'{fid}: UNKNOWN must have value null')
            if field.get('sources'):
                errors.append(f'{fid}: UNKNOWN must have no sources')
            if not field.get('note'):
                errors.append(f'{fid}: UNKNOWN needs an explanation')
    for fid, expected in REQUIRED_STATUS.items():
        if fid not in fields:
            errors.append(f'required field missing: {fid}')
        elif fields[fid]['status'] != expected:
            errors.append(f'{fid}: expected {expected}, found {fields[fid]["status"]}')
    return errors


def synthetic_ledger():
    """A minimal ledger that passes check_ledger. It holds no document content."""
    fields = []
    for fid, status in REQUIRED_STATUS.items():
        field = {'id': fid, 'status': status}
        if status == 'SOURCE_FACT':
            field.update(value='synthetic', sources=['PRIMARY_SDK_SYNTH'], note='')
        elif status == 'UNKNOWN':
            field.update(value=None, sources=[], note='synthetic: not established')
        else:
            field.update(value='synthetic', sources=[], note='')
        fields.append(field)
    return {
        'block_status': 'BLOCKED_ABI',
        'release_eligible': False,
        'sources': {
            'PRIMARY_SDK_SYNTH': {'tier': 'primary-sdk', 'licence': 'synthetic'},
            'SECONDARY_SYNTH': {'tier': 'secondary', 'licence': 'synthetic'},
            'PROJECT_SYNTH': {'tier': 'project', 'licence': 'synthetic', 'verified_primary': False},
        },
        'entries': [{'slot': 0, 'callable': 'UNKNOWN', 'returns_to_caller': 'UNKNOWN'}],
        'fields': fields,
    }


class CdBootContractRules(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ledger = synthetic_ledger()

    def test_the_synthetic_ledger_passes_all_checks(self):
        self.assertEqual(check_ledger(self.ledger), [])

    def test_the_real_gate_keeps_the_boot_slot_blocked_and_not_released(self):
        ledger, contracts = gate.load()
        record = gate.resolve(0, ledger, contracts)
        self.assertEqual(record['execution'], 'BLOCKED')
        self.assertIs(record['release_eligible'], False)

    def test_promoting_an_unknown_is_rejected(self):
        bad = copy.deepcopy(self.ledger)
        for field in bad['fields']:
            if field['id'] == 'media_detection':
                field.update(status='SOURCE_FACT', value='disc present', sources=['PRIMARY_SDK_SYNTH'])
        self.assertTrue(any('media_detection' in e for e in check_ledger(bad)))

    def test_promoting_secondary_only_rows_is_rejected(self):
        for fid in ('body_address_by_version', 'entry_jmp_table', 'stated_purpose'):
            bad = copy.deepcopy(self.ledger)
            for field in bad['fields']:
                if field['id'] == fid:
                    field['status'] = 'SOURCE_FACT'
            self.assertTrue(any(fid in e for e in check_ledger(bad)), fid)

    def test_secondary_only_source_fact_is_rejected(self):
        bad = copy.deepcopy(self.ledger)
        bad['fields'].append({'id': 'regression_secondary_only', 'status': 'SOURCE_FACT',
                              'value': 'x', 'sources': ['SECONDARY_SYNTH'], 'note': ''})
        errors = check_ledger(bad)
        self.assertTrue(any('regression_secondary_only' in e and 'primary-sdk' in e for e in errors), errors)

    def test_source_fact_citing_only_project_summary_is_rejected(self):
        bad = copy.deepcopy(self.ledger)
        bad['fields'].append({'id': 'regression_project_only', 'status': 'SOURCE_FACT',
                              'value': 'x', 'sources': ['PROJECT_SYNTH'], 'note': ''})
        errors = check_ledger(bad)
        self.assertTrue(any('regression_project_only' in e for e in errors), errors)

    def test_promoting_a_hypothesis_is_rejected(self):
        bad = copy.deepcopy(self.ledger)
        for field in bad['fields']:
            if field['id'] == 'returns_to_caller':
                field['status'] = 'SOURCE_FACT'
        self.assertTrue(any('returns_to_caller' in e for e in check_ledger(bad)))

    def test_callable_entry_is_rejected(self):
        bad = copy.deepcopy(self.ledger)
        bad['entries'][0]['callable'] = True
        self.assertTrue(any('callable=UNKNOWN' in e for e in check_ledger(bad)))

    def test_dev_callable_or_release_eligible_is_rejected(self):
        bad = copy.deepcopy(self.ledger)
        bad['release_eligible'] = True
        bad['dev_callable'] = True
        errors = check_ledger(bad)
        self.assertTrue(any('release_eligible' in e for e in errors))
        self.assertTrue(any('dev_callable' in e for e in errors))

    def test_excluded_source_reference_is_rejected(self):
        bad = copy.deepcopy(self.ledger)
        bad['sources']['HUC_CDROM_ASM'] = {'path': 'include/huc/cdrom.asm', 'licence': 'x'}
        self.assertTrue(any('excluded source' in e for e in check_ledger(bad)))


if __name__ == '__main__':
    unittest.main()
