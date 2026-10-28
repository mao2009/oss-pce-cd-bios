#!/usr/bin/env python3
"""Host tests for the Phase ABI-1 reference review ledger.

The ledger is a *review* artefact. It records what a local static audit of the
private reference BIOS established and what it did not. These tests keep it
from silently over-claiming, and they check that the published text can be
committed without shipping reference code.

The two axes are kept apart on purpose:

* the **static review** axis, which an observation of the private image can
  reach;
* the **verification** axis (``OBSERVED``/``PARTIAL``/``VERIFIED``), which only
  a *reproducible* real core observation can reach.

A static ``EXECUTION_SAFE`` verdict is never an execution permission.
"""
import copy
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import abi1_reference_review as review  # noqa: E402
from environment import dependency_dir  # noqa: E402


CONTRACTS = json.loads((ROOT / "spec" / "api_contracts.json").read_text(encoding="utf-8"))
DEV_LEDGER = json.loads((ROOT / "spec" / "dev_implementations.json").read_text(encoding="utf-8"))


def committed():
    return json.loads((ROOT / "spec" / "abi1_reference_review.json").read_text(encoding="utf-8"))


class CommittedLedgerTests(unittest.TestCase):
    def test_committed_ledger_validates(self):
        document, contracts, dev_ledger = review.load()
        self.assertEqual(document["schema"], review.SCHEMA)

    def test_summary_counts_are_derived_from_the_ledger(self):
        document, contracts, dev_ledger = review.load()
        metrics = review.summary(document, contracts, dev_ledger)["metrics"]
        self.assertEqual(metrics["static_review_apis"], 7)
        self.assertEqual(metrics["prior_local_only_observations"], 1)
        self.assertEqual(metrics["reproducible_real_core_observations"], 0)
        self.assertEqual(metrics["reference_abi_verified"], 0)
        self.assertEqual(metrics["dev_rom_implemented_apis"], 13)
        self.assertEqual(metrics["owner_approved_dev_callable"], 0)

    def test_the_two_axes_are_counted_apart(self):
        document = committed()
        static = sum(1 for api in document["apis"] if api["static"]["status"] == "OBSERVED_STATIC")
        reproducible = sum(1 for api in document["apis"]
                           if api["observation"]["reproducible_real_core"] == "OBSERVED")
        self.assertEqual(static, 7)
        self.assertEqual(reproducible, 0)
        # A static review of seven APIs is not a partial verification of seven APIs.
        self.assertEqual(sum(1 for api in document["apis"]
                             if api["abi_state"] in ("OBSERVED", "PARTIAL", "VERIFIED")), 0)

    def test_every_reviewed_slot_is_a_priority_api(self):
        reviewed = [api["api_id"] for api in committed()["apis"]]
        self.assertEqual(reviewed, list(review.PRIORITY_API_IDS))

    def test_no_ledger_may_claim_verified(self):
        for api in committed()["apis"]:
            self.assertNotEqual(api["abi_state"], "VERIFIED")

    def test_execution_is_not_run_for_every_reviewed_api(self):
        self.assertEqual(committed()["method"]["execution"], "NOT_RUN")
        for api in committed()["apis"]:
            self.assertEqual(api["observation"]["reproducible_real_core"], "NOT_RUN")
            self.assertEqual(api["safety"]["execution_permission"], "BLOCKED")

    def test_the_verification_states_are_documented(self):
        states = committed()["method"]["verification_states"]
        self.assertEqual(set(states), review.ABI_STATES)
        for name, text in states.items():
            self.assertTrue(text.strip(), name)


