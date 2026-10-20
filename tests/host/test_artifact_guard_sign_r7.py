"""Issue #337 round 7: the run: allowlist reads every simple command, not only the first word of a run: value.

Shell separators are a newline, ;, |, |&, &&, parentheses and braces. A GitHub expression is allowed only as the first word
of a command. Each case adds one step to the real pages.yml deploy job in memory. Cases marked 'allowlist' use no
signing term, so only the allowlist can refuse them; on ede72c06 they pass (the first word is echo), which is the regression.
The unmutated workflows and the real pages.yml run steps must still pass.
"""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import artifact_guard as guard

PAGES = '.github/workflows/pages.yml'


def real(rel=PAGES):
    return (guard.ROOT / rel).read_text(encoding='utf-8')


def with_run(body):
    """The real pages.yml with one more step at the end of its last job (deploy); body is the step's block run: text."""
    return real() + '      - name: probe\n        run: |\n' + ''.join('          ' + line + '\n' for line in body.split('\n'))


class SeparatorTests(unittest.TestCase):
    def assertRefused(self, body):
        self.assertTrue(guard.signing_violations(with_run(body), PAGES), body)

    def assertAccepted(self, body):
        self.assertEqual([], guard.signing_violations(with_run(body), PAGES), body)

    def test_unmutated_workflows_pass(self):
        for path in sorted((guard.ROOT / guard.WORKFLOWS).glob('*.y*ml')):
            rel = path.relative_to(guard.ROOT).as_posix()
            with self.subTest(workflow=rel):
                self.assertEqual([], guard.signing_violations(path.read_text(encoding='utf-8'), rel))

    def test_real_pages_run_steps_pass(self):   # the three real run: steps of the pages.yml deploy job
        self.assertAccepted('echo "python=$(command -v python3)" >> "$GITHUB_OUTPUT"')
        self.assertAccepted('"${{ steps.pyresolve.outputs.python }}" ".trusted-main/tools/artifact_guard.py" --root "$GITHUB_WORKSPACE" --check-placement')

    def test_newline_split_stem_is_refused(self):   # fails on ede72c06 (the stem is split by a newline, not by a command)
        self.assertRefused('echo a\nx=sign\nco$x sign-blob evidence/x.json')

    def test_newline_then_signing_command_is_refused(self):   # fails on ede72c06 (the signing term is found after decoding)
        self.assertRefused('echo a\ncosign sign-blob evidence/x.json')

    def test_newline_then_allowlist_miss_is_refused(self):   # allowlist; fails on ede72c06
        self.assertRefused('echo a\nprintf hi')

    def test_semicolon_then_command_is_refused(self):   # allowlist; fails on ede72c06
        self.assertRefused('echo a; printf hi')

    def test_pipe_into_command_is_refused(self):   # allowlist; fails on ede72c06
        self.assertRefused('echo a | printf hi')

    def test_pipe_ampersand_into_command_is_refused(self):   # allowlist; fails on ede72c06
        self.assertRefused('echo a |& printf hi')

    def test_and_or_then_command_is_refused(self):   # allowlist; fails on ede72c06
        self.assertRefused('echo a && printf hi')
        self.assertRefused('echo a || printf hi')

    def test_grouping_then_command_is_refused(self):   # allowlist; fails on ede72c06
        self.assertRefused('(printf hi)')
        self.assertRefused('{ printf hi; }')

    def test_semicolon_then_cosign_is_refused(self):   # fails on ede72c06 (signing term after the separator)
        self.assertRefused('echo a; cosign sign-blob evidence/x.json')

    def test_pipe_into_cosign_is_refused(self):   # fails on ede72c06 (signing term after the pipe)
        self.assertRefused('echo a | cosign sign-blob evidence/x.json')

    def test_github_expression_in_echo_argument_is_refused(self):   # fails on ede72c06 (allowed anywhere before)
        self.assertRefused('echo "${{ steps.pyresolve.outputs.python }}"')

    def test_ansi_c_quote_split_stem_is_refused(self):   # fails on ede72c06 (only the first word was read)
        self.assertRefused("echo a\nc$'osign' sign-blob evidence/x.json")

    def test_redirections_are_not_separators(self):
        self.assertAccepted('echo a 2>&1 >/dev/null')


if __name__ == '__main__':
    unittest.main()
