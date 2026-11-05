# 優先 CD API 別の OSS 再利用決定表（Issue #309）

- 対象 Issue: [#309](https://github.com/mao2009/oss-pce-cd-bios/issues/309)（親: [#105](https://github.com/mao2009/oss-pce-cd-bios/issues/105)）。Refs のみ。
- 基準: `origin/main` = `1cd5582`（2026-10-21 にクローンした時点）。
- 位置づけ: **決定表（調査のみ）**。コードのコピー・翻訳・ROM への取り込みは行わない。本書の判定は採用承認ではなく、すべて人間の法務レビュー待ち。
- 既存監査の利用: #105 の監査記録（[r3](oss-reuse-audit-r3.md)、[r4](oss-reuse-audit-r4.md)）、[cd-oss-survey](cd-oss-survey.md)、#308 の[台帳](oss-candidate-ledger.md)を引用する。本書では全件を再監査しない。
- 今回の再確認: 下表の「今回確認」は、2026-10-21 に `gh api`（pinned SHA 指定、raw 取得）で取得したファイル冒頭とルート LICENSE の文言である。コード本文は転載していない。
- 対象外: `/rom` と純正 BIOS は一切読んでいない。リファレンス BIOS は実行していない。

## 1. 判定語

| 判定 | 意味 |
| --- | --- |
| `REUSE_OK` | 技術適合とライセンス確定の両方を、ソース表示で確認できる。**本表では該当なし**。 |
| `CLEANROOM_ONLY` | コードは写さない。呼出側の事実、挙動、公開仕様だけを参照し、独立に実装する。 |
| `DO_NOT_USE_UNTIL_LEGAL_REVIEW` | ライセンスまたは権利状態を確定できない、またはコピーが本プロジェクトの方針と両立するか確認できない。人間の法務レビューまで採用・翻訳・コピーを行わない。 |
| `UNKNOWN` | 候補はあるが契約が未確定、または有効な候補コードが無い。 |

`REUSE_OK` にしない理由: pce-devel/huc の HuCC 系ファイルは Boost-1.0 の表示を持つが、同リポジトリのルート LICENSE は、多くの作者が利用条件を明示していないと述べ、英語原文で "It is therefore next to impossible to make an exact statement on the legal status of much of this collection of software"（訳: 本コレクションの多くの部分について法的地位を正確に述べることはほぼ不可能）と記す（今回確認、7–10 行）。ファイル表示だけでは確定できない。

## 2. API 別の総合判定

| API（slot / 候補エントリ） | 既存 Issue | 総合判定 | 根拠の要約 |
| --- | --- | --- | --- |
| `CD_BOOT`（`$00` / `$E000`） | [#9](https://github.com/mao2009/oss-pce-cd-bios/issues/9)、[#289](https://github.com/mao2009/oss-pce-cd-bios/issues/289) | `CLEANROOM_ONLY` | callee の実装候補が無い。呼出側の事実（MPR7 の扱い）のみ参照可。callee の ABI は全項目 UNKNOWN。 |
| `CD_READ`（`$03` / `$E009`） | [#12](https://github.com/mao2009/oss-pce-cd-bios/issues/12) | `CLEANROOM_ONLY` | 直接適合する SCSI 層（HuC `cdrom.asm`）は `DO_NOT_USE_UNTIL_LEGAL_REVIEW`。残りは呼出側と挙動の参照のみ。 |
| `CD_EXEC`（`$05` / `$E00F`） | [#14](https://github.com/mao2009/oss-pce-cd-bios/issues/14)、[#239](https://github.com/mao2009/oss-pce-cd-bios/issues/239) | `UNKNOWN` | 固定版で呼出側が見つからなかった（[ledger A1](cd-exec-contract-ledger.md) の grep 結果。検索の網羅性は未検証）。定義のみで、実装候補のコードが無い。 |
| `CD_STAT`（`$09` / `$E01B`） | [#18](https://github.com/mao2009/oss-pce-cd-bios/issues/18)、[#229](https://github.com/mao2009/oss-pce-cd-bios/issues/229) | `CLEANROOM_ONLY` | 呼出側の busy/ready 判定の形のみ参照可。戻り値の意味（mode と A）は UNKNOWN。 |

## 3. 候補別の決定表

列の意味: 候補（ソース・URL・版）／ライセンス（出典）／コピー再利用／仕様のみ参照（clean-room）／再ライセンス・許諾の要否／欠落している API 契約／実装依存（他 API・RAM・MPR・スタック・メディア）／判定。
「今回確認」は §2 の再取得結果、それ以外は既存記録の引用である。

### 3.1 CD_BOOT（`$E000`）

| 候補・ソース（版） | ライセンス（出典） | コピー再利用 | 仕様のみ参照 | 再ライセンス・許諾 | 欠落している契約 | 実装依存 | 判定 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| HuC `include/hucc/pcengine.inc`（[pce-devel/huc @ `54f5c736`](https://github.com/pce-devel/huc/blob/54f5c73606c1e7142e8095c727cab2767e2b3cb3/include/hucc/pcengine.inc)、467–468 行の `cd_boot = $00:E000` 宣言） | 8–10 行「Distributed under the Boost Software License, Version 1.0.」（今回確認） | 不可（宣言 1 行。写す対象が無い） | 可: 名称と番地の宣言という事実のみ | 表示保持。LICENSE_1_0.txt の同梱方法は人間判断（`include/hucc/` に無い） | 入口の意味、入力、戻り、MPR、スタック、IRQ（すべて UNKNOWN。撤去済み文書 §4（撤去済み文書）） | なし（宣言のみ） | `CLEANROOM_ONLY` |
| HuCC `include/hucc/core-kernel.asm` 478–516 行（呼出側 `call_bios`、同 `54f5c736`） | 8–10 行 Copyright John Brandwood 2021–2024、Boost-1.0 の表示（今回確認） | 不可: 呼出側であり callee ではない。ルート LICENSE の不明瞭さにより、コピーは確定できない | 可: 呼出し前に `tma7`/`tam7` で MPR7 を bank `$00` にし、戻り後に復元する（呼出側の SOURCE_FACT） | 同上 | callee の MPR0–6 要求、戻り規約、IRQ 状態 | MPR7、SP（呼出側のみ確認）、`sei` は CD ソフトリセット経路のみ | `CLEANROOM_ONLY` |
| Hu-Go! `bios.c`（[mckayemu/hugo @ `64ed226d`](https://github.com/mckayemu/hugo/blob/64ed226da2288738781743eeef228c069e8973a4/bios.c)） | `COPYING` 1–2 行「GNU GENERAL PUBLIC LICENSE Version 2」（今回確認）。ファイル内に著作権表示なし | 不可（GPL-2.0 が既定。コピーする場合の条件と、本プロジェクトの MIT 方針との両立は未確認。[ledger L-10](oss-candidate-ledger.md)） | 不可（HLE の挙動は実機の証拠にならない。[cd-stat §F26](research/cd-stat-contract.md) と同じ扱い） | GPL-2.0 の条件と ROM 配布方針の両立は未確認。人間判断 | — | — | `DO_NOT_USE_UNTIL_LEGAL_REVIEW` |
| STUM 2025 offsets（二次資料、撤去済み文書 §2） | CC BY-NC-SA 4.0 の本文（既存記録。今回未再読） | 不可（NC 条件） | 不可（本体番地は HYPOTHESIS のみ） | 非商用条件の扱いを人間判断 | 本体番地（未照合） | — | `DO_NOT_USE_UNTIL_LEGAL_REVIEW` |

### 3.2 CD_READ（`$E009`）

| 候補・ソース（版） | ライセンス（出典） | コピー再利用 | 仕様のみ参照 | 再ライセンス・許諾 | 欠落している契約 | 実装依存 | 判定 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| HuCC `include/hucc/hucc-systemcard.asm`（`cd_loaddata` / `cd_loadbank`、[54f5c736](https://github.com/pce-devel/huc/blob/54f5c73606c1e7142e8095c727cab2767e2b3cb3/include/hucc/hucc-systemcard.asm)） | 8–12 行 Copyright John Brandwood 2024、Boost-1.0（今回確認） | 不可: 呼出側ラッパ。callee ではない | 可: `_dh=0` でバイト指定、戻り X≠0 がエラー、成功時のみ `_add_sectors`（[cd-read-contract](cd-read-contract.md)、r3 §2） | 表示保持。ルート LICENSE の扱いは §1 と同じ | CD_READ 本体の戻りとエラー種別。MPR3 退避後の状態は HuCC コメントの記載のみで未実測 | MPR3、8KB 単位の分割、メディア（データ読込の前提） | `CLEANROOM_ONLY` |
| HuC `examples/asm/elmer/include/cdrom.asm`（[54f5c736](https://github.com/pce-devel/huc/blob/54f5c73606c1e7142e8095c727cab2767e2b3cb3/examples/asm/elmer/include/cdrom.asm)、SCSI READ(6) 層） | 8 行「This is derived from Hudson's original fast CD code in Legend of Xanadu 2,」、14 行「Modifications copyright John Brandwood 2022.」、16 行「Distributed under the Boost Software License, Version 1.0.」（今回確認）。派生元の権利者と許諾は表示なし。Boost の表示が派生元の部分にも及ぶかは、表示文言からは確定できない | 不可（ベースの権利状態が確定できない） | 条件付き可: CDB の形式、REQ/ACK、phase 値は [cd-oss-survey](cd-oss-survey.md) の register facts と独立の SCSI 仕様・実コア観測で再導出する。写しは不可 | 人間法務: Hudson 由来部分の権利。確認できなければ採用不可 | READ のリトライ上限（r3 §2: 試行上限が上位に見えない）、count 0 の意味 | `$1800`–`$1808`、ACK、ROM 側のバッファ、IRQ（現行の参照は polling のみ） | `DO_NOT_USE_UNTIL_LEGAL_REVIEW` |
| Hu-Go! `pcecd.c`（[mckayemu/hugo @ `64ed226d`](https://github.com/mckayemu/hugo/blob/64ed226da2288738781743eeef228c069e8973a4/pcecd.c)） | 1–9 行は 3 条項 BSD 系の本文で、著作権者の行なし（今回確認）。`COPYING` 6–7 行は「modified BSD（see LICENSE.bsd-modified）」と参照するが、そのファイルは pin に無い（[ledger L-12](oss-candidate-ledger.md)） | 不可（著作権者が不明。参照先の許諾本文も無い） | 可: セクタ転送の状態遷移の挙動参照（コードは写さない） | 著作権者の特定、`LICENSE.bsd-modified` 全文の確定 | READ のタイムアウト（grep 上は無し） | ホスト側のセクタ経路（Hu-Go の内部 API） | `DO_NOT_USE_UNTIL_LEGAL_REVIEW` |
| ares-emulator/ares `ares/pce/pcd/io.cpp`、`scsi.cpp`（[9408cb43](https://github.com/ares-emulator/ares/blob/9408cb43d4948fc3ea6e152a307a34348df3fe04/ares/pce/pcd/io.cpp)） | ルート LICENSE（今回確認）: ISC、「Copyright (c) 2004-2025 ares team, Near et al」、「Permission to use, copy, modify, and/or distribute this software for any purpose…」。io.cpp 自体は表示なし | 法的には ISC で写せる（表示保持が条件）。ただし C++ エミュレータ内部で firmware に不適合。方針 `REFERENCE_ONLY` を維持 | 可: `$1800` の bit、ACK（`$1802` bit 7）、リセット（`$1804` bit 1）、auto-ACK（[cd-oss-survey](cd-oss-survey.md) の S2） | 不要（ISC の表示保持のみ） | READ 本体の戻り（ares は controller 層のみ） | レジスタ、SCSI 層 | `CLEANROOM_ONLY` |
| PCSX-Redux nugget `openbios/cdrom/cdrom.c`（[c950e18a](https://github.com/pcsx-redux/nugget/blob/c950e18a168944ec2d4e6d3c408fc224317483a7/openbios/cdrom/cdrom.c)） | 1–5 行 MIT、「Copyright (c) 2020 PCSX-Redux authors」（今回確認） | 不可（PSX ABI。技術的に流用できない） | 可: 有界待ちの設計パターン（statemachine の `wait = 30000`、r3 §2）。反例: IRQ の無限待ち（r4） | 写す場合のみ MIT の表示保持 | — | PSX の CDROM レジスタ | `CLEANROOM_ONLY` |
| NeoCD-Libretro `src/hlebios.cpp` 他（[b1e04c73](https://github.com/libretro/neocd_libretro/blob/b1e04c738cb48a1dae0574b8877f6a116d270ca1/src/hlebios.cpp)） | 先頭に権利表示なし（今回確認）。ルート `LICENSE.md` は LGPL-3.0（r4 記録。今回未再読） | 不可（ファイル単位の権利者が UNKNOWN。LGPL の組込み条件も未確定） | 限定的: NEO-GEO CD の HLE であり PCE の挙動の参照価値は低い | 人間法務 | — | 68000 / NEO-GEO の ABI | `DO_NOT_USE_UNTIL_LEGAL_REVIEW` |

### 3.3 CD_EXEC（`$E00F`）

| 候補・ソース（版） | ライセンス（出典） | コピー再利用 | 仕様のみ参照 | 再ライセンス・許諾 | 欠落している契約 | 実装依存 | 判定 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| HuC `include/hucc/pcengine.inc`（`cd_exec = $00:E00F`、[54f5c736](https://github.com/pce-devel/huc/blob/54f5c73606c1e7142e8095c727cab2767e2b3cb3/include/hucc/pcengine.inc)） | Boost-1.0（§3.1 と同じ表示、今回確認） | 不可（宣言のみ） | 可: 名称と番地の宣言のみ | 表示保持 | 入力、出力、成功時の非復帰（HYPOTHESIS）、MPR、SP、IRQ、失敗時の戻り（すべて UNKNOWN。[cd-exec ledger](cd-exec-contract-ledger.md) A1、B1–D2） | 未確定 | `UNKNOWN` |
| HuC `include/huc/cdrom.asm` の `_cd_execoverlay`（[pce-devel/huc @ `54f5c736`](https://github.com/pce-devel/huc/blob/54f5c73606c1e7142e8095c727cab2767e2b3cb3/include/huc/cdrom.asm)、312–329 行。失敗時の戻りは [ledger B2](cd-exec-contract-ledger.md) の参照）。elmer の `examples/asm/elmer/include/cdrom.asm` とは別ファイル | ファイル冒頭（625 行）に著作権・ライセンス文言が無い（今回確認）。ライセンス未確認 | 不可（ライセンス未確認） | 不可: SDK の別関数であり、CD_EXEC の証拠にならない（ledger B2） | ライセンス表示が無いため、許諾の有無を人間が確認する | 同上 | 同上 | `DO_NOT_USE_UNTIL_LEGAL_REVIEW` |

### 3.4 CD_STAT（`$E01B`）

| 候補・ソース（版） | ライセンス（出典） | コピー再利用 | 仕様のみ参照 | 再ライセンス・許諾 | 欠落している契約 | 実装依存 | 判定 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| HuC `include/hucc/pcengine.inc`（477 行 `cd_stat = $00:E01B`、329–343 行の作業 RAM 定義、[54f5c736](https://github.com/pce-devel/huc/blob/54f5c73606c1e7142e8095c727cab2767e2b3cb3/include/hucc/pcengine.inc)） | Boost-1.0（今回確認） | 不可（宣言と作業域の定義のみ） | 可: 名称、番地、作業域の名前（[cd-stat §2 S1](research/cd-stat-contract.md)） | 表示保持 | mode と戻り A の意味（F04、F11、F12）、作業 RAM の副作用（F20）は UNKNOWN | 作業 RAM `_ax` 〜 `_dh`（定義のみ。書換えの有無は UNKNOWN） | `CLEANROOM_ONLY` |
| HuC `examples/asm/elmer/cd-core-scsitest/scsitest.asm`（681–695 行の busy/ready 判定、[54f5c736](https://github.com/pce-devel/huc/blob/54f5c73606c1e7142e8095c727cab2767e2b3cb3/examples/asm/elmer/cd-core-scsitest/scsitest.asm)） | 10–13 行 Copyright John Brandwood 2024–2025、Boost-1.0（今回確認） | 不可（試験プログラム。1501–1512 行の `test_stat` は `$E01B` を呼ばないため根拠にしない） | 可: 呼出側が戻り A を `cmp #$00` で busy/ready 判定する形（SOURCE_FACT、呼出側のみ） | 表示保持 | 戻り値のエンコーディング（UNKNOWN） | `CD_STAT` の busy 中の動作（F24） | `CLEANROOM_ONLY` |
| Hu-Go! `bios.c` の `CD_STAT` 分岐（[64ed226d](https://github.com/mckayemu/hugo/blob/64ed226da2288738781743eeef228c069e8973a4/bios.c)） | GPL-2.0（`COPYING`、今回確認） | 不可（GPL-2.0） | 不可（A=0 の HLE は証拠にならない。F26） | GPL 条件と ROM 配布方針の両立は未確認 | — | — | `DO_NOT_USE_UNTIL_LEGAL_REVIEW` |

## 4. 法務レビューが必要な項目（人間）

1. **pce-devel/huc の LICENSE**: 冒頭が、多くの作者が利用条件を明示していないと述べる。HuCC（`hucc-systemcard.asm`、`pcengine.inc` など）の Boost-1.0 表示を、採用可否の根拠にできるかの判断。
2. **LICENSE_1_0.txt の同梱**: HuCC の表示が指す本文は `include/hucc/` に無く、`examples/asm/elmer/` にのみある（[r4](oss-reuse-audit-r4.md)）。再配布時の NOTICE の扱い。
3. **elmer `cdrom.asm` のベース**: Hudson 由来の権利状態。確認できなければ `DO_NOT_USE_UNTIL_LEGAL_REVIEW` を維持する。
4. **Hu-Go! `pcecd.c`**: 著作権者の特定と、参照される `LICENSE.bsd-modified` の全文。
5. **Hu-Go! `bios.c`、`cd.c`**: GPL-2.0 のため採用対象外とする確定（[ledger L-10、L-11](oss-candidate-ledger.md)）。
6. **NeoCD-Libretro**: ファイル単位の権利者と、LGPL-3.0 の組込み条件。
7. **STUM 2025**: CC BY-NC-SA 4.0 の二次資料を、番地の照合にも使うかどうか。
8. **ares（ISC）**: 法的には写せるが、方針どおり参照のみとするかの確認。

## 5. 次に実装する CD API（推奨）

**推奨: `CD_STAT`（slot `$09`、`$E01B`）の DEV 専用の契約確認から始める。**

この推奨は、**戻り値の契約（mode と A の意味、F04・F11・F12）を先に実測で確定することを条件とする。** 確定するまで実装には進まない。

理由:
- 他の優先 API より依存が少ないと見込まれる。ディスク読込を伴わず、戻り値と副作用の観測が中心になる。ただし MPR と IRQ は未確定である。[cd-stat §6](research/cd-stat-contract.md) で未決のまま残っている項目は次のとおり。F19（呼出し中の MPR0–6）、F22（IRQ 状態と IRQ2 の発生有無）、F20（作業 RAM の副作用。BLOCKED_ABI）。したがって「MPR の切替や IRQ 設定が不要」とは言えない。
- 参照する事実は、呼出側の記述（`scsitest.asm` の busy/ready 判定）と名称の宣言に限られる。今回確認した表示の範囲では、コードの写しは必要ない。ただし判定は人間が行う。
- 検証経路が既にある。固定版 Geargrafx（`b49ae82a`）でのゲスト試験は、CD ドライブの STATUS と phase の観測に使える（[cd-oss-survey](cd-oss-survey.md) の T 行）。

ブロッカー: 戻り値の意味（mode と A）は UNKNOWN。実装の前に、無ディスク、busy 中、不正 mode の各ケースを人間が承認した上で実測し、契約台帳を確定させる。System Card ABI が未確定のため、`release_eligible=false` とし、BLOCKED_ABI を解除することは本 Issue の範囲外である。

見送り: `CD_BOOT`（callee の ABI が全項目 UNKNOWN で規模が大きい）、`CD_READ`（直接適合する SCSI 層が再許諾待ち）、`CD_EXEC`（固定版で呼出側が見つからなかったが、検索の網羅性は未検証）。

## 6. 範囲と限界

- コードのコピー、翻訳、ROM への取り込みは行っていない。`/rom` は読んでいない。リファレンス BIOS は実行していない。本体の逆アセンブリは掲載していない。
- 判定は法的助言ではない。SPDX や GitHub 判定は参考に留め、ファイルの表示文言と LICENSE 本文に基づく。
- 既存記録の値（r3、r4、cd-oss-survey、#308 台帳）は、今回再計測していない項目を含む。
- 「実装依存」の RAM 番地は、固定版の定義行の引用であり、実機での確認はしていない。
- 既存 API issue（#9、#12、#14、#18、#105）への参照コメントは未投稿。オーナーがコメントを承認するまで保留する。

## 7. 検証

### 7.1 根拠ごとの記録

| 根拠 | 版（revision）と行 | 結果 | 理由 |
| --- | --- | --- | --- |
| HuC `pcengine.inc` の Boost 表示と `cd_boot` / `cd_exec` / `cd_stat` の宣言 | `54f5c736`、8–10 行、467–477 行 | PASS | 2026-10-21 に `gh api` で取得して確認 |
| HuCC `core-kernel.asm`（`call_bios`）、`hucc-systemcard.asm` の表示 | `54f5c736`、8–12 行、478–516 行 | PASS | 同上。コードの本文は引用していない |
| elmer `cdrom.asm` の派生元表示 | `54f5c736`、8–16 行 | PASS | 同上。派生元の権利状態は確認できず（判定は DO_NOT_USE） |
| elmer `scsitest.asm` の表示と busy/ready 判定 | `54f5c736`、10–13 行、681–695 行 | PASS（表示）、NOT_RUN（判定の動作） | 表示は取得して確認。試験プログラムは実行していない |
| HuC ルート LICENSE の冒頭（権利状態の不明瞭さ） | `54f5c736`、1–30 行 | PASS | 同上 |
| Hu-Go! `COPYING`、`pcecd.c` 冒頭、`bios.c` 冒頭 | `64ed226d` | PASS | 同上。`LICENSE.bsd-modified` は pin に無い（[ledger L-12](oss-candidate-ledger.md)） |
| nugget `cdrom.c` の MIT 表示 | `c950e18a`、1–5 行 | PASS | 同上 |
| NeoCD `hlebios.cpp` の表示なし | `b1e04c73` | PASS | 同上。LGPL-3.0 はファイル単位の確認なし（既存記録） |
| ares ルート LICENSE（ISC）、`io.cpp` | `9408cb43` | PASS（LICENSE）、PASS（io.cpp の表示なし） | 同上 |
| CD_BOOT の本体番地、CD_READ の戻り規約、CD_EXEC の動作、CD_STAT の戻り値 | — | UNKNOWN / BLOCKED_ABI | 実行可能な callee が無いため測定できない（各 contract 台帳） |
| STUM 2025 の番地、Zeograd | — | NOT_RUN | 二次資料。今回未再読。台帳の根拠に使わない |

### 7.2 その他の検証

| 区分 | 結果 | 理由 |
| --- | --- | --- |
| 既存記録の再計測（r3、r4、cd-oss-survey、#308 台帳の本文と API 判定） | NOT_RUN | 引用のみ。再計測していない |
| 差分の確認（`git diff --check`） | PASS | docs のみ |
| `make check`（`tools/contracts.py`、`tools/check.py`） | 結果を §7.3 に記載 | 変更は docs のみ |
| 実コア（Geargrafx）での検証 | NOT_RUN | 本書は実装の検証ではない |
| 参照 BIOS の実行、`/rom` の読込 | NOT_RUN（実施していない） | 制約による |

### 7.3 make check の結果

- `TOOLS_DIR=<HOME>/oss-pce-cd-bios-tools`（`tools/setup.py actionlint` で v1.7.7 を導入）で `make check` を実行し、終了コード 0 を確認した。
- `tools/contracts.py`: PASS（81 件の候補契約が整合。BIOS 検証は NOT_RUN）。
- `tools/check.py`: PASS（Actions の schema、構文、whitespace を含む）。
- 最初の実行は、`/tmp` の既定の TOOLS_DIR に actionlint が無いため失敗した。ツール導入の問題であり、コードの問題ではない。
