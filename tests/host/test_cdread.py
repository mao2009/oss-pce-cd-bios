"""Host checks for the Issue #5 CD read diagnostic ROM and its judge (no emulator needed)."""
import copy
import hashlib
from pathlib import Path
import tempfile
import unittest
from tools.environment import requires_cc65

from tools.build_cdread import (
    BANK_BASE, DEFAULT_COUNT, DEFAULT_LBA, EXPECTED_MPR, HALT_CONFIRM_STEPS, REQUIRED_SYMBOLS, build_cdread, code_digest,
    parse_symbols, read_request, verify_cdread, with_request,
)
from tools.build_rom import build
from tools.cdread_verify import check_success, describe_failure, result_block
from tools.make_cd_image import DEFAULT_SECTORS, sector_payload


@requires_cc65
class CdReadBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.original = build(cls.root / "original")
        cls.debug_path = build_cdread(cls.original, cls.root / "debug", "debug")
        cls.release_path = build_cdread(cls.original, cls.root / "release", "release")
        cls.base = cls.original.read_bytes()
        cls.payload = cls.debug_path.read_bytes()
        cls.symbols = parse_symbols(cls.root / "debug/cdread.lbl")
        cls.digest = code_digest(cls.payload, cls.symbols)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_debug_and_release_are_byte_identical_and_api_table_untouched(self):
        self.assertEqual(self.payload, self.release_path.read_bytes())
        self.assertEqual(len(self.payload), 262144)
        self.assertEqual(self.payload[:0x1000], self.base[:0x1000])
        self.assertEqual(verify_cdread(self.payload, self.base, self.symbols, self.digest),
                         hashlib.sha256(self.payload).hexdigest())

    def test_symbols_and_vectors(self):
        self.assertEqual(set(self.symbols), set(REQUIRED_SYMBOLS))
        self.assertEqual(self.symbols["cd_entry"], 0xF000)
        irq = self.symbols["cd_unexpected_interrupt"].to_bytes(2, "little")
        self.assertEqual(self.payload[0x1ff6:0x1ffe], irq * 4)
        self.assertEqual(self.payload[0x1ffe:0x2000], b"\x00\xf0")

    def test_default_request_is_embedded_read6(self):
        self.assertEqual(read_request(self.payload, self.symbols), (DEFAULT_LBA, DEFAULT_COUNT))
        at = self.symbols["cd_cdb"] - BANK_BASE
        self.assertEqual(self.payload[at:at + 6], bytes((0x08, 0, 0, DEFAULT_LBA, DEFAULT_COUNT, 0)))

    def test_with_request_roundtrip_and_limits(self):
        patched = with_request(self.payload, self.symbols, 0x1FFFFF, 255)
        self.assertEqual(read_request(patched, self.symbols), (0x1FFFFF, 255))
        self.assertEqual(verify_cdread(patched, self.base, self.symbols, self.digest),
                         hashlib.sha256(patched).hexdigest())  # the request is test input, not code
        for lba, count in ((1 << 21, 1), (-1, 1), (0, 256), (0, -1)):
            with self.assertRaises(ValueError):
                with_request(self.payload, self.symbols, lba, count)

    def test_every_non_request_byte_flip_in_the_code_bank_fails_closed(self):
        request = range(self.symbols["cd_cdb"] - BANK_BASE, self.symbols["cd_cdb"] - BANK_BASE + 6)
        for offset in range(0x1000, 0x2000):
            if offset in request:
                continue
            damaged = bytearray(self.payload)
            damaged[offset] ^= 1
            with self.assertRaises(ValueError, msg=f"offset {offset:#x}"):
                verify_cdread(bytes(damaged), self.base, self.symbols, self.digest)

    def test_other_structural_corruptions_fail(self):
        for offset in (0, 0x0fff, 0x2000, 0x2001, 262143):
            damaged = bytearray(self.payload)
            damaged[offset] ^= 0xFF
            with self.assertRaises(ValueError):
                verify_cdread(bytes(damaged), self.base, self.symbols)
        with self.assertRaises(ValueError):
            verify_cdread(self.payload[:-1], self.base, self.symbols)
        with self.assertRaises(ValueError):
            verify_cdread(self.payload, self.base[:-1], self.symbols)

    def test_label_parser_rejects_conflicts_and_missing_names(self):
        labels = (self.root / "debug/cdread.lbl").read_text()
        with tempfile.TemporaryDirectory() as tmp:
            conflicting = Path(tmp) / "conflict.lbl"
            conflicting.write_text(labels + "al 00F001 .cd_entry\n")
            with self.assertRaisesRegex(ValueError, "contradictory"):
                parse_symbols(conflicting)
            missing = Path(tmp) / "missing.lbl"
            missing.write_text("\n".join(l for l in labels.splitlines() if not l.endswith(" .cd_select")) + "\n")
            with self.assertRaisesRegex(ValueError, "mismatch"):
                parse_symbols(missing)


