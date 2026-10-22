# EX_GETVER（$1E）観測契約・検証器（Phase ABI-2, Issue #474）

## 位置づけ

- 観測レコードの契約と検証器を定義する。**純正 BIOS は一切実行しない。**
- 合成 ROM（自作 DEV ROM 上の実コア観測）の証拠だけを受理する。`REFERENCE_ROM` の証拠は拒否する（承認経路が未定義のため。#473 / #459 / #367）。
- `tools/reference_jsr_gate.py` は 81/81 BLOCKED のまま。`release_eligible=false` を維持する。
- 検証器は「観測が成功した」ことを ABI VERIFIED に変えない。`abi_claim` は `NOT_RUN` または `OBSERVED_SYNTHETIC` のみ。

## 指標の区別

| 指標 | この PR での扱い |
|---|---|
| 静的調査済み | 変更なし（ABI-1 の 7 API） |
| 過去のローカル限定観測 | 変更なし（再現不可能、証拠として数えない） |
| 再現可能な実コア観測（純正） | 0 のまま。純正 ROM を実行しない |
| 合成 DEV ROM の観測 | 本 PR で検証器を通した件数を報告する（純正の件数には含めない） |
| ABI VERIFIED | 0 のまま。検証器は VERIFIED を受理しない |
| 実行許可 | 常に BLOCKED。静的判定も観測も許可を生まない |

## 観測レコード（v1）

`oss-pce-cd-bios/getver-observation/v1`。JSONL で 1 行 1 レコード。キーは固定集合で、重複キーは拒否する。

- `slot`（$1E）、`entry`（入口アドレス、DEV の `dev.lbl` 由来）
- `source`（`kind` = `SYNTHETIC_DEV_ROM`、`rom_sha256`）
- `core`（Geargrafx の固定 revision）、`harness`（ハーネス名と、ハーネスファイルの sha256）
- `case`、`temperature`（`cold` / `warm`）、`status`（`RETURNED` のみ受理）
- `inputs`、`before` / `after`（A/X/Y/P/SP/MPR0〜7/ZP32 バイト）
- `io`（`observation` と `accesses`）、`steps_used`、`step_budget`
- `abi_claim`、`record_digest`（本体の sha256。改ざん検知）

## 検証の順序（fail-closed）

1. キー集合・スキーマ・`record_digest`（改ざん）
2. スロットと入口
3. 証拠の出所（参照 ROM は `REFERENCE_UNAPPROVED`、未知の種類は拒否）
4. ROM・コア・ハーネスの識別
5. 状態（`RETURNED` 以外は拒否、予算超過は `BUDGET`）
6. MPR 不変、SP 不変
7. I/O アクセス（計測されて非空なら拒否）
8. ABI 主張（`VERIFIED` は拒否）

## 未計測の項目（正直な記録）

現行の core harness は以下を計測しない。レコードでは `NOT_MEASURED` と書き、「クリーンだった」とは扱わない。

- I/O アクセス（`io.observation = NOT_MEASURED`）
- 実行ステップ数（`steps_used = NOT_MEASURED`。`RETURNED` 状態を予算内の根拠とする）
- PC 軌跡、命令数、作業 RAM の全域差分（ZP32 のみ）

このため、合成観測のレコードが `OBSERVED_SYNTHETIC` になっても、I/O・命令数が検証済みとはならない。

## 負例（ホストテスト）

ROM 不一致、入口不一致、未承認の参照実行、不正な MPR 変更、予期しない I/O、スタック破壊、予算超過、改ざん、静的判定のみの許可、根拠のない VERIFIED、スキーマ違反、重複キー、ZP 長不正。各負例はその 1 性質だけを壊した正常レコードから作る。

## 残課題

- 参照 BIOS の観測には、オーナー承認・独立レビュー・安全機構（#473）が前提。本 PR では経路を追加しない。
- I/O と命令数の計測は、ハーネス側の計測追加（別 Issue）が必要。
