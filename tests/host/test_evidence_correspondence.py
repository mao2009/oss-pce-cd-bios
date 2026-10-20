"""Host tests for Issue #225: OBSERVED evidence must correspond to the claim's contract field.

Pure function tests on claims. The evidence entry is non-synthetic test data whose hash is passed in
RESOLVED as if resolve_evidence() had accepted it (Issue #359: OBSERVED needs real-core provenance).
It is not a measurement. No emulator, ROM or BIOS execution is involved; the resolve path itself is
covered in test_cdreset_ledger_adjudication.py.
"""
import copy
import hashlib
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))

from contracts import (  # noqa: E402
    CALLEE_BEHAVIOR_FIELDS, FIELDS, HARDWARE_OBSERVATION_FIELDS, load_contracts,
)
from contracts import adjudicate_claim, load_registry  # noqa: E402
from evidence_correspondence import OBSERVATION_KIND_FIELDS, adjudicate_observation  # noqa: E402

REAL = {'gear:src/cdrom.cpp': 'EMULATOR_SOURCE'}
CALLER = {'huc:include/hucc/pcengine.inc': 'CALLER_SOURCE'}
EVIDENCE_ENTRY = {
    'execution_conditions': 'REAL-CORE cd-read probe run on the pinned Geargrafx core',
    'core_revision': 'b49ae82a012eade566d72e9d61c9d95a5caa4da3',
    'input_type': 'cd-read probe sector',
    'verification_result': 'probe observation matches the stored artifact',
    'evidence_hash': hashlib.sha256(b'real-core cd-read probe artifact (correspondence fixture)\n').hexdigest(),
}
RESOLVED = frozenset({EVIDENCE_ENTRY['evidence_hash']})
OBSERVED = {
    'status': 'OBSERVED', 'value': 'observed value fixture',
    'sources': ['gear:src/cdrom.cpp'], 'confidence': 'SOURCE_SUPPORTED',
    'evidence': [EVIDENCE_ENTRY],
}
HW_OBSERVED = dict(OBSERVED, observation_kind='HW_BUS_READBACK')
CALLEE_OBSERVED = dict(OBSERVED, observation_kind='CALLEE_EXECUTION')
HYPOTHESIS = {
    'status': 'HYPOTHESIS', 'value': 'HuCC caller-side expectation only',
    'sources': ['huc:include/hucc/pcengine.inc'], 'confidence': 'SOURCE_SUPPORTED',
}
UNKNOWN = {'status': 'UNKNOWN', 'value': 'UNKNOWN', 'sources': [], 'confidence': 'UNKNOWN'}
BLOCKED = {'status': 'BLOCKED', 'value': 'UNKNOWN', 'sources': [], 'confidence': 'UNKNOWN'}
CALLEE_FIELDS = ('stack', 'returns', 'flags', 'irq')


def adjudicate(claim, field, executed_callee=False, kinds=REAL):
    return adjudicate_observation(claim, kinds, field=field, context=f'slot 1 {field}',
                                  executed_callee=executed_callee, resolved_evidence_hashes=RESOLVED)


class PositiveCorrespondenceTests(unittest.TestCase):
    # Issue #359 / #124: a kind that covers the field is necessary, never sufficient. With no producer
    # authentication, these structurally valid claims are refused. The correspondence itself still holds.
    def test_hardware_readback_matching_its_field_is_refused_without_authentication(self):
        with self.assertRaisesRegex(ValueError, 'not authenticated'):
            adjudicate(HW_OBSERVED, 'hw_bus_reset', executed_callee=False)

    def test_callee_kind_matching_each_callee_field_is_refused_without_authentication(self):
        for field in CALLEE_FIELDS:
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, 'not authenticated'):
                    adjudicate(CALLEE_OBSERVED, field, executed_callee=True)

    def test_kinds_partition_every_contract_field(self):
        self.assertEqual(OBSERVATION_KIND_FIELDS['HW_BUS_READBACK'], HARDWARE_OBSERVATION_FIELDS)
        self.assertEqual(OBSERVATION_KIND_FIELDS['CALLEE_EXECUTION'], CALLEE_BEHAVIOR_FIELDS)
        self.assertFalse(CALLEE_BEHAVIOR_FIELDS & HARDWARE_OBSERVATION_FIELDS)
        self.assertEqual(CALLEE_BEHAVIOR_FIELDS | HARDWARE_OBSERVATION_FIELDS, FIELDS | {'hw_bus_reset'})

    def test_non_observed_claims_pass_unchanged(self):
        adjudicate(UNKNOWN, 'flags')
        adjudicate(HYPOTHESIS, 'arguments', kinds=CALLER)
        adjudicate(BLOCKED, 'stack')

    def test_input_claim_is_not_mutated(self):
        claim = copy.deepcopy(HW_OBSERVED)
        with self.assertRaisesRegex(ValueError, 'not authenticated'):
            adjudicate(claim, 'hw_bus_reset')
        self.assertEqual(claim, HW_OBSERVED)


