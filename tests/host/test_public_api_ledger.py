"""Public API ledger allowlist (owner ruling): positive and negative tests on the contract ledger and synthetic copies.

Nothing here reads a private image or a secondary-source table. The negative samples are built by mutating a copy
of the public output or of the contract ledger, and must be refused.
"""
from pathlib import Path
import contextlib
import copy
import io
import json
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import public_api_ledger as pal  # noqa: E402
import reference_jsr_gate as gate  # noqa: E402
import dev_implementations as dev  # noqa: E402
from build_rom import FIRST_ENTRY, LAST_SLOT, load_slots, overrides  # noqa: E402

CONTRACTS = json.loads((ROOT / "spec" / "api_contracts.json").read_text(encoding="utf-8"))
SLOT13 = 13  # the unresolved-pair slot (the test never reads its real name)
EXPECTED_ALIAS_SLOTS = {13, 74, 75, 76, 80}  # the owner's alias set; checked here, not in the published tool


def alias_slots(contracts):
    return {slot for slot, api in enumerate(contracts["apis"]) if " / " in api["candidate_name"]}
PAUSE_SLOT = 8  # the pause contract slot, keyed by index only (see the slot-08 fixture test)


def public():
    return pal.build(CONTRACTS)


class AllowlistPositive(unittest.TestCase):
    def test_built_document_validates(self):
        self.assertEqual(public(), pal.validate(public(), CONTRACTS))

    def test_the_document_has_81_slots_and_is_not_released(self):
        document = public()
        self.assertEqual(len(document["apis"]), 81)
        self.assertIs(document["release_eligible"], False)

    def test_every_slot_is_unverified_and_gated(self):
        for api in public()["apis"]:
            with self.subTest(slot=api["slot"]):
                self.assertEqual(api["abi_verification"], "UNVERIFIED")
                self.assertEqual(api["execution_gate"], "BLOCKED")

    def test_no_name_is_published_while_provenance_is_pending(self):
        for api in public()["apis"]:
            with self.subTest(slot=api["slot"]):
                self.assertNotIn("name", api)
                self.assertEqual(api["name_publication"], "WITHHELD_PROVENANCE_PENDING")

    def test_the_public_layout_is_the_dev_rom_generated_table(self):
        # The DEV ROM generator (tools/build_rom.py) owns the table: 81 slots, 3-byte entries from FIRST_ENTRY.
        self.assertEqual(LAST_SLOT + 1, 81)
        self.assertEqual(pal.FIRST_ENTRY, FIRST_ENTRY)

    def test_no_entry_value_is_published(self):
        # Owner ruling: the public management output lists no entry value; the layout is checked on the contract side.
        for api in public()["apis"]:
            with self.subTest(slot=api["slot"]):
                self.assertNotIn("entry", api)

    def test_the_contract_entries_follow_the_layout_rule(self):
        pal.check_contracts(CONTRACTS)
        self.assertEqual(CONTRACTS["apis"][PAUSE_SLOT]["candidate_entry"], f"0x{FIRST_ENTRY + 3 * PAUSE_SLOT:04X}")

    def test_an_entry_field_added_to_the_public_output_is_refused(self):
        document = public()
        document["apis"][0]["entry"] = f"0x{FIRST_ENTRY:04X}"
        with self.assertRaises(pal.Refused):
            pal.validate(document, CONTRACTS)

    def test_every_slot_carries_the_same_withheld_status(self):
        # Per-slot classification is not published: it would reconstruct secondary-source structure.
        apis = public()["apis"]
        self.assertEqual({api["name_source"] for api in apis}, {"WITHHELD_PENDING"})
        self.assertEqual({api["source_confidence"] for api in apis}, {"PENDING"})

    def test_the_command_prints_only_the_public_document(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = pal.main([])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out.getvalue()), public())
        self.assertEqual(err.getvalue(), "")


