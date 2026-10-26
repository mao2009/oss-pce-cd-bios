"""ABI 準備の独立ホストモデル検証（Issue #75、スロット $41 MA_MUL16U）。

参照 System Card の MA_MUL16U ABI は未検証（UNKNOWN）。本テストが固定するのは
プロジェクト内の DEV 契約（src/overrides/slot_41.s）と、それを独立に記述したホストモデル
（tools/ma_mul16u_abi_model.py）の整合性だけであり、参照 ABI を主張しない。

検証内容:
- 積の 32-bit 形式とバイト順（リトルエンディアン 4 バイト）
- 最大積 $FFFF*$FFFF = $FFFE0001 と繰り上がり（キャリー）境界
- 部分積の和（シフト加算ではない独立経路）との一致
- DEV ルーチンがゼロページを書かず、スタックページ $21xx 以外へ書かないこと（メモリ破壊の範囲）
"""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from ma_mul16u_abi_model import model_product_bytes, model_product_word  # noqa: E402

SLOT_41 = ROOT / "src" / "overrides" / "slot_41.s"
DOC = ROOT / "docs" / "ma-mul16u-abi-prep.md"


class Mul16uAbiModelTests(unittest.TestCase):
    def test_max_product_is_fffe0001_little_endian(self):
        self.assertEqual(model_product_word(0xFFFF, 0xFFFF), 0xFFFE0001)
        self.assertEqual(model_product_bytes(0xFFFF, 0xFFFF), (0x01, 0x00, 0xFE, 0xFF))

    def test_byte_order_of_small_product(self):
        self.assertEqual(model_product_bytes(0x1234, 0x0001), (0x34, 0x12, 0x00, 0x00))
        self.assertEqual(model_product_bytes(0x0001, 0x1234), (0x34, 0x12, 0x00, 0x00))

    def test_carry_into_high_word(self):
        # 0x8000 * 2 = 0x10000: the low word overflows into bit 16
        self.assertEqual(model_product_bytes(0x8000, 0x0002), (0x00, 0x00, 0x01, 0x00))
        # 0x00FF * 0x0101 = 0xFFFF (no carry); 0x0100 * 0x0100 = 0x10000
        self.assertEqual(model_product_word(0x00FF, 0x0101), 0xFFFF)
        self.assertEqual(model_product_word(0x0100, 0x0100), 0x10000)

    def test_zero_and_identity(self):
        self.assertEqual(model_product_word(0, 0xFFFF), 0)
        self.assertEqual(model_product_word(0xFFFF, 0), 0)
        self.assertEqual(model_product_word(1, 0xABCD), 0xABCD)

    def test_model_matches_plain_product_on_sampled_pairs(self):
        edges = (0, 1, 2, 0x00FF, 0x0100, 0x7FFF, 0x8000, 0xAAAA, 0x5555, 0xFFFE, 0xFFFF)
        for m in edges:
            for q in edges:
                self.assertEqual(model_product_word(m, q), m * q, (m, q))
                self.assertEqual(
                    sum(b << (8 * i) for i, b in enumerate(model_product_bytes(m, q))),
                    m * q,
                    (m, q),
                )

    def test_routine_writes_only_stack_page(self):
        """メモリ破壊の範囲: 絶対書き込み先は $21xx（スタック）のみで、ZP（$00-$FF）へ書かない。"""
        source = SLOT_41.read_text(encoding="utf-8")
        code = [line.split(";", 1)[0] for line in source.splitlines()]
        writes = re.compile(
            r"^\s*(sta|stx|sty|stz|st0|st1|st2|ror|rol|inc|dec|asl|lsr|adc|sbc|ora|and|eor|trb|tsb|smb\d|rmb\d|tia|tii|tdd|tin|tai)\s+(\$[0-9A-Fa-f]+)",
            re.IGNORECASE,
        )
        operands = []
        for line in code:
            m = writes.match(line)
            if m:
                operands.append(m.group(2))
        self.assertTrue(operands, "write operands not found in slot_41.s")
        for op in operands:
            addr = int(op[1:], 16)
            self.assertGreaterEqual(addr, 0x2100, op)
            self.assertLessEqual(addr, 0x21FF, op)

    def test_source_declares_no_zero_page_storage(self):
        """弱い確認: ZP 宣言の不在のみ。実際の ZP 書き込みは test_routine_writes_only_stack_page が見る。"""
        source = SLOT_41.read_text(encoding="utf-8")
        self.assertNotIn(".zeropage", source)
        self.assertNotIn("ma_mul16u_zp", source)

    def test_doc_records_unknown_reference_items(self):
        self.assertTrue(DOC.exists(), "docs/ma-mul16u-abi-prep.md missing")
        text = DOC.read_text(encoding="utf-8")
        for needle in ("UNKNOWN", "VERIFIED", "0xE0C3", "carry", "clobber"):
            self.assertIn(needle, text)


if __name__ == "__main__":
    unittest.main()
