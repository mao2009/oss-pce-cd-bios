"""Host tests for Issue #225: OBSERVED evidence must correspond to the claim's contract field.

Pure function tests on synthetic claims. EVIDENCE_ENTRY is SYNTHETIC shape-only data, not a
measurement, and its hash resolves to nothing. No emulator, ROM or BIOS execution is involved.
"""
import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))

from contracts import (  # noqa: E402
    CALLEE_BEHAVIOR_FIELDS, FIELDS, HARDWARE_OBSERVATION_FIELDS, load_contracts,
)
from contracts import adjudicate_claim  # noqa: E402
from evidence_correspondence import OBSERVATION_KIND_FIELDS, adjudicate_observation  # noqa: E402

REAL = {'gear:src/cdrom.cpp': 'EMULATOR_SOURCE'}
CALLER = {'huc:include/hucc/pcengine.inc': 'CALLER_SOURCE'}
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
                                  executed_callee=executed_callee)


class PositiveCorrespondenceTests(unittest.TestCase):
    def test_hardware_readback_supports_hw_bus_reset_without_callee(self):
        adjudicate(HW_OBSERVED, 'hw_bus_reset', executed_callee=False)

    def test_callee_kind_supports_each_callee_field_when_callee_executed(self):
        for field in CALLEE_FIELDS:
            with self.subTest(field=field):
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
        adjudicate(claim, 'hw_bus_reset')
        self.assertEqual(claim, HW_OBSERVED)


class NegativeCorrespondenceTests(unittest.TestCase):
    def test_hardware_readback_is_rejected_for_every_callee_field(self):
        # Issue #225 core case: $1804 evidence reused as stack/returns/flags/irq is refused.
        for field in CALLEE_FIELDS:
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, 'does not support field'):
                    adjudicate(HW_OBSERVED, field, executed_callee=True)

    def test_callee_kind_is_rejected_for_hw_bus_reset(self):
        with self.assertRaisesRegex(ValueError, 'does not support field'):
            adjudicate(CALLEE_OBSERVED, 'hw_bus_reset', executed_callee=True)

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

    def test_callee_field_with_hw_kind_and_no_callee_is_rejected(self):
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

    def test_kind_key_cannot_smuggle_unknown_claim_keys(self):
        with self.assertRaisesRegex(ValueError, 'unknown claim keys'):
            adjudicate(dict(CALLEE_OBSERVED, executed_callee=True), 'returns', executed_callee=True)


class FormalPathTests(unittest.TestCase):
    """Issue #225 / PR #359: the formal path (contracts.adjudicate_claim, used by validate_contracts)
    must enforce observation_kind itself. Calling only the wrapper is not enough."""

    def formal(self, claim, field, executed_callee=False, kinds=REAL):
        return adjudicate_claim(claim, kinds, field=field, context=f'slot 1 {field}',
                                executed_callee=executed_callee)

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

    def test_formal_path_accepts_hw_readback_for_hw_bus_reset(self):
        self.formal(HW_OBSERVED, 'hw_bus_reset', executed_callee=False)

    def test_formal_path_rejects_kind_on_non_observed_claim(self):
        with self.assertRaisesRegex(ValueError, 'only an OBSERVED claim may declare'):
            self.formal(dict(HYPOTHESIS, observation_kind='HW_BUS_READBACK'), 'arguments', kinds=CALLER)


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
