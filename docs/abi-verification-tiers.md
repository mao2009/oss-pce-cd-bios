# ABI 検証ティアの設計（Issue #324）

**状態: 設計文書のみ。** スキーマ、ゲート、台帳、承認欄は変更しない。`spec/api_contracts.json`、
`spec/dev_implementations.json`、`spec/abi1_reference_review.json` の値も変更しない。
参照 BIOS の ABI は VERIFIED 0/81、DEV_CALLABLE のオーナー承認は 0、`release_eligible` は `false`
のまま。本書は互換性を主張しない。

- Issue: #324（オーナー判断待ち）
- 基準: origin/main `ec496e9`（HEAD は PR 本文を参照）
- 先行文書: `docs/abi-evidence-classification.md`（PR #323 の設計提案）。本書は同文書の 3 軸を
  4 軸に拡張し、フィールドごとの状態語の意味を定める。同文書と食い違う箇所は本書を優先する（§7）。
- 負例テスト: `tests/host/test_abi_verification_tiers.py`

## 1. 現状（main 時点の事実）

| 台帳 | 現在の値 | 意味 |
| --- | --- | --- |
| `spec/api_contracts.json`（参照 BIOS の契約） | 81 slot すべて `verification: NOT_RUN` | 参照 BIOS の実測はなし |
| `spec/abi1_reference_review.json`（静的レビュー） | 7 件、`abi_state: NOT_RUN`、`static: OBSERVED_STATIC` | 静的解析の記録。検証ではない |
| `spec/dev_implementations.json`（DEV 実装） | 81 行すべて `NOT_READY`、`approval` は null、DEV ROM の override は 10 slot | DEV ROM に実装がある slot と、その承認状態 |

`tools/contracts.py` の `CLAIM_STATUSES` は `UNKNOWN` / `HYPOTHESIS` / `OBSERVED` / `BLOCKED` だけで、
`VERIFIED` を表せない。`OBSERVED` は実コア由来の証明（#124、producer 認証）がないため
`adjudicate_claim()` が常に拒否する。

## 2. 4 軸の定義と分離

同じ語（`OBSERVED`、`PASS`、`HYPOTHESIS` など）が複数の意味で使われてきたため、
4 つの問いを別の軸に分ける。どの軸の値も、他の軸の値から計算・補完しない。ツールが 1 軸を読んで
別の軸を書き換えることは禁止する。

### 2.1 SOURCE_EVIDENCE（公開ソースの記述）

- 問い: 固定版の公開ソース（caller 側 SDK、HLE エミュレータ、実コアのソース）が、その項目について何を書いているか。
- 値: `UNKNOWN`（根拠なし）、`HYPOTHESIS`（ソースに記述がある。`confidence: SOURCE_SUPPORTED`）、
  `SOURCE_FACT`（caller 側の一次ソース本文の事実に限る。文書上のラベル）。
- 上限: ソース記述だけで `OBSERVED` や `VERIFIED` にはならない。
- 既存の対応: `spec/api_contracts.json` の `sources` と `confidence`、`tools/contracts.py` の `SOURCE_KIND_MEANINGS`。
- 判断者: 調査者。誤りはレビューで直す。

### 2.2 PROVENANCE_APPROVAL（再利用・配布の承認）

- 問い: そのソースやコードを、コピー、改変、ROM への組み込み、配布に使ってよいか。
- 値: `PENDING` / `APPROVED` / `RESTRICTED`。利用形態は `DIRECT` / `ADAPTED` / `REFERENCE_ONLY` / `REJECTED`（`docs/provenance.md`）。
- 現状: `provenance/approvers.txt` の bootstrap は `BLOCKED`。承認者が存在しない。
- 判断者: 人間の承認者だけ。CI、スキャナ、エージェントは承認できない。
- 注意: `APPROVED` は法的な再利用許可であり、挙動が正しいことの根拠にはならない。

### 2.3 CALLEE_VERIFICATION（参照 BIOS callee の実測）

- 問い: 参照 BIOS の callee を実コアで実行し、入出力、フラグ、MPR、スタック、RAM 差分を測ったか。
- 値: §3 の状態語。
- 実行記録（ROM ハッシュ、コア revision、入力、結果）が必要。現状は全 slot `NOT_RUN`。
- 注意: 実測の事実と、その事実を公開してよいかは別問題（オーナー判断 §6 の項目 5）。

### 2.4 DEV_IMPLEMENTATION（OSS の DEV ROM 実装）

