"""All 90 service identities and 270 scenario rows must be explicit and fail closed."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import reference_case_catalog as catalog
import reference_case_dispatch as dispatch


class ReferenceCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        slots = json.loads((ROOT / "spec/api_slots.json").read_text(encoding="utf-8"))["slots"]
        contracts = json.loads((ROOT / "spec/api_contracts.json").read_text(encoding="utf-8"))
        cls.doc = catalog.cases(slots, contracts)
        cls.rows = cls.doc["cases"]

    def test_270_cases_90_services_three_each(self):
        self.assertEqual(len(self.rows), 270)
        ids = {r["api_id"] for r in self.rows}
        self.assertEqual(len(ids), 90)
        for service in ids:
            self.assertEqual({r["category"] for r in self.rows if r["api_id"] == service},
                             set(catalog.TYPES))
        self.assertEqual(len({r["id"] for r in self.rows}), 270)

    def test_no_fake_results(self):
        self.assertTrue(all(r["expected"] is None and r["input_fixture"] is None
                            and r["measurement_status"] == "NOT_MEASURED"
                            and r["execution_status"] == "BLOCKED_ABI" for r in self.rows))
        result = dispatch.plan(self.doc)
        self.assertEqual(len(result), 270)
        self.assertTrue(all(not r["runnable"] and r["state"] == "BLOCKED_ABI" for r in result))

    def test_false_bool_cannot_bypass_budget(self):
        case = dict(self.rows[0], reviewed_abi=True, input_fixture={"a": 0},
                    timeout_instructions=True)
        self.assertEqual(catalog.classify(case), "BLOCKED_CONFIG")

    def test_catalog_missing_entry_rejected(self):
        with self.assertRaises(ValueError):
            dispatch.plan(dict(self.doc, cases=self.rows[:-1]))

    def test_source_attribution(self):
        examples = [r for r in self.rows if r["api_id"] == "slot-03"]
        self.assertEqual(len(examples), 3)
        self.assertTrue(all(r["arguments_hypothesis"] is not None for r in examples))


if __name__ == "__main__":
    unittest.main()
