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
  `SOURCE_FACT` は静的レビュー（`tools/abi1_reference_review.py` の `ITEM_STATUSES`）の語で、台帳 claim の値ではない。caller 側の一次ソース本文の事実に限る文書上のラベル。
- 上限: ソース記述だけで `OBSERVED` や `VERIFIED` にはならない。
- 既存の対応: `spec/api_contracts.json` の `sources` と `confidence`、`tools/contracts.py` の `SOURCE_KIND_MEANINGS`。
- 判断者: 調査者。誤りはレビューで直す。

### 2.2 PROVENANCE_APPROVAL（再利用・配布の承認）

- 問い: そのソースやコードを、コピー、改変、ROM への組み込み、配布に使ってよいか。
- 値: `PENDING` / `APPROVED` / `RESTRICTED`。利用形態は `DIRECT` / `ADAPTED` / `REFERENCE_ONLY` / `REJECTED`（`docs/provenance.md`）。
- 現状: `provenance/` ディレクトリがリポジトリになく、承認者の一覧（`provenance/approvers.txt`）も存在しない。一覧がないため fail closed。
- 判断者: 人間の承認者だけ。CI、スキャナ、エージェントは承認できない。
- 注意: `APPROVED` は法的な再利用許可であり、挙動が正しいことの根拠にはならない。

### 2.3 CALLEE_VERIFICATION（参照 BIOS callee の実測）

- 問い: 参照 BIOS の callee を実コアで実行し、入出力、フラグ、MPR、スタック、RAM 差分を測ったか。
- 値: §3 の状態語。
- 実行記録（`evidence_hash`、コア revision、入力、検証結果）が必要。現状は全 slot `NOT_RUN`。
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
| SOURCE_EVIDENCE | `HYPOTHESIS`（`SOURCE_FACT` は静的レビューの語） | `OBSERVED`、`VERIFIED`、provenance 承認、DEV_CALLABLE |
| PROVENANCE_APPROVAL | `APPROVED`（人間の承認者が記録） | 挙動の正しさ、callee 実測、互換性 |
| CALLEE_VERIFICATION | 到達できる状態はない（`OBSERVED` は認証前に拒否。§3） | `VERIFIED`（現スキーマでは表せない。#171） |
| DEV_IMPLEMENTATION | `DEV_CALLABLE`（DEV ROM に限る） | 参照 BIOS の `VERIFIED`、release 判定、互換性 |

どの軸の値が揃っても、`release_eligible: true` にはならない。release gate は別の人間承認を要し、
`tools/build_rom.py` の `release_gate()` は現在常に BLOCKED を返す。

## 3. フィールドごとの状態の意味

検証軸の状態は、契約の 1 フィールド（例: `returns`、`preserved`）ごとに判定する。
現行の語彙は 2 系統ある。台帳 claim 用（`tools/contracts.py` の `CLAIM_STATUSES`）と、静的レビュー用
（`tools/abi1_reference_review.py` の `ABI_STATES` と `ITEM_STATUSES`）。本書の 4 語は両者を対応づける読み替えである。

| 状態 | 意味 | 必要条件 | 現在到達できるか | 現在の件数 |
| --- | --- | --- | --- | --- |
| `UNKNOWN` | 根拠がない、または判定できない。既定値。 | なし。`value` は `UNKNOWN`、`confidence` は `UNKNOWN`。 | はい | 全フィールド |
| `OBSERVED` | 記録された実コアの実行で、そのフィールドの挙動が観測された。 | 再現可能な実コア実行、実行の記録（`evidence_hash`、コア revision、入力、検証結果）、EMULATOR_SOURCE の参照、`REALCORE` の表示、callee 挙動のフィールドでは `executed_callee=True`、`observation_kind` がフィールドを覆う、各証拠の `measurement_point_id` が `spec/measurement_points.json` に登録され、その kind と field が一致する（#364、#515）、producer 認証（#124）。 | いいえ。認証前は `adjudicate_claim()` が拒否（`#124`）。静的レビューの `OBSERVED_STATIC` は別物 | 0 |
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

