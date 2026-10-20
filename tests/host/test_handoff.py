"""Host checks for the Issue #242 synthetic ROM-to-RAM handoff judge (no emulator; real-core runs are in the integration test)."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests/integration"))
sys.path.insert(0, str(ROOT / "tools"))
from geargrafx_handoff import (  # noqa: E402
    Blocked, HandoffRejected, RAM_ENTRY, check_handoff_contract, expected_payload, judge, run_negatives)

GOOD_MPR = [0xFF, 0xF8, 1, 2, 31, 3, 4, 0]
LABELS = {"handoff_jump": 0xF0A0, "handoff_done": 0xF0B0}
PAYLOAD = expected_payload(LABELS["handoff_done"])  # compared as bytes; never executed here


def _row(cycle, phase, pc, sp=0xFF, mpr=GOOD_MPR):
    return {"cycle": cycle, "phase": phase, "pc": pc, "sp": sp, "mpr": mpr}


def _records(entry_pc=RAM_ENTRY, marker=b"\xa5\x5a", mpr=GOOD_MPR):
    records = {"TRACE": [], "OBS": []}
    for cycle in ("cold", "warm"):
        records["TRACE"] += [_row(cycle, "handoff_jump", LABELS["handoff_jump"]),
                             _row(cycle, "handoff_entry", entry_pc, mpr=mpr),
                             _row(cycle, "handoff_done", LABELS["handoff_done"])]
        dump = bytearray(64)
        dump[:len(PAYLOAD)] = PAYLOAD
        dump[0x20:0x22] = marker
        records["OBS"].append({"phase": cycle, "pc": LABELS["handoff_done"], "sp": 0xFF,
                               "work_ram_0200_hex": bytes(dump).hex()})
    return records


class HandoffContractTests(unittest.TestCase):
    def test_accepts_work_ram_entry(self):
        check_handoff_contract(RAM_ENTRY, 16, 0xFF, GOOD_MPR)

    def test_rejects_entry_outside_work_ram_window(self):
        for entry, size in ((0xF000, 16), (0x1FF8, 16), (0x3FF8, 16)):
            with self.subTest(entry=hex(entry)), self.assertRaises(ValueError):
                check_handoff_contract(entry, size, 0xFF, GOOD_MPR)

    def test_rejects_bad_bank_or_stack(self):
        bad = [[0xFF, 0x00] + GOOD_MPR[2:], [0xFF, 0xF8, 1, 2, 31, 3, 4, 1], [0x00, 0xF8] + GOOD_MPR[2:]]
        for mpr in bad:
            with self.subTest(mpr=mpr), self.assertRaises(ValueError):
                check_handoff_contract(RAM_ENTRY, 16, 0xFF, mpr)
        with self.assertRaises(ValueError):
            check_handoff_contract(RAM_ENTRY, 16, 0xFE, GOOD_MPR)


class HandoffJudgeTests(unittest.TestCase):
    def test_accepts_complete_handoff(self):
        judge(_records(), LABELS, PAYLOAD)

    def test_rejects_missing_entry_or_marker(self):
        records = _records()
        records["TRACE"] = [r for r in records["TRACE"] if r["phase"] != "handoff_entry" or r["cycle"] != "warm"]
        with self.assertRaises(ValueError):
            judge(records, LABELS, PAYLOAD)
        with self.assertRaises(ValueError):
            judge(_records(marker=b"\x00\x00"), LABELS, PAYLOAD)

    def test_rejects_entry_pc_and_bank_mismatch(self):
        with self.assertRaises(ValueError):
            judge(_records(entry_pc=RAM_ENTRY + 1), LABELS, PAYLOAD)
        with self.assertRaises(ValueError):
            judge(_records(mpr=[0xFF, 0x00] + GOOD_MPR[2:]), LABELS, PAYLOAD)


class HandoffReasonTests(unittest.TestCase):
    """Each negative is rejected for its own reason key; entry and MPR are checked before phase order."""

    def test_mpr1_rom_bank_is_rejected_for_mpr_not_phase_order(self):
        records = _records(mpr=[0xFF, 0x00] + GOOD_MPR[2:])
        with self.assertRaises(HandoffRejected) as cm:
            judge(records, LABELS, PAYLOAD)
        self.assertEqual(cm.exception.reason, "mpr")

    def test_missing_entry_is_rejected_for_entry_not_phase_order(self):
        records = _records()
        records["TRACE"] = [r for r in records["TRACE"] if r["phase"] != "handoff_entry"]
        with self.assertRaises(HandoffRejected) as cm:
            judge(records, LABELS, PAYLOAD)
        self.assertEqual(cm.exception.reason, "entry")

    def test_mpr_is_checked_before_phase_order(self):
        records = _records(mpr=[0xFF, 0x00] + GOOD_MPR[2:])
        records["TRACE"] = [r for r in records["TRACE"] if r["phase"] != "handoff_done"]
        with self.assertRaises(HandoffRejected) as cm:
            judge(records, LABELS, PAYLOAD)
        self.assertEqual(cm.exception.reason, "mpr")


def _mutant_rom():
    """Synthetic 8 KiB bank holding the bytes the negatives patch: MPR1 immediate and the JMP $2200 operand."""
    labels = {"handoff_bank": 0xE011, "handoff_jump": LABELS["handoff_jump"], "handoff_done": LABELS["handoff_done"]}
    rom = bytearray(0x2000)
    rom[0x11 - 1] = 0xF8
    rom[labels["handoff_jump"] - 0xE000:labels["handoff_jump"] - 0xE000 + 3] = bytes([0x4C, 0x00, 0x22])
    return bytes(rom), labels


class NegativeRunnerTests(unittest.TestCase):
    def test_probe_timeout_is_blocked_not_rejected(self):
        rom, labels = _mutant_rom()

        def run(path):
            raise subprocess.TimeoutExpired(cmd="probe", timeout=30)

        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(Blocked):
            run_negatives(rom, labels, PAYLOAD, Path(tmp), run)

    def test_probe_crash_is_blocked_not_rejected(self):
        rom, labels = _mutant_rom()

        def run(path):
            raise subprocess.CalledProcessError(returncode=3, cmd="probe")

        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(Blocked):
            run_negatives(rom, labels, PAYLOAD, Path(tmp), run)

    def test_wrong_reason_for_a_negative_fails(self):
        rom, labels = _mutant_rom()

        def run(path):
            records = _records()  # complete handoff except the phase list: rejected for "phase", not "mpr"
            records["TRACE"] = [r for r in records["TRACE"] if r["phase"] != "handoff_done"]
            return records

        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ValueError) as cm:
            run_negatives(rom, labels, PAYLOAD, Path(tmp), run)
        self.assertNotIsInstance(cm.exception, Blocked)

    def test_expected_reasons_are_recorded(self):
        rom, labels = _mutant_rom()

        def run(path):
            if path.name.startswith("mpr1_rom_bank"):
                return _records(mpr=[0xFF, 0x00] + GOOD_MPR[2:])
            records = _records()
            records["TRACE"] = [r for r in records["TRACE"] if r["phase"] != "handoff_entry"]
            return records

        with tempfile.TemporaryDirectory() as tmp:
            negatives = run_negatives(rom, labels, PAYLOAD, Path(tmp), run)
        self.assertEqual({k: v["reason"] for k, v in negatives.items()},
                         {"mpr1_rom_bank": "mpr", "entry_misaligned": "entry"})


if __name__ == "__main__":
    unittest.main()
