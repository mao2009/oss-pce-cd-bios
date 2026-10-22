"""Host tests for the EX_GETVER observation contract (Issue #474). Synthetic inputs only: no ROM, no core.

Each refusal class in the Phase ABI-2 negative list has a test that builds a valid synthetic record,
breaks exactly one property, and asserts the refusal code. A test that only checks for "some error"
would pass a validator that refuses everything, so every case also has a positive control.
"""
from copy import deepcopy
from pathlib import Path
import json
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import getver_observation as g  # noqa: E402

ROM = "a" * 64
HARNESS = "b" * 64
CORE = "c" * 40
ENTRY = 0xE05A
BUDGET = 2048
ZP = "00" * 32
SYNTHETIC = dict(rom_sha256=ROM, entry=ENTRY, core_revision=CORE, harness_revision=HARNESS, step_budget=BUDGET)


def state(sp=0xFF, mpr=(0xFF, 0xF8, 0xF8, 0, 0, 0, 0, 0)):
    return {"a": 0x11, "x": 0x03, "y": 0x00, "p": 0x04, "sp": sp, "mpr": list(mpr), "zp": ZP}


def record(**changes):
    """A sealed synthetic record: one warm RETURNED run of the DEV routine. Fabricated, not measured."""
    base = {
        "schema": g.SCHEMA, "slot": g.SLOT, "entry": ENTRY,
        "source": {"kind": "SYNTHETIC_DEV_ROM", "rom_sha256": ROM},
        "core": {"name": "geargrafx", "revision": CORE},
        "harness": {"name": "geargrafx_core_harness", "revision": HARNESS},
        "case": "synthetic_case", "temperature": "warm", "status": "RETURNED",
        "inputs": {"a": 0x11, "x": 0x03, "y": 0x00, "p": 0x04},  # must equal before's A/X/Y/P
        "before": state(), "after": state(),
        "io": {"observation": g.NOT_MEASURED, "accesses": []},
        "steps_used": g.NOT_MEASURED, "step_budget": BUDGET, "abi_claim": "OBSERVED_SYNTHETIC",
    }
    base.update(changes)
    return g.seal(base)


def mutate(change):
    """Apply ``change`` to a valid record, then reseal it so only the broken property is wrong."""
    value = deepcopy(record())
    change(value)
    return g.seal(value)


class ValidRecordTests(unittest.TestCase):
    def test_positive_control_is_accepted_as_synthetic_observation(self):
        self.assertEqual(g.validate(record(), **SYNTHETIC), "OBSERVED_SYNTHETIC")

    def test_record_survives_a_jsonl_round_trip(self):
        text = "\n".join(json.dumps(record(temperature=t)) for t in g.TEMPERATURES)
        records = g.load_jsonl(text)
        self.assertEqual(len(records), 2)
        for item in records:
            self.assertEqual(g.validate(item, **SYNTHETIC), "OBSERVED_SYNTHETIC")

    def test_unknown_key_is_refused(self):
        with self.assertRaisesRegex(g.Refused, "FIELDS"):
            g.validate(record(extra=1), **SYNTHETIC)


