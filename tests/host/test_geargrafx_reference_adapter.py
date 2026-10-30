"""The guarded local adapter must validate inputs and fail closed without the core."""
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import geargrafx_reference_adapter as adapter


def probe_row(**mutation):
    state = {"a": 0xAA, "x": 0x03, "y": 0x00, "p": 0x06, "sp": 0xFF, "pc": 0x3E09,
             "mpr": [0x00, 0xF8, 0, 0, 0, 0, 0, 0]}
    row = {"mode": "call", "status": "ok", "reason": "",
           "before": dict(state, pc=0x3E00), "after": state,
           "callee_pc": 0xE000, "entry_reached": True, "returned": True,
           "detected": False, "detected_pc": 0, "instructions_executed": 12, "budget": 3000,
           "ram_diff": [{"address": 0x2010, "before": 0, "after": 0x5A}]}  # synthetic placeholder
    row.update(mutation)
    return row


class ReferenceGeargrafxAdapterTests(unittest.TestCase):
    def test_api_execution_rejected_before_emulator(self):
        for api in ("slot-03", "psg-48-00"):
            with self.subTest(api=api):
                # The gate runs first on every branch, so unsupported ids are refused there.
                with self.assertRaisesRegex(ValueError, "reference JSR gate refused|GATE_TARGET_UNMAPPED"):
                    adapter.observe({"api_id": api, "max_instructions": 100})

    def test_slot_1e_requires_a_single_page_aligned_allowlist(self):
        with self.assertRaisesRegex(ValueError, "one RAM allowlist page interval"):
            adapter.validate_jsr_config({"api_id": "slot-1e", "max_instructions": 100})

    def test_jsr_experiment_rejects_invalid_budgets_before_emulator(self):
        for n in (0, 2, 3, 1000001, True):
            with self.subTest(n=n):
                with self.assertRaisesRegex(ValueError, "invalid JSR instruction budget"):
                    adapter.validate_jsr_config({"api_id": "slot-1e", "max_instructions": n})

    def test_jsr_rejects_unusable_allowlists(self):
        for allow in (None, "page", [], [{"start": 0, "end": 0x2000}, {"start": 0x2000, "end": 0x4000}],
                      [{"start": 1, "end": 0x2000}], [{"start": 0, "end": 0x10001}]):
            with self.subTest(allow=allow):
                with self.assertRaisesRegex(ValueError, "allowlist"):
                    adapter.validate_jsr_config({"api_id": "slot-1e", "max_instructions": 100,
                                                 "ram_write_allowlist": allow})

    def test_jsr_observation_accepts_a_completed_in_allowlist_call(self):
        observation = adapter.jsr_observation(probe_row())
        self.assertEqual(observation["api_id"], "slot-1e")
        self.assertEqual(observation["registers"]["x"], 3)
        self.assertEqual(observation["checkpoint"], adapter.JSR_CHECKPOINT)
        self.assertFalse(observation["guard"]["detected"])
        self.assertEqual(observation["ram_diff"], [{"address": 0x2010, "before": 0, "after": 0x5A}])

    def test_jsr_observation_rejects_incomplete_or_violating_runs(self):
        for mutation, message in [
            ({"status": "rejected", "reason": "budget_exhausted_before_rts"}, "rejected the call"),
            ({"entry_reached": False}, "never entered and returned"),
            ({"returned": False}, "never entered and returned"),
            ({"detected": True}, "store outside the allowlist"),
            ({"mode": "neg-write"}, "no call record"),
            ({"after": {}}, "state has the wrong shape"),
            ({"before": {"a": 1}}, "state has the wrong shape"),
            ({"instructions_executed": 0}, "instruction count invalid"),
        ]:
            with self.subTest(mutation=mutation):
                with self.assertRaisesRegex(ValueError, message):
                    adapter.jsr_observation(probe_row(**mutation))

    def test_reset_rejects_invalid_step_limits_before_emulator(self):
        # Gate patched to pass so the reset-only step-budget check itself is reached.
        for n in (0, -1, 10000001, True):
            with self.subTest(n=n), mock.patch.object(adapter.G, "require_allowed", return_value=None):
                with self.assertRaisesRegex(ValueError, "invalid step budget"):
                    adapter.observe({"api_id": "reference-reset-only",
                                     "max_instructions": n})

    def test_reset_is_gated_before_step_limits_and_emulator(self):
        # reference-reset-only is not a gated slot id, so the gate refuses it before budget checks.
        for n in (0, -1, 10000001, True):
            with self.subTest(n=n):
                with self.assertRaisesRegex(ValueError, "GATE_TARGET_UNMAPPED"):
                    adapter.observe({"api_id": "reference-reset-only",
                                     "max_instructions": n})


if __name__ == "__main__":
    unittest.main()
