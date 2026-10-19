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
    CLAIM_STATUSES, EVIDENCE_TRUST_LEVEL, FIELDS, adjudicate_claim, load_contracts,
)

CALLER = {'huc:include/hucc/pcengine.inc': 'CALLER_SOURCE'}
REAL = {'gear:src/cdrom.cpp': 'EMULATOR_SOURCE'}

UNKNOWN = {'status': 'UNKNOWN', 'value': 'UNKNOWN', 'sources': [], 'confidence': 'UNKNOWN'}
HYPOTHESIS = {
    'status': 'HYPOTHESIS', 'value': 'HuCC caller-side expectation only',
    'sources': ['huc:include/hucc/pcengine.inc'], 'confidence': 'SOURCE_SUPPORTED',
}
# SYNTHETIC evidence: well-formed in shape only. It is NOT a real measurement, does not
# support any particular contract field, and its hash resolves to nothing.
EVIDENCE_ENTRY = {
    'execution_conditions': 'SYNTHETIC test fixture; no real execution took place',
    'core_revision': 'b49ae82a012eade566d72e9d61c9d95a5caa4da3',
    'input_type': 'SYNTHETIC placeholder input',
    'verification_result': 'SYNTHETIC placeholder result',
    'evidence_hash': 'd1a2f529224a70666ede15c4cf774da484bf1b96495a53e08d474f09f8ff5a50',
}
OBSERVED = {
    'status': 'OBSERVED', 'value': 'SYNTHETIC observed value (not a measurement)',
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

    def test_observed_claim_with_format_valid_evidence_is_accepted(self):
        # Issue #208 positive case: shape-valid SYNTHETIC evidence is accepted. This asserts
        # nothing about measurement, ABI correctness or authenticity (FORMAT_ONLY).
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
            adjudicate_claim(bad, CALLER, field='stack', context='slot 1 stack',
                             executed_callee=True)

    def test_observed_requires_non_empty_evidence(self):
        # Issue #208 negative case: an OBSERVED claim without measurement evidence is rejected
        # even with an EMULATOR_SOURCE and an executed callee.
        bad = dict(OBSERVED, evidence=[])
        with self.assertRaisesRegex(ValueError, 'non-empty evidence entries'):
            adjudicate_claim(bad, REAL, field='stack', context='slot 1 stack',
                             executed_callee=True)

    def test_observed_without_evidence_key_is_rejected(self):
        bad = {k: v for k, v in OBSERVED.items() if k != 'evidence'}
        with self.assertRaisesRegex(ValueError, 'non-empty evidence entries'):
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
        with self.assertRaisesRegex(ValueError, 'non-empty evidence entries'):
            adjudicate_claim(bad, REAL, field='stack', context='slot 1 stack',
                             executed_callee=True)

    def test_observed_callee_field_rejected_without_executed_callee(self):
        with self.assertRaisesRegex(ValueError, 'executed callee'):
            adjudicate_claim(OBSERVED, REAL, field='returns', context='slot 1 returns')

    def test_observed_callee_field_accepted_when_callee_executed(self):
        adjudicate_claim(OBSERVED, REAL, field='returns', context='slot 1 returns',
                         executed_callee=True)

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

    def test_format_only_acceptance_does_not_check_field_correspondence(self):
        # Documents a known limit (not a feature): the same evidence entry is accepted for
        # unrelated fields because only its shape is checked. Semantic correspondence of
        # evidence to the claim field is NOT verified; a follow-up Issue owns that.
        for field in ('stack', 'returns', 'irq', 'flags'):
            adjudicate_claim(OBSERVED, REAL, field=field, context=field, executed_callee=True)

    def test_malformed_input_types_raise_valueerror(self):
        good = dict(OBSERVED)
        for bad_claim in (None, [], 'OBSERVED', 123, ('status',)):
            with self.assertRaisesRegex(ValueError, 'claim must be a dict', msg=repr(bad_claim)):
                adjudicate_claim(bad_claim, REAL, field='stack', context='x', executed_callee=True)
        for bad_kinds in (None, [], 'EMULATOR_SOURCE', 123):
            with self.assertRaisesRegex(ValueError, 'source_kinds must be a dict', msg=repr(bad_kinds)):
                adjudicate_claim(good, bad_kinds, field='stack', context='x', executed_callee=True)
        for bad_kinds in ({1: 'EMULATOR_SOURCE'}, {None: 'EMULATOR_SOURCE'}):
            with self.assertRaisesRegex(ValueError, 'source keys must be strings'):
                adjudicate_claim(good, bad_kinds, field='stack', context='x', executed_callee=True)
        for bad_kinds in ({'gear:src/cdrom.cpp': 'NOPE'}, {'gear:src/cdrom.cpp': None},
                          {'gear:src/cdrom.cpp': ['EMULATOR_SOURCE']}):
            with self.assertRaisesRegex(ValueError, 'invalid source kind'):
                adjudicate_claim(good, bad_kinds, field='stack', context='x', executed_callee=True)
        for bad_field in (None, 1, ['stack'], b'stack'):
            with self.assertRaisesRegex(ValueError, 'claim field must be a string'):
                adjudicate_claim(good, REAL, field=bad_field, context='x', executed_callee=True)
        for bad_exec in (1, 0, 'true', None):
            with self.assertRaisesRegex(ValueError, 'executed_callee must be a bool', msg=repr(bad_exec)):
                adjudicate_claim(good, REAL, field='stack', context='x', executed_callee=bad_exec)
        with self.assertRaisesRegex(ValueError, 'claim keys must be strings'):
            adjudicate_claim({1: 'x'}, REAL, field='stack', context='x', executed_callee=True)

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

    def test_malformed_types_raise_valueerror_not_typeerror(self):
        cases = [(dict(OBSERVED, status=['OBSERVED']), 'stack', True),
                 (OBSERVED, ['x'], True),
                 (dict(HYPOTHESIS, evidence=0), 'arguments', False),
                 (dict(UNKNOWN, evidence=None), 'flags', False),
                 (OBSERVED, 'stack', 'no'),
                 (dict(OBSERVED, registers=['Q']), 'stack', True),
                 (dict(OBSERVED, evidence=[dict(EVIDENCE_ENTRY, core_revision='x')]), 'stack', True),
                 (dict(OBSERVED, evidence=[dict(EVIDENCE_ENTRY, execution_conditions=' ')]), 'stack', True)]
        for claim, field, executed in cases:
            with self.assertRaises(ValueError, msg=repr((claim, field, executed))):
                adjudicate_claim(claim, {**REAL, **CALLER}, field=field, context='x',
                                 executed_callee=executed)


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
