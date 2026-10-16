"""Issue #12: CD_READ contract ledger + transport outcome model (host-only; real core is authoritative)."""
import contextlib
import copy
import hashlib
import io
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import tools.cd_read_contract as cd_read_contract

from tools.cd_read_contract import (
    COMPAT_REASON, EXIT_BLOCKED, EXIT_LOCAL_ONLY, INTEGRATION, LBA_LIMIT, PINNED, ROOT, SECTOR_BYTES, TRANSPORT_CLAIMS, TRUST_LABEL,
    compat_status, effective_status, real_core_requests, transport_outcome, validate,
)
from tools.make_cd_image import DEFAULT_SECTORS

ROM_SHA = "ab" * 32


# The expected result kind per cited scenario, written independently of tools/cd_read_contract.py.
# Positive reads must be PASS; deliberate bad requests must be PASS_EXPECTED_FAILURE; the CD-DA read is an
# observation. test_scenario_kinds_match_checker and test_scenario_kinds_match_integration_source tie these
# to the checker and to the integration suite.
EXPECTED_KIND = {
    "single_sector_lba5": "PASS", "first_sector_lba0": "PASS", "last_sector_lba15": "PASS",
    "three_sectors_lba5": "PASS", "disc_end_boundary_lba13_x3": "PASS",
    "wrong_sector_requested_lba6": "PASS_EXPECTED_FAILURE",
    "invalid_sector_lba16": "PASS_EXPECTED_FAILURE", "invalid_sector_lba_max": "PASS_EXPECTED_FAILURE",
    "read_past_disc_end_lba14_x3": "PASS_EXPECTED_FAILURE",
    "read6_on_audio_track_lba": "OBSERVED_EMULATOR_BEHAVIOUR",
}


def bundle_for(rom_sha256=ROM_SHA, **overrides):
    """A bundle as tests/integration/geargrafx_cdread.py writes it after a full PASS run."""
    names = {s for c in TRANSPORT_CLAIMS if c["status"] == "VERIFIED" for s in c["scenarios"]}
    bundle = {"status": "PASS", "geargrafx_revision": PINNED["revision"], "source_archive_sha256": PINNED["sha256"],
              "rom_sha256": rom_sha256, "tests": {n: {"status": EXPECTED_KIND[n]} for n in names}}
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

    def test_matching_bundle_is_local_only_never_verified(self):
        status = effective_status(bundle_for(), ROM_SHA)
        self.assertEqual([status[f"T{i}"] for i in range(1, 6)], ["LOCAL_ONLY"] * 5)
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
        self.assertEqual(effective_status(observed, ROM_SHA)["T5"], "LOCAL_ONLY")
        untested = bundle_for()
        untested["tests"]["read6_on_audio_track_lba"] = {"status": "NOT_TESTED"}
        self.assertEqual(effective_status(untested, ROM_SHA)["T5"], "UNKNOWN")

    def test_scenario_recorded_with_wrong_kind_is_not_verified(self):
        # A positive read rewritten as an expected failure, a deliberate bad request recorded as a plain PASS,
        # and a CD-DA observation recorded as a plain PASS must each leave their claim unverified.
        cases = {
            "positive_as_expected_failure": ("single_sector_lba5", "PASS_EXPECTED_FAILURE", "T1"),
            "negative_as_plain_pass": ("wrong_sector_requested_lba6", "PASS", "T1"),
            "bad_request_as_plain_pass": ("invalid_sector_lba16", "PASS", "T3"),
            "observation_as_plain_pass": ("read6_on_audio_track_lba", "PASS", "T5"),
        }
        for name, (scenario, kind, claim) in cases.items():
            with self.subTest(case=name):
                bundle = bundle_for()
                bundle["tests"][scenario] = {"status": kind}
                self.assertEqual(effective_status(bundle, ROM_SHA)[claim], "UNKNOWN")

    def test_scenario_kinds_match_checker(self):
        from tools.cd_read_contract import SCENARIO_KIND
        self.assertEqual(dict(SCENARIO_KIND), EXPECTED_KIND)
        for claim in TRANSPORT_CLAIMS:
            if claim["status"] == "VERIFIED":
                self.assertTrue(set(claim["scenarios"]) <= set(EXPECTED_KIND), claim["id"])

    def test_scenario_kinds_match_integration_source(self):
        # The integration suite decides each scenario's kind; the table must agree with its call sites.
        text = INTEGRATION.read_text(encoding="utf-8")
        declared = {}
        for call, name in re.findall(r'suite\.(positive|failing_guest|must_be_rejected|tampered_disc)\("(\w+)"', text):
            declared[name] = "PASS" if call == "positive" else "PASS_EXPECTED_FAILURE"
        declared.update(re.findall(r'results\["(\w+)"\] = \{\s*"status": "(\w+)"', text))
        for name, kind in EXPECTED_KIND.items():
            self.assertEqual(declared.get(name), kind, name)

    def test_malformed_bundle_is_blocked_not_an_exception(self):
        cases = {
            "test_entry_string": bundle_for(tests={"single_sector_lba5": "PASS"}),
            "test_entry_list": bundle_for(tests={"single_sector_lba5": ["PASS"]}),
            "bundle_list": [bundle_for()],
            "bundle_string": "PASS",
        }
        for name, bundle in cases.items():
            with self.subTest(case=name):
                self.assertEqual(effective_status(bundle, ROM_SHA)["T1"], "BLOCKED")

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


