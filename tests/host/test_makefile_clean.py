"""The clean target must remove the build/ of the checkout that owns the Makefile, not the build/ of the directory make runs in.

make is not installed on the Windows host, so the recipe is taken from the Makefile text and run directly. The test uses a
copy of the Makefile inside a temporary checkout, so no real build/ directory is touched.
"""
import shlex
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


class CleanTargetTests(unittest.TestCase):
    def clean_argv(self, checkout):
        lines = (REPO / 'Makefile').read_text(encoding='utf-8').splitlines()
        recipe = lines[lines.index('clean:') + 1]
        self.assertTrue(recipe.startswith('\t'), recipe)
        command = recipe[1:].replace('$(PYTHON)', Path(sys.executable).as_posix())  # posix: shlex would eat '\'
        command = command.replace('$(REPO_ROOT)', checkout.as_posix() + '/')
        self.assertNotIn('$(', command)
        return shlex.split(command)

    def test_clean_removes_the_checkout_build_and_not_the_invoking_directory(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            checkout = Path(tmp, 'checkout')
            (checkout / 'build').mkdir(parents=True)
            (checkout / 'build' / 'artifact.bin').write_bytes(b'x')
            invoker = Path(tmp, 'elsewhere')
            (invoker / 'build').mkdir(parents=True)
            (invoker / 'build' / 'precious.txt').write_text('not ours')
            result = subprocess.run(self.clean_argv(checkout), cwd=invoker, capture_output=True, text=True)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertFalse((checkout / 'build').exists())
            self.assertTrue((invoker / 'build' / 'precious.txt').is_file())


if __name__ == '__main__':
    unittest.main()
