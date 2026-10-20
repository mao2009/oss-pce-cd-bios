"""Issue #337 round 11: the attest download steps are pinned (artifact name, extract path, with: keys), the subject-path must
lie under the pinned download, and YAML aliases and anchors are refused in every workflow file.

Each refusal case copies the real .github tree to a temporary root, applies one mutation to one workflow, and runs the
same entry point the workflows run (check_guard_placement). The unmutated tree must pass.
"""
from pathlib import Path
import contextlib
import io
import re
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import artifact_guard as guard

API = 'api-source.yml'
CI = 'ci.yml'
NAME = 'bios-dev-not-compatible-${{ github.sha }}'
VERDICT_NAME = 'cdread-verdict-${{ github.sha }}'
CDREAD_WITH = '          name: ' + NAME + '\n          path: evidence\n'
VERDICT_WITH = '          name: ' + VERDICT_NAME + '\n          path: verdict\n'
# Issue #337 round 11 (review): the guard jobs' intermediate downloads, and the real subject-path blocks.
GUARD_BIOS_WITH = '          name: intermediate-bios-dev-${{ github.sha }}\n          path: .intake\n'
GUARD_VERDICT_WITH = '          name: intermediate-cdread-verdict-${{ github.sha }}\n          path: .intake\n'
CDREAD_SUBJECTS = ('          subject-path: |\n'
                   '            evidence/cdread-probe/cdread-evidence.json\n'
                   '            evidence/cdread-probe/cdread-probe-not-bios.pce\n')
VERDICT_SUBJECT = '          subject-path: verdict/cdread-verdict.json\n'


def remove_job(text, job):
    """The workflow text with one block job (two-space key) removed, up to the next block job."""
    lines = text.splitlines(keepends=True)
    start = lines.index(f'  {job}:\n')
    end = next(i for i in range(start + 1, len(lines)) if re.match(r'  [^ #]', lines[i]))
    return ''.join(lines[:start] + lines[end:])


def real(rel):
    return (guard.ROOT / '.github' / 'workflows' / rel).read_text(encoding='utf-8')


