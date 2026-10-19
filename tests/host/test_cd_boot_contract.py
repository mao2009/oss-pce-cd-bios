"""Host checks for the Issue #289 slot $00 (CD_BOOT, candidate $E000) caller-evidence ledger.

Pure JSON checks: no ROM build, no emulator, no reference BIOS. The ledger must keep the
reference callee BLOCKED_ABI, release_eligible=false, and must never promote an UNKNOWN or
HYPOTHESIS field to SOURCE_FACT, or mark the candidate entry callable.
"""
import copy
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / 'docs/research/cd-boot-contract-ledger.json'

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
}
FORBIDDEN_WORDS = ('VERIFIED', 'PASS', 'DEV_CALLABLE')
# A SOURCE_FACT needs at least one source of these tiers; secondary-only citations do not qualify.
PRIMARY_TIERS = {'primary-sdk', 'project'}
EXCLUDED_PATH_FRAGMENTS = ('include/huc/cdrom.asm', 'rom/', 'bios.c')


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
            elif not any(ledger['sources'].get(src, {}).get('tier') in PRIMARY_TIERS
                         for src in field['sources']):
                errors.append(f'{fid}: SOURCE_FACT needs a primary-sdk or project source')
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


class CdBootContractLedgerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ledger = json.loads(LEDGER.read_text(encoding='utf-8'))

    def test_ledger_passes_all_checks(self):
        self.assertEqual(check_ledger(self.ledger), [])

    def test_block_and_release_gates_hold(self):
        self.assertEqual(self.ledger['block_status'], 'BLOCKED_ABI')
        self.assertFalse(self.ledger['release_eligible'])
        self.assertNotIn('DEV_CALLABLE', json.dumps(self.ledger))

    def test_promoting_an_unknown_is_rejected(self):
        bad = copy.deepcopy(self.ledger)
        for field in bad['fields']:
            if field['id'] == 'media_detection':
                field.update(status='SOURCE_FACT', value='disc present', sources=['HUC_CORE_KERNEL'])
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
                              'value': 'x', 'sources': ['ZEOGRAD_BIOS_NOTES'], 'note': ''})
        errors = check_ledger(bad)
        self.assertTrue(any('regression_secondary_only' in e and 'primary-sdk' in e for e in errors), errors)

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
