#!/usr/bin/env python3
"""Host tests for the Phase ABI-1 reference review ledger.

The ledger is a *review* artefact: it records what a local static audit of the
private reference BIOS established and what it did not. These tests keep it
from silently over-claiming, and they check that the published text can be
committed without shipping reference code.
"""
import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import abi1_reference_review as review  # noqa: E402


CONTRACTS = json.loads((ROOT / "spec" / "api_contracts.json").read_text(encoding="utf-8"))


def committed():
    return json.loads((ROOT / "spec" / "abi1_reference_review.json").read_text(encoding="utf-8"))


class CommittedLedgerTests(unittest.TestCase):
    def test_committed_ledger_validates(self):
        document = review.load()
        self.assertEqual(document["schema"], review.SCHEMA)

    def test_summary_counts_the_reviewed_apis(self):
        summary = review.summary(committed())
        self.assertEqual(summary["reviewed_apis"], 7)
        self.assertEqual(summary["execution"], "NOT_RUN")
        self.assertIs(summary["release_eligible"], False)
        self.assertEqual(summary["trust"], "LOCAL_ONLY_NOT_VERIFIED")

    def test_every_reviewed_slot_is_a_priority_api(self):
        reviewed = {api["api_id"] for api in committed()["apis"]}
        self.assertEqual(reviewed, {"slot-1e", "slot-3c", "slot-3d", "slot-3f",
                                    "slot-40", "slot-41", "slot-42"})

    def test_no_ledger_may_claim_verified(self):
        for api in committed()["apis"]:
            self.assertNotEqual(api["abi_state"], "VERIFIED")

    def test_execution_is_not_run_for_every_reviewed_api(self):
        self.assertEqual(committed()["method"]["execution"], "NOT_RUN")
        for api in committed()["apis"]:
            self.assertNotEqual(api["execution"]["observation_status"], "OBSERVED_LOCAL_ONLY")

    def test_blocked_apis_keep_their_blocker(self):
        for api in committed()["apis"]:
            if api["abi_state"] in ("NOT_RUN", "BLOCKED", "PARTIAL"):
                self.assertTrue(api["execution"]["verdict"].startswith("BLOCKED")
                                or api["execution"]["verdict"] == "NOT_RUN",
                                api["api_id"])