class OpcodeTableTests(unittest.TestCase):
    """The published-text guard must cover every operand carrying opcode.

    The list is compared against the pinned cc65 HuC6280 opcode table, so the
    claim in the code comment and in the report is machine checked instead of
    asserted. The table lives in the pinned cc65 source tree, which is outside
    the repository; the test is skipped when that tree is not present.
    """

    def test_every_operand_carrying_opcode_is_guarded(self):
        table = dependency_dir("cc65") / "src" / "da65" / "opchuc6280.c"
        if not table.is_file():
            self.skipTest(f"pinned cc65 opcode table not available: {table}")
        rows = re.findall(r'\{\s*"([^"]*)"\s*,\s*(\d+)\s*,', table.read_text(encoding="utf-8"))
        self.assertGreater(len(rows), 200)
        relative = {"bra", "bcc", "bcs", "beq", "bne", "bmi", "bpl", "bvc", "bvs",
                    "bsr", "bbr0", "bbr1", "bbr2", "bbr3", "bbr4", "bbr5", "bbr6",
                    "bbr7", "bbs0", "bbs1", "bbs2", "bbs3", "bbs4", "bbs5", "bbs6",
                    "bbs7"}
        for mnemonic, size in rows:
            if not mnemonic or mnemonic in relative:
                continue
            if int(size) < 2:
                continue
            self.assertIn(mnemonic.upper(), review.FORBIDDEN_MNEMONICS,
                          f"{mnemonic} can carry an operand but is not guarded")


