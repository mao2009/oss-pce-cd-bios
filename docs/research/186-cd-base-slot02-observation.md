# Issue #186: CD_BASE slot `$02` の入口観測カタログとサニタイズ済み観測スキーマ（非実行）

対象: スロット `$02`（テーブル `$E006` = `$E000 + 3*2`）。**純正 callee の JSR は 0 件**。本書は
`docs/research/186-cd-base-lba-discrimination.md`（#731）の補完で、(a) 実行せずに取得できる観測と
実 JSR が必要な観測の厳密な分離、(b) #635（`docs/635-api-entry-observation.md`、#729/#730 の
自動ローダー）と突き合わせる観測スキーマ、(c) 状態区分と Owner 観測条件を定める。
コード: `tools/cd_base_slot02_observation.py`、テスト: `tests/host/test_cd_base_slot02_observation.py`。
プロジェクトは互換 System Card BIOS ではない。VERIFIED/DEV_CALLABLE は表現不能（スキーマで拒否）。

## 1. LBA / INDEX01 座標系の分離能力（再検証）

- 単一データトラック媒体（INDEX00=0、INDEX01=150 相当、D1）: 5 候補は 4 クラス。`ABS_LBA` と
  `INDEX00_START_PLUS_BLOCK` は同値（初回トラックの INDEX00 は常に LBA0）。`ABS` と `TRACK_START`
  は `(5,0)` 等で分離可能。
- 後続データトラック（INDEX00=150、INDEX01=300 相当、D2）: 5 候補すべて分離可能。
- INDEX01=0 の媒体: `ABS`/`TRACK_START`/`INDEX00_START` が 1 クラスに併合（識別不能）。
- 呼出側が定数を足す変種（`CALLER_FIXUP`）と callee が足す変種は、**どの媒体でも READ LBA 単独では
  識別不能**（#731 §1）。入力感度（O7）と呼出側ソース事実が必須。
- 商用媒体について `tools/cd_base_slot02_observation.media_structure()` / `candidate_partition()` は、
  stat-only 導出の構造**ブール値**と候補名のクラス分割のみを返し、LBA 数値は返さない。これは合成算術
  （HOST_ARITHMETIC）であり callee の証拠ではない。

## 2. 観測の分離（O1〜O12）

| ID | 内容 | 層 | 実 JSR | 現状 |
| --- | --- | --- | --- | --- |
| O1 | slot `$02` -> `$E006` | 静的（公開表） | 不要 | SOURCE_CONFIRMED（#635 表範囲） |
| O2 | 媒体構造（INDEX00/01 関係をブール値で） | stat-only（CUE 文本と BIN サイズのみ） | 不要 | 手段あり。実行は Owner ローカル opt-in |
| O3 | 媒体が分離できる候補クラス | ホスト算術 | 不要 | MEASURED（ホスト・合成のみ） |
| O4 | #635 観測器が JSR/JMP/直行/無呼出を区別 | 合成 + 固定コア | 不要（合成スタブ） | MEASURED（合成、`make api-entry-integration`） |
| O5 | 商用 IPL が最初に呼ぶテーブル slot・JSR/JMP・戻り一致 | #635（商用・呼出側） | 不要（callee は合成スタブ） | 本 PR では未実行。A の領域 |
| O6 | 呼出時点の A/X/Y/P/MPR | #635（呼出側） | 不要 | 公開記録は「記録した」ブールのみ。値はローカル限定 |
| O7 | callee が読む入力バイト | 純正 callee 差分 | **必要** | BLOCKED |
| O8 | CD_READ の SCSI READ LBA（座標系の確定） | 純正 callee | **必要** | BLOCKED |
| O9 | 戻り A/X/Y/P・戻り位置 | 純正 callee | **必要** | BLOCKED |
| O10 | MPR/RAM 副作用 | 純正 callee | **必要** | BLOCKED |
| O11 | CD_RESET 後の保持（#187） | 純正 callee | **必要** | BLOCKED |
| O12 | 失敗後の再試行 LBA（recbase 切替） | 純正 callee | **必要** | BLOCKED |

O5 は「ゲームが slot `$02` を最初に呼んだか」を示しうるが、**呼出側の事実**であって CD_BASE の
引数規約・戻り値・LBA 基準の証拠ではない。入口の JSR に至る**呼出側コードの静的逆アセンブル**は
商用バイトの読取りを伴うためここでは行わず、公開物にも含めない（ローカル限定の調査として Owner 判断）。

## 3. 観測スキーマ（`oss-pce-cd-bios/cd-base-slot02-observation/v1`）

固定キー: `schema, evidence_class, slot("$02"), tool, core_revision, jsr_count, status, reason, items`。
`items` は O ID をキーとし各値は `{status, value}`（MEASURED 以外は value=null）。

