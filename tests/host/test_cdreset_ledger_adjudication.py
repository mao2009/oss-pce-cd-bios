"""Host tests for the Issue #182 CD_RESET contract claim adjudication rules (pure functions).

`tools.contracts.adjudicate_claim` is deterministic and side-effect free, so synthetic claim
dicts are fed directly. No emulator, no ROM, no reference BIOS execution is involved.
VERIFIED is outside CLAIM_STATUSES and must never be reachable: an unexecuted callee can
not be promoted by these rules.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))

from contracts import (  # noqa: E402
    CLAIM_STATUSES, FIELDS, adjudicate_claim, load_contracts,
)

CALLER = {'huc:include/hucc/pcengine.inc': 'CALLER_SOURCE'}
REAL = {'gear:src/cdrom.cpp': 'EMULATOR_SOURCE'}

UNKNOWN = {'status': 'UNKNOWN', 'value': 'UNKNOWN', 'sources': [], 'confidence': 'UNKNOWN'}
HYPOTHESIS = {
    'status': 'HYPOTHESIS', 'value': 'HuCC caller-side expectation only',
    'sources': ['huc:include/hucc/pcengine.inc'], 'confidence': 'SOURCE_SUPPORTED',
}
OBSERVED = {
    'status': 'OBSERVED', 'value': '$1804 readback $02 while asserted, $00 after release',
    'sources': ['gear:src/cdrom.cpp'], 'confidence': 'SOURCE_SUPPORTED',
}
BLOCKED = {'status': 'BLOCKED', 'value': 'UNKNOWN', 'sources': [], 'confidence': 'UNKNOWN'}


class AdjudicateClaimTests(unittest.TestCase):
    def test_categories_are_only_the_four_and_verified_is_absent(self):
        self.assertEqual(CLAIM_STATUSES, {'UNKNOWN', 'HYPOTHESIS', 'OBSERVED', 'BLOCKED'})
        self.assertNotIn('VERIFIED', CLAIM_STATUSES)

    def test_unknown_claim_is_accepted(self):
        adjudicate_claim(UNKNOWN, CALLER, field='flags', context='slot 1 flags')

    def test_hypothesis_claim_is_accepted(self):
        adjudicate_claim(HYPOTHESIS, CALLER, field='arguments', context='slot 1 arguments')

    def test_observed_claim_with_real_core_source_is_accepted(self):
        adjudicate_claim(OBSERVED, REAL, field='stack', context='slot 1 stack',
                         executed_callee=True)

    def test_blocked_claim_is_accepted(self):
        adjudicate_claim(BLOCKED, CALLER, field='stack', context='slot 1 stack')

    def test_verified_status_is_rejected_illegal_promotion(self):
        bad = dict(HYPOTHESIS, status='VERIFIED')
        with self.assertRaisesRegex(ValueError, 'VERIFIED is not represented'):
            adjudicate_claim(bad, CALLER, field='returns', context='slot 1 returns')

    def test_unknown_status_string_is_rejected(self):
        bad = dict(UNKNOWN, status='PROMISED')
        with self.assertRaisesRegex(ValueError, 'unsupported claim status'):
            adjudicate_claim(bad, CALLER, field='flags', context='slot 1 flags')

    def test_missing_status_is_rejected(self):
        bad = {k: v for k, v in UNKNOWN.items() if k != 'status'}
        with self.assertRaisesRegex(ValueError, 'unsupported claim status'):
            adjudicate_claim(bad, CALLER, field='flags', context='slot 1 flags')

    def test_hypothesis_requires_sources(self):
        bad = dict(HYPOTHESIS, sources=[])
        with self.assertRaisesRegex(ValueError, 'invalid HYPOTHESIS claim'):
            adjudicate_claim(bad, CALLER, field='arguments', context='slot 1 arguments')

    def test_hypothesis_rejects_unknown_value_and_wrong_confidence(self):
        for bad in (dict(HYPOTHESIS, value='UNKNOWN'),
                    dict(HYPOTHESIS, confidence='UNKNOWN')):
            with self.assertRaisesRegex(ValueError, 'invalid HYPOTHESIS claim'):
                adjudicate_claim(bad, CALLER, field='arguments', context='slot 1 arguments')

    def test_observed_requires_emulator_source(self):
        # HLE/caller text alone can never be OBSERVED: it is not real-core measurement evidence.
        bad = dict(OBSERVED, sources=['huc:include/hucc/pcengine.inc'])
        with self.assertRaisesRegex(ValueError, 'EMULATOR_SOURCE measurement'):
            adjudicate_claim(bad, CALLER, field='stack', context='slot 1 stack')

    def test_observed_callee_field_rejected_without_executed_callee(self):
        # Callee ABI behavior (returns) may not be OBSERVED without an executed callee.
        with self.assertRaisesRegex(ValueError, 'executed callee'):
            adjudicate_claim(OBSERVED, REAL, field='returns', context='slot 1 returns')

    def test_observed_callee_field_accepted_when_callee_executed(self):
        adjudicate_claim(OBSERVED, REAL, field='returns', context='slot 1 returns',
                         executed_callee=True)

    def test_observed_hardware_observation_accepted_without_executed_callee(self):
        # hw_bus_reset is a low-level $1804 register/bus fact (hardware observation),
        # not callee ABI behavior: OBSERVED with a real-core EMULATOR_SOURCE must be
        # accepted even though no callee was executed.
        claim = dict(OBSERVED,
                     value='$1804 bit 1: assert -> readback $02, release -> $00, bus FREE')
        adjudicate_claim(claim, REAL, field='hw_bus_reset',
                         context='slot 1 hw_bus_reset', executed_callee=False)

    def test_observed_hardware_observation_rejects_caller_only_source(self):
        # A hardware observation still needs a real-core EMULATOR_SOURCE; caller/HLE
        # text alone can never be OBSERVED.
        bad = dict(OBSERVED, sources=['huc:include/hucc/pcengine.inc'])
        with self.assertRaisesRegex(ValueError, 'EMULATOR_SOURCE measurement'):
            adjudicate_claim(bad, CALLER, field='hw_bus_reset',
                             context='slot 1 hw_bus_reset', executed_callee=False)

    def test_hardware_observation_does_not_promote_callee_abi(self):
        # hw_bus_reset being OBSERVED must not relax callee-behavior claims: returns
        # observed on hardware evidence alone (no executed callee) is still rejected.
        claim = dict(OBSERVED, value='returns $00 in A')
        with self.assertRaisesRegex(ValueError, 'executed callee'):
            adjudicate_claim(claim, REAL, field='returns', context='slot 1 returns',
                             executed_callee=False)

    def test_blocked_requires_unknown_value_confidence_and_no_registers(self):
        for bad in (dict(BLOCKED, value='some blocker text'),
                    dict(BLOCKED, confidence='SOURCE_SUPPORTED'),
                    dict(BLOCKED, registers=['A'])):
            with self.assertRaisesRegex(ValueError, 'invalid BLOCKED claim'):
                adjudicate_claim(bad, CALLER, field='stack', context='slot 1 stack')

    def test_unknown_rejects_sources_and_non_unknown_value(self):
        for bad in (dict(UNKNOWN, sources=['huc:include/hucc/pcengine.inc']),
                    dict(UNKNOWN, value='UNKNOWN-ish')):
            with self.assertRaisesRegex(ValueError, 'invalid UNKNOWN claim'):
                adjudicate_claim(bad, CALLER, field='flags', context='slot 1 flags')

    def test_sources_must_be_known_source_keys(self):
        bad = dict(HYPOTHESIS, sources=['not:a:source'])
        with self.assertRaisesRegex(ValueError, 'known sources'):
            adjudicate_claim(bad, CALLER, field='arguments', context='slot 1 arguments')

    def test_duplicate_sources_rejected(self):
        bad = dict(HYPOTHESIS, sources=['huc:include/hucc/pcengine.inc'] * 2)
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            adjudicate_claim(bad, CALLER, field='arguments', context='slot 1 arguments')

class LedgerAdjudicationTests(unittest.TestCase):
    def test_ledger_statuses_stay_in_the_four_categories(self):
        data = load_contracts()
        for api in data['apis']:
            for field in FIELDS:
                claim = api['contract'][field]
                with self.subTest(slot=api['slot'], field=field):
                    self.assertIn(claim['status'], CLAIM_STATUSES)
                    self.assertNotEqual(claim['status'], 'VERIFIED')
                    adjudicate_claim(claim,
                                     {key: src['kind'] for key, src in data['sources'].items()},
                                     field=field, context=f"slot {api['slot']} {field}",
                                     executed_callee=bool(api['verification'].get('executed_callee')))

    def test_slot1_returns_is_unknown_and_no_callee_executed(self):
        data = load_contracts()
        slot1 = data['apis'][1]
        self.assertEqual((slot1['candidate_name'], slot1['candidate_entry']),
                         ('CD_RESET', '0xE003'))
        self.assertEqual(slot1['contract']['returns']['status'], 'UNKNOWN')
        self.assertEqual(slot1['verification'],
                         {'status': 'NOT_RUN', 'executed_callee': False, 'evidence': []})
        # Nothing in the whole ledger is VERIFIED: no executed reference-ROM callee exists.
        for api in data['apis']:
            for field in FIELDS:
                self.assertNotEqual(api['contract'][field]['status'], 'VERIFIED')


if __name__ == '__main__':
    unittest.main()
