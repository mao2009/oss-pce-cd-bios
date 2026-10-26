# Issue #78: MA_SQRT（$44）参照契約の準備メモ

- Issue: [#78](https://github.com/mao2009/oss-pce-cd-bios/issues/78)
- 基準 SHA: `ec496e9`（PR #519 DEV 実装マージ後の main）
- 状態: 準備のみ。参照 ABI は未検証（UNKNOWN）。参照 VERIFIED は 0/81 のまま。

## 1. 位置づけ（DEV 実装の事実と参照契約の事実を分ける）

| 区分 | 内容 | 根拠 |
|---|---|---|
| DEV 実装（確認済み） | `src/overrides/slot_44.s` は 16bit 符号なし入力に対し `floor(sqrt(n))` を返す独自ルーチン。入力 0..65535 の全数について、`slot_44.s` と同じ手順を書いた Python mirror を `math.isqrt` と照合済み（`slot_44.s` 自体の全数実行ではない） | `tests/host/test_ma_sqrt_dev.py`、`tests/integration/geargrafx_dev_sqrt.py`（PR #519） |
| 参照契約（未確認） | System Card の MA_SQRT の引数形式、丸め、戻り値配置、副作用は本リポジトリで確定していない | `spec/api_contracts.json` の slot 68 は `HYPOTHESIS` / 全 contract 項目 `UNKNOWN` / `NOT_RUN` |

ホスト model が `isqrt` と一致しても証明するのは DEV 実装の正しさだけである。参照 ABI の仕様を証明するものではない。

## 2. 準備範囲と各項目の現状

| 項目 | DEV 実装の挙動（事実） | 参照契約（UNKNOWN） |
|---|---|---|
| 入力形式 | X:A の 16bit 符号なし整数（X が上位、A が下位） | 整数か固定小数点か未確認。固定小数点なら小数点位置も未確認 |
| 丸め | 切り捨て（`floor`）。`isqrt` と一致 | 参照側の丸め（切り捨て・四捨五入・最近接）は未確認 |
| 0 | 結果 0（テスト済み） | 参照側の 0 入力の扱いは未確認 |
| 1 | 結果 1（テスト済み） | 参照側の 1 入力の扱いは未確認 |
| 完全平方数 | 4, 16, 65025 などで正確な平方根を返す（明示テスト済み。全数検査は Python mirror 経由） | 参照側の完全平方数の扱いは未確認 |
| 最大入力 | 65535 で 255 を返す（テスト済み）。trial は 9bit 以内 | 参照側で受け付ける最大値、範囲外入力の挙動は未確認 |
| 出力配置 | A = 結果（0..255）、X = 0 | 参照側の結果配置（A のみ、X:A、メモリ格納の有無）は未確認 |
| 副作用 | Y と P を保存して戻す。A と X は結果で上書き。ZP `$F7-$FF` のみ使用。I、MPR、I/O は触らない。スタックは呼び出し側 SP から 4 バイト以内 | 参照側の clobber、preserved、stack、MPR、RAM、IRQ への影響は未確認 |

## 3. UNKNOWN（参照側で未確定の事項）

1. 入力が整数か固定小数点か（小数点位置を含む）。
2. 丸め規則。
3. 0 と 1 を含む特殊入力の戻り値。
4. 受け付ける入力範囲と、範囲外入力の挙動。
5. 出力の配置（A、X、メモリのどれか）。
6. clobber、preserved、stack、MPR、RAM、フラグ、IRQ の副作用。
7. エントリ `$E0CC` と slot 68 の対応（`HYPOTHESIS`。HUC の include 由来。HLE selector は実行可能な入口の証拠ではない）。

## 4. BLOCKED 条件

次のいずれかが満たされるまで、参照契約を確定させず、`api_contracts.json` の status も変更しない。

- 参照側の一次資料（System Card 関数表の該当節、または HUC の include 行）が、入力形式、丸め、出力配置を明示していない。
- 参照 BIOS の callee を実行する権限と手順が承認されていない。本 Issue ではこの実行を行わない。
- 参照側の結果と DEV 実装の結果が一致するかを確認する根拠がない。

## 5. テスト（RED から GREEN）

- RED: `tests/host/test_ma_sqrt_reference_prep.py` を先に追加し、本ドキュメント不在で `test_doc_scope_rows_exact_and_ordered` と `test_doc_reports_exhaustive_check_as_python_mirror` の失敗を確認した（後者は本文の Python mirror 表記の修正前に失敗）。
- GREEN: 本ドキュメント追加後、同テストが通ること。加えて次を確認する。
  - MA_SQRT の contract 項目が全て `UNKNOWN`、`verification.status` が `NOT_RUN`、`executed_callee` が `false` のまま。
  - DEV の境界値（0, 1, 完全平方数, 65535）が `isqrt` と一致。
  - 出力配置（A = 結果、X = 0、Y と P の復元）が `slot_44.s` の記述どおりであることを、ソース上の命令順序で確認（実行による確認ではない）。

## 6. 次の作業

1. 参照側の一次資料で、入力形式、丸め、出力配置を確認する（本 Issue の範囲外の調査として別途扱う）。
2. 確認できた項目だけを契約へ反映し、レビューと承認を経る。承認フィールドは本 PR では変更しない。

## 7. マージ推奨

ドラフト PR として提出する。準備は DEV 側の事実整理とガードテストに限られ、参照契約の確定を含まない。マージの判断は所有者が行う。
