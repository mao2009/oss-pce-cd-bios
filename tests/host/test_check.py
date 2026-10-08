"""Use actual Git repositories to test committed and working-tree checks."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
from check import check_git_diff


class DiffTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.repo = Path(self.directory.name) / 'repo'
        self.repo.mkdir()
        self.git('init', '-b', 'main')
        self.git('config', 'user.name', 'Fixture Author')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.file = self.repo / 'file.txt'
        self.file.write_text('initial\n')
        self.commit()
        self.base = self.git('rev-parse', 'HEAD')

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.repo, check=True, capture_output=True,
                              text=True).stdout.strip()

    def commit(self):
        self.git('add', '.')
        self.git('commit', '-m', 'original test fixture')

    def event(self):
        return {'pull_request': {'base': {'sha': self.base}}}

    def test_valid_pr_diff_from_merge_base(self):
        self.git('checkout', '-b', 'feature')
        self.file.write_text('feature change\n')
        self.commit()
        self.git('checkout', 'main')
        self.file.write_text('unrelated base whitespace  \n')
        self.commit()
        advanced_base = self.git('rev-parse', 'HEAD')
        self.git('checkout', 'feature')
        check_git_diff(self.repo, 'pull_request', {'pull_request': {'base': {'sha': advanced_base}}})

    def test_committed_pr_trailing_whitespace_fails_on_clean_checkout(self):
        self.file.write_text('bad committed whitespace  \n')
        self.commit()
        self.assertEqual(self.git('status', '--porcelain'), '')
        with self.assertRaises(subprocess.CalledProcessError):
            check_git_diff(self.repo, 'pull_request', self.event())

    def test_push_range_detects_bad_commit_before_tip(self):
        self.file.write_text('bad earlier file  \n')
        self.commit()
        (self.repo / 'other.txt').write_text('good tip\n')
        self.commit()
        with self.assertRaises(subprocess.CalledProcessError):
            check_git_diff(self.repo, 'push', {'before': self.base, 'after': self.git('rev-parse', 'HEAD')})

    def test_valid_push_range(self):
        self.file.write_text('valid push\n')
        self.commit()
        check_git_diff(self.repo, 'push', {'before': self.base, 'after': self.git('rev-parse', 'HEAD')})

    def test_unstaged_and_staged_whitespace_fail(self):
        self.file.write_text('bad working tree  \n')
        with self.assertRaises(subprocess.CalledProcessError):
            check_git_diff(self.repo)
        self.git('add', 'file.txt')
        with self.assertRaises(subprocess.CalledProcessError):
            check_git_diff(self.repo)

    def test_valid_uncommitted_changes(self):
        self.file.write_text('good unstaged\n')
        check_git_diff(self.repo)
        self.git('add', 'file.txt')
        check_git_diff(self.repo)

    def test_missing_base_and_event_metadata_fail_explicitly(self):
        for args in (('local', {}, 'nonexistent'), ('pull_request', {}, None),
                     ('pull_request', {'pull_request': {'base': {'sha': 'f' * 40}}}, None),
                     ('push', {'before': self.base, 'after': 'f' * 40}, None),
                     ('push', {'before': '0' * 40, 'after': self.base}, None)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                check_git_diff(self.repo, *args)

    def test_push_checkout_mismatch_fails(self):
        self.file.write_text('new commit\n')
        self.commit()
        with self.assertRaisesRegex(ValueError, 'does not match'):
            check_git_diff(self.repo, 'push', {'before': self.base, 'after': self.base})

    def test_disconnected_pr_history_fails(self):
        self.git('checkout', '--orphan', 'disconnected')
        self.git('add', '.')
        self.git('commit', '-m', 'independent fixture history')
        with self.assertRaisesRegex(ValueError, 'merge base'):
            check_git_diff(self.repo, 'pull_request', self.event())

    def test_shallow_checkout_never_reports_incomplete_check_as_pass(self):
        self.file.write_text('new commit\n')
        self.commit()
        shallow = Path(self.directory.name) / 'shallow'
        subprocess.run(['git', 'clone', '--depth', '1', self.repo.as_uri(), str(shallow)],
                       check=True, capture_output=True)
        with self.assertRaisesRegex(ValueError, 'shallow'):
            check_git_diff(shallow, 'pull_request', self.event())

    def test_local_root_commit_is_checked(self):
        check_git_diff(self.repo)
        self.file.write_text('bad root  \n')
        self.git('add', '.')
        self.git('commit', '--amend', '--no-edit')
        with self.assertRaises(subprocess.CalledProcessError):
            check_git_diff(self.repo)


if __name__ == '__main__':
    unittest.main()
