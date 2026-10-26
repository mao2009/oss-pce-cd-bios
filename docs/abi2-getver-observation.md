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
- `inputs`（A/X/Y/P の 4 キーのみ。各値は 0〜255 の整数で、bool・負数・256 以上・文字列・浮動小数点・null は拒否。`before` の A/X/Y/P と一致しなければ拒否）
- `before` / `after`（A/X/Y/P/SP/MPR0〜7/ZP32 バイト）
- `io`（キーは `observation` と `accesses` のみ。`accesses` はリスト）、`steps_used`、`step_budget`
- `abi_claim`、`record_digest`（本体の sha256。**整合性検査であり、真正性の証明ではない**。下記）

### `record_digest` の意味（整合性のみ）

- 偶発的な破損と、ハッシュを再計算しない改変を検出する。
- 改ざんした本文に対して SHA-256 を再計算すれば一致してしまう。よって**一致は作成者・実行・出所を証明しない**。
- 一致しても `abi_claim` は `VERIFIED` に昇格しない。純正 JSR の実行許可も生まれない（`decide_execution` は常に BLOCKED）。
- 実測の出所を独立に証明する仕組み（署名、独立レビューされた証拠経路）は本モジュールの責務外。BiosDevKit 側の将来の証拠認証とは分離し、#473 で扱う。

## 検証の順序（fail-closed）

1. キー集合・スキーマ・`record_digest`（整合性。JSON 化できない値は `FIELDS`）
2. スロットと入口
3. 証拠の出所（参照 ROM は `REFERENCE_UNAPPROVED`、未知の種類は拒否）
4. ROM・コア・ハーネスの識別
5. 状態（`RETURNED` 以外は拒否、予算超過は `BUDGET`）
6. 状態の構造、`inputs` の構造と `before` との一致
7. MPR 不変、SP 不変
8. I/O の構造と値（`NOT_MEASURED` のまま、`accesses` は空。`MEASURED_NO_ACCESS` は計測器の証明がないため拒否）
9. ABI 主張（`VERIFIED` は拒否）

不正な構造は例外ではなく `Refused` で拒否する。`Refused` 以外の例外が出ることは `MalformedStructureTests` で確認する。

## 未計測の項目（正直な記録）

現行の core harness は以下を計測しない。レコードでは `NOT_MEASURED` と書き、「クリーンだった」とは扱わない。

- I/O アクセス（`io.observation = NOT_MEASURED`）
- 実行ステップ数（`steps_used = NOT_MEASURED`。`RETURNED` 状態を予算内の根拠とする）
- PC 軌跡、命令数、作業 RAM の全域差分（ZP32 のみ）

このため、合成観測のレコードが `OBSERVED_SYNTHETIC` になっても、I/O・命令数が検証済みとはならない。

## 負例（ホストテスト）

ROM 不一致、入口不一致、未承認の参照実行、不正な MPR 変更、予期しない I/O、スタック破壊、予算超過、改ざん、静的判定のみの許可、根拠のない VERIFIED、スキーマ違反、重複キー、ZP 長不正。各負例はその 1 性質だけを壊した正常レコードから作る。

追加の負例（#475 指摘 2）:

- `inputs`：余分なキー、欠落キー、配列・null・文字列、bool・負数・256 以上・浮動小数点・null、`before` との不一致
- `io`：配列・null・文字列、キーの欠落・余分、`accesses` がリスト以外、未計測なのに `accesses` あり、未計測なのに `MEASURED_NO_ACCESS` と主張
- 整合性：本文だけ変更（拒否）、本文とハッシュを両方変更（整合性は通過するが、真正性は主張せず `OBSERVED_SYNTHETIC` のまま昇格しない）、`VERIFIED` 主張の再封印（拒否）
- 任意の構造の型違い：拒否のみで例外は出さない

## 合成 ROM の I/O 計測について

`MEASURED_NO_ACCESS` を受理するには、計測器自身の出所を証明する仕組みが必要になる。現行の core harness にはそれがないため、本検証器はこの値を全レコードで拒否する。

## 証拠検証器（verify_evidence）と確認チェックリスト

`verify_evidence(records, ...)` は合成観測レコードだけを対象とし、各レコードを `validate` に通したうえで次を検査する。

| 項目 | 検査 | 拒否コード |
|---|---|---|
| 返り値の版（DEV） | X=3、Y=0（`src/overrides/slot_1E.s`）。DEV 専用で、参照 BIOS の期待値ではない | `OUTPUT` |
| A の保存 | `after.a == before.a` | `OUTPUT` |
| P | 記録のみ。保存レジスタとして主張しない（変化しても受理） | なし |
| SP / MPR | `validate` で不変を検査 | `STACK` / `MPR` |
| RAM（ZP32） | `after.zp == before.zp`。ZP32 の外は `NOT_MEASURED` | `RAM` |
| cold / warm の対 | ケースごとに cold と warm が各 1 件、入力ベクタが同一 | `PAIR` |
| cold / warm の後状態 | A/X/Y/P/SP/MPR/ZP が完全一致 | `COLD_WARM` |
| 参照の版 | `UNKNOWN`。DEV の X/Y と比較しない（比較は `BLOCKED`） | — |

- 証拠が 0 件なら `pairs = NOT_RUN` とし、合格とは扱わない。
- 戻り値は `checklist`、`abi_status`（`reference_abi_verified` は常に 0）、`execution`（常に `BLOCKED`）。
- 呼び出し元の SOURCE_FACT は `CALLER_SOURCE_FACTS` に、取得元の所在（`huc:include/hucc/...` の行番号）付きで置く。呼び出し側の記述であり、BIOS の挙動ではない。
- `CALLER_MAJOR_IN_X`：呼び出し側は X を 3 と比較する（`core-stage1.asm` 94-99 行）。
- `CALLER_TXA_SAY`：呼び出し側は X:A を TXA/SAY で Y:A に変換する（`hucc-systemcard.asm` 72-79 行）。

### 確認チェックリスト（cold / warm・版の差）

| 確認項目 | DEV 合成 | 参照 BIOS |
|---|---|---|
| cold と warm の入力・後状態が一致 | 検証器で判定（対がなければ NOT_RUN） | 未実行（BLOCKED） |
| 返り値の版バイト | X=3、Y=0（ハードコード） | UNKNOWN（イメージ依存） |
| System Card の版の比較 | 対象外 | BLOCKED |
| A の保存、ZP32 の不変 | 検証器で判定 | UNKNOWN |

## 残課題

- 参照 BIOS の観測には、オーナー承認・独立レビュー・安全機構（#473）が前提。本 PR では経路を追加しない。
- I/O と命令数の計測は、ハーネス側の計測追加（別 Issue）が必要。
