"""Host tests for the CD_BASE slot $02 observation catalogue and sanitized record schema (#186).

Synthetic/no-execution. Nothing here runs the genuine callee, reads a ROM or disc, or establishes
a BIOS ABI. The tests pin: the observable catalogue's tier split (what needs a real JSR), the
record validator's fail-closed behaviour (leaks, over-claims, evidence-class mixing), and the
stat-derived media classifier (structural booleans and equivalence classes only).
"""
import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import cd_base_slot02_observation as O  # noqa: E402
import reference_jsr_gate as G  # noqa: E402

PINNED = "b49ae82a012eade566d72e9d61c9d95a5caa4da3"


def caller_value(**over):
    value = {"verdict": "API_ENTRY_CONFIRMED", "first_table_slot": 2, "method": "JSR", "in_table": True,
             "return_matches_caller": True}
    value.update(over)
    return value


def caller_record(**over):
    return O.new_record("COMMERCIAL_CALLER_ENTRY", "MEASURED", "CALLER_SIDE_ENTRY_ONLY",
                        {"O5_caller_first_api": {"status": "MEASURED", "value": caller_value(**over)}},
                        tool="geargrafx_api_entry")


class CatalogueTests(unittest.TestCase):
    def test_real_jsr_items_are_exactly_the_callee_facts(self):
        real = sorted(k for k, v in O.OBSERVABLES.items() if v["tier"] == "REAL_JSR")
        self.assertEqual(real, ["O10_callee_mpr_ram", "O11_reset_retention", "O12_failure_retry",
                                "O7_callee_input_bytes", "O8_read_lba", "O9_callee_return"])

    def test_no_jsr_tiers_cover_the_rest(self):
        for k, v in O.OBSERVABLES.items():
            self.assertEqual(v["tier"] in O.NO_JSR_TIERS, v["tier"] != "REAL_JSR", k)

    def test_every_tier_has_an_evidence_class(self):
        carried = {t for tiers in O.CLASS_TIERS.values() for t in tiers}
        self.assertEqual(carried, {v["tier"] for v in O.OBSERVABLES.values()})

    def test_table_entry_matches_stride(self):
        self.assertEqual(int(O.TABLE_ENTRY[1:], 16), 0xE000 + 3 * 2)

    def test_verified_not_representable(self):
        self.assertNotIn("VERIFIED", O.STATUSES)
        self.assertNotIn("DEV_CALLABLE", O.STATUSES)


class GateConsistencyTests(unittest.TestCase):
    def test_gate_still_refuses_slot_02(self):
        ledger, contracts = G.load()
        rec = G.resolve(2, ledger, contracts)
        self.assertEqual(rec["execution"], "BLOCKED")
        self.assertFalse(rec["execution_permitted"])
        self.assertEqual(rec["jsr_count"], 0)
        with self.assertRaises(G.GateRefused):
            G.require_allowed("slot-02")

    def test_genuine_callee_record_is_blocked_only(self):
        rec = O.blocked_genuine_record("GATE_REFUSES_SLOT_02")
        self.assertEqual(rec["status"], "BLOCKED")
        self.assertTrue(all(i["status"] == "BLOCKED" for i in rec["items"].values()))

    def test_genuine_callee_measurement_rejected(self):
        for status in ("MEASURED", "SOURCE_CONFIRMED"):
            rec = O.blocked_genuine_record("X")
            rec["items"]["O8_read_lba"] = {"status": status, "value": 1}
            with self.assertRaises(ValueError, msg=status):
                O.validate_record(rec)
        rec = O.blocked_genuine_record("X")
        rec["status"] = "MEASURED"
        with self.assertRaises(ValueError):
            O.validate_record(rec)

    def test_real_jsr_tier_cannot_hide_in_other_classes(self):
        for cls in ("HOST_ARITHMETIC", "SYNTHETIC_REAL_CORE", "COMMERCIAL_STAT_ONLY", "COMMERCIAL_CALLER_ENTRY"):
            with self.assertRaises(ValueError, msg=cls):
                O.new_record(cls, "BLOCKED", "X", {"O8_read_lba": {"status": "BLOCKED", "value": None}},
                             core_revision=PINNED if cls == "SYNTHETIC_REAL_CORE" else None)

    def test_nonzero_jsr_count_rejected_everywhere(self):
        for cls in O.EVIDENCE_CLASSES:
            rec = O.new_record(cls, "NOT_RUN", "NONE_RUN", core_revision=PINNED if cls == "SYNTHETIC_REAL_CORE" else None)
            rec["jsr_count"] = 1
            with self.assertRaises(ValueError, msg=cls):
                O.validate_record(rec)