class NegativeListTests(unittest.TestCase):
    """The ten refusal classes from the Phase ABI-2 negative list."""

    def assert_refused(self, item, code, **overrides):
        with self.assertRaises(g.Refused) as caught:
            g.validate(item, **(SYNTHETIC | overrides))
        self.assertEqual(caught.exception.code, code, str(caught.exception))

    def test_1_rom_identity_mismatch(self):
        self.assert_refused(record(), "ROM_MISMATCH", rom_sha256="d" * 64)

    def test_2_entry_mismatch(self):
        self.assert_refused(record(), "ENTRY_MISMATCH", entry=ENTRY + 1)

    def test_3_unapproved_reference_execution_request(self):
        reference = mutate(lambda r: r.update(source={"kind": "REFERENCE_ROM", "rom_sha256": ROM}))
        self.assert_refused(reference, "REFERENCE_UNAPPROVED")

    def test_4_illegal_mpr_change(self):
        item = mutate(lambda r: r["after"].update(mpr=[0xFF, 0xF8, 0xF8, 0, 0, 0, 0, 1]))
        self.assert_refused(item, "MPR")

    def test_5_unexpected_io_access(self):
        item = mutate(lambda r: r["io"].update(accesses=[{"addr": 0x2000, "kind": "write"}]))
        self.assert_refused(item, "IO")

    def test_6_stack_corruption(self):
        item = mutate(lambda r: r["after"].update(sp=0xFE))
        self.assert_refused(item, "STACK")

    def test_7_step_budget_exceeded(self):
        self.assert_refused(mutate(lambda r: r.update(status="TIMEOUT")), "BUDGET")
        self.assert_refused(mutate(lambda r: r.update(steps_used=BUDGET + 1)), "BUDGET")

    def test_8_tampered_record_is_refused(self):
        item = record()
        item["after"]["a"] = 0x99  # body changed after sealing: the digest no longer matches
        self.assert_refused(item, "TAMPERED")

    def test_9_static_review_alone_grants_no_execution(self):
        decision = g.decide_execution({"verdict": "SAFE"}, [])
        self.assertEqual(decision["execution"], "BLOCKED")
        self.assertEqual(decision["reason"], "NO_OBSERVATION_STATIC_ONLY")
        observed = g.decide_execution({"verdict": "SAFE"}, [record()])
        self.assertEqual(observed["execution"], "BLOCKED")
        self.assertNotEqual(observed["execution"], "ALLOWED")

    def test_10_verified_claim_without_evidence_is_refused(self):
        self.assert_refused(mutate(lambda r: r.update(abi_claim="VERIFIED")), "ABI_CLAIM")
        counts = g.abi_status([record()])
        self.assertEqual(counts["reference_abi_verified"], 0)
        self.assertEqual(counts["reproducible_reference_observations"], 0)
        self.assertEqual(counts["synthetic_observations"], 1)


class DigestScopeTests(unittest.TestCase):
    """record_digest is an integrity check. These tests pin down what a matching digest does and does not prove."""

    def test_body_change_without_reseal_is_refused(self):
        item = record()
        item["after"]["a"] = 0x99  # digest left stale on purpose
        with self.assertRaisesRegex(g.Refused, "TAMPERED"):
            g.validate(item, **SYNTHETIC)

    def test_body_and_digest_changed_together_pass_integrity_but_prove_nothing(self):
        altered = mutate(lambda r: r["after"].update(a=0x99))  # a forger reseals the altered body
        self.assertEqual(g.validate(altered, **SYNTHETIC), "OBSERVED_SYNTHETIC")  # integrity only, no provenance
        self.assertEqual(g.decide_execution({"verdict": "SAFE"}, [altered])["execution"], "BLOCKED")

    def test_matching_digest_cannot_promote_a_claim_to_verified(self):
        forged = mutate(lambda r: r.update(abi_claim="VERIFIED"))  # resealed, so the digest matches
        with self.assertRaisesRegex(g.Refused, "ABI_CLAIM"):
            g.validate(forged, **SYNTHETIC)


