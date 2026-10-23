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
]

ALLOWED = [
    'cp build/out.bin "$RUNNER_TEMP/out.bin"',
    'install -m 644 build/out.bin "$RUNNER_TEMP/out/out.bin"',
    'echo x | tee -a "$GITHUB_STEP_SUMMARY"',
    'echo "x=1" | tee -a "$GITHUB_OUTPUT"',
    'dd if=build/out.bin of=build/copy.bin bs=4096',
    'python3 -m pip install -r requirements.txt',
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


class NotGuaranteed(unittest.TestCase):
    def test_documented_limits_pass_the_pattern_check(self):
        for run in NOT_GUARANTEED:
            with self.subTest(run=run):
                self.assertIsNone(guard._run_write_problem(run), f'documented limit changed: {run}')


if __name__ == '__main__':
    unittest.main()
