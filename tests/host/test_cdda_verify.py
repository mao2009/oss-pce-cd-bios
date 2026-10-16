"""Host checks for the Issue #15 CD-DA judge on synthetic observations (no emulator needed)."""
import unittest

from tools.cdda_verify import DISTINCT_RIGHT, IDLE, PAUSED, PLAYING, SAMPLES_PER_SECTOR, SCRIPT, STOPPED, check_all, pcm
from tools.make_cd_image import audio_frame


def hit(name, step, x, state, lba=0, sample=0, start=0):
    return {"name": name, "step": step, "x": x, "scsi_phase": 0,
            "audio": {"state": state, "start_lba": start, "stop_lba": 79, "current_lba": lba, "current_sample": sample}}


def synthetic(played=SAMPLES_PER_SECTOR + 10):
    """A run shaped like the real Geargrafx output: READ, play `played` samples, pause, stop."""
    rows = [[16 + i // SAMPLES_PER_SECTOR, i % SAMPLES_PER_SECTOR, *pcm(16 + i // SAMPLES_PER_SECTOR, i % SAMPLES_PER_SECTOR), 200 + i]
            for i in range(played)]
    lba, sample = divmod(16 * SAMPLES_PER_SECTOR + played, SAMPLES_PER_SECTOR)
    mid = rows[-5][:2]
    hits = [hit("cdda_cmd_done", 100, 0, IDLE),
            hit("cdda_cmd_done", 199 + 3, 1, PLAYING, 16, 3, 16),
            hit("cdda_wait_done", 200 + played - 5, 0, PLAYING, *mid, 16),
            hit("cdda_cmd_done", 200 + played - 2, 2, PLAYING, lba, sample, 16),
            hit("cdda_cmd_done", 200 + played + 10, 3, PAUSED, lba, sample, 16),
            hit("cdda_wait_done", 200 + played + 20, 0, PAUSED, lba, sample, 16),
            hit("cdda_cmd_done", 200 + played + 30, 4, PAUSED, lba, sample, 16),
            hit("cdda_cmd_done", 200 + played + 40, 5, STOPPED, lba, sample, 16),
            hit("cdda_wait_done", 200 + played + 50, 0, STOPPED, lba, sample, 16),
            hit("cdda_cmd_done", 200 + played + 60, 6, STOPPED, lba, sample, 16)]
    result = bytearray(0xB0)
    for index, (_, size) in enumerate(SCRIPT):
        result[0x10 + 4 * index + 2:0x10 + 4 * index + 4] = size.to_bytes(2, "little")
    rel, ab = lba - 16, lba + 150
    msf = lambda f: bytes(((f // 4500 // 10) << 4 | f // 4500 % 10, (f // 75 % 60 // 10) << 4 | f // 75 % 60 % 10,
                           (f % 75 // 10) << 4 | f % 75 % 10))
    for offset, state in ((0x80, PLAYING), (0x90, PAUSED), (0xA0, STOPPED)):
        result[offset:offset + 10] = bytes((state, 1, 2, 1)) + msf(rel) + msf(ab)
    run = {"reason": "cdda_halt_ok", "halt_confirmed": True, "unattributed_nonzero": 0,
           "result_hex": result.hex(), "buffer_hex": (audio_frame(0, DISTINCT_RIGHT)[16:2064] + b"\x5a" * 256).hex()}
    return {"CAP": [{"instruction_step": True, "cdrom_hardware": True, "cdrom_media": True}], "HIT": hits,
            "AUD": rows, "JUMP": [[0, 0, 16, 0, 199]], "RUN": [run]}


class CddaJudgeTests(unittest.TestCase):
    def test_well_formed_observation_passes(self):
        observed = check_all(synthetic())
        self.assertEqual(observed["cdda_play"]["samples_verified_against_bin"], SAMPLES_PER_SECTOR + 10)
        self.assertEqual(observed["read6_on_audio_lba"]["cdda_state_after"], "IDLE")

    def test_every_mutation_is_rejected(self):
        def mutate(path):
            records = synthetic()
            path(records)
            return records
        def set_result(records, offset, value):
            data = bytearray(bytes.fromhex(records["RUN"][0]["result_hex"]))
            data[offset] = value
            records["RUN"][0]["result_hex"] = data.hex()
        cases = {
            "pcm_sample_wrong": lambda r: r["AUD"][300].__setitem__(2, r["AUD"][300][2] + 1),
            "channels_swapped": lambda r: r["AUD"].__setitem__(300, [*r["AUD"][300][:2], r["AUD"][300][3], r["AUD"][300][2], r["AUD"][300][4]]),
            "gap_in_stream": lambda r: r["AUD"].pop(100),
            "too_few_samples": lambda r: r.update(AUD=r["AUD"][:10]),
            "samples_before_play": lambda r: r["AUD"][0].__setitem__(4, 50),
            "read6_started_audio": lambda r: r["HIT"][0]["audio"].update(state=PLAYING),
            "read6_data_wrong": lambda r: r["RUN"][0].update(buffer_hex="00" + r["RUN"][0]["buffer_hex"][2:]),
            "not_playing_after_d8": lambda r: r["HIT"][1]["audio"].update(state=PAUSED),
            "no_seek_jump": lambda r: r.update(JUMP=[]),
            "position_moved_while_paused": lambda r: r["HIT"][5]["audio"].update(current_sample=0),
            "not_stopped_after_d9": lambda r: r["HIT"][7]["audio"].update(state=PAUSED),
            "subq_state_wrong": lambda r: set_result(r, 0x90, PLAYING),
            "subq_track_wrong": lambda r: set_result(r, 0xA2, 1),
            "bad_scsi_status": lambda r: set_result(r, 0x14, 2),
            "guest_failed": lambda r: set_result(r, 0, 4),
            "unexplained_audio": lambda r: r["RUN"][0].update(unattributed_nonzero=3),
            "budget_stop": lambda r: r["RUN"][0].update(reason="budget"),
            "checkpoint_missing": lambda r: r["HIT"].pop(5),
            "no_stepping": lambda r: r["CAP"][0].update(instruction_step=False),
        }
        for name, change in cases.items():
            with self.subTest(name), self.assertRaises(ValueError):
                check_all(mutate(change))


if __name__ == "__main__":
    unittest.main()
