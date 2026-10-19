# ex_bcdbin（BCD→二進数）独自仕様

Issue #71。`src/ex_bcdbin.s` は本プロジェクトが独自に定めた仕様で書いた内部ルーチンである。
純正 BIOS の EX_BCDBIN の ABI とは **照合していない**。
呼出契約は HYPOTHESIS のままで、確定したとは主張しない。ジャンプテーブル未接続で、
API の既存スタブは従来どおりである。

## 仕様（本プロジェクト独自）

| 項目 | 内容 |
| --- | --- |
| 入力 | A = packed BCD 1バイト、2桁。上位ニブル = 十の位、下位ニブル = 一の位（`$00`〜`$99`） |
| 前提 | D フラグ = 0（ADC を使うため。D=1 での動作は未定義） |
| 正常 | C = 0、A = 二進値 0〜99 |
| 不正 BCD | どちらかのニブルが 9 を超える場合は C = 1、A = 入力値のまま |
| 保存 | X、Y を保存。スタックは均衡し、スタック以外のメモリは書かない。スタック使用は PHX / PHY の 2 バイト（JSR の戻り先 2 バイトは別） |
| 未定義 | N / Z / V フラグ |

## 検証

ホスト：`python3 -m unittest tests.host.test_ex_bcdbin -v`
実コア（opt-in、Linux 限定、通常のホストテスト・既定 CI には含めない）：`python3 tests/integration/geargrafx_bcdbin.py`

- ホスト参照モデル：全 256 入力を独立な判定式と照合し、0 / 9 / 10 / 99 や `$0A` / `$9A` / `$A0` / `$FF`
  などの境界値も確認する。BIN→BCD→BIN の往復はモデル内の符号化で確認しており、EX_BINBCD（#70）が
  できたら差し替える予定である。
- 実コア：`tests/fixtures/bcdbin_harness.s` が全 256 入力で `ex_bcdbin` を呼び、A / P / X / Y と最終 SP を
  作業 RAM に書く。`geargrafx_smoke.execute` で cold / warm の両方を読み出してモデルと照合する。
  一の位チェック（`CMP #10`）・十の位チェック（`CMP #$A0`）・十の位の重み（`ADC #10`）を書き換えた ROM が
  不一致として検出されることも実コアで確かめる。Linux 以外、または pinned cc65 / コアが無いときは SKIP（PASS ではない）。

Geargrafx での観測は実機での動作を証明しない。BIOS 互換も主張しない。
