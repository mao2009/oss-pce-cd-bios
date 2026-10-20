"""Issue #337 round 10: keys that change the execution environment of a step are refused by the signing layer
(signing_violations, which runs execution_scope_violations and run_allowlist_violations).

Each negative edits the real pages.yml in memory and asserts the signing layer refuses it. The unmutated workflows and
the real file must pass. Each negative fails on the round-9 base (39569ec), which has no scope check.
"""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import artifact_guard as guard

PAGES = '.github/workflows/pages.yml'
DEPLOY_ANCHOR = '    environment:\n      name: github-pages\n'   # the deploy job's job-level key, unique in pages.yml
TOP_ANCHOR = '\njobs:\n'                                      # the top-level jobs: key, unique in pages.yml
UPLOAD_ANCHOR = '      - name: Upload static site\n'          # a uses: step in the held deploy job


def real(rel=PAGES):
    return (guard.ROOT / rel).read_text(encoding='utf-8')


def replace_once(text, anchor, new):
    assert text.count(anchor) == 1, anchor
    return text.replace(anchor, new)


def deploy_job_key(extra):
    """The real pages.yml with extra job-level lines in the deploy job, before its environment: key."""
    return replace_once(real(), DEPLOY_ANCHOR, extra + DEPLOY_ANCHOR)


def top_level(block):
    """The real pages.yml with a top-level block (for example env: or defaults:) before jobs:."""
    return replace_once(real(), TOP_ANCHOR, '\n' + block + TOP_ANCHOR)


def upload_step_key(extra):
    """The real pages.yml with an extra key on the held deploy job's uses: step Upload static site."""
    return replace_once(real(), UPLOAD_ANCHOR, UPLOAD_ANCHOR + extra)


class ExecutionScopeTests(unittest.TestCase):
    def assertSigningRefused(self, text, mention=None):
        problems = guard.signing_violations(text, PAGES)
        self.assertTrue(problems, 'signing layer accepted the mutated workflow')
        if mention is not None:
            self.assertTrue(any(mention in problem for problem in problems), problems)

    def test_real_pages_yml_passes(self):
        self.assertEqual([], guard.signing_violations(real(), PAGES))
        self.assertEqual([], guard.execution_scope_violations(real(), PAGES))

    def test_unmutated_workflows_pass(self):
        for path in sorted((guard.ROOT / guard.WORKFLOWS).glob('*.y*ml')):
            rel = path.relative_to(guard.ROOT).as_posix()
            with self.subTest(workflow=rel):
                self.assertEqual([], guard.signing_violations(path.read_text(encoding='utf-8'), rel))

    def test_job_level_env_path_in_pages_deploy_is_refused(self):
        self.assertSigningRefused(deploy_job_key('    env:\n      PATH: /tmp/evil\n'), "'env'")

    def test_job_level_container_in_pages_deploy_is_refused(self):
        self.assertSigningRefused(deploy_job_key('    container: ubuntu:24.04\n'), "'container'")

    def test_top_level_env_bash_env_is_refused(self):
        self.assertSigningRefused(top_level('env:\n  BASH_ENV: /tmp/evil\n'), 'top-level env')

    def test_top_level_env_path_is_refused(self):
        self.assertSigningRefused(top_level('env:\n  PATH: /tmp/evil\n'), 'top-level env')

    def test_top_level_defaults_run_shell_is_refused(self):
        self.assertSigningRefused(top_level('defaults:\n  run:\n    shell: bash\n'), 'top-level defaults')

    def test_unknown_job_level_key_in_pages_deploy_is_refused(self):
        self.assertSigningRefused(deploy_job_key('    zz-unknown: x\n'), "'zz-unknown'")

    def test_step_level_env_in_held_uses_step_is_refused(self):
        self.assertSigningRefused(upload_step_key('        env:\n          PATH: /tmp/evil\n'), "'env'")

    def test_mutations_are_refused_by_the_scope_check_directly(self):
        # The scope check itself (not only the combined layer) refuses each mutation.
        for text in (deploy_job_key('    env:\n      PATH: /tmp/evil\n'),
                     deploy_job_key('    container: ubuntu:24.04\n'),
                     top_level('env:\n  BASH_ENV: /tmp/evil\n'),
                     top_level('env:\n  PATH: /tmp/evil\n'),
                     top_level('defaults:\n  run:\n    shell: bash\n'),
                     deploy_job_key('    zz-unknown: x\n')):
            with self.subTest(text=text[:120]):
                self.assertTrue(guard.execution_scope_violations(text, PAGES))


if __name__ == '__main__':
    unittest.main()