class ContractStructure(unittest.TestCase):
    """The contract ledger's alias structure and entries are checked by rule, not against a copied table."""

    def test_the_real_contract_passes(self):
        pal.check_contracts(CONTRACTS)

    def test_the_contract_alias_slots_are_the_owner_set(self):
        # The owner's alias set is checked here, in a test, so the published tool keeps no per-slot list.
        self.assertEqual(alias_slots(CONTRACTS), EXPECTED_ALIAS_SLOTS)

    def test_a_single_name_for_an_alias_slot_is_refused(self):
        contracts = copy.deepcopy(CONTRACTS)
        contracts["apis"][SLOT13]["candidate_name"] = "SYNTH_A"
        self.assertNotEqual(alias_slots(contracts), EXPECTED_ALIAS_SLOTS)

    def test_a_pair_for_a_single_name_slot_is_refused(self):
        contracts = copy.deepcopy(CONTRACTS)
        contracts["apis"][0]["candidate_name"] = "SYNTH_A / SYNTH_B"
        self.assertNotEqual(alias_slots(contracts), EXPECTED_ALIAS_SLOTS)

    def test_a_lowercase_or_spaced_name_is_refused(self):
        for name in ("synth_a", "SYNTH A", "SYNTH_A /"):
            with self.subTest(name=name):
                contracts = copy.deepcopy(CONTRACTS)
                contracts["apis"][0]["candidate_name"] = name
                with self.assertRaises(pal.Refused):
                    pal.check_contracts(contracts)

    def test_a_duplicate_name_is_refused(self):
        contracts = copy.deepcopy(CONTRACTS)
        contracts["apis"][SLOT13]["candidate_name"] = "SYNTH_A / SYNTH_A"
        with self.assertRaises(pal.Refused):
            pal.check_contracts(contracts)

    def test_an_entry_that_differs_from_the_layout_rule_is_refused(self):
        contracts = copy.deepcopy(CONTRACTS)
        contracts["apis"][0]["candidate_entry"] = "0xEFFF"
        with self.assertRaises(pal.Refused):
            pal.check_contracts(contracts)


class ReleaseGateState(unittest.TestCase):
    """The gate state is public management state; it must stay closed (owner ruling, Issue #476/#324)."""

    def test_the_reference_jsr_gate_is_closed_and_not_released(self):
        gate_file = json.loads((ROOT / "spec" / "reference_jsr_gate.json").read_text(encoding="utf-8"))
        for key, record in gate_file["slots"].items():
            with self.subTest(slot=key):
                self.assertEqual(record["execution"], "BLOCKED")
                self.assertIs(record["release_eligible"], False)

    def test_the_abi1_ledger_claims_no_verified_state_and_no_release(self):
        ledger = json.loads((ROOT / "spec" / "abi1_reference_review.json").read_text(encoding="utf-8"))
        self.assertIs(ledger["release_eligible"], False)
        self.assertTrue(all(api["abi_state"] != "VERIFIED" for api in ledger["apis"]))
        self.assertTrue(all(api["safety"]["execution_permission"] == "BLOCKED" for api in ledger["apis"]))


class AllowlistNegative(unittest.TestCase):
    def refused(self, mutate):
        document = public()
        mutate(document)
        with self.assertRaises(pal.Refused):
            pal.validate(document, CONTRACTS)

    def test_an_unknown_top_level_field_is_refused(self):
        self.refused(lambda d: d.update(callee_instructions=3))

    def test_an_unknown_slot_field_is_refused(self):
        self.refused(lambda d: d["apis"][0].update(hazard_counts={"MPR_WRITE": 0}))

    def test_a_name_field_is_refused_while_names_are_withheld(self):
        self.refused(lambda d: d["apis"][0].update(name="SYNTH_A"))

    def test_a_publication_claim_for_names_is_refused(self):
        self.refused(lambda d: d["apis"][0].update(name_publication="PUBLISHED"))

    def test_a_nested_object_smuggled_into_an_allowed_field_is_refused(self):
        self.refused(lambda d: d["apis"][0].update(entry_source={"callee_instructions": 3}))

    def test_an_entry_that_differs_from_the_layout_is_refused(self):
        self.refused(lambda d: d["apis"][30].update(entry="0xEFFF"))

    def test_a_verified_state_is_refused(self):
        self.refused(lambda d: d["apis"][0].update(abi_verification="VERIFIED"))

    def test_an_open_execution_gate_is_refused(self):
        self.refused(lambda d: d["apis"][0].update(execution_gate="ALLOWED"))

    def test_a_release_claim_is_refused(self):
        self.refused(lambda d: d.update(release_eligible=True))

    def test_a_missing_slot_is_refused(self):
        self.refused(lambda d: d.update(apis=d["apis"][:-1]))

    def test_an_unconfirmed_confidence_on_a_confirmed_slot_is_refused(self):
        self.refused(lambda d: d["apis"][0].update(source_confidence="UNCONFIRMED_NAME"))

    def test_a_name_source_outside_the_uniform_status_is_refused(self):
        self.refused(lambda d: d["apis"][0].update(name_source="PRIVATE_IMAGE"))

    def test_a_per_slot_alias_classification_is_refused(self):
        self.refused(lambda d: d["apis"][13].update(name_source="UNRESOLVED_ALTERNATIVES"))

    def test_a_per_slot_author_label_classification_is_refused(self):
        self.refused(lambda d: d["apis"][77].update(name_source="UNCONFIRMED_AUTHOR_LABEL"))

    def test_a_per_slot_unconfirmed_confidence_is_refused(self):
        self.refused(lambda d: d["apis"][74].update(source_confidence="UNCONFIRMED_NAME"))

    def test_a_name_field_added_next_to_the_status_is_refused(self):
        self.refused(lambda d: d["apis"][13].update(name="SYNTH_A / SYNTH_B"))

    def test_a_forged_entry_source_is_refused(self):
        self.refused(lambda d: d["apis"][0].update(entry_source="self-declared"))

    def test_a_wrong_schema_version_is_refused(self):
        self.refused(lambda d: d.update(schema="oss-pce-cd-bios/public-api-ledger/v3"))

    def test_an_entry_key_is_refused_at_the_slot_level(self):
        self.refused(lambda d: d["apis"][0].update(entry="0xE000"))

    def test_a_non_integer_slot_is_refused(self):
        self.refused(lambda d: d["apis"][1].update(slot="1"))


