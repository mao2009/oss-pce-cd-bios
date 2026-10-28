"""Issue #544 review (C-2): the privileged closure of approved run: texts, with negative cases.

A privileged fixture job (id-token: write) runs one approved run: text in both the trusted copy and the candidate copy. The
trusted copy's approval table is regenerated for it (the table is policy: the candidate copy carries the same table). Each
negative case then changes one file the closure executes in the candidate, or adds a file that could shadow an import, and
asserts that policy_guard refuses it. Static matching is a ceiling: see the policy_guard docstring for what is not followed.
"""
from pathlib import Path
import json
import shutil
import subprocess
import sys
import tempfile
import unittest

HOST = Path(__file__).resolve().parent
TOOLS = Path(__file__).resolve().parents[2] / 'tools'
sys.path.insert(0, str(TOOLS))
import artifact_guard as guard  # noqa: E402
import policy_guard as policy  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
FIXTURE = '.github/workflows/priv_fixture.yml'
ALLOWLIST = 'tools/artifact_guard_run_allowlist.json'


def priv_workflow(run):
    return ('name: priv fixture\non: [push]\njobs:\n  priv:\n    runs-on: ubuntu-24.04\n    permissions:\n'
            '      id-token: write\n    steps:\n      - run: ' + run + '\n')


class ClosureCase(unittest.TestCase):
    run_text = "make setup"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.trusted = Path(self.tmp.name) / 'trusted'
        self.candidate = Path(self.tmp.name) / 'candidate'
        for root in (self.trusted, self.candidate):
            for rel in ('.github', 'tools', 'tests'):
                shutil.copytree(REPO / rel, root / rel, ignore=shutil.ignore_patterns('__pycache__'))
            shutil.copy(REPO / 'Makefile', root / 'Makefile')
        for root in (self.trusted, self.candidate):
            (root / FIXTURE).write_text(priv_workflow(self.run_text), encoding='utf-8')
        (self.trusted / ALLOWLIST).write_text(json.dumps(guard.build_run_allowlist(self.trusted)), encoding='utf-8')
        shutil.copy(self.trusted / ALLOWLIST, self.candidate / ALLOWLIST)

    def refused(self):
        return policy.check_candidate(self.candidate, self.trusted)

    def append(self, rel, text):
        with (self.candidate / rel).open('a', encoding='utf-8') as handle:
            handle.write(text)


