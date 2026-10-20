"""Issue #349 (PR 349): the Pages publishing path is judged by the structure of the job that holds it.

pages_structure_violations decides from the decoded YAML. The negatives mutate the decoded document and dump it again, so
each case changes one structural fact and nothing else. Two layouts are accepted: the split layout of #374 (validate,
build, deploy) and the single-job layout that tests/fixtures/pages-single-job.yml records (the pre-#374 file).
"""
import copy
from pathlib import Path
import sys
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import artifact_guard as guard

PAGES = '.github/workflows/pages.yml'
LEGACY = ROOT / 'tests' / 'fixtures' / 'pages-single-job.yml'
DEPLOY_PINNED = 'actions/deploy-pages@d6db90164ac5ed86f2b6aed7e0febac5b3c0c03e'
UPLOAD_PINNED = 'actions/upload-pages-artifact@7b1f4a764d45c48632c6b24a0339c27f5614fb0b'


def real_text():
    return (ROOT / PAGES).read_text(encoding='utf-8')


def load(text):
    return yaml.load(text, Loader=guard._strict_loader())


def text_of(doc):
    return yaml.safe_dump(doc, sort_keys=False)


def split_doc():
    return load(real_text())


def legacy_doc():
    return load(LEGACY.read_text(encoding='utf-8'))


def problems_of(doc, label=PAGES):
    return guard.pages_structure_violations(text_of(doc), label)


