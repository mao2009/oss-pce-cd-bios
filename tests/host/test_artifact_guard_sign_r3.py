"""Issue #337 round 3: signing-job `if:` regression tests (fail-closed key, tag, anchor and duplicate checks).

Each mutation is applied to the real api-source.yml in memory. The unmutated workflow must pass. On the round-2 guard
(a15b1ed) the tagged, anchored and duplicate-key cases below pass the check, which is the regression they pin.
"""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import artifact_guard as guard

REL = '.github/workflows/api-source.yml'
JOBS = ('attest-cdread', 'attest-cdread-verdict')
VALUE = "github.event_name == 'push' && github.ref == 'refs/heads/main'"
IF_LINE = '    if: ' + VALUE + '\n'


def real():
    return (guard.ROOT / REL).read_text(encoding='utf-8')


def mutate(job, new):
    """The real workflow with the approved `if:` line of `job` replaced by `new` (which must include a trailing newline)."""
    text = real()
    head = text.index(f'\n  {job}:\n')
    at = text.index(IF_LINE, head)
    return text[:at] + new + text[at + len(IF_LINE):]


def placement(text):
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, '.github', 'workflows').mkdir(parents=True)
        Path(tmp, REL).write_text(text, encoding='utf-8')
        guard.check_guard_placement(Path(tmp))


class SignFailClosedTests(unittest.TestCase):
    def assertRefused(self, text):
        self.assertNotEqual([], guard.attest_condition_violations(text, REL))
        with self.assertRaises(ValueError):
            placement(text)

    def test_unmutated_workflow_passes(self):
        self.assertEqual([], guard.attest_condition_violations(real(), REL))
        placement(real())

    def test_tagged_if_key_before_approved_line_is_refused(self):
        for job in JOBS:
            with self.subTest(job=job):
                self.assertRefused(mutate(job, "    !!str if: github.event_name == 'pull_request'\n" + IF_LINE))

    def test_tagged_if_key_after_approved_line_is_refused(self):
        for job in JOBS:
            with self.subTest(job=job):
                self.assertRefused(mutate(job, IF_LINE + "    !!str if: github.event_name == 'pull_request'\n"))

    # The next three keep the approved line and add a second `if` key with the SAME value after it: PyYAML's last-wins
    # value is then still approved, so the round-2 guard passed them (they fail there) and only the key-form check refuses.
    def test_anchor_on_if_key_is_refused(self):
        for job in JOBS:
            with self.subTest(job=job):
                self.assertRefused(mutate(job, IF_LINE + '    &k if: ' + VALUE + '\n'))
                self.assertRefused(mutate(job, '    &k if: ' + VALUE + '\n'))

    def test_tagged_quoted_if_key_is_refused(self):
        for job in JOBS:
            with self.subTest(job=job):
                self.assertRefused(mutate(job, IF_LINE + '    !!str "if": ' + VALUE + '\n'))
                self.assertRefused(mutate(job, '    !!str "if": ' + VALUE + '\n'))

    def test_duplicate_plain_if_key_is_refused(self):
        for job in JOBS:
            with self.subTest(job=job):
                self.assertRefused(mutate(job, IF_LINE + "    if: github.event_name == 'pull_request'\n"))

    def test_complex_key_if_is_refused(self):
        for job in JOBS:
            with self.subTest(job=job):
                self.assertRefused(mutate(job, IF_LINE + '    ? if\n    : ' + VALUE + '\n'))
                self.assertRefused(mutate(job, '    ? if\n    : ' + VALUE + '\n'))

    def test_duplicate_other_job_key_is_refused_by_strict_loader(self):
        for job in JOBS:
            with self.subTest(job=job):
                self.assertRefused(mutate(job, IF_LINE + '    runs-on: ubuntu-24.04\n'))

    def test_anchor_on_job_value_is_refused_by_event_walk(self):
        for job in JOBS:
            with self.subTest(job=job):
                text = mutate(job, IF_LINE)
                head = text.index(f'\n  {job}:\n')
                at = text.index('    needs: ', head) + len('    needs: ')
                text = text[:at] + '&n ' + text[at:]
                self.assertRefused(text)
