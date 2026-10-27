# CD ブートローダの最小仕様と合成ブートメディア（#550、wave26 W04）

状態: **仕様案（DRAFT）。実装・ROM 配線・参照 BIOS 実行は行わない。**
参照 JSR ゲートは CLOSED のまま。`spec/api_contracts.json` の schema・VERIFIED 状態は変更しない。

本書は次を分ける。

1. ゲーム固有の事実（CUE のメタデータのみ。セクタ内容は読んでいない）
2. SUPER CD-ROM2 の一般規則（公開資料に基づく。多くは HYPOTHESIS）
3. 本リポジトリが定める最小ローダ契約（合成メディアで試験する対象）

**sector 0 が実行されるとは仮定しない。** 契約は「データトラックの先頭から読む」ところから始める。

確度の表記:

- `VERIFIED`: 本リポジトリの開発用イメージを固定版 Geargrafx (`b49ae82a`) で実測した事実のみ。実機・別エミュレータでは未確認。
- `HYPOTHESIS`: 公開資料の記述、または資料からの推測。一次資料・実行で未確認。
- `UNKNOWN`: 出典がない、または出典が取得できなかった。値は埋めない。

---

## 1. ゲーム固有の事実（CUE のメタデータのみ）

対象: `C:/Users/Hobart/Downloads/Shin Megami Tensei (Japan)/Shin Megami Tensei (Japan).cue`（ローカル読取のみ。内容は本書に含めない）。

| 項目 | CUE から読んだ内容 | 確度 |
| --- | --- | --- |
| トラック数 | 16 | VERIFIED（CUE の記述どおり） |
| データトラック | `TRACK 02 MODE1/2352` の 1 本のみ。他は `AUDIO` | VERIFIED（CUE の記述どおり） |
| トラック 02 の INDEX | `INDEX 00 00:00:00`、`INDEX 01 00:03:00`（プリギャップ 225 セクタ = 3 秒 × 75 フレーム。150 ではない） | VERIFIED（CUE の記述どおり） |
| トラック 01 | `AUDIO`、`INDEX 01 00:00:00` | VERIFIED（CUE の記述どおり） |
| トラック 03 | `AUDIO`、`INDEX 00 00:00:00`、`INDEX 01 00:02:00` | VERIFIED（CUE の記述どおり） |
| トラック 02 の BIN サイズ | 18,630,192 バイト = 2352 × 7,921 セクタ（割り切れる） | VERIFIED（ファイルサイズの算術） |
| トラック 01 の BIN サイズ | 3,311,616 バイト = 2352 × 1,408 セクタ | VERIFIED（ファイルサイズの算術） |
| トラック 02 の絶対 LBA | トラック 01 の BIN（1,408 セクタ）と連続配置の仮定から、INDEX 00 = LBA 1408、INDEX 01 = LBA 1633（1408 + 225） | HYPOTHESIS（TOC は未確認。連続配置は CUE からは示されない） |
| 起動に使うセクタ | 未確認 | UNKNOWN |
| トラック 02 が起動データを持つか | 未確認。データトラックが 1 本しかない点は、2 本目を使う経路（後述 §2 C7）を試す必要があることを示す | UNKNOWN |

注: 本リポジトリの `docs/cd-base-contract.md` が指摘するとおり、INDEX 01 が LBA 0 のディスクでは「絶対 LBA」と「INDEX 01 相対」を区別できない。このゲームのトラック 02 は INDEX 01 が自分の FILE 先頭から 225 フレーム（00:03:00）、連続配置の仮定で絶対 LBA 1633 にあり、両者を区別できる条件に当たる（225 は FILE 内の相対値で、絶対 LBA ではない）。ただし、区別の試験はゲームデータを使わずに §5 の合成メディアで行う。

---

## 2. 公開資料の主張と判定

