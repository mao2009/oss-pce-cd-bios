"""Evidence artifact resolution (Issue #224): synthetic artifacts in a temp dir only, no real core or BIOS data.

Acceptance means the artifact bytes match evidence_hash and core_revision matches the pin. It is not
authenticity (Issue #124 / #171) and does not make a claim VERIFIED.
"""
import hashlib
import os
import tempfile
import unittest
from pathlib import Path

from tools.contracts import (ARTIFACT_TRUST_LEVEL, EVIDENCE_TRUST_LEVEL, PINNED_CORE_REVISION,
                             resolve_evidence)

ARTIFACT = b'synthetic evidence bytes, not a measurement'
OTHER = b'a different synthetic artifact'
DIGEST = hashlib.sha256(ARTIFACT).hexdigest()


def _symlinks_supported():
    with tempfile.TemporaryDirectory() as d:
        target = Path(d) / 'target'
        target.write_bytes(b'x')
        try:
            os.symlink(target, Path(d) / 'link')
        except (OSError, NotImplementedError):
            return False
        return True


SYMLINKS_OK = _symlinks_supported()


def entry(**overrides):
    base = {'execution_conditions': 'synthetic', 'core_revision': PINNED_CORE_REVISION,
            'input_type': 'synthetic', 'verification_result': 'synthetic', 'evidence_hash': DIGEST}
    base.update(overrides)
    return base


class ResolveEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        (self.dir / DIGEST).write_bytes(ARTIFACT)

    def tearDown(self):
        self.tmp.cleanup()

    def resolve(self, e, **kwargs):
        return resolve_evidence(e, artifact_dir=self.dir, **kwargs)

    def test_matching_artifact_and_pinned_revision_resolve(self):
        self.assertEqual(self.resolve(entry()), self.dir / DIGEST)

    def test_trust_levels_are_distinct_concepts(self):
        self.assertEqual(EVIDENCE_TRUST_LEVEL, 'FORMAT_ONLY')
        self.assertNotEqual(ARTIFACT_TRUST_LEVEL, EVIDENCE_TRUST_LEVEL)

    def test_missing_artifact_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'missing or not a regular file'):
            self.resolve(entry(evidence_hash=hashlib.sha256(b'absent').hexdigest()))

    def test_format_valid_but_unstored_synthetic_hash_is_rejected(self):
        # The FORMAT_ONLY synthetic hash used in the ledger tests has no stored artifact: never PASS.
        with self.assertRaisesRegex(ValueError, 'missing or not a regular file'):
            self.resolve(entry(evidence_hash='d1a2f529224a70666ede15c4cf774da484bf1b96495a53e08d474f09f8ff5a50'))

    def test_tampered_artifact_under_recorded_name_is_rejected(self):
        (self.dir / DIGEST).write_bytes(ARTIFACT + b'x')
        with self.assertRaisesRegex(ValueError, 'does not match evidence_hash'):
            self.resolve(entry())

    def test_different_file_substituted_under_name_is_rejected(self):
        other_name = hashlib.sha256(OTHER).hexdigest()
        (self.dir / other_name).write_bytes(OTHER)
        (self.dir / DIGEST).write_bytes(OTHER)
        with self.assertRaisesRegex(ValueError, 'does not match evidence_hash'):
            self.resolve(entry())
        self.assertEqual(self.resolve(entry(evidence_hash=other_name)), self.dir / other_name)

    def test_recorded_revision_other_than_pin_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'differs from pinned'):
            self.resolve(entry(core_revision='a' * 40))

    def test_one_character_revision_difference_is_rejected(self):
        # 40-hex and format-valid, but not the pin: only the pin check catches it.
        near = PINNED_CORE_REVISION[:-1] + ('1' if PINNED_CORE_REVISION[-1] != '1' else '2')
        with self.assertRaisesRegex(ValueError, 'differs from pinned'):
            self.resolve(entry(core_revision=near))

    def test_path_traversal_and_non_digest_names_are_rejected(self):
        for bad in ('../' + DIGEST[3:], '..\\' + DIGEST[3:], '/' + DIGEST[1:], DIGEST.upper(),
                    DIGEST[:-1], DIGEST + '0', 'a' * 64, ''):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    self.resolve(entry(evidence_hash=bad))

    def test_malformed_entries_are_rejected_before_any_file_access(self):
        for bad in (None, 'x', [], {}, dict(entry(), extra='x'),
                    {k: v for k, v in entry().items() if k != 'input_type'},
                    entry(core_revision=PINNED_CORE_REVISION.upper()), entry(execution_conditions=' ')):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    self.resolve(bad)

    def test_injected_pin_is_enforced(self):
        with self.assertRaisesRegex(ValueError, 'differs from pinned'):
            self.resolve(entry(), pinned_revision='b' * 40)
        self.assertEqual(len(PINNED_CORE_REVISION), 40)

    @unittest.skipUnless(SYMLINKS_OK, 'symlink creation not permitted on this OS/account (SKIP)')
    def test_symlinked_artifact_is_rejected_even_when_target_hash_matches(self):
        # The link target has the correct bytes and name, so only the is_symlink() guard can reject it.
        artdir = self.dir / 'art'
        artdir.mkdir()
        (self.dir / 'real.bin').write_bytes(ARTIFACT)
        os.symlink(self.dir / 'real.bin', artdir / DIGEST)
        with self.assertRaisesRegex(ValueError, 'missing or not a regular file'):
            resolve_evidence(entry(), artifact_dir=artdir)

    def test_artifact_directory_is_not_consulted_for_unrelated_names(self):
        # Only <dir>/<evidence_hash> is read; a sibling file with another name never satisfies the lookup.
        (self.dir / 'other.bin').write_bytes(ARTIFACT)
        with self.assertRaises(ValueError):
            self.resolve(entry(evidence_hash=hashlib.sha256(b'unrelated').hexdigest()))
        self.assertTrue(os.path.isfile(self.dir / 'other.bin'))


if __name__ == '__main__':
    unittest.main()