class SplitLayoutTests(unittest.TestCase):
    """The approved #374 layout: validate -> build -> deploy."""

    def assertRefused(self, doc, mention=None):
        found = problems_of(doc)
        self.assertTrue(found, 'the structure check accepted a mutated split layout')
        if mention is not None:
            self.assertTrue(any(mention in p for p in found), found)

    def test_real_pages_yml_passes_as_text(self):
        self.assertEqual([], guard.pages_structure_violations(real_text(), PAGES))

    def test_real_split_layout_passes(self):
        self.assertEqual([], problems_of(split_doc()))

    def test_real_file_has_three_jobs(self):
        self.assertEqual({'validate', 'build', 'deploy'}, set(split_doc()['jobs']))

    def test_other_workflows_without_pages_actions_pass(self):
        for path in sorted((ROOT / guard.WORKFLOWS).glob('*.y*ml')):
            rel = path.relative_to(ROOT).as_posix()
            if path.name == 'pages.yml':
                continue
            with self.subTest(workflow=rel):
                self.assertEqual([], guard.pages_structure_violations(path.read_text(encoding='utf-8'), rel))

    def test_id_token_added_to_build_is_refused(self):
        doc = split_doc()
        doc['jobs']['build']['permissions']['id-token'] = 'write'
        self.assertRefused(doc, "job 'build' must have permissions")

    def test_pages_write_or_id_token_on_validate_is_refused(self):
        for key, value in (('pages', 'write'), ('id-token', 'write')):
            with self.subTest(permission=key):
                doc = split_doc()
                doc['jobs']['validate']['permissions'][key] = value
                self.assertRefused(doc, "job 'validate' must have permissions")

    def test_extra_permission_on_deploy_is_refused(self):
        doc = split_doc()
        doc['jobs']['deploy']['permissions']['contents'] = 'write'
        self.assertRefused(doc, "job 'deploy' must have permissions")

    def test_deploy_without_id_token_is_refused(self):
        doc = split_doc()
        del doc['jobs']['deploy']['permissions']['id-token']
        self.assertRefused(doc, "job 'deploy' must have permissions")

    def test_deploy_needs_must_be_build(self):
        for needs in ('validate', ['build'], ['build', 'validate']):
            with self.subTest(needs=needs):
                doc = split_doc()
                doc['jobs']['deploy']['needs'] = needs
                self.assertRefused(doc, "job 'deploy' must have needs: 'build'")

    def test_build_needs_must_be_validate(self):
        doc = split_doc()
        doc['jobs']['build']['needs'] = 'deploy'
        self.assertRefused(doc, "job 'build' must have needs: 'validate'")

    def test_deploy_condition_must_be_the_main_only_condition(self):
        for condition in (None, 'always()', "github.ref == 'refs/heads/main'",
                          "github.event_name != 'pull_request' || github.ref == 'refs/heads/main'"):
            with self.subTest(condition=condition):
                doc = split_doc()
                if condition is None:
                    del doc['jobs']['deploy']['if']
                else:
                    doc['jobs']['deploy']['if'] = condition
                self.assertRefused(doc, "job 'deploy' must have if:")

    def test_deploy_job_keys_outside_the_allowed_set_are_refused(self):
        extras = {'env': {'PATH': '/tmp/evil'}, 'container': 'ubuntu:24.04',
                  'services': {'svc': {'image': 'x'}}, 'defaults': {'run': {'shell': 'bash'}},
                  'zz-unknown': 'x'}
        for key, value in extras.items():
            with self.subTest(key=key):
                doc = split_doc()
                doc['jobs']['deploy'][key] = value
                self.assertRefused(doc, f"sets job-level {key!r}")

    def test_deploy_step_added_is_refused(self):
        added = {'name': 'Checkout', 'uses': 'actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683'}
        run = {'name': 'Probe', 'run': 'echo hi'}
        for step in (added, run):
            with self.subTest(step=step):
                doc = split_doc()
                doc['jobs']['deploy']['steps'].append(step)
                self.assertRefused(doc, "exactly one step")

    def test_deploy_step_keys_outside_the_allowed_set_are_refused(self):
        for key, value in {'env': {'PATH': '/tmp/evil'}, 'run': 'echo hi', 'if': 'always()', 'with': {'x': '1'},
                           'shell': 'bash', 'working-directory': '/tmp'}.items():
            with self.subTest(key=key):
                doc = split_doc()
                doc['jobs']['deploy']['steps'][0][key] = value
                self.assertRefused(doc, f"step sets {key!r}")

    def test_deploy_action_must_be_pinned_to_a_full_sha(self):
        for ref in ('actions/deploy-pages@v4', 'actions/deploy-pages@' + 'd6db901' * 2):
            with self.subTest(ref=ref):
                doc = split_doc()
                doc['jobs']['deploy']['steps'][0]['uses'] = ref
                self.assertRefused(doc, 'pinned to a full commit SHA')

    def test_upload_moved_into_deploy_is_refused(self):
        doc = split_doc()
        doc['jobs']['deploy']['steps'].insert(0, {'name': 'Upload', 'uses': UPLOAD_PINNED,
                                                   'with': {'path': '.artifact-stage/site'}})
        self.assertRefused(doc, 'the split layout')

    def test_deploy_action_outside_deploy_is_refused(self):
        doc = split_doc()
        doc['jobs']['validate']['steps'].append({'name': 'Deploy', 'uses': DEPLOY_PINNED})
        self.assertRefused(doc, 'the split layout')

    def test_second_deploy_action_is_refused(self):
        doc = split_doc()
        doc['jobs']['deploy']['steps'].append({'name': 'Deploy again', 'uses': DEPLOY_PINNED})
        self.assertRefused(doc, 'the split layout')

    def test_unknown_job_is_refused(self):
        doc = split_doc()
        doc['jobs']['deploy2'] = copy.deepcopy(doc['jobs']['deploy'])
        self.assertRefused(doc, 'the single-job layout')

    def test_pages_action_in_another_file_is_refused(self):
        for label in ('ci.yml', '.github/workflows/ci.yml'):
            with self.subTest(label=label):
                found = guard.pages_structure_violations(real_text(), label)
                self.assertTrue(any('outside pages.yml' in p for p in found), found)


