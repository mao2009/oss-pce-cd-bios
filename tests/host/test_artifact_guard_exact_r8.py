"""Issue #337 round 8: a run: in a job with id-token: write that is not an approved signing job must exactly match
APPROVED_RUN_TEXTS for (workflow, job, step name). No shell parsing decides acceptance.

Each negative case edits the real pages.yml in memory. The unmutated workflows and the real file must pass.
"""
from pathlib import Path
import sys
import unittest

try:
    import yaml
except ImportError as error:  # host-test dependency: these checks cannot run without it
    raise ImportError("PyYAML is required for this host test: pip install pyyaml") from error

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import artifact_guard as guard

PAGES = '.github/workflows/pages.yml'
PAGES_NAME = 'pages.yml'
ARGUMENT_CASE = '"${{ steps.pyresolve.outputs.python }}" -c "print(1)"'
# The approved run texts, written out so a failure is a behaviour failure and not a missing constant.
PY_RESOLVE_TEXT = 'echo "python=$(command -v python3)" >> "$GITHUB_OUTPUT"'
CHECK_TEXT = ('"${{ steps.pyresolve.outputs.python }}" ".trusted-main/tools/artifact_guard.py" '
              '--root "$GITHUB_WORKSPACE" --check-placement')


GUARD_NAME = 'Guard placement (trusted)'   # the real approved step whose run: carries the python expression
GUARD_ANCHOR = f'      - name: {GUARD_NAME}\n'
GUARD_RUN = '        run: |\n          ' + CHECK_TEXT + '\n'


def real(rel=PAGES):
    return (guard.ROOT / rel).read_text(encoding='utf-8')


LEGACY = guard.ROOT / 'tests' / 'fixtures' / 'pages-single-job.yml'   # the pre-#374 single-job layout


def legacy():
    return LEGACY.read_text(encoding='utf-8')


def with_guard_run(run_line):
    """The real pages.yml with the run: of the real Guard placement step replaced by run_line (same step name)."""
    text = legacy()
    assert text.count(GUARD_RUN) == 1
    return text.replace(GUARD_RUN, '        run: |\n          ' + run_line + '\n')


def with_guard_keys(extra):
    """The real pages.yml with extra key lines added to the real Guard placement step (its run: text unchanged)."""
    text = legacy()
    assert text.count(GUARD_ANCHOR) == 1
    return text.replace(GUARD_ANCHOR, GUARD_ANCHOR + extra)


def with_run(body, name='probe'):
    """The real pages.yml with one more step at the end of its deploy job; body is the step's run: text (block)."""
    return legacy() + f'      - name: {name}\n        run: |\n' + ''.join('          ' + line + '\n' for line in body.split('\n'))


def id_token_runs(text):
    """{(workflow, job, step name): run} for every run: step in a job with id-token: write that is not approved."""
    doc = yaml.load(text, Loader=guard._strict_loader())
    found = {}
    for job, body in doc['jobs'].items():
        perms = body.get('permissions', doc.get('permissions'))
        if not (isinstance(perms, dict) and perms.get('id-token') == 'write') or (PAGES_NAME, job) in guard.ATTEST_NEEDS:
            continue
        for step in body.get('steps') or []:
            if isinstance(step, dict) and 'run' in step:
                found[(PAGES_NAME, job, step.get('name'))] = step['run']
    return found


class ExactMatchTests(unittest.TestCase):
    def assertRefused(self, text):
        self.assertTrue(guard.signing_violations(text, PAGES), text[-300:])

    def test_real_pages_yml_passes(self):
        self.assertEqual([], guard.signing_violations(real(), PAGES))

    def test_unmutated_workflows_pass(self):
        for path in sorted((guard.ROOT / guard.WORKFLOWS).glob('*.y*ml')):
            rel = path.relative_to(guard.ROOT).as_posix()
            with self.subTest(workflow=rel):
                self.assertEqual([], guard.signing_violations(path.read_text(encoding='utf-8'), rel))

    def test_registry_matches_the_real_id_token_runs(self):
        # Drift guard: the registry holds exactly the run texts of the real pages.yml id-token jobs, nothing more or less.
        runs = id_token_runs(legacy())
        registry = {key: text for key, text in guard.APPROVED_RUN_TEXTS.items() if key[0] == PAGES_NAME}
        self.assertEqual(sorted(registry), sorted(runs))
        self.assertEqual(4, len(registry))
        for key, text in registry.items():
            with self.subTest(step=key):
                self.assertEqual(text, runs[key].removesuffix('\n'))

    def assertExactRefused(self, text):
        # Asserts the exact-match layer itself refuses (not only --check-placement or the round 6/7 layer).
        problems = guard.run_allowlist_violations(text, PAGES)
        self.assertTrue(any('exact match' in problem for problem in problems), problems)

    def test_critical_argument_after_expression_is_refused(self):
        # Same real step name as the approved one; only the run: text differs. The control below passes.
        self.assertRefused(with_guard_run(ARGUMENT_CASE))
        self.assertEqual([], guard.signing_violations(with_guard_run(CHECK_TEXT), PAGES))

    def test_critical_argument_refused_by_exact_match_layer(self):
        self.assertExactRefused(with_guard_run(ARGUMENT_CASE))
        self.assertEqual([], guard.run_allowlist_violations(with_guard_run(CHECK_TEXT), PAGES))

    def test_approved_run_with_extra_key_is_refused_by_exact_match(self):
        # Round 9: name, id and run only. Each extra key changes how the approved text is executed.
        for extra in ('        env:\n          PATH: /tmp/evil\n',
                      '        if: always()\n',
                      '        working-directory: /tmp\n',
                      '        shell: bash\n',
                      '        continue-on-error: true\n'):
            with self.subTest(extra=extra):
                self.assertExactRefused(with_guard_keys(extra))
        self.assertEqual([], guard.run_allowlist_violations(legacy(), PAGES))

    def test_one_character_change_to_an_approved_run_is_refused(self):
        self.assertEqual(1, legacy().count('--check-placement\n'))
        changed = legacy().replace('--check-placement\n', '--check-placemenx\n', 1)
        self.assertNotEqual(legacy(), changed)
        self.assertRefused(changed)

    def test_modified_copy_of_an_approved_run_is_refused(self):
        self.assertRefused(with_run(CHECK_TEXT.replace('--check-placement', '--check-placemenx')))

    def test_added_echo_step_is_refused(self):
        self.assertRefused(with_run('echo hi'))

    def test_approved_text_under_another_step_name_is_refused(self):
        self.assertRefused(with_run(PY_RESOLVE_TEXT))

    def test_approved_text_as_a_second_step_with_the_same_name_is_refused(self):
        self.assertRefused(with_run(PY_RESOLVE_TEXT, name='Resolve python3 (trusted)'))

    def test_identical_run_in_a_different_job_is_refused(self):
        other = ('  other:\n    runs-on: ubuntu-24.04\n    permissions:\n      id-token: write\n    steps:\n'
                 '      - name: Resolve python3 (trusted)\n'
                 '        run: echo "python=$(command -v python3)" >> "$GITHUB_OUTPUT"\n')
        problems = guard.signing_violations(legacy() + other, PAGES)
        self.assertTrue(any("'other'" in problem for problem in problems), problems)


if __name__ == '__main__':
    unittest.main()