class NegativeCorrespondenceTests(unittest.TestCase):
    def test_hw_readback_kind_is_rejected_for_every_callee_field(self):
        # Issue #225 core case: a claim DECLARING HW_BUS_READBACK cannot support stack/returns/flags/irq.
        # This is a declaration-based rejection; the provenance of the evidence is not verified.
        for field in CALLEE_FIELDS:
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, 'does not support field'):
                    adjudicate(HW_OBSERVED, field, executed_callee=True)

    def test_callee_kind_is_rejected_for_hw_bus_reset(self):
        with self.assertRaisesRegex(ValueError, 'does not support field'):
            adjudicate(CALLEE_OBSERVED, 'hw_bus_reset', executed_callee=True)

    def test_hw_readback_only_passes_when_declared_as_hw_readback(self):
        # Guard against promotion: HW readback stays a hw_bus_reset fact. No callee field is accepted
        # for it, and VERIFIED is not a representable status (adjudicate_claim returns None or raises).
        for field in CALLEE_FIELDS:
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, 'does not support field'):
                    adjudicate(HW_OBSERVED, field, executed_callee=True)
        with self.assertRaisesRegex(ValueError, 'not authenticated'):
            adjudicate(HW_OBSERVED, 'hw_bus_reset')
        with self.assertRaisesRegex(ValueError, 'VERIFIED is not represented'):
            adjudicate(dict(OBSERVED, status='VERIFIED'), 'hw_bus_reset')

    def test_callee_field_with_callee_kind_but_unexecuted_callee_is_rejected(self):
        # Not executed means not measured: no promotion through a declared kind.
        for field in CALLEE_FIELDS:
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, 'executed callee'):
                    adjudicate(CALLEE_OBSERVED, field, executed_callee=False)

    def test_callee_field_with_hw_kind_is_rejected_even_when_callee_executed(self):
        # Executed callee does not make a bus readback evidence of returns: kind mismatch.
        with self.assertRaisesRegex(ValueError, 'does not support field'):
            adjudicate(HW_OBSERVED, 'returns', executed_callee=True)

    def test_hw_kind_on_callee_field_fails_executed_callee_check_first(self):
        # Ordering: with executed_callee=False the executed-callee rule fires before the kind rule.
        # The kind rule itself is covered by test_hw_readback_kind_is_rejected_for_every_callee_field.
        with self.assertRaisesRegex(ValueError, 'executed callee'):
            adjudicate(HW_OBSERVED, 'returns', executed_callee=False)

    def test_observed_without_kind_is_rejected(self):
        for field in ('hw_bus_reset',) + CALLEE_FIELDS:
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, 'requires observation_kind'):
                    adjudicate(OBSERVED, field, executed_callee=True)

    def test_unknown_or_non_string_kind_is_rejected(self):
        for kind in ('CALLEE', 'hw_bus_readback', '', None, 1, ['HW_BUS_READBACK'],
                     {'HW_BUS_READBACK': 1}):
            with self.subTest(kind=repr(kind)):
                claim = dict(HW_OBSERVED, observation_kind=kind)
                with self.assertRaisesRegex(ValueError, 'requires observation_kind'):
                    adjudicate(claim, 'hw_bus_reset')

    def test_non_observed_claim_cannot_declare_a_kind(self):
        for base, kinds, field in ((HYPOTHESIS, CALLER, 'arguments'), (UNKNOWN, REAL, 'flags'),
                                   (BLOCKED, REAL, 'stack')):
            with self.subTest(status=base['status']):
                with self.assertRaisesRegex(ValueError, 'only an OBSERVED claim may declare'):
                    adjudicate(dict(base, observation_kind='HW_BUS_READBACK'), field, kinds=kinds)

    def test_hw_observed_without_evidence_is_still_rejected(self):
        with self.assertRaisesRegex(ValueError, 'non-empty evidence entries'):
            adjudicate(dict(HW_OBSERVED, evidence=[]), 'hw_bus_reset')

    def test_kind_does_not_bypass_caller_only_source_rule(self):
        with self.assertRaisesRegex(ValueError, 'EMULATOR_SOURCE source reference'):
            adjudicate(dict(HW_OBSERVED, sources=['huc:include/hucc/pcengine.inc']),
                       'hw_bus_reset', kinds=CALLER)

    def test_unknown_field_cannot_use_a_kind_to_pass(self):
        for name in ('hw_bus_reset2', 'Returns', ''):
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, 'unknown claim field'):
                    adjudicate(CALLEE_OBSERVED, name, executed_callee=True)

    def test_non_dict_claim_fails_closed(self):
        for bad in (None, [], 'OBSERVED'):
            with self.subTest(claim=repr(bad)):
                with self.assertRaisesRegex(ValueError, 'claim must be a dict'):
                    adjudicate(bad, 'stack', executed_callee=True)

    def test_executed_callee_declared_inside_claim_is_rejected(self):
        # executed_callee is a function argument, not a claim key: a claim carrying it is an unknown key.
        with self.assertRaisesRegex(ValueError, 'unknown claim keys'):
            adjudicate(dict(CALLEE_OBSERVED, executed_callee=True), 'returns', executed_callee=True)