class PrivilegeAndTriggerTests(unittest.TestCase):
    """Issue #349 review: triggers, pages: write outside the deploy job, contents: write, and run: in the deploy job."""

    def assertRefusedWith(self, doc, mention, label=PAGES):
        found = problems_of(doc, label)
        self.assertTrue(any(mention in p for p in found), found)

    def test_real_file_passes_with_push_only_deploy_condition(self):
        self.assertEqual([], problems_of(split_doc()))
        self.assertEqual("github.event_name == 'push' && github.ref == 'refs/heads/main'",
                         split_doc()['jobs']['deploy']['if'])

    def test_pull_request_target_trigger_on_pages_is_refused(self):
        doc = split_doc()
        doc[True]['pull_request_target'] = None   # the bare `on:` key loads as True (YAML 1.1)
        self.assertRefusedWith(doc, "extra ['pull_request_target']")

    def test_workflow_run_trigger_on_pages_is_refused(self):
        doc = split_doc()
        doc[True]['workflow_run'] = {'workflows': ['CI'], 'types': ['completed']}
        self.assertRefusedWith(doc, "extra ['workflow_run']")

    def test_deploy_condition_admitting_pull_request_target_is_refused(self):
        doc = split_doc()
        doc['jobs']['deploy']['if'] = "github.event_name != 'pull_request' && github.ref == 'refs/heads/main'"
        self.assertRefusedWith(doc, "job 'deploy' must have if:")

    def test_new_pages_write_job_in_pages_yml_is_refused(self):
        doc = split_doc()
        doc['jobs']['extra'] = {'runs-on': 'ubuntu-24.04', 'permissions': {'pages': 'write'},
                                'steps': [{'name': 'x', 'run': 'echo x'}]}
        self.assertRefusedWith(doc, "job 'extra' grants pages: write")

    def test_pages_write_in_a_new_workflow_file_is_refused(self):
        doc = {'on': 'push', 'jobs': {'pages-extra': {'runs-on': 'ubuntu-24.04', 'permissions': {'pages': 'write'},
                                                      'steps': [{'run': 'curl -X POST https://api.github.com/repos/x/pages'}]}}}
        self.assertRefusedWith(doc, "job 'pages-extra' grants pages: write", '.github/workflows/pages-extra.yml')

    def test_pages_write_via_write_all_in_ci_is_refused(self):
        doc = {'on': 'push', 'jobs': {'verify': {'runs-on': 'ubuntu-24.04', 'permissions': 'write-all',
                                                 'steps': [{'run': 'true'}]}}}
        self.assertRefusedWith(doc, "job 'verify' grants pages: write", '.github/workflows/ci.yml')

    def test_contents_write_on_a_non_release_job_is_refused(self):
        doc = {'on': 'push', 'jobs': {'verify': {'runs-on': 'ubuntu-24.04', 'permissions': {'contents': 'write'},
                                                 'steps': [{'run': 'true'}]}}}
        self.assertRefusedWith(doc, "job 'verify' grants contents: write", '.github/workflows/ci.yml')

    def test_run_step_in_deploy_job_is_refused(self):
        doc = split_doc()
        doc['jobs']['deploy']['steps'][0]['run'] = 'echo x'
        self.assertRefusedWith(doc, "job 'deploy' step sets 'run'")


class SingleJobLayoutTests(unittest.TestCase):
    """The pre-#374 layout: one deploy job holds the upload and the deploy."""

    def test_legacy_fixture_passes(self):
        self.assertEqual([], problems_of(legacy_doc()))

    def test_legacy_fixture_has_one_job_named_deploy(self):
        self.assertEqual({'validate', 'deploy'}, set(legacy_doc()['jobs']))

    def test_upload_and_deploy_in_build_job_is_refused(self):
        doc = legacy_doc()
        doc['jobs']['build'] = copy.deepcopy(doc['jobs']['deploy'])
        del doc['jobs']['deploy']
        found = problems_of(doc)
        self.assertTrue(found)

    def test_extra_permission_is_refused(self):
        doc = legacy_doc()
        doc['jobs']['deploy']['permissions']['contents'] = 'write'
        self.assertTrue(any("permissions" in p for p in problems_of(doc)))

    def test_unpinned_deploy_action_is_refused(self):
        doc = legacy_doc()
        for step in doc['jobs']['deploy']['steps']:
            if str(step.get('uses', '')).startswith('actions/deploy-pages@'):
                step['uses'] = 'actions/deploy-pages@v4'
        self.assertTrue(any('pinned to full commit SHAs' in p for p in problems_of(doc)))

    def test_second_deploy_action_is_refused(self):
        doc = legacy_doc()
        doc['jobs']['deploy']['steps'].append({'name': 'Deploy again', 'uses': DEPLOY_PINNED})
        self.assertTrue(problems_of(doc))


if __name__ == '__main__':
    unittest.main()