| # | 主張 | 出典 | 確度 | 備考 |
| --- | --- | --- | --- | --- |
| C1 | HuC SDK は `cd_boot = $00:E000` を宣言する | `pce-devel/huc` `54f5c736` の `include/hucc/pcengine.inc` 467–468 行（`docs/research/cd-boot-contract.md` の台帳経由。本セッションでは再取得していない） | VERIFIED（宣言の存在のみ） | 関数の意味・戻り・入力は含まない |
| C2 | `$E000` が JMP テーブルの入口である | 二次資料 STUM（版別表）のみ | HYPOTHESIS | 一次資料で未照合 |
| C3 | 起動は「最初のデータトラック」を選ぶ（通常 Track 2） | <https://www.stum.de/2026/pc-engine-second-data-track/>（2026-05-01、本セッションで取得して照合） | HYPOTHESIS | 二次資料。BIOS のバイナリ・一次資料では未確認 |
| C4 | BIOS は各トラックに対し `cd_contnts`、`cd_dinfo` を順に呼び、データトラックを見つける | 同上 | HYPOTHESIS | 関数名は二次資料の記述 |
| C5 | IPL は見つけたデータトラックの最初の 2 セクタから読む | 同上 | HYPOTHESIS | セクタ長（2048 か 2352 か）は UNKNOWN |
| C6 | IPL の読込先アドレス、エントリ、ヘッダの各フィールド | 同上（記載なし） | UNKNOWN | 二次資料にも記載がない |
| C7 | 16 回読みに失敗すると `retry?`（`$2273`）を立て、`recbase1` を使う | 同上（System Card 3.0 のアドレス付き） | HYPOTHESIS | 二次資料のみ。`$2273` の意味は未確認 |
| C8 | `recbase0 = recbase1 = 最初のデータトラックの LBA` が自動で設定される | 同上 | HYPOTHESIS | 二次資料 |
| C9 | `necretro.org` の Boot ROM ページに起動手順の記述がある | <https://necretro.org/CD-ROM%C2%B2_systems/Boot_ROM> | UNKNOWN（**未取得**） | bot 保護（Anubis）で拒否された。本文は確認していない |
| C10 | リセット時 MPR7 = `$00`、リセットベクタは `$FFFE` | `docs/system-card-rom.md`（リポジトリの要約）、`docs/research/cd-boot-contract.md` §5 | HYPOTHESIS | 製造元マニュアルは未照合 |
| C11 | `cd_read` は slot `$03`、CPU `$E009`、24 ビットブロック番号 | `docs/cd-read-contract.md` §1（HYPOTHESIS 扱い） | HYPOTHESIS | BIOS 呼出し経路。ローダ本体の入力とは別 |
| C12 | 呼出し時の MPR・IRQ・スタックの要求 | 全資料に記載なし | UNKNOWN | — |
| C13 | 成功時の制御は戻らない | `docs/cd-exec-contract-ledger.md`（CD_EXEC について HYPOTHESIS）。ブート経路では記載なし | UNKNOWN | — |
| C14 | 開発用イメージで READ(6) LBA 0 が 2048 バイトを返す、LBA 5 のヘッダ経路が動く | `docs/cd-media-state.md`、`docs/cd-load-exec-probe.md` | VERIFIED（Geargrafx `b49ae82a`、開発用のみ） | 実ディスクの形式についての主張ではない |
| C15 | 開発用ローダの MPR 初期化（MPR0 = `$FF`、MPR1 = `$F8`）で payload が動く | `docs/cd-load-exec-probe.md` | VERIFIED（同上） | 実 BIOS の要求ではない |
| C16 | Hu-Go の HLE・Mednafen・Geargrafx のソースから得た挙動 | `docs/cd-oss-survey.md` | 参照のみ（採用しない） | GPL。振る舞いの根拠にしない（#309 方針） |

除外: x86 El Torito（検索結果の BIOS 一般論）は PC Engine と無関係のため採らない。

---

## 3. 最小ローダ契約（本リポジトリが定める、合成メディア向けの v0）

この契約は**実ゲームの起動形式ではない**。合成メディアでローダの判定ロジックと試験の枠組みを検証するためのものである。実ゲームへの適用は §6 の阻害要因が解けた後に決める。

| 項目 | v0 契約（合成メディア） | 実 SUPER CD-ROM2 の状態 |
| --- | --- | --- |
| トラック選択 | TOC（CUE）上の最初の `MODE1/2352` トラックを使う | HYPOTHESIS（C3、最初のデータトラック） |
| 読出し LBA の基準 | ヘッダ位置は「データトラック INDEX 01 からの相対セクタ番号」で表し、絶対 LBA = INDEX 01 の LBA + 相対値 | UNKNOWN（絶対 LBA か相対かは未決。`docs/cd-base-contract.md`） |
| 読出し単位・方法 | 1 セクタ（2048 バイト）を READ(6) で読む。ヘッダ 1 セクタのみ | UNKNOWN（IPL の 2 セクタ、セクタ長は未確認。C5） |
| ヘッダ | 16 バイト。magic、エントリオフセット、長さ、チェックサム、予約。形式は `docs/cd-load-exec-probe.md` の開発用形式を流用 | UNKNOWN（実形式は未確認） |
| 転送先 RAM | 論理 `$3000`（MPR1 = `$F8` の作業 RAM）。`$2800` を経由して転送してよい（開発用実績） | UNKNOWN |
| 長さ | 1 セクタ内に収まる。上限 = 2048 − ヘッダ長、かつ長さ ≥ 1 | UNKNOWN |
| エントリ | 転送先 + エントリオフセット。オフセットは長さ未満 | UNKNOWN（C2 の `$E000` は BIOS 側の入口の仮説で、IPL のエントリとは別） |
| BIOS の初期化要求 | 開発用: MPR0 = `$FF`、MPR1 = `$F8`、I フラグはセットのままポーリング、SP は BIOS が設定 | UNKNOWN（C12） |
| 戻り | 合成 payload は開発用マーカーで戻る（試験専用）。ブート経路の戻りは契約に含めない | UNKNOWN（C13） |
| sector 0 | 実行しない。データトラック以外のセクタを制御対象にしない | 仮定しない（検証対象） |

