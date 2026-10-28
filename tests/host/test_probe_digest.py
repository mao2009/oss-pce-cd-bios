"""Stale-digest detection for probe source evidence (no ROM or BIOS data involved)."""
import json
import tempfile
import unittest
from pathlib import Path

from tools.probe_digest import check_evidence_file, check_recorded, sha256_file


class ProbeDigestTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.source = self.dir / "probe.cpp"
        self.source.write_bytes(b"int main() { return 0; }\n")
        self.evidence = self.dir / "handoff-evidence.json"

    def tearDown(self):
        self._tmp.cleanup()

    def test_matching_digest_passes(self):
        self.assertEqual(check_recorded(sha256_file(self.source), self.source), sha256_file(self.source))

    def test_digest_of_a_changed_source_is_stale(self):
        old = sha256_file(self.source)
        self.source.write_bytes(b"int main() { return 1; }\n")  # source changed after the record
        with self.assertRaisesRegex(ValueError, "STALE probe_source_sha256"):
            check_recorded(old, self.source)

    def test_existing_evidence_with_current_digest_passes(self):
        self.evidence.write_text(json.dumps({"probe_source_sha256": sha256_file(self.source)}))
        self.assertIsNotNone(check_evidence_file(self.evidence, self.source))

    def test_existing_evidence_with_stale_digest_fails(self):
        self.evidence.write_text(json.dumps({"probe_source_sha256": sha256_file(self.source)}))
        self.source.write_bytes(b"changed\n")
        with self.assertRaisesRegex(ValueError, "STALE"):
            check_evidence_file(self.evidence, self.source)

    def test_missing_evidence_is_not_checked(self):
        self.assertIsNone(check_evidence_file(self.dir / "absent.json", self.source))


if __name__ == "__main__":
    unittest.main()
