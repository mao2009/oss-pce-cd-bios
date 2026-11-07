"""Host boundary contracts for DEV PCEX (#668). No emulator, no cc65.

PCEX is the 8-byte DEV header (src/cd_load_exec_probe.s, tools/build_cdload_exec.py):

    [0:4] magic "PCEX", [4] entry offset (u8), [5] length (u8, 1..255),
    [6] checksum sum(code) mod 256, [7] reserved, [8:] code of `length` bytes.

Length excess, sector-boundary excess and load-address excess are not separate guest
codes: with a one-byte length they are unrepresentable and bounded by construction.
These tests pin that claim (host encoder, synthetic-boot oracle, arithmetic) and
pin the judge's partial-copy / overshoot detection on synthetic records.  They do
not run the guest; real-core cold/warm evidence lives in
tests/integration/geargrafx_dev_ipl_boot.py (pinned Geargrafx).
"""
import copy
import sys
import unittest
from pathlib import Path

from tools import synthetic_boot_media as gen
from tools import synthetic_boot_oracle as oracle
from tools.build_cdload_exec import (EXEC_ADDRESS, HDR_BYTES, SECTOR_BYTES, encode_sector)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration"))
import geargrafx_cdload_exec as judge  # noqa: E402
from tools.build_cdload_exec import EXPECTED_MPR  # noqa: E402
from tools.cdread_verify import PHASE_BUS_FREE, PHASE_COMMAND, PHASE_DATA_IN, PHASE_MESSAGE_IN, PHASE_STATUS  # noqa: E402

RAW = 2352
USER_OFFSET = 16
MAGIC = b"PCEX"

PAYLOAD = bytes(range(1, 41))
SYMBOLS = {"cd_halt_ok": 0xE100, "cd_payload_return": 0xE080, "payload_done": EXEC_ADDRESS + 0x20}
SCSI_SEQUENCE = [PHASE_COMMAND, PHASE_DATA_IN, PHASE_STATUS, PHASE_MESSAGE_IN, PHASE_BUS_FREE]


def halt_fields(arrival=1000, steps=64):
    return {"halt_confirmed": True, "halt_confirmation_steps": steps, "halt_confirmation_required": 64,
            "halted_after_step": arrival, "step": arrival + steps}


def success_observation(code=PAYLOAD, lba=5, count=1):
    sector = encode_sector(lba, code)
    buffer = bytearray(b"\x5a" * 6144)
    buffer[:SECTOR_BYTES] = sector
    buffer[judge.EXEC_OFF:judge.EXEC_OFF + len(code)] = code
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


def failure_observation(code=20, stage=11, transferred=SECTOR_BYTES, lba=5):
    """Failure observation with the EXEC window left at the $5A sentinel (no copy)."""
    buffer = bytearray(b"\x5a" * 6144)
    buffer[:SECTOR_BYTES] = encode_sector(lba, PAYLOAD)
    result = bytes([code, stage]) + transferred.to_bytes(2, "little") + bytes([0, 0, 0])
    return {"CAP": [], "HIT": [], "PHASE": [],
            "RUN": [{"cycle": c, "reason": "cd_halt_fail", **halt_fields(),
                     "result_hex": result.hex(), "buffer_hex": buffer.hex()} for c in ("cold", "warm")]}


def patched_image(entry, length, checksum, code, layout="V1"):
    """Generator image with the INDEX 01 header replaced by the given PCEX values."""
    img = gen.build_case_image("NONE", layout)
    index01 = oracle.parse_tracks(img.cue)[1]["index01"]
    bin_bytes = bytearray(img.bin)
    at = index01 * RAW + USER_OFFSET
    bin_bytes[at:at + 4] = MAGIC
    bin_bytes[at + 4] = entry
    bin_bytes[at + 5] = length
    bin_bytes[at + 6] = checksum
    bin_bytes[at + 7] = 0
    bin_bytes[at + 8:at + 8 + len(code)] = code
    return img.cue, bytes(bin_bytes)


