"""Guards that host-side tests live where `make test` discovers them (Makefile is the required gate)."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class RunPathTests(unittest.TestCase):
    def test_debugger_setup_is_discovered_by_make_test(self):
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        recipe = re.search(r"^test:[^\n]*\n((?:\t[^\n]*\n)+)", makefile, re.M)
        self.assertIsNotNone(recipe, "Makefile test target not found")
        self.assertIn("discover -s tests/host", recipe.group(1))
        self.assertTrue((ROOT / "tests/host/test_debugger_setup.py").is_file(),
                        "test_debugger_setup.py must sit in tests/host to run under make test")


if __name__ == "__main__":
    unittest.main()