def placement(mutated=None):
    """None when check_guard_placement passes on a copy of the real tree with mutated = {file: text}; else its message."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        shutil.copytree(guard.ROOT / '.github', root / '.github')
        for rel, text in (mutated or {}).items():
            (root / '.github' / 'workflows' / rel).write_text(text, encoding='utf-8')
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                guard.check_guard_placement(root)
        except ValueError as error:
            return str(error)
    return None


class PinnedAttestTests(unittest.TestCase):
    def setUp(self):
        self.api = real(API)
        self.ci = real(CI)
        self.assertEqual(1, self.api.count(CDREAD_WITH), 'the real attest-cdread download step must be unique')
        self.assertEqual(1, self.api.count(VERDICT_WITH), 'the real attest-cdread-verdict download step must be unique')

    def assertRefused(self, text, needle, rel=API):
        message = placement({rel: text})
        self.assertIsNotNone(message, f'expected a refusal mentioning {needle!r}, but the guard passed')
        self.assertIn(needle, message)

    def test_real_workflows_pass(self):
        self.assertIsNone(placement())

    def test_changed_artifact_name_is_refused(self):
        self.assertRefused(self.api.replace(CDREAD_WITH, CDREAD_WITH.replace(NAME, 'bios-dev-other-${{ github.sha }}')),
                           "'attest-cdread'")

    def test_changed_extract_path_is_refused(self):
        self.assertRefused(self.api.replace(CDREAD_WITH, CDREAD_WITH.replace('path: evidence', 'path: elsewhere')),
                           "'attest-cdread'")

    def test_changed_verdict_extract_path_is_refused(self):
        self.assertRefused(self.api.replace(VERDICT_WITH, VERDICT_WITH.replace('path: verdict', 'path: elsewhere')),
                           "'attest-cdread-verdict'")

    def test_subject_path_outside_download_is_refused(self):
        # The subject stays verdict/cdread-verdict.json; the download now extracts elsewhere, so the subject is not a file
        # of the pinned download.
        self.assertRefused(self.api.replace(VERDICT_WITH, VERDICT_WITH.replace('path: verdict', 'path: evidence')),
                           "'attest-cdread-verdict'")

    def test_run_id_is_refused(self):
        self.assertRefused(self.api.replace(CDREAD_WITH, CDREAD_WITH + '          run-id: ${{ github.run_id }}\n'),
                           "'run-id'")

    def test_repository_is_refused(self):
        self.assertRefused(self.api.replace(CDREAD_WITH, CDREAD_WITH + '          repository: other/repo\n'),
                           "'repository'")

    def test_github_token_is_refused(self):
        self.assertRefused(self.api.replace(CDREAD_WITH, CDREAD_WITH + '          github-token: ${{ github.token }}\n'),
                           "'github-token'")

    def test_merge_multiple_is_refused(self):
        self.assertRefused(self.api.replace(VERDICT_WITH, VERDICT_WITH + '          merge-multiple: true\n'),
                           "'merge-multiple'")

    def test_unknown_with_key_is_refused(self):
        self.assertRefused(self.api.replace(CDREAD_WITH, CDREAD_WITH + '          pattern: "*"\n'), "'pattern'")

    def test_duplicate_key_in_download_is_refused(self):
        # Already refused today, as a side effect of the strict loader in execution_scope_violations. Kept as a pin.
        self.assertRefused(self.api.replace(CDREAD_WITH, CDREAD_WITH + '          path: other\n'), 'duplicate')

    def test_yaml_alias_is_refused(self):
        self.assertRefused('x-anchor: &anc 1\nx-alias: *anc\n' + self.api, 'alias')

    def test_yaml_anchor_is_refused_in_other_workflow(self):
        self.assertRefused('x-anchor: &anc 1\n' + self.ci, 'anchor', rel=CI)

    def test_yaml_alias_is_refused_in_other_workflow(self):
        self.assertRefused('x-anchor: &anc 1\nx-alias: *anc\n' + self.ci, 'alias', rel=CI)


class RealFileTests(unittest.TestCase):
    """Round 11 review: the real api-source.yml must keep every pinned job, and its download steps are pinned too."""

    def setUp(self):
        self.api = real(API)
        for block in (GUARD_BIOS_WITH, GUARD_VERDICT_WITH, CDREAD_SUBJECTS, VERDICT_SUBJECT):
            self.assertEqual(1, self.api.count(block), f'the real block must be unique: {block!r}')

    def assertRefused(self, text, needle, rel=API):
        message = placement({rel: text})
        self.assertIsNotNone(message, f'expected a refusal mentioning {needle!r}, but the guard passed')
        self.assertIn(needle, message)

    def test_deleting_attest_cdread_job_is_refused(self):
        self.assertRefused(remove_job(self.api, 'attest-cdread'), "job 'attest-cdread' is missing")

    def test_deleting_attest_cdread_verdict_job_is_refused(self):
        self.assertRefused(remove_job(self.api, 'attest-cdread-verdict'), "job 'attest-cdread-verdict' is missing")

    def test_deleting_guard_job_is_refused(self):
        self.assertRefused(remove_job(self.api, 'guard-bios-dev'), "job 'guard-bios-dev' is missing")

    def test_guard_bios_dev_download_changed_name_is_refused(self):
        self.assertRefused(self.api.replace(GUARD_BIOS_WITH, GUARD_BIOS_WITH.replace('intermediate-bios-dev',
                                                                                    'intermediate-other')),
                           "'guard-bios-dev'")

    def test_guard_bios_dev_download_changed_path_is_refused(self):
        self.assertRefused(self.api.replace(GUARD_BIOS_WITH, GUARD_BIOS_WITH.replace('path: .intake', 'path: elsewhere')),
                           "'guard-bios-dev'")

    def test_guard_bios_dev_download_run_id_is_refused(self):
        self.assertRefused(self.api.replace(GUARD_BIOS_WITH, GUARD_BIOS_WITH + '          run-id: ${{ github.run_id }}\n'),
                           "'run-id'")

    def test_guard_cdread_verdict_download_changed_name_is_refused(self):
        self.assertRefused(self.api.replace(GUARD_VERDICT_WITH, GUARD_VERDICT_WITH.replace('intermediate-cdread-verdict',
                                                                                          'intermediate-other')),
                           "'guard-cdread-verdict'")

    def test_guard_cdread_verdict_download_changed_path_is_refused(self):
        self.assertRefused(self.api.replace(GUARD_VERDICT_WITH,
                                            GUARD_VERDICT_WITH.replace('path: .intake', 'path: elsewhere')),
                           "'guard-cdread-verdict'")

    def test_guard_cdread_verdict_download_run_id_is_refused(self):
        self.assertRefused(self.api.replace(GUARD_VERDICT_WITH, GUARD_VERDICT_WITH + '          run-id: ${{ github.run_id }}\n'),
                           "'run-id'")


class SubjectPathTests(unittest.TestCase):
    """Round 11 review: the subject-path check, called directly so no other layer can refuse first."""

    def setUp(self):
        self.api = real(API)
        self.assertEqual(1, self.api.count(CDREAD_SUBJECTS))
        self.assertEqual(1, self.api.count(VERDICT_SUBJECT))

    def test_real_subject_paths_pass(self):
        self.assertEqual([], guard.attest_download_violations(self.api, API))

    def test_subject_not_in_the_pinned_download_is_refused(self):
        text = self.api.replace(CDREAD_SUBJECTS, CDREAD_SUBJECTS.replace('cdread-probe-not-bios.pce', 'other.pce'))
        problems = guard.attest_download_violations(text, API)
        self.assertTrue(any("'attest-cdread'" in p and 'must sign exactly' in p for p in problems), problems)

    def test_subject_outside_the_download_directory_is_refused(self):
        text = self.api.replace(CDREAD_SUBJECTS, CDREAD_SUBJECTS.replace('evidence/cdread-probe/cdread-evidence.json',
                                                                         'elsewhere/cdread-probe/cdread-evidence.json'))
        problems = guard.attest_download_violations(text, API)
        self.assertTrue(any("'attest-cdread'" in p and 'must sign exactly' in p for p in problems), problems)

    def test_verdict_subject_not_in_the_pinned_download_is_refused(self):
        text = self.api.replace(VERDICT_SUBJECT, VERDICT_SUBJECT.replace('cdread-verdict.json', 'other.json'))
        problems = guard.attest_download_violations(text, API)
        self.assertTrue(any("'attest-cdread-verdict'" in p and 'must sign exactly' in p for p in problems), problems)


if __name__ == '__main__':
    unittest.main()
