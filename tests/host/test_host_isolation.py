"""Issue #333 acceptance: host tests never fetch, build or run the real Geargrafx.

Real-Geargrafx tests live in tests/integration and run through `make integration-test`,
which `make test` depends on (and ci.yml runs `make test` in its required verify job).
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# Execution paths only: the Geargrafx fetch/build calls and the real-core script path bound to a name.
REAL = re.compile(r"""fetch\(\s*['"]geargrafx|build_geargrafx\(|INTEGRATION\s*=.*integration/geargrafx_""")


class HostIsolationTests(unittest.TestCase):
    def test_host_tests_do_not_fetch_build_or_run_real_geargrafx(self):
        offenders = []
        for path in sorted((ROOT / "tests/host").glob("*.py")):
            if path.name == Path(__file__).name:
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                # read_text() of a generator script (a file read, not an execution) is allowed.
                if REAL.search(line) and "read_text" not in line:
                    offenders.append(f"{path.name}:{number}")
        self.assertEqual(offenders, [], "real Geargrafx fetch/build/run found in tests/host")

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