class InputSchemaTests(unittest.TestCase):
    def assert_inputs_refused(self, inputs, code="FIELDS"):
        item = mutate(lambda r: r.update(inputs=inputs))
        with self.assertRaisesRegex(g.Refused, code):
            g.validate(item, **SYNTHETIC)

    def test_1_extra_key_refused(self):
        self.assert_inputs_refused({"a": 0x11, "x": 0x03, "y": 0x00, "p": 0x04, "s": 0x00})

    def test_2_missing_key_refused(self):
        self.assert_inputs_refused({"a": 0x11, "x": 0x03, "y": 0x00})

    def test_3_inputs_not_an_object_refused(self):
        for bad in ([0x11, 0x03, 0x00, 0x04], None, "a=11"):
            with self.subTest(inputs=bad):
                self.assert_inputs_refused(bad)

    def test_4_bool_value_refused(self):
        self.assert_inputs_refused({"a": True, "x": 0x03, "y": 0x00, "p": 0x04})

    def test_5_value_out_of_byte_range_refused(self):
        for bad in (256, -1, "17", 1.0, None):
            with self.subTest(value=bad):
                self.assert_inputs_refused({"a": bad, "x": 0x03, "y": 0x00, "p": 0x04})

    def test_6_inputs_differing_from_before_refused(self):
        self.assert_inputs_refused({"a": 0x11, "x": 0x22, "y": 0x00, "p": 0x04}, code="INPUTS")

    def test_positive_control_inputs_match_before(self):
        self.assertEqual(g.validate(record(), **SYNTHETIC), "OBSERVED_SYNTHETIC")


class IoSchemaTests(unittest.TestCase):
    def assert_io_refused(self, io):
        item = mutate(lambda r: r.update(io=io))
        with self.assertRaisesRegex(g.Refused, "IO"):
            g.validate(item, **SYNTHETIC)

    def test_7_io_not_an_object_refused(self):
        for bad in ([], None, "NOT_MEASURED"):
            with self.subTest(io=bad):
                self.assert_io_refused(bad)

    def test_8_io_missing_or_extra_key_refused(self):
        self.assert_io_refused({"observation": g.NOT_MEASURED})
        self.assert_io_refused({"observation": g.NOT_MEASURED, "accesses": [], "note": "clean"})

    def test_9_accesses_not_a_list_refused(self):
        for bad in ({}, None, "none", 0):
            with self.subTest(accesses=bad):
                self.assert_io_refused({"observation": g.NOT_MEASURED, "accesses": bad})

    def test_10_claiming_no_access_without_measurement_refused(self):
        self.assert_io_refused({"observation": g.IO_MEASURED_NONE, "accesses": []})

    def test_unmeasured_io_with_recorded_accesses_refused(self):
        self.assert_io_refused({"observation": g.NOT_MEASURED, "accesses": [{"addr": 0x2000, "kind": "read"}]})


class MalformedStructureTests(unittest.TestCase):
    """Malformed structure must surface as Refused, never as a Python exception from the validator."""

    def test_any_wrong_shape_in_a_field_is_refused_not_raised(self):
        bad_values = (None, [], "x", -1, True, {}, [0] * 8)
        for field in ("source", "core", "harness", "before", "after", "io", "inputs", "steps_used", "abi_claim"):
            for bad in bad_values:
                with self.subTest(field=field, value=bad):
                    item = mutate(lambda r: r.update({field: bad}))
                    with self.assertRaises(g.Refused):
                        g.validate(item, **SYNTHETIC)


class SchemaTests(unittest.TestCase):
    def test_wrong_schema_version_is_refused(self):
        item = mutate(lambda r: r.update(schema="oss-pce-cd-bios/getver-observation/v0"))
        with self.assertRaisesRegex(g.Refused, "SCHEMA"):
            g.validate(item, **SYNTHETIC)

    def test_duplicate_key_in_jsonl_is_refused(self):
        line = json.dumps(record())[:-1] + ', "case": "dup"}'
        with self.assertRaisesRegex(g.Refused, "FIELDS"):
            g.load_jsonl(line)

    def test_bad_zero_page_length_is_refused(self):
        item = mutate(lambda r: r["before"].update(zp="00"))
        with self.assertRaisesRegex(g.Refused, "FIELDS"):
            g.validate(item, **SYNTHETIC)