class PrivilegedClosure(ClosureCase):
    def test_identical_privileged_fixture_passes_the_closure(self):
        # The closure is checked on its own: the artifact guard refuses id-token in a fixture job (not on its allowlist),
        # which is a separate rule and not what this file tests.
        self.assertEqual(policy.closure_problems(self.candidate, self.trusted), [])

    def test_real_trusted_tree_has_no_privileged_closure_problem(self):
        # the actual repository: its privileged jobs execute no repository file (only the trusted guard, by checkout)
        files, _, problems = policy.closure_of(REPO)
        self.assertEqual(problems, [])
        self.assertEqual(files, set())

    def test_1_approved_make_setup_with_modified_makefile_is_refused(self):
        self.append('Makefile', '\t@echo modified-by-pull-request\n')
        self.assertTrue(policy.closure_problems(self.candidate, self.trusted))

    def test_2_makefile_to_setup_py_modified_is_refused(self):
        self.append('tools/setup.py', '\n# modified: reached through Makefile setup -> tools/setup.py\n')
        self.assertTrue(policy.closure_problems(self.candidate, self.trusted))

    def test_3_setup_py_import_modified_is_refused(self):
        self.append('tools/environment.py', '\n# modified: imported by tools/setup.py\n')
        self.assertTrue(policy.closure_problems(self.candidate, self.trusted))

    def test_4_indirect_shell_call_target_swapped_is_refused(self):
        # bash tools/hop.sh (approved) runs python3 tools/leaf.py: the second hop is swapped in the candidate
        for root in (self.trusted, self.candidate):
            (root / 'tools' / 'hop.sh').write_text('#!/usr/bin/env bash\npython3 tools/leaf.py\n', encoding='utf-8')
            (root / 'tools' / 'leaf.py').write_text('VALUE = 1\n', encoding='utf-8')
            (root / FIXTURE).write_text(priv_workflow('bash tools/hop.sh'), encoding='utf-8')
        self.assertEqual(policy.closure_problems(self.candidate, self.trusted), [])
        self.append('tools/leaf.py', 'import os\nos.system("true")\n')   # swapped second hop
        self.assertTrue(policy.closure_problems(self.candidate, self.trusted))

    def test_5_shadowing_module_added_beside_executed_code_is_refused(self):
        (self.candidate / 'tools' / 'json.py').write_text('raise SystemExit("shadow")\n', encoding='utf-8')
        self.assertTrue(any('shadow' in p for p in policy.closure_problems(self.candidate, self.trusted)))

    def test_6_dynamic_construct_in_trusted_closure_is_refused_even_when_identical(self):
        (self.trusted / 'tools' / 'dyn.py').write_text('import importlib\nimportlib.import_module("x")\n', encoding='utf-8')
        (self.candidate / 'tools' / 'dyn.py').write_text((self.trusted / 'tools' / 'dyn.py').read_text(encoding='utf-8'),
                                                         encoding='utf-8')
        (self.trusted / FIXTURE).write_text(priv_workflow('python3 tools/dyn.py'), encoding='utf-8')
        (self.candidate / FIXTURE).write_text(priv_workflow('python3 tools/dyn.py'), encoding='utf-8')
        (self.trusted / ALLOWLIST).write_text(json.dumps(guard.build_run_allowlist(self.trusted)), encoding='utf-8')
        shutil.copy(self.trusted / ALLOWLIST, self.candidate / ALLOWLIST)
        self.assertTrue(policy.closure_problems(self.candidate, self.trusted))

    def test_7_privileged_workflow_deleted_is_refused(self):
        (self.candidate / FIXTURE).unlink()
        self.assertTrue(policy.policy_files_match(self.candidate, self.trusted))

    def test_8_candidate_code_is_not_executed_by_the_policy_job(self):
        marker = Path(self.tmp.name) / 'marker'
        (self.candidate / 'tools' / 'artifact_guard.py').write_text(
            'open(%r, "w").write("ran")\nraise SystemExit(0)\n' % str(marker), encoding='utf-8')
        result = subprocess.run([sys.executable, str(TOOLS / 'policy_guard.py'), '--candidate', str(self.candidate),
                                 '--trusted', str(self.trusted)], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(marker.exists(), 'candidate code ran in the policy job')
        print(result.stderr.strip().splitlines()[-1][:200])


class BypassCases(ClosureCase):
    """Review bypasses of the first closure version (Issue #544 review, round 2). Each must be refused now."""

    def set_run(self, text):
        for root in (self.trusted, self.candidate):
            (root / FIXTURE).write_text(priv_workflow(text), encoding='utf-8')

    def test_c1_bare_make_runs_the_default_goal_and_its_closure(self):
        self.set_run('make -j2')
        files, _, _ = policy.closure_of(self.trusted)
        self.assertIn('tools/setup.py', files)   # the default goal is the first rule: setup
        self.append('tools/setup.py', '\n# default goal\n')
        self.assertTrue(policy.closure_problems(self.candidate, self.trusted))

    def test_c2_variable_path_is_refused_on_the_trusted_side(self):
        self.set_run('python3 $TOOLS/setup.py')
        self.assertTrue(policy.closure_of(self.trusted)[2])

    def test_c2_extensionless_executed_path_is_followed(self):
        for root in (self.trusted, self.candidate):
            (root / 'tools' / 'hook').write_text('#!/bin/sh\necho ok\n', encoding='utf-8')
        self.set_run('tools/hook')
        self.assertEqual(policy.closure_problems(self.candidate, self.trusted), [])
        self.append('tools/hook', 'curl https://example.invalid\n')
        self.assertTrue(policy.closure_problems(self.candidate, self.trusted))

    def test_c3_shell_keyword_hides_eval_is_refused(self):
        self.set_run('if true; then eval "python3 tools/leaf.py"; fi')
        self.assertTrue(policy.closure_of(self.trusted)[2])

    def test_c3_interpreter_with_grouped_c_flag_is_refused(self):
        self.set_run('bash -ec "python3 tools/leaf.py"')
        self.assertTrue(policy.closure_of(self.trusted)[2])

    def test_c4_package_added_beside_executed_code_shadows_an_import(self):
        self.set_run('make setup')
        (self.candidate / 'tools' / 'json').mkdir()
        (self.candidate / 'tools' / 'json' / '__init__.py').write_text('raise SystemExit(1)\n', encoding='utf-8')
        self.assertTrue(any('json' in p for p in policy.closure_problems(self.candidate, self.trusted)))

    def test_c4_root_module_added_is_refused_for_python_m(self):
        self.set_run('python3 -m unittest discover -s tests/host -p "test_policy_guard_closure.py"')
        (self.candidate / 'unittest.py').write_text('raise SystemExit(1)\n', encoding='utf-8')
        self.assertTrue(any('unittest.py' in p for p in policy.closure_problems(self.candidate, self.trusted)))

    def test_m1_job_without_permissions_is_privileged(self):
        import yaml
        doc = yaml.safe_load('on: [push]\njobs:\n  j:\n    runs-on: ubuntu-24.04\n    steps:\n      - run: make setup\n')
        self.assertTrue(policy.privileged_job(doc, doc['jobs']['j']))

    def test_m1_job_reading_a_secret_is_privileged(self):
        import yaml
        doc = yaml.safe_load('on: [push]\npermissions:\n  contents: read\njobs:\n  j:\n    runs-on: ubuntu-24.04\n'
                             '    steps:\n      - run: echo ${{ secrets.X }}\n')
        self.assertTrue(policy.privileged_job(doc, doc['jobs']['j']))

    def test_m2_sinclude_is_followed(self):
        for root in (self.trusted, self.candidate):
            (root / 'tools' / 'x.mk').write_text('X = 1\n', encoding='utf-8')
            with (root / 'Makefile').open('a', encoding='utf-8') as handle:
                handle.write('sinclude tools/x.mk\n')
        self.assertEqual(policy.closure_problems(self.candidate, self.trusted), [])
        self.append('tools/x.mk', 'all:; curl https://example.invalid\n')
        self.assertTrue(policy.closure_problems(self.candidate, self.trusted))

    def test_m4_expression_in_privileged_run_is_refused(self):
        self.set_run('echo ${{ github.head_ref }}')
        self.assertTrue(policy.closure_of(self.trusted)[2])

    def test_m7_yaml_extension_workflows_are_scanned(self):
        (self.trusted / '.github' / 'workflows' / 'priv_fixture.yaml').write_text(
            priv_workflow('python3 tools/leaf.py'), encoding='utf-8')
        self.assertIn('python3 tools/leaf.py', [run for run, _ in policy._trusted_steps(self.trusted)])


class UnprivilegedBoundary(unittest.TestCase):
    def test_nightly_plan_job_is_outside_the_privileged_closure(self):
        # The nightly plan job holds no write permission: its run: text is approved exactly (approval_problems) but its
        # called scripts are not pinned. This is the documented boundary, not a silent pass: owner decision (C-3).
        import yaml
        doc = yaml.safe_load((REPO / '.github' / 'workflows' / 'nightly.yml').read_text(encoding='utf-8'))
        self.assertFalse(policy.privileged_job(doc, doc['jobs']['plan']))


if __name__ == '__main__':
    unittest.main()
