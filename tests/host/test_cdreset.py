"""Host checks for the Issue #10 CD_RESET contract doc and its fail-closed judge (synthetic records only)."""
import json
import re
import unittest

from tools.cdreset_verify import CANDIDATE_ENTRY, REQUIRED, classify, judge
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

    def test_rom_sha256_must_be_64_lowercase_hex_or_empty(self):
        self.assertEqual(judge(record(rom_sha256="ab" * 32)), "PASS_HYPOTHESIS")
        for bad in ("0" * 63, "0" * 65, "g" * 64, "A" * 64, "0" * 31 + " " + "0" * 32, "name-of-rom",
                    0, None, b"0" * 64):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                judge(record(rom_sha256=bad))

    def test_negative_and_out_of_range_numbers_fail_closed(self):
        bad_values = (dict(entry_pc=-1), dict(entry_pc=0x10000), dict(return_pc=-1),
                      dict(expected_return_pc=0x10000), dict(sp_entry=-1), dict(sp_exit=0x100),
                      dict(bus_after=-1), dict(bus_after=0x100), dict(polls=-1), dict(poll_budget=-1))
        for bad in bad_values:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                judge(record(**bad))

    def test_wrong_types_fail_closed(self):
        bad_values = (dict(entry_pc=True), dict(entry_pc=float(CANDIDATE_ENTRY)), dict(entry_pc="0xE003"),
                      dict(sp_exit=None), dict(bus_after=True), dict(polls="10"), dict(polls=10.0),
                      dict(polls=True), dict(returned="yes"), dict(returned=1), dict(source=None),
                      dict(core_revision=["x"]), dict(scenario=["disc_ready"]))
        for bad in bad_values:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                judge(record(**bad))

    def test_timeout_budget_overrun_and_unreturned_are_distinct(self):
        self.assertEqual(classify(record(returned=False, polls=100)), ("FAIL", "timeout"))
        self.assertEqual(classify(record(returned=False, polls=40)), ("FAIL", "not_returned"))
        self.assertEqual(classify(record(returned=False, polls=101)), ("FAIL", "budget_overrun"))
        self.assertEqual(classify(record(scenario="no_disc", returned=False, polls=100)), ("OBSERVED", "timeout"))
        self.assertEqual(classify(record(scenario="drive_busy", returned=False, polls=40)),
                         ("OBSERVED", "not_returned"))
        self.assertEqual(classify(record(scenario="drive_busy", returned=False, polls=101)),
                         ("FAIL", "budget_overrun"))
        self.assertEqual(classify(record(scenario="no_disc", returned=False, polls=101)),
                         ("FAIL", "budget_overrun"))


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

    def test_hw_bus_reset_is_blocked_in_doc_and_ledger_never_observed(self):
        # Issue #364 / #225 round: hw_bus_reset has no verifiable hardware-bus evidence, so the contract row,
        # the basis line and the checked-in hardware ledger all say BLOCKED (never OBSERVED).
        text = (ROOT / "docs/cd-reset-contract.md").read_text(encoding="utf-8")
        status = re.search(r"^\| `hw_bus_reset` \| (\w+) \|", text, re.M)
        self.assertIsNotNone(status)
        self.assertEqual(status.group(1), "BLOCKED")
        self.assertRegex(text, r"(?m)^- `hw_bus_reset` は BLOCKED である")
        ledger = json.loads((ROOT / "spec/hardware_observations.json").read_text(encoding="utf-8"))
        entries = {e["field"]: e["status"] for e in ledger["entries"]}
        self.assertEqual(entries, {"hw_bus_reset": "BLOCKED"})
        self.assertNotRegex(text, r"`hw_bus_reset` \| OBSERVED")
        self.assertNotRegex(text, r"`hw_bus_reset` is OBSERVED")

    def test_decision_basis_lists_exactly_the_table_statuses(self):
        text = (ROOT / "docs/cd-reset-contract.md").read_text(encoding="utf-8")
        rows = dict(re.findall(r"^\| `(\w+)` \| (VERIFIED|HYPOTHESIS|UNKNOWN) \|", text, re.M))
        m = re.search(r"^- 根拠: 上の契約表で (.+?) が UNKNOWN、(.+?) は caller 側または HLE 由来の HYPOTHESIS のみ",
                      text, re.M)
        self.assertIsNotNone(m)
        names = lambda s: set(re.findall(r"`(\w+)`", s))
        self.assertEqual(names(m.group(1)), {k for k, v in rows.items() if v == "UNKNOWN"})
        self.assertEqual(names(m.group(2)), {k for k, v in rows.items() if v == "HYPOTHESIS"})


if __name__ == "__main__":
    unittest.main()
