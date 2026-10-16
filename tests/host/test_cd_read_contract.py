"""Issue #12: CD_READ contract ledger + transport outcome model (host-only; real core is authoritative)."""
import copy
import json
import unittest

from tools.cd_read_contract import (
    INTEGRATION, LBA_LIMIT, ROOT, SECTOR_BYTES, TRANSPORT_CLAIMS, real_core_requests, transport_outcome, validate,
)
from tools.make_cd_image import DEFAULT_SECTORS


class TransportOutcomeTests(unittest.TestCase):
    def test_positive_full_delivery(self):
        for lba, count in ((5, 1), (0, 1), (15, 1), (5, 3), (13, 3)):
            self.assertEqual(transport_outcome(16, lba, count), (count, False), (lba, count))

    def test_negative_start_at_or_beyond_disc_end(self):
        for lba in (16, 17, LBA_LIMIT - 1):
            self.assertEqual(transport_outcome(16, lba, 1), (0, True), lba)

    def test_boundary_run_off_end(self):
        self.assertEqual(transport_outcome(16, 14, 3), (2, True))
        self.assertEqual(transport_outcome(16, 15, 2), (1, True))
        self.assertEqual(transport_outcome(1, 0, 1), (1, False))

    def test_outside_executed_domain_is_refused_not_guessed(self):
        for disc, lba, count in ((0, 0, 1), (16, -1, 1), (16, LBA_LIMIT, 1), (16, 5, 0), (16, 5, 4), (16, 5, 256)):
            with self.subTest(disc=disc, lba=lba, count=count), self.assertRaises(ValueError):
                transport_outcome(disc, lba, count)

    def test_model_matches_every_real_core_request(self):
        rows = real_core_requests(INTEGRATION.read_text(encoding="utf-8"))
        names = {r[0] for r in rows}
        self.assertTrue({"single_sector_lba5", "disc_end_boundary_lba13_x3", "invalid_sector_lba16",
                         "invalid_sector_lba_max", "read_past_disc_end_lba14_x3"} <= names, names)
        for name, lba, count, transferred in rows:
            delivered, early = transport_outcome(DEFAULT_SECTORS, lba, count)
            if transferred is None:
                self.assertEqual((delivered, early), (count, False), name)
            else:
                self.assertEqual((delivered * SECTOR_BYTES, early), (transferred, True), name)


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.api = json.loads((ROOT / "spec/api_contracts.json").read_text(encoding="utf-8"))
        self.text = INTEGRATION.read_text(encoding="utf-8")

    def test_current_ledger_is_consistent(self):
        validate(self.api, self.text)
        self.assertEqual({c["status"] for c in TRANSPORT_CLAIMS}, {"VERIFIED", "HYPOTHESIS", "UNKNOWN"})

    def test_promotions_without_evidence_are_rejected(self):
        claims = copy.deepcopy(TRANSPORT_CLAIMS)
        for change in ("unknown_to_verified", "missing_scenario", "bad_status", "foreign_evidence"):
            with self.subTest(change=change):
                c = copy.deepcopy(claims)
                if change == "unknown_to_verified": c[6]["status"] = "VERIFIED"
                if change == "missing_scenario": c[0]["scenarios"] = ["no_such_run"]
                if change == "bad_status": c[0]["status"] = "PASS"
                if change == "foreign_evidence": c[0]["evidence"] = "hugo bios.c HLE"
                with self.assertRaises(ValueError):
                    validate(self.api, self.text, c)

    def test_callee_abi_cannot_be_verified_or_implemented_silently(self):
        for change in ("verified_field", "executed", "implemented", "renamed"):
            with self.subTest(change=change):
                api = copy.deepcopy(self.api)
                slot = api["apis"][3]
                if change == "verified_field": slot["contract"]["arguments"]["status"] = "VERIFIED"
                if change == "executed": slot["verification"]["executed_callee"] = True
                if change == "implemented": slot["implementation_status"] = "DONE"
                if change == "renamed": slot["candidate_name"] = "CD_SEEK"
                with self.assertRaises(ValueError):
                    validate(api, self.text)


if __name__ == "__main__":
    unittest.main()
