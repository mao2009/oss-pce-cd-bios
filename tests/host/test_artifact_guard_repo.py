"""Repository-coupled artifact guard tests (Issue #227, PR #286).

They read this repository's workflows and check that the real workflows pass the guard. They run only where the
workflows carry the guard; the tool-level tests are in test_artifact_guard.py."""
import re
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
                    self.assertIn('.trusted-main/tools/artifact_guard.py', line, path.name)

    def test_real_ci_workflow_with_a_build_step_moved_into_the_guard_job_is_refused(self):
        path = ROOT / '.github' / 'workflows' / 'ci.yml'
        text = path.read_text(encoding='utf-8')
        anchor = '      - name: Download intermediate test fixture artifact'
        self.assertEqual(1, text.count(anchor))
        mutated = text.replace(anchor, '      - name: Build in guard job\n        run: make build\n' + anchor)
        self.assertNotEqual([], guard.guard_placement_violations(mutated, '.github/workflows/ci.yml'))


class AttestationScopeTests(unittest.TestCase):
    """Owner decision (Issue #349): the only attested subjects are the two synthetic CD-read files. The workflow's
    subject-path and the documented publication scope must both say exactly that."""
    SCOPE = {'evidence/cdread-probe/cdread-evidence.json', 'evidence/cdread-probe/cdread-probe-not-bios.pce'}

    def attest_subjects(self, text):
        """Every path named by subject-path in the attest-cdread job: an inline value or the indented block below it."""
        job = text.split('\n  attest-cdread:\n', 1)[1].split('\n  verify-cdread-attestation:', 1)[0]
        lines = job.splitlines()
        paths = []
        for i, line in enumerate(lines):
            match = re.match(r'''^(\s*)(?:-\s+)?["']?subject-path["']?\s*:\s*(.*)$''', line)
            if not match:
                continue
            indent, inline = len(match.group(1)), match.group(2).split(' #')[0].strip()
            if inline and inline not in ('|', '|-', '|+', '>'):
                paths += inline.strip('"\'').split()
            for rest in lines[i + 1:]:
                if rest.strip() and len(rest) - len(rest.lstrip()) <= indent:
                    break
                if rest.strip() and not rest.lstrip().startswith('#'):
                    paths.append(rest.strip())
        return set(paths)

    def test_attest_cdread_subject_path_is_exactly_the_two_synthetic_files(self):
        text = (ROOT / '.github' / 'workflows' / 'api-source.yml').read_text(encoding='utf-8')
        self.assertEqual(self.SCOPE, self.attest_subjects(text))

    def test_inline_subject_path_is_read(self):
        # Review LOW (#349): `subject-path: evidence/x` on one line was not read, so an extra subject could hide.
        fixture = 'jobs:\n  attest-cdread:\n    steps:\n      - uses: x\n        with:\n          subject-path: evidence/x.json\n'
        self.assertEqual({'evidence/x.json'}, self.attest_subjects(fixture))

    def test_publication_scope_and_residual_risk_are_documented(self):
        doc = (ROOT / 'docs' / 'provenance.md').read_text(encoding='utf-8')
        self.assertIn('**Attestation publication scope**', doc)
        for path in self.SCOPE:
            self.assertIn(path, doc)


class PathPolicyTests(unittest.TestCase):
    """D: no repository upload uses a wildcard root pattern; a future one must be a reviewed change."""

    # Glob metacharacters, held as a constant so that the first path segment is tested rather than whole patterns.
    WILDCARD_CHARS = '*?'

    def test_no_repository_upload_uses_a_wildcard_root_pattern(self):
        for path in sorted((ROOT / guard.WORKFLOWS).glob('*.y*ml')):
            for pattern in upload_patterns(path.read_text(encoding='utf-8')):
                source = guard.source_pattern(pattern)
                first_segment = source.split('/')[0]
                self.assertFalse(any(char in first_segment for char in self.WILDCARD_CHARS), (path.name, source))


class RepositoryTests(unittest.TestCase):
    def test_pages_workflow_actions_are_pinned_to_a_full_commit_sha(self):
        # Issue #349: the Pages actions were pinned to movable tags, which a maintainer can re-point after review.
        text = (ROOT / '.github' / 'workflows' / 'pages.yml').read_text(encoding='utf-8')
        refs = dict(re.findall(r'uses:\s*actions/([\w-]+)@(\S+)', text))
        for action in ('configure-pages', 'upload-pages-artifact', 'deploy-pages', 'checkout'):
            self.assertIn(action, refs)
            self.assertRegex(refs[action], r'^[0-9a-f]{40}$', action)

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
