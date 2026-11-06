"""Regression tests for the minimal trusted-main artifact guard invocation."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))
import check  # noqa: E402


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()


class TrustedGuardInvocation(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.repo = Path(temp.name) / "repo"
        (self.repo / "tools").mkdir(parents=True)
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.name", "test fixture")
        git(self.repo, "config", "user.email", "fixture@example.invalid")
        (self.repo / "tools" / "artifact_guard.py").write_text(
            "import sys\n"
            "from pathlib import Path\n"
            "if __name__ == '__main__':\n"
            "    assert sys.argv[1] == '--root'\n"
            "    Path(sys.argv[2], 'trusted-ran.marker').write_text('trusted')\n",
            encoding="utf-8")
        git(self.repo, "add", "tools/artifact_guard.py")
        git(self.repo, "commit", "-qm", "trusted main guard")
        git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")

    def test_main_guard_runs_without_candidate_guard_import(self):
        (self.repo / "tools" / "artifact_guard.py").write_text(
            "raise SystemExit('CANDIDATE_GUARD_EXECUTED')\n", encoding="utf-8")
        check.check_with_trusted_artifact_guard(self.repo)
        self.assertEqual((self.repo / "trusted-ran.marker").read_text(), "trusted")

    def test_missing_trusted_ref_fails_closed(self):
        git(self.repo, "update-ref", "-d", "refs/remotes/origin/main")
        with self.assertRaisesRegex(ValueError, "origin/main"):
            check.check_with_trusted_artifact_guard(self.repo)
        self.assertFalse((self.repo / "trusted-ran.marker").exists())

    def test_missing_guard_in_trusted_commit_fails_closed(self):
        git(self.repo, "rm", "-q", "tools/artifact_guard.py")
        git(self.repo, "commit", "-qm", "remove the trusted guard")
        git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        with self.assertRaisesRegex(ValueError, "unavailable"):
            check.check_with_trusted_artifact_guard(self.repo)
        self.assertFalse((self.repo / "trusted-ran.marker").exists())

    def test_check_module_import_does_not_load_candidate_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            shutil.copyfile(REPO / "tools" / "check.py", path / "check.py")
            (path / "environment.py").write_text(
                "from pathlib import Path\n"
                "ROOT = Path(__file__).resolve().parent\n"
                "def executable(name): return name\n"
                "def core_path(): return None\n", encoding="utf-8")
            (path / "artifact_guard.py").write_text(
                "raise SystemExit('CANDIDATE_GUARD_EXECUTED')\n", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-I", "-c",
                 "import sys; sys.path.insert(0, sys.argv[1]); import check; print('SAFE')",
                 str(path)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("SAFE", result.stdout)
            self.assertNotIn("CANDIDATE_GUARD_EXECUTED", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