class Slot13GateState(unittest.TestCase):
    """Slot 13 (the unresolved-pair slot): the gate, claim and dev-ledger checks, on the real files and on mutated copies.

    Slot 13 has no reviewed gate record, so the real gate must refuse it (fail closed). The negative cases
    mutate a copy; the real files are never changed by a test.
    """

    @classmethod
    def setUpClass(cls):
        cls.ledger, cls.contracts = gate.load()

    def test_the_abi_is_unverified_in_the_claim_and_the_public_output(self):
        self.assertEqual(self.contracts["apis"][SLOT13]["verification"]["status"], "NOT_RUN")
        self.assertEqual(public()["apis"][SLOT13]["abi_verification"], "UNVERIFIED")

    def test_the_real_gate_refuses_slot_13_because_no_reviewed_record_exists(self):
        self.assertNotIn(str(SLOT13), self.ledger["slots"])
        record = gate.resolve(SLOT13, self.ledger, self.contracts)
        self.assertEqual(record["execution"], "BLOCKED")
        self.assertIn("NO_REVIEWED_GATE_RECORD", record["reasons"])
        self.assertIs(record["execution_permitted"], False)
        self.assertIs(record["release_eligible"], False)

    def test_require_allowed_fails_closed_for_slot_13(self):
        with self.assertRaises(gate.GateRefused):
            gate.require_allowed("slot-0d")

    def test_the_public_output_keeps_slot_13_closed_and_its_pair_unresolved(self):
        api = public()["apis"][SLOT13]
        self.assertEqual(api["name_source"], "WITHHELD_PENDING")
        self.assertEqual(api["execution_gate"], "BLOCKED")
        self.assertIs(public()["release_eligible"], False)

    def test_a_verified_claim_for_slot_13_is_refused_by_the_public_validator(self):
        document = public()
        document["apis"][SLOT13]["abi_verification"] = "VERIFIED"
        with self.assertRaises(pal.Refused):
            pal.validate(document, CONTRACTS)

    def test_a_recorded_callee_run_for_slot_13_keeps_the_gate_closed(self):
        # A mutated claim that claims an executed callee must not reach MATERIALS_COMPLETE or ALLOWED.
        contracts = copy.deepcopy(self.contracts)
        contracts["apis"][SLOT13]["verification"] = {"status": "VERIFIED", "executed_callee": True, "evidence": ["x"]}
        record = gate.resolve(SLOT13, self.ledger, contracts)
        self.assertEqual(record["execution"], "BLOCKED")
        self.assertIn("CALLEE_VERIFICATION_RECORDED", record["reasons"])

    def test_a_reviewed_record_for_slot_13_with_execution_allowed_is_refused_by_the_ledger_check(self):
        ledger = copy.deepcopy(self.ledger)
        ledger["slots"]["13"] = {
            "candidate_entry": f"0x{FIRST_ENTRY + 3 * SLOT13:04X}",
            "execution": "ALLOWED",
            "release_eligible": False,
            "hazard_controls": {f"H{i}": "CONTROLLED" for i in range(1, 8)},
        }
        with self.assertRaises(ValueError):
            gate.validate_ledger(ledger, self.contracts)

    def test_a_reviewed_record_for_slot_13_with_release_eligible_is_refused_by_the_ledger_check(self):
        ledger = copy.deepcopy(self.ledger)
        ledger["slots"]["13"] = {
            "candidate_entry": f"0x{FIRST_ENTRY + 3 * SLOT13:04X}",
            "execution": "BLOCKED",
            "release_eligible": True,
            "hazard_controls": {f"H{i}": "CONTROLLED" for i in range(1, 8)},
        }
        with self.assertRaises(ValueError):
            gate.validate_ledger(ledger, self.contracts)

    def test_a_dev_callable_promotion_of_slot_13_without_approval_is_refused(self):
        ledger = copy.deepcopy(dev.load_ledger())
        entry = next(e for e in ledger["slots"] if e["id"] == SLOT13)
        entry["state"] = "DEV_CALLABLE"
        problems = dev.validate(ledger, load_slots(), set(overrides()))
        self.assertTrue(problems, "a DEV_CALLABLE slot 13 without approval must be reported")

    def test_a_renamed_slot_13_contract_is_refused_not_silently_accepted(self):
        contracts = copy.deepcopy(CONTRACTS)
        contracts["apis"][SLOT13]["candidate_name"] = "SYNTH_A"
        self.assertNotEqual(alias_slots(contracts), EXPECTED_ALIAS_SLOTS)


