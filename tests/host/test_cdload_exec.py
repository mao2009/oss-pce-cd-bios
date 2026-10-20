"""Host checks for the load-and-execute experiment (Issues #14 and #5): build, sector layout, labels. No emulator."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from tools.environment import requires_cc65

from tools.build_cdload_exec import (EXEC_ADDRESS, HDR_BYTES, SECTOR_BYTES, USER_OFFSET, build_cdload_exec,
                                     build_exec_disc, encode_sector, parse_labels)
from tools.build_rom import build
from tools.make_cd_image import BIN_NAME, RAW_BYTES, sector_payload


@requires_cc65
class CdLoadExecBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.original = build(cls.root / "original")
        cls.debug_path = build_cdload_exec(cls.original, cls.root / "debug", "debug")
        cls.release_path = build_cdload_exec(cls.original, cls.root / "release", "release")
        cls.payload = cls.debug_path.read_bytes()
        cls.code = (cls.root / "debug/payload.bin").read_bytes()
        cls.symbols = parse_labels(cls.root / "debug/cdload.lbl")
        cls.payload_symbols = parse_labels(cls.root / "debug/payload.lbl")

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_debug_and_release_identical_and_api_table_untouched(self):
        self.assertEqual(self.payload, self.release_path.read_bytes())
        self.assertEqual(len(self.payload), 262144)
        self.assertEqual(self.payload[:0x1000], self.original.read_bytes()[:0x1000])

    def test_payload_starts_at_exec_address_and_fits_the_sector(self):
        self.assertEqual(self.payload_symbols["payload_entry"], EXEC_ADDRESS)
        self.assertTrue(EXEC_ADDRESS <= self.payload_symbols["payload_done"] < EXEC_ADDRESS + len(self.code))
        self.assertTrue(1 <= len(self.code) <= 255)
        self.assertLessEqual(HDR_BYTES + len(self.code), SECTOR_BYTES)

    def test_stage0_return_label_and_halt_labels_are_in_rom(self):
        self.assertEqual(self.symbols["cd_entry"], 0xF000)
        for name in ("cd_payload_return", "cd_halt_ok", "cd_halt_fail", "cd_buffer_ready", "cd_data_done"):
            self.assertGreaterEqual(self.symbols[name], 0xE000)

    def test_sector_header_and_tail_layout(self):
        sector = encode_sector(5, self.code)
        self.assertEqual(sector[:4], b"PCEX")
        self.assertEqual(sector[4], 0)
        self.assertEqual(sector[5], len(self.code))
        self.assertEqual(sector[6], sum(self.code) & 0xFF)
        self.assertEqual(sector[HDR_BYTES:HDR_BYTES + len(self.code)], self.code)
        self.assertEqual(sector[HDR_BYTES + len(self.code):], sector_payload(5)[HDR_BYTES + len(self.code):])

    def test_disc_carries_the_sector_at_requested_lba(self):
        cue = build_exec_disc(self.root / "disc", 5, self.code)
        raw = (self.root / "disc" / BIN_NAME).read_bytes()
        at = 5 * RAW_BYTES + USER_OFFSET
        self.assertEqual(raw[at:at + SECTOR_BYTES], encode_sector(5, self.code))
        self.assertEqual(raw[at - 16:at - 16 + 12], b"\x00" + b"\xff" * 10 + b"\x00")  # sync pattern intact
        self.assertTrue(cue.exists())

    def test_tampered_sector_byte_changes_only_its_bytes(self):
        clean = encode_sector(5, self.code)
        damaged = bytearray(clean)
        damaged[HDR_BYTES] ^= 1
        self.assertEqual(sum(a != b for a, b in zip(clean, damaged)), 1)
        self.assertNotEqual(hashlib.sha256(clean).hexdigest(), hashlib.sha256(bytes(damaged)).hexdigest())


class CdLoadExecContractTests(unittest.TestCase):
    """cc65-independent checks: only encode_sector(), so these always run (no @requires_cc65 here)."""

    def test_payload_size_limits_are_enforced(self):
        for code in (b"", bytes(256)):
            with self.assertRaises(ValueError):
                encode_sector(5, code)


# ---- judge negatives on synthetic records (no emulator): halt confirmation and SCSI phase sequence ----
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration"))
import geargrafx_cdload_exec as judge  # noqa: E402
from tools.build_cdload_exec import EXPECTED_MPR  # noqa: E402
from tools.cdread_verify import PHASE_BUS_FREE, PHASE_COMMAND, PHASE_DATA_IN, PHASE_MESSAGE_IN, PHASE_STATUS  # noqa: E402

PAYLOAD = bytes(range(1, 41))
SYMBOLS = {"cd_halt_ok": 0xE100, "cd_payload_return": 0xE080, "payload_done": EXEC_ADDRESS + 0x20}
SCSI_SEQUENCE = [PHASE_COMMAND, PHASE_DATA_IN, PHASE_STATUS, PHASE_MESSAGE_IN, PHASE_BUS_FREE]


def halt_fields(arrival=1000, steps=64):
    return {"halt_confirmed": True, "halt_confirmation_steps": steps, "halt_confirmation_required": 64,
            "halted_after_step": arrival, "step": arrival + steps}


def observation(lba=5, count=1):
    """Synthetic but internally consistent cold+warm success observation for judge_success()."""
    sector = encode_sector(lba, PAYLOAD)
    buffer = bytearray(b"\x5a" * 6144)
    buffer[:SECTOR_BYTES] = sector
    buffer[judge.EXEC_OFF:judge.EXEC_OFF + len(PAYLOAD)] = PAYLOAD
    buffer[judge.MARK_OFF:judge.MARK_OFF + 7] = bytes([0xA5, 0x5A, 0, 0, 0xF8, 0xFF, 0x3C])
    result = bytes([0, 16]) + SECTOR_BYTES.to_bytes(2, "little") + bytes([0, 0, 0])
    heads = {"cd_buffer_ready": "5a" * 32, "cd_data_done": sector[:32].hex()}
    pcs = {"payload_entry": EXEC_ADDRESS, "payload_done": SYMBOLS["payload_done"],
           "cd_payload_return": SYMBOLS["cd_payload_return"], "cd_halt_ok": SYMBOLS["cd_halt_ok"]}
    records = {"CAP": [{"instruction_step": True, "cdrom_hardware": True, "cdrom_media": True}],
               "RUN": [], "HIT": [], "PHASE": []}
    for cycle in ("cold", "warm"):
        records["RUN"].append({"cycle": cycle, "reason": "cd_halt_ok", "pc": SYMBOLS["cd_halt_ok"],
                               **halt_fields(), "mpr": list(EXPECTED_MPR), "sp": 0xFF,
                               "result_hex": result.hex(), "buffer_hex": buffer.hex()})
        for step, name in enumerate(judge.ORDER, start=1):
            row = {"cycle": cycle, "name": name, "step": step * 10, "pc": pcs.get(name, 0xE000)}
            if name in heads:
                row["buffer_head_hex"] = heads[name]
            records["HIT"].append(row)
        for phase in SCSI_SEQUENCE:
            row = {"cycle": cycle, "scsi_phase": phase}
            if phase == PHASE_DATA_IN:
                row.update(load_sector=lba, load_count=count)
            records["PHASE"].append(row)
    return records


def failure_observation():
    result = bytes([20, 11]) + SECTOR_BYTES.to_bytes(2, "little") + bytes([0, 0, 0])
    return {"CAP": [], "HIT": [], "PHASE": [],
            "RUN": [{"cycle": c, "reason": "cd_halt_fail", **halt_fields(), "result_hex": result.hex()}
                    for c in ("cold", "warm")]}


class CdLoadExecJudgeTests(unittest.TestCase):
    def assertRejected(self, records, lba=5, count=1):
        with self.assertRaises(ValueError):
            judge.judge_success(records, PAYLOAD, SYMBOLS, lba, count)

    def test_synthetic_success_is_accepted(self):
        judge.judge_success(observation(), PAYLOAD, SYMBOLS, 5, 1)

    def test_halt_missing_fields_rejected(self):
        for key in ("halt_confirmed", "halt_confirmation_steps", "halt_confirmation_required",
                    "halted_after_step", "step"):
            with self.subTest(missing=key):
                records = observation()
                del records["RUN"][1][key]
                self.assertRejected(records)

    def test_halt_63_instructions_rejected(self):
        records = observation()
        for run in records["RUN"]:
            run.update(halt_fields(steps=63), halt_confirmation_required=63)
        self.assertRejected(records)

    def test_halt_required_not_64_rejected(self):
        for required in (63, 32, 0):
            with self.subTest(required=required):
                records = observation()
                records["RUN"][0]["halt_confirmation_required"] = required
                self.assertRejected(records)

    def test_halt_step_accounting_and_invalid_values_rejected(self):
        cases = {"step_not_arrival_plus_steps": lambda r: r.update(step=r["step"] + 1),
                 "arrival_negative": lambda r: r.update(halted_after_step=-1),
                 "arrival_none": lambda r: r.update(halted_after_step=None),
                 "arrival_string": lambda r: r.update(halted_after_step="1000"),
                 "confirmed_string": lambda r: r.update(halt_confirmed="True"),
                 "confirmed_false": lambda r: r.update(halt_confirmed=False)}
        for name, edit in cases.items():
            with self.subTest(case=name):
                records = observation()
                edit(records["RUN"][0])
                self.assertRejected(records)

    def test_missing_scsi_phase_rejected(self):
        for phase in SCSI_SEQUENCE:
            with self.subTest(missing=phase):
                records = observation()
                for cycle in ("cold", "warm"):
                    rows = [r for r in records["PHASE"] if r["cycle"] == cycle]
                    records["PHASE"].remove(next(r for r in rows if r["scsi_phase"] == phase))
                self.assertRejected(records)

    def test_reordered_scsi_phases_rejected(self):
        for i in range(len(SCSI_SEQUENCE) - 1):
            with self.subTest(swap_at=i):
                records = observation()
                rows = [r for r in records["PHASE"] if r["cycle"] == "cold"]
                rows[i], rows[i + 1] = rows[i + 1], rows[i]
                records["PHASE"] = [r for r in records["PHASE"] if r["cycle"] != "cold"] + rows
                self.assertRejected(records)

    def test_wrong_lba_rejected(self):
        records = observation(lba=6)  # emulator saw READ of LBA 6
        self.assertRejected(records, lba=5)  # expected LBA 5
        records = observation(lba=5)
        for row in records["PHASE"]:
            if row["scsi_phase"] == PHASE_DATA_IN:
                row["load_sector"] = 6
        self.assertRejected(records, lba=5)

    def test_wrong_count_rejected(self):
        records = observation(count=2)  # emulator saw READ of 2 sectors
        self.assertRejected(records, count=1)
        self.assertRejected(observation(count=1), count=2)

    def test_judge_failure_requires_full_halt_confirmation(self):
        judge.judge_failure(failure_observation(), 20, 11, SECTOR_BYTES)
        cases = {"steps_63": lambda r: r.update(halt_confirmation_steps=63, halt_confirmation_required=63,
                                                  step=1063),
                 "required_missing": lambda r: r.pop("halt_confirmation_required"),
                 "confirmed_false": lambda r: r.update(halt_confirmed=False)}
        for name, edit in cases.items():
            with self.subTest(case=name):
                records = failure_observation()
                edit(records["RUN"][0])
                with self.assertRaises(ValueError):
                    judge.judge_failure(records, 20, 11, SECTOR_BYTES)


if __name__ == "__main__":
    unittest.main()
