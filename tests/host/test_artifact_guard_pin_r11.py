"""Issue #337 round 11: the attest download steps are pinned (artifact name, extract path, with: keys), the subject-path must
lie under the pinned download, and YAML aliases and anchors are refused in every workflow file.

Each refusal case copies the real .github tree to a temporary root, applies one mutation to one workflow, and runs the
same entry point the workflows run (check_guard_placement). The unmutated tree must pass.
"""
from pathlib import Path
import contextlib
import io
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


if __name__ == '__main__':
    unittest.main()
