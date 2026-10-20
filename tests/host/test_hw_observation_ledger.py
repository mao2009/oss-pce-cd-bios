"""Host tests for the Issue #226 hardware-observation ledger (pure data checks, no emulator).

Hardware observations (hw_bus_reset) are stored apart from the callee ABI ledger. These tests
check the checked-in ledger and reject any entry that would let a hardware fact pose as callee
behavior, a VERIFIED result, or a release-eligible claim.
"""
import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))

from contracts import FIELDS, HARDWARE_OBSERVATION_FIELDS  # noqa: E402
from hw_observation_ledger import LEDGER_PATH, validate_ledger  # noqa: E402

LEDGER = json.loads(LEDGER_PATH.read_text(encoding='utf-8'))


def ledger_with(entry_changes=None, top_changes=None, drop=()):
    data = copy.deepcopy(LEDGER)
    for key, value in (top_changes or {}).items():
        data[key] = value
    for key, value in (entry_changes or {}).items():
        data['entries'][0][key] = value
    for key in drop:
        data['entries'][0].pop(key)
    return data


class CheckedInLedgerTests(unittest.TestCase):
    def test_checked_in_ledger_validates(self):
        validate_ledger(LEDGER)

    def test_ledger_is_not_release_eligible_and_has_no_callee_abi(self):
        self.assertIs(LEDGER['release_eligible'], False)
        self.assertEqual(LEDGER['callee_abi'], 'UNVERIFIED')

    def test_only_hardware_fields_are_stored_and_callee_fields_stay_out(self):
        names = {entry['field'] for entry in LEDGER['entries']}
        self.assertEqual(names, {'hw_bus_reset'})
        self.assertTrue(names <= HARDWARE_OBSERVATION_FIELDS)
        self.assertFalse(names & set(FIELDS))

    def test_api_contract_ledger_has_no_hardware_observation_field(self):
        text = (ROOT / 'spec' / 'api_contracts.json').read_text(encoding='utf-8')
        self.assertNotIn('hw_bus_reset', text)


class NegativeLedgerTests(unittest.TestCase):
    def assertRejected(self, data, pattern):
        with self.assertRaisesRegex(ValueError, pattern):
            validate_ledger(data)

    def test_release_eligible_true_is_rejected(self):
        self.assertRejected(ledger_with(top_changes={'release_eligible': True}), 'release_eligible must be false')

    def test_callee_abi_claim_is_rejected(self):
        self.assertRejected(ledger_with(top_changes={'callee_abi': 'VERIFIED'}), 'callee_abi must be UNVERIFIED')

    def test_callee_field_cannot_enter_hardware_ledger(self):
        self.assertRejected(ledger_with(entry_changes={'field': 'returns'}), 'not a hardware observation field')

    def test_verified_status_is_rejected(self):
        self.assertRejected(ledger_with(entry_changes={'status': 'VERIFIED'}), 'unsupported status')

    def test_callee_fields_inside_entry_are_rejected(self):
        data = ledger_with()
        data['entries'][0]['returns'] = 'A=$00'
        self.assertRejected(data, 'keys must be exactly')

    def test_missing_environment_is_rejected(self):
        self.assertRejected(ledger_with(drop=('environment',)), 'keys must be exactly')

    def test_hardware_environment_is_not_accepted_yet(self):
        data = ledger_with()
        data['entries'][0]['environment']['kind'] = 'HARDWARE'
        self.assertRejected(data, 'not an accepted measurement environment')

    def test_caller_source_cannot_back_observation(self):
        data = ledger_with()
        data['entries'][0]['source']['kind'] = 'CALLER_SOURCE'
        self.assertRejected(data, 'source kind must be EMULATOR_SOURCE')

    def test_short_revision_is_rejected(self):
        data = ledger_with()
        data['entries'][0]['source']['revision'] = 'b49ae82a'
        self.assertRejected(data, '40-hex')

    def test_missing_source_reference_file_is_rejected(self):
        data = ledger_with()
        data['entries'][0]['source']['reference'] = 'docs/does-not-exist.md'
        self.assertRejected(data, 'not a repository file')

    def test_unknown_trust_level_is_rejected(self):
        self.assertRejected(ledger_with(entry_changes={'trust_level': 'FORMAT_ONLY'}), 'unsupported trust_level')

    def test_observed_without_measured_trust_is_rejected(self):
        self.assertRejected(ledger_with(entry_changes={'trust_level': 'NOT_MEASURED'}),
                            'OBSERVED requires a measured trust level')

    def test_observed_without_runs_is_rejected(self):
        data = ledger_with()
        data['entries'][0]['environment']['runs'] = []
        self.assertRejected(data, 'at least one run')

    def test_not_run_must_not_claim_measurement(self):
        self.assertRejected(ledger_with(entry_changes={'status': 'NOT_RUN'}),
                            'must not claim a measurement')

    def test_empty_not_evidence_for_is_rejected(self):
        self.assertRejected(ledger_with(entry_changes={'not_evidence_for': []}), 'not_evidence_for')

    def test_duplicate_field_is_rejected(self):
        data = ledger_with()
        data['entries'].append(copy.deepcopy(data['entries'][0]))
        self.assertRejected(data, 'duplicate entry')


if __name__ == '__main__':
    unittest.main()
