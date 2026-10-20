"""Issue #364: measurement-point binding of evidence records. Synthetic logs in a temp dir only.

Nothing here runs the real core. A synthetic record that binds correctly is UNKNOWN, never OBSERVED.
"""
import hashlib
import tempfile
import unittest
from pathlib import Path

from tools.contracts import FIELDS
from tools.measurement_evidence import (PINNED_CORE_REVISION, REGISTRY_PATH, REGISTRY_SHA256,
                                        REVIEW_MARKER_PATH, check_pin_review, load_registry, parse_registry,
                                        verify_record)

# Independent second pin: changing spec/measurement_points.json requires changing this literal too.
PINNED_REGISTRY_SHA256 = 'b9634b90dead3dc1a7ea8c32b2a22e8951c7fd8a2f2bc08a2484eba105368819'
BINARY = 'b' * 64
HW_POINT = {'id': 'HW_BUS_READBACK_1804', 'kind': 'HW_BUS_READBACK', 'covers_fields': ['hw_bus_reset'], 'hook': 'x'}
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
        self._n = getattr(self, '_n', 0) + 1
        path = self.dir / f'{self._n}.log'  # unique name: mp_id may be a non-str in malformed-record cases
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
        self.assertEqual(hashlib.sha256(REGISTRY_PATH.read_bytes()).hexdigest(), PINNED_REGISTRY_SHA256)
        callee = self.registry['CALLEE_EXECUTION_SLOT']['covers_fields']
        self.assertEqual(set(callee), set(FIELDS))
        with tempfile.TemporaryDirectory() as d:
            tampered = Path(d) / 'points.json'
            tampered.write_bytes(REGISTRY_PATH.read_bytes().replace(b'"stack"', b'"stacq"'))
            with self.assertRaisesRegex(ValueError, 'pin'):
                load_registry(tampered)

    def test_caller_supplied_registry_is_validated_fail_closed(self):
        # covers_fields given as a string would make `field in covers` a substring test ('bus' in 'hw_bus_reset').
        bad_maps = [
            {'HW_BUS_READBACK_1804': dict(HW_POINT, covers_fields='hw_bus_reset')},
            {'HW_BUS_READBACK_1804': dict(HW_POINT, id='OTHER_ID')},
            {'HW_BUS_READBACK_1804': dict(HW_POINT, kind='OTHER')},
            {'HW_BUS_READBACK_1804': None},
            ['HW_BUS_READBACK_1804'],
        ]
        record, path = self._record(HW_LOG, 'HW_BUS_READBACK_1804')
        for bad in bad_maps:
            with self.subTest(registry=bad), self.assertRaises(ValueError):
                verify_record(record, path, field='bus', binary_sha256=BINARY, registry=bad)
        with self.assertRaises(ValueError):
            verify_record(record, path, field='bus', binary_sha256=BINARY, registry={'HW_BUS_READBACK_1804': HW_POINT})
        with self.subTest(field='non-str'), self.assertRaises(ValueError):
            self._verify(record, path, field=['hw_bus_reset'])

    def test_pin_change_without_review_marker_fails(self):
        # Committed marker must name the current registry and core pins.
        self.assertIsNone(check_pin_review())
        with tempfile.TemporaryDirectory() as d:
            marker = Path(d) / 'review.json'
            good = REVIEW_MARKER_PATH.read_bytes()
            marker.write_bytes(good.replace(REGISTRY_SHA256.encode(), b'0' * 64))
            with self.assertRaisesRegex(ValueError, 'registry pin changed'):
                check_pin_review(marker)
            marker.write_bytes(good.replace(PINNED_CORE_REVISION.encode(), b'0' * 40))
            with self.assertRaisesRegex(ValueError, 'core_revision pin changed'):
                check_pin_review(marker)
            with self.assertRaisesRegex(ValueError, 'registry pin changed'):
                check_pin_review(marker, registry_sha256='1' * 64)
            marker.write_bytes(good.replace(b'"reviewed_in_pr": 371', b'"reviewed_in_pr": 0'))
            with self.assertRaisesRegex(ValueError, 'PR number'):
                check_pin_review(marker)
            marker.write_bytes(good.replace(b'"reviewed_in_pr": 371', b'"reviewed_in_pr": 371, "extra": 1'))
            with self.assertRaisesRegex(ValueError, 'malformed'):
                check_pin_review(marker)
            with self.assertRaisesRegex(ValueError, 'missing'):
                check_pin_review(Path(d) / 'absent.json')

    def test_duplicate_measurement_point_id_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'duplicate measurement_point_id'):
            parse_registry({'schema_version': 1, 'points': [HW_POINT, dict(HW_POINT, covers_fields=['irq'])]})

    def test_malformed_registry_schema_is_rejected(self):
        def doc(*points):
            return {'schema_version': 1, 'points': list(points)}
        bad_docs = [
            None, [], {'points': []}, {'schema_version': 2, 'points': []}, {'schema_version': 1, 'points': {}},
            doc('HW_BUS_READBACK_1804'), doc(None), doc(['id']),
            doc({k: v for k, v in HW_POINT.items() if k != 'hook'}),
            doc(dict(HW_POINT, extra=1)),
            doc(dict(HW_POINT, id=['x'])), doc(dict(HW_POINT, id='lower-case')), doc(dict(HW_POINT, id=1)),
            doc(dict(HW_POINT, kind=['HW_BUS_READBACK'])), doc(dict(HW_POINT, kind='OTHER')),
            doc(dict(HW_POINT, hook=None)),
            doc(dict(HW_POINT, covers_fields='hw_bus_reset')), doc(dict(HW_POINT, covers_fields=[['x']])),
            doc(dict(HW_POINT, covers_fields=[''])), doc(dict(HW_POINT, covers_fields=[1])),
        ]
        for bad in bad_docs:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_registry(bad)

    def test_malformed_record_fields_are_controlled_rejections(self):
        # Each must raise ValueError (never TypeError/UnicodeError/KeyError escaping the verifier).
        cases = [
            ('provenance', ['REAL_CORE']), ('provenance', None),
            ('measurement_point_id', ['HW_BUS_READBACK_1804']), ('measurement_point_id', 7),
            ('measurement_point_id', {'a': 1}), ('measurement_point_id', '\ud800'),
            ('log_sha256', None), ('log_sha256', 'A' * 64), ('log_sha256', 'a' * 63), ('log_sha256', ['a' * 64]),
            ('core_revision', None), ('core_revision', ['x']),
        ]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                record, path = self._record(HW_LOG, 'HW_BUS_READBACK_1804', **{key: value})
                with self.assertRaises(ValueError):
                    self._verify(record, path)
        record, path = self._record(HW_LOG, 'HW_BUS_READBACK_1804')
        for bad_record in [None, [], dict(record, extra=1), {k: v for k, v in record.items() if k != 'log_sha256'}]:
            with self.subTest(bad_record=bad_record), self.assertRaises(ValueError):
                verify_record(bad_record, path, field='hw_bus_reset', binary_sha256=BINARY, registry=self.registry)
        for bad_binary in [None, 1, 'B' * 64, 'b' * 63]:
            with self.subTest(binary=bad_binary), self.assertRaises(ValueError):
                self._verify(record, path, binary=bad_binary)

    def test_missing_or_unreadable_log_is_rejected(self):
        record, path = self._record(HW_LOG, 'HW_BUS_READBACK_1804')
        with self.assertRaisesRegex(ValueError, 'missing'):
            self._verify(record, self.dir / 'absent.log')
        with self.assertRaises(ValueError):
            self._verify(record, None)
        with self.assertRaises(ValueError):
            self._verify(record, self.dir)

    def test_substituted_log_from_other_point_is_rejected(self):
        # A valid callee log, hashed honestly, must not bind to the $1804 readback point.
        record, path = self._record(CALLEE_LOG, 'HW_BUS_READBACK_1804')
        with self.assertRaisesRegex(ValueError, 'marker differs'):
            self._verify(record, path)

    def test_log_reused_for_uncovered_field_is_rejected(self):
        record, path = self._record(CALLEE_LOG, 'CALLEE_EXECUTION_SLOT')
        with self.assertRaisesRegex(ValueError, 'does not cover'):
            self._verify(record, path, field='hw_bus_reset', declared_kind='CALLEE_EXECUTION')


if __name__ == '__main__':
    unittest.main()