class NegativeTests(unittest.TestCase):
    """Mutants of the committed ledger must be rejected, not softened."""

    def setUp(self):
        self.document = committed()
        self.contracts = CONTRACTS

    def assertRejected(self, document, fragment):
        with self.assertRaises(review.Invalid) as caught:
            review.validate(document, self.contracts)
        self.assertIn(fragment, str(caught.exception))

    def test_verified_claim_is_rejected(self):
        document = copy.deepcopy(self.document)
        document["apis"][0]["abi_state"] = "VERIFIED"
        self.assertRejected(document, "VERIFIED")

    def test_release_eligible_cannot_be_set(self):
        document = copy.deepcopy(self.document)
        document["release_eligible"] = True
        self.assertRejected(document, "release_eligible")

    def test_execution_may_not_claim_a_run(self):
        document = copy.deepcopy(self.document)
        document["method"]["execution"] = "EXECUTED"
        self.assertRejected(document, "execution")

    def test_entry_address_must_match_the_claim_ledger(self):
        document = copy.deepcopy(self.document)
        document["apis"][0]["candidate_entry"] = "0xE000"
        self.assertRejected(document, "candidate entry")

    def test_name_must_match_the_claim_ledger(self):
        document = copy.deepcopy(self.document)
        document["apis"][0]["name"] = "SOMETHING_ELSE"
        self.assertRejected(document, "name differs")

    def test_api_id_must_match_the_slot(self):
        document = copy.deepcopy(self.document)
        document["apis"][0]["api_id"] = "slot-2f"
        self.assertRejected(document, "api_id does not match")

    def test_missing_static_item_is_rejected(self):
        document = copy.deepcopy(self.document)
        del document["apis"][0]["static_items"]["stack_usage"]
        self.assertRejected(document, "static item keys")

    def test_unknown_static_item_status_is_rejected(self):
        document = copy.deepcopy(self.document)
        document["apis"][0]["static_items"]["stack_usage"]["status"] = "PROBABLY_FINE"
        self.assertRejected(document, "static item stack_usage status")

    def test_empty_static_item_value_is_rejected(self):
        document = copy.deepcopy(self.document)
        document["apis"][0]["static_items"]["mpr_change"]["value"] = "  "
        self.assertRejected(document, "static item mpr_change value")

    def test_unknown_safety_verdict_is_rejected(self):
        document = copy.deepcopy(self.document)
        document["apis"][0]["safety"]["verdict"] = "PROBABLY_SAFE"
        self.assertRejected(document, "safety verdict")

    def test_unknown_observation_status_is_rejected(self):
        document = copy.deepcopy(self.document)
        document["apis"][0]["execution"]["observation_status"] = "DEFINITELY_OBSERVED"
        self.assertRejected(document, "observation status")

    def test_observed_state_needs_an_observation(self):
        document = copy.deepcopy(self.document)
        document["apis"][1]["abi_state"] = "OBSERVED"
        self.assertRejected(document, "OBSERVED needs a recorded observation")

    def test_unobserved_slot_may_not_claim_observed(self):
        document = copy.deepcopy(self.document)
        document["apis"][0]["execution"]["observation_status"] = "NOT_RUN"
        self.assertRejected(document, "must not cite prior evidence")

    def test_budget_exhaustion_is_rejected(self):
        document = copy.deepcopy(self.document)
        document["apis"][0]["static"]["budget_exhausted"] = True
        self.assertRejected(document, "budget")

    def test_unknown_hazard_name_is_rejected(self):
        document = copy.deepcopy(self.document)
        document["apis"][0]["static"]["hazard_counts"]["MYSTERY"] = 1
        self.assertRejected(document, "hazard names")

    def test_decoded_instruction_in_published_text_is_rejected(self):
        document = copy.deepcopy(self.document)
        document["apis"][0]["static_items"]["expected_return_value"]["value"] = "stz $42"
        self.assertRejected(document, "decoded instruction")

    def test_a_lower_case_decoded_instruction_is_rejected(self):
        document = copy.deepcopy(self.document)
        document["apis"][0]["static_items"]["expected_return_value"]["value"] = "lda #$0a"
        self.assertRejected(document, "decoded instruction")

    def test_internal_address_in_published_text_is_rejected(self):
        document = copy.deepcopy(self.document)
        document["apis"][0]["static_items"]["expected_return_value"]["value"] = "read $CD10"
        self.assertRejected(document, "non public address")

    def test_missing_execution_blocker_is_rejected(self):
        document = copy.deepcopy(self.document)
        document["method"]["execution_blockers"] = []
        self.assertRejected(document, "execution_blockers")

    def test_an_unresolved_profile_may_not_be_safe(self):
        document = copy.deepcopy(self.document)
        document["apis"][0]["static"]["unresolved"] = ["page_bank_unknown"]
        self.assertRejected(document, "cannot be EXECUTION_SAFE")

    def test_a_non_returning_path_may_not_be_safe(self):
        document = copy.deepcopy(self.document)
        document["apis"][0]["static"]["non_returning_states"] = 1
        self.assertRejected(document, "cannot be EXECUTION_SAFE")

    def test_a_runtime_mpr_switch_forces_the_blocked_verdict(self):
        document = copy.deepcopy(self.document)
        document["apis"][0]["static"]["hazard_counts"]["MPR_WRITE"] = 1
        self.assertRejected(document, "forces EXECUTION_BLOCKED")

    def test_a_block_transfer_forces_the_blocked_verdict(self):
        document = copy.deepcopy(self.document)
        document["apis"][0]["static"]["hazard_counts"]["BLOCK_TRANSFER"] = 1
        self.assertRejected(document, "forces EXECUTION_BLOCKED")

    def test_a_fresh_execution_claim_is_rejected(self):
        document = copy.deepcopy(self.document)
        document["apis"][1]["abi_state"] = "OBSERVED"
        document["apis"][1]["execution"]["observation_status"] = "OBSERVED_LOCAL_ONLY"
        document["apis"][1]["execution"]["verdict"] = "OBSERVED_LOCAL_ONLY"
        self.assertRejected(document, "execution verdict vocabulary")

    def test_a_prior_observation_has_to_name_its_source(self):
        document = copy.deepcopy(self.document)
        document["apis"][0]["execution"]["prior_evidence"] = "NONE"
        self.assertRejected(document, "has to name its source")

    def test_a_prior_observation_source_has_to_exist(self):
        document = copy.deepcopy(self.document)
        document["apis"][0]["execution"]["prior_evidence"] = "docs/not-a-real-file.md"
        self.assertRejected(document, "not a repository file")

    def test_a_slot_without_an_observation_may_not_cite_prior_evidence(self):
        document = copy.deepcopy(self.document)
        document["apis"][1]["execution"]["prior_evidence"] = "docs/reference-bios-test-plan.md"
        self.assertRejected(document, "must not cite prior evidence")

    def test_unknown_top_level_key_is_rejected(self):
        document = copy.deepcopy(self.document)
        document["extra"] = 1
        self.assertRejected(document, "review keys")


if __name__ == "__main__":
    unittest.main()