def good_records(symbols, lba=5, count=1):
    """Synthetic observations of a correct run (host-only test input for the judge)."""
    expected = b"".join(sector_payload(lba + i) for i in range(count))
    buffer = expected + bytes([0x5A]) * (6144 - len(expected))
    result = bytes((0, 9)) + len(expected).to_bytes(2, "little") + bytes(28)
    run = lambda cycle: {"cycle": cycle, "name": "cd_halt_ok", "reason": "cd_halt_ok", "step": 1000,
                         "halt_confirmed": True, "halted_after_step": 1000 - HALT_CONFIRM_STEPS,
                         "halt_confirmation_steps": HALT_CONFIRM_STEPS, "halt_confirmation_required": HALT_CONFIRM_STEPS,
                         "pc": symbols["cd_halt_ok"], "x": 0xC0, "sp": 0xFF, "p": 0x24, "mpr": list(EXPECTED_MPR),
                         "result_hex": result.hex(), "buffer_hex": buffer.hex()}
    phases = lambda cycle: [
        {"cycle": cycle, "scsi_phase": p, "load_sector": s, "load_count": c, "pc": 0xF100}
        for p, s, c in ((3, 0, 0), (4, lba, count), (7, lba + count, 0), (6, lba + count, 0), (0, lba + count, 0))]
    return {"CAP": [{"instruction_step": True, "cdrom_hardware": True, "cdrom_media": True}],
            "RUN": [run("cold"), run("warm")], "PHASE": phases("cold") + phases("warm"), "HIT": [], "BLOCK": []}


