"""Issue #544 trust boundary: the SHA of each source is checked on its own (event payload, checkout result, protected base).

Real git repositories are made in temporary directories: the checkout SHA is what `git rev-parse HEAD` reports in each copy, the
expected SHA is what the event payload names. Negative cases: a SHA mismatch, a checkout without its own repository, a pull
request that targets a branch other than main, the bootstrap base (no guard on it), a self-approved run table, a reused check
result, a privileged checkout of the candidate head, and the exact read-only tuples of the policy job.
"""
from pathlib import Path
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HOST = Path(__file__).resolve().parent
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import policy_guard as policy  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
APP_ID = 15368
FIXTURE = '.github/workflows/priv_fixture.yml'
ALLOWLIST = 'tools/artifact_guard_run_allowlist.json'
CHECKOUT = 'actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683'
DOWNLOAD = 'actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093'


def git(cwd, *args):
    env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    return subprocess.run(['git', '-C', str(cwd), *args], capture_output=True, text=True, env=env, check=True).stdout.strip()


def make_repo(root, message):
    for rel in ('.github', 'tools'):
        shutil.copytree(REPO / rel, root / rel, ignore=shutil.ignore_patterns('__pycache__'))
    git(root, 'init', '-q')
    git(root, 'add', '-A')
    git(root, '-c', 'user.name=t', '-c', 'user.email=t@example.invalid', 'commit', '-q', '-m', message)
    return git(root, 'rev-parse', 'HEAD')


def event(base_sha, head_sha, base_ref='main'):
    return {'pull_request': {'base': {'ref': base_ref, 'sha': base_sha}, 'head': {'ref': 'topic', 'sha': head_sha}}}


class ProvenanceCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.trusted = Path(self.tmp.name) / 'trusted'
        self.candidate = Path(self.tmp.name) / 'candidate'
        self.trusted.mkdir()
        self.candidate.mkdir()
        self.base_sha = make_repo(self.trusted, 'trusted main')
        self.head_sha = make_repo(self.candidate, 'pull request head')

    def problems(self, ev):
        return policy.check_candidate(self.candidate, self.trusted, ev)


class SourcesAgree(ProvenanceCase):
    def test_event_and_checkouts_agree_and_pass(self):
        self.assertEqual(self.problems(event(self.base_sha, self.head_sha)), [])

    def test_trusted_checkout_sha_mismatch_is_refused(self):
        refused = self.problems(event('a' * 40, self.head_sha))
        self.assertTrue(any('SHA mismatch' in p and 'trusted checkout' in p for p in refused), refused)

    def test_candidate_checkout_sha_mismatch_is_refused(self):
        refused = self.problems(event(self.base_sha, self.base_sha))   # head checkout is not at the event head
        self.assertTrue(any('candidate checkout' in p for p in refused), refused)

    def test_checkout_without_its_own_repository_is_refused(self):
        (self.candidate / '.git').rename(self.candidate / 'git-away')   # no repository of its own: nothing to read
        self.assertIsNone(policy.git_head(self.candidate))
        self.assertTrue(self.problems(event(self.base_sha, self.head_sha)))

    def test_pull_request_to_a_branch_other_than_main_is_refused(self):
        refused = self.problems(event(self.base_sha, self.head_sha, base_ref='wave26/w10-511'))
        self.assertTrue(any('protected base' in p for p in refused), refused)

    def test_event_without_pull_request_is_refused(self):
        self.assertTrue(self.problems({}))

    def test_no_git_variable_can_redirect_the_read(self):
        os.environ['GIT_DIR'] = str(self.trusted / '.git')
        try:
            self.assertEqual(policy.git_head(self.candidate), self.head_sha)
        finally:
            del os.environ['GIT_DIR']


class Bootstrap(ProvenanceCase):
    def test_base_without_the_guard_is_not_trusted(self):
        for root in (self.trusted, self.candidate):   # the base has no guard: neither the workflow nor the script is there
            (root / '.github' / 'workflows' / 'policy-guard.yml').unlink(missing_ok=True)
            (root / 'tools' / 'policy_guard.py').unlink()
        refused = policy.check_candidate(self.candidate, self.trusted)
        self.assertTrue(any('not trusted here' in p for p in refused), refused)


