"""P0 regression: candidate guard imports must not execute before trusted policy validation."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))
import check  # noqa: E402

from test_check_trusted_allowlist_529 import TrustedMainFixture  # noqa: E402


class CheckModuleImportIsolation(unittest.TestCase):
    def test_importing_check_does_not_import_candidate_artifact_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "check.py").write_text((REPO / "tools" / "check.py").read_text(encoding="utf-8"), encoding="utf-8")
            (root / "environment.py").write_text(
                "from pathlib import Path\n"
                "ROOT = Path(__file__).resolve().parent\n"
                "def executable(name): return name\n"
                "def core_path(): return None\n", encoding="utf-8")
            (root / "artifact_guard.py").write_text(
                "raise SystemExit('UNTRUSTED_MODULE_EXECUTED')\n", encoding="utf-8")
            child = subprocess.run(
                [sys.executable, "-I", "-c",
                 "import sys; sys.path.insert(0, sys.argv[1]); import check; print('IMPORTED_SAFE')",
                 str(root)],
                capture_output=True, text=True)
            self.assertEqual(child.returncode, 0, child.stdout + child.stderr)
            self.assertIn("IMPORTED_SAFE", child.stdout)
            self.assertNotIn("UNTRUSTED_MODULE_EXECUTED", child.stdout + child.stderr)


class TrustedGuardExecution(TrustedMainFixture):
    def test_poisoned_candidate_yaml_module_is_not_imported(self):
        (self.repo / "tools" / "yaml.py").write_text(
            "raise SystemExit('CANDIDATE_YAML_EXECUTED')\n", encoding="utf-8")
        self.enforce()  # -I prevents the candidate tools directory from entering the guard's imports.

    def test_candidate_guard_code_is_refused_without_running_it(self):
        path = self.repo / "tools" / "artifact_guard.py"
        path.write_text(path.read_text(encoding="utf-8") +
                        "\nraise SystemExit('CANDIDATE_GUARD_EXECUTED')\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "differs from origin/main") as error:
            self.enforce()
        self.assertNotIn("CANDIDATE_GUARD_EXECUTED", str(error.exception))

    def test_isolated_guard_reports_approval_refusal_from_trusted_copy(self):
        self.edit(".github/workflows/ci.yml", "run: make check", "run: make check --not-approved")
        with self.assertRaisesRegex(ValueError, "not an approved exact match"):
            self.enforce()


if __name__ == "__main__":
    unittest.main()