class ConverterTests(unittest.TestCase):
    def evidence(self, cold_status="RETURNED", extra_cases=()):
        observed = {"before": state(), "after": state()}
        case = {"name": "plugin_ex_getver", "target": "api_slot_1E",
                "status": {"cold": cold_status, "warm": "RETURNED"},
                "observed": {"cold": observed, "warm": observed}}
        selftest = {"name": "selftest_add", "target": "harness_selftest", "a": 1, "x": 2, "y": 3, "p": 4,
                    "status": {"cold": "RETURNED", "warm": "RETURNED"}, "observed": {"cold": observed}}
        return {"geargrafx_revision": CORE, "cases": [selftest, case, *extra_cases]}

    def test_converts_only_the_api_slot_case_for_each_returned_temperature(self):
        records = g.from_core_evidence(self.evidence(), case_name="plugin_ex_getver", rom_sha256=ROM, entry=ENTRY,
                                       harness_revision=HARNESS, step_budget=BUDGET)
        self.assertEqual([r["temperature"] for r in records], ["cold", "warm"])
        for item in records:
            self.assertEqual(g.validate(item, **SYNTHETIC), "OBSERVED_SYNTHETIC")
            self.assertEqual(item["io"]["observation"], g.NOT_MEASURED)
            self.assertEqual(item["steps_used"], g.NOT_MEASURED)

    def test_a_cycle_that_did_not_return_produces_no_record(self):
        records = g.from_core_evidence(self.evidence(cold_status="TIMEOUT"), case_name="plugin_ex_getver", rom_sha256=ROM, entry=ENTRY,
                                       harness_revision=HARNESS, step_budget=BUDGET)
        self.assertEqual([r["temperature"] for r in records], ["warm"])

    def test_run_that_breaks_the_contract_is_refused_not_counted(self):
        evidence = self.evidence()
        evidence["cases"][1]["observed"]["warm"]["after"]["sp"] = 0xFE  # stack corrupted: validate() refuses it
        with self.assertRaisesRegex(g.Refused, "STACK"):
            g.from_core_evidence(evidence, case_name="plugin_ex_getver", rom_sha256=ROM, entry=ENTRY,
                                 harness_revision=HARNESS, step_budget=BUDGET)

    def test_missing_case_name_is_refused(self):
        with self.assertRaisesRegex(g.Refused, "CASE"):
            g.from_core_evidence(self.evidence(), case_name="no_such_case", rom_sha256=ROM, entry=ENTRY,
                                 harness_revision=HARNESS, step_budget=BUDGET)

    def test_duplicate_case_name_is_refused(self):
        dup = self.evidence()["cases"][1]
        with self.assertRaisesRegex(g.Refused, "CASE"):
            g.from_core_evidence(self.evidence(extra_cases=[dup]), case_name="plugin_ex_getver", rom_sha256=ROM,
                                 entry=ENTRY, harness_revision=HARNESS, step_budget=BUDGET)

    def test_case_not_targeting_api_slot_is_refused(self):
        evidence = self.evidence()
        evidence["cases"][1]["target"] = "harness_selftest"
        with self.assertRaisesRegex(g.Refused, "ENTRY_MISMATCH"):
            g.from_core_evidence(evidence, case_name="plugin_ex_getver", rom_sha256=ROM, entry=ENTRY,
                                 harness_revision=HARNESS, step_budget=BUDGET)

    def test_nan_in_core_evidence_is_refused_not_raised(self):
        evidence = self.evidence()
        evidence["cases"][1]["observed"]["warm"]["before"]["a"] = float("nan")
        with self.assertRaisesRegex(g.Refused, "FIELDS"):
            g.from_core_evidence(evidence, case_name="plugin_ex_getver", rom_sha256=ROM, entry=ENTRY,
                                 harness_revision=HARNESS, step_budget=BUDGET)