1. **項目 3: `ORIGINAL` の OSS 実装の `provenance_approval` を誰が承認するか。** 承認者の一覧がリポジトリにない（fail closed）。
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

## 8. 未決 6 項目の判断表（推奨と影響）

本節は §6 の 6 項目について、現状、選択肢、推奨とその理由、互換性、セキュリティ、必要な人間の承認、判断前に進めてよい作業を示す。
推奨は提案であり、決定ではない。どの項目も、オーナーが判断するまでスキーマ、`VERIFIED` の扱い、信頼されたゲートの状態を変えない。

### 8.1 項目 3: `provenance_approval` の承認者

- **現状:** `provenance/` ディレクトリも承認者の一覧（`provenance/approvers.txt`）もリポジトリにない。承認者がいないので、`ORIGINAL` の承認は付かない（fail closed）。`tools/provenance_guard.py` は既に契約を定めている。承認者一覧の編集には、PR の base にある一覧に載った maintainer の承認が要り、PR 作成者は承認できず、一覧が空または無い base は閉じる。ブートストラップ時は、PR が追加する一覧を作成者以外の admin に限って使う。
- **選択肢:**
  - A. オーナーが名指しした承認者の一覧をリポジトリに置く（オーナーが作る人間のコミット）。
  - B. 複数のメンテナのグループに委ねる。
  - C. 承認者を置かず、`ORIGINAL` の DEV_CALLABLE を作らない。
- **推奨: A。** 承認者は信頼の境界であり、エージェントや CI が書けてはならない。名指しの一覧なら、誰が承認できるかを監査できる。
- **互換性:** 既存の台帳の値は変わらない。一覧がない現状は fail closed のまま。
- **セキュリティ:** 一覧そのものが信頼の基点になる。エージェントが一覧を編集できれば、自己承認が成立する。一覧の編集権限をオーナーに限る（CODEOWNERS 等）必要がある。
- **必要な人間の承認:** オーナーが承認者を決め、その一覧を人間の手でコミットする。
- **判断前に進めてよいこと:** 一覧がないことを fail closed として検査するテスト。承認者なしでの DEV 実装の作業（承認は付けない）。

### 8.2 項目 4: 台帳への 3 軸キーの追加

- **現状:** 3 軸は本書の §2 で対応づけている。`spec/api_contracts.json` にはキーがない。`tools/contracts.py` の `CLAIM_KEYS` は固定集合で、未知のキーは拒否される。
- **選択肢:**
  - A. `spec/api_contracts.json` に 3 軸のキーを追加する（スキーマ変更）。
  - B. 現状どおり、本書の対応表だけで読み替える。
  - C. 3 軸の情報を別ファイルに置く。
- **推奨: B を当面続ける。** キーを追加すると、`CLAIM_KEYS` の変更とスキーマ変更の承認が要る。承認者（§8.1）と公開範囲（§8.3）が決まる前に入れると、軸の値を別の軸から書き込む経路を作りかねない。
- **互換性:** A は `CLAIM_KEYS` と全 81 行の検査を変える。B は影響なし。
- **セキュリティ:** キーは宣言であり、計測ではない。キーが増えると、ツールが一軸を読んで別の軸を書く誘惑が生まれる。
- **必要な人間の承認:** A の場合、オーナーによるスキーマ変更の承認。B は承認不要。
- **判断前に進めてよいこと:** 対応表の維持と、キー欠落を前提にした負例テスト。

### 8.3 項目 5: `CALLEE_VERIFICATION` の公開範囲

- **現状:** 参照 BIOS の実測は公開していない。`docs/provenance.md` は、逆アセンブル一覧と抽出・翻訳されたファームウェアコードのコミットと配布を禁じている。全 81 slot は `NOT_RUN`。
- **選択肢:**
  - A. caller から見える ABI（引数、戻り値、破壊レジスタ）だけを公開する。
  - B. 内部挙動も含めて公開する。
  - C. 承認まで何も公開しない。
