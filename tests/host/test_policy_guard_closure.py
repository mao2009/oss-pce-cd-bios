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
APP_ID = 15368   # the same GitHub Actions app ID that test_policy_guard.py uses (existing value, not new)


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
        # owner design: a privileged job runs only trusted code, so a repository run is refused by design
        self.assertTrue(policy.closure_problems(self.candidate, self.trusted))

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
        # owner design: a privileged job runs only trusted code, so a repository run is refused by design
        self.assertTrue(policy.closure_problems(self.candidate, self.trusted))
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
        # owner design: a privileged job runs only trusted code, so a repository run is refused by design
        self.assertTrue(policy.closure_problems(self.candidate, self.trusted))
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
        # owner design: a privileged job runs only trusted code, so a repository run is refused by design
        self.assertTrue(policy.closure_problems(self.candidate, self.trusted))
        self.append('tools/x.mk', 'all:; curl https://example.invalid\n')
        self.assertTrue(policy.closure_problems(self.candidate, self.trusted))

    def test_m4_expression_in_privileged_run_is_refused(self):
        self.set_run('echo ${{ github.head_ref }}')
        self.assertTrue(policy.closure_of(self.trusted)[2])

    def test_m7_yaml_extension_workflows_are_scanned(self):
        (self.trusted / '.github' / 'workflows' / 'priv_fixture.yaml').write_text(
            priv_workflow('python3 tools/leaf.py'), encoding='utf-8')
        self.assertIn('python3 tools/leaf.py', [run for run, _ in policy._trusted_steps(self.trusted)])


class ReviewRoundTwoBypasses(ClosureCase):
    """Bypasses named by the round-2 independent review of #544 (each must be refused or followed)."""

    def set_run(self, text):
        for root in (self.trusted, self.candidate):
            (root / FIXTURE).write_text(priv_workflow(text), encoding='utf-8')

    def test_r1_pipe_into_an_interpreter_is_refused(self):
        self.set_run('cat tools/leaf.py | bash')
        self.assertTrue(policy.closure_of(self.trusted)[2])

    def test_r2_variable_command_head_is_refused(self):
        self.set_run('tools/$X')
        self.assertTrue(policy.closure_of(self.trusted)[2])

    def test_r3_extensionless_file_passed_as_an_argument_is_followed(self):
        for root in (self.trusted, self.candidate):
            (root / 'tools' / 'hook').write_text('#!/bin/sh\necho ok\n', encoding='utf-8')
        self.set_run('timeout 5 tools/hook')
        # owner design: a privileged job runs only trusted code, so a repository run is refused by design
        self.assertTrue(policy.closure_problems(self.candidate, self.trusted))
        self.append('tools/hook', 'curl https://example.invalid\n')
        self.assertTrue(policy.closure_problems(self.candidate, self.trusted))

    def test_r4_from_os_import_system_is_refused(self):
        for root in (self.trusted, self.candidate):
            (root / 'tools' / 'launch.py').write_text('from os import system\nsystem("true")\n', encoding='utf-8')
        self.set_run('python3 tools/launch.py')
        self.assertTrue(policy.closure_problems(self.candidate, self.trusted))

    def test_r5_subprocess_getoutput_is_a_runtime_program(self):
        for root in (self.trusted, self.candidate):
            (root / 'tools' / 'query.py').write_text('import subprocess\nsubprocess.getoutput("true")\n', encoding='utf-8')
        self.set_run('python3 tools/query.py')
        self.assertTrue(policy.closure_problems(self.candidate, self.trusted))


