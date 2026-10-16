"""No adapter or fabricated trace may be counted as a real reference execution."""
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
import sys
sys.path.insert(0, str(TOOLS))
import reference_bios_runner as runner


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
            "ram_write_allowlist": [{"start": 8192, "end": 8256}],
            "isolated_run": True,
            "reviewed_abi": True,
            "approved_fixture": True,
        }
        self.observation = {
            "schema": runner.OBS_SCHEMA, "api_id": "slot-1e", "status": "OBSERVED",
            "engine": "geargrafx", "checkpoint": "return",
            "registers": {"a": 0, "x": 1, "y": 2, "p": 0x24, "sp": 255, "pc": 0xe05a},
            "mpr": [255, 248, 0, 0, 0, 0, 0, 0],
            "ram_diff": [{"address": 8192, "before": 0, "after": 1}],
            "instructions_executed": 32,
        }

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
        ]:
            with self.subTest(mutate=mutate):
                with self.assertRaises(runner.Blocked):
                    runner.check_observation(dict(self.observation, **mutate), self.config)


if __name__ == "__main__":
    unittest.main()
