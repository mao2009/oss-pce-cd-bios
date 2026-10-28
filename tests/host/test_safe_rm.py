"""Unit tests for tools/safe_rm.sh.

Every test runs inside a fresh temporary HOME. Nothing outside that
temporary tree is ever passed as a removal target, and refusal tests assert
that the protected directories are still intact afterwards.
"""

import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HELPER = REPO / "tools" / "safe_rm.sh"


def run_safe_rm(home, target):
    env = dict(os.environ, HOME=str(home))
    script = 'set -u; . "$1"; safe_rm_rf "$2"'
    return subprocess.run(
        ["bash", "-c", script, "_", str(HELPER), target],
        env=env,
        capture_output=True,
        text=True,
    )


class SafeRmTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name).resolve()
        self.home = self.base / "home"
        self.home.mkdir()
        self.root = self.home / ".cache" / "oss-pce-safe-rm"
        self.root.mkdir(parents=True)
        os.chmod(self.root, stat.S_IRWXU)
        self.outside = self.base / "outside"
        self.outside.mkdir()
        (self.outside / "keep.txt").write_text("keep")

    def tearDown(self):
        self._tmp.cleanup()

    def assertRefused(self, target):
        result = run_safe_rm(self.home, target)
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.outside / "keep.txt").exists())
        return result

    def test_empty_target_is_refused(self):
        self.assertRefused("")

    def test_undefined_variable_aborts_under_set_u(self):
        script = 'set -u; . "$1"; safe_rm_rf "$UNDEFINED_VARIABLE_FOR_TEST"'
        env = dict(os.environ, HOME=str(self.home))
        env.pop("UNDEFINED_VARIABLE_FOR_TEST", None)
        result = subprocess.run(
            ["bash", "-c", script, "_", str(HELPER)],
            env=env, capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((self.outside / "keep.txt").exists())

    def test_filesystem_root_is_refused(self):
        self.assertRefused("/")

    def test_home_directory_is_refused(self):
        self.assertRefused(str(self.home))

    def test_parent_of_home_is_refused(self):
        self.assertRefused(str(self.home.parent))

    def test_relative_path_is_refused(self):
        self.assertRefused("oss-pce-safe-rm/x")

    def test_dotdot_escape_is_refused(self):
        self.assertRefused(f"{self.root}/../../outside")

    def test_dot_component_is_refused(self):
        self.assertRefused(f"{self.root}/./x")

    def test_allowed_root_itself_is_refused(self):
        result = run_safe_rm(self.home, str(self.root))
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.root.is_dir())

    def test_path_outside_root_is_refused(self):
        self.assertRefused(str(self.outside))

    def test_symlink_target_is_refused(self):
        link = self.root / "link"
        link.symlink_to(self.outside, target_is_directory=True)
        result = run_safe_rm(self.home, str(link))
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((self.outside / "keep.txt").exists())
        self.assertTrue(link.is_symlink())

    def test_symlinked_ancestor_is_refused(self):
        (self.root / "link").symlink_to(self.outside, target_is_directory=True)
        target = self.root / "link" / "keep-dir"
        result = run_safe_rm(self.home, str(target))
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((self.outside / "keep.txt").exists())

    def test_directory_with_git_is_refused(self):
        repo = self.root / "repo"
        (repo / ".git").mkdir(parents=True)
        (repo / "data.txt").write_text("uncommitted")
        result = run_safe_rm(self.home, str(repo))
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((repo / "data.txt").exists())

    def test_worktree_style_git_file_is_refused(self):
        wt = self.root / "wt"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /elsewhere")
        result = run_safe_rm(self.home, str(wt))
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((wt / ".git").exists())

    def test_nested_git_is_refused(self):
        outer = self.root / "outer"
        (outer / "inner" / ".git").mkdir(parents=True)
        result = run_safe_rm(self.home, str(outer))
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((outer / "inner" / ".git").exists())

    def test_missing_root_is_refused(self):
        shutil.rmtree(self.root)
        result = run_safe_rm(self.home, str(self.root / "x"))
        self.assertNotEqual(result.returncode, 0)

    def test_symlinked_root_is_refused(self):
        shutil.rmtree(self.root)
        self.root.symlink_to(self.outside, target_is_directory=True)
        result = run_safe_rm(self.home, str(self.root / "keep.txt"))
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((self.outside / "keep.txt").exists())

    def test_normal_scratch_directory_is_removed(self):
        scratch = self.root / "scratch-one"
        (scratch / "sub").mkdir(parents=True)
        (scratch / "sub" / "file.txt").write_text("x")
        result = run_safe_rm(self.home, str(scratch))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(scratch.exists())
        self.assertTrue((self.outside / "keep.txt").exists())

    def test_nonexistent_target_is_a_no_op(self):
        result = run_safe_rm(self.home, str(self.root / "absent"))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_spaces_and_literal_wildcard_are_handled(self):
        spaced = self.root / "a b"
        spaced.mkdir()
        star = self.root / "*"
        star.mkdir()
        sibling = self.root / "sibling"
        sibling.mkdir()
        self.assertEqual(run_safe_rm(self.home, str(spaced)).returncode, 0)
        self.assertFalse(spaced.exists())
        self.assertEqual(run_safe_rm(self.home, str(star)).returncode, 0)
        self.assertFalse(star.exists())
        self.assertTrue(sibling.is_dir())


if __name__ == "__main__":
    unittest.main()