class ApprovalAndReuse(ProvenanceCase):
    def test_self_approval_in_the_run_table_is_refused(self):
        table = json.loads((self.candidate / ALLOWLIST).read_text(encoding='utf-8'))
        table.append({'workflow': FIXTURE, 'job': 'priv', 'step': 'self', 'shell': None, 'runs_on': 'ubuntu-24.04',
                      'sha256': 'f' * 64, 'job_sha256': 'f' * 64})
        (self.candidate / ALLOWLIST).write_text(json.dumps(table), encoding='utf-8')
        git(self.candidate, '-c', 'user.name=t', '-c', 'user.email=t@example.invalid', 'commit', '-q', '-am', 'self approval')
        self.head_sha = git(self.candidate, 'rev-parse', 'HEAD')
        refused = self.problems(event(self.base_sha, self.head_sha))
        self.assertTrue(any(ALLOWLIST in p and 'trusted main' in p for p in refused), refused)

    def test_check_result_reused_from_another_head_is_refused(self):
        payload = {'check_runs': [{'name': 'policy-guard', 'head_sha': 'b' * 40, 'status': 'completed',
                                   'conclusion': 'success', 'app': {'id': APP_ID}}]}
        self.assertTrue(policy.check_run_records(payload, self.head_sha, APP_ID))

    def test_check_result_from_this_head_passes(self):
        payload = {'check_runs': [{'name': 'policy-guard', 'head_sha': self.head_sha, 'status': 'completed',
                                   'conclusion': 'success', 'app': {'id': APP_ID}}]}
        self.assertEqual(policy.check_run_records(payload, self.head_sha, APP_ID), [])


def priv_trusted_tree(run, checkout_with):
    """A trusted tree with one privileged job: a pinned checkout with the given with: block, then one run: text."""
    lines = ['name: priv fixture', 'on: [push]', 'jobs:', '  priv:', '    runs-on: ubuntu-24.04', '    permissions:',
             '      id-token: write', '    steps:', f'      - uses: {CHECKOUT}', '        with:']
    lines += [f'          {key}: {value}' for key, value in checkout_with.items()]
    lines += [f'      - run: {run}', '']
    return '\n'.join(lines)


