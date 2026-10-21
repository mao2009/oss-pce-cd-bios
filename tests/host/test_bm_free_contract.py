"""Issue #305: BM_FREE contract ledger guard (docs/305-bm-free-contract.md). Pure stdlib, reads docs and spec only.

Checks that the ledger keeps every row labelled CONFIRMED-by-source / HYPOTHESIS / UNKNOWN, has no VERIFIED claim,
keeps the #307 device probe BLOCKED_ABI, and that the claim ledger (spec/api_contracts.json slot 25) has not been
promoted without this ledger being updated.
"""
import json
import os
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / 'docs' / '305-bm-free-contract.md'
CONTRACTS = ROOT / 'spec' / 'api_contracts.json'
ALLOWED = {'CONFIRMED-by-source', 'HYPOTHESIS', 'UNKNOWN'}


def _label(row_text):
    cells = [c.strip() for c in row_text.strip().strip('|').split('|')]
    return cells[-1]


class BmFreeContractLedger(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = LEDGER.read_text(encoding='utf-8')
        cls.rows = [line for line in cls.text.splitlines() if re.match(r'^\| R\d{2} \|', line)]

    def test_rows_are_numbered_and_complete(self):
        ids = [line.split('|')[1].strip() for line in self.rows]
        self.assertEqual(ids, [f'R{i:02d}' for i in range(1, 22)])

    def test_each_row_has_an_allowed_label(self):
        for line in self.rows:
            label = _label(line)
            first = next((a for a in ALLOWED if label.startswith(a)), None)
            self.assertIsNotNone(first, f'row without allowed label: {line}')

    def test_counts_match_summary(self):
        labels = [_label(line) for line in self.rows]
        counts = {a: sum(1 for lb in labels if lb.startswith(a)) for a in ALLOWED}
        self.assertEqual(counts, {'CONFIRMED-by-source': 8, 'HYPOTHESIS': 5, 'UNKNOWN': 8})
        self.assertIn('`CONFIRMED-by-source` 8、`HYPOTHESIS` 5、`UNKNOWN` 8', self.text)

    def test_no_verified_claim(self):
        self.assertNotIn('VERIFIED', self.text.replace('UNVERIFIED', ''))

    def test_probe_stays_blocked_abi(self):
        self.assertIn('**判定: BLOCKED_ABI。プローブは作成・実行しない（NOT_RUN）。**', self.text)
        self.assertIn('release_eligible=false', self.text)
        probes = []
        for dirpath, dirnames, filenames in os.walk(ROOT):
            dirnames[:] = [d for d in dirnames if d not in ('.git', '.claude')]
            probes += [f for f in filenames if re.search(r'(bm|buram)[_-]?free.*probe|probe.*(bm|buram)[_-]?free', f, re.I)]
        self.assertEqual(probes, [], 'a BM_FREE probe file exists; #307 must stay NOT_RUN')

    def test_claim_ledger_not_promoted(self):
        data = json.loads(CONTRACTS.read_text(encoding='utf-8'))
        slots = [s for s in data['apis'] if isinstance(s, dict) and s.get('candidate_name') == 'BM_FREE']
        self.assertTrue(slots, 'BM_FREE slot missing from spec/api_contracts.json')
        contract = slots[0]['contract']
        self.assertEqual(slots[0]['mapping_status'], 'HYPOTHESIS')
        for field in ('arguments', 'returns', 'clobbered', 'preserved', 'stack', 'mpr', 'ram', 'flags', 'irq', 'errors'):
            self.assertIn(field, contract, f'{field} column missing from BM_FREE contract')
            self.assertEqual(contract[field]['status'], 'UNKNOWN', field)


if __name__ == '__main__':
    unittest.main()