class NegativeTests(unittest.TestCase):
    """Mutants of the committed ledger must be rejected, not softened."""

    def setUp(self):
        self.document = committed()
        self.contracts = CONTRACTS
        self.dev_ledger = DEV_LEDGER

    def assertRejected(self, document, fragment):
        with self.assertRaises(review.Invalid) as caught:
            review.validate(document, self.contracts, self.dev_ledger)
        self.assertIn(fragment, str(caught.exception))

    # -- axis separation ------------------------------------------------------
    def test_a_static_review_cannot_be_counted_as_a_dynamic_observation(self):
        # A reproducible observation cannot be claimed on the strength of a
        # static review: the state would have to follow it, and the static
        # profile cannot produce one.
        document = copy.deepcopy(self.document)
        document["apis"][1]["observation"]["reproducible_real_core"] = "OBSERVED"
        self.assertRejected(document, "a reproducible observation cannot stay in NOT_RUN")

    def test_a_static_review_cannot_be_counted_as_a_partial_verification(self):
        document = copy.deepcopy(self.document)
        document["apis"][2]["abi_state"] = "PARTIAL"
        self.assertRejected(document, "needs a reproducible real core observation")

    def test_a_past_local_observation_is_not_a_reproducible_measurement(self):
        # The prior local only observation of slot $1E must not be counted as a
        # reproducible measurement: a static profile and a past local run are
        # not a reproducible observation.
        document = copy.deepcopy(self.document)
        document["apis"][0]["observation"]["reproducible_real_core"] = "OBSERVED"
        self.assertRejected(document, "a reproducible observation cannot stay in NOT_RUN")

    def test_a_past_local_observation_does_not_promote_the_abi_state(self):
        document = copy.deepcopy(self.document)
        document["apis"][0]["abi_state"] = "OBSERVED"
        self.assertRejected(document, "needs a reproducible real core observation")

    def test_a_reproducible_observation_cannot_stay_not_run(self):
        document = copy.deepcopy(self.document)
        document["apis"][1]["observation"]["reproducible_real_core"] = "OBSERVED"
        document["apis"][1]["abi_state"] = "NOT_RUN"
        self.assertRejected(document, "a reproducible observation cannot stay in NOT_RUN")

    def test_verified_without_evidence_is_rejected(self):
        document = copy.deepcopy(self.document)
        document["apis"][0]["abi_state"] = "VERIFIED"
        self.assertRejected(document, "VERIFIED")

    def test_a_duplicate_api_is_rejected(self):
        document = copy.deepcopy(self.document)
        document["apis"].append(copy.deepcopy(document["apis"][0]))
        self.assertRejected(document, "duplicate api_id")

    def test_an_api_outside_the_phase_scope_is_rejected(self):
        document = copy.deepcopy(self.document)
        document["apis"][1]["api_id"] = "slot-3e"
        document["apis"][1]["slot"] = 0x3E
        self.assertRejected(document, "outside the Phase ABI-1 scope")

    def test_a_removed_api_is_rejected(self):
        document = copy.deepcopy(self.document)
        document["apis"] = document["apis"][:-1]
        self.assertRejected(document, "not exactly the Phase ABI-1 scope")

    def test_a_hand_written_aggregate_that_does_not_match_is_rejected(self):
        document = copy.deepcopy(self.document)
        document["summary"]["static_review_apis"] = 6
        self.assertRejected(document, "summary.static_review_apis")

    def test_an_aggregate_that_counts_a_prior_observation_as_reproducible_is_rejected(self):
        document = copy.deepcopy(self.document)
        document["summary"]["reproducible_real_core_observations"] = 1
        self.assertRejected(document, "summary.reproducible_real_core_observations")

    def test_an_aggregate_that_counts_a_static_review_as_a_verification_is_rejected(self):
        document = copy.deepcopy(self.document)
        document["summary"]["reference_abi_verified"] = 1
        self.assertRejected(document, "summary.reference_abi_verified")

    def test_an_aggregate_that_miscounts_the_dev_rom_is_rejected(self):
        document = copy.deepcopy(self.document)
        document["summary"]["dev_rom_implemented_apis"] = 6
        self.assertRejected(document, "summary.dev_rom_implemented_apis")

    def test_an_aggregate_that_miscounts_the_dev_callable_is_rejected(self):
        document = copy.deepcopy(self.document)
        document["summary"]["owner_approved_dev_callable"] = 1
        self.assertRejected(document, "summary.owner_approved_dev_callable")

    # -- static safety versus execution permission -----------------------------
    def test_a_static_safe_verdict_cannot_become_a_permission(self):
        document = copy.deepcopy(self.document)
        document["apis"][0]["safety"]["execution_permission"] = "ALLOWED"
        self.assertRejected(document, "execution permission of a static review is always BLOCKED")

    def test_the_zero_page_verdict_cannot_become_a_permission_either(self):
        document = copy.deepcopy(self.document)
        document["apis"][1]["safety"]["static_verdict"] = \
            "EXECUTION_SAFE_WITH_OBSERVABLE_ZERO_PAGE_WRITE"
        document["apis"][1]["safety"]["execution_permission"] = "ALLOWED"
        self.assertRejected(document, "execution permission of a static review is always BLOCKED")

    def test_a_static_verdict_may_only_carry_the_static_scope(self):
        document = copy.deepcopy(self.document)
        document["apis"][0]["safety"]["scope"] = "EXECUTION_APPROVED"
        self.assertRejected(document, "may only carry the static scope")

    def test_the_approval_requirement_may_not_be_dropped(self):
        document = copy.deepcopy(self.document)
        document["apis"][0]["safety"]["approval_requirement"] = "NONE"
        self.assertRejected(document, "approval requirement")

    def test_the_independent_approval_procedure_may_not_be_dropped(self):
        document = copy.deepcopy(self.document)
        document["method"]["independent_approval_procedure"] = "NONE"
        self.assertRejected(document, "independent_approval_procedure")

    def test_a_static_analysis_cannot_produce_verified(self):
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

    # -- the claim ledger and the published text --------------------------------
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
        document["apis"][1]["api_id"] = "slot-3c"
        document["apis"][1]["slot"] = 30
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

    def test_unknown_static_verdict_is_rejected(self):
        document = copy.deepcopy(self.document)
        document["apis"][0]["safety"]["static_verdict"] = "PROBABLY_SAFE"
        self.assertRejected(document, "static verdict")

    def test_unknown_abi_state_is_rejected(self):
        document = copy.deepcopy(self.document)
        document["apis"][0]["abi_state"] = "DEFINITELY_OBSERVED"
        self.assertRejected(document, "abi state vocabulary")

    def test_unknown_observation_vocabulary_is_rejected(self):
        document = copy.deepcopy(self.document)
        document["apis"][0]["observation"]["reproducible_real_core"] = "OBSERVED_LOCAL_ONLY"
        self.assertRejected(document, "reproducible observation vocabulary")

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

    def test_a_data_dependent_store_forces_the_blocked_verdict(self):
        document = copy.deepcopy(self.document)
        document["apis"][0]["static"]["hazard_counts"]["DATA_DEPENDENT_STORE"] = 1
        self.assertRejected(document, "forces EXECUTION_BLOCKED")

    def test_a_store_outside_page_one_forces_the_blocked_verdict(self):
        document = copy.deepcopy(self.document)
        document["apis"][0]["static"]["hazard_counts"]["OTHER_STORE"] = 1
        self.assertRejected(document, "forces EXECUTION_BLOCKED")

    def test_a_fresh_execution_claim_is_rejected(self):
        document = copy.deepcopy(self.document)
        document["apis"][1]["abi_state"] = "OBSERVED"
        document["apis"][1]["observation"]["reproducible_real_core"] = "OBSERVED_LOCAL_ONLY"
        self.assertRejected(document, "reproducible observation vocabulary")

    def test_a_prior_observation_has_to_name_its_source(self):
        document = copy.deepcopy(self.document)
        document["apis"][0]["observation"]["prior_evidence"] = "NONE"
        self.assertRejected(document, "has to name its source")

    def test_a_prior_observation_source_has_to_exist(self):
        document = copy.deepcopy(self.document)
        document["apis"][0]["observation"]["prior_evidence"] = "docs/not-a-real-file.md"
        self.assertRejected(document, "not a repository file")

    def test_a_prior_observation_source_outside_the_repository_is_rejected(self):
        document = copy.deepcopy(self.document)
        document["apis"][0]["observation"]["prior_evidence"] = "/etc/passwd"
        self.assertRejected(document, "not a repository file")

    def test_a_prior_observation_source_outside_the_repository_by_traversal(self):
        document = copy.deepcopy(self.document)
        document["apis"][0]["observation"]["prior_evidence"] = "../../../../etc/passwd"
        self.assertRejected(document, "not a repository file")

    def test_a_slot_without_an_observation_may_not_cite_prior_evidence(self):
        document = copy.deepcopy(self.document)
        document["apis"][1]["observation"]["prior_evidence"] = "docs/reference-bios-test-plan.md"
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

    def test_a_decoded_bit_opcode_in_published_text_is_rejected(self):
        document = copy.deepcopy(self.document)
        document["apis"][0]["static_items"]["expected_return_value"]["value"] = "smb0 $42"
        self.assertRejected(document, "decoded instruction")

    def test_internal_address_in_published_text_is_rejected(self):
        document = copy.deepcopy(self.document)
        document["apis"][0]["static_items"]["expected_return_value"]["value"] = "read $CD10"
        self.assertRejected(document, "non public address")

    def test_missing_execution_blocker_is_rejected(self):
        document = copy.deepcopy(self.document)
        document["method"]["execution_blockers"] = []
        self.assertRejected(document, "execution_blockers")

    def test_unknown_top_level_key_is_rejected(self):
        document = copy.deepcopy(self.document)
        document["extra"] = 1
        self.assertRejected(document, "review keys")