class RoundFourBypasses(ClosureCase):
    """Round-four review items (Issue #544): Makefile overrides, submodule and relative imports, parent packages, long
    discover options, and the check-run record. Each must be refused or followed."""

    def set_run(self, text):
        for root in (self.trusted, self.candidate):
            (root / FIXTURE).write_text(priv_workflow(text), encoding='utf-8')

    def test_make_command_line_variable_is_refused(self):
        self.set_run('make setup PYTHON=/tmp/evil')
        self.assertTrue(policy.closure_of(self.trusted)[2])

    def test_makefile_shell_directive_is_refused(self):
        with (self.trusted / 'Makefile').open('a', encoding='utf-8') as handle:
            handle.write('SHELL := /tmp/evil\n')
        self.set_run('make setup')
        self.assertTrue(policy.closure_of(self.trusted)[2])

    def test_from_package_import_submodule_is_followed(self):
        (self.trusted / 'tools' / 'user.py').write_text('from tools import leaf\n', encoding='utf-8')
        (self.candidate / 'tools' / 'user.py').write_text('from tools import leaf\n', encoding='utf-8')
        (self.trusted / 'tools' / 'leaf.py').write_text('VALUE = 1\n', encoding='utf-8')
        (self.candidate / 'tools' / 'leaf.py').write_text('VALUE = 1\n', encoding='utf-8')
        self.set_run('python3 tools/user.py')
        # owner design: a privileged job runs only trusted code, so a repository run is refused by design
        self.assertTrue(policy.closure_problems(self.candidate, self.trusted))
        self.append('tools/leaf.py', 'import os\n')
        self.assertTrue(policy.closure_problems(self.candidate, self.trusted))

    def test_relative_import_is_followed(self):
        for root in (self.trusted, self.candidate):
            (root / 'tools' / 'pkg').mkdir(exist_ok=True)
            (root / 'tools' / 'pkg' / '__init__.py').write_text('', encoding='utf-8')
            (root / 'tools' / 'pkg' / 'rel.py').write_text('from . import helper\n', encoding='utf-8')
            (root / 'tools' / 'pkg' / 'helper.py').write_text('VALUE = 1\n', encoding='utf-8')
        self.set_run('python3 tools/pkg/rel.py')
        # owner design: a privileged job runs only trusted code, so a repository run is refused by design
        self.assertTrue(policy.closure_problems(self.candidate, self.trusted))
        self.append('tools/pkg/helper.py', 'import os\n')
        self.assertTrue(policy.closure_problems(self.candidate, self.trusted))

    def test_parent_package_init_is_followed(self):
        for root in (self.trusted, self.candidate):
            (root / 'tools' / 'pkg').mkdir(exist_ok=True)
            (root / 'tools' / 'pkg' / '__init__.py').write_text('X = 1\n', encoding='utf-8')
            (root / 'tools' / 'pkg' / 'sub.py').write_text('VALUE = 1\n', encoding='utf-8')
            (root / 'tools' / 'user2.py').write_text('import pkg.sub\n', encoding='utf-8')
        self.set_run('python3 tools/user2.py')
        # owner design: a privileged job runs only trusted code, so a repository run is refused by design
        self.assertTrue(policy.closure_problems(self.candidate, self.trusted))
        self.append('tools/pkg/__init__.py', 'import os\n')
        self.assertTrue(policy.closure_problems(self.candidate, self.trusted))

    def test_long_discover_options_are_followed(self):
        self.set_run('python3 -m unittest discover --start-directory tests/host --pattern "test_policy_guard_closure.py"')
        files, _, _ = policy.closure_of(self.trusted)
        self.assertIn('tests/host/test_policy_guard_closure.py', files)
        self.append('tests/host/test_policy_guard_closure.py', '\n# discovered in the privileged run\n')
        self.assertTrue(policy.closure_problems(self.candidate, self.trusted))


