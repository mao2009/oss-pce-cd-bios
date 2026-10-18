"""Host tests for the Issue #182 CD_RESET contract claim adjudication rules (pure functions).

`tools.contracts.adjudicate_claim` is deterministic and side-effect free, so synthetic claim
dicts are fed directly. No emulator, no ROM, no reference BIOS execution is involved.
VERIFIED is outside CLAIM_STATUSES and must never be reachable: an unexecuted callee can
not be promoted by these rules.

Issue #208 strengthens OBSERVED: a source reference (source code URL/revision) only proves
that source text exists; it is not measurement evidence. OBSERVED promotion now additionally
requires non-empty, well-formed measurement evidence (execution conditions, core revision,
input type, verification result, sha256 evidence hash). `verification.executed_callee` is
part of the evidence and is not sufficient by itself.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))

from contracts import (  # noqa: E402
    CALLEE_BEHAVIOR_FIELDS, CLAIM_STATUSES, EVIDENCE_TRUST_LEVEL, FIELDS,
    HARDWARE_OBSERVATION_FIELDS, SOURCE_KIND_MEANINGS, adjudicate_claim, load_contracts,
)

CALLER = {'huc:include/hucc/pcengine.inc': 'CALLER_SOURCE'}
HLE = {'hugo:bios.c': 'HLE_SOURCE'}
REAL = {'gear:src/cdrom.cpp': 'EMULATOR_SOURCE'}

UNKNOWN = {'status': 'UNKNOWN', 'value': 'UNKNOWN', 'sources': [], 'confidence': 'UNKNOWN'}
HYPOTHESIS = {
    'status': 'HYPOTHESIS', 'value': 'HuCC caller-side expectation only',
    'sources': ['huc:include/hucc/pcengine.inc'], 'confidence': 'SOURCE_SUPPORTED',
}
# Complete, well-formed measurement evidence for a synthetic OBSERVED claim.
EVIDENCE_ENTRY = {
    'execution_conditions': 'Geargrafx core cold boot; Super System Card 3.0 JP candidate; no disc',
    'core_revision': 'b49ae82a012eade566d72e9d61c9d95a5caa4da3',
    'input_type': 'CD_RESET system-call selector passed in Y',
    'verification_result': '$1804 readback $02 while asserted, $00 after release',
    'evidence_hash': 'd1a2f529224a70666ede15c4cf774da484bf1b96495a53e08d474f09f8ff5a50',
}
OBSERVED = {
    'status': 'OBSERVED', 'value': '$1804 readback $02 while asserted, $00 after release',
    'sources': ['gear:src/cdrom.cpp'], 'confidence': 'SOURCE_SUPPORTED',
    'evidence': [EVIDENCE_ENTRY],
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

    def test_observed_claim_with_complete_evidence_is_accepted(self):
        # Issue #208 positive case: EMULATOR_SOURCE + executed callee + complete evidence.
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

    def test_hypothesis_caller_source_accepted(self):
        # CALLER_SOURCE + HYPOTHESIS = caller 側の期待のみ。callee の実行は保証されない。
        adjudicate_claim(HYPOTHESIS, CALLER, field='arguments', context='slot 1 arguments')

    def test_hypothesis_hle_source_accepted(self):
        # HLE_SOURCE + HYPOTHESIS = HLE エミュレータ挙動のみ。実機ファームウェアの証拠ではない。
        hle_hypothesis = {
            'status': 'HYPOTHESIS', 'value': 'Hu-Go HLE writes $222D as disc-present marker',
            'sources': ['hugo:bios.c'], 'confidence': 'SOURCE_SUPPORTED',
        }
        adjudicate_claim(hle_hypothesis, HLE, field='ram', context='slot 1 ram')

    def test_hypothesis_emulator_source_accepted(self):
        # EMULATOR_SOURCE + HYPOTHESIS = 実コアのソース参照のみ。実行証跡ではなく callee ABI/実行は未確認。
        # callee execution なしでは OBSERVED に昇格しない（callee ABI は HYPOTHESIS のまま）。
        emulator_hypothesis = {
            'status': 'HYPOTHESIS', 'value': 'Geargrafx core source suggests $1804 readback',
            'sources': ['gear:src/cdrom.cpp'], 'confidence': 'SOURCE_SUPPORTED',
        }
        adjudicate_claim(emulator_hypothesis, REAL, field='stack', context='slot 1 stack')

    def test_emulator_source_meaning_does_not_claim_execution(self):
        self.assertIn('実行証跡ではなく', SOURCE_KIND_MEANINGS['EMULATOR_SOURCE'])
        self.assertNotIn('実測', SOURCE_KIND_MEANINGS['EMULATOR_SOURCE'])

    def test_source_kind_meanings_document_all_three_kinds(self):
        self.assertEqual(set(SOURCE_KIND_MEANINGS),
                         {'CALLER_SOURCE', 'HLE_SOURCE', 'EMULATOR_SOURCE'})
        for meaning in SOURCE_KIND_MEANINGS.values():
            self.assertIsInstance(meaning, str) and self.assertTrue(meaning)

    def test_observed_rejected_for_caller_source_only(self):
        # CALLER_SOURCE だけでは OBSERVED になれない（callee 実行を保証しない）。
        bad = dict(OBSERVED, sources=['huc:include/hucc/pcengine.inc'])
        with self.assertRaisesRegex(ValueError, 'EMULATOR_SOURCE measurement'):
            adjudicate_claim(bad, CALLER, field='stack', context='slot 1 stack')

    def test_observed_rejected_for_hle_source_only(self):
        # HLE_SOURCE だけでは OBSERVED になれない（実機ファームウェアの証拠ではない）。
        bad = dict(OBSERVED, sources=['hugo:bios.c'])
        with self.assertRaisesRegex(ValueError, 'EMULATOR_SOURCE measurement'):
            adjudicate_claim(bad, HLE, field='stack', context='slot 1 stack')

    def test_emulator_source_hypothesis_not_promotable_to_observed_without_executed_callee(self):
        # EMULATOR_SOURCE + HYPOTHESIS は callee ABI を HYPOTHESIS のまま保つ。
        # callee execution なしでは OBSERVED へ昇格しない。
        with self.assertRaisesRegex(ValueError, 'executed callee'):
            adjudicate_claim(OBSERVED, REAL, field='returns', context='slot 1 returns')

    def test_observed_requires_emulator_source(self):
        # HLE/caller text alone can never be OBSERVED: it is not an emulator-source reference.
        bad = dict(OBSERVED, sources=['huc:include/hucc/pcengine.inc'])
        with self.assertRaisesRegex(ValueError, 'EMULATOR_SOURCE measurement'):
            adjudicate_claim(bad, CALLER, field='stack', context='slot 1 stack',
                             executed_callee=True)

    def test_observed_requires_non_empty_evidence(self):
        # Issue #208 negative case: an OBSERVED claim without measurement evidence is rejected
        # even with an EMULATOR_SOURCE and an executed callee.
        bad = dict(OBSERVED, evidence=[])
        with self.assertRaisesRegex(ValueError, 'non-empty measurement evidence'):
            adjudicate_claim(bad, REAL, field='stack', context='slot 1 stack',
                             executed_callee=True)

    def test_observed_without_evidence_key_is_rejected(self):
        bad = {k: v for k, v in OBSERVED.items() if k != 'evidence'}
        with self.assertRaisesRegex(ValueError, 'non-empty measurement evidence'):
            adjudicate_claim(bad, REAL, field='stack', context='slot 1 stack',
                             executed_callee=True)

    def test_observed_with_invalid_evidence_hash_is_rejected(self):
        bad = dict(OBSERVED, evidence=[dict(EVIDENCE_ENTRY, evidence_hash='not-a-sha256')])
        with self.assertRaisesRegex(ValueError, 'sha256'):
            adjudicate_claim(bad, REAL, field='stack', context='slot 1 stack',
                             executed_callee=True)

    def test_observed_with_incomplete_evidence_entry_is_rejected(self):
        incomplete = dict(EVIDENCE_ENTRY)
        del incomplete['verification_result']
        bad = dict(OBSERVED, evidence=[incomplete])
        with self.assertRaisesRegex(ValueError, 'evidence entry must contain exactly'):
            adjudicate_claim(bad, REAL, field='stack', context='slot 1 stack',
                             executed_callee=True)

    def test_executed_callee_alone_without_evidence_is_insufficient(self):
        # Issue #208: executed_callee self-attestation is part of the evidence, not sufficient.
        bad = {k: v for k, v in OBSERVED.items() if k != 'evidence'}
        with self.assertRaisesRegex(ValueError, 'non-empty measurement evidence'):
            adjudicate_claim(bad, REAL, field='stack', context='slot 1 stack',
                             executed_callee=True)

    def test_observed_callee_field_rejected_without_executed_callee(self):
        # Callee ABI behavior (returns) may not be OBSERVED without an executed callee.
        with self.assertRaisesRegex(ValueError, 'executed callee'):
            adjudicate_claim(OBSERVED, REAL, field='returns', context='slot 1 returns')

    def test_observed_callee_field_accepted_when_callee_executed(self):
        adjudicate_claim(OBSERVED, REAL, field='returns', context='slot 1 returns',
                         executed_callee=True)

    def test_observed_hardware_observation_accepted_without_executed_callee(self):
        # hw_bus_reset is a low-level $1804 register/bus fact (hardware observation),
        # not callee ABI behavior: OBSERVED with an EMULATOR_SOURCE reference plus evidence must be
        # accepted even though no callee was executed.
        claim = dict(OBSERVED,
                     value='$1804 bit 1: assert -> readback $02, release -> $00, bus FREE')
        adjudicate_claim(claim, REAL, field='hw_bus_reset',
                         context='slot 1 hw_bus_reset', executed_callee=False)

    def test_observed_hardware_observation_rejects_caller_only_source(self):
        # A hardware observation still needs an EMULATOR_SOURCE reference; caller/HLE
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

    # ---- Issue #208 (MAJOR-2): evidence trust boundary. EVIDENCE_ENTRY is SYNTHETIC test data,
    # never a real measurement; acceptance only means the *shape* is valid (FORMAT_ONLY).
    def test_evidence_trust_level_is_format_only(self):
        self.assertEqual(EVIDENCE_TRUST_LEVEL, 'FORMAT_ONLY')

    def test_complete_structural_evidence_is_schema_accepted_not_authenticated(self):
        # Accepted for shape only: nothing resolves the hash to a stored artifact.
        adjudicate_claim(OBSERVED, REAL, field='stack', context='slot 1 stack', executed_callee=True)

    def test_invalid_evidence_hashes_are_rejected(self):
        for h in ('0' * 64, 'a' * 64, 'D1A2' + 'f' * 60, 'f' * 63, 'g' * 64, 12345, None):
            bad = dict(OBSERVED, evidence=[dict(EVIDENCE_ENTRY, evidence_hash=h)])
            with self.assertRaises(ValueError, msg=repr(h)):
                adjudicate_claim(bad, REAL, field='stack', context='slot 1 stack', executed_callee=True)

    def test_evidence_entry_with_unknown_or_non_string_field_is_rejected(self):
        for entry in (dict(EVIDENCE_ENTRY, extra='x'), dict(EVIDENCE_ENTRY, core_revision=1),
                      dict(EVIDENCE_ENTRY, input_type='')):
            bad = dict(OBSERVED, evidence=[entry])
            with self.assertRaises(ValueError):
                adjudicate_claim(bad, REAL, field='stack', context='slot 1 stack', executed_callee=True)

    def test_non_observed_claim_carrying_evidence_is_rejected(self):
        # Evidence must not ride along on HYPOTHESIS: no implicit promotion path.
        bad = dict(HYPOTHESIS, evidence=[EVIDENCE_ENTRY])
        with self.assertRaisesRegex(ValueError, 'only an OBSERVED claim may carry evidence'):
            adjudicate_claim(bad, CALLER, field='arguments', context='slot 1 arguments')

    def test_unknown_claim_key_cannot_bypass_checks(self):
        # e.g. a self-declared executed_callee inside the claim is not a recognised key.
        bad = dict(OBSERVED, executed_callee=True)
        with self.assertRaisesRegex(ValueError, 'unknown claim keys'):
            adjudicate_claim(bad, REAL, field='returns', context='slot 1 returns')

    def test_invalid_source_kind_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'invalid source kind'):
            adjudicate_claim(OBSERVED, {'gear:src/cdrom.cpp': 'EMULATOR_TRACE'}, field='stack',
                             context='slot 1 stack', executed_callee=True)

    # ---- Issue #207 (MAJOR-1) x #208: callee ABI vs hardware observation.
    def test_hardware_observed_with_evidence_and_no_callee_is_conditionally_accepted(self):
        adjudicate_claim(OBSERVED, REAL, field='hw_bus_reset', context='hw_bus_reset',
                         executed_callee=False)

    def test_hardware_observed_without_evidence_is_rejected(self):
        for bad in (dict(OBSERVED, evidence=[]), {k: v for k, v in OBSERVED.items() if k != 'evidence'}):
            with self.assertRaisesRegex(ValueError, 'non-empty measurement evidence'):
                adjudicate_claim(bad, REAL, field='hw_bus_reset', context='hw_bus_reset',
                                 executed_callee=False)

    def test_callee_observed_with_executed_false_is_rejected_even_with_evidence(self):
        with self.assertRaisesRegex(ValueError, 'executed callee'):
            adjudicate_claim(OBSERVED, REAL, field='returns', context='slot 1 returns',
                             executed_callee=False)

    def test_callee_observed_executed_true_without_evidence_is_rejected(self):
        bad = dict(OBSERVED, evidence=[])
        with self.assertRaisesRegex(ValueError, 'non-empty measurement evidence'):
            adjudicate_claim(bad, REAL, field='returns', context='slot 1 returns', executed_callee=True)

    def test_unknown_field_name_cannot_bypass_executed_callee(self):
        for name in ('hw_bus_reset2', 'returns ', 'Returns', 'callee_returns', ''):
            with self.assertRaisesRegex(ValueError, 'unknown claim field', msg=repr(name)):
                adjudicate_claim(OBSERVED, REAL, field=name, context='x', executed_callee=False)

    def test_hardware_and_callee_field_sets_are_disjoint(self):
        self.assertFalse(CALLEE_BEHAVIOR_FIELDS & HARDWARE_OBSERVATION_FIELDS)
        self.assertEqual(CALLEE_BEHAVIOR_FIELDS, FIELDS)

    def test_caller_or_hle_evidence_cannot_promote_callee_observed(self):
        hle = {'hugo:bios.c': 'HLE_SOURCE'}
        for kinds, src in ((CALLER, 'huc:include/hucc/pcengine.inc'), (hle, 'hugo:bios.c')):
            bad = dict(OBSERVED, sources=[src])
            with self.assertRaisesRegex(ValueError, 'EMULATOR_SOURCE measurement'):
                adjudicate_claim(bad, kinds, field='returns', context='slot 1 returns',
                                 executed_callee=True)

    def test_verified_is_unreachable(self):
        bad = dict(OBSERVED, status='VERIFIED')
        for field in ('returns', 'hw_bus_reset'):
            with self.assertRaisesRegex(ValueError, 'VERIFIED'):
                adjudicate_claim(bad, REAL, field=field, context='x', executed_callee=True)


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
        # Slot 1 claims declare evidence slots but no measurement has been performed.
        for field in FIELDS:
            self.assertEqual(slot1['contract'][field]['evidence'], [])
        # Nothing in the whole ledger is VERIFIED: no executed reference-ROM callee exists.
        for api in data['apis']:
            for field in FIELDS:
                self.assertNotEqual(api['contract'][field]['status'], 'VERIFIED')


if __name__ == '__main__':
    unittest.main()