class ReferenceGateIsolationTests(unittest.TestCase):
    """The static review must not be able to open the reference JSR gate.

    The gate is a separate, fail-closed mechanism. It must keep refusing every
    slot, and it must not read the Phase ABI-1 ledger, so that a static
    ``EXECUTION_SAFE`` verdict can never reach it.
    """

    def test_the_reference_jsr_gate_still_refuses_every_slot(self):
        import subprocess
        result = subprocess.run([sys.executable, str(ROOT / "tools" / "reference_jsr_gate.py"),
                                 "--check"], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 3)
        self.assertIn("closed for 81/81 slots", result.stdout)
        self.assertIn("jsr_count=0", result.stdout)

    def test_the_reference_jsr_gate_does_not_read_the_phase_ledger(self):
        source = (ROOT / "tools" / "reference_jsr_gate.py").read_text(encoding="utf-8")
        self.assertNotIn("abi1", source.lower())
        self.assertNotIn("static_verdict", source)
        self.assertNotIn("execution_permission", source)

    def test_the_gate_ledger_is_untouched_by_this_phase(self):
        ledger = json.loads((ROOT / "spec" / "reference_jsr_gate.json").read_text(encoding="utf-8"))
        for entry in ledger["slots"].values():
            self.assertEqual(entry["execution"], "BLOCKED")
            self.assertIs(entry["release_eligible"], False)


if __name__ == "__main__":
    unittest.main()
