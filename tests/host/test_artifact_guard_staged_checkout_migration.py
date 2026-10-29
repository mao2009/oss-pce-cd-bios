"""Migration regression: only the two exact trusted checkout layouts may pass."""
from pathlib import Path
import sys
import unittest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))
import artifact_guard as guard  # noqa: E402
from test_artifact_guard import TRUSTED_CHECKOUT  # noqa: E402


def accepts(step):
    lines = step.splitlines()
    # A standalone step body at the standard six-space indentation.
    return guard._is_trusted_checkout(lines, (0, len(lines), "guard-test"), 6)


class ExactCheckoutMigration(unittest.TestCase):
    def test_new_trusted_tools_checkout_is_accepted(self):
        self.assertTrue(accepts(TRUSTED_CHECKOUT))

    def test_legacy_single_guard_checkout_is_accepted(self):
        self.assertTrue(accepts(TRUSTED_CHECKOUT.replace(
            "sparse-checkout: tools\n", "sparse-checkout: tools/artifact_guard.py\n")))

    def test_unexpected_checkout_path_is_rejected(self):
        self.assertFalse(accepts(TRUSTED_CHECKOUT.replace(
            "sparse-checkout: tools\n", "sparse-checkout: .\n")))

    def test_candidate_repository_override_is_rejected(self):
        self.assertFalse(accepts(TRUSTED_CHECKOUT.replace(
            "          ref: main\n", "          repository: attacker/guard\n          ref: main\n")))

    def test_duplicate_sparse_checkout_is_rejected(self):
        self.assertFalse(accepts(TRUSTED_CHECKOUT.replace(
            "          sparse-checkout: tools\n",
            "          sparse-checkout: tools/artifact_guard.py\n          sparse-checkout: tools\n")))


if __name__ == "__main__":
    unittest.main()
