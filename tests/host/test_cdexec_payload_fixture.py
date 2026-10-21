"""Issue #240: 合成 CD_EXEC ロードペイロード fixture の host 検査（エミュレータ・cc65・ROM 不要）。"""
import hashlib
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cdexec_payload_fixture as fx  # noqa: E402


def good_sector():
    return fx.build_sector(fx.PAYLOAD_CODE, 0)


def raw_sector(length, entry_off=0, code=None):
    """長さ・entry を生値で書いたセクタ（build_sector の範囲検査を迂回する負例用）。"""
    body = fx.PAYLOAD_CODE if code is None else code
    header = fx.MAGIC + length.to_bytes(2, "little") + entry_off.to_bytes(2, "little")
    return (header + body).ljust(fx.SECTOR_BYTES, b"\x00")


class PayloadFixturePositiveTests(unittest.TestCase):
    def test_pinned_hashes_match_recomputed_values(self):
        self.assertEqual(hashlib.sha256(fx.PAYLOAD_CODE).hexdigest(), fx.EXPECTED_CODE_SHA256)
        self.assertEqual(hashlib.sha256(good_sector()).hexdigest(), fx.EXPECTED_SECTOR_SHA256)

    def test_listing_is_consistent_and_writes_only_marker(self):
        self.assertEqual(len(fx.PAYLOAD_CODE), 11)
        self.assertEqual(fx.PAYLOAD_CODE[-1], 0x60)  # RTS
        # STA abs の宛先は $3F00 / $3F01 のみ（マーカー外へ書かない）
        stores = [code for code, text in fx.LISTING if text.startswith("STA")]
        self.assertEqual([int.from_bytes(s[1:3], "little") for s in stores], [fx.MARKER_ADDR, fx.MARKER_ADDR + 1])

    def test_sector_layout(self):
        s = good_sector()
        self.assertEqual(len(s), fx.SECTOR_BYTES)
        self.assertEqual(s[:4], fx.MAGIC)
        self.assertEqual(int.from_bytes(s[4:6], "little"), len(fx.PAYLOAD_CODE))
        self.assertEqual(int.from_bytes(s[6:8], "little"), 0)
        self.assertEqual(s[fx.HDR_BYTES:fx.HDR_BYTES + len(fx.PAYLOAD_CODE)], fx.PAYLOAD_CODE)
        self.assertEqual(set(s[fx.HDR_BYTES + len(fx.PAYLOAD_CODE):]), {0})

    def test_load_payload_accepts_good_sector(self):
        plan = fx.load_payload(good_sector())
        self.assertEqual(plan.dest, fx.PLACE_BASE)
        self.assertEqual(plan.entry, fx.EXPECTED_ENTRY)
        self.assertEqual(plan.code, fx.PAYLOAD_CODE)

    def test_marker_check(self):
        self.assertTrue(fx.marker_present(b"\xa5\x5a"))
        self.assertFalse(fx.marker_present(b"\x00\x00"))
        self.assertFalse(fx.marker_present(b"\xa5"))

    def test_size_boundaries_for_builder_and_structural_check(self):
        self.assertEqual(len(fx.build_sector(bytes(fx.MAX_PAYLOAD))), fx.SECTOR_BYTES)
        self.assertEqual(fx.plan_load(fx.build_sector(bytes(fx.MAX_PAYLOAD))).code, bytes(fx.MAX_PAYLOAD))
        with self.assertRaises(ValueError):
            fx.build_sector(b"")
        with self.assertRaises(ValueError):
            fx.build_sector(bytes(fx.MAX_PAYLOAD + 1))
        with self.assertRaises(ValueError):
            fx.build_sector(b"\x60", entry_offset=1)


class PayloadFixtureNegativeTests(unittest.TestCase):
    def assertReason(self, reason, fn, *args):
        with self.assertRaises(fx.PayloadError) as ctx:
            fn(*args)
        self.assertEqual(ctx.exception.reason, reason)

    def test_no_media(self):
        self.assertReason("NO_MEDIA", fx.load_payload, None)

    def test_short_or_long_sector(self):
        self.assertReason("BAD_SECTOR_SIZE", fx.load_payload, good_sector()[:-1])
        self.assertReason("BAD_SECTOR_SIZE", fx.load_payload, good_sector() + b"\x00")
        self.assertReason("BAD_SECTOR_SIZE", fx.load_payload, b"")

    def test_bad_magic(self):
        s = bytearray(good_sector())
        s[0] ^= 0xFF
        self.assertReason("BAD_MAGIC", fx.load_payload, bytes(s))

    def test_invalid_length(self):
        for length in (0, fx.MAX_PAYLOAD + 1, 0xFFFF):
            with self.subTest(length=length):
                self.assertReason("BAD_LENGTH", fx.load_payload, raw_sector(length))

    def test_entry_not_inside_payload(self):
        self.assertReason("BAD_ENTRY", fx.load_payload, raw_sector(len(fx.PAYLOAD_CODE), len(fx.PAYLOAD_CODE)))
        self.assertReason("BAD_ENTRY", fx.load_payload, raw_sector(len(fx.PAYLOAD_CODE), 0xFFFF))

    def test_destination_outside_region(self):
        cases = {"below_base": 0x2FFF, "entry_shift": 0x3001, "overlaps_marker": 0x3F00,
                 "far_outside": 0xFFF0}
        for name, dest in cases.items():
            with self.subTest(case=name):
                self.assertReason("BAD_DEST", fx.load_payload, good_sector(), dest)

    def test_max_payload_at_base_stays_below_marker(self):
        code = bytes(fx.MAX_PAYLOAD)
        plan = fx.plan_load(fx.build_sector(code, 0), fx.PLACE_BASE)
        self.assertLess(plan.dest + len(plan.code), fx.MARKER_ADDR)

    def test_corrupted_payload_byte_fails_sha(self):
        s = bytearray(good_sector())
        s[fx.HDR_BYTES + 1] ^= 0x01
        self.assertReason("BAD_SHA256", fx.load_payload, bytes(s))

    def test_corrupted_padding_byte_fails_sha(self):
        s = bytearray(good_sector())
        s[-1] ^= 0x01
        self.assertReason("BAD_SHA256", fx.load_payload, bytes(s))

    def test_structural_check_does_not_hide_sha_corruption_as_success(self):
        s = bytearray(good_sector())
        s[fx.HDR_BYTES] ^= 0x80
        fx.plan_load(bytes(s))  # 構造は正しいので通る
        self.assertReason("BAD_SHA256", fx.load_payload, bytes(s))


if __name__ == "__main__":
    unittest.main()
