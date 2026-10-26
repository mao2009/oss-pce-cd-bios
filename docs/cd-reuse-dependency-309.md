# CD API の OSS 再利用分類と実装依存マップ（Issue #309 補遺）

- 対象 Issue: [#309](https://github.com/mao2009/oss-pce-cd-bios/issues/309)（親: [#105](https://github.com/mao2009/oss-pce-cd-bios/issues/105)）。Refs のみ。
- 前提: [PR #422](https://github.com/mao2009/oss-pce-cd-bios/pull/422)（merged）の [cd-reuse-decision-309.md](cd-reuse-decision-309.md) が API 別の決定表（`CLEANROOM_ONLY` / `DO_NOT_USE_UNTIL_LEGAL_REVIEW` / `UNKNOWN`）を作成済み。本書は再監査せず、その判定を依頼の4分類に写し、API 間の依存と #190 の解除条件を補う。
- 基準: `origin/main` = `ec496e9`。
- 位置づけ: **調査と文書のみ**。コードのコピー・翻訳・ROM 組込みは行っていない。純正 BIOS・`/rom` は読んでいない。分類は法的助言ではなく、人間の法務レビューの材料である。
- 「今回再取得」は、本書の作成時に pinned SHA で `gh api` から取得して確認した項目だけを指す（§6）。それ以外は既存記録の引用である。

## 1. 分類語（依頼の4分類と、#422 の判定語）

| 分類 | 意味 | #422 の判定との対応 |
| --- | --- | --- |
| portable | 技術適合・ライセンス確定・ABI 確定の全てが揃い、コード採用候補になる | 該当 **0 件** |
| spec-reference only | 名称・番地・挙動など事実のみ参照し、コードは写さず独立に実装する | `CLEANROOM_ONLY` |
| rejected | コードの複写・採用を行わない（GPL・NC 条件、または技術的に不適合） | `DO_NOT_USE_UNTIL_LEGAL_REVIEW` のうち GPL・NC のもの |
| awaiting relicensing | ライセンス・権利者が確定できず、人間の法務判断（再許諾・表示の確定）待ち | `DO_NOT_USE_UNTIL_LEGAL_REVIEW` のうち権利状態が不明なもの |
| UNKNOWN（補助） | 候補はあるが契約が未確定、または有効な候補コードが無く分類できない | `UNKNOWN`（#422 の CD_EXEC、未調査の API） |

## 2. API 別の分類

| API（slot） | Issue | 分類 | 主な根拠 |
| --- | --- | --- | --- |
| CD_BOOT（`$00`） | #9、#289 | spec-reference only | callee 実装候補なし。宣言名のみ（HuC `pcengine.inc`、Boost-1.0 表示）。入口の意味は UNKNOWN、戻りは HYPOTHESIS（呼出側由来）（[cd-boot-contract](research/cd-boot-contract.md)） |
| CD_READ（`$03`） | #12、#148、#188、#189 | spec-reference only（転送層は awaiting relicensing） | 呼出側ラッパ（HuCC `hucc-systemcard.asm`）は参照のみ。SCSI 層（HuC elmer `cdrom.asm`）は派生元 Hudson の権利が不明 → awaiting relicensing |
| CD_EXEC（`$05`） | #14、#239 | UNKNOWN | 実行可能な callee 候補が無い。固定版で呼出側が見つからなかった（[ledger](cd-exec-contract-ledger.md) A1）。検索の網羅性は未検証。HuC `include/huc/cdrom.asm` の `_cd_execoverlay` はライセンス表示が無く、対象外（awaiting relicensing） |
| CD_STAT（`$09`） | #18、#229 | spec-reference only | busy/ready 判定の呼出側形のみ（HuC `scsitest.asm`、Boost-1.0）。A=0 の判定は HYPOTHESIS、他の戻り値の意味は UNKNOWN（[cd-stat-contract](research/cd-stat-contract.md)） |
| CD_BASE（`$02`） | #11 | spec-reference only | 外部候補に実装なし。座標系は UNKNOWN（[cd-base-abi-ledger](cd-base-abi-ledger.md)） |
| CD_SEEK、CD_PLAY、CD_SEARCH、CD_PAUSE、CD_SUBQ、CD_DINFO、CD_CONTNTS、CD_SUBRQ/CD_SUBRD、CD_PCMRD、CD_FADE | #13、#15〜#24 | **未分類（UNKNOWN）** | OSS 候補の調査は本 Issue で未実施。候補を #308 台帳に追加してから分類する |

### 2.1 候補ごとの分類

| 候補（ソース・pinned 版） | ライセンスの事実 | 分類 | 理由 |
| --- | --- | --- | --- |
| HuC `examples/asm/elmer/include/cdrom.asm`（`54f5c736`） | 冒頭「derived from Hudson's original fast CD code in Legend of Xanadu 2」。改変部のみ「Distributed under the Boost Software License, Version 1.0」（今回再取得で確認） | awaiting relicensing | 派生元の権利者・許諾が表示にない。Boost 表示が派生元部分に及ぶか確定できない。複写は不可 |
| HuC ルート `LICENSE`（`54f5c736`） | 「It is therefore next to impossible to make an exact statement on the legal status of much of this collection of software」（今回再取得で確認） | awaiting relicensing（HuC 全体の前提） | HuCC の Boost 表示だけでは採用根拠にならない |
| HuCC `hucc-systemcard.asm`、`pcengine.inc`、`core-kernel.asm`、`scsitest.asm`（`54f5c736`） | Boost-1.0 表示（既存記録と一致） | spec-reference only | 呼出側・宣言・試験。上記ルート LICENSE の不明瞭さが残るため複写しない |
| HuC `include/huc/cdrom.asm`（`_cd_execoverlay`、`54f5c736`） | ファイル冒頭にライセンス文言なし（既存記録、#422 の再取得） | awaiting relicensing | 表示が無いため許諾の有無を確認できない |
| Hu-Go! `pcecd.c`（`64ed226d`） | 3条項 BSD 系の本文、著作権者の行なし。`COPYING` は `LICENSE.bsd-modified` を参照するがツリーに無い（既存記録、#422 の再取得） | awaiting relicensing | 著作権者不明、参照先の全文も無い |
| Hu-Go! `bios.c`、`cd.c`（`64ed226d`） | `COPYING` により GPL-2.0 既定。`EXCEPTED` は `pcecd.c` のみ（今回再取得で確認） | rejected | ledger の ROM 方針により組込み不可（提案 RESTRICTED、人間の確定待ち）（[ledger L-10、L-11](oss-candidate-ledger.md)） |
| Hu-Go! の CD_STAT 分岐（HLE） | GPL-2.0 | rejected | ライセンスに加え、HLE の挙動は実機の証拠にならない（[cd-stat-contract](research/cd-stat-contract.md)） |
| nugget `openbios/cdrom/cdrom.c`（`c950e18a`） | MIT、PCSX-Redux authors（既存記録） | spec-reference only | ライセンスは許容的だが PSX の ABI。PCE へ移植できない |
| ares `pce/pcd/io.cpp`、`scsi.cpp`（`9408cb43`） | ルート LICENSE は ISC（既存記録。本書では再取得していない） | spec-reference only | ISC は複写を許すが、C++ エミュレータ内部で firmware に不適合。既存方針 `REFERENCE_ONLY` を維持 |
| NeoCD-Libretro `src/hlebios.cpp` 他（`b1e04c73`） | 先頭に権利表示なし。ルート LICENSE.md は LGPL-3.0（既存記録） | awaiting relicensing | ファイル単位の権利者が不明。NEO-GEO の HLE で PCE の参照価値も低い |
| STUM 2025 の番地表（二次資料） | CC BY-NC-SA 4.0（既存記録） | rejected（NC）。番地は未照合のため参照しない | NC 条件。本体番地は未照合 |
| Geargrafx（`b49ae82a`、テスト用の外部実行系） | GPL-3.0（既存記録） | rejected（コード）／外部実行のみ | ROM・リポジトリへ取り込まない。外部プロセスとして検証に使うのみ |
| MAME `pce_cd.cpp`（既存記録 BSD-3、ファイル単位） | 全体は GPL。ファイル単位のライセンスは **未検証**（commit・hash は未計算、[L-19](oss-candidate-ledger.md)） | UNKNOWN（未検証のため保留） | 検証前は分類を確定しない。参照するなら仕様の事実のみ |
| cc65 runtime `udiv.s` 等（zlib 系、#308 L-16） | zlib 系（既存記録） | 対象外（CD API ではない） | ビルド依存の話。CD API の portable 候補にはしない |

portable に分類された候補は **0 件**。permissive なライセンス（ISC、MIT、Boost）のものも、ABI 未確定または技術的な不適合により採用しない。

## 3. API 間の実装依存

矢印は「左が確定しないと右の契約が書けない」を表す。**出典あり**は引用先の文言で確認した依存、**HYPOTHESIS**は出典が無く仮説として置いたものである。

```
CD_BASE ($02, #11) ──> CD_READ ($03, #12) ──> 転送層 (#190)  [出典あり]
CD_STAT ($09, #18) ──> CD_READ                                 [出典あり: 呼出側の待ち方（F10、F23）]
CD_RESET ($01, #10) ──> CD_STAT                                 [出典あり: 呼出側は reset 後に busy を待つ（F23）]
CD_STAT ──> CD_SEEK ($04, #13)                                  [HYPOTHESIS]
CD_READ ──> CD_EXEC ($05, #14)                                  [HYPOTHESIS: 呼出側の根拠なし（A1）]
CD_READ / CD_EXEC ──> CD_BOOT ($00, #9)                         [HYPOTHESIS]
```

| 依存 | 分類 | 現状 | 根拠 |
| --- | --- | --- | --- |
| CD_READ ← CD_BASE | 出典あり | ブロック番号の基点（絶対 LBA か CD_BASE 相対か）が UNKNOWN | [188 A8](188-cd-read-transfer-mode-contract.md) |
| CD_READ ← CD_STAT | 出典あり（呼出側の事実） | 呼出側は CD_STAT の A=0 で ready へ進み、非 0 で再試行する（F10）。reset 後は busy を待つ（F23）。callee の busy/timeout の戻り値は UNKNOWN（[cd-read-contract §2 T8](cd-read-contract.md)） | [cd-stat-contract](research/cd-stat-contract.md) F10、F23 |
| CD_READ ← メディア状態 | 出典あり | 無媒体の callee 戻りが UNKNOWN（T7）。デバイス層の観測は §4 の E8 を参照 | [cd-read-contract §2](cd-read-contract.md)、[cd-media-state](cd-media-state.md) |
| CD_READ ← MPR3 | 出典あり | 呼出側が退避・復元する。callee 内の MPR 状態は UNKNOWN | [188 B3](188-cd-read-transfer-mode-contract.md) |
| CD_EXEC ← CD_READ | HYPOTHESIS | 固定版の HuC overlay loader は `cd_read` を直接呼ぶ（[ledger A3](cd-exec-contract-ledger.md)）が、CD_EXEC callee が CD_READ を使うかは UNKNOWN（呼出側なし、A1、D2） | [cd-exec ledger](cd-exec-contract-ledger.md) |
| CD_BOOT ← CD_RESET | 削除 | リセット起動と slot 呼出しは「別の経路」と記されており（[cd-boot-contract](research/cd-boot-contract.md) §1）、依存とは言えない | — |

## 4. #190（CD_READ 転送層）の解除に必要な証拠

#190 の受入条件は「必須の参照 ABI が UNKNOWN なら実装を開始せず BLOCKED」である。以下の証拠がすべて揃うまで BLOCKED_ABI を維持する。

### 4.1 前提（満たされていない安全条件）

| # | 前提 | 現状 | 根拠 |
| --- | --- | --- | --- |
| P1 | JSR gate: slot 3 の reviewed 記録と独立承認、execution が `ALLOWED` | 不成立（記録なし。`NO_REVIEWED_GATE_RECORD`、`NO_INDEPENDENT_APPROVAL`） | [188-cd-read-mode-safety](188-cd-read-mode-safety.md) G1 |
| P2 | #173 の副作用前遮断（#365 命令境界、#366 IRQ 入口、#367 ブロック転送の書込み） | 不成立。#173、#365、#366、#367 は OPEN（確認日 2026-10-26） | 同 G2、[173-safety-monitor-limits](173-safety-monitor-limits.md) |

P1 と P2 が成立するまで、E6 と E8 の実観測は行わない。

### 4.2 必要な証拠

| # | 必要な証拠 | 現状 | 出典として使えないもの |
| --- | --- | --- | --- |
| E1 | 転送先モード（`_dh = 3` の意味、RAM 宛先の単位）と 2048 バイト1セクターの対応 | UNKNOWN（[188 A1–A5](188-cd-read-transfer-mode-contract.md)） | 呼出側コメント「Use MPR3」のみ |
| E2 | count の単位と count 0 の挙動 | UNKNOWN（A4、T8） | Hu-Go! の HLE（`$22`） |
| E3 | 基点（CD_BASE 相対か絶対 LBA か） | UNKNOWN（A8、#11） | 呼出側の計算のみ |
| E4 | 成功時の A/X/Y、失敗時のエラー種別（無媒体、範囲外、busy） | UNKNOWN（B1–B2、#189） | 合成 fixture（callee 未実行） |
| E5 | 戻り時の MPR0–7、スタック深さ、I フラグ前提 | UNKNOWN（B3–B8、R12） | HuCC のコメント |
| E6 | 上記 E1–E5 を純正 callee で実行して測った結果（pinned Geargrafx、自己所有 BIOS をローカルのみで使用、バイトは非公開） | 未実施（`CALLEE_REAL_CORE_RUNS = 0`）。P1、P2 が前提 | #5 の23シナリオ（デバイス層の READ(6) 診断。callee の証拠ではない。[#190 のコメント（2026-10-21）](https://github.com/mao2009/oss-pce-cd-bios/issues/190)） |
| E7 | 転送ルーチンの出自の確認：独立に実装し、HuC elmer `cdrom.asm` の文言・構造を複写していないこと | 未実施。elmer は awaiting relicensing | 派生元を複写したコード |
| E8a | 範囲外 LBA（LBA 16 以上）の安全な失敗 | callee 側は UNKNOWN（T3、#189）。デバイス層の STATUS のみ観測（[188](188-cd-read-transfer-mode-contract.md)） | — |
| E8b | 無媒体の callee 戻り | UNKNOWN（T7、#189）。デバイス層では `NO_DATA` を観測済み（後述） | — |
| E9 | タイムアウトと再試行の上限（有界性） | UNKNOWN（T8。#190 の完了条件「タイムアウト上限の負例」に対応） | 上限の無い待ち（デバイス層の反例を除く） |

E8b について、[cd-media-state](cd-media-state.md) の対照実験（Geargrafx `b49ae82a`、媒体なし）は、デバイス層で `NO_DATA`・0 バイトを観測している。一方、[cd-read-contract](cd-read-contract.md) T7 は「Geargrafx does not run without media」と記す。両者は矛盾しており、本書は解決しない。デバイス層の観測は callee の戻りの証拠にはならない。

### 4.3 解除の判定

- 最低限の解除条件は、P1・P2 が成立したうえで **E1 から E6、E9 が admissible な証跡で PASS** し、E7 が人間に確認されること。E8a と E8b は無媒体・範囲外の経路を扱う場合に必須である。
- 「admissible」の定義は [cd-read-contract](cd-read-contract.md) §3 に従う。独立に裏付けられた System Card 3.0 の挙動記録が必要で、現行の規則では VERIFIED を返す関数は無い（同 §0、§4）。
- したがって、E1 から E6 の PASS には **ゲートまたは判定規則の変更を、人間が決める必要がある**。エージェントは規則を変更しない。
- #148 の呼出側台帳（merged）と #188、#189 の契約文書（merged）は依存として揃っている。ただし #188 と #189 の Issue は OPEN であり、#190 の本文が求める ABI の確定は満たしていない。契約文書は callee の ABI 証拠ではない。

## 5. 次の作業（提案）

1. #190 は BLOCKED_ABI のまま。P1、P2 の解消と E1 から E9 の観測計画を、別 Issue で人間が承認してから進める。本 PR では行わない。
2. CD_EXEC と CD_BOOT は callee 候補の探索を先に行う。候補が見つからない限り、UNKNOWN または spec-reference only のまま。
3. HuC elmer `cdrom.asm` の派生元権利を、人間の法務レビューに回す（§2.1）。
4. 未分類の CD API（§2 の下段）は、#308 台帳に候補を追加してから分類する。

## 6. 検証

| 区分 | 結果 | 理由 |
| --- | --- | --- |
| 今回再取得（pinned SHA、`gh api`） | PASS | HuC `LICENSE` の冒頭（「much of this collection」の全文）、elmer `cdrom.asm` 冒頭 8・14・16 行、Hu-Go! `COPYING` の `EXCEPTED` 文言 |
| 既存記録の引用（pcecd.c、nugget、NeoCD、ares、MAME、STUM） | NOT_RUN（再取得なし） | #308 台帳・#422 の記録を引用。本書では再取得していない |
| Issue の状態（#173、#188、#189、#365〜#367） | PASS | 2026-10-26 に `gh issue view` で確認。すべて OPEN |
| 依存マップの矢印 | 出典あり／HYPOTHESIS を §3 に明記 | 出典ありの矢印のみ引用先の文言と照合 |
| 相対リンク解決（外部 URL を除く） | PASS | 2026-10-26 に grep と存在確認で実施 |
| `git diff --check` | PASS | docs のみ |
| 実コア・純正 BIOS | NOT_RUN | 調査のみ |

## 7. 範囲と限界

- コード・バイトの複写、disassembly の掲載はしていない。
- 分類は 2026-10-26 時点の既存記録に基づく。
- 法的助言ではない。権利の最終判定は人間が行う。
