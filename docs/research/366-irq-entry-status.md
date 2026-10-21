# Issue #366: 割込み入口（IRQ2 / IRQ1 / TIQ）の副作用前遮断 実測ステータス

- 対象 Issue: #366（親: #173、計画: #361 V03）
- 状態: 判定器は main 済み（#380, d95ae8c）。実コアでの入口停止の実測は **BLOCKED**（未計測）。
- 本書の位置づけ: #366 の受入条件を、既存の判定器・テスト・実コア観測の成果に照らして棚卸しした記録。新たな実コア値は含まない。
- 純正 BIOS の JSR・割込みハンドラは一切実行していない。`BLOCKED_ABI` と `release_eligible=false` を維持する。

## 既存成果（main）

| 項目 | 内容 |
|---|---|
| 判定器 | `tools/irq_entry_judge.py`（標準ライブラリのみ。合成観測行を判定） |
| テスト | `tests/host/test_irq_entry_judge.py`（7 件） |
| 検証 | `python3 -m unittest discover -s tests/host -p 'test_irq_entry_judge.py'` → 7 件 OK（WSL, HEAD 1cd5582） |
| 判定器の自己検査 | `python3 tools/irq_entry_judge.py` → IRQ2 / IRQ1 / TIQ 各 PASS、各 13 変異を拒否、libretro は各 BLOCKED |

ベクタは `IRQ2 = $FFF6`、`IRQ1 = $FFF8`、`TIQ = $FFFA`。入口の push は 3 バイト（PC hi, PC lo, P）で、SP `$FF→$FC` は `docs/geargrafx-contract-evidence.md` の文書値である。

## 受入条件の棚卸し

| # | 受入条件 | 現状 | 根拠 |
|---|---|---|---|
| 1 | IRQ2 / IRQ1 / TIQ それぞれで、解決済みハンドラ先頭に合成 ROM で停止できることを実測 | **BLOCKED（未計測）** | 割込み入口用の probe モードが存在しない。判定器は合成行のみで判定している。 |
| 2 | 停止時点でハンドラ命令が 1 つも実行されていないことを確認 | 判定器のみ | `handler_instructions == 0` を判定。実コア値は未取得。 |
| 3 | スタック書込みが page 1 `[$2000,$4000)` 内に限られることを確認 | 判定器のみ | 書込み 3 件の範囲を判定。実コア値は未取得。 |
| 4 | debugger ビルドでのみ成立と明記し、libretro は BLOCKED | **充足（判定器レベル）** | libretro は常に BLOCKED。理由は GG_DISABLE_DISASSEMBLER による実行 breakpoint と PC API の欠如。 |
| 5 | 変異テストで停止判定を外すと検出される | **充足（判定器レベル）** | 13 変異の拒否、および「停止判定を `if False` に置換すると偽の PASS になる」ことを確認するテスト。実コアに対する変異は未実施。 |

## 実コアで必要な観測（未実装の probe の仕様）

pinned core（`geargrafx-b49ae82a…-debugger`、`/home/loach/oss-pce-cd-bios-tools/` 配下に存在）で、次の 1 行観測を取る必要がある。

- `mode=irq-entry`、`vector`（IRQ2 / IRQ1 / TIQ）、`vector_addr`
- `vector_lo` / `vector_hi`（デバイス側の読み戻し値）
- `execbp_hit`（ハンドラ先頭の実行 breakpoint が立ったか）
- `stop_pc`（停止時の PC）、`handler_instructions`（停止までに実行した命令数）
- `handler_writes`（停止前にハンドラ本体が書いた番地）、`stack_writes`（入口で書いた番地）

このうち `execbp_hit`、`stop_pc`、`handler_instructions` は、命令境界ごとに PC を読んで停止させる方式で取得する。既存の `tests/integration/geargrafx_st_boundary.cpp`（#383、main 済み）が同様の 1 命令単位の駆動を持つため、テンプレートとして使える。ただし ST 用の駆動は IRQ の注入や vector の解決を行わない。

## ブロック項目と理由