class RecordValidationTests(unittest.TestCase):
    def test_caller_entry_record_valid(self):
        self.assertEqual(caller_record()["slot"], "$02")

    def test_caller_entry_cannot_carry_callee_tier(self):
        with self.assertRaises(ValueError):
            O.new_record("COMMERCIAL_CALLER_ENTRY", "MEASURED", "MIX",
                         {"O8_read_lba": {"status": "MEASURED", "value": 3}})

    def test_host_arithmetic_cannot_carry_caller_entry(self):
        with self.assertRaises(ValueError):
            O.new_record("HOST_ARITHMETIC", "MEASURED", "MIX",
                         {"O5_caller_first_api": {"status": "MEASURED", "value": caller_value()}})

    def test_unmeasured_item_must_be_null(self):
        with self.assertRaises(ValueError):
            O.new_record("HOST_ARITHMETIC", "NOT_RUN", "X",
                         {"O3_candidate_classes": {"status": "UNKNOWN", "value": [["ABS_LBA"]]}})

    def test_valued_item_needs_value(self):
        with self.assertRaises(ValueError):
            O.new_record("HOST_ARITHMETIC", "MEASURED", "X",
                         {"O3_candidate_classes": {"status": "MEASURED", "value": None}})

    def test_bad_caller_value_shapes(self):
        for over in ({"method": "CALL"}, {"verdict": "OK"}, {"first_table_slot": 99}, {"in_table": 1},
                     {"return_matches_caller": "yes"}, {"first_table_slot": 2.0}):
            with self.assertRaises(ValueError, msg=over):
                caller_record(**over)

    def test_o5_coherence(self):
        bad = (
            {"method": "NONE"},                                            # confirmed entry with no method
            {"method": "NONE", "first_table_slot": None, "in_table": False, "return_matches_caller": None},
            {"method": "JSR", "in_table": False},                          # JSR outside the table
            {"method": "JMP", "first_table_slot": None},                   # table entry without a slot
            {"method": "DIRECT", "in_table": False, "first_table_slot": 2},  # non-table entry with a slot
        )
        for over in bad:
            with self.assertRaises(ValueError, msg=over):
                caller_record(**over)
        caller_record(method="DIRECT", in_table=False, first_table_slot=None, return_matches_caller=None)
        caller_record(verdict="PRE_API_BLOCKER_OBSERVED", method="NONE", in_table=False,
                      first_table_slot=None, return_matches_caller=None)

    def test_extra_value_key_rejected(self):
        rec = caller_record()
        rec["items"]["O5_caller_first_api"]["value"]["caller_pc"] = 0x4000
        with self.assertRaises(ValueError):
            O.validate_record(rec)

    def test_value_shapes_for_every_valued_observable(self):
        cases = {
            "O1_table_slot_address": (57350, "$E006", [True], {"a": 1}),
            "O3_candidate_classes": (12345, ["ABS_LBA"], [["0xE006"]], [[]], {"file_path": "C:/x"}),
            "O4_instrument_validity": (1, "yes", None),
            "O6_caller_sets_regs": (0x12, {"a": 1}, "A=1"),
        }
        tier_class = {"O1_table_slot_address": "HOST_ARITHMETIC", "O3_candidate_classes": "HOST_ARITHMETIC",
                      "O4_instrument_validity": "SYNTHETIC_REAL_CORE", "O6_caller_sets_regs": "COMMERCIAL_CALLER_ENTRY"}
        for name, values in cases.items():
            cls = tier_class[name]
            for value in values:
                with self.assertRaises(ValueError, msg=(name, value)):
                    O.new_record(cls, "MEASURED", "X", {name: {"status": "MEASURED", "value": value}},
                                 core_revision=PINNED if cls == "SYNTHETIC_REAL_CORE" else None)
        O.new_record("HOST_ARITHMETIC", "MEASURED", "X",
                     {"O3_candidate_classes": {"status": "MEASURED", "value": [["ABS_LBA"]]}})
        O.new_record("HOST_ARITHMETIC", "NOT_RUN", "X",
                     {"O1_table_slot_address": {"status": "SOURCE_CONFIRMED", "value": True}})

    def test_leak_scan_directly(self):
        for key in ("cue_path", "sha256", "filePath", "sectorData", "titleId", "romName", "SaveFile",
                    "entryAddress", "caller_pc", "LBAValue", "payloadHex"):
            with self.assertRaises(ValueError, msg=key):
                O.check_no_leak_keys({"items": {"x": {key: 1}}})
        with self.assertRaises(ValueError):
            O.check_no_leak_keys({1: "x"})
        # fixed schema vocabulary is allowed even where it contains a fragment ('from', 'zero')
        O.check_no_leak_keys(O.new_record("COMMERCIAL_STAT_ONLY", "MEASURED", "X", {
            "O2_media_structure": {"status": "MEASURED", "value": O.media_structure(0, 150)}}))

    def test_leak_keys_rejected_in_record(self):
        for key in ("cue_path", "sha256", "title", "sectorBytes", "rom_name"):
            rec = caller_record()
            rec[key] = "x"
            with self.assertRaises(ValueError, msg=key):
                O.validate_record(rec)
            rec = caller_record()
            rec["items"][key] = {"status": "UNKNOWN", "value": None}
            with self.assertRaises(ValueError, msg=key):
                O.validate_record(rec)

    def test_free_text_fields_rejected(self):
        for field, bad in (("reason", "C:\\Users\\x\\rom\\g.bin 0123456789abcdef"), ("reason", "lower case"),
                           ("reason", "X" * 61), ("tool", "/home/u/rom/g.bin"), ("tool", "Tool Name"),
                           ("core_revision", "b49ae82a/home/u/secret"), ("core_revision", "b49ae82a")):
            rec = caller_record()
            rec[field] = bad
            with self.assertRaises(ValueError, msg=(field, bad)):
                O.validate_record(rec)

    def test_overclaim_words_rejected(self):
        for word in ("VERIFIED", "V_E_R_I_F_I_E_D", "DEV_CALLABLE", "COMPATIBLE"):
            with self.assertRaises(ValueError, msg=word):
                O.new_record("HOST_ARITHMETIC", "NOT_RUN", f"STATUS_{word}")
        with self.assertRaises(ValueError):
            O.new_record("HOST_ARITHMETIC", "NOT_RUN", "X", tool="verified")
        with self.assertRaises(ValueError):
            O.new_record("HOST_ARITHMETIC", "VERIFIED", "X")

    def test_record_status_tied_to_items(self):
        with self.assertRaises(ValueError):
            O.new_record("HOST_ARITHMETIC", "MEASURED", "X")  # MEASURED with no measured item
        with self.assertRaises(ValueError):
            O.new_record("HOST_ARITHMETIC", "NOT_RUN", "X",
                         {"O3_candidate_classes": {"status": "MEASURED", "value": [["ABS_LBA"]]}})

    def test_synthetic_real_core_requires_pinned_revision(self):
        with self.assertRaises(ValueError):
            O.new_record("SYNTHETIC_REAL_CORE", "NOT_RUN", "X")
        with self.assertRaises(ValueError):
            O.new_record("SYNTHETIC_REAL_CORE", "NOT_RUN", "X", core_revision="deadbeef")
        O.new_record("SYNTHETIC_REAL_CORE", "NOT_RUN", "X", core_revision=PINNED)
        with self.assertRaises(ValueError):
            O.new_record("HOST_ARITHMETIC", "NOT_RUN", "X", core_revision=PINNED)

    def test_unknown_and_missing_keys(self):
        rec = caller_record()
        extra = copy.deepcopy(rec)
        extra["extra"] = 1
        with self.assertRaises(ValueError):
            O.validate_record(extra)
        missing = copy.deepcopy(rec)
        del missing["jsr_count"]
        with self.assertRaises(ValueError):
            O.validate_record(missing)
        wrong_slot = copy.deepcopy(rec)
        wrong_slot["slot"] = "$03"
        with self.assertRaises(ValueError):
            O.validate_record(wrong_slot)
        with self.assertRaises(ValueError):
            O.validate_record([])


