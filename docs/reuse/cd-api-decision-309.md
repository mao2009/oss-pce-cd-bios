# 優先 CD API 別の OSS 再利用決定表 補遺（Issue #309, W18）

- 対象 Issue: [#309](https://github.com/mao2009/oss-pce-cd-bios/issues/309)（親: [#105](https://github.com/mao2009/oss-pce-cd-bios/issues/105)）。Refs のみ。
- 基準: 台帳ブランチ `feature/parallel-w17-oss-ledger` HEAD `159e968`（PR #561 の積み上げ）。
- **正本**: [cd-reuse-decision-309.md](../cd-reuse-decision-309.md)。API 別の判定、根拠、法務項目（§4）、推奨（§5）、検証（§7）は正本を参照する。正本は本 PR で変更しない。
- 本書の役割: (a) 正本の API 行への索引、(b) 正本に無かった行の追記、(c) 正本と台帳 [oss-candidate-ledger.md](../oss-candidate-ledger.md)（L-xx）との矛盾の列挙。判定は新たに作らない。
- 対象外: `/rom` と純正 BIOS は読まず、使用しない。リファレンス BIOS は実行しない。コードは転記しない。

## 1. 判定語

正本 §1 の語彙をそのまま採用する: `REUSE_OK`（本表では該当なし）、`CLEANROOM_ONLY`、`DO_NOT_USE_UNTIL_LEGAL_REVIEW`、`UNKNOWN`。
台帳の状態は次の語で併記する: `PENDING`（台帳 disposition。人間承認待ち）。全候補は PENDING（cleared 0 件）。本書は「再利用可」「採用」を確定しない。

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
| N-1 | `CD_STAT` | L-18 ares `ares/pce/pcd/io.cpp`、`scsi.cpp` | REFERENCE_ONLY / PENDING | 正本 §3.2 は同ファイルを CD_READ 行に記録。CD_STAT への対応付けは本書の新規追記 | `CLEANROOM_ONLY`（正本 §3.2 の語を継承）。PENDING |

N-1 の依存と未決: ares の controller 層が CD_STAT の mode と戻り A に相当する状態を持つかは未確認（UNKNOWN、§4 U-1）。

## 4. 新規 UNKNOWN

| ID | 対象 | 内容 | 根拠の所在 |
| --- | --- | --- | --- |
| U-1 | `CD_STAT` | ares の状態表現が CD_STAT の戻り値（mode、A）に対応するかは未確認 | 正本 §4.4 の mode と A の UNKNOWN に依存 |
| U-2 | 台帳 | HuC `include/huc/cdrom.asm`（CD_EXEC、正本 §3.3）、elmer `cd-core-scsitest/scsitest.asm`（CD_STAT、正本 §3.4）、`include/hucc/pcengine.inc`（宣言、正本 §3.1、§3.3、§3.4）は台帳に**未登録**。正本の判定は台帳の監査を経ていない | 台帳 [oss-candidate-ledger.md](../oss-candidate-ledger.md) の L-xx に無いことを確認。正本の 309 判定は残る |
| U-3 | 基準 | 正本の基準は `origin/main` = `1cd5582`（2026-10-21）、台帳の基準は `origin/main` = `6407bd4`（2026-10-20）。正本の判定が台帳と同じ版を見ているかは未確認 | 両文書の基準行 |

## 5. 矛盾一覧（owner 判断。どちらが正しいかは断定しない）

台帳の値と正本の判定語が異なる箇所。語の差か判定の方向の差かを区別した。

| ID | 候補（台帳） | 台帳 | 正本 | 種別 | owner 判断事項 |
| --- | --- | --- | --- | --- | --- |
| K-1 | L-08 HuC elmer `cdrom.asm` | REFERENCE_ONLY / PENDING | §3.2 `DO_NOT_USE_UNTIL_LEGAL_REVIEW` | 方向の差（参照のみ か 採用不可 か） | 権利未確定の扱いを一つに決める |
| K-2 | L-09 Hu-Go! `pcecd.c` | REFERENCE_ONLY / PENDING | §3.2 `DO_NOT_USE_UNTIL_LEGAL_REVIEW` | 方向の差 | 同上。参照する `LICENSE.bsd-modified` が pin に無い点を含む |
| K-3 | L-14 NeoCD `hlebios.cpp` 他 | REFERENCE_ONLY / PENDING | §3.2 `DO_NOT_USE_UNTIL_LEGAL_REVIEW` | 方向の差 | ファイル単位の権利者 UNKNOWN の扱い |
| K-4 | L-10 Hu-Go! `bios.c` | REJECTED / 提案 RESTRICTED | §3.1、§3.4 `DO_NOT_USE_UNTIL_LEGAL_REVIEW` | 語の差（採用不可の方向は一致） | 語を統一する |
| K-5 | L-02、L-06、L-13 | REFERENCE_ONLY / PENDING | §3.1、§3.2 `CLEANROOM_ONLY` | 語の差（参照のみの方向は一致） | 語を統一する |
| K-6 | L-18 ares | REFERENCE_ONLY / PENDING | §3.2 `CLEANROOM_ONLY`（ISC は写せると記す） | 語の差。正本は法的に写せると記し、台帳は方針 `REFERENCE_ONLY` を維持 | 方針を優先するか確認する |
| K-7 | L-10、L-11 | 「提案: RESTRICTED（確定ではない）」 | §4 項 5 で「採用対象外とする確定」と記す | 確定の度合いの差 | 確定するかを人間が判断する |

矛盾の判定を本書で一方に寄せることはしない。

## 6. 検証

| 対象 | 方法 | 結果 |
| --- | --- | --- |
| 本書の相対 Markdown リンク | `docs/reuse/` からの解決とファイル存在の確認 | 下記 §6.1 |
| 台帳 ID の参照 | 本書内の L-xx を台帳と照合 | 下記 §6.1 |
| 正本の変更有無 | `git diff` で正本のパスを確認 | 正本は未変更（PASS） |

### 6.1 結果

- 相対リンク: 3 件すべて存在（リンク切れ 0）。
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
