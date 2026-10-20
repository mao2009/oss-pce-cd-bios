"""Issue #337 round 6: run: allowlist for jobs that hold id-token: write without being an approved signing job.

Each case adds one step to the real pages.yml deploy job (the only such job today) in memory. The run: value is read after
YAML decoding, so escapes are already applied. On 2f9002a the negative cases pass the check (escaped stems, an assembled
command word, a $VAR command word, eval, and shell indirection), which is the regression they pin. The unmutated workflows
must pass, and the allowlisted echo step must pass.
"""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import artifact_guard as guard

PAGES = '.github/workflows/pages.yml'


def real(rel=PAGES):
    return (guard.ROOT / rel).read_text(encoding='utf-8')


def with_step(run_lines):
    """The real pages.yml with one more step at the end of its last job (deploy). run_lines is the step's `run:` text."""
    return real() + '      - name: probe\n' + run_lines


class RunAllowlistTests(unittest.TestCase):
    def assertRefused(self, run_lines):
        problems = guard.signing_violations(with_step(run_lines), PAGES)
        self.assertTrue(problems, run_lines)

    def test_unmutated_workflows_pass(self):
        for path in sorted((guard.ROOT / guard.WORKFLOWS).glob('*.y*ml')):
            rel = path.relative_to(guard.ROOT).as_posix()
            with self.subTest(workflow=rel):
                self.assertEqual([], guard.signing_violations(path.read_text(encoding='utf-8'), rel))

    def test_escaped_stem_in_double_quoted_run_is_refused(self):   # fails on 2f9002a
        self.assertRefused('        run: "\\x63osign sign-blob evidence/x.json"\n')
        self.assertRefused('        run: "\\u0063osign sign-blob evidence/x.json"\n')

    def test_assembled_command_word_is_refused(self):   # fails on 2f9002a
        self.assertRefused('        run: x=sign; co$x sign-blob evidence/x.json\n')

    def test_variable_command_word_is_refused(self):   # fails on 2f9002a
        self.assertRefused('        run: $CMD --version\n')

    def test_eval_is_refused(self):   # fails on 2f9002a
        self.assertRefused('        run: eval echo hi\n')

    def test_shell_indirection_is_refused(self):   # fails on 2f9002a
        for run in ('bash -c "echo hi"', 'sh -c "echo hi"'):
            with self.subTest(run=run):
                self.assertRefused(f'        run: {run}\n')

    def test_other_indirection_is_refused(self):   # fails on 2f9002a
        for run in ('env echo hi', 'exec echo hi', 'xargs echo hi', 'source x.sh'):
            with self.subTest(run=run):
                self.assertRefused(f'        run: {run}\n')

    def test_command_outside_the_allowlist_is_refused(self):
        for run in ('printf hi', 'command sign-blob evidence/x.json', '\\cosign x', '"echo" hi'):
            with self.subTest(run=run):
                self.assertRefused(f'        run: {run}\n')

    def test_allowlisted_command_passes(self):
        self.assertEqual([], guard.signing_violations(with_step('        run: echo hello\n'), PAGES))
        block = ('        run: |\n'
                 '          echo "python=$(command -v python3)" >> "$GITHUB_OUTPUT"\n')
        self.assertEqual([], guard.signing_violations(with_step(block), PAGES))


if __name__ == '__main__':
    unittest.main()
