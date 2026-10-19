# ABI 証拠の3軸分離と DEV 専用実装の管理（設計提案）

**状態: 提案のみ。スキーマ・ゲート・既存台帳は変更しない。** 本書は新規ファイルであり、
`docs/abi-contracts.md`、`spec/api_contracts.json`、`docs/research/*` の各台帳、ゲートコードを
編集しない。スキーマ変更を伴う項目はすべてオーナー判断待ち（§6）。BIOS 互換性は主張しない。
全 81 スロットの `verification` は `NOT_RUN`、ROM は `abi: UNVERIFIED` / `release_eligible: false`、
参照ケース 270 件は `BLOCKED_ABI` のまま。

## 1. 目的

現行の証拠体系では、次の3つの問いが同じ語彙（`HYPOTHESIS`、`PENDING`、`OBSERVED` など）や
同じ行に並んで記録されている。

1. 公開ソースが何を述べているか。
2. そのソースやコードの再利用・配布が承認されているか。
3. 参照 BIOS の callee の挙動を実測したか。

3つは互いに独立であり、どれか1つが進んでも他は進まない。本書はこれを3つの軸として明示的に
分け、さらに OSS 側の DEV 専用実装（例: `src/overrides/slot_1E.s`）を4つ目の別台帳で扱う設計を示す。

## 2. 3軸の定義

### 2.1 SOURCE_EVIDENCE（公開ソースの記述）

- 問い: ピン留めした公開ソース（caller SDK、HLE エミュレータ、実コアのソース）がその項目について何を書いているか。
- 取り得る値: `UNKNOWN`（根拠なし） / `HYPOTHESIS`（ソースに記述がある。`confidence: SOURCE_SUPPORTED`）。
  claim status の `BLOCKED` はこの軸ではなく、2.3 の「判定できない」に含める。
- 上限: `HYPOTHESIS` / `SOURCE_SUPPORTED`。ソースの記述だけでは `OBSERVED` / `VERIFIED` にならない。
- 既存フィールドとの対応: `spec/api_contracts.json` の各 claim の `status`・`value`・`sources`・`confidence`、
  `mapping_status`、`name_observations`。ソース種別は `tools/contracts.py` の `SOURCE_KIND_MEANINGS`
  （`CALLER_SOURCE` / `HLE_SOURCE` / `EMULATOR_SOURCE`）。各 `docs/research/*-contract.md` の SOURCE_FACT 行もこの軸にあたる。
- 判断者: 調査者。誤りがあればレビューで直す。

### 2.2 PROVENANCE_APPROVAL（再利用・配布の承認）

- 問い: そのソースやコードを、コピー・改変・ROM への組み込み・配布に使ってよいか。
- 取り得る値: `PENDING` / `APPROVED` / `RESTRICTED`（`docs/provenance.md` の処分）。
  利用形態は `DIRECT` / `ADAPTED` / `REFERENCE_ONLY` / `REJECTED`。
- 既存フィールドとの対応: `spec/api_contracts.json` の `provenance_status`（schema 1 では `PENDING` 固定）、
  `docs/oss-audit-systemcard.md`。`tools/provenance_guard.py` が参照する `provenance/allowlist.tsv` と
  `provenance/approvers.txt` は、現時点ではリポジトリに存在しない（approver bootstrap は `BLOCKED`）。
- 判断者: 人間の承認者のみ。CI・スキャナ・エージェントは承認できない。
- 注意: `APPROVED` は法的な再利用許可であって、挙動が正しいことの根拠にはならない。

### 2.3 CALLEE_VERIFICATION（参照 BIOS callee の実測）

- 問い: 参照 BIOS の callee を実コアで実行し、入出力・フラグ・MPR・スタック・RAM 差分を測ったか。
- 取り得る値: `NOT_RUN` / `BLOCKED`（判定できない。claim status の `BLOCKED`、`BLOCKED_ABI` など）/ `OBSERVED`（`LOCAL_ONLY`）/ `VERIFIED`。
  `VERIFIED` は現スキーマでは表せない（`CLAIM_STATUSES` に無い。Issue #171 の対象）。
- 既存フィールドとの対応: `verification`（`status`・`executed_callee`・`evidence`）、
  `tools/reference_case_catalog.py` の `execution_status` / `measurement_status`。`OBSERVED` には次の2種類があり、別物として扱う。
  - 台帳 claim の `OBSERVED`（`tools/contracts.py`）: `executed_callee` と形式検査だけの evidence
    （`EVIDENCE_TRUST_LEVEL = FORMAT_ONLY`）が必要。この claim に付く `confidence` / `sources` は
    この軸の前提条件であって、SOURCE_EVIDENCE の根拠ではない。
  - `tools/reference_bios_runner.py` のローカル観測 `OBSERVED LOCAL_ONLY`（`LOCAL_ONLY_NOT_VERIFIED`）: 台帳の外の記録。
