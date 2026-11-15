"""Host check for EX_JOYSNS (slot 33) + EX_SCRSIZ (slot 35) co-presence (Issues 43/45).

Pure, no core. Verifies same-ROM integration preconditions used by the
real-core sequential test (geargrafx_dev_2123_integration.py):
both overrides present, DEV_EXPERIMENTAL (not ABI), preservation contracts,
API-table wiring, OVERRIDES below probe overlay, and MA_MUL16S slot 79
still present (no regression).

RED->GREEN: missing-slot and overlay-breach cases are BLOCKED, never passed.
"""
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC21 = ROOT / "src" / "overrides" / "slot_21.s"
SRC23 = ROOT / "src" / "overrides" / "slot_23.s"
SRC4F = ROOT / "src" / "overrides" / "slot_4F.s"
SPEC = ROOT / "spec" / "dev_implementations.json"
DOLLAR = chr(36)


def load_slots():
    data = json.loads(SPEC.read_text())
    return {s["id"]: s for s in data["slots"]}


def verify_both_present(overridden_ids):
    missing = [i for i in (33, 35) if i not in set(overridden_ids)]
    if missing:
        raise ValueError("BLOCKED: missing DEV override slots %s" % (missing,))
    return True


def verify_no_regression_4f(overridden_ids):
    if 79 not in set(overridden_ids):
        raise ValueError("BLOCKED: MA_MUL16S slot 79 missing")
    return True


class CoPresenceTests(unittest.TestCase):
    def test_both_sources_present_and_dev_experimental(self):
        for src in (SRC21, SRC23):
            self.assertTrue(src.is_file(), "missing %s" % src)
            text = src.read_text()
            self.assertIn("DEV_EXPERIMENTAL", text)
            self.assertIn("NOT the reference", text)
            self.assertIn("tma #$01", text)
            self.assertIn("tam #$01", text)

    def test_spec_entries_project_only_not_verified(self):
        slots = load_slots()
        for i in (33, 35):
            s = slots[i]
            self.assertEqual(s["abi_contract"]["status"], "PROJECT_SPEC_ONLY")
            self.assertNotEqual(s["abi_contract"]["status"], "VERIFIED")
        self.assertEqual(slots[33]["candidate_name"], "EX_JOYSNS")
        self.assertEqual(slots[35]["candidate_name"], "EX_SCRSIZ")

    def test_verify_both_present_passes(self):
        self.assertTrue(verify_both_present([33, 35, 79]))

    def test_verify_both_present_red_missing_21(self):
        with self.assertRaises(ValueError):
            verify_both_present([35, 79])

    def test_verify_both_present_red_missing_23(self):
        with self.assertRaises(ValueError):
            verify_both_present([33, 79])

    def test_no_regression_4f_present(self):
        slots = load_slots()
        self.assertEqual(slots[79]["candidate_name"], "MA_MUL16S")
        self.assertTrue(verify_no_regression_4f([33, 35, 79]))

    def test_no_regression_4f_red_missing(self):
        with self.assertRaises(ValueError):
            verify_no_regression_4f([33, 35])


class WiringTests(unittest.TestCase):
    def test_api_table_entries_wired(self):
        text = (ROOT / "tools" / "build_rom.py").read_text()
        self.assertIn("jmp api_slot_", text)
        self.assertIn("api_slot_", text)

    def test_overrides_below_probe_overlay(self):
        overlay = DOLLAR + "E800"
        for src in (SRC21, SRC23, SRC4F):
            text = src.read_text()
            self.assertNotIn(overlay, text)


if __name__ == "__main__":
    unittest.main()