class SourceAndCaseSchemaTests(unittest.TestCase):
    def test_source_with_extra_key_is_refused(self):
        item = mutate(lambda r: r.update(source={"kind": "SYNTHETIC_DEV_ROM", "rom_sha256": ROM, "note": "x"}))
        with self.assertRaisesRegex(g.Refused, "SOURCE"):
            g.validate(item, **SYNTHETIC)

    def test_source_missing_key_is_refused(self):
        item = mutate(lambda r: r.update(source={"kind": "SYNTHETIC_DEV_ROM"}))
        with self.assertRaisesRegex(g.Refused, "SOURCE"):
            g.validate(item, **SYNTHETIC)

    def test_unknown_source_kind_is_refused(self):
        item = mutate(lambda r: r.update(source={"kind": "LIVE_ROM", "rom_sha256": ROM}))
        with self.assertRaisesRegex(g.Refused, "SOURCE"):
            g.validate(item, **SYNTHETIC)

    def test_core_or_harness_identity_mismatch_is_refused(self):
        with self.assertRaisesRegex(g.Refused, "ROM_MISMATCH"):
            g.validate(record(), **(SYNTHETIC | {"core_revision": "d" * 40}))
        with self.assertRaisesRegex(g.Refused, "ROM_MISMATCH"):
            g.validate(record(), **(SYNTHETIC | {"harness_revision": "d" * 64}))

    def test_bad_temperature_is_refused(self):
        item = mutate(lambda r: r.update(temperature="hot"))
        with self.assertRaisesRegex(g.Refused, "FIELDS"):
            g.validate(item, **SYNTHETIC)

    def test_status_other_than_returned_is_refused(self):
        item = mutate(lambda r: r.update(status="ERROR"))
        with self.assertRaisesRegex(g.Refused, "STATUS"):
            g.validate(item, **SYNTHETIC)

    def test_float_slot_entry_or_budget_is_refused(self):
        with self.assertRaisesRegex(g.Refused, "ENTRY_MISMATCH"):
            g.validate(mutate(lambda r: r.update(slot=float(g.SLOT))), **SYNTHETIC)
        with self.assertRaisesRegex(g.Refused, "ENTRY_MISMATCH"):
            g.validate(mutate(lambda r: r.update(entry=float(ENTRY))), **SYNTHETIC)
        with self.assertRaisesRegex(g.Refused, "BUDGET"):
            g.validate(mutate(lambda r: r.update(step_budget=float(BUDGET))), **SYNTHETIC)

    def test_empty_or_non_string_case_is_refused(self):
        for bad in ("", 1, None, ["case"]):
            with self.subTest(case=bad):
                item = mutate(lambda r: r.update(case=bad))
                with self.assertRaisesRegex(g.Refused, "FIELDS"):
                    g.validate(item, **SYNTHETIC)


