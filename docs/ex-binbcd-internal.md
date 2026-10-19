# EX_BINBCD 内部ルーチン（本プロジェクト独自仕様）

Issue #70。`src/ex_binbcd.s` の `ex_binbcd_packed` は本プロジェクトが独自に定めた契約で動く HuC6280 ルーチンである。**System Card の slot `$3C` / 候補 `$E0B4` の ABI ではない**。純正 EX_BINBCD の契約は未確認（Spec=HYPOTHESIS）のままで、本ルーチンの検証結果はその根拠にならない。ROM ジャンプテーブルには接続していない。

## 契約（独自）

| 項目 | 内容 |
|---|---|
| 入力 | A = 符号なし 2 進数。有効範囲は 0..99 |
| 出力（範囲内） | A = **packed BCD** 1 バイト（bit7-4 = 十の位、bit3-0 = 一の位）。C=0 |
| 範囲外（A = 100..255） | A は入力のまま、C=1。範囲外をエラーとして返すだけで、桁あふれ値は作らない |
| 保存 | X, Y を保存。D は立てない（D=1 のまま呼ばれた場合は対象外） |
| 未規定 | 戻り時の N/Z/V |
| 副作用 | RAM/IO アクセスなし。スタック使用 3 バイト（JSR 2 + PHX 1） |

unpacked BCD（1 桁 1 バイト。例: 10 → `01 00`）は提供しない。packed の 10 は 1 バイト `$10` である。

## 検証

- ホストモデル: `tools/ex_binbcd.py` の `binbcd_packed`。`tests/host/test_ex_binbcd.py` が 0, 9, 10, 99, 100, 255 と 0..99 全値の桁妥当性、バイト範囲外入力の拒否、プローブのアセンブルを確認する。
- 実コア（opt-in）: `python3 tests/integration/geargrafx_ex_binbcd.py --mode debug`。HuCARD プローブ `tests/fixtures/ex_binbcd_probe.s` が 0..255 全入力で A/P/X を RAM に記録し、ホストモデルと照合する。`ADC #$10` を `ADC #$20` に変えた変異 ROM が不一致になることも確認する。Geargrafx の結果は実機の挙動を証明しない。
