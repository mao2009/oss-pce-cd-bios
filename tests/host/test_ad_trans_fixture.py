"""Issue #298: synthetic AD_TRANS 1-sector model and judge negatives. Model: tools/ad_trans_fixture.py.

Host-only. Passing these tests is NOT evidence of the AD_TRANS callee ABI or real hardware.
"""
from pathlib import Path
import hashlib
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import ad_trans_fixture as ad  # noqa: E402

DISC = 16
S = ad.SECTOR
TOP = ad.ADPCM_RAM - S  # largest valid dest (inclusive)

# Literal SHA-256 of the 2048-byte sector for these LBAs. Written out by hand so the
# expected bytes do not come from the same generator (ad.sector_bytes) under test.
SECTOR_SHA256 = {
    0: 'd8c1fcf73c28dfcda3e1f15f71c4b6872ecd644b7c3b4bcfd8357f558c2b634d',
    5: '498f232ab4d8659ee2b1e9241737a5a7919920befee8766c095292d6d539991a',
    15: '38de473adac24b444a2f40e2b9728eea59027e1b8aee46ea8083f009fafc55ef',
}

BASE = {'lba': 5, 'count': 1, 'dest': 0x1000, 'busy': False, 'media': True}


def req(**kw):
    r = dict(BASE)
    r.update(kw)
    return r


# Named cases: (name, request, expected rejection reason). Every name is unique, so no
# case can silently overwrite another (the earlier dict literal lost two keys).
REJECT_CASES = [
    ('no_media', req(media=False), 'REJECT_NO_MEDIA'),
    ('busy', req(busy=True), 'REJECT_BUSY'),
    ('count_0', req(count=0), 'REJECT_COUNT'),
    ('count_2', req(count=2), 'REJECT_COUNT'),
    ('count_true', req(count=True), 'REJECT_TYPE'),
    ('count_float', req(count=1.0), 'REJECT_TYPE'),
    ('lba_neg_1', req(lba=-1), 'REJECT_LBA'),
    ('lba_upper_exclusive', req(lba=DISC), 'REJECT_LBA'),
    ('lba_neg_huge', req(lba=-(2 ** 64)), 'REJECT_LBA'),
    ('lba_huge', req(lba=2 ** 64), 'REJECT_LBA'),
    ('lba_true', req(lba=True), 'REJECT_TYPE'),
    ('lba_float', req(lba=5.0), 'REJECT_TYPE'),
    ('lba_str', req(lba='5'), 'REJECT_TYPE'),
    ('dest_neg_1', req(dest=-1), 'REJECT_DEST'),
    ('dest_past_end', req(dest=TOP + 1), 'REJECT_DEST'),
    ('dest_at_ram_size', req(dest=ad.ADPCM_RAM), 'REJECT_DEST'),
    ('dest_huge', req(dest=2 ** 64), 'REJECT_DEST'),
    ('dest_true', req(dest=True), 'REJECT_TYPE'),
    ('dest_float', req(dest=4096.0), 'REJECT_TYPE'),
    ('dest_str', req(dest='4096'), 'REJECT_TYPE'),
    ('media_int_0', req(media=0), 'REJECT_TYPE'),
    ('media_str', req(media='no'), 'REJECT_TYPE'),
    ('busy_int_0', req(busy=0), 'REJECT_TYPE'),
    ('busy_str', req(busy='yes'), 'REJECT_TYPE'),
]

# Boundary cases that must succeed (dest TOP is the last valid start, lba DISC-1 the last sector).
VALID_CASES = [
    ('lba_0_dest_0', req(lba=0, dest=0)),
    ('lba_last_dest_top', req(lba=DISC - 1, dest=TOP)),
    ('lba_5_dest_1234', req(lba=5, dest=0x1234)),
]


def run(r, ram=None):
    """Return (verdict, outcome, before, after). A raised exception is reported as 'EXCEPTION:...'."""
    before = bytes(ram if ram is not None else b'\xA5' * ad.ADPCM_RAM)
    after = bytearray(before)
    try:
        outcome = ad.model_transfer(r, DISC, after)
    except Exception as e:  # a crash is a failure, not a rejection
        return 'FAIL', 'EXCEPTION:' + type(e).__name__, before, bytes(after)
    verdict = ad.judge(r, DISC, outcome, before, bytes(after))
    return verdict, outcome, before, bytes(after)


class FixtureTest(unittest.TestCase):
    def test_sectors_are_full_length_and_distinct(self):
        sectors = [ad.sector_bytes(n) for n in range(DISC)]
        self.assertTrue(all(len(s) == S for s in sectors))
        self.assertEqual(len(set(sectors)), DISC)

    def test_sector_digests_match_literals(self):
        for lba, want in SECTOR_SHA256.items():
            self.assertEqual(hashlib.sha256(ad.sector_bytes(lba)).hexdigest(), want, lba)

    def test_positive_valid_cases_write_exact_bytes(self):
        for name, r in VALID_CASES:
            verdict, outcome, before, after = run(r)
            self.assertEqual((verdict, outcome), ('PASS', 'OK'), name)
            d = r['dest']
            self.assertEqual(hashlib.sha256(after[d:d + S]).hexdigest(), SECTOR_SHA256[r['lba']], name)
            self.assertEqual(after[:d], before[:d], name)
            self.assertEqual(after[d + S:], before[d + S:], name)

    def test_negative_named_cases_reject_and_leave_ram_unchanged(self):
        for name, r, want in REJECT_CASES:
            with self.subTest(case=name):
                verdict, outcome, before, after = run(r)
                self.assertEqual(outcome, want)
                self.assertEqual(verdict, 'PASS')
                self.assertEqual(after, before, 'RAM changed on rejected request')

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
