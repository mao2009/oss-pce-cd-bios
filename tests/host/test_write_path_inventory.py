"""Host tests for the unmonitored write-path inventory and the JSR observation schema (Issue #173).

Synthetic only: exercises tools/write_path_inventory.py and tools/jsr_observation.py on the committed
inventory and on hand-built fixtures. No emulator, no BIOS and no ROM are read. The inventory must
stay fail-closed: every row BLOCKED, evidence present, and ST/IRQ/MPR/MMIO/RAM paths covered. An
observation must never be read as real-core evidence unless its provenance says so, and a fixture
can never claim it.
"""
import copy
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import jsr_observation as O
import write_path_inventory as W
import reference_jsr_gate as G

PINNED = json.loads((ROOT / "tools" / "dependencies.json").read_text())["geargrafx"]["revision"]
INVENTORY = json.loads((ROOT / "spec" / "write_path_inventory.json").read_text())


def fresh():
    return copy.deepcopy(INVENTORY)


def row(inventory, row_id):
    return next(r for r in inventory["rows"] if r["id"] == row_id)


def real_observation():
    """Synthetic-BIOS real-core shaped observation (values illustrative, not a measurement)."""
    return {
        "schema": O.SCHEMA,
        "provenance": {"kind": "REAL_CORE_EXECUTION", "core_revision": PINNED, "bios": "SYNTHETIC",
                       "real_core_claim": True},
        "slot": "$1E",
        "outcome": {"status": "ok", "reason": "", "stop_pc": 0x3E05, "instructions_executed": 3,
                    "budget": 100, "returned": True, "return_pc": 0x3E05},
        "registers": {"a": 0xAA, "x": 0x55, "y": 0xCC, "p": 4, "sp": 0xFD, "pc": 0x3E05},
        "mpr": [0, 0xF8, 0, 0, 0, 0, 0, 0],
        "ram_diff": [],
        "irq": {"status": "UNKNOWN"},
        "guard": {"detected": False, "detected_pc": None, "mpr_changed": False},
    }


def fixture_observation():
    obs = real_observation()
    obs["provenance"] = {"kind": "HOST_FIXTURE", "core_revision": None, "bios": "SYNTHETIC",
                         "real_core_claim": False}
    return obs


class WritePathInventoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="wpi-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def claimed(self, inventory, row_id, kind="STATIC_SOURCE_REFERENCE", marker=True):
        """Give row_id a static prevention claim whose evidence file (in the temp root) carries a run marker."""
        text = f"measurement-run-id: RUN-0123456789AB {kind}\n" if marker else "no run recorded here\n"
        (self.tmp / "run.txt").write_text(text, encoding="utf-8")
        row(inventory, row_id).update(monitor_status="PREVENTED_STATIC", prevents_before_side_effect="YES",
                                      detection_point="BEFORE_RUN", measurement="STATIC_SOURCE_REFERENCE",
                                      evidence=["run.txt"])
        return inventory

    def validate_in_tmp(self, inventory):
        for r in inventory["rows"]:
            for item in r["evidence"]:
                if (ROOT / item).is_file() and not (self.tmp / item).exists():
                    (self.tmp / item).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy(ROOT / item, self.tmp / item)
        return W.validate(inventory, PINNED, root=self.tmp)

    def test_committed_inventory_validates_and_stays_blocked(self):
        summary, _ = W.load()
        self.assertEqual(summary["gate_status"], "BLOCKED")
        self.assertEqual(summary["rows"], len(INVENTORY["rows"]))
        self.assertGreater(summary["unresolved"], 0)

    def test_committed_inventory_claims_no_prevention_without_a_run_identifier(self):
        for r in INVENTORY["rows"]:
            with self.subTest(row=r["id"]):
                self.assertIn(r["prevents_before_side_effect"], ("NO", "UNKNOWN"))

    def test_st_mmio_irq_paths_are_recorded_unresolved(self):
        for row_id in ("W05", "W06", "W07"):
            self.assertIn(row(INVENTORY, row_id)["monitor_status"],
                          ("UNMONITORED", "DETECTED_AFTER_SIDE_EFFECT"))
            self.assertEqual(row(INVENTORY, row_id)["prevents_before_side_effect"], "NO")
        self.assertEqual(row(INVENTORY, "W05")["monitor_status"], "UNMONITORED")
        self.assertEqual(row(INVENTORY, "W14")["monitor_status"], "UNMONITORED")

    def test_every_row_is_blocked_and_unlock_is_impossible(self):
        for r in INVENTORY["rows"]:
            self.assertEqual(r["gate_status"], "BLOCKED")
        self.assertEqual(G.resolve(1, *G.load())["execution"], "BLOCKED")

    def test_mutation_unlocking_a_row_is_rejected(self):
        inv = fresh()
        row(inv, "W05")["gate_status"] = "ALLOWED"
        with self.assertRaisesRegex(ValueError, "gate_status must be BLOCKED"):
            W.validate(inv, PINNED)

    def test_mutation_prevention_claim_without_measurement_is_rejected(self):
        inv = self.claimed(fresh(), "W03")
        self.assertEqual(self.validate_in_tmp(inv)["unresolved"], 17)
        row(inv, "W03")["measurement"] = "NOT_MEASURED"
        with self.assertRaisesRegex(ValueError, "W03: prevention claimed without measurement"):
            self.validate_in_tmp(inv)

    def test_mutation_prevented_status_with_no_prevention_is_rejected(self):
        inv = fresh()
        row(inv, "W06")["monitor_status"] = "PREVENTED_MEASURED"
        with self.assertRaisesRegex(ValueError, "prevention status with non-prevention value|NO prevention"):
            W.validate(inv, PINNED)

    def test_mutation_missing_evidence_file_is_rejected(self):
        inv = fresh()
        row(inv, "W05")["evidence"] = ["tests/integration/does_not_exist.py"]
        with self.assertRaisesRegex(ValueError, "evidence file missing"):
            W.validate(inv, PINNED)

    def test_mutation_absolute_or_parent_evidence_path_is_rejected(self):
        inv = fresh()
        row(inv, "W05")["evidence"] = ["../outside.py"]
        with self.assertRaisesRegex(ValueError, "repository-relative"):
            W.validate(inv, PINNED)

    def test_mutation_dropped_category_is_rejected(self):
        inv = fresh()
        for r in inv["rows"]:
            if r["category"] == "DEVICE":
                r["category"] = "RAM"
        with self.assertRaisesRegex(ValueError, "missing categories"):
            W.validate(inv, PINNED)

    def test_mutation_deleted_st_bypass_row_is_rejected(self):
        inv = fresh()
        inv["rows"] = [r for r in inv["rows"] if "ST1" not in r["path"] and "ST0" not in r["path"]
                       and "ST2" not in r["path"]]
        with self.assertRaisesRegex(ValueError, "missing categories|required path marker missing"):
            W.validate(inv, PINNED)

    def test_mutation_revision_drift_is_rejected(self):
        inv = fresh()
        inv["pinned_core_revision"] = "0" * 40
        with self.assertRaisesRegex(ValueError, "drift"):
            W.validate(inv, PINNED)

    def test_mutation_duplicate_id_and_unknown_enum_are_rejected(self):
        inv = fresh()
        inv["rows"].append(copy.deepcopy(inv["rows"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate row id"):
            W.validate(inv, PINNED)
        inv = fresh()
        row(inv, "W02")["detection_point"] = "SOMETIMES"
        with self.assertRaisesRegex(ValueError, "unknown detection_point"):
            W.validate(inv, PINNED)

    def test_mutation_detected_after_side_effect_cannot_claim_prevention(self):
        inv = fresh()
        row(inv, "W06")["prevents_before_side_effect"] = "YES"
        with self.assertRaisesRegex(ValueError, "W06: post-side-effect detection cannot claim prevention"):
            W.validate(inv, PINNED)

    def test_mutation_unknown_status_cannot_claim_prevention(self):
        inv = fresh()
        row(inv, "W11")["prevents_before_side_effect"] = "YES"
        with self.assertRaisesRegex(ValueError, "W11: UNKNOWN status must not claim"):
            W.validate(inv, PINNED)

    def test_mutation_per_write_claim_on_unknown_block_move_is_rejected(self):
        inv = fresh()
        row(inv, "W15")["prevents_before_side_effect"] = "PER_WRITE"
        row(inv, "W15")["measurement"] = "REAL_CORE_SYNTHETIC"  # isolate the status rule from the measurement rule
        with self.assertRaisesRegex(ValueError, "W15: UNKNOWN status must not claim"):
            W.validate(inv, PINNED)

    def test_w09_w10_detection_point_is_not_recorded(self):
        for row_id in ("W09", "W10"):
            with self.subTest(row=row_id):
                self.assertEqual(row(INVENTORY, row_id)["monitor_status"], "UNKNOWN")
                self.assertEqual(row(INVENTORY, row_id)["detection_point"], "UNKNOWN")

    def test_w15_block_move_is_unknown_and_claims_no_per_write_stop(self):
        self.assertEqual(row(INVENTORY, "W15")["monitor_status"], "UNKNOWN")
        self.assertEqual(row(INVENTORY, "W15")["prevents_before_side_effect"], "UNKNOWN")

    def test_w04_measurement_matches_the_unreached_branch(self):
        self.assertEqual(row(INVENTORY, "W04")["measurement"], "STATIC_SOURCE_REFERENCE")

    def test_w02_path_is_limited_and_unmeasured_remainder_is_its_own_row(self):
        self.assertNotIn("pages 3-7", row(INVENTORY, "W02")["path"])
        remainder = [r for r in INVENTORY["rows"] if r["category"] == "RAM" and r["id"] != "W02"
                     and r["monitor_status"] == "UNKNOWN"]
        self.assertTrue(remainder, "unmeasured CPU store addresses must stay visible as UNKNOWN")

    def test_w17_path_does_not_claim_vdc_dma_bypass(self):
        path = row(INVENTORY, "W17")["path"]
        self.assertNotIn("VDC DMA", path)
        self.assertIn("CD/ADPCM", path)

    def test_doc_status_column_matches_inventory(self):
        doc = (ROOT / "docs" / "173-write-path-inventory.md").read_text(encoding="utf-8")
        lines = doc.splitlines()
        for r in INVENTORY["rows"]:
            with self.subTest(row=r["id"]):
                line = next(l for l in lines if l.startswith(f"| {r['id']} |"))
                self.assertIn(r["monitor_status"], line)

    def test_mutation_unmonitored_or_permitted_cannot_claim_yes(self):
        inv = fresh()
        row(inv, "W05")["prevents_before_side_effect"] = "YES"
        with self.assertRaisesRegex(ValueError, "W05: UNMONITORED or PERMITTED_UNISOLATED cannot claim prevention"):
            W.validate(inv, PINNED)

    def test_mutation_permitted_cannot_claim_yes(self):
        inv = fresh()
        row(inv, "W16")["prevents_before_side_effect"] = "YES"
        with self.assertRaisesRegex(ValueError, "W16: UNMONITORED or PERMITTED_UNISOLATED cannot claim prevention"):
            W.validate(inv, PINNED)

    def test_mutation_unmonitored_block_cannot_claim_per_write(self):
        inv = fresh()
        row(inv, "W14")["prevents_before_side_effect"] = "PER_WRITE"
        with self.assertRaisesRegex(ValueError, "W14: UNMONITORED or PERMITTED_UNISOLATED cannot claim prevention"):
            W.validate(inv, PINNED)

    def test_mutation_prevention_needs_a_pre_side_effect_detection_point(self):
        inv = self.claimed(fresh(), "W03")
        row(inv, "W03")["detection_point"] = "NONE"
        with self.assertRaisesRegex(ValueError, "W03: prevention requires a pre-side-effect detection point"):
            self.validate_in_tmp(inv)

    def test_mutation_detected_after_side_effect_needs_after_detection_point(self):
        inv = fresh()
        row(inv, "W06")["detection_point"] = "BEFORE_STORE"
        with self.assertRaisesRegex(ValueError, "W06: DETECTED_AFTER_SIDE_EFFECT requires detection point AFTER_SIDE_EFFECT"):
            W.validate(inv, PINNED)
        inv = fresh()
        row(inv, "W06")["detection_point"] = "INSTRUCTION_BOUNDARY"
        with self.assertRaisesRegex(ValueError, "W06: DETECTED_AFTER_SIDE_EFFECT requires detection point AFTER_SIDE_EFFECT"):
            W.validate(inv, PINNED)

    def test_mutation_partial_known_pc_claim_after_side_effect_is_rejected(self):
        # W12 as previously recorded: a partial (KNOWN_PC_ONLY) stop claimed on a row detected after the store landed.
        inv = fresh()
        row(inv, "W12")["prevents_before_side_effect"] = "KNOWN_PC_ONLY"
        with self.assertRaisesRegex(ValueError, "W12: post-side-effect detection cannot claim prevention"):
            W.validate(inv, PINNED)

    def test_mutation_partial_known_pc_claim_needs_pre_side_effect_point(self):
        inv = self.claimed(fresh(), "W03")
        row(inv, "W03")["prevents_before_side_effect"] = "KNOWN_PC_ONLY"
        row(inv, "W03")["detection_point"] = "NONE"
        with self.assertRaisesRegex(ValueError, "W03: prevention requires a pre-side-effect detection point"):
            self.validate_in_tmp(inv)

    def test_w12_records_no_prevention_and_stays_blocked(self):
        self.assertEqual(row(INVENTORY, "W12")["prevents_before_side_effect"], "NO")
        self.assertEqual(row(INVENTORY, "W12")["monitor_status"], "DETECTED_AFTER_SIDE_EFFECT")
        self.assertEqual(row(INVENTORY, "W12")["gate_status"], "BLOCKED")

    # Pinned allowlists: the inventory may not declare its own status, measurement or prevention vocabulary.
    def test_mutation_unapproved_prevention_value_declared_in_inventory_is_rejected(self):
        inv = fresh()
        inv["prevents_before_side_effect_values"].append("PARTIAL_PC")
        with self.assertRaisesRegex(ValueError, "must equal the fixed code allowlist"):
            W.validate(inv, PINNED)

    def test_mutation_undeclared_post_side_effect_status_name_is_rejected(self):
        # A new post-side-effect status name, declared in the inventory, must still be refused by the pinned set.
        inv = fresh()
        inv["monitor_status_values"].append("DETECTED_AFTER_SIDE_EFFECT_PARTIAL")
        row(inv, "W06")["monitor_status"] = "DETECTED_AFTER_SIDE_EFFECT_PARTIAL"
        row(inv, "W06")["prevents_before_side_effect"] = "KNOWN_PC_ONLY"
        row(inv, "W06")["detection_point"] = "BEFORE_STORE"
        with self.assertRaisesRegex(ValueError, "monitor_status_values must equal the fixed code set"):
            W.validate(inv, PINNED)

    def test_mutation_unpinned_late_detected_status_on_row_is_rejected(self):
        inv = fresh()
        row(inv, "W06")["monitor_status"] = "LATE_DETECTED"
        row(inv, "W06")["prevents_before_side_effect"] = "KNOWN_PC_ONLY"
        row(inv, "W06")["detection_point"] = "BEFORE_STORE"
        with self.assertRaisesRegex(ValueError, "W06: unknown monitor_status"):
            W.validate(inv, PINNED)

    def test_mutation_monitor_status_values_declared_drift_is_rejected(self):
        inv = fresh()
        inv["monitor_status_values"].append("LATE_DETECTED")
        with self.assertRaisesRegex(ValueError, "monitor_status_values must equal the fixed code set"):
            W.validate(inv, PINNED)

    def test_mutation_measurement_values_declared_drift_is_rejected(self):
        inv = fresh()
        inv["measurement_values"].append("PLANNED")
        with self.assertRaisesRegex(ValueError, "measurement_values must equal the fixed code set"):
            W.validate(inv, PINNED)

    def test_mutation_undefined_measurement_cannot_satisfy_prevention(self):
        # PLANNED is declared by the inventory and set on a claimed row: it must still not satisfy the claim.
        inv = self.claimed(fresh(), "W03")
        inv["measurement_values"].append("PLANNED")
        row(inv, "W03")["measurement"] = "PLANNED"
        with self.assertRaisesRegex(ValueError, "measurement_values must equal the fixed code set|unknown measurement"):
            self.validate_in_tmp(inv)

    def test_mutation_undefined_measurement_on_row_is_rejected(self):
        inv = self.claimed(fresh(), "W03")
        row(inv, "W03")["measurement"] = "PLANNED"
        with self.assertRaisesRegex(ValueError, "W03: unknown measurement"):
            self.validate_in_tmp(inv)

    def test_valid_static_claim_with_content_validated_run_marker_is_accepted(self):
        inv = self.claimed(fresh(), "W03")
        self.assertEqual(self.validate_in_tmp(inv)["rows"], len(INVENTORY["rows"]))

    def test_prevented_static_with_complete_evidence_is_accepted_and_stays_blocked(self):
        # Positive: status, pre-side-effect point, static measurement, a non-placeholder marker of that kind.
        inv = self.claimed(fresh(), "W03")
        summary = self.validate_in_tmp(inv)
        self.assertEqual(summary["status_counts"].get("PREVENTED_STATIC"), 1)
        self.assertEqual(summary["gate_status"], "BLOCKED")
        self.assertEqual(row(inv, "W03")["gate_status"], "BLOCKED")

    def test_prevented_static_with_empty_evidence_list_is_rejected(self):
        inv = self.claimed(fresh(), "W03")
        row(inv, "W03")["evidence"] = []
        with self.assertRaisesRegex(ValueError, "W03: evidence list empty"):
            self.validate_in_tmp(inv)

    def test_prevented_static_with_missing_evidence_file_is_rejected(self):
        inv = self.claimed(fresh(), "W03")
        row(inv, "W03")["evidence"] = ["run-not-present.txt"]
        with self.assertRaisesRegex(ValueError, "W03: evidence file missing"):
            self.validate_in_tmp(inv)

    def test_prevented_static_with_empty_evidence_file_is_rejected(self):
        inv = self.claimed(fresh(), "W03")
        (self.tmp / "run.txt").write_text("", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "W03: prevention claimed without content-validated run evidence"):
            self.validate_in_tmp(inv)

    def test_prevented_static_with_placeholder_run_id_is_rejected(self):
        for placeholder in ("RUN-000000000000", "RUN-FFFFFFFFFFFF"):
            with self.subTest(placeholder=placeholder):
                inv = self.claimed(fresh(), "W03")
                (self.tmp / "run.txt").write_text(f"measurement-run-id: {placeholder} STATIC_SOURCE_REFERENCE\n",
                                                  encoding="utf-8")
                with self.assertRaisesRegex(ValueError, f"W03: placeholder run id {placeholder}"):
                    self.validate_in_tmp(inv)

    def test_prevented_static_with_template_marker_is_rejected(self):
        # Template text is not hex, so it never matches the run-id format at all.
        inv = self.claimed(fresh(), "W03")
        (self.tmp / "run.txt").write_text("measurement-run-id: RUN-XXXXXXXXXXXX STATIC_SOURCE_REFERENCE\n",
                                          encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "W03: prevention claimed without content-validated run evidence"):
            self.validate_in_tmp(inv)

    def test_prevented_static_with_indented_marker_is_rejected(self):
        inv = self.claimed(fresh(), "W03")
        (self.tmp / "run.txt").write_text("  measurement-run-id: RUN-0123456789AB STATIC_SOURCE_REFERENCE\n",
                                          encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "W03: prevention claimed without content-validated run evidence"):
            self.validate_in_tmp(inv)

    def test_prevented_static_with_non_prevention_value_is_rejected(self):
        for value in ("UNKNOWN", "NO"):
            with self.subTest(value=value):
                inv = self.claimed(fresh(), "W03")
                row(inv, "W03")["prevents_before_side_effect"] = value
                with self.assertRaisesRegex(ValueError, "W03: (prevention status with non-prevention value|NO prevention)"):
                    self.validate_in_tmp(inv)

    def test_prevented_static_with_non_static_measurement_is_rejected(self):
        # Even a matching marker for REAL_CORE_SYNTHETIC cannot back a PREVENTED_STATIC row.
        inv = self.claimed(fresh(), "W03", kind="REAL_CORE_SYNTHETIC")
        row(inv, "W03")["measurement"] = "REAL_CORE_SYNTHETIC"
        with self.assertRaisesRegex(ValueError, "W03: prevention claimed without measurement"):
            self.validate_in_tmp(inv)

    def test_prevention_claim_with_evidence_without_run_marker_is_rejected(self):
        inv = self.claimed(fresh(), "W03", marker=False)
        with self.assertRaisesRegex(ValueError, "W03: prevention claimed without content-validated run evidence"):
            self.validate_in_tmp(inv)

    def test_synthetic_only_evidence_cannot_satisfy_prevention(self):
        # A REAL_CORE_SYNTHETIC run (synthetic BIOS) with a valid marker still must not satisfy a prevention claim.
        inv = self.claimed(fresh(), "W03", kind="REAL_CORE_SYNTHETIC")
        row(inv, "W03")["measurement"] = "REAL_CORE_SYNTHETIC"
        with self.assertRaisesRegex(ValueError, "W03: prevention claimed without measurement"):
            self.validate_in_tmp(inv)

    def test_marker_naming_another_measurement_does_not_satisfy_claim(self):
        inv = self.claimed(fresh(), "W03", kind="REAL_CORE_SYNTHETIC")
        with self.assertRaisesRegex(ValueError, "W03: prevention claimed without content-validated run evidence"):
            self.validate_in_tmp(inv)

    # Well-formed is not authentic. Each case below must fail closed; none may reach a measured state.
    def test_forged_run_id_with_valid_format_cannot_reach_prevented_measured(self):
        inv = self.claimed(fresh(), "W03")
        row(inv, "W03")["monitor_status"] = "PREVENTED_MEASURED"
        with self.assertRaisesRegex(ValueError, "W03: PREVENTED_MEASURED requires authenticated real-core evidence"):
            self.validate_in_tmp(inv)

    def test_prevented_measured_is_rejected_even_with_synthetic_or_static_markers(self):
        for kind in ("STATIC_SOURCE_REFERENCE", "REAL_CORE_SYNTHETIC"):
            with self.subTest(kind=kind):
                inv = self.claimed(fresh(), "W03", kind=kind)
                row(inv, "W03").update(monitor_status="PREVENTED_MEASURED", measurement=kind)
                with self.assertRaisesRegex(ValueError, "PREVENTED_MEASURED requires authenticated"):
                    self.validate_in_tmp(inv)

    def test_malformed_forged_run_ids_are_not_content_validated(self):
        for forged in ("RUN-0123456789ab", "RUN-012345678", "RUN-0123456789ABX", "run-0123456789AB"):
            with self.subTest(forged=forged):
                inv = self.claimed(fresh(), "W03")
                (self.tmp / "run.txt").write_text(f"measurement-run-id: {forged} STATIC_SOURCE_REFERENCE\n",
                                                  encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "W03: prevention claimed without content-validated run evidence"):
                    self.validate_in_tmp(inv)

    def test_tampered_evidence_with_trailing_text_on_marker_is_rejected(self):
        inv = self.claimed(fresh(), "W03")
        (self.tmp / "run.txt").write_text("measurement-run-id: RUN-0123456789AB STATIC_SOURCE_REFERENCE extra\n",
                                          encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "W03: prevention claimed without content-validated run evidence"):
            self.validate_in_tmp(inv)

    def test_tampered_evidence_with_conflicting_markers_fails_closed(self):
        inv = self.claimed(fresh(), "W03")
        (self.tmp / "run.txt").write_text("measurement-run-id: RUN-0123456789AB STATIC_SOURCE_REFERENCE\n"
                                          "measurement-run-id: RUN-0123456789AB REAL_CORE_SYNTHETIC\n",
                                          encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "W03: conflicting run markers in evidence"):
            self.validate_in_tmp(inv)

    def test_tampered_evidence_with_two_run_ids_fails_closed(self):
        inv = self.claimed(fresh(), "W03")
        (self.tmp / "run.txt").write_text("measurement-run-id: RUN-0123456789AB STATIC_SOURCE_REFERENCE\n"
                                          "measurement-run-id: RUN-BA9876543210 STATIC_SOURCE_REFERENCE\n",
                                          encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "W03: conflicting run markers in evidence"):
            self.validate_in_tmp(inv)

    def test_reused_run_id_from_another_claim_is_rejected(self):
        # Two rows citing the same run id: one result cannot back two claims.
        inv = self.claimed(fresh(), "W03")
        row(inv, "W04").update(monitor_status="PREVENTED_STATIC", prevents_before_side_effect="YES",
                               detection_point="BEFORE_RUN", measurement="STATIC_SOURCE_REFERENCE",
                               evidence=["run.txt"])
        with self.assertRaisesRegex(ValueError, "W04: run id RUN-0123456789AB is reused from W03"):
            self.validate_in_tmp(inv)

    def test_unknown_row_renamed_to_prevented_static_with_existing_evidence_is_rejected(self):
        # W18 is UNKNOWN (no measured evidence). Renaming it to PREVENTED_STATIC on an existing evidence file is not enough.
        inv = fresh()
        row(inv, "W18").update(monitor_status="PREVENTED_STATIC", prevents_before_side_effect="YES",
                               detection_point="BEFORE_STORE", measurement="STATIC_SOURCE_REFERENCE")
        with self.assertRaisesRegex(ValueError, "W18: prevention claimed without content-validated run evidence"):
            W.validate(inv, PINNED)

    def test_post_side_effect_detection_cannot_claim_in_enum_partial_value(self):
        # Real negative on in-enum values: the rule keys on the status name and on the detection point.
        for value in ("KNOWN_PC_ONLY", "YES"):
            for detection in ("AFTER_SIDE_EFFECT", "BEFORE_STORE"):
                with self.subTest(value=value, detection=detection):
                    inv = fresh()
                    row(inv, "W08")["prevents_before_side_effect"] = value
                    row(inv, "W08")["detection_point"] = detection
                    with self.assertRaisesRegex(ValueError, "W08: post-side-effect detection cannot claim prevention"):
                        W.validate(inv, PINNED)

    def test_post_side_effect_detection_point_cannot_claim_in_enum_partial_value(self):
        # W03 is not DETECTED_AFTER_SIDE_EFFECT, but its detection point is AFTER_SIDE_EFFECT: the rule keys on both.
        inv = self.claimed(fresh(), "W03")
        row(inv, "W03")["prevents_before_side_effect"] = "KNOWN_PC_ONLY"
        row(inv, "W03")["detection_point"] = "AFTER_SIDE_EFFECT"
        with self.assertRaisesRegex(ValueError, "W03: post-side-effect detection cannot claim prevention"):
            self.validate_in_tmp(inv)

    def test_mutation_gate_moving_off_blocked_is_rejected(self):
        for value in ("PARTIAL", "ALLOWED", "OPEN", "blocked"):
            with self.subTest(gate=value):
                inv = fresh()
                inv["gate_status_default"] = value
                with self.assertRaisesRegex(ValueError, "gate_status_default must be BLOCKED"):
                    W.validate(inv, PINNED)
                inv = fresh()
                row(inv, "W18")["gate_status"] = value
                with self.assertRaisesRegex(ValueError, "W18: gate_status must be BLOCKED"):
                    W.validate(inv, PINNED)

    def test_current_rows_are_accepted_by_the_new_rules(self):
        summary = W.validate(fresh(), PINNED)
        self.assertEqual(summary["rows"], len(INVENTORY["rows"]))

    def test_cli_exits_zero_on_committed_inventory(self):
        self.assertEqual(W.main(["--check"]), 0)
        self.assertEqual(W.main(["--bogus"]), 2)


class JsrObservationTest(unittest.TestCase):
    def test_real_core_observation_validates(self):
        O.validate_observation(real_observation())

    def test_fixture_observation_validates_without_real_core_claim(self):
        O.validate_observation(fixture_observation())

    def test_fixture_cannot_claim_real_core_evidence(self):
        obs = fixture_observation()
        obs["provenance"]["real_core_claim"] = True
        with self.assertRaisesRegex(ValueError, "HOST_FIXTURE must not claim"):
            O.validate_observation(obs)

    def test_real_core_with_wrong_revision_is_rejected(self):
        obs = real_observation()
        obs["provenance"]["core_revision"] = "1" * 40
        with self.assertRaisesRegex(ValueError, "pinned revision"):
            O.validate_observation(obs)

    def test_real_core_with_private_bios_is_rejected(self):
        obs = real_observation()
        obs["provenance"]["bios"] = "PRIVATE_LOCAL"
        with self.assertRaisesRegex(ValueError, "synthetic BIOS"):
            O.validate_observation(obs)

    def test_irq_must_be_present_and_unknown_carries_no_values(self):
        obs = real_observation()
        del obs["irq"]
        with self.assertRaises(ValueError):
            O.validate_observation(obs)
        obs = real_observation()
        obs["irq"] = {"status": "UNKNOWN", "mask": 0}
        with self.assertRaisesRegex(ValueError, "UNKNOWN irq"):
            O.validate_observation(obs)

    def test_observed_irq_requires_bytes(self):
        obs = real_observation()
        obs["irq"] = {"status": "OBSERVED", "mask": 0x1FF, "pending": 0}
        with self.assertRaisesRegex(ValueError, "irq.mask"):
            O.validate_observation(obs)

    def test_register_and_mpr_range_mutations_are_rejected(self):
        for mutate in (lambda o: o["registers"].update(a=256),
                       lambda o: o["registers"].update(pc=0x10000),
                       lambda o: o.update(mpr=[0] * 7),
                       lambda o: o["mpr"].__setitem__(0, -1),
                       lambda o: o["outcome"].update(returned=False)):
            obs = real_observation()
            mutate(obs)
            with self.assertRaises(ValueError):
                O.validate_observation(obs)

    def test_return_pc_must_match_return_state(self):
        obs = real_observation()
        obs["outcome"]["return_pc"] = None
        with self.assertRaisesRegex(ValueError, "return_pc"):
            O.validate_observation(obs)

    def test_rejected_outcome_needs_reason_and_budget_is_enforced(self):
        obs = real_observation()
        obs["outcome"].update(status="rejected", reason="")
        with self.assertRaisesRegex(ValueError, "needs a reason"):
            O.validate_observation(obs)
        obs = real_observation()
        obs["outcome"]["instructions_executed"] = 101
        obs["outcome"]["budget"] = 100
        with self.assertRaisesRegex(ValueError, "exceeds budget"):
            O.validate_observation(obs)

    def test_ram_diff_entry_mutations_are_rejected(self):
        obs = real_observation()
        obs["ram_diff"] = [{"address": 0x2000, "before": 0, "after": 0x5A, "extra": 1}]
        with self.assertRaisesRegex(ValueError, "ram_diff entry keys"):
            O.validate_observation(obs)

    def test_probe_row_maps_to_schema(self):
        row_ = {"status": "ok", "reason": "", "before": {"a": 0, "x": 0, "y": 0, "p": 4, "sp": 255,
                                                         "pc": 0x3E00, "mpr": [0, 0xF8, 0, 0, 0, 0, 0, 0]},
                "after": {"a": 0xAA, "x": 0x55, "y": 0xCC, "p": 4, "sp": 0xFD, "pc": 0x3E05,
                          "mpr": [0, 0xF8, 0, 0, 0, 0, 0, 0]},
                "stop_pc": 0x3E05, "instructions_executed": 3, "budget": 100, "returned": True,
                "detected": False, "mpr_changed": False, "ram_diff": [],
                "callee_pc": 0x3E02}
        obs = O.from_probe_row(row_, {"kind": "HOST_FIXTURE", "core_revision": None, "bios": "SYNTHETIC",
                                      "real_core_claim": False})
        self.assertEqual(obs["registers"]["a"], 0xAA)
        self.assertEqual(obs["outcome"]["return_pc"], 0x3E05)
        self.assertEqual(obs["irq"], {"status": "UNKNOWN"})
        self.assertFalse(obs["guard"]["detected"])


if __name__ == "__main__":
    unittest.main()
