"""Host checks for the Issue #144 firmware-side CD media-state observation point.

Records below are SYNTHETIC (hand-built), not real-core results; they only exercise the judge.
"""
import copy
from pathlib import Path
import tempfile
import unittest

from tools.build_rom import build
from tools.build_cd_media_state import (MAGIC, NO_DATA, SIGNATURE_OK, UNRECOGNISED, build_cd_media_state,
                                        check_pair, judge, verify_cd_media_state)
from tools.build_reset_entry import RESET_CODE, build_reset_entry

SENTINEL = 0x5A


def run_row(cycle, state, nbytes, status=0, message=0, data=b""):
    result = bytes([state, 6, nbytes & 0xFF, nbytes >> 8, status, message]).ljust(32, b"\0")
    buffer = data.ljust(2048, bytes([SENTINEL])).ljust(6144, b"\0")
    return {"name": "reset_ready", "cycle": cycle, "reason": "reset_ready", "halt_confirmed": True,
            "pc": 0xF010, "x": state, "sp": 0xFF, "p": 0x04, "mpr": [0xFF, 0xF8, 0, 0, 0, 0, 0, 0],
            "result_hex": result.hex(), "buffer_hex": buffer.hex()}


def records(state, nbytes, disc, data=b"", status=0, phases=()):
    return {"rom_sha256": "aa",
            "CAP": [{"instruction_step": True, "cdrom_hardware": True, "disc_given": disc, "drive_ready": disc,
                     "loader": "paired:SetCDROMType(STANDARD)+LoadHuCardFromBuffer+ResetMedia(false)"}],
            "HIT": [{"name": "ms_done", "cycle": c} for c in ("cold", "warm")],
            "PHASE": [{"cycle": c, "scsi_phase": p} for c in ("cold", "warm") for p in phases],
            "RUN": [run_row(c, state, nbytes, status, data=data) for c in ("cold", "warm")]}


SIGNED = MAGIC + b"\x01" * 2030
WITH_DISC = records(SIGNATURE_OK, 2048, True, SIGNED, phases=(4,))
NO_DISC = records(NO_DATA, 0, False)


class CdMediaStateBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name)
        cls.base_path = build(root / "base")
        cls.base = cls.base_path.read_bytes()
        cls.rom = build_cd_media_state(cls.base_path, root / "debug", "debug")
        cls.release = build_cd_media_state(cls.base_path, root / "release", "release")
        cls.reset_only = build_reset_entry(cls.base_path, root / "reset", "debug").read_bytes()

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_reproducible_and_verified(self):
        self.assertEqual(self.rom.read_bytes(), self.release.read_bytes())
        verify_cd_media_state(self.rom.read_bytes(), self.base)

    def test_called_from_reset_entry_and_default_reset_rom_unchanged(self):
        data = self.rom.read_bytes()
        self.assertEqual(data[0x1000:0x100D], RESET_CODE[:13])   # same sourced init as #143
        self.assertEqual(data[0x100D], 0x20)                      # JSR cd_media_state
        self.assertEqual(data[0x1010:0x1012], b"\x80\xfe")        # then the #143 reset_ready self-loop
        self.assertEqual(self.reset_only[0x1000:0x1000 + len(RESET_CODE)], RESET_CODE)

    def test_no_unverified_scsi_command(self):
        # Only READ(6) LBA 0, one sector: no TEST UNIT READY, no READ TOC ($DE), no audio command.
        data = self.rom.read_bytes()
        self.assertIn(bytes.fromhex("080000000100"), data[0x1000:0x2000])
        for opcode in (0xD8, 0xD9, 0xDA, 0xDD, 0xDE):
            self.assertNotIn(bytes([opcode, 0x00, 0x00]), data[0x1000:0x2000])

    def test_corruption_rejected(self):
        data = self.rom.read_bytes()
        for offset in (0x100D, 0x1010, 0x1FFE, 1, 0x2000):
            bad = bytearray(data)
            bad[offset] ^= 0xFF
            with self.subTest(hex(offset)), self.assertRaises(ValueError):
                verify_cd_media_state(bytes(bad), self.base)


class CdMediaStateJudgeTests(unittest.TestCase):
    def test_synthetic_positive_states(self):
        self.assertEqual(judge(WITH_DISC), "MEDIA_SIGNATURE_OK")
        self.assertEqual(judge(NO_DISC), "NO_DATA")
        self.assertEqual(judge(records(UNRECOGNISED, 2048, True, bytes(2048), phases=(4,))), "MEDIA_UNRECOGNISED")

    def test_pair_requires_one_variable(self):
        self.assertEqual(check_pair(WITH_DISC, NO_DISC), ("MEDIA_SIGNATURE_OK", "NO_DATA"))
        for mutate in (lambda r: r.update(rom_sha256="bb"),
                       lambda r: r["CAP"][0].update(loader="LoadBiosFromBuffer(syscard)+LoadMedia(cue)"),
                       lambda r: r["CAP"][0].update(disc_given=True)):
            empty = copy.deepcopy(NO_DISC)
            mutate(empty)
            with self.assertRaises(ValueError):
                check_pair(WITH_DISC, empty)
        with self.assertRaises(ValueError):  # same judgment on both sides is no evidence
            check_pair(WITH_DISC, copy.deepcopy(WITH_DISC) | {"CAP": NO_DISC["CAP"]})

    def test_no_media_or_timeout_never_pass(self):
        cases = {
            "error code (selection timeout)": records(0x82, 0, False),
            "in progress": records(0xFF, 0, False),
            "no data but CHECK CONDITION": records(NO_DATA, 0, False, status=2),
            "no data claimed but DATA_IN seen": records(NO_DATA, 0, False, phases=(4,)),
            "no data claimed but buffer written": records(NO_DATA, 0, False, data=b"\0"),
            "signature claimed without magic": records(SIGNATURE_OK, 2048, True, bytes(2048), phases=(4,)),
            "unrecognised claimed with magic": records(UNRECOGNISED, 2048, True, SIGNED, phases=(4,)),
            "short read claimed signature": records(SIGNATURE_OK, 1024, True, SIGNED, phases=(4,)),
        }
        for name, rec in cases.items():
            with self.subTest(name), self.assertRaises(ValueError):
                judge(rec)

    def test_boot_code_effects_rejected(self):
        mutations = {
            "budget": lambda r: r.update(reason="budget", halt_confirmed=False),
            "trap": lambda r: r.update(reason="reset_unexpected_interrupt"),
            "stack unbalanced": lambda r: r.update(sp=0xFD),
            "mpr0 lost": lambda r: r.update(mpr=[0, 0xF8, 0, 0, 0, 0, 0, 0]),
            "irq unmasked": lambda r: r.update(p=0),
            "x differs from result": lambda r: r.update(x=0x01 if r["x"] != 1 else 3),
        }
        for name, mutate in mutations.items():
            bad = copy.deepcopy(NO_DISC)
            mutate(bad["RUN"][1])
            with self.subTest(name), self.assertRaises(ValueError):
                judge(bad)
        missing = copy.deepcopy(NO_DISC)
        missing["HIT"] = missing["HIT"][:1]
        with self.assertRaises(ValueError):
            judge(missing)
        warm_differs = copy.deepcopy(WITH_DISC)
        warm_differs["RUN"][1] = NO_DISC["RUN"][1]
        with self.assertRaises(ValueError):
            judge(warm_differs)


if __name__ == "__main__":
    unittest.main()
