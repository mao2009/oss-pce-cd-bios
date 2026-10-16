"""No adapter or fabricated trace may be counted as a real reference execution."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))
import reference_bios_runner as runner


def jsr_observation():
    state = {"a": 0xAA, "x": 0x03, "y": 0x00, "p": 0x06, "sp": 0xFF, "pc": 0x3E09,
             "mpr": [0xF8, 0xF8, 0, 0, 0, 0, 0, 0]}
    return {
        "schema": runner.OBS_SCHEMA, "api_id": "slot-1e", "status": "OBSERVED",
        "engine": "geargrafx", "checkpoint": "slot_1e_ex_getver_returned_to_caller",
        "registers": {k: state[k] for k in ("a", "x", "y", "p", "sp", "pc")},
        "mpr": state["mpr"],
        "ram_diff": [{"address": 0x21FE, "before": 0, "after": 8}],
        "instructions_executed": 32,
        "before": dict(state, pc=0x3E00),
        "after": state,
        "call": {"entry_reached": True, "callee_pc": 0xF02D, "returned": True},
        "guard": {"detected": False, "status": "no_violation"},
    }


class ReferenceRunnerTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "schema": "oss-pce-cd-bios/reference-bios-run-config/v1",
            "api_id": "slot-1e",
            "bios_path": "rom/never-committed-reference.pce",
            "bios_sha256": "a" * 64,
            "test_rom_path": "build/reference-bios/test.pce",
            "test_rom_sha256": "b" * 64,
            "emulator_revision": "pinned-source",
            "max_instructions": 300,
            "ram_write_allowlist": [{"start": 8192, "end": 16384}],
            "isolated_run": True,
            "reviewed_abi": True,
            "approved_fixture": True,
        }
        self.observation = jsr_observation()

    def test_missing_adapter_blocks(self):
        with self.assertRaises(runner.Blocked):
            runner.run(self.config, None)

    def test_schema_validation_and_negatives(self):
        runner.check_observation(self.observation, self.config)
        for mutate in [
            {"status": "PASS"},
            {"status": "NOT_RUN"},
            {"instructions_executed": 301},
            {"instructions_executed": True},
            {"ram_diff": [{"address": 8191, "before": 0, "after": 1}]},
            {"ram_diff": [{"address": 8192, "before": 0, "after": 256}]},
            {"registers": {}},
            {"engine": "fake"},
            {"before": {}},
            {"after": {"a": 0}},
            {"call": {"entry_reached": True, "callee_pc": 1, "returned": False}},
            {"guard": {"detected": True, "status": "violation"}},
            {"guard": {"detected": False}},
        ]:
            with self.subTest(mutate=mutate):
                with self.assertRaises(runner.Blocked):
                    runner.check_observation(dict(self.observation, **mutate), self.config)

    def test_reset_observation_keeps_the_nine_field_shape(self):
        cfg = dict(self.config, api_id="reference-reset-only")
        reset = {
            "schema": runner.OBS_SCHEMA, "api_id": "reference-reset-only", "status": "OBSERVED",
            "engine": "geargrafx", "checkpoint": "reset_first_instruction_no_api_callee",
            "registers": {"a": 0, "x": 0, "y": 0, "p": 4, "sp": 255, "pc": 0xE0F4},
            "mpr": [0, 0, 0, 0, 0, 0, 0, 0], "ram_diff": [], "instructions_executed": 1,
        }
        runner.check_observation(reset, cfg)
        with self.assertRaises(runner.Blocked):
            runner.check_observation(dict(reset, before={}), cfg)


if __name__ == "__main__":
    unittest.main()