- 問い: DEV ROM のこの slot に OSS 実装があり、構造検査と DEV テストが通ったか。
- 値: `NOT_READY`（既定、承認なし）、`DEV_CALLABLE`（DEV ROM に限る。承認あり）、`BLOCKED`（理由付きで拒否）。
- 書いてよい場所は `spec/dev_implementations.json` だけ。`spec/api_contracts.json` と
  `docs/research/*` には書かない。
- 意味: DEV ROM についての記述に限る。参照 BIOS の ABI や互換性は一切含意しない（オーナー承認 2026-10-21 の定義）。
- DEV テストの PASS は、OSS 実装の挙動の記録であって参照 BIOS の検証ではない。

### 2.5 軸の分離のまとめ

| 軸 | 到達できる最上位 | 到達できないもの |
| --- | --- | --- |
| SOURCE_EVIDENCE | `HYPOTHESIS` / `SOURCE_FACT` | `OBSERVED`、`VERIFIED`、provenance 承認、DEV_CALLABLE |
| PROVENANCE_APPROVAL | `APPROVED`（人間の承認者が記録） | 挙動の正しさ、callee 実測、互換性 |
| CALLEE_VERIFICATION | `OBSERVED`（認証前は拒否。§3） | `VERIFIED`（現スキーマでは表せない。#171） |
| DEV_IMPLEMENTATION | `DEV_CALLABLE`（DEV ROM に限る） | 参照 BIOS の `VERIFIED`、release 判定、互換性 |

どの軸の値が揃っても、`release_eligible: true` にはならない。release gate は別の人間承認を要し、
現在は常に `BLOCKED`。

## 3. フィールドごとの状態の意味

検証軸の状態は、契約の 1 フィールド（例: `returns`、`preserved`）ごとに判定する。
現行の語彙は 2 系統ある。台帳 claim 用（`tools/contracts.py` の `CLAIM_STATUSES`）と、静的レビュー用
（`tools/abi1_reference_review.py` の `ABI_STATES` と `ITEM_STATUSES`）。本書の 4 語は両者を対応づける読み替えである。

| 状態 | 意味 | 必要条件 | 現在到達できるか | 現在の件数 |
| --- | --- | --- | --- | --- |
| `UNKNOWN` | 根拠がない、または判定できない。既定値。 | なし。`value` は `UNKNOWN`、`confidence` は `UNKNOWN`。 | はい | 全フィールド |
| `OBSERVED` | 記録された実コアの実行で、そのフィールドの挙動が観測された。 | 再現可能な実コア実行、実行の記録（ROM ハッシュ、コア revision、入力、結果）、producer 認証（#124）、`observation_kind` がフィールドを覆う。 | いいえ。認証前は `adjudicate_claim()` が拒否（`#124`）。静的レビューの `OBSERVED_STATIC` は別物 | 0 |
| `PARTIAL` | 観測したのは入力領域の一部（ケース、フラグ、条件）。 | `OBSERVED` の条件に加え、覆った範囲と覆っていない範囲を明記すること。 | いいえ（abi1 の語彙にあるが、再現可能な観測がない） | 0 |
| `VERIFIED-equivalent` | 参照 BIOS の全入力領域が実コアで観測され、認証され、独立レビューされ、オーナーが承認した状態。 | `OBSERVED` の条件に加え、全入力領域、独立レビュー、オーナー承認、#171 の trusted VERIFIED 経路。 | いいえ。`VERIFIED` は `CLAIM_STATUSES` と `adjudicate_claim()` で拒否される。abi1 は静的経路からの `VERIFIED` を拒否する | 0 |

補足:

- `HYPOTHESIS`、`SOURCE_FACT`、`OBSERVED_STATIC` は SOURCE_EVIDENCE または静的レビューの値である。
  CALLEE_VERIFICATION のどの状態にも昇格させない。
- 「`OBSERVED` 相当」を `OBSERVED_STATIC`（静的レビュー）や `OBSERVED LOCAL_ONLY`（ローカル観測）に当てはめない。
  名前が似ているが、CALLEE_VERIFICATION の `OBSERVED` ではない。
- 状態の間に順序はない。どの状態からも他の状態を計算しない。
- `VERIFIED-equivalent` は設計上の定義であり、この語を台帳の値として書くことは禁止する（`VERIFIED` を書けないのと同じ）。

## 4. 負例テスト

`tests/host/test_abi_verification_tiers.py` は、次の 4 種類の入力が VERIFIED や承認に昇格しないことを確かめる。
テストの fixture は同ファイル内の合成データで、実コアも参照 BIOS も使わない。

