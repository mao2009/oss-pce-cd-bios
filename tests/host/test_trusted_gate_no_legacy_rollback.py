"""Integration regression: post-cutover trusted policy forbids reverting guard-verify to its old approved job."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import artifact_guard as guard  # noqa: E402


class LegacyGuardVerifyRollback(unittest.TestCase):
    def test_activated_policy_refuses_the_exact_pre_migration_guard_verify_job(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            candidate = tmp / "candidate"
            trusted = tmp / "trusted"
            shutil.copytree(ROOT / ".github", candidate / ".github")
            (trusted / "tools").mkdir(parents=True)
            shutil.copyfile(ROOT / guard.RUN_ALLOWLIST_FILE, trusted / guard.RUN_ALLOWLIST_FILE)
            (trusted / guard.POLICY_ACTIVATION_FILE).write_text(
                guard.POLICY_ACTIVATION_TEXT, encoding="utf-8")
            old_ci = (ROOT / "tests/fixtures/trusted-gate-legacy-ci.yml").read_text(encoding="utf-8")
            current_ci = candidate / ".github/workflows/ci.yml"
            self.assertNotEqual(current_ci.read_text(encoding="utf-8"), old_ci)

            # Positive control: the new job with the trusted Policy check is approved.
            guard.enforced_check(candidate, guard.check_guard_placement, allowlist_root=trusted)

            # Exact old CI job is still present in the *transitional* union table,
            # but must be forbidden once trusted main has the activation marker.
            current_ci.write_text(old_ci, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "not an approved exact match"):
                guard.enforced_check(candidate, guard.check_guard_placement, allowlist_root=trusted)

            # Negative control: absent activation the previously approved old
            # job is accepted, proving the above failure is caused by cutover.
            (trusted / guard.POLICY_ACTIVATION_FILE).unlink()
            guard.enforced_check(candidate, guard.check_guard_placement, allowlist_root=trusted)


if __name__ == "__main__":
    unittest.main()