- **推奨: C を当面とし、承認後に A へ広げる。** 内部挙動は provenance の判断に依存する。公開範囲を先に決めると、承認前に内部の詳細が漏れる。
- **互換性:** C は影響なし。A は承認後の追記で、既存の値は変えない。
- **セキュリティ:** 内部の詳細の公開は、参照 BIOS の再配布や provenance 上の問題を招きうる。
- **必要な人間の承認:** オーナーが公開範囲を決める。
- **判断前に進めてよいこと:** ローカル観測を `LOCAL_ONLY` のまま保管すること。公開物には何も追加しない。

### 8.4 新規: DEV_CALLABLE の承認者の許可リスト

- **現状:** `tools/dev_implementations.py` の `_owner_approved()` は空でない文字列を通す。`test_self_declared_approver_is_refused` は expectedFailure で、この穴を示している。
- **選択肢:**
  - A. オーナーが決めた承認者名の一覧をリポジトリに置き、validator がそれを検査する。
  - B. 人間だけが署名する承認ファイルを別途置き、承認欄の値と照合する。
  - C. 現状のまま（文字列のみ）。
- **推奨: A。** 穴を直接塞ぐ最小の変更である。一覧の編集権限は §8.1 と同じくオーナーに限る。
- **互換性:** 既存の台帳に `DEV_CALLABLE` の行はない。テストの正の例（`approved_by: owner`）は一覧に含める必要がある。
- **セキュリティ:** 一覧が書き換えられれば無効になる。一覧の保護が前提である。
- **必要な人間の承認:** オーナーが一覧の内容と編集権限を決め、その変更を人間が行う。
- **判断前に進めてよいこと:** expectedFailure を残すこと。エージェントが `DEV_CALLABLE` を書かないこと。

### 8.5 新規: 状態語 `PARTIAL` と `VERIFIED-equivalent` の採用

- **現状:** 両方とも本書の定義だけである。`PARTIAL` は `tools/abi1_reference_review.py` の `ABI_STATES` にあるが、再現可能な観測がないため到達しない。`VERIFIED-equivalent` はどこにもない。
- **選択肢:**
  - A. 本書の用語としてだけ採用し、コードにもスキーマにも入れない。
  - B. 台帳の値として追加する（スキーマ変更）。
  - C. 採用せず、`OBSERVED` の注記で代用する。
- **推奨: A。** 共通の語彙があれば読み手が混同しない。台帳に入れると、`VERIFIED-equivalent` が保存された値になり、昇格の経路を作る。
- **互換性:** A は影響なし。B は `CLAIM_STATUSES` の変更とスキーマ変更を伴う。
- **セキュリティ:** `VERIFIED-equivalent` を保存された値にすると、それ自体が検証済みのように読まれる。保存しないことが安全上の要点である。
- **必要な人間の承認:** A はオーナーが定義を受け入れる。B はスキーマ変更の承認。
- **判断前に進めてよいこと:** `PARTIAL` と `VERIFIED-equivalent` の claim 状態を `adjudicate_claim()` が拒否する負例テスト（本 PR で追加済み）を維持すること。

### 8.6 新規: `docs/abi-evidence-classification.md` §2 の置き換え

- **現状:** PR #323 の文書が main にあり、§2 に 3 軸の定義がある。本書の定義と一部が重なるが、同一ではない。
- **選択肢:**
  - A. #323 の §2 を本書の定義で置き換える。
  - B. #323 の文書はそのままにし、本書から相互参照する。
  - C. 2 つの文書を 1 つにまとめる。
- **推奨: B を当面とし、§8.2 と §8.5 が決まってから A を検討する。** 既に main にある文書の書き換えは差分が大きく、決まっていない項目の定義を先に固定してしまう。
- **互換性:** 文書だけの変更で、コードには影響しない。
- **セキュリティ:** 定義が二つ残ると、読み手が異なる定義を採る危険がある。相互参照でその旨を示す。
- **必要な人間の承認:** オーナーが A、B、C のどれかを決める。
- **判断前に進めてよいこと:** 本書から #323 文書への相互参照（§2 に記載済み）。
