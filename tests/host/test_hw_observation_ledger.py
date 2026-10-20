"""Host tests for the Issue #226 hardware-observation ledger (pure data checks, no emulator).

Hardware observations (hw_bus_reset) are stored apart from the callee ABI ledger. These tests
check the checked-in ledger and reject any entry that would let a hardware fact pose as callee
behavior, a VERIFIED result, or a release-eligible claim.
"""
import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))

import hw_observation_ledger  # noqa: E402
from contracts import FIELDS, HARDWARE_OBSERVATION_FIELDS  # noqa: E402
from hw_observation_ledger import LEDGER_PATH, validate_ledger  # noqa: E402

LEDGER = json.loads(LEDGER_PATH.read_text(encoding='utf-8'))
# Negative-test base only: the pre-downgrade shape of the checked-in entry (OBSERVED, measured trust, runs).
# Every entry derived from it must be refused. The checked-in ledger itself is BLOCKED (Issue #124).
MEASURED_BASE = copy.deepcopy(LEDGER)
MEASURED_BASE['entries'][0].update(status='OBSERVED', trust_level='EMULATOR_CORE_UNARCHIVED')
MEASURED_BASE['entries'][0]['environment']['runs'] = ['cold', 'warm']


def ledger_with(entry_changes=None, top_changes=None, drop=()):
    data = copy.deepcopy(MEASURED_BASE)
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

    def test_observed_entry_with_measured_trust_and_runs_is_refused_without_authentication(self):
        # Self-declared trust level and runs are not authentication (#124): OBSERVED is never accepted.
        self.assertRejected(ledger_with(), 'not authenticated')

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
        data = copy.deepcopy(LEDGER)
        data['entries'].append(copy.deepcopy(data['entries'][0]))
        self.assertRejected(data, 'duplicate entry')


class SourceReferenceConfinementTests(unittest.TestCase):
    """source.reference must be a repository-relative path that resolves inside ROOT.

    ROOT is patched to a temporary repository so that an outside file exists on disk and
    only the confinement rule (not mere existence) can reject it.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        self.repo = base / 'repo'
        (self.repo / 'docs').mkdir(parents=True)
        (self.repo / 'docs' / 'ok.md').write_text('ok', encoding='utf-8')
        self.outside = base / 'outside.md'
        self.outside.write_text('outside', encoding='utf-8')
        patcher = mock.patch.object(hw_observation_ledger, 'ROOT', self.repo)
        patcher.start()
        self.addCleanup(patcher.stop)

    def check(self, reference):
        data = copy.deepcopy(LEDGER)  # checked-in (BLOCKED) entry: acceptance must not depend on OBSERVED
        data['entries'][0]['source']['reference'] = reference
        validate_ledger(data)

    def assertRejected(self, reference):
        with self.assertRaisesRegex(ValueError, 'not a repository file'):
            self.check(reference)

    def test_absolute_path_outside_repo_is_rejected(self):
        self.assertRejected(str(self.outside))

    def test_absolute_path_to_inside_file_is_rejected(self):
        self.assertRejected(str(self.repo / 'docs' / 'ok.md'))

    def test_parent_traversal_is_rejected(self):
        self.assertRejected('../outside.md')

    def test_backslash_parent_traversal_is_rejected(self):
        self.assertRejected('docs\\..\\..\\outside.md')

    def test_symlink_escape_is_rejected(self):
        try:
            os.symlink(self.outside, self.repo / 'docs' / 'link.md')
        except OSError as exc:
            self.skipTest(f'BLOCKED: cannot create symlink in this environment ({exc})')
        self.assertRejected('docs/link.md')

    def test_repo_relative_path_is_accepted(self):
        self.check('docs/ok.md')


if __name__ == '__main__':
    unittest.main()