class FormalPathTests(unittest.TestCase):
    """Issue #225 / PR #359: the formal path (contracts.adjudicate_claim, used by validate_contracts)
    must enforce observation_kind itself. Calling only the wrapper is not enough."""

    def formal(self, claim, field, executed_callee=False, kinds=REAL):
        return adjudicate_claim(claim, kinds, field=field, context=f'slot 1 {field}',
                                executed_callee=executed_callee, resolved_evidence_hashes=RESOLVED)

    def test_formal_path_rejects_observed_callee_field_without_kind(self):
        # Old formal check accepts this: any OBSERVED claim with executed callee passes.
        with self.assertRaisesRegex(ValueError, 'requires observation_kind'):
            self.formal(OBSERVED, 'returns', executed_callee=True)

    def test_formal_path_rejects_hw_readback_reused_as_returns(self):
        with self.assertRaisesRegex(ValueError, 'does not support field'):
            self.formal(HW_OBSERVED, 'returns', executed_callee=True)

    def test_formal_path_rejects_callee_kind_when_callee_not_executed(self):
        with self.assertRaisesRegex(ValueError, 'executed callee'):
            self.formal(CALLEE_OBSERVED, 'returns', executed_callee=False)

    def test_formal_path_rejects_hw_observed_without_kind(self):
        with self.assertRaisesRegex(ValueError, 'requires observation_kind'):
            self.formal(OBSERVED, 'hw_bus_reset')

    def test_formal_path_refuses_hw_readback_for_hw_bus_reset_without_authentication(self):
        with self.assertRaisesRegex(ValueError, 'not authenticated'):
            self.formal(HW_OBSERVED, 'hw_bus_reset', executed_callee=False)

    def test_formal_path_rejects_kind_on_non_observed_claim(self):
        with self.assertRaisesRegex(ValueError, 'only an OBSERVED claim may declare'):
            self.formal(dict(HYPOTHESIS, observation_kind='HW_BUS_READBACK'), 'arguments', kinds=CALLER)


