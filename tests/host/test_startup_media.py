"""Host checks for the #142 startup-probe media / no-media classifier (synthetic records only)."""
import copy
import unittest

from tools.startup_media import DISC_LOADER, NO_MEDIA_LOADER, classify_startup_media

CHECKPOINT = 0xF03D
STATE = {"pc": CHECKPOINT, "sp": 255, "work_ram_0200_hex": "424f4f542101" + "00" * 58}


def records(media):
    cap = {"instruction_step": True, "pc_before": 0xF000, "pc_after": 0xF001, "cdrom_hardware": True,
           "cdrom_media": media, "disc_given": media, "scsi_phase": 0, "cd_irq_active": 0,
           "loader": DISC_LOADER if media else NO_MEDIA_LOADER}
    obs = [dict(STATE, phase="loaded", pc=0xF000)] + [dict(STATE, phase=p) for p in ("cold", "nonreturning", "warm")]
    return {"CAP": [cap], "OBS": obs}


class ClassifyTests(unittest.TestCase):
    def test_both_branches_recorded_separately(self):
        none = classify_startup_media(records(False), CHECKPOINT)
        disc = classify_startup_media(records(True), CHECKPOINT)
        self.assertEqual((none["media"], disc["media"]), ("NO_MEDIA", "MEDIA_PRESENT"))
        self.assertEqual(set(none), {"media", "cpu_executed", "cd_state", "ram_marker", "stop_pcs"})
        self.assertEqual(none["stop_pcs"], [CHECKPOINT, CHECKPOINT, CHECKPOINT])
        self.assertEqual(none["ram_marker"], "424f4f5421")
        self.assertEqual(none["cd_state"], {"scsi_phase": 0, "cd_irq_active": 0})

    def forged(self, media, mutate):
        data = copy.deepcopy(records(media))
        mutate(data)
        with self.assertRaises(ValueError):
            classify_startup_media(data, CHECKPOINT)

    def test_forged_media_claims_rejected(self):
        # a no-media run claiming media, a disc run claiming none, and mixed loader/flags
        self.forged(False, lambda r: r["CAP"][0].update(cdrom_media=True))
        self.forged(False, lambda r: r["CAP"][0].update(disc_given=True))
        self.forged(False, lambda r: r["CAP"][0].update(loader=DISC_LOADER))
        self.forged(True, lambda r: r["CAP"][0].update(cdrom_media=False))
        self.forged(True, lambda r: r["CAP"][0].update(loader=NO_MEDIA_LOADER))

    def test_missing_evidence_rejected(self):
        for media in (False, True):
            self.forged(media, lambda r: r["CAP"][0].update(cdrom_hardware=False))
            self.forged(media, lambda r: r["CAP"][0].pop("scsi_phase"))
            self.forged(media, lambda r: r["CAP"][0].update(instruction_step=False))
            self.forged(media, lambda r: r.update(CAP=[]))
            self.forged(media, lambda r: r.update(CAP=r["CAP"] * 2))
            self.forged(media, lambda r: r.update(OBS=[]))

    def test_cpu_not_executed_or_wrong_stop_rejected(self):
        # boundary: PC unchanged after the first step; stop PC one byte off; marker wrong or short
        self.forged(False, lambda r: r["CAP"][0].update(pc_after=0xF000))
        self.forged(False, lambda r: r["OBS"][2].update(pc=CHECKPOINT + 1))
        self.forged(False, lambda r: r["OBS"][3].update(work_ram_0200_hex="424f4f5420" + "00" * 59))
        self.forged(False, lambda r: r["OBS"][1].update(work_ram_0200_hex="424f4f"))

    def test_cd_controller_not_idle_rejected(self):
        self.forged(False, lambda r: r["CAP"][0].update(scsi_phase=4))
        self.forged(False, lambda r: r["CAP"][0].update(cd_irq_active=8))


if __name__ == "__main__":
    unittest.main()