- 判断者: 実行記録（ROM ハッシュ、コア revision、入力、結果）。現状は全スロット `NOT_RUN`。
  slot `$1E` のローカル観測は `LOCAL_ONLY_NOT_VERIFIED` で、台帳には昇格していない。
- 注意: 実測した事実と、その事実を公開してよいかは別の問題。callee の内部実装の詳細は公開しない（`docs/provenance.md`）。

## 3. 軸の間に含意はない

| 前提 | 含意しないもの |
| --- | --- |
| SOURCE_EVIDENCE = `HYPOTHESIS` | provenance `APPROVED`、callee 実測、BIOS 挙動 |
| PROVENANCE_APPROVAL = `APPROVED` | ソースの記述が正しいこと、callee 挙動、互換性 |
| CALLEE_VERIFICATION = `OBSERVED LOCAL_ONLY` | `VERIFIED`、他スロットへの一般化、公開の許可、OSS 実装の正しさ |
| 3軸すべてが進んだ状態 | `release_eligible: true`（release gate は別の人間承認を要し、現在は常に `BLOCKED`） |

自動の昇格はしない。どの軸の値も、他の軸の値から計算・補完しない。ツールが1つの軸を読んで
別の軸を書き換えることは禁止する。

## 4. DEV 専用実装の分離管理（第4台帳の提案）

### 4.1 問題

`docs/abi-contracts.md` が書いているとおり、`spec/api_contracts.json` は参照 BIOS の検証状態を
記録する台帳で、OSS 側の開発実装を表す欄がない。そのため slot `$1E` には DEV ROM 用の override と
実コアでの部分的な実測があるのに、台帳上は `implementation_status: NOT_STARTED` のままになっている。
この欄を緩めて開発実装を書き込むと、OSS 実装の存在を参照 BIOS の検証と取り違える経路ができる。

### 4.2 提案（未実装・オーナー判断待ち）

参照 BIOS 台帳とは別のファイル（仮称 `spec/dev_implementations.json`）に、OSS DEV 専用実装だけを
記録する。行は slot ごとに1行。

| フィールド | 内容 | 制約 |
| --- | --- | --- |
| `slot` / `override` | 例: `0x1E` / `src/overrides/slot_1E.s` | `tools/build_rom.py` の `overrides()` と一致すること |
| `origin` | `ORIGINAL` / `ADAPTED` / `DIRECT` | `ADAPTED` / `DIRECT` は APPROVED の provenance 記録への参照が必須。APPROVED でなければ DEV ROM を含むどのバイナリにも入れない（`docs/provenance.md`） |
| `provenance_approval` | 2.2 の値。人間の承認者と日付 | `ORIGINAL` で `APPROVED` 以外の場合は DEV_CALLABLE にしない |
| `structural_check` | `verify_dev_rom()` の結果 | ソースからの再ビルドとのバイト一致検査と構造検査。挙動の検証ではない |
| `dev_test` | OSS 実装に対する実コアテスト（例: `tests/integration/geargrafx_contracts.py`）の記録 | 測る対象は OSS 実装で、参照 BIOS ではない |
| `source_sha256` / `rom_sha256` / `core_revision` | override ソース、DEV ROM、Geargrafx の識別子 | 現在の値と一致しなければ、その行は DEV_CALLABLE から外れる |
| `compatibility_basis` | `VERIFIED` の CALLEE_VERIFICATION 記録への参照、または `NONE` | `VERIFIED` は現スキーマで表せない（#171）ため、当面は `NONE` 固定。`OBSERVED` / `LOCAL_ONLY` / SOURCE_EVIDENCE は参照できない |
| `abi` / `release_eligible` | `UNVERIFIED` / `false` | 定数。この台帳から変更できない |

### 4.3 DEV_CALLABLE の扱い

- `DEV_CALLABLE` は「DEV ROM のこの slot に OSS 実装があり、構造検査と DEV テストが通った」という、
  **DEV ROM についての記述**に限る。参照 BIOS の ABI や互換性については何も述べない。
- 書いてよい場所はこの第4台帳だけ。`spec/api_contracts.json` と `docs/research/*` には書かない
  （既存の禁止語テスト `tests/host/test_cd_*_contract.py` などはそのまま残す）。
- 条件（すべて必要）: `structural_check` PASS、`dev_test` を実行して PASS、`provenance_approval` が `APPROVED`
  （`ORIGINAL` の場合も人間の確認が必要）、人間による記入。ツールによる自動記入はしない。