class RegistryRuleTests(unittest.TestCase):
    """Issue #364 rules inside the formal path: an OBSERVED kind must equal the kind of a registry measurement
    point covering the field, the provenance marker must be present, and the registry pin must be reviewed.
    Every case is refused (OBSERVED is unreachable until #124); the tests pin which rule refuses it."""

    def formal(self, claim, field, executed_callee=False):
        return adjudicate_claim(claim, REAL, field=field, context=f'slot 1 {field}',
                                executed_callee=executed_callee, resolved_evidence_hashes=RESOLVED)

    def registry(self, **changes):
        reg = copy.deepcopy(load_registry())
        for point in reg.values():
            if 'hw_bus_reset' in point['covers_fields']:
                point.update(changes)
        return reg

    def test_only_hw_readback_point_covers_hw_bus_reset(self):
        covering = {p['kind'] for p in load_registry().values() if 'hw_bus_reset' in p['covers_fields']}
        self.assertEqual(covering, {'HW_BUS_READBACK'})

    def test_matching_kind_is_refused_only_by_authentication(self):
        with self.assertRaisesRegex(ValueError, 'not authenticated'):
            self.formal(HW_OBSERVED, 'hw_bus_reset')

    def test_kind_not_matching_registry_point_is_rejected(self):
        with mock.patch('contracts.load_registry', return_value=self.registry(kind='CALLEE_EXECUTION')):
            with self.assertRaisesRegex(ValueError, 'no measurement point of kind HW_BUS_READBACK covers'):
                self.formal(HW_OBSERVED, 'hw_bus_reset')

    def test_field_not_covered_by_registry_point_is_rejected(self):
        with mock.patch('contracts.load_registry', return_value=self.registry(covers_fields=['returns'])):
            with self.assertRaisesRegex(ValueError, "covers field 'hw_bus_reset'"):
                self.formal(HW_OBSERVED, 'hw_bus_reset')

    def test_unreviewed_registry_pin_is_rejected(self):
        with mock.patch('contracts.check_pin_review', side_effect=ValueError('pin review marker missing')):
            with self.assertRaisesRegex(ValueError, 'pin review marker missing'):
                self.formal(HW_OBSERVED, 'hw_bus_reset')

    def test_unverified_provenance_without_realcore_marker_is_rejected(self):
        evidence = dict(EVIDENCE_ENTRY, execution_conditions='cd-read probe run on a local build')
        with self.assertRaisesRegex(ValueError, 'lacks the REALCORE provenance marker'):
            self.formal(dict(HW_OBSERVED, evidence=[evidence]), 'hw_bus_reset')

    def test_callee_kind_for_hw_bus_reset_is_rejected_by_field_rule(self):
        with self.assertRaisesRegex(ValueError, 'does not support field'):
            self.formal(CALLEE_OBSERVED, 'hw_bus_reset', executed_callee=True)


class LedgerRegressionTests(unittest.TestCase):
    """81 API regression: the shipped ledger still passes, and the wrapper agrees with adjudicate_claim."""

    def test_ledger_has_81_apis_and_no_observed_claim(self):
        data = load_contracts()
        self.assertEqual(len(data['apis']), 81)
        for api in data['apis']:
            for field in FIELDS:
                self.assertNotEqual(api['contract'][field]['status'], 'OBSERVED',
                                    msg=f"slot {api['slot']} {field}")

    def test_wrapper_accepts_every_ledger_claim_as_base_adjudication_does(self):
        data = load_contracts()
        kinds = {key: src['kind'] for key, src in data['sources'].items()}
        for api in data['apis']:
            executed = bool(api['verification'].get('executed_callee'))
            for field in FIELDS:
                claim = api['contract'][field]
                context = f"slot {api['slot']} {field}"
                with self.subTest(context=context):
                    adjudicate_observation(claim, kinds, field=field, context=context,
                                           executed_callee=executed)

    def test_hw_bus_reset_has_no_ledger_storage_for_observed(self):
        # Ledger still stores only callee fields; a kind-tagged hardware claim is not ledger content.
        data = load_contracts()
        self.assertNotIn('hw_bus_reset', data['apis'][1]['contract'])


if __name__ == '__main__':
    unittest.main()
