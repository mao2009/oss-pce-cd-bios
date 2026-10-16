"""Contract hypotheses and reference run preflight are blocked by default."""
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load(name):
    path = ROOT / "tools" / (name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ReferenceBiosPreflightTests(unittest.TestCase):
    def test_81_source_attributed_worklists(self):
        api = json.loads((ROOT / "spec/api_contracts.json").read_text(encoding="utf-8"))
        r = load("reference_abi_worklist").extract(api)
        self.assertEqual(len(r["apis"]), 81)
        self.assertEqual(len({x["id"] for x in r["apis"]}), 81)
        self.assertTrue(all(x["measurement_status"] == "NOT_MEASURED"
                            and x["test_input_fixture"] is None
                            and x["reference_expectation"] is None for x in r["apis"]))
        self.assertEqual(r["apis"][3]["name"], "CD_READ")

    def test_default_preflight_fails_closed(self):
        m = load("reference_bios_preflight")
        self.assertTrue(m.validate({}))
        good = {
            "schema": m.EXPECTED_SCHEMA,
            "api_id": "slot-1e",
            "bios_path": "rom/private-syscard3.pce",
            "bios_sha256": "a" * 64,
            "test_rom_path": "build/reference-bios/test.pce",
            "test_rom_sha256": "b" * 64,
            "emulator_revision": "pinned-commit",
            "max_instructions": 100000,
            "ram_write_allowlist": [{"start": 8192, "end": 8448}],
            "isolated_run": True,
            "reviewed_abi": True,
            "approved_fixture": True,
        }
        self.assertFalse(m.validate(good))
        for key in ("isolated_run", "reviewed_abi", "approved_fixture"):
            bad = dict(good, **{key: False})
            self.assertTrue(m.validate(bad))
        self.assertTrue(m.validate(dict(good, max_instructions=True)))
        self.assertTrue(m.validate(dict(good, ram_write_allowlist=[])))
        self.assertTrue(m.validate(dict(good, bios_sha256="not-a-hash")))


if __name__ == "__main__":
    unittest.main()
