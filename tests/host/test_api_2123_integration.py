"""Host check for EX_JOYSNS (slot 33) + EX_SCRSIZ (slot 35) co-presence (Issues 43/45).

Pure, no core. Verifies same-ROM integration preconditions used by the
real-core sequential test (geargrafx_dev_2123_integration.py):
both overrides present, DEV_EXPERIMENTAL (not ABI), preservation contracts,
API-table wiring, OVERRIDES below probe overlay, and MA_MUL16S slot 79
still present (no regression).

The positive checks use the build's real override selection
(tools/build_rom.py overrides()), not just fixed lists, so an actual
selection regression (slot dropped from src/overrides) turns RED.

Snapshot and per-call X/Y helpers of the integration driver are covered
here with temp files and mocks (no core needed):
- the snapshot round-trips verified bytes and refuses tampered bytes
- all 8 runs consume the single snapshot path, never the original ROM
- per-call X/Y corruption is RED even when the final X/Y look fine

RED->GREEN: missing-slot, tampered-snapshot and X/Y-clobber cases are
BLOCKED/failed, never passed.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests" / "integration"))
from build_rom import overrides as build_overrides  # noqa: E402
import geargrafx_dev_2123_integration as I  # noqa: E402

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


class RealSelectionTests(unittest.TestCase):
    """The validators above run on the build's real override selection."""

    @classmethod
    def real_ids(cls):
        return set(build_overrides(ROOT))

    def test_real_selection_has_2123_and_4f(self):
        ids = self.real_ids()
        self.assertIn(33, ids)
        self.assertIn(35, ids)
        self.assertIn(79, ids)
        self.assertEqual(len(ids), 18)
        self.assertTrue(verify_both_present(ids))
        self.assertTrue(verify_no_regression_4f(ids))

    def test_real_selection_minus_21_red(self):
        with self.assertRaises(ValueError):
            verify_both_present(self.real_ids() - {33})

    def test_real_selection_minus_23_red(self):
        with self.assertRaises(ValueError):
            verify_both_present(self.real_ids() - {35})

    def test_real_selection_minus_4f_red(self):
        with self.assertRaises(ValueError):
            verify_no_regression_4f(self.real_ids() - {79})


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


def _canned_res(mask, lo, order):
    """A valid driver RESULT record for the given inputs (both orders)."""
    joy = I.expected_a(mask)
    a1, a2 = (joy, lo & 0xFF) if order == 0 else (lo & 0xFF, joy)
    return {
        "pc": 1000, "halt": 1000, "sp": 0xFF,
        "mpr": [0xF8, 0xF8, 0, 0, 0, 0, 0, 0],
        "a1": a1, "p1": 0, "sp1": 0xFF, "mpr1": 0xF8,
        "x1": 0x5A, "y1": 0xA5,
        "a2": a2, "p2": 0, "sp2": 0xFF, "mpr2": 0xF8,
        "x2": 0x5A, "y2": 0xA5,
        "x": 0x5A, "y": 0xA5, "sp_f": 0xFF, "mpr_f": 0xF8,
        "vdc_ar": 9, "vdc_r9": lo & 0xFF, "order": order,
    }


class CheckOneXYTests(unittest.TestCase):
    def test_valid_both_orders_green(self):
        for order in (0, 1):
            self.assertEqual(I.check_one(_canned_res(0, 0x11, order), 0, 0x11, order, "t"), [])

    def test_first_call_x_clobber_red(self):
        res = _canned_res(0, 0x11, 0)
        res["x1"] = 0x00
        errs = I.check_one(res, 0, 0x11, 0, "t")
        self.assertTrue(any("x1" in e for e in errs), errs)

    def test_first_call_y_clobber_red(self):
        res = _canned_res(0, 0x11, 1)
        res["y1"] = 0x00
        errs = I.check_one(res, 0, 0x11, 1, "t")
        self.assertTrue(any("y1" in e for e in errs), errs)

    def test_second_call_xy_clobber_red(self):
        res = _canned_res(0, 0x11, 0)
        res["x2"] = 0xFF
        res["y2"] = 0x00
        errs = I.check_one(res, 0, 0x11, 0, "t")
        self.assertTrue(any("x2" in e for e in errs), errs)
        self.assertTrue(any("y2" in e for e in errs), errs)

    def test_final_only_good_still_red(self):
        # First call clobbers X but the second call repairs it: the final
        # X/Y look fine, yet the intermediate record must still fail.
        res = _canned_res(0, 0x11, 0)
        res["x1"] = 0xFF
        res["x"] = 0x5A
        res["y"] = 0xA5
        errs = I.check_one(res, 0, 0x11, 0, "t")
        self.assertTrue(any("x1" in e for e in errs), errs)


class SnapshotHelperTests(unittest.TestCase):
    def test_snapshot_roundtrip_green(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            data = b"VERIFIED-ROM-BYTES-0123456789"
            snap = I.write_verified_snapshot(data, folder)
            with mock.patch.object(I, "verify_dev_rom") as v:
                out = I.load_verified_snapshot(snap, data)
            self.assertEqual(out, data)
            v.assert_called_once_with(data)

    def test_snapshot_tamper_refused(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            data = b"VERIFIED-ROM-BYTES-0123456789"
            snap = I.write_verified_snapshot(data, folder)
            os.chmod(snap, 0o644)
            snap.write_bytes(b"TAMPERED-BYTES-xxxxxxxxxxxxxx")
            with mock.patch.object(I, "verify_dev_rom") as v:
                with self.assertRaises(ValueError):
                    I.load_verified_snapshot(snap, data)
            v.assert_not_called()

    def test_snapshot_reverify_failure_propagates(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            data = b"VERIFIED-ROM-BYTES-0123456789"
            snap = I.write_verified_snapshot(data, folder)
            with mock.patch.object(I, "verify_dev_rom", side_effect=ValueError("tampered")):
                with self.assertRaises(ValueError):
                    I.load_verified_snapshot(snap, data)

    def test_main_runs_all_eight_on_single_snapshot(self):
        seen_roms = []
        seen_contents = []

        def canned_run(probe, rom, cue, keymask, lo, order, steps=4096):
            seen_roms.append(Path(rom))
            seen_contents.append(Path(rom).read_bytes())
            return _canned_res(keymask, lo, order)

        with tempfile.TemporaryDirectory() as td:
            rom = Path(td) / "dev-only-not-compatible-syscard3.pce"
            rom.write_bytes(b"INPUT-ROM-BYTES-abcdef0123456789")
            expected = rom.read_bytes()
            argv = ["prog", "--rom", str(rom)]
            with mock.patch.object(I, "verify_dev_rom"), \
                    mock.patch.object(I, "compile_probe", return_value=Path(td) / "probe"), \
                    mock.patch.object(I, "run_case", side_effect=canned_run), \
                    mock.patch.object(sys, "argv", argv):
                I.main()
        self.assertEqual(len(seen_roms), 8)
        first = seen_roms[0]
        self.assertTrue(all(p == first for p in seen_roms))
        # The single path is the frozen snapshot, never the input ROM.
        self.assertNotEqual(first.resolve(), rom.resolve())
        self.assertTrue(all(c == expected for c in seen_contents))


if __name__ == "__main__":
    unittest.main()
