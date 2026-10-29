# 優先 CD API 別の OSS 再利用決定表 補遺（Issue #309, W18）

- 対象 Issue: [#309](https://github.com/mao2009/oss-pce-cd-bios/issues/309)（親: [#105](https://github.com/mao2009/oss-pce-cd-bios/issues/105)）。Refs のみ。
- 基準: 台帳ブランチ `feature/parallel-w17-oss-ledger` HEAD `159e968`（PR #561 の積み上げ）。
- **正本**: [cd-reuse-decision-309.md](../cd-reuse-decision-309.md)。API 別の判定、根拠、法務項目（§4）、推奨（§5）、検証（§7）は正本を参照する。正本は本 PR で変更しない。
- 本書の役割: (a) 正本の API 行への索引、(b) 正本に無かった行の追記、(c) 正本と台帳 [oss-candidate-ledger.md](../oss-candidate-ledger.md)（L-xx）との差の 3 軸整理。新たな採用判定は作らない。
- 対象外: `/rom` と純正 BIOS は読まず、使用しない。リファレンス BIOS は実行しない。コードは転記しない。

## 1. 判定の 3 軸

差分の整理には次の 3 軸を使う。軸は互いに独立しており、一つの軸の値から他の軸を推測しない。

| 軸 | 取り得る値 | 意味 |
| --- | --- | --- |
| `reuse_mode` | `REFERENCE_ONLY` / `DIRECT` / `ADAPTED` / `REJECTED` | 台帳の利用形態。`REFERENCE_ONLY` は仕様・挙動の参照に限り、コードを写さないという意味 |
| `code_adoption` | 取り込み禁止 / 保留（理由付き） | コードをリポジトリや ROM に取り込めるかの状態。本書は取り込みを承認しない |
| `approval_status` | `PENDING` / `提案: RESTRICTED（確定ではない）` / （人間による確定の記録のみ） | 人間による確定状態。本書は `APPROVED` と記す行を作らない |

語の扱い:
- 正本 §1 の語（`CLEANROOM_ONLY`、`DO_NOT_USE_UNTIL_LEGAL_REVIEW`、`UNKNOWN`）は、3 軸のどれに当たるかを §5 で示す。
- 正本の `CLEANROOM_ONLY` は `reuse_mode = REFERENCE_ONLY` の語に相当する。正本の `DO_NOT_USE_UNTIL_LEGAL_REVIEW` は `code_adoption = 取り込み禁止` に相当する。
- `reuse_mode` と `code_adoption` は独立に記録する。`REFERENCE_ONLY` でも、権利が不確実なコードの取り込みは禁止である。

## 2. API 索引（正本の行を重複させない）

| API（slot / エントリ） | 正本の行 | 正本の総合判定 | 本書での追記 |
| --- | --- | --- | --- |
| `CD_BOOT`（`$00` / `$E000`） | 正本 §2、§3.1 | `CLEANROOM_ONLY` | 追記なし（L-09、L-13 は正本 §3.2 の CD_READ 行に記録済みのため参照のみ） |
| `CD_READ`（`$03` / `$E009`） | 正本 §2、§3.2 | `CLEANROOM_ONLY` | 追記なし。台帳の全候補（L-02、L-08、L-09、L-13、L-14、L-18）は正本に記録済み |
| `CD_EXEC`（`$05` / `$E00F`） | 正本 §2、§3.3 | `UNKNOWN` | 追記なし |
| `CD_STAT`（`$09` / `$E01B`） | 正本 §2、§3.4 | `CLEANROOM_ONLY` | 追記 1 件（L-18 ares の CD_STAT 対応。§3） |

正本の §5（推奨 API）と §4（法務項目 1〜8）は本書で繰り返さない。

## 3. 正本に無かった行

| 行 | API | 候補 | 台帳 reuse_mode / disposition | 正本との関係 | 判定 |
| --- | --- | --- | --- | --- | --- |
| N-1 | `CD_STAT` | L-18 ares `ares/pce/pcd/io.cpp`、`scsi.cpp` | REFERENCE_ONLY / PENDING | 正本 §3.2 は同ファイルを CD_READ 行に記録。CD_STAT への対応付けは本書の新規追記 | **判定保留**（新たな判定は付けない）。PENDING |

判定を保留した理由: 正本 §3.2 の `CLEANROOM_ONLY` は CD_READ の根拠に基づく判定であり、CD_STAT の根拠ではない。CD_STAT の mode と戻り A の対応が未確認のため（§4 U-1）、本書は CD_STAT 欄に判定を写さない。

N-1 の依存と未決: ares の controller 層が CD_STAT の mode と戻り A に相当する状態を持つかは未確認（UNKNOWN、§4 U-1）。

## 4. 新規 UNKNOWN

| ID | 対象 | 内容 | 根拠の所在 |
| --- | --- | --- | --- |
| U-1 | `CD_STAT` | ares の状態表現が CD_STAT の戻り値（mode、A）に対応するかは未確認 | 正本 §4.4 の mode と A の UNKNOWN に依存 |
| U-2 | 台帳 | HuC `include/huc/cdrom.asm`（CD_EXEC、正本 §3.3）、elmer `cd-core-scsitest/scsitest.asm`（CD_STAT、正本 §3.4）、`include/hucc/pcengine.inc`（宣言、正本 §3.1、§3.3、§3.4）は台帳に**未登録**。正本の判定は台帳の監査を経ていない | 台帳 [oss-candidate-ledger.md](../oss-candidate-ledger.md) の L-xx に無いことを確認。正本の 309 判定は残る |
| U-3 | 基準 | 正本の基準は `origin/main` = `1cd5582`（2026-10-21）、台帳の基準は `origin/main` = `6407bd4`（2026-10-20）。正本の判定が台帳と同じ版を見ているかは未確認 | 両文書の基準行 |

## 5. 正本と台帳の差（3 軸で整理）

owner の決定に基づく整理。各行の `approval_status` は人間による確定状態を表し、未確定の行は `PENDING` または「提案」のまま記す。本書で確定したものは無い。

| ID | 候補（台帳 ID） | 台帳 `reuse_mode` | 正本の判定語（軸への対応） | `code_adoption` | `approval_status` | 差の種別 |
| --- | --- | --- | --- | --- | --- | --- |
| K-1 | L-08 HuC elmer `cdrom.asm` | `REFERENCE_ONLY` | `DO_NOT_USE_UNTIL_LEGAL_REVIEW`（`code_adoption` の禁止に相当） | 取り込み禁止。ベースは Hudson 原コード由来で権利者・表示が未確認 | PENDING | 語と軸の対応の差。`REFERENCE_ONLY` と取り込み禁止は両立する |
| K-2 | L-09 Hu-Go! `pcecd.c` | `REFERENCE_ONLY` | `DO_NOT_USE_UNTIL_LEGAL_REVIEW`（同上） | 取り込み禁止。著作権者未確認。参照先の `LICENSE.bsd-modified` が pin に無い | PENDING | 同上 |
| K-3 | L-14 NeoCD `hlebios.cpp` 他 | `REFERENCE_ONLY` | `DO_NOT_USE_UNTIL_LEGAL_REVIEW`（同上） | 取り込み禁止。ファイル単位の権利者 UNKNOWN | PENDING | 同上。技術情報の参照は独立資料で裏付けられた事実に限る |
| K-4 | L-10 Hu-Go! `bios.c` | `REJECTED` | `DO_NOT_USE_UNTIL_LEGAL_REVIEW`（§3.1、§3.4） | 取り込み禁止。GPL-2.0 の既定。MIT の ROM へ組込み不可 | 提案: RESTRICTED（確定ではない） | 語の差。`reuse_mode` と取り込み禁止の方向は一致 |
| K-5 | L-02、L-06、L-13 | `REFERENCE_ONLY` | `CLEANROOM_ONLY`（§3.1、§3.2） | 保留。参照のみの方針で、コードの取り込みは承認されていない | PENDING | 語の差。`reuse_mode` の方向は一致 |
| K-6 | L-18 ares | `REFERENCE_ONLY` | `CLEANROOM_ONLY`（§3.2。ISC の表示保持で写せると記す） | 保留。ISC は法的に写せ得るが、`REFERENCE_ONLY` 方針を維持し取り込まない。方針変更は owner 判断 | PENDING | 語の差。方針（`REFERENCE_ONLY`）は維持する |
| K-7 | L-10、L-11 | `REJECTED` | 正本 §4 項 5 は「採用対象外とする確定」と記す | 取り込み禁止（K-4 と同じ） | 提案: RESTRICTED（未確定。正式承認ではない） | 確定度の差。正本の「確定」の語は台帳の「提案」と食い違う。正本は変更せず、確定するかは owner が判断する |

- 本書は第三者コードの採用を承認しない。`approval_status` に `APPROVED` と記す行は無い。
- K-7 の `RESTRICTED` は台帳の「提案」であり、確定ではない。本書および本 PR で正式承認の記述をしない。
- 技術情報（CD_READ、CD_EXEC、CD_STAT の挙動）は、独立資料（台帳、[cd-oss-survey.md](../cd-oss-survey.md) 等）で裏付けられた事実に限る。コードの構造や文言は写さない。

## 6. 検証

| 対象 | 方法 | 結果 |
| --- | --- | --- |
| 本書の相対 Markdown リンク | `docs/reuse/` からの解決とファイル存在の確認 | 下記 §6.1 |
| 台帳 ID の参照 | 本書内の L-xx を台帳と照合 | 下記 §6.1 |
| 正本の変更有無 | `git diff` で正本のパスを確認 | 正本は未変更（PASS） |

### 6.1 結果

- 相対リンク: 一意なリンクは 2 件（正本、台帳）。いずれも存在（リンク切れ 0）。
- 台帳 ID: 本書が引用する L-02、L-06、L-08、L-09、L-10、L-11、L-13、L-14、L-18 は台帳に存在（不一致 0）。
- 外部 URL: 本書は新規に取得していない（NOT_RUN）。

### 6.2 リンク検査

- 実施: Python で本書の相対リンクを解決し、ファイル存在を確認。結果は §6.1 のとおり（PASS）。
- 正本 `docs/cd-reuse-decision-309.md` は本 PR の差分に含まれない（台帳ブランチに対する差分は空）。

### 6.3 NOT_RUN

| 項目 | 理由 |
| --- | --- |
| 実機・実コア（Geargrafx）での検証 | 本 Issue は決定表の補遺のみ。範囲外 |
| 外部 URL の疎通 | 新規取得していない |
| 法務確認・provenance 監査 | 人間の作業 |
| 台帳 PENDING の確定 | 0 件のまま |
| CI | PR 作成後に実行 |

## 7. 範囲と限界

- コードのコピー、翻訳、ROM への取り込みは行っていない。純正 BIOS のバイト列は記載していない。
- 判定は法的助言ではない。