class MediaClassifierTests(unittest.TestCase):
    def test_structure_booleans_only(self):
        s = O.media_structure(0, 150)
        self.assertEqual(s, {"index01_nonzero": True, "index00_differs_from_zero": False,
                             "data_tracks_after_first": False})
        s2 = O.media_structure(150, 300)
        self.assertTrue(s2["index00_differs_from_zero"] and s2["data_tracks_after_first"])
        rec = O.new_record("COMMERCIAL_STAT_ONLY", "MEASURED", "STAT_ONLY",
                           {"O2_media_structure": {"status": "MEASURED", "value": s}})
        self.assertEqual(rec["evidence_class"], "COMMERCIAL_STAT_ONLY")
        with self.assertRaises(ValueError):
            O.new_record("COMMERCIAL_STAT_ONLY", "MEASURED", "LEAK",
                         {"O2_media_structure": {"status": "MEASURED",
                                                 "value": {"index01_nonzero": 150, "index00_differs_from_zero": False,
                                                           "data_tracks_after_first": False}}})

    def test_single_track_media_cannot_separate_abs_from_index00(self):
        # D1-like (INDEX00 = 0, INDEX01 = 150): INDEX00_START coincides with ABS_LBA.
        parts = O.candidate_partition(0, 150)
        self.assertIn(sorted(["ABS_LBA", "INDEX00_START_PLUS_BLOCK"]), parts)
        self.assertEqual(len(parts), 4)

    def test_later_data_track_separates_index00(self):
        # D2-like (INDEX00 = 150, INDEX01 = 300): all five registry candidates separable.
        self.assertEqual(len(O.candidate_partition(150, 300)), 5)

    def test_index01_zero_collapses_three(self):
        parts = O.candidate_partition(0, 0)
        self.assertIn(sorted(["ABS_LBA", "INDEX00_START_PLUS_BLOCK", "TRACK_START_PLUS_BLOCK"]), parts)
        self.assertEqual(len(parts), 3)

    def test_partition_is_valid_o3_value(self):
        for args in ((0, 150), (150, 300), (0, 0)):
            O.new_record("HOST_ARITHMETIC", "MEASURED", "X",
                         {"O3_candidate_classes": {"status": "MEASURED", "value": O.candidate_partition(*args)}})


if __name__ == "__main__":
    unittest.main()
