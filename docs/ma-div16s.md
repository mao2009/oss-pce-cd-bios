# MA_DIV16S DEV 契約（Issue #76、スロット `$42`）

DEV API 候補 MA_DIV16S（slot `$42`、候補エントリ `0xE0C6` = HYPOTHESIS）の**プロジェクト独自契約**。
参照 System Card の呼出規約は **UNVERIFIED**（`spec/api_contracts.json` の arguments / returns / clobbered は UNKNOWN）。
本書は DEV ROM 専用の定義であり、VERIFIED / DEV_CALLABLE / release_eligible を主張しない。
参照 BIOS の callee は呼ばず、JSR も実行していない。

実装: `src/math/ma_div16s.s`（MATHLIB）と `src/overrides/slot_42.s`（DEV ROM の OVERRIDES）。
アルゴリズムは同一の復元除算で、セグメント指定のみ異なる。

## 入出力（スタック渡し）

| 項目 | 定義 |
| --- | --- |
| 呼出側 | `X:A` = 被除数（符号付き 16-bit、X が上位、A が下位）。`PHA` 除数 lo、`PHA` 除数 hi（hi が上）、`JSR` |
| 商 | `X:A` に返す。0 方向への切り捨て（truncation）。符号は被除数 XOR 除数 |
| 余り | 呼出側が push した除数 2 バイトを上書き。先に `PLA` されるバイトが余り hi、その下が余り lo。余りの符号は被除数の符号 |
| C | 除数 0 のとき 1。それ以外は 0 |
| V | -32768 / -1 = +32768 が表現できないとき 1（このとき `X:A` = `$8000`） |
| N, Z | 返す商を反映 |
| Y, I, D, T | 保持（I/D/T は呼出前の P から復元）。B は格納されない（PLP で捨てられる） |
| 除数 0 | 商 0、余り = 被除数、C = 1 |
| スタック | JSR 戻り番地の上に 7 バイト push。ゼロページ・BSS・I/O・MPR 書込みなし |
| 前提 | MPR1 がスタックページ（RAM `$F8`、`$2100`）を写像していること。入口 S `>= $07`。T=0 で呼ぶこと（T=1 は契約外） |

## 符号とエッジケース（host 判定器 `tools/ma_div16s_model.py` が定義）

| 被除数 / 除数 | 商 | 余り | 備考 |
| --- | --- | --- | --- |
| 7 / 2 | 3 | 1 | 基本 |
| -7 / 2 | -3 | -1 | 切り捨ては 0 方向（floor の -4 ではない） |
| 7 / -2 | -3 | 1 | 余りは被除数の符号 |
| -7 / -2 | 3 | -1 | |
| 5 / 0 | 0 | 5 | C = 1 |
| -32768 / -1 | `$8000` | 0 | V = 1 |
| -32768 / -32768 | 1 | 0 | 両方 `$8000` の絶対値 |
| 32767 / -32768 | 0 | 32767 | |

host 判定器は 6 フィールド（商・余り・C・V・N・Z）を比較する。
`tests/host/test_ma_div16s.py::test_judge_rejects_plausible_mutants` は、床除算・余りの符号誤り・V 欠落・C 欠落・Z 欠落を
判定器が拒否することを確認する。RED 確認: V を常に 0 にしたモデルの写しに対して、ModelTest 全体（7 件）のうち 3 件が失敗した
（新試験 `test_judge_rejects_plausible_mutants` を含む。作業ディレクトリ外の一時コピーで実施し、コミットしていない）。
GREEN: 実モデルで 7 件すべて成功。

## 公開ソースの名称衝突（オープン問題）

`docs/abi-contracts.md` の名称表で既に記録している。`$42/$43` について、HuCC の `pcengine.inc` の equate 名は
`$42 = MA_DIV16S`、`$43 = MA_DIV16U` とし（候補エントリ `0xE0C6` / `0xE0C9` は本プロジェクトの `spec/api_contracts.json` の記録）、Hu-Go の `bios.c` の selector 定義は
`MA_DIV16U / MA_DIV16S` の順に並べる。Hu-Go の観測はエントリを証明しない（HLE selector）。
したがって**符号付き／符号なしと `$42`／`$43` の対応は未確定**であり、本 DEV 実装の割当（`$42` = 符号付き）を
参照 ABI の事実として扱わない。解決には参照 BIOS の実測が必要で、本 Issue では実施しない。

## 未確定事項（UNKNOWN）

- 参照 BIOS の引数・戻り値・破壊レジスタ（全て UNKNOWN）。
- 参照 BIOS での除数 0、-32768 / -1 の挙動。
- `$42`／`$43` の符号付き・符号なしの割当（上記の衝突）。
- 実機（PC Engine）での T=1 呼出の挙動、PHP が T を push するか。

## ブロック条件

- 参照 BIOS の実行は、参照 JSR 実行ゲートが閉じているため禁止（ゲートは全スロットで閉）。
- 参照 ABI の検証は、名称衝突と実測の両方が解決されるまで VERIFIED にできない。
- DEV ROM は `release_eligible: false` のまま。承認フィールド（`approved_by` など）は変更しない。

## 試験の範囲

- host: `tests/host/test_ma_div16s.py`（モデルと判定器。アセンブル済みバイトは対象外）。
- 実コア: `tests/integration/geargrafx_dev_div16s.py`（DEV ROM、本 Issue では再実行していない）。
- 本 PR は host 試験の追加のみ。Geargrafx の重い実行はなし。