class ExceptionSafetyTests(unittest.TestCase):
    """Malformed input of any shape must come back as Refused, never as a Python exception."""

    def test_deep_nesting_in_jsonl_is_refused(self):
        deep = "[" * 100000 + "]" * 100000  # json raises RecursionError here, which is not a ValueError
        with self.assertRaisesRegex(g.Refused, "FIELDS"):
            g.load_jsonl(deep)

    def test_deep_nesting_in_record_field_is_refused(self):
        item = record()
        nested = []
        tail = nested
        for _ in range(100000):
            tail.append([])
            tail = tail[0]
        item["case"] = nested
        item["record_digest"] = "0" * 64  # unsealed on purpose: sealing itself would recurse
        with self.assertRaisesRegex(g.Refused, "FIELDS"):
            g.validate(item, **SYNTHETIC)

    def test_non_text_input_to_load_jsonl_is_refused(self):
        for bad in (None, b'{"a": 1}', 1, ["{}"]):
            with self.subTest(input=bad), self.assertRaisesRegex(g.Refused, "FIELDS"):
                g.load_jsonl(bad)

    def test_non_dict_records_are_refused(self):
        for bad in ([1], (1,), None, "record", 1.5, float("nan"), object()):
            with self.subTest(record=bad), self.assertRaisesRegex(g.Refused, "FIELDS"):
                g.validate(bad, **SYNTHETIC)

    def test_nan_and_infinity_in_a_record_are_refused(self):
        for literal in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(literal=literal):
                text = json.dumps(record()).replace('"synthetic_case"', literal)  # reaches validate via the JSON path
                (item,) = g.load_jsonl(text)
                with self.assertRaisesRegex(g.Refused, "FIELDS"):
                    g.validate(item, **SYNTHETIC)

    def test_nan_in_a_byte_field_is_refused(self):
        for bad in (float("nan"), float("inf")):
            with self.subTest(value=bad):
                item = record()
                item["before"]["a"] = bad
                item["record_digest"] = "0" * 64  # unsealed: seal() itself refuses NaN/inf
                with self.assertRaisesRegex(g.Refused, "FIELDS"):
                    g.validate(item, **SYNTHETIC)
        # -0.0 serialises fine, so it is sealed and reaches the byte check
        with self.assertRaisesRegex(g.Refused, "FIELDS"):
            g.validate(mutate(lambda r: r["before"].update(a=-0.0)), **SYNTHETIC)

    def test_huge_integer_in_a_byte_field_is_refused(self):
        with self.assertRaisesRegex(g.Refused, "FIELDS"):
            g.validate(mutate(lambda r: r["before"].update(a=2**64)), **SYNTHETIC)
        with self.assertRaisesRegex(g.Refused, "BUDGET"):
            g.validate(mutate(lambda r: r.update(steps_used=2**70)), **SYNTHETIC)

    def test_non_string_key_in_a_nested_object_is_refused(self):
        item = record()
        item["case"] = {1: "int key", "b": 2}  # sort_keys cannot order mixed key types
        item["record_digest"] = "0" * 64
        with self.assertRaisesRegex(g.Refused, "FIELDS"):
            g.validate(item, **SYNTHETIC)

    def test_non_string_top_level_key_is_refused(self):
        item = record()
        item[1] = 2
        with self.assertRaisesRegex(g.Refused, "FIELDS"):
            g.validate(item, **SYNTHETIC)

    def test_duplicate_key_in_nested_object_is_refused(self):
        text = json.dumps(record()).replace('"io": {', '"io": {"observation": "NOT_MEASURED", "observation": "NOT_MEASURED", ', 1)
        with self.assertRaisesRegex(g.Refused, "FIELDS"):
            g.load_jsonl(text)

    def test_malformed_core_evidence_is_refused(self):
        def evidence(case):
            return {"geargrafx_revision": CORE, "cases": [case]}

        observed = {"before": state(), "after": state()}
        bad_cases = {
            "not an object": "plugin_ex_getver",
            "missing target": {"name": "plugin_ex_getver", "status": {}, "observed": {}},
            "missing status": {"name": "plugin_ex_getver", "target": "api_slot_1E", "observed": {"warm": observed}},
            "observed not an object": {"name": "plugin_ex_getver", "target": "api_slot_1E",
                                       "status": {"warm": "RETURNED"}, "observed": ["warm"]},
            "missing after state": {"name": "plugin_ex_getver", "target": "api_slot_1E",
                                    "status": {"warm": "RETURNED"}, "observed": {"warm": {"before": state()}}},
        }
        for label, case in bad_cases.items():
            with self.subTest(case=label), self.assertRaisesRegex(g.Refused, "FIELDS"):
                g.from_core_evidence(evidence(case), case_name="plugin_ex_getver", rom_sha256=ROM, entry=ENTRY,
                                     harness_revision=HARNESS, step_budget=BUDGET)
        for label, bad in (("no evidence", None), ("no cases key", {"geargrafx_revision": CORE})):
            with self.subTest(evidence=label), self.assertRaisesRegex(g.Refused, "FIELDS"):
                g.from_core_evidence(bad, case_name="plugin_ex_getver", rom_sha256=ROM, entry=ENTRY,
                                     harness_revision=HARNESS, step_budget=BUDGET)


if __name__ == "__main__":
    unittest.main()