- `compatibility_basis: NONE` の行は、DEV_CALLABLE であっても「互換性根拠なし」と表示する。
  DEV テストの期待値が `VERIFIED` の記録に結び付いていない場合、その期待値は OSS 側の自前仕様として扱い、
  互換性の根拠にはしない。`tests/integration/geargrafx_contracts.py` の slot `$1E` の期待値は
  `LOCAL_ONLY` の実測に基づくので、これも自前仕様にあたる。
- SOURCE_EVIDENCE（`HYPOTHESIS`）や `OBSERVED LOCAL_ONLY` を根拠に、互換性があるものとして扱うことはできない。
  これで「未検証の互換性が DEV_CALLABLE に昇格する」経路を塞ぐ。
- 現行の slot `$1E` は、provenance の承認者がいない（bootstrap `BLOCKED`）ため、この提案の下では
  DEV_CALLABLE にならない。`docs/testing.md` の「Callable DEV-only」は DEV ROM 上で呼び出して戻ることを
  実測したという記述で、この状態語とは別のものである。

## 5. 既存ゲートへの影響

本書は提案文書だけで、どのゲートも変更しない。§4 を実装する場合も、次のゲートは緩めずに残すことを前提にする。

| ゲート | 現行の挙動 | 影響 |
| --- | --- | --- |
| `tools/contracts.py` `validate_contracts` | `provenance_status=PENDING`、`implementation_status=NOT_STARTED`、`verification=NOT_RUN` を強制 | 変更なし。開発実装は第4台帳に置くので、`NOT_STARTED` を緩める必要がない |
| `CLAIM_STATUSES` / `EVIDENCE_TRUST_LEVEL` | `VERIFIED` を表せない。`OBSERVED` には evidence が必要（FORMAT_ONLY） | 変更なし。`VERIFIED` は #171 の範囲 |
| `tools/reference_case_catalog.py` `classify` | `reviewed_abi`/fixture が無ければ `BLOCKED_ABI`。MEASURED は `LOCAL_MEASUREMENT` | 変更なし。第4台帳は `reviewed_abi` を設定しない |
| `tools/build_rom.py` `release_gate` | override があっても常に `BLOCKED`（exit 3） | 変更なし。DEV_CALLABLE を release 判定の入力にしない |
| `tools/provenance_guard.py` | allowlist 変更には承認者が必要。bootstrap は `BLOCKED` | 変更なし。2.2 の `APPROVED` はこのゲートを迂回しない |
| `tools/evidence_provenance.py` | PASS でも `compatibility: NOT_PROMOTED` | 変更なし |
| `tools/compat_evidence.py` | stage を自動昇格しない | 変更なし |
| 研究台帳の禁止語テスト | `DEV_CALLABLE`/`VERIFIED`/`OBSERVED`/`PASS` などを禁止 | 変更なし。§4.3 で書き込み先を第4台帳だけに限るため、両立する |

実装時に追加すべき負例テスト（提案）:

- 第4台帳を読んで `spec/api_contracts.json` の値や `reviewed_abi` を書き換えるツールがあれば失敗する。
- `compatibility_basis` が `VERIFIED` 以外（SOURCE_EVIDENCE、`OBSERVED`、`LOCAL_ONLY`、FORMAT_ONLY evidence）を指していたら失敗する。
- `source_sha256` / `rom_sha256` / `core_revision` が現在の値と一致しない行が DEV_CALLABLE のままなら失敗する。
- `abi != UNVERIFIED` または `release_eligible != false` なら失敗する。
- `release_gate` が第4台帳の内容にかかわらず `BLOCKED` を返すこと。

## 6. オーナー判断事項

1. 第4台帳（仮称 `spec/dev_implementations.json`）とその validator を導入するか。導入しない場合、slot `$1E` の DEV 実装は今後も台帳外（`docs/abi-contracts.md` の注記）で扱う。
2. `DEV_CALLABLE` を §4.3 の意味で定義するか。誰が記入してよいか。
3. `ORIGINAL` の OSS 実装の `provenance_approval` を誰が承認するか（`provenance/approvers.txt` の bootstrap は現在 `BLOCKED`）。
4. 研究台帳（`docs/research/*.json` と `spec/api_contracts.json`）に、3軸を明示するキー（例: `source_evidence` / `provenance_approval` / `callee_verification`）を追加するか。現行のフィールド名は変えず、§2 の対応表を当面の読み替えとする。
5. CALLEE_VERIFICATION で実測した事実のうち、公開してよい範囲（caller から見える ABI に限るか）。

本書は上記を実装しない。判断が出た項目ごとに Issue を分けて実装する。