- `evidence_class`: `HOST_ARITHMETIC` / `SYNTHETIC_REAL_CORE`（pinned `b49ae82a` 必須） /
  `COMMERCIAL_STAT_ONLY` / `COMMERCIAL_CALLER_ENTRY` / `GENUINE_CALLEE`。各クラスが載せられる層は
  固定（例: 呼出側記録に O8 は載らない）。
- `status`: `NOT_RUN / SKIP / BLOCKED / UNKNOWN / SOURCE_CONFIRMED / MEASURED`。記録全体の `MEASURED` は
  いずれかの項目が MEASURED のときに限る。値を持てるのは MEASURED / SOURCE_CONFIRMED のみ。
- `reason` は許可リスト（`REASON_CODES`）のコードのみ（自由文不可）、`tool` は `[a-z0-9_]{0,40}`、`core_revision` は
  null か pinned の完全な 40 桁（SYNTHETIC_REAL_CORE は必須）。
- 項目値は観測ごとに厳密な型: O1 ブール、O2 ブール 3 つ、O3 候補名のリストのリスト、O4 ブール、O6 ブール、
  O5 `{verdict(#635 の 8 値), first_table_slot(0..80|null), method(JSR/JMP/DIRECT/NONE), in_table,
  return_matches_caller}`（整合性検査あり。例: JSR/JMP は表内かつ slot あり、DIRECT は表外で slot なし）。
  `first_table_slot` は商用 IPL の最初の呼出し slot であり、記録の対象 slot（`$02`）とは別。
  呼出し元 PC・入口 PC の数値・スタックバイトは含めない。O7-O12 は値を持てない。
- 拒否: パス/ハッシュ/タイトル/セクタ/ペイロード/PC/アドレス/LBA 値などのキー（camelCase・非文字列キー含む
  全階層）、VERIFIED/DEV_CALLABLE/COMPATIBLE の語（記号区切りの回避も検出。ただし語彙は列挙のため限定的な
  補助検査であり、主防御は上記の厳密な型・正規表現）、`jsr_count != 0`、未知キー、層とクラスの不一致、
  `GENUINE_CALLEE` の MEASURED/SOURCE_CONFIRMED（ゲートが slot `$02` を拒否する間は BLOCKED 記録のみ作成可）。
- #635 の商用証拠 JSON（verdict/apicalls/runs）は O5 へ機械的に写像できる（verdict 値は同一語彙）。

## 4. 状態区分（証拠に基づく）

- **VERIFIED**: なし（昇格不可）。
- **MEASURED（合成・ホスト）**: 座標系判別能力（#731）、O3、O4。純正 callee の証拠ではない。
- **SOURCE_CONFIRMED**: O1（テーブル範囲・stride）。
- **UNKNOWN**: CD_BASE の入力規約、READ LBA 基準、戻り値、MPR/RAM 副作用、保持、再試行（O7〜O12）、
  `CALLER_FIXUP` の delta、entry 環境（MPR0=`$FF` 仮説は未確認）。
- **BLOCKED**: O7〜O12 全部（純正 `$E006` の実 JSR）。理由: #173 の ST/一般 MMIO/IRQ 書込（H4/H5）の
  事前阻止が pinned コアで不成立、CD_BASE 固有の許可範囲（allowlist）が ABI UNKNOWN のため定義不能、
  #228 `reference_jsr_gate` が slot `$02` を拒否（迂回しない）、Owner 承認未取得。
- **NOT_RUN/SKIP**: O2/O5 の商用実測は本 PR では未実行（商用ローダー/観測の編集・実行責任は #729/#730 側）。

## 5. Owner 観測条件（不足事項）

1. #173: ST/一般 MMIO/IRQ 書込を実行前に阻止できる機構（または H4/H5 を受容する明示的な Owner 判断）。
2. CD_BASE 専用 allowlist と #228 台帳への slot `$02` 行（独立レビュー付き。現状は拒否で正しい）。
3. 合法に保有する純正 System Card 3.0 のローカル opt-in（`./rom/`、読取専用・非公開）。
4. 同一 fail-closed ハーネスでの限定実測順: stage 1（座標系 O8）-> stage 2（入力感度 O7）-> #187
   （O9〜O12）。単独 model 名・成功値は出さず、残余クラスで報告。
5. O2/O5 の商用ローカル実測を実施するか（本 PR は準備のみ）。公開は構造化ブール値・列挙値のみ。

## 6. 非主張

純正 BIOS 互換、CD_BASE 実装、ABI 確定、商用ゲームの起動前進は主張しない。Issue #186 は OPEN 維持。