| 種類 | 試すこと | 代表テスト |
| --- | --- | --- |
| 偽造証拠 | 形式が正しく、ハッシュが一致し、revision が pin と一致する証拠。`REALCORE` の文字列、`executed_callee`、呼び出し側が渡すハッシュ集合 | `ForgedEvidenceTests`（`#124` の拒否で止まること）、`VERIFIED` 状態の拒否 |
| 自己申告の承認 | claim 内の `approval` キー、承認欄が空または片方だけの `DEV_CALLABLE`、承認者が自分で書いた値 | `SelfDeclaredApprovalTests`、`test_self_declared_approver_is_refused`（expectedFailure，§5） |
| DEV の結果 | `DEV_CALLABLE` の行に `VERIFIED` の ABI 状態を付けた場合、承認された DEV 行を静的レビューに数えた場合 | `DevResultTests` |
| 静的解析の結果 | 呼び出し側ソースだけの `OBSERVED`、静的レビューからの `VERIFIED`、`OBSERVED` / `PARTIAL` の静的レビュー、`EXECUTION_SAFE` を許可として扱う | `StaticAnalysisTests` |

設計文書の語彙テスト（`DesignDocumentTests`）は、本書が使う状態語と 4 軸の名前がコードと一致することを確かめる。

## 5. 既知の穴（未解決）

1. **DEV 承認が自己申告を拒否しない。** `tools/dev_implementations.py` の `_owner_approved()` は、
   `approved_by` と `review_ref` が空でない文字列であれば通す。エージェントが自分の名前で
   `DEV_CALLABLE` を書けば、検証は通る。`test_self_declared_approver_is_refused` は expectedFailure で、
   この穴を明示する。承認者の許可リストを作るのは §6 の判断事項（新規）。
2. **`summary()` の `owner_approved` の名称。** この数は承認欄に文字列があるかどうかを数えるだけで、オーナー承認を数えていない。
   名称は誤解を招く。改名は別 PR。
3. **`real_core_evidence` はパスの存在だけを確認する。** テストファイルの存在は、実コアで実行した証拠ではない。
4. **`compatibility_basis` は未実装。** `docs/abi-evidence-classification.md` §4.2 の提案のみ。
5. **producer 認証がない（#124、#171）。** `OBSERVED` も `VERIFIED-equivalent` も、この穴が塞がるまで到達できない。
6. **全入力の DEV 実コア実行は台帳の外にある。** slot `$3D` の 256 入力 × 18 条件は WSL のローカル実行のみで、CI で再現されていない（Issue #324 のコメント）。
   `docs/evidence/dev-callable-*.md` の記録は DEV 承認の判断材料であり、台帳の値ではない。

## 6. オーナーが #324 で判断すること

2026-10-21 の決定で項目 1（台帳導入）と項目 2（DEV_CALLABLE の定義）は承認済み。以下は未決。

1. **項目 3: `ORIGINAL` の OSS 実装の `provenance_approval` を誰が承認するか。** 承認者 bootstrap は `BLOCKED`。
2. **項目 4: 台帳に 3 軸のキー（`source_evidence` / `provenance_approval` / `callee_verification`）を追加するか。**
   スキーマ変更なので、本書は提案しない。
3. **項目 5: `CALLEE_VERIFICATION` で実測した事実のうち、公開してよい範囲。**
4. **新規: DEV_CALLABLE の承認者の許可リストを作るか。** 作る場合は、承認欄の書き込み権限と、validator が
   許可リストを検査するかを決める（§5 の 1）。
5. **新規: 状態語（`PARTIAL`、`VERIFIED-equivalent`）の定義を採用するか。** 定義だけなら、スキーマ変更は要らない。
6. **新規: 本書で `docs/abi-evidence-classification.md` §2 の定義を置き換えるか。**

## 7. 変えないこと

- `spec/api_contracts.json` のスキーマと、すべての status 値（`NOT_RUN`、`UNKNOWN` など）。
- `VERIFIED` への昇格。どの台帳でも `VERIFIED` は 0 件。
- `release_eligible`、`VERIFIED_PATH_ENABLED`、参照 JSR ゲート、承認フィールド。
- 参照 BIOS の実行。本作業では実行していない。
- オーナー承認の記録。本書と負例テストは承認欄に一切書き込まない。

オーナー承認 2026-10-24（`measurement_point_id` の一時入力拡張）は本書の対象外であり、本書はそれを変更しない。
