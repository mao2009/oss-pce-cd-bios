"""Issue #337 round 12: adversarial workflow shapes against the placement layer (check_guard_placement).

Each case writes one workflow or composite action into a temporary root and asserts that the placement check refuses it.
The scope cases (services, defaults, env in a file with no upload step) failed before scope_key_violations ran on every
file. The other cases are regression pins for refusals that already hold; they must stay refused.
"""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import artifact_guard as guard

SHA = 'a' * 40
PERMISSIONS = 'permissions:\n  contents: read\n'
UPLOAD = f'      - uses: actions/upload-artifact@{SHA}\n        with:\n          name: x\n          path: out.txt\n'


def workflow(steps='      - run: echo hi\n', top='on: [push]\n', job=''):
    return f'name: t\n{top}{PERMISSIONS}jobs:\n  j:\n    runs-on: ubuntu-latest\n{job}    steps:\n{steps}'


def placement_refuses(rel, text):
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


class ScopeWithoutUpload(unittest.TestCase):
    """A file with no upload step used to skip the scope check, so these passed."""

    def assert_refused(self, text, key):
        reason = placement_refuses('.github/workflows/ci.yml', text)
        self.assertIsNotNone(reason, f'{key} must be refused')
        self.assertIn(key, reason)

    def test_job_services_in_upload_free_file_refused(self):
        self.assert_refused(workflow(job='    services:\n      s:\n        image: alpine\n'), 'services')

    def test_job_defaults_in_upload_free_file_refused(self):
        self.assert_refused(workflow(job='    defaults:\n      run:\n        working-directory: /tmp\n'), 'defaults')

    def test_top_level_env_in_upload_free_file_refused(self):
        self.assert_refused(workflow(top='on: [push]\nenv:\n  GH_TOKEN: x\n'), 'env')

    def test_plain_build_job_without_scope_keys_passes(self):
        self.assertIsNone(placement_refuses('.github/workflows/ci.yml', workflow()))


class RegressionPins(unittest.TestCase):
    """Refusals that already hold on main. Each must stay refused."""

    def assert_refused(self, rel, text):
        self.assertIsNotNone(placement_refuses(rel, text), rel)

    def test_gh_release_in_run_refused(self):
        self.assert_refused('.github/workflows/ci.yml', workflow(steps='      - run: gh release create v1 out.txt\n'))

    def test_reusable_job_uses_refused(self):
        self.assert_refused('.github/workflows/ci.yml',
                            'name: t\non: [push]\njobs:\n  j:\n    uses: ./.github/workflows/evil.yml\n')

    def test_docker_step_refused(self):
        self.assert_refused('.github/workflows/ci.yml', workflow(steps='      - uses: docker://alpine\n'))

    def test_folded_run_publish_refused(self):
        self.assert_refused('.github/workflows/ci.yml', workflow(steps='      - run: >-\n          gh release create v1\n'))

    def test_pull_request_target_refused(self):
        self.assert_refused('.github/workflows/ci.yml',
                            'name: t\non: [pull_request_target]\njobs:\n  j:\n    runs-on: ubuntu-latest\n'
                            '    steps:\n      - run: echo hi\n')

    def test_composite_action_publish_refused(self):
        self.assert_refused('.github/actions/x/action.yml',
                            'name: x\ndescription: x\nruns:\n  using: composite\n  steps:\n'
                            '    - run: gh release create v1 out.txt\n      shell: bash\n')

    def test_escaped_quoted_uses_key_refused(self):
        self.assert_refused('.github/workflows/ci.yml',
                            workflow(steps=f'      - "\\u0075ses": actions/deploy-pages@{SHA}\n'))

    def test_second_yaml_document_publish_refused(self):
        text = workflow() + f'---\nname: t2\non: [push]\njobs:\n  k:\n    runs-on: ubuntu-latest\n' \
                            f'    steps:\n      - uses: actions/deploy-pages@{SHA}\n'
        self.assert_refused('.github/workflows/ci.yml', text)

    def test_crlf_tagged_pages_action_refused(self):
        self.assert_refused('.github/workflows/ci.yml',
                            workflow(steps='      - uses: actions/deploy-pages@v4\r\n').replace('\n', '\r\n'))

    def test_flow_style_jobs_docker_step_refused(self):
        self.assert_refused('.github/workflows/ci.yml',
                            'name: t\non: [push]\njobs: {j: {runs-on: x, steps: [{uses: docker://alpine}]}}\n')

    def test_tab_separated_docker_uses_refused(self):
        self.assert_refused('.github/workflows/ci.yml', workflow(steps='      - uses:\tdocker://alpine\n'))

    def test_unknown_sha_pinned_action_refused(self):
        self.assert_refused('.github/workflows/ci.yml', workflow(steps=f'      - uses: acme/publisher@{SHA}\n'))

    def test_gh_api_post_release_refused(self):
        self.assert_refused('.github/workflows/ci.yml', workflow(steps='      - run: gh api -X POST repos/o/r/releases\n'))

    def test_yaml_anchor_refused(self):
        self.assert_refused('.github/workflows/ci.yml',
                            workflow(steps=f'      - <<: &a {{uses: actions/checkout@{SHA}}}\n'))