class PcexLengthBoundaryTests(unittest.TestCase):
    """Length is one byte: 0 and 256+ are the only excess values and both are host-rejected."""

    def test_min_and_max_lengths_are_accepted(self):
        for code in (b"\xea", bytes([0xEA]) * 255):
            with self.subTest(length=len(code)):
                sector = encode_sector(5, code)
                self.assertEqual(sector[5], len(code))
                self.assertEqual(sector[6], sum(code) & 0xFF)
                self.assertEqual(sector[HDR_BYTES:HDR_BYTES + len(code)], code)

    def test_zero_and_256_are_rejected_by_the_host_encoder(self):
        for code in (b"", bytes(256)):
            with self.subTest(length=len(code)):
                with self.assertRaises(ValueError):
                    encode_sector(5, code)

    def test_max_length_stays_inside_the_sector(self):
        # 8 + 255 = 263 < 2048, so no PCEX value can cross the sector boundary.
        self.assertLess(HDR_BYTES + 255, SECTOR_BYTES)
        for length in (1, 2, 255):
            self.assertLessEqual(HDR_BYTES + length, SECTOR_BYTES, length)

    def test_max_length_stays_inside_work_ram_and_marker(self):
        # EXEC is $3000, BUF is $2800-$2FFF, so the copy starts after the staging sector;
        # 255 bytes end at $30FE, below MARK $3F00 and work RAM top $4000.
        self.assertEqual(EXEC_ADDRESS, 0x3000)
        self.assertEqual(0x2800 + SECTOR_BYTES, 0x3000)
        self.assertEqual(EXEC_ADDRESS + 255 - 1, 0x30FE)
        self.assertLess(EXEC_ADDRESS + 255, 0x3F00)
        self.assertLess(EXEC_ADDRESS + 255, 0x4000)

    def test_entry_must_stay_inside_the_code(self):
        # entry is one byte too, so the largest valid entry is 254 (with length 255);
        # 255 would point one past the last code byte.
        self.assertLess(254, 255)
        self.assertEqual(EXEC_ADDRESS + 254, 0x30FE)  # last code byte of a 255-byte copy


class PcexOracleBoundaryTests(unittest.TestCase):
    """The host oracle (independent of the generator) agrees on the PCEX boundary values."""

    def judge(self, entry, length, code, checksum=None):
        if checksum is None:
            checksum = sum(code) & 0xFF
        cue, bin_bytes = patched_image(entry, length, checksum, code)
        return oracle.judge(cue, bin_bytes, {"lba_offset": 0, "abort_in_data": False})

    def test_length_zero_is_bad_length(self):
        self.assertEqual(self.judge(0, 0, b""), "BAD_LENGTH")

    def test_max_length_with_valid_checksum_is_ok(self):
        code = bytes([0xEA] * 255)
        self.assertEqual(self.judge(0, 255, code), "OK")

    def test_entry_at_length_and_past_it_are_bad_entry(self):
        code = b"\x01\x02"
        self.assertEqual(self.judge(2, 2, code), "BAD_ENTRY_OFFSET")
        self.assertEqual(self.judge(3, 2, code), "BAD_ENTRY_OFFSET")

    def test_entry_last_byte_ok_past_last_rejected(self):
        code = bytes([0x42] * 255)
        self.assertEqual(self.judge(254, 255, code), "OK")
        self.assertEqual(self.judge(255, 255, code), "BAD_ENTRY_OFFSET")

    def test_length_one_entry_zero_ok_entry_one_rejected(self):
        self.assertEqual(self.judge(0, 1, b"\x42"), "OK")
        self.assertEqual(self.judge(1, 1, b"\x42"), "BAD_ENTRY_OFFSET")

    def test_checksum_covers_exactly_the_declared_code(self):
        code = b"\x01\x02"
        self.assertEqual(self.judge(0, 2, code, checksum=4), "BAD_CHECKSUM")
        # Bytes after the declared code do not change the checksum scope.
        self.assertEqual(self.judge(0, 2, code, checksum=3), "OK")

    def test_magic_is_checked_first(self):
        img = gen.build_case_image("NONE", "V1")
        index01 = oracle.parse_tracks(img.cue)[1]["index01"]
        bad = bytearray(img.bin)
        bad[index01 * RAW + USER_OFFSET] ^= 0xFF
        self.assertEqual(oracle.judge(img.cue, bytes(bad), {"lba_offset": 0, "abort_in_data": False}),
                         "BAD_MAGIC")