### 失敗状態（v0）

失敗時は実行せず、結果ブロックに理由を残して停止する（fail-closed）。コードは開発用ローダの既存値を流用する。

| 状態 | 条件 | 既存コード（開発用） |
| --- | --- | --- |
| `NO_TRACK` | データトラックがない、または MODE1/2352 がない | 新規 |
| `DATA_TIMEOUT` | DATA-IN 途中で中断 | 4 |
| `PREMATURE_STATUS` | 範囲外 LBA、または読出し中のステータス早期到達 | 5 |
| `BAD_MAGIC` | magic 不一致（LBA 0 を読んでも同じ） | 20 |
| `BAD_ENTRY_OFFSET` | エントリ ≥ 長さ | 21 |
| `BAD_LENGTH` | 長さ 0 または上限超過 | 22 |
| `BAD_CHECKSUM` | チェックサム不一致 | 23 |
| `BUDGET` | ステップ上限超過 | 既存 |
| `RETRY_EXHAUSTED` | 実機の再試行は UNKNOWN（C7）。v0 は再試行しない | 予約 |

---

## 4. 結論: 仕様表の状態（一覧）

| 項目 | 状態 |
| --- | --- |
| BIOS 入口 `$E000` の存在 | VERIFIED（宣言のみ） |
| BIOS 入口の意味・戻り・入力 | UNKNOWN |
| 最初のデータトラックを使う | HYPOTHESIS |
| IPL は 2 セクタ | HYPOTHESIS |
| IPL の読込先・エントリ・ヘッダ | UNKNOWN |
| 再試行 16 回と `recbase1` | HYPOTHESIS |
| 合成メディアの契約 v0 | 本書で定義（実装は未着手） |
| ゲームのトラック構成（CUE） | VERIFIED（メタデータ） |
| ゲームの起動セクタ | UNKNOWN |

---

## 5. 合成ブートメディアの設計（ゲームデータを使わない）

目的: §3 の v0 契約を、ゲームデータなしで試験する。

**生成方法**: 生成スクリプト（新規、未作成）が CUE/BIN を `build/` 以下に作る。BIN は生成物としてコミットしない。コミットするのは生成条件とダイジェストの台帳だけ。内容は乱数ではなく固定のテストパターンを使う。

**レイアウト案（2 変種）**

| 変種 | トラック構成 | 試験する点 |
| --- | --- | --- |
| V1（対照） | 単一 FILE。`TRACK 01 AUDIO`（N フレーム）、`TRACK 02 MODE1/2352` の `INDEX 01` = フレーム N（プリギャップなし）、ヘッダは INDEX 01 のセクタ | 基本の読出し・判定 |
| V2（ゲームと同形） | 単一 FILE。V1 と同じだが、データトラックに `INDEX 00` = フレーム N、`INDEX 01` = フレーム N + 225 を置く（プリギャップ 225 フレームを BIN に格納。ゲームの `INDEX 00 00:00:00` → `INDEX 01 00:03:00` の差と同じ） | 絶対 LBA と INDEX 01 相対の区別（`docs/cd-base-contract.md` の未決） |
| V2M（追加条件） | 多 FILE（トラックごとに FILE）。データトラックの FILE で `INDEX 00 00:00:00`、`INDEX 01 00:03:00`（FILE 先頭から 225 フレーム = 529,200 バイト） | 多 FILE の解釈。V1・V2 とは別に報告し、失敗・未対応を合格扱いにしない |

基準（M4/M5）は単一 FILE の V1・V2 とする。多 FILE の V2M は追加条件であり、その結果で V1・V2 の合否を置き換えない。
V2 はゲームの INDEX 構造だけを写す。ゲームのセクタ内容は含まない。
INDEX の値は FILE 先頭からのフレーム数である。単一 FILE では FILE 先頭が LBA 0 なので INDEX 値がそのまま絶対 LBA になる。多 FILE では、絶対 LBA = 先行 FILE のフレーム数の合計 + INDEX 値（連続配置の仮定。`tools/cue_analysis.py` と同じ規約）。

**セクタ内容**

