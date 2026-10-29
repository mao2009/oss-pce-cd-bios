# 優先 CD API 別の OSS 再利用決定表（Issue #309, W18）

- 対象 Issue: [#309](https://github.com/mao2009/oss-pce-cd-bios/issues/309)（親: [#105](https://github.com/mao2009/oss-pce-cd-bios/issues/105)）。Refs のみ。
- 基準: 台帳ブランチ `feature/parallel-w17-oss-ledger` の HEAD `159e968`（PR #561 の積み上げ。main 未マージ）。
- 位置づけ: **決定表（調査のみ）**。コードのコピー・翻訳・ROM への取り込みは行わない。
- 状態: 台帳の全候補は `PENDING`（cleared 0 件）。本書は「再利用可」「採用」を確定しない。各行は「候補」「依存」「未決（PENDING）」「UNKNOWN」で表す。
- 台帳参照: [oss-candidate-ledger.md](../oss-candidate-ledger.md)（L-xx）、[reuse-inventory.md](../reuse-inventory.md)、[reuse/oss-candidate-verification-308.md](oss-candidate-verification-308.md)。本書はこれらを編集しない。
- 対象外: 純正 BIOS・市販 ROM・`./rom/` の私有ファイルは読まず、使用しない。リファレンス BIOS は実行しない。コードは転記しない。

## 1. 語の定義

| 語 | 意味 |
| --- | --- |
| 候補 | API の実装または契約確認に参照しうる既存コード。台帳 ID（L-xx）で示す。採用の意味を持たない。 |
| 依存 | 候補を使う場合に必要になる他 API・RAM・MPR・IRQ・スタック・メディア。 |
| 未決（PENDING） | 台帳の disposition が PENDING（人間承認待ち）、または本書で確定できない判断。 |
| UNKNOWN | 根拠から確認できない項目。推測で埋めない。 |

対応付けの注意: 台帳には API 欄が無い。候補と API の対応は、台帳の「使える範囲」欄と [cd-reuse-decision-309.md](../cd-reuse-decision-309.md) の記述から本書が判断した。対応付けの確定は人間のレビューによる。

## 2. API 行（決定表）

| API（slot / エントリ） | 既存 Issue | 候補（台帳 ID） | 依存（要約） | 未決（PENDING） | UNKNOWN 件数 | 台帳 disposition |
| --- | --- | --- | --- | --- | --- | --- |
| `CD_BOOT`（`$00` / `$E000`） | #9、#289 | L-06、L-09、L-10、L-13 | MPR7 の bank 切替（呼出側）、callee の MPR 要求、IRQ、スタック | 4 候補。L-10 は提案 RESTRICTED を人間が判定 | 6（§4.1） | PENDING |
| `CD_READ`（`$03` / `$E009`） | #12 | L-02、L-08、L-09、L-13、L-14、L-18 | MPR3、8KB 単位の分割、CD メディア、`$1800`–`$1808` レジスタ、IRQ（現行は polling） | 6 候補すべて。L-08 はベースの権利者未確定 | 6（§4.2） | PENDING |
| `CD_EXEC`（`$05` / `$E00F`） | #14、#239 | 台帳に登録なし（§5 の G-1） | 呼出側が固定版で未発見。MPR、SP、IRQ が未確定 | 候補の登録と権利確認 | 8（§4.3） | 該当なし（台帳未登録） |
| `CD_STAT`（`$09` / `$E01B`） | #18、#229 | L-10、L-18、台帳未登録（§5 の G-2） | 作業 RAM `_ax`〜`_dh`（定義のみ）、busy 中の動作、MPR、IRQ | L-10 は提案 RESTRICTED。L-18 は参照のみ | 7（§4.4） | PENDING（L-10 を除く） |

API 行数: 4。UNKNOWN 件数の合計: 27（各行は §4 の番号付き一覧と一致させる）。

## 3. 候補別の決定表

列: 行 | API | 候補（台帳 ID・ファイル） | 台帳の reuse_mode / disposition | 本書の判定 | 依存・未決の要点

| 行 | API | 候補 | 台帳 reuse_mode / disposition | 判定 | 依存・未決の要点 |
| --- | --- | --- | --- | --- | --- |
| C-01 | `CD_BOOT` | L-06 `include/hucc/core-kernel.asm`（`call_bios`） | REFERENCE_ONLY / PENDING | 候補（参照のみ） | 呼出側の事実のみ。割込み契約が System Card と食い違う恐れ |
| C-02 | `CD_BOOT` | L-09 Hu-Go! `pcecd.c` | REFERENCE_ONLY / PENDING | 候補（参照のみ） | 著作権者 UNKNOWN。`LICENSE.bsd-modified` が pin に無い |
| C-03 | `CD_BOOT` | L-10 Hu-Go! `bios.c` | REJECTED（GPL-2.0 既定） / 提案 RESTRICTED | 未決（人間判定） | HLE の挙動は実機の証拠にならない |
| C-04 | `CD_BOOT` | L-13 nugget `openbios/cdrom/cdrom.c` 他 | REFERENCE_ONLY / PENDING | 候補（参照のみ） | PSX ABI。設計パターンの参照のみ |
| C-05 | `CD_READ` | L-02 `include/hucc/hucc-systemcard.asm` | REFERENCE_ONLY / PENDING | 候補（参照のみ） | 呼出側ラッパ。callee ではない。`_dh=0` とエラー判定の形を参照 |
| C-06 | `CD_READ` | L-08 HuC elmer `examples/asm/elmer/include/cdrom.asm` | REFERENCE_ONLY / PENDING | 未決（権利 UNKNOWN） | ベースは Hudson 原コード由来で表示なし。採用不可扱い |
| C-07 | `CD_READ` | L-09 Hu-Go! `pcecd.c` | REFERENCE_ONLY / PENDING | 候補（参照のみ） | C-02 と同じ。転送の状態遷移の挙動参照 |
| C-08 | `CD_READ` | L-13 nugget `openbios/cdrom/*` | REFERENCE_ONLY / PENDING | 候補（参照のみ） | 有界待ちの設計パターン（PSX ABI） |
| C-09 | `CD_READ` | L-14 NeoCD-Libretro `hlebios.cpp` 他 | REFERENCE_ONLY / PENDING | 未決（ファイル単位の権利者 UNKNOWN） | ルート LICENSE.md は LGPL-3.0。ファイル表示なし |
| C-10 | `CD_READ` | L-18 ares `ares/pce/pcd/io.cpp`、`scsi.cpp` | REFERENCE_ONLY / PENDING | 候補（参照のみ） | `$1800` の bit、ACK、リセットの挙動参照。C++ のため firmware に不適合 |
| C-11 | `CD_EXEC` | 台帳未登録: HuC `include/huc/cdrom.asm` の `_cd_execoverlay` | 台帳に無い | 未決（登録と権利確認が必要） | ファイル冒頭に著作権・ライセンス表示なし（UNKNOWN） |
| C-12 | `CD_STAT` | 台帳未登録: HuC elmer `cd-core-scsitest/scsitest.asm`（681–695 行の busy/ready 判定） | 台帳に無い | 候補（参照のみ・未登録） | Boost-1.0 表示。試験プログラムのため根拠は呼出側の形のみ |
| C-13 | `CD_STAT` | L-18 ares `io.cpp` 他 | REFERENCE_ONLY / PENDING | 候補（参照のみ） | 戻り値の意味は ares の controller 層では確定しない |
| C-14 | `CD_STAT` | L-10 Hu-Go! `bios.c` の `CD_STAT` 分岐 | REJECTED / 提案 RESTRICTED | 未決（人間判定） | A=0 の HLE は証拠にならない |

候補行: 14（CD_BOOT 4、CD_READ 6、CD_EXEC 1、CD_STAT 3）。

## 4. API 別の依存と UNKNOWN（番号付き）

### 4.1 `CD_BOOT`

1. 入口の意味
2. 入力レジスタ
3. 戻り値
4. callee の MPR0–6 要求（呼出側は MPR7 を bank `$00` にして戻り後に復元する。[cd-boot-contract](../research/cd-boot-contract.md)）
5. スタック
6. IRQ 状態

### 4.2 `CD_READ`

1. 本体の戻り値とエラー種別（[cd-read-contract](../cd-read-contract.md)）
2. READ の再試行上限（上位に見えない。r3 §2）
3. count 0 の意味
4. MPR3 退避後の状態（HuCC コメントの記載のみ。未実測）
5. ROM 側バッファ
6. READ のタイムアウト（grep 上は無し）

### 4.3 `CD_EXEC`

1. 入力
2. 出力
3. 成功時の非復帰（HYPOTHESIS。UNKNOWN 扱い）
4. MPR
5. SP
6. IRQ
7. 失敗時の戻り（[cd-exec-contract-ledger](../cd-exec-contract-ledger.md) の B2 参照）
8. 呼出側の網羅性（固定版の grep 結果。検索の網羅性は未検証）

### 4.4 `CD_STAT`

1. mode の意味（F04、F11、F12）
2. 戻り値 A の意味
3. busy 中の動作（F24）
4. 戻り値のエンコーディング
5. 呼出し中の MPR0–6（F19）
6. IRQ 状態と IRQ2 の発生有無（F22）
7. 作業 RAM `_ax`〜`_dh` への書換えの有無（F20。BLOCKED_ABI）

（出典: [cd-stat-contract](../research/cd-stat-contract.md) §6、[cd-stat-device-observation](../cd-stat-device-observation.md)）

## 5. 台帳のギャップ（後続 Issue で扱う）

- G-1: HuC `include/huc/cdrom.asm` は [oss-candidate-ledger.md](../oss-candidate-ledger.md) に登録が無い。`CD_EXEC` の唯一の関連ファイルであり、登録と権利確認が必要。
- G-2: HuC elmer `cd-core-scsitest/scsitest.asm` は台帳に登録が無い。`CD_STAT` の busy/ready 判定の唯一の根拠であり、登録が必要。
- G-3: HuC `include/hucc/pcengine.inc`（`cd_boot` / `cd_exec` / `cd_stat` の番地宣言）は台帳に無い。宣言のみで実装物ではないため、登録の優先度は低い。
- G-4: 台帳の「使える範囲」は API 別ではない。API 別の対応は本書 §3 が担うため、台帳側の列追加は人間判断。

## 6. 先行文書との関係

同じ Issue の [cd-reuse-decision-309.md](../cd-reuse-decision-309.md) は判定語に `CLEANROOM_ONLY`、`DO_NOT_USE_UNTIL_LEGAL_REVIEW`、`UNKNOWN` を使い、`CD_STAT` の実装前提の推奨を記す。本書はその判定を上書きせず、台帳の PENDING 語彙（候補・依存・未決・UNKNOWN）に揃えて表示した。両文書の判定語の統合はレビューで決める。本書に「推奨 API」の判定は含めない。

## 7. 検証

### 7.1 パス・リンクの実在確認

| 対象 | 方法 | 結果 | 備考 |
| --- | --- | --- | --- |
| 本書内の相対リンク（Markdown リンク） | `docs/reuse/` からの相対パスを解決してファイル存在を確認 | 下記 7.2 に記録 | 外部 URL は未確認（7.3） |
| 台帳 ID（L-01〜L-20）の参照 | [oss-candidate-ledger.md](../oss-candidate-ledger.md) 内の ID を grep | PASS（本書が引用する L-02、L-06、L-08、L-09、L-10、L-13、L-14、L-18 は全て台帳に存在） | 台帳の値は再計測していない |
| 台帳未登録の 2 ファイル（G-1、G-2） | リポジトリ内を `git ls-files` で検索 | PASS（台帳に無いことを確認） | 上流ツリーは未取得 |

### 7.2 リンク検査の結果

- 相対 Markdown リンク 12 件: 12 件存在（リンク切れ 0）。
- 外部 URL 2 件（Issue #309、#105）: 未取得（NOT_RUN）。
- 台帳 ID: 本書が引用する ID はすべて台帳に存在（不一致 0）。
- 結果: PASS（リンク切れなし）。

### 7.3 NOT_RUN（実施していない項目）

| 項目 | 状態 | 理由 |
| --- | --- | --- |
| 実機・実コア（Geargrafx）での CD_BOOT / CD_READ / CD_EXEC / CD_STAT の観測 | NOT_RUN | 本 Issue は決定表の作成のみ。実機・実コアの検証は範囲外。`./rom/` は使用しない |
| 外部 URL（上流の GitHub ページ）の疎通 | NOT_RUN | 本書は外部 URL を新たに取得していない。引用は既存文書の値 |
| 候補のライセンス・権利の法務確認 | NOT_RUN | 人間の provenance 監査が必要 |
| 台帳 PENDING の確定（cleared 件数） | NOT_RUN（0 件のまま） | 承認は人間が行う |
| CI（`make check` 等） | NOT_RUN | 本 PR は文書のみ。CI は PR 作成後に実行される |

## 8. 範囲と限界

- コードのコピー、翻訳、ROM への取り込みは行っていない。純正 BIOS のバイト列は記載していない。
- 本書の UNKNOWN は、既存文書と台帳に根拠が無いことを示す。実機で誤りと判明した場合は、該当行を更新する。
- 判定は法的助言ではない。採用には人間の provenance 監査と承認記録（台帳の監査テンプレート）が必要。
