"""Host checks for the Issue #10 CD_RESET contract doc and its fail-closed judge (synthetic records only)."""
import re
import unittest

from tools.cdreset_verify import CANDIDATE_ENTRY, REQUIRED, judge
from tools.contracts import FIELDS, ROOT, load_contracts
from tools.environment import DEPENDENCIES


def record(**changes):
    base = {"source": "geargrafx-real", "core_revision": DEPENDENCIES["geargrafx"]["revision"],
            "rom_sha256": "00" * 32, "scenario": "disc_ready", "entry_pc": CANDIDATE_ENTRY, "returned": True,
            "return_pc": 0x4003, "expected_return_pc": 0x4003, "sp_entry": 0xFD, "sp_exit": 0xFD,
            "bus_after": 0x00, "polls": 10, "poll_budget": 100}
    base.update(changes)
    return base


class CdResetJudgeTests(unittest.TestCase):
    def test_positive_disc_ready_is_only_a_hypothesis_pass(self):
        self.assertEqual(judge(record()), "PASS_HYPOTHESIS")

    def test_mock_and_unpinned_evidence_never_pass(self):
        self.assertEqual(judge(record(source="mock")), "SKIP")
        self.assertEqual(judge(record(source="hle")), "SKIP")
        self.assertEqual(judge(record(core_revision="deadbeef")), "BLOCKED")
        self.assertEqual(judge(record(rom_sha256="")), "BLOCKED")

    def test_negative_disc_ready_failures(self):
        for bad in (dict(entry_pc=0xE000), dict(returned=False), dict(bus_after=0x80), dict(bus_after=0x08),
                    dict(sp_exit=0xFB), dict(return_pc=0x4002)):
            with self.subTest(bad=bad):
                self.assertEqual(judge(record(**bad)), "FAIL")

    def test_poll_budget_boundary(self):
        self.assertEqual(judge(record(polls=100)), "PASS_HYPOTHESIS")
        self.assertEqual(judge(record(polls=101)), "FAIL")
        self.assertEqual(judge(record(bus_after=0x07)), "PASS_HYPOTHESIS")  # bits outside $F8 are not phase

    def test_unknown_outcome_scenarios_are_observed_not_passed(self):
        for scenario in ("no_disc", "drive_busy"):
            with self.subTest(scenario=scenario):
                self.assertEqual(judge(record(scenario=scenario)), "OBSERVED")
                self.assertEqual(judge(record(scenario=scenario, returned=False, bus_after=0xD8)), "OBSERVED")
                self.assertEqual(judge(record(scenario=scenario, sp_exit=0xFF)), "FAIL")
                self.assertEqual(judge(record(scenario=scenario, polls=101)), "FAIL")

    def test_malformed_records_fail_closed(self):
        for key in REQUIRED:
            broken = record()
            del broken[key]
            with self.subTest(missing=key), self.assertRaises(ValueError):
                judge(broken)
        for bad in (dict(scenario="invalid_track"), dict(poll_budget=0), dict(poll_budget="100")):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                judge(record(**bad))


class CdResetContractDocTests(unittest.TestCase):
    def test_doc_never_promotes_ledger_and_nothing_is_verified(self):
        ledger = load_contracts()["apis"][1]
        self.assertEqual((ledger["candidate_name"], ledger["candidate_entry"]), ("CD_RESET", "0xE003"))
        self.assertEqual((ledger["provenance_status"], ledger["verification"]["status"]), ("PENDING", "NOT_RUN"))
        text = (ROOT / "docs/cd-reset-contract.md").read_text(encoding="utf-8")
        rows = dict(re.findall(r"^\| `(\w+)` \| (VERIFIED|HYPOTHESIS|UNKNOWN) \|", text, re.M))
        self.assertLessEqual(FIELDS, set(rows))
        for field in FIELDS:
            with self.subTest(field=field):
                self.assertEqual(rows[field], ledger["contract"][field]["status"])
        self.assertNotIn("VERIFIED", rows.values())


if __name__ == "__main__":
    unittest.main()