- INDEX 01 のセクタ: 16 バイトのヘッダ + 固定のテストパターン（`payload` は §3 の開発用 payload を流用してよい）。
- LBA 0（トラック 01 の先頭）: 試験用のデコイ。`BAD_MAGIC` を含む内容にする。ローダが sector 0 を実行していないことを、デコイが制御されないことで示す。

**期待結果**

| ケース | 期待 |
| --- | --- |
| V1、V2 の正常ヘッダ | 読出し成功、payload 実行、マーカー一致 |
| データトラックのヘッダ magic を壊す | `BAD_MAGIC` |
| チェックサムを壊す | `BAD_CHECKSUM` |
| エントリ = 長さ | `BAD_ENTRY_OFFSET` |
| 長さ 0 | `BAD_LENGTH` |
| データトラックを除去（オーディオのみ） | `NO_TRACK` |
| LBA 0 だけにヘッダを置き、データトラックには置かない | `BAD_MAGIC`（sector 0 を実行しないことの確認） |
| DATA-IN 途中で中断 | `DATA_TIMEOUT` |
| V2 で絶対 LBA と INDEX 01 相対を取り違える実装 | 別の読出し LBA になり、判定器が `BAD_MAGIC` で拒否 |
| V2M（多 FILE、追加条件） | V1・V2 と別に報告する。判定器が多 FILE に対応していなければ未評価とし、合格にしない |

**任意の試験（既定では無効）**: データトラックの 1 本目を壊し、2 本目を使うかを見る（C7、C8 の確認）。2 本目を使うのは仮説であり、v0 の契約には入れない。

**判定器**: `docs/cd-load-exec-probe.md` と同じく、PC の順序（buffer_ready → data_done → payload_entry → payload_done → return → halt）を検査する独立の判定器。判定器は合成メディアの期待値をホスト側で計算する。

**合否の扱い**: 結果は開発用の LOCAL 証拠。VERIFIED は付けない（`docs/cd-read-contract.md` の方針どおり）。

---

## 6. 実ゲームの試験を止める阻害要因

| # | 阻害要因 | 状態 | 解く条件 |
| --- | --- | --- | --- |
| B1 | IPL の形式・読込先・エントリが一次資料で不明 | UNKNOWN | 製造元 System Card 文書、または一次資料の入手 |
| B2 | BIOS 入口 `$E000` の入力・戻り・MPR が未確認。台帳は BLOCKED_ABI | BLOCKED | 実行可能な callee と ABI の測定（#173 系の遮断が前提） |
| B3 | 参照 BIOS を使えない。参照 JSR ゲートは CLOSED | BLOCKED | ゲートの再判断（maintainer） |
| B4 | ゲームデータは公開できない。実ゲームの試験は手元だけで、結果を証拠として残せない | 制約 | 合成メディアで契約を先に固める |
| B5 | CUE が示すデータトラックは 1 本のみ。2 本目（`recbase1`）の使用は二次資料の主張で未確認 | UNKNOWN | TOC（トラック情報）を手元で確認（内容は公開しない） |
| B6 | 絶対 LBA と INDEX 01 相対が未決。ゲームは INDEX 01 が INDEX 00 の 225 フレーム後（絶対 LBA 1633 は HYPOTHESIS）のため影響する | UNKNOWN | V2 の合成メディアで先に決める |
| B7 | 測定は Geargrafx のみ。実機・別エミュレータは NOT_TESTED | 制約 | 第 2 のエミュレータ、または実機 |
| B8 | 再試行（16 回、`retry?`）の意味が未確認 | HYPOTHESIS | 合成メディアの故障注入で試験（任意試験） |
| B9 | 一次資料 `necretro.org` を取得できなかった（bot 保護） | UNKNOWN | 別経路での取得、または手動での確認 |
| B10 | 関連 Issue #145、#290、#291 が OPEN。slot 00 の契約は BLOCKED_ABI | 進行中 | 上記 B1–B3 の解消 |

---

## 7. 次の作業（提案）

1. 合成メディア生成スクリプトを作る（V1、V2、§5 の期待結果）。ゲームデータは読まない。
2. V2 の結果で B6（絶対 LBA と INDEX 01 相対）を決める。
3. B1、B2 の一次資料（System Card 文書）を入手する。入手できるまで、実ゲームの試験は行わない。
4. 必要なら maintainer が参照 JSR ゲートを再判断する（本 PR では変更しない）。

## 8. 未実施

- 合成メディア生成スクリプトの作成・実行（本 PR は仕様のみ）
- 参照 BIOS の実行（ゲート CLOSED）
- `make check`、`make test` の実行（コード変更がないため未実施）
- 一次資料の照合（C9 は未取得）
