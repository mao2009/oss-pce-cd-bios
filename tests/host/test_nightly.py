import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
from nightly import SCHEMA, decide, find_baseline


class NightlyTests(unittest.TestCase):
    def test_first_changed_identical_and_legacy(self):
        sha = "a" * 40
        baseline = {"schema": SCHEMA, "source_ref": "main", "source_sha": sha}
        self.assertTrue(decide(sha, None))
        self.assertFalse(decide(sha, baseline))
        self.assertTrue(decide("b" * 40, baseline))
        self.assertTrue(decide(sha, {**baseline, "schema": "legacy"}))
        self.assertTrue(decide(sha, {**baseline, "source_ref": "feature"}))
        with self.assertRaises(ValueError):
            decide("invalid", baseline)

    def test_success_only_baseline_ignores_failed_current_and_expired(self):
        pages = [{"artifacts": [{"id": i, "expired": i == 5,
                                  "workflow_run": {"id": i}} for i in (5, 4, 3, 2, 1)]}]
        baseline = {"schema": SCHEMA, "source_ref": "main", "source_sha": "a" * 40}
        calls = []

        def fake_gh(*args):
            calls.append(args)
            if "--paginate" in args:
                return json.dumps(pages)
            if args[0] == "api":
                run = int(args[-1].split("/")[-1])
                return json.dumps({"status": "completed", "conclusion": "failure" if run == 3 else "success",
                                   "path": ".github/workflows/nightly.yml", "head_branch": "main"})
            directory = Path(args[args.index("--dir") + 1])
            (directory / "nightly-state.json").write_text(json.dumps(baseline))
            return ""

        with patch("nightly.gh", side_effect=fake_gh):
            self.assertEqual(find_baseline("owner/repo", "4"), baseline)
        self.assertEqual([args[2] for args in calls if args[0] == "run"], ["2"])


if __name__ == "__main__":
    unittest.main()