class JudgeTests(unittest.TestCase):
    symbols = {name: 0xF000 + i * 16 for i, name in enumerate(REQUIRED_SYMBOLS)}
    disc = [sector_payload(i) for i in range(DEFAULT_SECTORS)]

    def judge(self, records, lba=5, count=1):
        check_success(records, self.symbols, self.disc, lba, count)

    def test_accepts_correct_single_and_multi_sector_reads(self):
        self.judge(good_records(self.symbols))
        self.judge(good_records(self.symbols, 13, 3), 13, 3)
        self.assertEqual(result_block(good_records(self.symbols)["RUN"][0])["bytes"], 2048)

    def test_rejects_every_single_bit_flip_in_first_middle_last_byte_of_each_cycle(self):
        for cycle in (0, 1):
            for offset in (0, 1, 1023, 2047, 2048):  # 2048 = overrun past the requested sector
                records = copy.deepcopy(good_records(self.symbols))
                data = bytearray(bytes.fromhex(records["RUN"][cycle]["buffer_hex"]))
                data[offset] ^= 1
                records["RUN"][cycle]["buffer_hex"] = data.hex()
                with self.assertRaises(ValueError, msg=f"cycle {cycle} offset {offset}"):
                    self.judge(records)

    def test_rejects_wrong_expectation_and_wrong_request(self):
        with self.assertRaises(ValueError):
            self.judge(good_records(self.symbols), lba=6)  # data is sector 5, judged as 6
        with self.assertRaises(ValueError):
            self.judge(good_records(self.symbols), lba=5, count=2)
        for lba, count in ((16, 1), (15, 2), (0, 0)):
            with self.assertRaises(ValueError):
                self.judge(good_records(self.symbols), lba, count)

    def test_rejects_non_success_and_environment_differences(self):
        symbols_pc_other = 0xF123
        mutations = {
            "failed_halt": lambda r: r["RUN"][0].update(reason="cd_halt_fail"),
            "stuck_pc": lambda r: r["RUN"][1].update(pc=0xF123),
            "budget": lambda r: r["RUN"][0].update(reason="budget"),
            "x": lambda r: r["RUN"][0].update(x=0xB0),
            "sp": lambda r: r["RUN"][1].update(sp=0xFE),
            "interrupt_flag": lambda r: r["RUN"][0].update(p=0x20),
            "decimal_flag": lambda r: r["RUN"][0].update(p=0x2C),
            "mpr": lambda r: r["RUN"][0]["mpr"].__setitem__(2, 9),
            "short_count": lambda r: r["RUN"][0].update(result_hex=(bytes((0, 9, 0, 4)) + bytes(28)).hex()),
            "scsi_status": lambda r: r["RUN"][0].update(result_hex=(bytes((0, 9, 0, 8, 2, 0)) + bytes(26)).hex()),
            "error_code": lambda r: r["RUN"][0].update(result_hex=(bytes((5, 5, 0, 8)) + bytes(28)).hex()),
            "missing_warm": lambda r: r["RUN"].pop(),
            "budget_right_after_arrival": lambda r: r["RUN"][0].update(
                reason="budget", halt_confirmed=False, halt_confirmation_steps=0, step=r["RUN"][0]["halted_after_step"]),
            "only_63_steps_confirmed": lambda r: r["RUN"][0].update(
                halt_confirmed=False, halt_confirmation_steps=63, step=r["RUN"][0]["halted_after_step"] + 63),
            "63_steps_but_flag_true": lambda r: r["RUN"][0].update(halt_confirmation_steps=63),
            "halt_confirmed_missing": lambda r: r["RUN"][0].pop("halt_confirmed"),
            "halt_confirmed_false": lambda r: r["RUN"][1].update(halt_confirmed=False),
            "halt_confirmed_not_bool": lambda r: r["RUN"][0].update(halt_confirmed=1),
            "steps_field_missing": lambda r: r["RUN"][0].pop("halt_confirmation_steps"),
            "arrival_step_missing": lambda r: r["RUN"][0].pop("halted_after_step"),
            "arrival_step_negative": lambda r: r["RUN"][0].update(halted_after_step=-1),
            "arrival_plus_steps_mismatch": lambda r: r["RUN"][1].update(halted_after_step=5),
            "required_below_constant": lambda r: r["RUN"][0].update(
                halt_confirmation_required=1, halt_confirmation_steps=1, halted_after_step=999, step=1000),
            "pc_left_during_confirmation": lambda r: r["RUN"][0].update(
                halt_confirmed=False, pc=symbols_pc_other, halt_confirmation_steps=10),
            "warm_unconfirmed_cold_ok": lambda r: r["RUN"][1].update(
                reason="budget", halt_confirmed=False, halt_confirmation_steps=30),
            "unexpected_interrupt": lambda r: r["RUN"][0].update(reason="cd_unexpected_interrupt"),
            "no_capability": lambda r: r["CAP"][0].update(cdrom_media=False),
            "wrong_phase_order": lambda r: r["PHASE"].__setitem__(slice(0, 5), r["PHASE"][4::-1]),
            "emulator_other_lba": lambda r: r["PHASE"][1].update(load_sector=4),
            "emulator_other_count": lambda r: r["PHASE"][6].update(load_count=2),
        }
        for name, mutate in mutations.items():
            records = copy.deepcopy(good_records(self.symbols))
            mutate(records)
            with self.assertRaises((ValueError, KeyError), msg=name):
                self.judge(records)

    def test_describe_failure_reports_code_stage_and_bytes(self):
        records = good_records(self.symbols)
        records["RUN"][0].update(reason="cd_halt_fail", result_hex=(bytes((5, 5, 0, 16)) + bytes(28)).hex())
        self.assertEqual(describe_failure(records)[0], ("cd_halt_fail", 5, 5, 4096))


if __name__ == "__main__":
    unittest.main()