FLOW_JOB = 'name: t\non: [push]\npermissions:\n  contents: read\njobs:\n  build: {runs-on: ubuntu-latest, container: alpine, steps: [{run: echo hi}]}\n'


def indented_job(indent, extra):
    """A build job whose body sits at `indent` spaces, with an extra key line (for example container) at the same depth."""
    pad = ' ' * indent
    return (f'name: t\non: [push]\npermissions:\n  contents: read\njobs:\n  build:\n{pad}runs-on: ubuntu-latest\n'
            f'{pad}{extra}\n{pad}steps:\n{pad}  - run: echo hi\n')


class ScopeByStructure(unittest.TestCase):
    """Issue #337 round 12 (review of PR 512): the scope rule reads the parsed YAML (top-level keys and jobs[*] keys), not
    line indentation. Each case below bypassed the earlier line-based check and must now be refused."""

    def assert_refused(self, text, key):
        reason = placement_refuses('.github/workflows/ci.yml', text)
        self.assertIsNotNone(reason, f'{key} must be refused')
        self.assertIn(key, reason)

    def test_job_container_indent_5_refused(self):
        self.assert_refused(indented_job(5, 'container: alpine'), 'container')

    def test_job_container_indent_6_refused(self):
        self.assert_refused(indented_job(6, 'container: alpine'), 'container')

    def test_job_container_indent_8_refused(self):
        self.assert_refused(indented_job(8, 'container: alpine'), 'container')

    def test_whole_file_indent_4_container_refused(self):
        text = workflow(job='    container: alpine\n')   # job keys at 4 spaces; the whole file is then shifted by 4
        text = '\n'.join(('    ' + line) if line else line for line in text.split('\n'))
        self.assert_refused(text, 'container')

    def test_flow_style_job_container_refused(self):
        self.assert_refused(FLOW_JOB, 'container')

    def test_quoted_job_container_indent_6_refused(self):
        self.assert_refused(indented_job(6, '"container": alpine'), 'container')

    def test_job_services_indent_6_refused(self):
        self.assert_refused(indented_job(6, 'services: {s: {image: alpine}}'), 'services')

    def test_job_defaults_indent_6_refused(self):
        self.assert_refused(indented_job(6, 'defaults: {run: {shell: bash}}'), 'defaults')

    def test_top_level_defaults_refused(self):
        self.assert_refused(workflow(top='on: [push]\ndefaults:\n  run:\n    shell: bash\n'), 'defaults')

    def test_yaml_anchor_alias_in_job_refused(self):
        text = indented_job(6, 'container: &c alpine').replace('steps:', 'x: *c\n      steps:')
        self.assert_refused(text, 'alias')

    def test_second_document_job_container_indent_6_refused(self):
        text = workflow() + '---\nname: t2\njobs:\n  k:\n      runs-on: x\n      container: alpine\n      steps:\n' \
                            '        - run: echo hi\n'
        self.assertIsNotNone(placement_refuses('.github/workflows/ci.yml', text), 'second document must be refused')


class RealWorkflowsPass(unittest.TestCase):
    """Positive control: the six real workflows must still pass the placement check (no false refusal)."""

    def test_real_repo_placement_passes(self):
        guard.check_guard_placement(guard.ROOT)


if __name__ == '__main__':
    unittest.main()
