"""Negative integrity checks for research records, not callee execution tests."""
import copy
import json
import unittest
from tools.contracts import ROOT, load_contracts, validate_contracts


class ContractsTests(unittest.TestCase):
    def setUp(self):
        self.data = copy.deepcopy(load_contracts())
        self.inventory = json.loads((ROOT / 'spec/api_slots.json').read_text())
        self.markdown = (ROOT / 'docs/api-inventory.md').read_text()

    def check(self):
        validate_contracts(self.data, self.inventory, self.markdown)

    def test_all_slots_keep_unknown_preservation_and_no_execution(self):
        self.check()
        for api in self.data['apis']:
            self.assertEqual(api['contract']['preserved']['status'], 'UNKNOWN')
            self.assertFalse(api['verification']['executed_callee'])
        for slot in (1, 3, 30):
            self.assertEqual(self.data['apis'][slot]['contract']['arguments']['status'], 'HYPOTHESIS')

    def test_lost_duplicated_reordered_and_renamed_candidates(self):
        original = copy.deepcopy(self.data)
        for change in ('lost', 'duplicate', 'reorder', 'rename', 'entry'):
            with self.subTest(change=change):
                self.data = copy.deepcopy(original)
                if change == 'lost': self.data['apis'].pop()
                if change == 'duplicate': self.data['apis'][4] = self.data['apis'][3]
                if change == 'reorder': self.data['apis'].reverse()
                if change == 'rename': self.data['apis'][13]['candidate_name'] = 'CD_SUBRD'
                if change == 'entry': self.data['apis'][3]['candidate_entry'] = '0xE00C'
                with self.assertRaises(ValueError): self.check()

    def test_markdown_inventory_drift(self):
        self.markdown = self.markdown.replace('`MA_DIV16S`', '`MA_DIV16U`')
        with self.assertRaisesRegex(ValueError, 'entry/name mismatch'): self.check()

    def test_missing_required_field(self):
        del self.data['apis'][3]['contract']['irq']
        with self.assertRaisesRegex(ValueError, 'contract fields'): self.check()

    def test_unknown_cannot_hide_assertions_sources_or_confidence(self):
        original = copy.deepcopy(self.data)
        for field, value in [('value', 'A preserved'), ('sources', ['hugo:bios.c']),
                             ('confidence', 'SOURCE_SUPPORTED'), ('registers', ['A'])]:
            with self.subTest(field=field):
                self.data = copy.deepcopy(original)
                self.data['apis'][3]['contract']['preserved'][field] = value
                with self.assertRaisesRegex(ValueError, 'invalid UNKNOWN'): self.check()

    def test_reported_claim_needs_source_and_cannot_be_verified(self):
        original = copy.deepcopy(self.data)
        for field, value in [('sources', []), ('sources', ['nonexistent']), ('status', 'VERIFIED'),
                             ('value', 'UNKNOWN'), ('confidence', 'VERIFIED')]:
            with self.subTest(field=field):
                self.data = copy.deepcopy(original)
                self.data['apis'][3]['contract']['arguments'][field] = value
                with self.assertRaises(ValueError): self.check()

    def test_no_false_verification_or_approval(self):
        original = copy.deepcopy(self.data)
        for field, value in [('verification', {'status': 'PASS', 'executed_callee': True, 'evidence': []}),
                             ('implementation_status', 'IMPLEMENTED'), ('provenance_status', 'APPROVED')]:
            self.data = copy.deepcopy(original)
            self.data['apis'][30][field] = value
            with self.assertRaises(ValueError): self.check()

    def test_source_revision_hash_url_and_union(self):
        original = copy.deepcopy(self.data)
        for field, value in [('revision', 'main'), ('sha256', 'bad'), ('url', 'https://example.com'),
                             ('provenance_status', 'APPROVED')]:
            self.data = copy.deepcopy(original)
            self.data['sources']['hugo:bios.c'][field] = value
            with self.assertRaises(ValueError): self.check()
        self.data = copy.deepcopy(original)
        self.data['apis'][3]['sources'].remove('hugo:bios.c')
        with self.assertRaises(ValueError): self.check()

    def test_contradictory_register_preservation_rejected(self):
        for field in ('preserved', 'clobbered'):
            self.data['apis'][3]['contract'][field] = {
                'status': 'HYPOTHESIS', 'value': field+' A', 'registers': ['A'],
                'confidence': 'SOURCE_SUPPORTED', 'sources': ['hugo:bios.c']}
        with self.assertRaisesRegex(ValueError, 'contradictory'): self.check()

    def test_division_conflict_and_missing_late_hucc_names_retained(self):
        self.assertEqual([o['name'] for o in self.data['apis'][66]['name_observations']],
                         ['MA_DIV16S', 'MA_DIV16U'])
        self.assertEqual([o['name'] for o in self.data['apis'][67]['name_observations']],
                         ['MA_DIV16U', 'MA_DIV16S'])
        for api in self.data['apis'][77:]:
            self.assertEqual(api['mapping_sources'], [])


if __name__ == '__main__':
    unittest.main()