class PauseSlotGateState(unittest.TestCase):
    """The pause slot: the release and BLOCKED state is checked on the real gate, since the pause document is withdrawn."""

    @classmethod
    def setUpClass(cls):
        cls.ledger, cls.contracts = gate.load()

    def test_the_real_gate_keeps_the_pause_slot_blocked_and_not_released(self):
        record = gate.resolve(PAUSE_SLOT, self.ledger, self.contracts)
        self.assertEqual(record["execution"], "BLOCKED")
        self.assertIs(record["release_eligible"], False)
        self.assertIs(record["execution_permitted"], False)

    def test_the_public_output_keeps_the_pause_slot_unverified_and_blocked(self):
        api = public()["apis"][PAUSE_SLOT]
        self.assertEqual(api["abi_verification"], "UNVERIFIED")
        self.assertEqual(api["execution_gate"], "BLOCKED")


class SurvivingContractState(unittest.TestCase):
    """Replacement coverage for the withdrawn-document pins (slots 7, 10, 11, 12 and 15).

    The approved surviving contracts are the public ledger, the gate, and the DEV state file. Each affected slot must
    show: no verification, no executed callee, zero reference JSR, a BLOCKED gate, and a non-callable DEV state.
    """

    AFFECTED = (7, 10, 11, 12, 15)

    @classmethod
    def setUpClass(cls):
        cls.ledger, cls.contracts = gate.load()
        cls.dev = {entry["id"]: entry for entry in dev.load_ledger()["slots"]}

    def test_each_affected_slot_is_unverified_and_not_executed(self):
        for slot in self.AFFECTED:
            with self.subTest(slot=slot):
                verification = self.contracts["apis"][slot]["verification"]
                self.assertEqual(verification["status"], "NOT_RUN")
                self.assertIs(verification["executed_callee"], False)

    def test_each_affected_slot_is_blocked_with_zero_reference_jsr(self):
        for slot in self.AFFECTED:
            with self.subTest(slot=slot):
                record = gate.resolve(slot, self.ledger, self.contracts)
                self.assertEqual(record["execution"], "BLOCKED")
                self.assertEqual(record["jsr_count"], 0)
                self.assertIs(record["release_eligible"], False)

    def test_each_affected_slot_is_not_a_dev_callable_entry(self):
        for slot in self.AFFECTED:
            with self.subTest(slot=slot):
                self.assertNotEqual(self.dev[slot]["state"], "DEV_CALLABLE")

    def test_a_callee_promoted_to_executed_on_an_affected_slot_is_refused_by_the_gate(self):
        contracts = copy.deepcopy(self.contracts)
        contracts["apis"][10]["verification"] = {"status": "VERIFIED", "executed_callee": True, "evidence": ["x"]}
        record = gate.resolve(10, self.ledger, contracts)
        self.assertEqual(record["execution"], "BLOCKED")
        self.assertIn("CALLEE_VERIFICATION_RECORDED", record["reasons"])


if __name__ == "__main__":
    unittest.main()
