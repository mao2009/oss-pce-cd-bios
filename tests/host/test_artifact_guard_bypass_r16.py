"""Issue #337 round 16: file-copy and tee bypasses of _run_write_problem, and the documented limits of the run: check.

REFUSED: cp, mv, install, dd or tee with a destination (or any argument) assembled at run time, which can name
GITHUB_ENV or GITHUB_PATH. ALLOWED: the same commands with fixed targets or the allowed forms ($GITHUB_OUTPUT,
$GITHUB_STEP_SUMMARY, $RUNNER_TEMP/ without expansion).

NOT GUARANTEED (see the list in tools/artifact_guard.py, _run_write_problem): eval, bash -c, $(...) that builds a command,
source of a file, in-repo scripts and make targets, variables set by read or printf -v, and LD_PRELOAD or PYTHONPATH style
exports. The test NOT_GUARANTEED below records that these pass the pattern check; it is not a safety claim.
"""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import artifact_guard as guard

REFUSED = [
    'cp /tmp/evil $DEST',
    'cp /tmp/evil "${DEST}"',
    'mv /tmp/evil "$DEST"',
    'install -m 644 /tmp/evil $DEST',
    'dd if=/tmp/evil of=$DEST',
    'dd if=/tmp/evil of="$RUNNER_TEMP"/$NAME',
    'echo X=1 | tee --append $F',
    'echo X=1 | tee -- $F',
    'echo X=1 | tee --output-error=warn $F',
    'cp a b; cp /tmp/evil $DEST',
    'sudo cp /tmp/evil $DEST',
    'echo before\ncp /tmp/evil $DEST',
    'echo before\n  cp /tmp/evil $DEST',
    'echo before\nmv /tmp/evil "$DEST"',
    'echo before\ninstall -m 644 /tmp/evil $DEST',
    'echo before\ndd if=/tmp/evil of=$DEST',
    'echo before\n\ncp /tmp/evil "${DEST}"',
    'sudo -E cp /tmp/evil $DEST',
    'echo x | sudo -n tee --append $F',
    'env FOO=1 cp /tmp/evil $DEST',
    'time cp /tmp/evil $DEST',
    'echo before\ncp -t $DEST /tmp/evil',
    'cp --target-directory=$DEST /tmp/evil',
    'cp /tmp/evil $DEST -v',
    'cp /tmp/evil $DEST --preserve=mode',
    'install -m 644 /tmp/evil $DEST -v',
    'install -Dm644 /tmp/evil $DEST -v',
    'cp -rt $DEST /tmp/evil',
    'cp -at $DEST /tmp/evil',
    'install -Dt $DEST /tmp/evil',
    'cp --target-dir=$DEST /tmp/evil',
    'cp --targ $DEST /tmp/evil',
    'cp -t build/* /tmp/evil',
    'if cp /tmp/evil $DEST; then :; fi',
    'if ! cp /tmp/evil $DEST; then :; fi',
    'true && { cp /tmp/evil $DEST; }',
    '/bin/cp /tmp/evil $DEST',
    'nice -n 5 cp /tmp/evil $DEST',
    'sudo -u root cp /tmp/evil $DEST',
    'cp /tmp/evil build/*',
    'echo X=1 >&$F',
    'FOO=1 cp /tmp/evil $DEST',
    'case $X in a) cp /tmp/evil $DEST ;; esac',
    'N=cmd1\necho x >> "$RUNNER_TEMP/_runner_\\\nfile_commands/$N"',
    'echo a\\\\\ncp /tmp/evil $DEST',
]

ALLOWED = [
    'cp build/out.bin "$RUNNER_TEMP/out.bin"',
    'install -m 644 build/out.bin "$RUNNER_TEMP/out/out.bin"',
    'echo x | tee -a "$GITHUB_STEP_SUMMARY"',
    'echo "x=1" | tee -a "$GITHUB_OUTPUT"',
    'dd if=build/out.bin of=build/copy.bin bs=4096',
    'python3 -m pip install -r requirements.txt',
    'mkdir -p intermediate\ncp --parents build/debug/*.pce build/debug/*.json intermediate/',
    'cp --parents build/debug/*.pce build/debug/*.json intermediate/',
    'mv build/*.log logs/',
    'make test 2>&1 | tee -a "$GITHUB_STEP_SUMMARY"',
    'FOO=1 make test',
]

NOT_GUARANTEED = [
    'eval "$CMD"',
    'bash -c "$CMD"',
    'source ./env.sh',
    'printf -v V %s x; echo x >> "$GITHUB_OUTPUT"',
    'read -r V < input.txt',
    'export LD_PRELOAD=/tmp/evil.so',
    'export PYTHONPATH=/tmp/evil',
]


class FileCopyRefused(unittest.TestCase):
    def test_each_bypass_is_refused(self):
        for run in REFUSED:
            with self.subTest(run=run):
                self.assertIsNotNone(guard._run_write_problem(run), f'must be refused: {run}')


class FileCopyAllowed(unittest.TestCase):
    def test_fixed_and_allowed_forms_pass(self):
        for run in ALLOWED:
            with self.subTest(run=run):
                self.assertIsNone(guard._run_write_problem(run), f'must pass: {run}')


class PatternCostIsLinear(unittest.TestCase):
    def test_repeated_wrapper_options_do_not_backtrack(self):
        import time
        started = time.monotonic()
        self.assertIsNone(guard._run_write_problem('sudo ' + '-a ' * 300 + 'echo x'))
        self.assertIsNone(guard._run_write_problem('sudo ' + '-a X=1 ' * 60 + 'echo x'))
        self.assertLess(time.monotonic() - started, 2.0, 'FILE_WRITER_RE backtracks exponentially on wrapper options')


class NotGuaranteed(unittest.TestCase):
    def test_documented_limits_pass_the_pattern_check(self):
        for run in NOT_GUARANTEED:
            with self.subTest(run=run):
                self.assertIsNone(guard._run_write_problem(run), f'documented limit changed: {run}')


if __name__ == '__main__':
    unittest.main()
