"""Issue #337 round 15: run: text that writes GITHUB_ENV, GITHUB_PATH, BASH_ENV, PATH or a runner file command, assembled
at run time (_run_write_problem).

Negative cases must be refused (a reason is returned). Positive controls are the run: patterns the repo uses today and
must pass. This is a pattern check. Dynamic code (eval, bash -c, a sourced or executed file) is not covered and is not
tested here; see the docstring of _run_write_problem.
"""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import artifact_guard as guard

REFUSED = [
    # literal runner files and PATH assignments (regression of round 14 behaviour)
    'echo /tmp/evil >> "$GITHUB_PATH"',
    'echo X=1 >> $GITHUB_ENV',
    'echo "PATH=/tmp/evil" >> "$GITHUB_ENV"',
    'export PATH+=:/tmp/evil',
    'PATH=/tmp/evil make',
    # split by quotes or backslashes
    'echo X=1 >> "GITHUB""_ENV"',
    'echo X=1 >> G\\ITHUB_ENV',
    # assembled at run time: the name is built from parts
    'A=GITHUB_; B=ENV; echo X=1 >> "${A}${B}"',
    'A=GITHUB_; echo X=1 >> "$A"ENV',
    'X=GITHUB_EN; echo X=1 >> "${X}V"',
    'S=ENV; echo X=1 >> "$(printf GITHUB_%s $S)"',
    'F="$RUNNER_TEMP/_runner_$S"; echo X=1 >> "$F"',
    'S=1; echo X=1 | tee -a "$F"',
    # indirect expansion and namerefs
    'V=GITHUB_ENV; echo X=1 >> "${!V}"',
    'declare -n r=PATH_VAR; echo x > "$r"',
    'typeset -n t=GITHUB_ENV; echo x >> "$t"',
    # runner-internal paths, direct or by glob
    'echo X=1 > "$RUNNER_TEMP/_runner_file_commands/set_env_1"',
    'echo X=1 >> "$RUNNER_TEMP"/_runner*/set_env*',
    'echo X=1 >> "$RUNNER_TEMP/_runner_file_commands/add_path_1"',
    'echo X=1 >> "$RUNNER_TEMP/x" $GITHUB_E*',
    # a target that is not in the allowed forms
    'echo x >> "$HOME/out"',
    'echo x > "$(mktemp)"',
    # a glob beside a variable is refused by design (no repo workflow uses one)
    'ls "$GITHUB_WORKSPACE"/build/*.bin',
]

ALLOWED = [
    'echo "python=$(command -v python3)" >> "$GITHUB_OUTPUT"',
    'echo "sha=$GITHUB_SHA" >> "$GITHUB_OUTPUT"',
    'echo "## Summary" >> "$GITHUB_STEP_SUMMARY"',
    'gh api "repos/$GITHUB_REPOSITORY/rules/branches/main" > "$RUNNER_TEMP/main-rules.json"',
    'python3 tests/integration/geargrafx_bcdbin.py 2>&1 | tee build/ci-bcdbin/bcdbin-core.log',
    'make dev-boot-internal-integration 2>&1 | tee build/debug/dev-boot-internal/run.log',
    'python3 tools/check.py >&2',
    'make > /dev/null',
    'python3 x.py --cases tests/fixtures/core-harness-plugin.json',
]


class RunWriteRefused(unittest.TestCase):
    def test_each_attack_is_refused(self):
        for run in REFUSED:
            with self.subTest(run=run):
                self.assertIsNotNone(guard._run_write_problem(run), f'must be refused: {run}')


class RunWriteAllowed(unittest.TestCase):
    def test_existing_patterns_pass(self):
        for run in ALLOWED:
            with self.subTest(run=run):
                self.assertIsNone(guard._run_write_problem(run), f'must pass: {run}')


if __name__ == '__main__':
    unittest.main()