class TrustBoundaryTests(unittest.TestCase):
    """The bundle is self-declared JSON. A local check reaches LOCAL_ONLY at best; a compatibility gate stays BLOCKED."""

    def test_valid_self_declared_bundle_never_passes_compat_gate(self):
        status = compat_status(bundle_for(), ROM_SHA)
        self.assertEqual([status[f"T{i}"] for i in range(1, 6)], ["BLOCKED"] * 5)
        self.assertEqual((status["T6"], status["T7"], status["T8"]), ("HYPOTHESIS", "UNKNOWN", "UNKNOWN"))

    def test_forged_bundle_with_pinned_hashes_is_local_only(self):
        # A hand-written tests map that copies the pinned revision and the ROM hash is consistent locally
        # (that is the documented limit): it is LOCAL_ONLY, never VERIFIED/PASS, and BLOCKED for compatibility.
        forged = bundle_for()
        self.assertEqual(set(effective_status(forged, ROM_SHA).values()) & {"VERIFIED", "PASS"}, set())
        self.assertEqual(effective_status(forged, ROM_SHA)["T1"], "LOCAL_ONLY")
        self.assertEqual(compat_status(forged, ROM_SHA)["T1"], "BLOCKED")
        self.assertEqual(TRUST_LABEL, "LOCAL_ONLY")
        self.assertIn("SELF-DECLARED", COMPAT_REASON)
        self.assertIn("LOCAL_ONLY", COMPAT_REASON)

    def test_compat_gate_never_returns_verified_or_pass(self):
        for bundle, rom in ((None, None), (bundle_for(), ROM_SHA), (bundle_for(), None)):
            with self.subTest(bundle=bundle is not None, rom=rom is not None):
                self.assertNotIn("VERIFIED", compat_status(bundle, rom).values())

    def run_cli(self, bundle):
        # Run main() against a temporary ROOT holding a ROM and a forged bundle with the correct ROM hash.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "spec").mkdir()
            shutil.copy(ROOT / "spec/api_contracts.json", root / "spec/api_contracts.json")
            out = root / "build/debug/cdread-probe"
            out.mkdir(parents=True)
            rom = b"forged rom"
            (out / "cdread-probe-not-bios.pce").write_bytes(rom)
            if bundle is not None:
                bundle = bundle(hashlib.sha256(rom).hexdigest())
                (out / "cdread-evidence.json").write_text(json.dumps(bundle), encoding="utf-8")
            stdout = io.StringIO()
            with mock.patch.object(cd_read_contract, "ROOT", root), contextlib.redirect_stdout(stdout),                     self.assertRaises(SystemExit) as exit_:
                cd_read_contract.main([])
            return exit_.exception.code, stdout.getvalue()

    def test_cli_forged_bundle_is_not_pass_and_exits_nonzero(self):
        code, text = self.run_cli(lambda sha: bundle_for(rom_sha256=sha))
        self.assertEqual(code, EXIT_LOCAL_ONLY)
        self.assertNotEqual(code, 0)
        self.assertTrue(text.startswith("LOCAL_ONLY "), text)
        self.assertNotIn("PASS ", text)
        self.assertNotIn("VERIFIED'", text)

    def test_cli_malformed_tests_entry_is_blocked(self):
        code, text = self.run_cli(lambda sha: bundle_for(rom_sha256=sha, tests={"single_sector_lba5": "PASS"}))
        self.assertEqual(code, EXIT_BLOCKED)
        self.assertTrue(text.startswith("BLOCKED "), text)

    def test_blocked_exit_code_is_fixed_and_matches_harness_119(self):
        # #119 tests/integration/geargrafx_harness.py: EXIT = {"PASS": 0, "FAIL": 1, "BLOCKED": 3, "SKIP": 4}
        self.assertEqual(EXIT_BLOCKED, 3)

    def test_end_of_disc_runs_use_premature_status_code_of_115(self):
        # #115 tools/cd_status.py CODES: 5 -> PREMATURE_STATUS. Every end-of-disc failing_guest call uses code 5.
        text = INTEGRATION.read_text(encoding="utf-8")
        codes = set(re.findall(r'suite\.failing_guest\("\w+", suite\.variant\([^)]*\), \w+, \w+, (\d+), 5,', text))
        self.assertEqual(codes, {"5"})
        self.assertTrue(TRUST_LABEL.startswith("LOCAL_ONLY"))  # #119 artifact_policy and #116 trust both say local-only


if __name__ == "__main__":
    unittest.main()