class PcexJudgeBoundaryTests(unittest.TestCase):
    """Synthetic-record judges: partial copy and overshoot must be rejected, not only full copy."""

    def test_synthetic_success_at_max_length_is_accepted(self):
        code = bytes([0xEA] * 255)
        judge.judge_success(success_observation(code), code, SYMBOLS, 5, 1)

    def test_success_tail_overshoot_is_rejected(self):
        records = success_observation()
        data = bytearray(bytes.fromhex(records["RUN"][0]["buffer_hex"]))
        data[judge.EXEC_OFF + len(PAYLOAD)] ^= 0x01  # one byte past the copied code
        records["RUN"][0]["buffer_hex"] = data.hex()
        with self.assertRaises(ValueError) as caught:
            judge.judge_success(records, PAYLOAD, SYMBOLS, 5, 1)
        self.assertIn("overshoot", str(caught.exception))

    def test_success_far_tail_byte_is_rejected(self):
        records = success_observation()
        data = bytearray(bytes.fromhex(records["RUN"][0]["buffer_hex"]))
        data[judge.EXEC_WINDOW_END_OFF - 1] ^= 0x80  # $30FF guarded byte (tail far from a short copy)
        records["RUN"][0]["buffer_hex"] = data.hex()
        with self.assertRaises(ValueError) as caught:
            judge.judge_success(records, PAYLOAD, SYMBOLS, 5, 1)
        self.assertIn("overshoot", str(caught.exception))

    def test_failure_with_untouched_exec_is_accepted(self):
        judge.judge_failure(failure_observation(), 20, 11, SECTOR_BYTES)

    def test_failure_with_partial_prefix_copy_is_rejected(self):
        records = failure_observation()
        for run in records["RUN"]:
            data = bytearray(bytes.fromhex(run["buffer_hex"]))
            data[judge.EXEC_OFF:judge.EXEC_OFF + 10] = PAYLOAD[:10]  # prefix only, not the full code
            run["buffer_hex"] = data.hex()
        with self.assertRaises(ValueError) as caught:
            judge.judge_failure(records, 20, 11, SECTOR_BYTES)
        self.assertIn("copy happened", str(caught.exception))

    def test_failure_with_single_byte_copy_is_rejected(self):
        records = failure_observation()
        data = bytearray(bytes.fromhex(records["RUN"][1]["buffer_hex"]))
        data[judge.EXEC_OFF] = PAYLOAD[0]
        records["RUN"][1]["buffer_hex"] = data.hex()
        with self.assertRaises(ValueError) as caught:
            judge.judge_failure(records, 20, 11, SECTOR_BYTES)
        self.assertIn("copy happened", str(caught.exception))

    def test_failure_with_far_sentinel_byte_is_rejected(self):
        records = failure_observation()
        data = bytearray(bytes.fromhex(records["RUN"][0]["buffer_hex"]))
        data[judge.EXEC_WINDOW_END_OFF - 1] ^= 0x01  # $30FF guarded byte
        records["RUN"][0]["buffer_hex"] = data.hex()
        with self.assertRaises(ValueError) as caught:
            judge.judge_failure(records, 20, 11, SECTOR_BYTES)
        self.assertIn("copy happened", str(caught.exception))

    def test_check_copy_exact_rejects_wrong_length(self):
        records = success_observation()
        with self.assertRaises(ValueError):
            judge.check_copy_exact(records, b"")
        with self.assertRaises(ValueError):
            judge.check_copy_exact(records, bytes(256))

    def test_check_exec_untouched_rejects_missing_buffer(self):
        records = failure_observation()
        del records["RUN"][0]["buffer_hex"]
        with self.assertRaises((ValueError, KeyError)):
            judge.check_exec_untouched(records)


if __name__ == "__main__":
    unittest.main()