class PrivilegedCheckout(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.trusted = Path(self.tmp.name) / 'trusted'
        shutil.copytree(REPO / '.github', self.trusted / '.github', ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copytree(REPO / 'tools', self.trusted / 'tools', ignore=shutil.ignore_patterns('__pycache__'))

    def uses_problems(self, checkout_with, run='test -f Makefile'):
        (self.trusted / FIXTURE).write_text(priv_trusted_tree(run, checkout_with), encoding='utf-8')
        return policy.privileged_uses_problems(self.trusted)

    def test_privileged_checkout_of_the_candidate_head_is_refused(self):
        refused = self.uses_problems({'ref': policy.CHECKOUT_HEAD_REF, 'persist-credentials': 'false'})
        self.assertTrue(refused)

    def test_data_only_head_tuple_in_another_workflow_is_refused(self):
        # the same ref and path as the policy job, but in a file that is not policy-guard.yml: the exception is per file
        self.assertTrue(self.uses_problems({'ref': policy.CHECKOUT_HEAD_REF, 'path': '.candidate',
                                            'persist-credentials': 'false'}))

    def test_pinned_action_writing_into_the_trusted_tree_is_refused(self):
        text = ('name: x\non: [push]\njobs:\n  j:\n    permissions:\n      id-token: write\n    steps:\n'
                f'      - uses: {DOWNLOAD}\n        with:\n          path: .trusted/tools\n'
                '      - run: test -f Makefile\n')
        (self.trusted / FIXTURE).write_text(text, encoding='utf-8')
        self.assertTrue(policy.privileged_uses_problems(self.trusted))

    def test_secret_in_workflow_env_makes_the_job_privileged(self):
        text = ('name: x\non: [push]\nenv:\n  T: ${{ secrets.X }}\njobs:\n  j:\n    permissions:\n      contents: read\n'
                '    steps:\n      - run: make setup\n')
        shutil.copy(REPO / 'Makefile', self.trusted / 'Makefile')
        (self.trusted / FIXTURE).write_text(text, encoding='utf-8')
        self.assertTrue(policy.closure_of(self.trusted)[2])   # privileged now: its run text is analysed, and make is refused

    def test_upper_case_secrets_context_makes_the_job_privileged(self):
        text = '\n'.join(['name: x', 'on: [push]', 'jobs:', '  j:', '    permissions:', '      contents: read', '    steps:',
                          '      - run: make setup', '        env:', '          T: ${{ SECRETS.X }}', ''])
        shutil.copy(REPO / 'Makefile', self.trusted / 'Makefile')
        (self.trusted / FIXTURE).write_text(text, encoding='utf-8')
        self.assertTrue(policy.closure_of(self.trusted)[2])

    def _download_step(self, with_lines):
        text = '\n'.join(['name: x', 'on: [push]', 'jobs:', '  j:', '    permissions:', '      id-token: write', '    steps:',
                          f'      - uses: {DOWNLOAD}', '        with:'] + with_lines + [''])
        (self.trusted / FIXTURE).write_text(text, encoding='utf-8')
        return policy.privileged_uses_problems(self.trusted)

    def test_untrusted_expression_in_a_privileged_with_value_is_refused(self):
        self.assertTrue(self._download_step(['          path: ${{ github.event.pull_request.title }}']))

    def test_fixed_run_expression_in_a_privileged_with_value_passes(self):
        self.assertEqual(self._download_step(['          name: bios-${{ github.sha }}', '          path: evidence']), [])

    def test_base_checkout_into_another_path_is_refused(self):
        self.assertTrue(self.uses_problems({'ref': policy.CHECKOUT_BASE_REF, 'path': '../x', 'persist-credentials': 'false'}))

    def test_reusable_workflow_call_in_a_privileged_job_is_refused(self):
        text = ('name: x\non: [push]\njobs:\n  j:\n    permissions:\n      id-token: write\n'
                '    uses: evil/repo/.github/workflows/x.yml@main\n    secrets: inherit\n')
        (self.trusted / FIXTURE).write_text(text, encoding='utf-8')
        self.assertTrue(policy.privileged_uses_problems(self.trusted))

    def test_privileged_checkout_of_the_base_with_credentials_is_refused(self):
        self.assertTrue(self.uses_problems({'ref': policy.CHECKOUT_BASE_REF, 'persist-credentials': 'true'}))

    def test_privileged_checkout_with_no_ref_is_refused(self):
        self.assertTrue(self.uses_problems({'persist-credentials': 'false'}))

    def test_privileged_checkout_of_another_repository_is_refused(self):
        self.assertTrue(self.uses_problems({'ref': policy.CHECKOUT_BASE_REF, 'repository': 'x/y',
                                            'persist-credentials': 'false'}))

    def test_uppercase_action_name_is_still_checked(self):
        refused = self.uses_problems({'ref': policy.CHECKOUT_HEAD_REF, 'persist-credentials': 'true'})
        self.assertTrue(refused)
        (self.trusted / FIXTURE).write_text(priv_trusted_tree('test -f Makefile', {'ref': policy.CHECKOUT_HEAD_REF,
                                                                                 'persist-credentials': 'true'})
                                            .replace(CHECKOUT, CHECKOUT.replace('actions/checkout', 'ACTIONS/CHECKOUT')),
                                            encoding='utf-8')
        self.assertTrue(policy.privileged_uses_problems(self.trusted))

    def test_other_pinned_sha_for_checkout_is_refused(self):
        (self.trusted / FIXTURE).write_text(priv_trusted_tree('test -f Makefile', {'ref': policy.CHECKOUT_BASE_REF,
                                                                                 'persist-credentials': 'false'})
                                            .replace(CHECKOUT, 'actions/checkout@' + 'a' * 40), encoding='utf-8')
        self.assertTrue(policy.privileged_uses_problems(self.trusted))

    def test_data_only_candidate_copy_in_policy_job_passes(self):
        text = (REPO / '.github/workflows/policy-guard.yml').read_text(encoding='utf-8')
        (self.trusted / '.github/workflows/policy-guard.yml').write_text(text, encoding='utf-8')
        self.assertEqual(policy.privileged_uses_problems(self.trusted), [])

    def test_malformed_event_base_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(policy.provenance_problems(Path(tmp), Path(tmp), {'pull_request': {'base': 'main', 'head': []}}))

    def test_base_checkout_of_the_protected_base_passes(self):
        self.assertEqual(self.uses_problems({'ref': policy.CHECKOUT_BASE_REF, 'path': '.trusted',
                                                     'persist-credentials': 'false'}), [])

    def test_unpinned_privileged_action_is_refused(self):
        (self.trusted / FIXTURE).write_text(priv_trusted_tree('test -f Makefile', {}).replace(CHECKOUT, 'actions/checkout@v4'),
                                            encoding='utf-8')
        self.assertTrue(policy.privileged_uses_problems(self.trusted))

    def test_pull_request_target_with_write_is_refused(self):
        text = ('name: x\non:\n  pull_request_target:\n    types: [opened]\npermissions:\n  contents: write\njobs:\n  j:\n'
                '    runs-on: ubuntu-24.04\n    steps:\n      - run: test -f Makefile\n')
        (self.trusted / FIXTURE).write_text(text, encoding='utf-8')
        self.assertTrue(policy.privileged_uses_problems(self.trusted))


class ReadOnlyTuples(unittest.TestCase):
    EXACT = 'python3 .trusted/tools/policy_guard.py --candidate .candidate --trusted .trusted'

    def refused(self, run):
        return policy._privileged_command_problems(run, 'run:')

    def test_exact_policy_tuple_is_allowed(self):
        self.assertEqual(self.refused(self.EXACT), [])

    def test_candidate_input_outside_the_exact_pair_is_refused(self):
        self.assertTrue(self.refused(self.EXACT.replace('--candidate .candidate', '--candidate .git')))

    def test_inline_form_of_the_exact_pair_is_refused(self):
        self.assertTrue(self.refused(self.EXACT.replace('--candidate .candidate', '--candidate=.candidate')))

    def test_same_basename_under_another_path_is_refused(self):
        self.assertTrue(self.refused(self.EXACT.replace('.trusted/tools/policy_guard.py', '.trusted/tools/x/policy_guard.py')))

    def test_candidate_script_is_refused(self):
        self.assertTrue(self.refused(self.EXACT.replace('.trusted/tools', '.candidate/tools')))


if __name__ == '__main__':
    unittest.main()
