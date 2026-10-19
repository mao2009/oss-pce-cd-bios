"""Repository-coupled artifact guard tests (Issue #227, PR #286).

They read this repository's workflows and check that the real workflows pass the guard. They run only where the
workflows carry the guard; the tool-level tests are in test_artifact_guard.py."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_artifact_guard import ROOT, UPLOAD, check_upload_artifacts, guard, upload_patterns  # noqa: E402


class WorkflowRepositoryTests(unittest.TestCase):
    def test_workflows_call_only_the_trusted_main_copy(self):
        for path in sorted((ROOT / guard.WORKFLOWS).glob('*.y*ml')):
            for line in path.read_text(encoding='utf-8').splitlines():
                if 'artifact_guard.py' in line and 'sparse-checkout' not in line:   # invocations, not the checkout config
                    self.assertIn('${{ runner.temp }}/trusted-main/tools/artifact_guard.py', line, path.name)

    def test_real_ci_workflow_with_a_build_step_moved_into_the_guard_job_is_refused(self):
        path = ROOT / '.github' / 'workflows' / 'ci.yml'
        text = path.read_text(encoding='utf-8')
        anchor = '      - name: Download intermediate test fixture artifact'
        self.assertEqual(1, text.count(anchor))
        mutated = text.replace(anchor, '      - name: Build in guard job\n        run: make build\n' + anchor)
        self.assertNotEqual([], guard.guard_placement_violations(mutated, '.github/workflows/ci.yml'))


class PathPolicyTests(unittest.TestCase):
    """D: no repository upload uses a wildcard root pattern; a future one must be a reviewed change."""

    def test_no_repository_upload_uses_a_wildcard_root_pattern(self):
        for path in sorted((ROOT / guard.WORKFLOWS).glob('*.y*ml')):
            for pattern in upload_patterns(path.read_text(encoding='utf-8')):
                source = guard.source_pattern(pattern)
                self.assertNotIn(source, ('*', '**', '*/*', '**/*'), path.name)
                self.assertFalse(source.startswith(('*', '**')), path.name)


class RepositoryTests(unittest.TestCase):
    def test_every_repository_upload_step_has_a_post_build_guard_before_it(self):
        # Owner decision: the guard runs after the build and before upload-artifact, in every upload workflow.
        for path in sorted((ROOT / guard.WORKFLOWS).glob('*.y*ml')):
            rel = path.relative_to(ROOT).as_posix()
            self.assertEqual([], guard.guard_placement_violations(path.read_text(encoding='utf-8'), rel))

    def test_repository_upload_patterns_are_visible_to_the_guard(self):
        patterns = []
        for path in sorted((ROOT / guard.WORKFLOWS).glob('*.y*ml')):
            patterns.extend(upload_patterns(path.read_text(encoding='utf-8')))
        patterns = [guard.source_pattern(p) for p in patterns]
        self.assertIn('build/debug/cdread-probe/*.pce', patterns)
        self.assertIn('build/debug/*.pce', patterns)
        self.assertIn('verdict/cdread-verdict.json', patterns)

    def test_repository_workflows_pass_without_local_build_outputs(self):
        check_upload_artifacts(ROOT)


if __name__ == '__main__':
    unittest.main()
