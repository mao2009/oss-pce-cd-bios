"""Real Geargrafx adapter cannot claim any API callee execution yet."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import geargrafx_reference_adapter as adapter


class ReferenceGeargrafxAdapterTests(unittest.TestCase):
    def test_api_execution_rejected_before_emulator(self):
        for api in ("slot-03", "psg-48-00"):
            with self.subTest(api=api):
                with self.assertRaisesRegex(ValueError, "no reference BIOS API call wrapper"):
                    adapter.observe({"api_id": api, "max_instructions": 100})

    def test_valid_jsr_is_blocked_before_emulator_until_runtime_write_guard(self):
        with self.assertRaisesRegex(ValueError, "runtime RAM/I/O write guard"):
            adapter.observe({"api_id": "slot-1e", "max_instructions": 100})

    def test_jsr_experiment_rejects_invalid_budgets_before_emulator(self):
        for n in (0, 2, 1000001, True):
            with self.subTest(n=n):
                with self.assertRaisesRegex(ValueError, "invalid JSR instruction budget"):
                    adapter.observe({"api_id": "slot-1e", "max_instructions": n})

    def test_reset_rejects_invalid_step_limits_before_emulator(self):
        for n in (0, -1, 10000001, True):
            with self.subTest(n=n):
                with self.assertRaisesRegex(ValueError, "invalid step budget"):
                    adapter.observe({"api_id": "reference-reset-only",
                                     "max_instructions": n})


if __name__ == "__main__":
    unittest.main()