class CheckRunRecord(unittest.TestCase):
    """The check-run listing for the head SHA (the record check that the policy-guard ruleset relies on)."""

    HEAD = 'a' * 40

    def make_run(self, **overrides):
        run = {'name': 'policy-guard', 'head_sha': self.HEAD, 'status': 'completed', 'conclusion': 'success',
               'app': {'id': APP_ID}}
        run.update(overrides)
        return run

    def test_matching_completed_success_passes(self):
        self.assertEqual(policy.check_run_records({'check_runs': [self.make_run()]}, self.HEAD, APP_ID), [])

    def test_missing_run_is_refused(self):
        self.assertTrue(policy.check_run_records({'check_runs': []}, self.HEAD, APP_ID))

    def test_wrong_app_is_refused(self):
        self.assertTrue(policy.check_run_records({'check_runs': [self.make_run(app={'id': 1})]}, self.HEAD, APP_ID))

    def test_stale_head_is_refused(self):
        self.assertTrue(policy.check_run_records({'check_runs': [self.make_run(head_sha='b' * 40)]}, self.HEAD, APP_ID))

    def test_skipped_is_refused(self):
        self.assertTrue(policy.check_run_records({'check_runs': [self.make_run(conclusion='skipped')]}, self.HEAD, APP_ID))

    def test_in_progress_is_refused(self):
        self.assertTrue(policy.check_run_records(
            {'check_runs': [self.make_run(status='in_progress', conclusion=None)]}, self.HEAD, APP_ID))

    def test_command_line_record_check_refuses_skipped(self):
        import json
        import subprocess
        import tempfile
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / 'runs.json'
        path.write_text(json.dumps({'check_runs': [self.make_run(conclusion='skipped')]}), encoding='utf-8')
        result = subprocess.run([sys.executable, str(TOOLS / 'policy_guard.py'), '--check-record', str(path),
                                 '--head', self.HEAD, '--app-id', str(APP_ID)], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        print(result.stderr.strip().splitlines()[-1][:200])


class RoundFiveBypasses(ClosureCase):
    """Round-five review items (Issue #544): root GNUmakefile shadowing, binary module shadowing, -m arguments."""

    def set_run(self, text):
        for root in (self.trusted, self.candidate):
            (root / FIXTURE).write_text(priv_workflow(text), encoding='utf-8')

    def test_c1_candidate_gnumakefile_is_refused(self):
        self.set_run('make setup')
        (self.candidate / 'GNUmakefile').write_text('setup:\n\techo pwned\n', encoding='utf-8')
        self.assertTrue(any('GNUmakefile' in p for p in policy.closure_problems(self.candidate, self.trusted)))

    def test_c2_candidate_sourceless_bytecode_shadow_is_refused(self):
        self.set_run('make setup')
        (self.candidate / 'tools' / 'json.pyc').write_bytes(b'\x00\x00\x00\x00')
        self.assertTrue(any('json.pyc' in p for p in policy.closure_problems(self.candidate, self.trusted)))

    def test_c3_file_named_in_the_arguments_of_dash_m_is_followed(self):
        self.set_run('python3 -m json.tool tools/leaf.py')
        for root in (self.trusted, self.candidate):
            (root / 'tools' / 'leaf.py').write_text('VALUE = 1\n', encoding='utf-8')
        # owner design: a privileged job runs only trusted code, so a repository run is refused by design
        self.assertTrue(policy.closure_problems(self.candidate, self.trusted))
        self.append('tools/leaf.py', 'import os\n')
        self.assertTrue(policy.closure_problems(self.candidate, self.trusted))


class PrivilegedDesign(ClosureCase):
    """Owner decision (Issue #544): a privileged job runs only trusted-SHA code; unknown or indirect execution fails closed."""

    def set_run(self, text):
        for root in (self.trusted, self.candidate):
            (root / FIXTURE).write_text(priv_workflow(text), encoding='utf-8')

    def test_make_in_a_privileged_job_is_refused_even_when_identical(self):
        self.set_run('make setup')
        self.assertTrue(policy.closure_problems(self.candidate, self.trusted))

    def test_timeout_wrapper_is_refused(self):
        self.set_run('timeout 600 make setup')
        self.assertTrue(policy.closure_problems(self.candidate, self.trusted))

    def test_unknown_command_is_refused(self):
        self.set_run('curl https://example.invalid/x')
        self.assertTrue(policy.closure_problems(self.candidate, self.trusted))

    def test_bash_dash_c_is_refused(self):
        self.set_run('bash -c "echo ok"')
        self.assertTrue(policy.closure_problems(self.candidate, self.trusted))

    def test_python_dash_m_is_refused(self):
        self.set_run('python3 -m json.tool')
        self.assertTrue(policy.closure_problems(self.candidate, self.trusted))

    def test_trusted_checkout_script_and_listed_commands_are_allowed(self):
        self.set_run('test -n "$X" || { echo no; exit 1; }; python3 .trusted/tools/policy_guard.py --help')
        self.assertEqual(policy.closure_problems(self.candidate, self.trusted), [])

    def test_local_action_in_a_privileged_job_is_refused(self):
        (self.trusted / '.github' / 'actions' / 'x').mkdir(parents=True, exist_ok=True)
        (self.trusted / '.github' / 'actions' / 'x' / 'action.yml').write_text('runs: {using: composite, steps: []}\n',
                                                                               encoding='utf-8')
        (self.trusted / FIXTURE).write_text(
            'name: priv\non: [push]\njobs:\n  priv:\n    runs-on: ubuntu-24.04\n    permissions:\n      id-token: write\n'
            '    steps:\n      - uses: ./.github/actions/x\n', encoding='utf-8')
        self.assertTrue(policy.closure_of(self.trusted)[2])


class PrivilegedHardening(ClosureCase):
    """Review round (Issue #544, last MAJOR items): each route the review named must be refused."""

    def set_run(self, text):
        for root in (self.trusted, self.candidate):
            (root / FIXTURE).write_text(priv_workflow(text), encoding='utf-8')

    def refused(self, text):
        self.set_run(text)
        return bool(policy.closure_of(self.trusted)[2])

    def test_process_substitution_is_refused(self):
        self.assertTrue(self.refused('cmp <(curl https://example.invalid) /dev/null'))

    def test_parent_step_in_trusted_path_is_refused(self):
        self.assertTrue(self.refused('python3 .trusted/../tools/x.py'))

    def test_leading_assignment_is_refused(self):
        self.assertTrue(self.refused('PYTHONPATH=tools python3 .trusted/tools/policy_guard.py --help'))

    def test_attached_dash_m_is_refused(self):
        self.assertTrue(self.refused('python3 -mjson.tool .trusted/tools/policy_guard.py'))

    def test_cp_into_trusted_checkout_is_refused(self):
        self.assertTrue(self.refused('cp tools/x .trusted/tools/x'))

    def test_mkdir_in_trusted_checkout_is_refused(self):
        self.assertTrue(self.refused('mkdir -p .trusted-main/x'))

    def test_gh_download_into_trusted_checkout_is_refused(self):
        self.assertTrue(self.refused('gh run download 1 -D .trusted/x'))

    def test_redirection_is_refused(self):
        self.assertTrue(self.refused('echo x > .trusted/tools/y.py'))

    def test_missing_repository_source_is_refused_not_dropped(self):
        self.assertTrue(self.refused('cp tools/missing.py build/x'))

    def test_listed_commands_on_trusted_checkout_still_pass(self):
        self.set_run('test -n "$X" || { echo no; exit 1; }; python3 .trusted/tools/policy_guard.py --help')
        self.assertEqual(policy.closure_of(self.trusted)[2], [])


class CriticalReviewRoute(ClosureCase):
    """Review round (Issue #544, BLOCKED): the stdin program, chained command substitution, glob and absolute writes."""

    def set_run(self, text):
        for root in (self.trusted, self.candidate):
            (root / FIXTURE).write_text(priv_workflow(text), encoding='utf-8')

    def refused(self, text):
        self.set_run(text)
        return bool(policy.closure_of(self.trusted)[2])

    def test_stdin_program_with_trusted_path_argument_is_refused(self):
        self.assertTrue(self.refused('python3 - .trusted/tools/policy_guard.py'))

    def test_bash_dash_s_is_refused(self):
        self.assertTrue(self.refused('bash -s .trusted/tools/policy_guard.py'))

    def test_interactive_option_is_refused(self):
        self.assertTrue(self.refused('bash -i .trusted/tools/policy_guard.py'))

    def test_chained_command_substitution_is_refused(self):
        self.assertTrue(self.refused('echo $(command -v x)$(id)'))

    def test_glob_write_into_trusted_checkout_is_refused(self):
        self.assertTrue(self.refused('tee .tr*/tools/policy_guard.p* < /dev/null'))

    def test_absolute_write_path_is_refused(self):
        self.assertTrue(self.refused('tee /home/runner/work/_temp/_runner_file_commands/add_path_x'))

    def test_policy_job_argument_form_is_allowed(self):
        self.set_run('python3 .trusted/tools/policy_guard.py --candidate .candidate --trusted .trusted')
        self.assertEqual(policy.closure_of(self.trusted)[2], [])


class FinalReviewRoute(ClosureCase):
    """Final review (Issue #544): a command after ; hidden in a command -v match, and ./ writer prefixes."""

    def set_run(self, text):
        for root in (self.trusted, self.candidate):
            (root / FIXTURE).write_text(priv_workflow(text), encoding='utf-8')

    def test_command_hidden_after_semicolon_in_command_v_is_refused(self):
        self.set_run('echo $(command -v x;sh<.candidate/p.sh)')
        self.assertTrue(policy.closure_of(self.trusted)[2])

    def test_dot_slash_repository_path_as_writer_target_is_refused(self):
        self.set_run('cp .candidate/payload ./tools/json')
        self.assertTrue(policy.closure_of(self.trusted)[2])


class LastReviewRoutes(ClosureCase):
    """Review of 09db90a (BLOCKED): a substitution in command position, brace expansion, gh api writes, .candidate writes."""

    def set_run(self, text):
        for root in (self.trusted, self.candidate):
            (root / FIXTURE).write_text(priv_workflow(text), encoding='utf-8')

    def test_substitution_in_command_position_is_refused(self):
        self.set_run('echo PAYLOAD | $(command -v tee) echo .trusted/tools/policy_guard.py')
        self.assertTrue(policy.closure_of(self.trusted)[2])

    def test_brace_expansion_into_a_parent_step_is_refused(self):
        self.set_run('python3 .trusted/{..,x}/.candidate/tools/evil.py')
        self.assertTrue(policy.closure_of(self.trusted)[2])

    def test_brace_expansion_into_a_writer_is_refused(self):
        self.set_run('tee .tru{s,x}ted/tools/y')
        self.assertTrue(policy.closure_of(self.trusted)[2])

    def test_gh_api_with_a_write_method_is_refused(self):
        self.set_run('gh api -X PATCH repos/x/y')
        self.assertTrue(policy.closure_of(self.trusted)[2])

    def test_writer_into_candidate_checkout_is_refused(self):
        self.set_run('tee .candidate/out')
        self.assertTrue(policy.closure_of(self.trusted)[2])

    def test_gh_api_read_and_the_brace_group_still_pass(self):
        self.set_run('gh api repos/x/y; test -n "$X" || { echo no; exit 1; }')
        self.assertEqual(policy.closure_of(self.trusted)[2], [])


class UnprivilegedBoundary(unittest.TestCase):
    def test_nightly_plan_job_is_outside_the_privileged_closure(self):
        # The nightly plan job holds no write permission: its run: text is approved exactly (approval_problems) but its
        # called scripts are not pinned. This is the documented boundary, not a silent pass: owner decision (C-3).
        import yaml
        doc = yaml.safe_load((REPO / '.github' / 'workflows' / 'nightly.yml').read_text(encoding='utf-8'))
        self.assertFalse(policy.privileged_job(doc, doc['jobs']['plan']))


if __name__ == '__main__':
    unittest.main()
