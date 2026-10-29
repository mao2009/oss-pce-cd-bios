"""Issue #529: tools/check.py reads the guard and run allowlist from origin/main, never from the checked tree.

A fixture repository holds a trusted commit (the real guard, the real workflows and an allowlist generated from them) as
refs/remotes/origin/main. The candidate is its working tree, edited per case. Refusals are asserted, not inferred.
Which check refuses is the approval table in most cases (a changed job changes its digest, so its run: texts are refused
as unapproved); the placement check is the reason only where the stage/verify step is the sole change.
Ruleset enforcement and the CI wiring of the trusted copy are owner items (see the boundary test at the end).
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'tools'))
import artifact_guard as guard  # noqa: E402
import check  # noqa: E402

ALLOWLIST = 'tools/artifact_guard_run_allowlist.json'
CI_WORKFLOW = '.github/workflows/ci.yml'


def git(repo, *arguments):
    return subprocess.run(['git', *arguments], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


class TrustedMainFixture(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name) / 'repo'
        self.repo.mkdir()
        git(self.repo, 'init', '-q')
        git(self.repo, 'config', 'user.email', 'fixture@example.invalid')
        git(self.repo, 'config', 'user.name', 'fixture')
        (self.repo / 'tools').mkdir()
        shutil.copy(REPO / 'tools/artifact_guard.py', self.repo / 'tools/artifact_guard.py')
        shutil.copytree(REPO / '.github/workflows', self.repo / '.github/workflows')
        self.approve()
        self.commit_as_main()
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        for name in ('CI', 'GITHUB_ACTIONS', check.LOCAL_TRUST_OPT_IN):
            os.environ.pop(name, None)

    def approve(self):
        """Write an allowlist that approves exactly the run: texts in the working tree."""
        (self.repo / ALLOWLIST).write_text(json.dumps(guard.build_run_allowlist(self.repo)), encoding='utf-8')

    def commit_as_main(self):
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-q', '--allow-empty', '-m', 'trusted main')
        git(self.repo, 'update-ref', 'refs/remotes/origin/main', 'HEAD')

    def edit(self, rel, old, new):
        path = self.repo / rel
        text = path.read_text(encoding='utf-8')
        self.assertIn(old, text)
        path.write_text(text.replace(old, new, 1), encoding='utf-8')

    def enforce(self):
        check.enforce_guard(self.repo)


class NormalPath(TrustedMainFixture):
    def test_candidate_identical_to_trusted_main_passes(self):
        self.enforce()


class CandidateSelfApproval(TrustedMainFixture):
    def test_candidate_rewrites_run_text_and_approves_it_is_refused(self):
        self.edit(CI_WORKFLOW, 'run: make check', 'run: make check --self-approved')
        self.approve()   # the candidate's own table now approves its own text
        with self.assertRaisesRegex(ValueError, 'not approved|allowlist|approv'):
            self.enforce()

    def test_self_rooted_check_would_pass_so_the_trust_root_is_the_gate(self):
        # Shows the hole the fix closes: the old wiring read the table from the checked tree and passed this candidate.
        self.edit(CI_WORKFLOW, 'run: make check', 'run: make check --self-approved')
        self.approve()
        guard.enforced_check(self.repo, guard.check_guard_placement, allowlist_root=self.repo)
        with self.assertRaises(ValueError):
            self.enforce()


class CandidateRunMismatch(TrustedMainFixture):
    def test_changed_run_text_with_untouched_allowlist_is_refused(self):
        self.edit(CI_WORKFLOW, 'run: make check', 'run: make check --unapproved')
        with self.assertRaisesRegex(ValueError, 'approv'):
            self.enforce()


class CandidateGuardTampered(TrustedMainFixture):
    def test_candidate_guard_body_change_is_refused_before_any_check(self):
        with (self.repo / 'tools/artifact_guard.py').open('a', encoding='utf-8') as handle:
            handle.write('\n# candidate weakening\n')
        with self.assertRaisesRegex(ValueError, 'differs from origin/main'):
            self.enforce()


class CandidateGuardStepRemoved(TrustedMainFixture):
    def test_workflow_without_its_trusted_verify_step_is_refused(self):
        text = (self.repo / CI_WORKFLOW).read_text(encoding='utf-8')
        start = text.index('      - name: Verify staged artifacts (trusted guard)')
        end = text.index('      - name: Save verified test fixture and evidence')
        (self.repo / CI_WORKFLOW).write_text(text[:start] + text[end:], encoding='utf-8')
        self.approve()
        with self.assertRaises(ValueError):
            self.enforce()


class TrustedAllowlistMissing(TrustedMainFixture):
    def test_missing_allowlist_on_origin_main_fails_closed(self):
        git(self.repo, 'rm', '-q', ALLOWLIST)
        self.commit_as_main()
        with self.assertRaisesRegex(ValueError, 'is missing \\(fails closed\\)'):
            self.enforce()


class OriginMainUnavailable(TrustedMainFixture):
    def test_missing_origin_main_fails_closed(self):
        git(self.repo, 'update-ref', '-d', 'refs/remotes/origin/main')
        with self.assertRaisesRegex(ValueError, 'unavailable, so the trusted guard'):
            self.enforce()

    def test_local_opt_in_is_refused_under_ci(self):
        git(self.repo, 'update-ref', '-d', 'refs/remotes/origin/main')
        os.environ[check.LOCAL_TRUST_OPT_IN] = '1'
        os.environ['GITHUB_ACTIONS'] = 'true'
        with self.assertRaisesRegex(ValueError, 'refused under CI'):
            self.enforce()

    def test_local_opt_in_trusts_checked_tree_only_outside_ci(self):
        git(self.repo, 'update-ref', '-d', 'refs/remotes/origin/main')
        os.environ[check.LOCAL_TRUST_OPT_IN] = '1'
        self.edit(CI_WORKFLOW, 'run: make check', 'run: make check --self-approved')
        self.approve()
        self.enforce()   # dev-only: the checked tree is the root, so the self-approved text now passes locally


class StaleOriginMain(TrustedMainFixture):
    def test_stale_main_that_predates_a_run_change_refuses_it(self):
        stale = git(self.repo, 'rev-parse', 'HEAD')
        self.edit(CI_WORKFLOW, 'run: make check', 'run: make check --newer')
        self.approve()
        git(self.repo, 'commit', '-q', '-am', 'newer candidate')
        git(self.repo, 'update-ref', 'refs/remotes/origin/main', stale)   # origin/main is the older commit
        with self.assertRaisesRegex(ValueError, 'approv'):
            self.enforce()


class BoundaryWiring(unittest.TestCase):
    def test_ci_guard_runs_from_trusted_main_copy_not_from_check_py(self):
        # tools/check.py and Makefile are candidate code. A candidate that edits them can skip the local make check
        # gate, so CI must not rely on them: its guard-verify job runs the trusted copy directly (not through make check).
        ci = (REPO / CI_WORKFLOW).read_text(encoding='utf-8')
        self.assertRegex(ci, r'ref: main\n\s+path: \.trusted-main')
        self.assertIn('".trusted-main/tools/artifact_guard.py" --root "$GITHUB_WORKSPACE" --check-placement', ci)
        self.assertIn('--check-policy .trusted-main', ci)


if __name__ == '__main__':
    unittest.main()
