"""独立ホストモデル: 符号なし 16x16 -> 32-bit 積（スロット $41 MA_MUL16U の DEV 契約）。

DEV 実装（src/overrides/slot_41.s）のシフト加算とは別経路で計算する。乗数の各ビットについて
被乗数を桁位置へずらした部分積を、16-bit 境界での繰り上がり（キャリー）を明示的に伝播させて
加算する。結果は 32-bit 値、および DEV 契約どおりのリトルエンディアン 4 バイトで返す。

参照 System Card の ABI ではない。UNKNOWN 項目（入出力レジスタ、フラグ、タイミング等）は扱わない。
"""

MASK16 = 0xFFFF
MASK32 = 0xFFFFFFFF


def _partial_product_sum(multiplicand, multiplier):
    """部分積の総和を 16-bit 段で繰り上げ伝播させて計算する（シフト加算の再利用なし）。"""
    total = 0
    for bit in range(16):
        if (multiplier >> bit) & 1:
            partial = (multiplicand & MASK16) << bit  # 最大 bit 30 まで
            # 下位 16 bit と上位へ溢れる部分を分けて加算（繰り上がりを明示）
            low = (total & MASK16) + (partial & MASK16)
            carry = low >> 16
            high = (total >> 16) + (partial >> 16) + carry
            total = ((high & MASK16) << 16) | (low & MASK16)
            # 32-bit を超える成分は存在しない（最大積 0xFFFE0001 < 2**32）
            total &= MASK32
    return total


def model_product_word(multiplicand, multiplier):
    """16-bit 符号なし被乗数・乗数を 32-bit 積として返す。入力は 0..0xFFFF のみ有効。"""
    if not (0 <= multiplicand <= MASK16 and 0 <= multiplier <= MASK16):
        raise ValueError("MA_MUL16U operands must be 16-bit unsigned")
    return _partial_product_sum(multiplicand, multiplier)


def model_product_bytes(multiplicand, multiplier):
    """積を DEV 契約のバイト順（bits 0-7 が先頭、リトルエンディアン）で 4 要素のタプルにする。"""
    word = model_product_word(multiplicand, multiplier)
    return tuple((word >> (8 * i)) & 0xFF for i in range(4))
