"""Release-gate exit codes: expected refusal (3) must never be confused with failures."""
import contextlib
import io
import subprocess
import sys
import unittest
from unittest.mock import patch

from tools import build_rom
from tools.build_rom import EXIT_BLOCKED, EXIT_ERROR, EXIT_OK, ROOT, ReleaseBlocked
from tools.expect_exit import main as _expect

PY = sys.executable


def expect(argv):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return _expect(argv)


def check(code, program):
    return expect(['x', str(code), '^BLOCKED:', '--', PY, '-c', program])


class ReleaseGateExitCodes(unittest.TestCase):
    def test_real_gate_refuses_with_blocked_code(self):
        r = subprocess.run([PY, str(ROOT / 'tools/build_rom.py'), '--release-gate'], capture_output=True, text=True)
        self.assertEqual(r.returncode, EXIT_BLOCKED)
        self.assertRegex(r.stderr, r'^BLOCKED:')
        self.assertNotEqual(EXIT_BLOCKED, EXIT_ERROR)

    def test_main_maps_outcomes(self):
        with patch('sys.argv', ['build_rom', '--release-gate']), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(build_rom.main(), EXIT_BLOCKED)
            with patch.object(build_rom, 'load_slots', side_effect=ValueError('broken slots')):
                self.assertEqual(build_rom.main(), EXIT_ERROR)
            with patch.object(build_rom, 'release_gate', return_value=None):  # hypothetical pass
                self.assertEqual(build_rom.main(), EXIT_OK)
        self.assertTrue(issubclass(ReleaseBlocked, ValueError))

    def test_wrapper_accepts_only_expected_refusal(self):
        self.assertEqual(check(3, 'import sys; print("BLOCKED: x", file=sys.stderr); sys.exit(3)'), 0)
        real = [PY, str(ROOT / 'tools/build_rom.py'), '--release-gate']
        self.assertEqual(expect(['x', '3', '^BLOCKED:', '--', *real]), 0)

    def test_wrapper_rejects_unexpected_outcomes(self):
        cases = {
            'unexpected success': 'print("BLOCKED: x")',
            'tool/build error': 'import sys; print("ERROR: missing tool ca65", file=sys.stderr); sys.exit(2)',
            'error that still says BLOCKED': 'import sys; print("BLOCKED: x"); sys.exit(2)',
            'refusal without message': 'import sys; sys.exit(3)',
            'python exception': 'raise RuntimeError("boom")',
            'exit 3 with traceback': 'import sys, traceback\ntry: raise KeyError()\nexcept KeyError: traceback.print_exc()\nprint("BLOCKED: x"); sys.exit(3)',
        }
        for name, program in cases.items():
            with self.subTest(name):
                self.assertEqual(check(3, program), 1)

    def test_wrapper_rejects_missing_command(self):
        self.assertEqual(expect(['x', '3', 'BLOCKED', '--', '/nonexistent/tool']), 1)


if __name__ == '__main__':
    unittest.main()
