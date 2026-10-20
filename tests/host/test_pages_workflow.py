"""Privilege split of .github/workflows/pages.yml (validate, build, deploy).

Stdlib only: the repository has no YAML dependency, so this parses the block subset that pages.yml uses (mappings,
block sequences, scalars and `|` block scalars). validate and build hold contents: read only; only deploy holds
pages: write and id-token: write, and it runs nothing but actions/deploy-pages."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PAGES = ROOT / '.github' / 'workflows' / 'pages.yml'
DEPLOY_IF = "github.event_name != 'pull_request' && github.ref == 'refs/heads/main'"


def _scalar(value):
    return value[1:-1] if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"" else value


def _map(lines, i, indent):
    out = {}
    while i < len(lines) and lines[i][0] == indent and not lines[i][1].startswith('- '):
        key, _, rest = lines[i][1].partition(':')
        rest = rest.strip()
        i += 1
        if rest == '|':   # block scalar: the following deeper lines are text, not keys
            body = []
            while i < len(lines) and lines[i][0] > indent:
                body.append(lines[i][1])
                i += 1
            out[key.strip()] = '\n'.join(body)
        elif rest:
            out[key.strip()] = _scalar(rest)
        elif i < len(lines) and lines[i][0] > indent:
            out[key.strip()], i = _block(lines, i, lines[i][0])
        else:
            out[key.strip()] = None
    return out, i


def _seq(lines, i, indent):
    out = []
    while i < len(lines) and lines[i][0] == indent and lines[i][1].startswith('- '):
        rest = lines[i][1][2:]
        if ':' in rest:
            lines[i] = (indent + 2, rest)
            item, i = _map(lines, i, indent + 2)
            out.append(item)
        else:
            out.append(_scalar(rest))
            i += 1
    return out, i


def _block(lines, i, indent):
    if lines[i][1].startswith('- '):
        return _seq(lines, i, indent)
    return _map(lines, i, indent)


def load(text):
    lines = [(len(raw) - len(raw.lstrip(' ')), raw.strip()) for raw in text.splitlines()
             if raw.strip() and not raw.lstrip().startswith('#')]
    doc, end = _map(lines, 0, 0)
    if end != len(lines):
        raise ValueError(f'unparsed workflow content at line {end}: {lines[end][1]!r}')
    return doc


def steps_of(job):
    return job.get('steps') or []


def uses_of(step):
    return step.get('uses', '')


def all_keys(node):
    """Every mapping key anywhere under node (for finding run:/env:/checkout anywhere in a job)."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from all_keys(value)
    elif isinstance(node, list):
        for item in node:
            yield from all_keys(item)


class PagesWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = load(PAGES.read_text(encoding='utf-8'))
        cls.jobs = cls.doc.get('jobs') or {}

    def test_three_jobs_validate_build_deploy(self):
        self.assertEqual({'validate', 'build', 'deploy'}, set(self.jobs))

    def test_build_needs_validate_and_deploy_needs_build(self):
        self.assertEqual('validate', self.jobs['build'].get('needs'))
        self.assertEqual('build', self.jobs['deploy'].get('needs'))

    def test_deploy_runs_only_on_push_to_main_with_the_approved_condition(self):
        self.assertEqual(DEPLOY_IF, self.jobs['deploy'].get('if'))

    def test_deploy_permissions_are_exactly_pages_write_and_id_token_write(self):
        self.assertEqual({'pages': 'write', 'id-token': 'write'}, self.jobs['deploy'].get('permissions'))

    def test_deploy_has_no_checkout_run_or_env(self):
        deploy = self.jobs['deploy']
        keys = set(all_keys(deploy))
        self.assertFalse({'run', 'env'} & keys, sorted({'run', 'env'} & keys))
        for step in steps_of(deploy):
            self.assertNotIn('checkout', uses_of(step))
            self.assertNotIn('run', step)
            self.assertNotIn('env', step)

    def test_deploy_runs_only_actions_deploy_pages(self):
        steps = steps_of(self.jobs['deploy'])
        self.assertEqual(1, len(steps))
        self.assertTrue(uses_of(steps[0]).startswith('actions/deploy-pages@'), uses_of(steps[0]))

    def test_build_has_no_id_token_and_only_read_permissions(self):
        perms = self.jobs['build'].get('permissions') or {}
        self.assertNotIn('id-token', perms)
        self.assertEqual({'contents': 'read'}, perms)

    def test_validate_only_reads_contents(self):
        self.assertEqual({'contents': 'read'}, self.jobs['validate'].get('permissions'))

    def test_build_uploads_the_pages_artifact(self):
        uses = [uses_of(s) for s in steps_of(self.jobs['build'])]
        self.assertTrue(any(u.startswith('actions/upload-pages-artifact@') for u in uses), uses)

    def test_only_deploy_holds_id_token(self):
        for name in ('validate', 'build'):
            self.assertNotIn('id-token', self.jobs[name].get('permissions') or {}, name)

    def test_every_uses_line_is_pinned_to_a_full_commit_sha(self):
        refs = re.findall(r'^\s*(?:-\s+)?uses:\s*(\S+)', PAGES.read_text(encoding='utf-8'), re.M)
        self.assertTrue(refs)
        for ref in refs:
            self.assertRegex(ref, r'^[\w.-]+/[\w.-]+@[0-9a-f]{40}$', ref)

    def test_pages_actions_are_pinned_to_resolved_shas_with_tag_comments(self):
        expected = {
            'actions/configure-pages': ('983d7736d9b0ae728b81ab479565c72886d7745b', 'v5'),
            'actions/upload-pages-artifact': ('7b1f4a764d45c48632c6b24a0339c27f5614fb0b', 'v4'),
            'actions/deploy-pages': ('d6db90164ac5ed86f2b6aed7e0febac5b3c0c03e', 'v4'),
        }
        found = {}
        for job in self.jobs.values():
            for step in steps_of(job):
                value = uses_of(step)
                action, _, _ = value.partition('@')
                if action in expected:
                    ref, _, comment = value.partition('#')
                    found[action] = (ref.strip().partition('@')[2], comment.strip())
        self.assertEqual({k: v for k, (v, _) in expected.items()}, {k: v[0] for k, v in found.items()})
        self.assertEqual({k: tag for k, (_, tag) in expected.items()}, {k: v[1] for k, v in found.items()})


if __name__ == '__main__':
    unittest.main()
