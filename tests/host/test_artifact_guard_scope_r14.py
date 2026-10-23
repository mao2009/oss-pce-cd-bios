"""Issue #337 round 14 (review of PR 512, comment 6355357008): step-level execution environment, key case, non-mapping
jobs and fail-closed parsing in scope_key_violations.

Negative cases below must be refused. They fail on the round-13 base 6d4ded7 (RED) and pass with the round-14 guard (GREEN),
except the pins at the end, which were already refused on 6d4ded7 and must stay refused.
Each case is a workflow or composite action in a temporary root (placement_refuses), or a direct call of
scope_key_violations on a text.
"""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import artifact_guard as guard

CI = '.github/workflows/ci.yml'
ACTION = '.github/actions/x/action.yml'
SHA = 'a' * 40
PERMISSIONS = 'permissions:\n  contents: read\n'


def workflow(steps='      - run: echo hi\n', top='', job=''):
    return f'name: t\non: [push]\n{top}{PERMISSIONS}jobs:\n  build:\n    runs-on: ubuntu-latest\n{job}    steps:\n{steps}'


def placement_refuses(rel, text):
    """The refusal message of check_guard_placement on a temporary root holding only this file, or None when it passes."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8', newline='')
        try:
            guard.check_guard_placement(root)
        except ValueError as error:
            return str(error)
    return None


def real(rel):
    return (guard.ROOT / rel).read_text(encoding='utf-8')


class StepEnvironmentRefused(unittest.TestCase):
    """Issue #337 round 14 (N3, item 2-4): a step-level env, shell or working-directory key, or a run: text that writes
    GITHUB_PATH, GITHUB_ENV or BASH_ENV, is refused in a file with no upload step too."""

    def assert_refused(self, text, key, rel=CI):
        reason = placement_refuses(rel, text)
        self.assertIsNotNone(reason, f'{key} must be refused')
        self.assertIn(key, reason)

    def test_step_env_path_in_upload_free_workflow_refused(self):
        self.assert_refused(workflow(steps='      - name: build\n        env:\n          PATH: /tmp/evil\n        run: python3 x.py\n'),
                            'PATH')

    def test_step_env_bash_env_refused(self):
        self.assert_refused(workflow(steps='      - name: build\n        env:\n          BASH_ENV: /tmp/evil.sh\n        run: make\n'),
                            'BASH_ENV')

    def test_step_env_pythonpath_refused(self):
        self.assert_refused(workflow(steps='      - name: build\n        env: {PYTHONPATH: /tmp/evil}\n        run: make\n'),
                            'PYTHONPATH')

    def test_step_env_flow_form_path_refused(self):
        self.assert_refused(workflow(steps='      - name: build\n        env: {GH_TOKEN: x, PATH: /tmp}\n        run: make\n'),
                            'PATH')

    def test_step_env_name_allowed_for_other_step_refused(self):
        # GH_TOKEN is allowed only for the exact (file, job, step name) entries; a new step with the same key is refused.
        self.assert_refused(workflow(steps='      - name: new step\n        env:\n          GH_TOKEN: x\n        run: make\n'),
                            'GH_TOKEN')

    def test_step_shell_refused(self):
        self.assert_refused(workflow(steps='      - name: build\n        shell: pwsh\n        run: make\n'), 'shell')

    def test_step_working_directory_refused(self):
        self.assert_refused(workflow(steps='      - name: build\n        working-directory: /tmp\n        run: make\n'),
                            'working-directory')

    def test_step_working_directory_in_upload_free_file_refused(self):
        self.assert_refused(workflow(steps='      - name: build\n        working-directory: ../evil\n        run: make\n'),
                            'working-directory', rel=CI)

    def test_run_writes_github_path_refused(self):
        self.assert_refused(workflow(steps='      - run: echo /tmp/evil >> "$GITHUB_PATH"\n'), 'GITHUB_PATH')

    def test_run_writes_github_env_path_refused(self):
        self.assert_refused(workflow(steps='      - run: echo "PATH=/tmp/evil" >> "$GITHUB_ENV"\n'), 'GITHUB_ENV')

    def test_run_exports_path_refused(self):
        self.assert_refused(workflow(steps='      - run: export PATH=/tmp/evil:$PATH; python3 x.py\n'), 'PATH')

    def test_run_appends_path_refused(self):
        self.assert_refused(workflow(steps='      - run: PATH+=:/tmp/evil; python3 x.py\n'), 'PATH')

    def test_run_exports_path_append_refused(self):
        self.assert_refused(workflow(steps='      - run: export PATH+=:/tmp/evil; python3 x.py\n'), 'PATH')

    def test_run_declares_path_append_refused(self):
        self.assert_refused(workflow(steps='      - run: declare -x PATH+=:/tmp/evil; python3 x.py\n'), 'PATH')

    def test_run_exports_quoted_path_refused(self):
        self.assert_refused(workflow(steps='      - run: export "PATH=/tmp/evil:$PATH"; python3 x.py\n'), 'PATH')

    def test_run_exports_single_quoted_path_refused(self):
        self.assert_refused(workflow(steps="      - run: export 'PATH=/tmp/evil:$PATH'; python3 x.py\n"), 'PATH')

    def test_run_names_that_contain_path_are_not_refused(self):
        for run in ('MY_PATH=/x make', 'PYTHONPATH=/x make', 'GOPATH=/x go build', 'echo PATHS=1'):
            self.assertIsNone(placement_refuses(CI, workflow(steps=f'      - run: {run}\n')), run)

    def test_composite_action_step_env_refused(self):
        text = ('name: x\ndescription: x\nruns:\n  using: composite\n  steps:\n'
                '    - run: echo hi\n      env:\n        PATH: /tmp/evil\n')
        self.assert_refused(text, 'PATH', rel=ACTION)

    def test_composite_action_run_writes_github_path_refused(self):
        text = 'name: x\ndescription: x\nruns:\n  using: composite\n  steps:\n    - run: echo /tmp >> "$GITHUB_PATH"\n'
        self.assert_refused(text, 'GITHUB_PATH', rel=ACTION)

    def test_allowlisted_step_with_extra_path_key_refused(self):
        # The real ci.yml step is allowed GH_TOKEN only. Adding PATH to that step must be refused.
        text = real('.github/workflows/ci.yml')
        anchor = '          GH_TOKEN: ${{ github.token }}\n        run: |\n          python3 tools/provenance_guard.py'
        self.assertEqual(1, text.count(anchor))
        mutated = text.replace(anchor, '          GH_TOKEN: ${{ github.token }}\n          PATH: /tmp/evil\n'
                                       '        run: |\n          python3 tools/provenance_guard.py')
        problems = guard.scope_key_violations(mutated, CI)
        self.assertTrue(any('PATH' in p for p in problems), problems)


class KeyCaseAndShape(unittest.TestCase):
    """Issue #337 round 14 (N1, N2, N4): uppercase reserved keys, jobs: that is not a mapping, and a syntax error are
    refused by scope_key_violations itself."""

    def test_uppercase_job_container_refused(self):
        text = workflow(job='    CONTAINER: alpine\n')
        problems = guard.scope_key_violations(text, CI)
        self.assertTrue(any('CONTAINER' in p or 'container' in p.lower() for p in problems), problems)

    def test_uppercase_job_container_refused_by_placement(self):
        self.assertIsNotNone(placement_refuses(CI, workflow(job='    CONTAINER: alpine\n')))

    def test_uppercase_top_level_env_refused(self):
        self.assertIsNotNone(placement_refuses(CI, workflow(top='ENV:\n  GH_TOKEN: x\n')))

    def test_uppercase_step_env_key_refused(self):
        self.assertIsNotNone(placement_refuses(CI, workflow(steps='      - name: b\n        ENV:\n          PATH: /tmp\n'
                                                                    '        run: make\n')))

    def test_mixed_case_step_shell_key_refused(self):
        self.assertIsNotNone(placement_refuses(CI, workflow(steps='      - name: b\n        Shell: bash\n        run: make\n')))

    def test_jobs_as_list_with_container_refused(self):
        text = 'name: t\non: [push]\njobs:\n  - build:\n      runs-on: x\n      container: alpine\n      steps:\n        - run: echo\n'
        problems = guard.scope_key_violations(text, CI)
        self.assertTrue(problems, 'a jobs: list must be refused, not skipped')
        self.assertIsNotNone(placement_refuses(CI, text))

    def test_jobs_as_string_refused(self):
        text = 'name: t\non: [push]\njobs: nothing\n'
        self.assertTrue(guard.scope_key_violations(text, CI), 'jobs: must be a mapping')

    def test_job_as_string_refused(self):
        text = 'name: t\non: [push]\njobs:\n  build: "x"\n'
        self.assertTrue(guard.scope_key_violations(text, CI), 'a job that is not a mapping must be refused')

    def test_steps_as_mapping_refused(self):
        text = 'name: t\non: [push]\njobs:\n  build:\n    runs-on: x\n    steps: {run: echo}\n'
        self.assertTrue(guard.scope_key_violations(text, CI), 'steps: must be a list')

    def test_syntax_error_fails_closed_inside_scope_check(self):
        text = workflow().replace('    runs-on: ubuntu-latest', '    runs-on: [ubuntu-latest')   # unclosed flow sequence
        problems = guard.scope_key_violations(text, CI)
        self.assertTrue(problems, 'a syntax error must refuse in scope_key_violations itself, not return []')

    def test_non_mapping_document_fails_closed(self):
        self.assertTrue(guard.scope_key_violations('- a\n- b\n', CI))

    def test_composite_action_without_runs_mapping_refused(self):
        self.assertTrue(guard.scope_key_violations('name: x\ndescription: x\nruns: echo\n', ACTION))


class RegressionPins(unittest.TestCase):
    """Previously bypassing shapes (review of PR 512). Already refused on 6d4ded7; they must stay refused."""

    def assert_refused(self, text, key):
        reason = placement_refuses(CI, text)
        self.assertIsNotNone(reason, f'{key} must be refused')
        self.assertIn(key, reason)

    def test_job_container_indent_5_refused(self):
        self.assert_refused(
            'name: t\non: [push]\npermissions:\n  contents: read\njobs:\n  build:\n     runs-on: x\n     container: alpine\n'
            '     steps:\n       - run: echo hi\n', 'container')

    def test_job_container_indent_8_refused(self):
        self.assert_refused(
            'name: t\non: [push]\npermissions:\n  contents: read\njobs:\n  build:\n        runs-on: x\n        container: alpine\n'
            '        steps:\n          - run: echo hi\n', 'container')

    def test_whole_file_indent_4_refused(self):
        text = workflow(job='    container: alpine\n')
        text = '\n'.join(('    ' + line) if line else line for line in text.split('\n'))
        self.assert_refused(text, 'container')

    def test_flow_style_job_container_refused(self):
        self.assert_refused('name: t\non: [push]\njobs: {build: {runs-on: x, container: alpine, steps: [{run: echo}]}}\n',
                            'container')

    def test_quoted_job_container_key_refused(self):
        self.assert_refused('name: t\non: [push]\npermissions:\n  contents: read\njobs:\n  build:\n    runs-on: x\n'
                            '    "container": alpine\n    steps:\n      - run: echo\n', 'container')

    def test_job_services_indent_6_refused(self):
        self.assert_refused('name: t\non: [push]\npermissions:\n  contents: read\njobs:\n  build:\n    runs-on: x\n'
                            '      services: {s: {image: alpine}}\n    steps:\n      - run: echo\n', 'services')

    def test_job_defaults_indent_6_refused(self):
        self.assert_refused('name: t\non: [push]\npermissions:\n  contents: read\njobs:\n  build:\n    runs-on: x\n'
                            '      defaults: {run: {shell: bash}}\n    steps:\n      - run: echo\n', 'defaults')


class RealWorkflowsAndAllowlist(unittest.TestCase):
    """Positive controls: no false refusal on the six real workflows, and every allowlist entry carries a reason."""

    def test_real_workflows_scope_passes(self):
        for path in sorted((guard.ROOT / '.github' / 'workflows').glob('*.yml')):
            rel = path.relative_to(guard.ROOT).as_posix()
            self.assertEqual([], guard.scope_key_violations(real(rel), rel), rel)

    def test_real_repo_placement_passes(self):
        guard.check_guard_placement(guard.ROOT)

    def test_composite_action_without_env_passes(self):
        text = 'name: x\ndescription: x\nruns:\n  using: composite\n  steps:\n    - run: echo hi\n'
        self.assertEqual([], guard.scope_key_violations(text, ACTION))

    def test_every_allowlist_entry_has_a_reason(self):
        for key, reason in guard.STEP_EXCEPTIONS.items():
            self.assertTrue(isinstance(reason, str) and len(reason) > 20, key)


if __name__ == '__main__':
    unittest.main()
