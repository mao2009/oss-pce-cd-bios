"""Host checks for the Issue #242 synthetic ROM-to-RAM handoff judge (no emulator; real-core runs are in the integration test)."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests/integration"))
sys.path.insert(0, str(ROOT / "tools"))
from geargrafx_handoff import RAM_ENTRY, check_handoff_contract, expected_payload, judge  # noqa: E402

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


if __name__ == "__main__":
    unittest.main()
