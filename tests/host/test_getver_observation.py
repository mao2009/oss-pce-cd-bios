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


if __name__ == "__main__":
    unittest.main()
