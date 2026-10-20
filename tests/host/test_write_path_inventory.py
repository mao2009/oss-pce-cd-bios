"""Host tests for the unmonitored write-path inventory and the JSR observation schema (Issue #173).

Synthetic only: exercises tools/write_path_inventory.py and tools/jsr_observation.py on the committed
inventory and on hand-built fixtures. No emulator, no BIOS and no ROM are read. The inventory must
stay fail-closed: every row BLOCKED, evidence present, and ST/IRQ/MPR/MMIO/RAM paths covered. An
observation must never be read as real-core evidence unless its provenance says so, and a fixture
can never claim it.
"""
import copy
import json
import sys
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
    def test_committed_inventory_validates_and_stays_blocked(self):
        summary, _ = W.load()
        self.assertEqual(summary["gate_status"], "BLOCKED")
        self.assertEqual(summary["rows"], len(INVENTORY["rows"]))
        self.assertGreater(summary["unresolved"], 0)

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
        inv = fresh()
        row(inv, "W01")["measurement"] = "NOT_MEASURED"
        with self.assertRaisesRegex(ValueError, "prevention claimed without measurement"):
            W.validate(inv, PINNED)

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
        with self.assertRaisesRegex(ValueError, "W06: DETECTED_AFTER_SIDE_EFFECT cannot claim prevention"):
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

    def test_mutation_prevention_value_without_measurement_is_rejected(self):
        inv = fresh()
        row(inv, "W02")["measurement"] = "NOT_MEASURED"
        with self.assertRaisesRegex(ValueError, "W02: prevention claimed without measurement"):
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
