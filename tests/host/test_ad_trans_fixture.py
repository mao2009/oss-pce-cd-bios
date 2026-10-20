"""Issue #298: synthetic AD_TRANS 1-sector model and judge negatives. Model: tools/ad_trans_fixture.py.

Host-only. Passing these tests is NOT evidence of the AD_TRANS callee ABI or real hardware.
"""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import ad_trans_fixture as ad  # noqa: E402

DISC = 16
S = ad.SECTOR


def req(**kw):
    base = {'lba': 5, 'count': 1, 'dest': 0x1000, 'busy': False, 'media': True}
    base.update(kw)
    return base


def run(r, ram=None):
    before = bytearray(ram if ram is not None else b'\xA5' * ad.ADPCM_RAM)
    after = bytearray(before)
    outcome = ad.model_transfer(r, DISC, after)
    return ad.judge(r, DISC, outcome, bytes(before), bytes(after)), outcome, bytes(after)


class FixtureTest(unittest.TestCase):
    def test_sectors_are_full_length_and_distinct(self):
        sectors = [ad.sector_bytes(n) for n in range(DISC)]
        self.assertTrue(all(len(s) == S for s in sectors))
        self.assertEqual(len(set(sectors)), DISC)

    def test_positive_full_byte_compare(self):
        for lba in range(DISC):
            for dest in (0, 0x1234, ad.ADPCM_RAM - S):
                verdict, outcome, after = run(req(lba=lba, dest=dest))
                self.assertEqual((verdict, outcome), ('PASS', 'OK'), (lba, dest))
                self.assertEqual(after[dest:dest + S], ad.sector_bytes(lba))

    def test_negatives_are_rejected_and_ram_untouched(self):
        cases = {
            'REJECT_NO_MEDIA': req(media=False),
            'REJECT_BUSY': req(busy=True),
            'REJECT_COUNT': req(count=0),
            'REJECT_LBA': req(lba=-1),
            'REJECT_LBA': req(lba=DISC),
            'REJECT_DEST': req(dest=-1),
            'REJECT_DEST': req(dest=ad.ADPCM_RAM - S + 1),
        }
        for want, r in cases.items():
            verdict, outcome, _ = run(r)
            self.assertEqual((verdict, outcome), ('PASS', want), want)

    def test_judge_rejects_forged_success_for_invalid_request(self):
        before = b'\xA5' * ad.ADPCM_RAM
        after = bytearray(before)
        bad = req(dest=ad.ADPCM_RAM)
        after[0:S] = ad.sector_bytes(5)  # would be a write elsewhere, not at the requested dest
        self.assertEqual(ad.judge(bad, DISC, 'OK', before, bytes(after)), 'FAIL')

    def test_judge_rejects_wrong_byte_and_neighbour_corruption(self):
        before = b'\xA5' * ad.ADPCM_RAM
        r = req()
        good = bytearray(before)
        ad.model_transfer(r, DISC, good)
        wrong = bytearray(good)
        wrong[r['dest'] + 7] ^= 1
        self.assertEqual(ad.judge(r, DISC, 'OK', before, bytes(wrong)), 'FAIL')
        neighbour = bytearray(good)
        neighbour[r['dest'] - 1] ^= 1
        self.assertEqual(ad.judge(r, DISC, 'OK', before, bytes(neighbour)), 'FAIL')
        self.assertEqual(ad.judge(r, DISC, 'OK', before, bytes(good)), 'PASS')

    def test_judge_rejects_rejected_outcome_that_changed_ram(self):
        before = b'\xA5' * ad.ADPCM_RAM
        after = bytearray(before)
        after[0] ^= 1
        self.assertEqual(ad.judge(req(busy=True), DISC, 'REJECT_BUSY', before, bytes(after)), 'FAIL')

    def test_declared_not_evidence(self):
        self.assertFalse(ad.RELEASE_ELIGIBLE)
        self.assertEqual(ad.CALLEE_ABI, 'UNKNOWN')


if __name__ == "__main__":
    unittest.main()
