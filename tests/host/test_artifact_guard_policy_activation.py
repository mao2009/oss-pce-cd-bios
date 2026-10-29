"""The staged trust anchor must retire old guard-verify jobs after activation."""
from pathlib import Path
import json
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import artifact_guard as guard  # noqa: E402

ALLOW = "tools/artifact_guard_run_allowlist.json"
MARKER = "tools/trusted_guard_policy_active.txt"


class GuardPolicyActivation(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "tools").mkdir()
        shutil.copyfile(ROOT / ALLOW, self.root / ALLOW)

    def activate(self):
        (self.root / MARKER).write_text(guard.POLICY_ACTIVATION_TEXT, encoding="utf-8")

    def records(self):
        return json.loads((self.root / ALLOW).read_text(encoding="utf-8"))

    def test_before_activation_both_exact_guard_verify_job_versions_are_allowed(self):
        entries = self.records()
        future = next(e for e in entries if e["step"] == guard.POLICY_STEP_NAME)
        current = [e for e in entries if e["workflow"] == ".github/workflows/ci.yml"
                   and e["job"] == "guard-verify" and e["job_sha256"] != future["job_sha256"]]
        self.assertGreater(len(current), 0)
        table = guard.load_run_allowlist(self.root)
        self.assertTrue(any((e["sha256"], e["job_sha256"]) in table.get((
            e["workflow"], e["job"], e["step"], e["shell"], e["runs_on"]), set()) for e in current))

    def test_after_activation_only_the_job_containing_policy_check_is_approved(self):
        self.activate()
        entries = self.records()
        future = next(e for e in entries if e["step"] == guard.POLICY_STEP_NAME)
        table = guard.load_run_allowlist(self.root)
        pairs = [item for key, values in table.items()
                 if key[0] == ".github/workflows/ci.yml" and key[1] == "guard-verify" for item in values]
        self.assertGreater(len(pairs), 0)
        self.assertTrue(all(job_sha == future["job_sha256"] for _, job_sha in pairs))

    def test_invalid_activation_content_fails_closed(self):
        (self.root / MARKER).write_text("disabled\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "malformed"):
            guard.load_run_allowlist(self.root)

    def test_activated_trusted_marker_cannot_be_removed_by_candidate(self):
        candidate = self.root / "candidate"
        trusted = self.root / "trusted"
        for tree in (candidate, trusted):
            (tree / "tools").mkdir(parents=True)
            shutil.copyfile(ROOT / "tools/artifact_guard.py", tree / "tools/artifact_guard.py")
            shutil.copyfile(ROOT / ALLOW, tree / ALLOW)
        (trusted / MARKER).write_text(guard.POLICY_ACTIVATION_TEXT, encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "differs from trusted main"):
            guard.policy_violations(candidate, trusted)
        (candidate / MARKER).write_text(guard.POLICY_ACTIVATION_TEXT, encoding="utf-8")
        guard.policy_violations(candidate, trusted)

    def test_candidate_cannot_bootstrap_a_different_activation_value(self):
        candidate = self.root / "candidate"
        trusted = self.root / "trusted"
        for tree in (candidate, trusted):
            (tree / "tools").mkdir(parents=True)
            shutil.copyfile(ROOT / "tools/artifact_guard.py", tree / "tools/artifact_guard.py")
            shutil.copyfile(ROOT / ALLOW, tree / ALLOW)
        (candidate / MARKER).write_text("skip-checks\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "invalid bootstrap value"):
            guard.policy_violations(candidate, trusted)


if __name__ == "__main__":
    unittest.main()
