"""Issue #333 acceptance: host tests never fetch, build, need or run the real Geargrafx.

Real-Geargrafx tests live in tests/integration and run through `make integration-test`,
which `make test` depends on (and ci.yml runs `make test` in its required verify job).
"""
import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# Line-level execution paths: the Geargrafx fetch/build calls and a built-core presence check.
REAL_LINE = re.compile(r"""fetch\(\s*['"]geargrafx|build_geargrafx\(|core_path\(\)\.is_file\(\)""")
# A tests/integration Geargrafx script path (any spelling of the file name).
REAL_PATH = re.compile(r"geargrafx_\w+\.py")
LAUNCHERS = {"run", "Popen", "call", "check_call", "check_output", "system"}


def offenders_in(source):
    """Line numbers of real-Geargrafx execution paths in one Python source."""
    found = []
    tree = ast.parse(source)
    # Module-level names bound to a Geargrafx script path (e.g. INTEGRATION = ... / "geargrafx_x.py").
    real_names = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(c, ast.Constant) and isinstance(c.value, str) and REAL_PATH.search(c.value)
                for c in ast.walk(node.value)):
            real_names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", "")) in LAUNCHERS:
            for arg in ast.walk(node):
                if (isinstance(arg, ast.Constant) and isinstance(arg.value, str) and REAL_PATH.search(arg.value)) \
                        or (isinstance(arg, ast.Name) and arg.id in real_names):
                    found.append(node.lineno)
                    break
    for number, line in enumerate(source.splitlines(), 1):
        # read_text() of a generator script (a file read, not an execution) is allowed.
        if REAL_LINE.search(line) and "read_text" not in line:
            found.append(number)
    return sorted(found)


class DetectorTests(unittest.TestCase):
    def test_detects_direct_launch_of_geargrafx_integration_script(self):
        cases = [
            'subprocess.run([sys.executable, str(ROOT / "tests/integration/geargrafx_bcdbin.py")])\n',
            'SCRIPT = ROOT / "tests/integration" / "geargrafx_smoke.py"\nsubprocess.run([sys.executable, str(SCRIPT)])\n',
            'subprocess.Popen(["python", "tests/integration/geargrafx_api.py"])\n',
            'subprocess.run([sys.executable,\n                 str(ROOT / "tests/integration/geargrafx_st_bypass.py")])\n',
        ]
        for source in cases:
            with self.subTest(source=source):
                self.assertTrue(offenders_in(source))

    def test_ignores_reads_and_docstrings(self):
        cases = [
            'text = (ROOT / "tests/integration/geargrafx_cdread.py").read_text(encoding="utf-8")\n',
            '"""Shape as tests/integration/geargrafx_cdread.py writes it."""\n',
        ]
        for source in cases:
            with self.subTest(source=source):
                self.assertEqual(offenders_in(source), [])

    def test_detects_fetch_build_and_core_presence(self):
        for source in ["bootstrap.fetch('geargrafx')\n", "bootstrap.build_geargrafx(d, 1)\n",
                       "self.assertTrue(core_path().is_file())\n"]:
            with self.subTest(source=source):
                self.assertTrue(offenders_in(source))


class HostIsolationTests(unittest.TestCase):
    def test_host_tests_do_not_fetch_build_need_or_run_real_geargrafx(self):
        offenders = []
        for path in sorted((ROOT / "tests/host").glob("*.py")):
            if path.name == Path(__file__).name:
                continue
            for number in offenders_in(path.read_text(encoding="utf-8")):
                offenders.append(f"{path.name}:{number}")
        self.assertEqual(offenders, [], "real Geargrafx fetch/build/need/run found in tests/host")

    def test_make_test_runs_integration_test_target(self):
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        recipe = re.search(r"^test:[^\n]*\n((?:\t[^\n]*\n)+)", makefile, re.M)
        self.assertIsNotNone(recipe, "Makefile test target not found")
        self.assertIn("$(MAKE) integration-test", recipe.group(1))
        self.assertRegex(makefile, r"(?m)^integration-test:\n\t[^\n]*discover -s tests/integration")

    def test_ci_required_job_runs_make_test(self):
        ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn("run: make test", ci)


if __name__ == "__main__":
    unittest.main()