1. **実コアでの入口停止の実測（受入条件 1〜3）**
   - 理由: 割込み入口用の probe モードが未実装。合成 ROM の vector 配置と IRQ 注入の経路が無い。
   - 待っている interface: #365 / #367 と共有する駆動ループ。#365 の駆動（#383）は main 済みだが、IRQ 注入と vector 解決の経路は含まない。#367 の現状は本作業では確認していない。
   - 同じ駆動ループを IRQ 版に拡張する変更は、共有ファイルへの並行変更に当たるため、依存が固定されるまで行わない。
2. **libretro ビルドでの扱い（受入条件 4）**
   - 理由: 実行 breakpoint と PC API が無いため、入口停止を観測できない。判定器は BLOCKED を返す。
   - 扱い: 観測手段が無いことを BLOCKED として記録する。PASS にはしない。

## 既存文書との関係

- `docs/geargrafx-contract-evidence.md` の IRQ 記述（SP `$FF→$FC→$FF`、積まれる P/PC、IRR ビット）は、pinned core の既存 contracts ハーネスによる観測と文書値である。本 Issue の「ハンドラ命令 0 個で停止」の観測とは別物であり、代用しない。
- 参照 BIOS の割込み契約（戻り・フラグ・レジスタ）は未確認であり、`BLOCKED_ABI` のままとする。

## 次の作業（依存解消後）

1. #367 / #365 の駆動ループの interface を確定させる。
2. IRQ 版の probe（`tests/integration/geargrafx_irq_entry.cpp` 相当）を新規ファイルとして追加し、合成 ROM で 3 ベクタを実測する。
3. 実測行を判定器に通し、変異（停止判定の除去、ハンドラ命令の混入）を実コアで確認する。
4. 結果を本書の表に反映する。実測前の値は UNKNOWN のままとする。

## 実コア実測（自由実行の入口拒否、判定 PASS）

- 検証専用パッチ `tests/integration/patches/geargrafx-b49ae82-irq-entry-guard.patch`（`HandleIRQ` のベクタ選択後、スタック push と PC 変更の前に null 既定のフックを呼ぶ）。ドライバ `tests/integration/geargrafx_irq_entry_guard.py`、プローブ `tests/integration/geargrafx_irq_entry_guard.cpp`。
- 合成 BIOS / 合成 CD のみ。RunToVBlank による自由実行（デバッガのステップなし）。割込みは IRR に注入し、ゲストは `CLI; BRA *`。

| 変種 | 判定 | 観測の要点 |
|---|---|---|
| `armed`（全拒否） | PASS | フック呼出し時と終了後のスタックが 0、PC はゲストループ `$3E01` のまま、ハンドラのマーカ未書込み |
| `unguarded`（null フック、正例） | PASS | スタックに `$3E,$01,P`（I=0, B=0）、PC はハンドラ末尾 `$3F85`、マーカ `$77` |
| `masked`（IDR で遮断） | PASS | フック未呼出し、スタック不変 |
| `region-allow`（ページ1内ハンドラ、正例） | PASS | 許可されて入口成立、ハンドラ実行 |
| `outregion`（ページ1外ハンドラ、負例） | PASS | フック拒否、スタック不変、ハンドラ未実行 |

- 対象ベクタ: IRQ2（$FFF6）、IRQ1（$FFF8）、TIQ（$FFFA）の 3 つすべて。
- libretro: BLOCKED（libretro の共有オブジェクトを駆動するハーネスがないため未観測。PASS ではない）。

### 残存（本結果が証明しないこと）
- 拒否点で観測している。ハンドラ先頭の命令数 0 は、マーカと A が不変であることによる間接判定であり、実行 breakpoint による停止ではない。
- 割込みは IRR への直接注入。実機の割込み源（タイマ、VDC、CD）の発生経路は検証していない。
- ST0/ST1/ST2 とブロック転送は対象外（#365 / #367）。#173 は解除しない。
- `tools/irq_entry_judge.py`（#380）は停止点観測の行形式を前提とするため、本ドライバの行には適用していない。
