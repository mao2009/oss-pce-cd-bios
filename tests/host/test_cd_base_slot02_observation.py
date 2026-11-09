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


def caller_record(**over):
    value = {"verdict": "API_ENTRY_CONFIRMED", "slot": 2, "method": "JSR", "in_table": True,
             "return_matches_caller": True}
    value.update(over)
    return O.new_record("COMMERCIAL_CALLER_ENTRY", "MEASURED", "caller-side entry only",
                        {"O5_caller_first_api": {"status": "MEASURED", "value": value}},
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
        rec = O.blocked_genuine_record("gate refuses slot $02")
        self.assertEqual(rec["status"], "BLOCKED")
        self.assertTrue(all(i["status"] == "BLOCKED" for i in rec["items"].values()))

    def test_genuine_callee_measurement_rejected(self):
        rec = O.blocked_genuine_record("x")
        rec["items"]["O8_read_lba"] = {"status": "MEASURED", "value": 1}
        with self.assertRaises(ValueError):
            O.validate_record(rec)
        rec = O.blocked_genuine_record("x")
        rec["status"] = "MEASURED"
        with self.assertRaises(ValueError):
            O.validate_record(rec)

    def test_nonzero_jsr_count_rejected_everywhere(self):
        for cls in O.EVIDENCE_CLASSES:
            rec = O.new_record(cls, "NOT_RUN", "none", core_revision=None if cls != "SYNTHETIC_REAL_CORE" else "b49ae82a")
            rec["jsr_count"] = 1
            with self.assertRaises(ValueError, msg=cls):
                O.validate_record(rec)


class RecordValidationTests(unittest.TestCase):
    def test_caller_entry_record_valid(self):
        self.assertEqual(caller_record()["slot"], "$02")

    def test_caller_entry_cannot_carry_callee_tier(self):
        with self.assertRaises(ValueError):
            O.new_record("COMMERCIAL_CALLER_ENTRY", "MEASURED", "mix",
                         {"O8_read_lba": {"status": "MEASURED", "value": 3}})

    def test_host_arithmetic_cannot_carry_caller_entry(self):
        with self.assertRaises(ValueError):
            O.new_record("HOST_ARITHMETIC", "MEASURED", "mix",
                         {"O5_caller_first_api": {"status": "MEASURED", "value": None}})

    def test_unmeasured_item_must_be_null(self):
        with self.assertRaises(ValueError):
            O.new_record("HOST_ARITHMETIC", "NOT_RUN", "x",
                         {"O3_candidate_classes": {"status": "UNKNOWN", "value": 5}})

    def test_bad_caller_value_shapes(self):
        for over in ({"method": "CALL"}, {"verdict": "OK"}, {"slot": 99}, {"in_table": 1},
                     {"return_matches_caller": "yes"}):
            with self.assertRaises(ValueError, msg=over):
                caller_record(**over)

    def test_extra_value_key_rejected(self):
        rec = caller_record()
        rec["items"]["O5_caller_first_api"]["value"]["caller_pc"] = 0x4000
        with self.assertRaises(ValueError):
            O.validate_record(rec)

    def test_leak_keys_rejected(self):
        for key in ("cue_path", "sha256", "title", "sector_bytes", "rom_name", "save_file", "entry_address"):
            rec = caller_record()
            rec["items"][key] = {"status": "UNKNOWN", "value": None}
            with self.assertRaises(ValueError, msg=key):
                O.validate_record(rec)
            rec = caller_record()
            rec[key] = "x"
            with self.assertRaises(ValueError, msg=key):
                O.validate_record(rec)

    def test_overclaim_words_rejected(self):
        for word in ("VERIFIED", "DEV_CALLABLE", "compatible"):
            with self.assertRaises(ValueError, msg=word):
                O.new_record("HOST_ARITHMETIC", "NOT_RUN", f"status {word}")
        with self.assertRaises(ValueError):
            O.new_record("HOST_ARITHMETIC", "VERIFIED", "x")

    def test_caller_regs_public_value_is_boolean_only(self):
        for bad in (0x12, {"a": 1}, "A=1"):
            with self.assertRaises(ValueError, msg=bad):
                O.new_record("COMMERCIAL_CALLER_ENTRY", "MEASURED", "x",
                             {"O6_caller_sets_regs": {"status": "MEASURED", "value": bad}})
        O.new_record("COMMERCIAL_CALLER_ENTRY", "MEASURED", "x",
                     {"O6_caller_sets_regs": {"status": "MEASURED", "value": True}})

    def test_synthetic_real_core_requires_pinned_revision(self):
        with self.assertRaises(ValueError):
            O.new_record("SYNTHETIC_REAL_CORE", "NOT_RUN", "x")
        with self.assertRaises(ValueError):
            O.new_record("SYNTHETIC_REAL_CORE", "NOT_RUN", "x", core_revision="deadbeef")
        O.new_record("SYNTHETIC_REAL_CORE", "NOT_RUN", "x", core_revision="b49ae82a012eade566d72e9d61c9d95a5caa4da3")

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


class MediaClassifierTests(unittest.TestCase):
    def test_structure_booleans_only(self):
        s = O.media_structure(0, 150)
        self.assertEqual(s, {"index01_nonzero": True, "index00_differs_from_zero": False,
                             "data_tracks_after_first": False})
        s2 = O.media_structure(150, 300)
        self.assertTrue(s2["index00_differs_from_zero"] and s2["data_tracks_after_first"])
        rec = O.new_record("COMMERCIAL_STAT_ONLY", "MEASURED", "stat-only",
                           {"O2_media_structure": {"status": "MEASURED", "value": s}})
        self.assertEqual(rec["evidence_class"], "COMMERCIAL_STAT_ONLY")
        with self.assertRaises(ValueError):
            O.new_record("COMMERCIAL_STAT_ONLY", "MEASURED", "leak",
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
        parts = O.candidate_partition(150, 300)
        self.assertEqual(len(parts), 5)

    def test_index01_zero_collapses_three(self):
        parts = O.candidate_partition(0, 0)
        self.assertIn(sorted(["ABS_LBA", "INDEX00_START_PLUS_BLOCK", "TRACK_START_PLUS_BLOCK"]), parts)

    def test_classifier_returns_names_not_values(self):
        for part in O.candidate_partition(0, 150):
            for name in part:
                self.assertIn(name, O.C.CANDIDATE_NAMES)


if __name__ == "__main__":
    unittest.main()
