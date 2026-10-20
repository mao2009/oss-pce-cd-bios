"""Issue #364: measurement-point binding of evidence records. Synthetic logs in a temp dir only.

Nothing here runs the real core. A synthetic record that binds correctly is UNKNOWN, never OBSERVED.
"""
import hashlib
import tempfile
import unittest
from pathlib import Path

from tools.contracts import FIELDS
from tools.measurement_evidence import (PINNED_CORE_REVISION, REGISTRY_PATH, load_registry,
                                        verify_record)

BINARY = 'b' * 64
HW_LOG = b'MEASUREMENT_POINT HW_BUS_READBACK_1804\nSYNTHETIC_READBACK 00\n'
# SHA-256 of HW_LOG bytes, computed outside the test (printf ... | sha256sum). Not derived in-test.
HW_LOG_SHA256 = 'afac3b478530bcc1eb73241b12004944a0b6a4fc3b95053c4092e6034282abbd'
CALLEE_LOG = b'MEASUREMENT_POINT CALLEE_EXECUTION_SLOT\nEXECUTED_CALLEE 1\n'


class MeasurementEvidenceTest(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.dir = Path(self._dir.name)
        self.registry = load_registry()

    def tearDown(self):
        self._dir.cleanup()

    def _record(self, log, mp_id, provenance='SYNTHETIC', **overrides):
        path = self.dir / f'{mp_id}.log'
        path.write_bytes(log)
        record = {'provenance': provenance, 'core_revision': PINNED_CORE_REVISION, 'binary_sha256': BINARY,
                  'measurement_point_id': mp_id, 'log_sha256': hashlib.sha256(log).hexdigest()}
        record.update(overrides)
        return record, path

    def _verify(self, record, path, field='hw_bus_reset', declared_kind=None, binary=BINARY):
        return verify_record(record, path, field=field, binary_sha256=binary, declared_kind=declared_kind,
                             registry=self.registry)

    def test_positive_recomputed_hash_matches_and_synthetic_stays_unknown(self):
        record, path = self._record(HW_LOG, 'HW_BUS_READBACK_1804')
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), HW_LOG_SHA256)
        self.assertEqual(record['log_sha256'], HW_LOG_SHA256)
        self.assertEqual(self._verify(record, path, declared_kind='HW_BUS_READBACK'), 'UNKNOWN')

    def test_real_core_labelled_record_is_unknown_not_observed(self):
        # NOT OBSERVED, by design: producer authentication is not implemented (#124), so a caller-supplied
        # provenance='REAL_CORE' label must not promote a record. This fails on 3ab083c (returns OBSERVED).
        record, path = self._record(CALLEE_LOG, 'CALLEE_EXECUTION_SLOT', provenance='REAL_CORE')
        self.assertEqual(self._verify(record, path, field='stack', declared_kind='CALLEE_EXECUTION'), 'UNKNOWN')
        record, path = self._record(HW_LOG, 'HW_BUS_READBACK_1804', provenance='REAL_CORE')
        self.assertEqual(self._verify(record, path, declared_kind='HW_BUS_READBACK'), 'UNKNOWN')

    def test_tampered_log_is_rejected(self):
        record, path = self._record(HW_LOG, 'HW_BUS_READBACK_1804')
        path.write_bytes(HW_LOG.replace(b'00', b'01'))
        with self.assertRaisesRegex(ValueError, 'sha256 differs'):
            self._verify(record, path)

    def test_wrong_measurement_id_is_rejected(self):
        record, path = self._record(HW_LOG, 'CALLEE_EXECUTION_SLOT')
        with self.assertRaisesRegex(ValueError, 'marker differs'):
            self._verify(record, path, field='stack')
        record, path = self._record(HW_LOG, 'NOT_A_POINT')
        with self.assertRaisesRegex(ValueError, 'not in registry'):
            self._verify(record, path)

    def test_field_outside_point_coverage_is_rejected(self):
        # The former loophole: a $1804 readback point presented as callee behavior.
        record, path = self._record(HW_LOG, 'HW_BUS_READBACK_1804')
        with self.assertRaisesRegex(ValueError, 'does not cover'):
            self._verify(record, path, field='stack', declared_kind='CALLEE_EXECUTION')

    def test_self_declared_kind_without_matching_record_is_rejected(self):
        record, path = self._record(HW_LOG, 'HW_BUS_READBACK_1804')
        with self.assertRaisesRegex(ValueError, 'does not match the registry'):
            self._verify(record, path, declared_kind='CALLEE_EXECUTION')
        record, path = self._record(CALLEE_LOG, 'CALLEE_EXECUTION_SLOT')
        with self.assertRaisesRegex(ValueError, 'does not match the registry'):
            self._verify(record, path, field='stack', declared_kind='HW_BUS_READBACK')

    def test_callee_point_without_executed_record_is_rejected(self):
        log = b'MEASUREMENT_POINT CALLEE_EXECUTION_SLOT\n'
        record, path = self._record(log, 'CALLEE_EXECUTION_SLOT')
        with self.assertRaisesRegex(ValueError, 'EXECUTED_CALLEE'):
            self._verify(record, path, field='stack', declared_kind='CALLEE_EXECUTION')

    def test_core_and_binary_pins_are_enforced(self):
        record, path = self._record(HW_LOG, 'HW_BUS_READBACK_1804', core_revision='0' * 40)
        with self.assertRaisesRegex(ValueError, 'core_revision'):
            self._verify(record, path)
        record, path = self._record(HW_LOG, 'HW_BUS_READBACK_1804')
        with self.assertRaisesRegex(ValueError, 'binary_sha256'):
            self._verify(record, path, binary='c' * 64)

    def test_synthetic_record_cannot_reach_observed(self):
        record, path = self._record(CALLEE_LOG, 'CALLEE_EXECUTION_SLOT')
        self.assertNotEqual(self._verify(record, path, field='stack', declared_kind='CALLEE_EXECUTION'),
                            'OBSERVED')

    def test_registry_is_pinned_and_covers_contract_fields(self):
        self.assertEqual(REGISTRY_PATH.read_bytes()[-1:], b'\n')
        callee = self.registry['CALLEE_EXECUTION_SLOT']['covers_fields']
        self.assertEqual(set(callee), set(FIELDS))
        with tempfile.TemporaryDirectory() as d:
            tampered = Path(d) / 'points.json'
            tampered.write_bytes(REGISTRY_PATH.read_bytes().replace(b'"stack"', b'"stacq"'))
            with self.assertRaisesRegex(ValueError, 'pin'):
                load_registry(tampered)


if __name__ == '__main__':
    unittest.main()
