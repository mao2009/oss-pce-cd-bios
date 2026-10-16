"""Issue #12: CD_READ contract ledger + transport outcome model (host-only; real core is authoritative)."""
import copy
import json
import re
import unittest

from tools.cd_read_contract import (
    INTEGRATION, LBA_LIMIT, PINNED, ROOT, SECTOR_BYTES, TRANSPORT_CLAIMS, effective_status, real_core_requests,
    transport_outcome, validate,
)
from tools.make_cd_image import DEFAULT_SECTORS

ROM_SHA = "ab" * 32


def bundle_for(rom_sha256=ROM_SHA, **overrides):
    """A bundle as tests/integration/geargrafx_cdread.py writes it after a full PASS run."""
    names = {s for c in TRANSPORT_CLAIMS if c["status"] == "VERIFIED" for s in c["scenarios"]}
    bundle = {"status": "PASS", "geargrafx_revision": PINNED["revision"], "source_archive_sha256": PINNED["sha256"],
              "rom_sha256": rom_sha256, "tests": {n: {"status": "PASS"} for n in names}}
    bundle.update(overrides)
    return bundle


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

    def test_parser_sees_every_real_core_call_site(self):
        text = INTEGRATION.read_text(encoding="utf-8")
        positive_calls = len(re.findall(r"suite\.positive\(", text))
        end_of_disc_calls = sum("suite.failing_guest(" in line and ", 5, 5," in line for line in text.splitlines())
        self.assertEqual(len(real_core_requests(text)), positive_calls + end_of_disc_calls)


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.api = json.loads((ROOT / "spec/api_contracts.json").read_text(encoding="utf-8"))

    def test_structure_and_callee_abi_are_unpromoted(self):
        validate(self.api)

    def test_source_text_and_missing_bundle_never_verify(self):
        status = effective_status(None, None)
        self.assertEqual([status[f"T{i}"] for i in range(1, 6)], ["BLOCKED"] * 5)
        self.assertEqual((status["T6"], status["T7"], status["T8"]), ("HYPOTHESIS", "UNKNOWN", "UNKNOWN"))

    def test_matching_passing_bundle_verifies_transport_only(self):
        status = effective_status(bundle_for(), ROM_SHA)
        self.assertEqual([status[f"T{i}"] for i in range(1, 6)], ["VERIFIED"] * 5)
        self.assertEqual((status["T6"], status["T7"], status["T8"]), ("HYPOTHESIS", "UNKNOWN", "UNKNOWN"))

    def test_bundle_from_other_rom_emulator_or_run_is_refused(self):
        cases = {
            "other_rom": (bundle_for(rom_sha256="cd" * 32), ROM_SHA),
            "rom_missing": (bundle_for(), None),
            "other_revision": (bundle_for(geargrafx_revision="0" * 40), ROM_SHA),
            "other_archive": (bundle_for(source_archive_sha256="0" * 64), ROM_SHA),
            "failed_run": (bundle_for(status="FAIL"), ROM_SHA),
            "no_bundle": (None, ROM_SHA),
        }
        for name, (bundle, rom) in cases.items():
            with self.subTest(case=name):
                self.assertEqual(effective_status(bundle, rom)["T1"], "BLOCKED")

    def test_cited_scenario_missing_or_failing_downgrades_to_unknown(self):
        missing = bundle_for()
        del missing["tests"]["single_sector_lba5"]
        failing = bundle_for()
        failing["tests"]["invalid_sector_lba16"] = {"status": "FAIL"}
        self.assertEqual(effective_status(missing, ROM_SHA)["T1"], "UNKNOWN")
        self.assertEqual(effective_status(failing, ROM_SHA)["T3"], "UNKNOWN")

    def test_asserted_observation_counts_but_not_started_or_untested_does_not(self):
        observed = bundle_for()
        observed["tests"]["read6_on_audio_track_lba"] = {"status": "OBSERVED_EMULATOR_BEHAVIOUR"}
        self.assertEqual(effective_status(observed, ROM_SHA)["T5"], "VERIFIED")
        untested = bundle_for()
        untested["tests"]["read6_on_audio_track_lba"] = {"status": "NOT_TESTED"}
        self.assertEqual(effective_status(untested, ROM_SHA)["T5"], "UNKNOWN")

    def test_claim_citing_unrun_scenario_is_not_verified(self):
        claims = copy.deepcopy(TRANSPORT_CLAIMS)
        claims[0]["scenarios"] = ["no_such_run"]
        validate(self.api, claims)
        self.assertEqual(effective_status(bundle_for(), ROM_SHA, claims)["T1"], "UNKNOWN")

    def test_malformed_or_foreign_promotions_are_rejected(self):
        for change in ("unknown_to_verified", "bad_status", "foreign_evidence"):
            with self.subTest(change=change):
                c = copy.deepcopy(TRANSPORT_CLAIMS)
                if change == "unknown_to_verified": c[6]["status"] = "VERIFIED"
                if change == "bad_status": c[0]["status"] = "PASS"
                if change == "foreign_evidence": c[0]["evidence"] = "hugo bios.c HLE"
                with self.assertRaises(ValueError):
                    validate(self.api, c)

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
                    validate(api)


if __name__ == "__main__":
    unittest.main()
