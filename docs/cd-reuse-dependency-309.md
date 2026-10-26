# CD API の OSS 再利用分類と実装依存マップ（Issue #309 補遺）

- 対象 Issue: [#309](https://github.com/mao2009/oss-pce-cd-bios/issues/309)（親: [#105](https://github.com/mao2009/oss-pce-cd-bios/issues/105)）。Refs のみ。
- 前提: [PR #422](https://github.com/mao2009/oss-pce-cd-bios/pull/422)（merged）が [cd-reuse-decision-309.md](cd-reuse-decision-309.md) で API 別の決定表（`CLEANROOM_ONLY` / `DO_NOT_USE_UNTIL_LEGAL_REVIEW` / `UNKNOWN`）を作成済み。本書は再監査せず、その判定を依頼の4分類に写し、API 間の依存と #190 の解除条件を補う。
- 基準: `origin/main` = `ec496e9`。作業ブランチ `wave26/w12-309`。
- 位置づけ: **調査と文書のみ**。コードのコピー・翻訳・ROM 組込みは行っていない。純正 BIOS・`/rom` は読んでいない。分類は法的助言ではなく、人間の法務レビューの材料である。

## 1. 分類語（依頼の4分類との対応）

| 分類 | 意味 | #422 の判定との対応 |
| --- | --- | --- |
| portable | 技術適合・ライセンス確定・ABI 確定の全てが揃い、コード採用候補になる | 該当 **0 件**。CD 系 API の callee 実装は、どの候補にも存在しない |
| spec-reference only | 名称・番地・挙動など事実のみ参照し、コードは写さず独立に実装する | `CLEANROOM_ONLY` |
| rejected | コードの写し・採用を行わない（ライセンス衝突、または技術的に不適合） | `DO_NOT_USE_UNTIL_LEGAL_REVIEW` のうち GPL・NC 条件のもの |
| awaiting relicensing | ライセンス・権利者が確定できず、人間の法務判断（再許諾・表示の確定）待ち | `DO_NOT_USE_UNTIL_LEGAL_REVIEW` のうち権利状態が不明なもの |

## 2. API 別の分類

| API（slot） | Issue | 分類 | 主な根拠 |
| --- | --- | --- | --- |
| CD_BOOT（`$00`） | #9、#289 | spec-reference only | callee 実装候補なし。宣言名のみ（HuC `pcengine.inc`、Boost-1.0 表示）。ABI は全項目 UNKNOWN（[cd-boot-contract](research/cd-boot-contract.md)） |
| CD_READ（`$03`） | #12、#148、#188、#189 | spec-reference only（転送層は awaiting relicensing） | 呼出側ラッパ（HuCC `hucc-systemcard.asm`）は参照のみ。SCSI 層（HuC elmer `cdrom.asm`）は派生元 Hudson の権利が不明 → awaiting relicensing |
| CD_EXEC（`$05`） | #14、#239 | awaiting relicensing（宣言は spec-reference） | HuC `include/huc/cdrom.asm` の `_cd_execoverlay` はライセンス表示なし。呼出側も固定版で未発見（[ledger](cd-exec-contract-ledger.md)） |
| CD_STAT（`$09`） | #18、#229 | spec-reference only | busy/ready 判定の呼出側形のみ（HuC `scsitest.asm`、Boost-1.0）。戻り値の意味は UNKNOWN |
| CD_BASE（`$02`） | #11 | spec-reference only | 外部候補に実装なし。座標系は UNKNOWN（[cd-base-abi-ledger](cd-base-abi-ledger.md)） |
| CD_SEEK / CD_PLAY / CD_SEARCH / CD_PAUSE / CD_SUBQ / CD_DINFO / CD_CONTNTS / CD_SUBRQ / CD_PCMRD / CD_FADE | #13、#15〜#24 | spec-reference only | 個別の OSS 候補調査は未実施（既存記録の範囲外）。実装前に各 API の候補を #308 台帳に追加してから判定する |

### 2.1 候補ごとの分類

| 候補（ソース・pinned 版） | ライセンスの事実 | 分類 | 理由 |
| --- | --- | --- | --- |
| HuC `examples/asm/elmer/include/cdrom.asm`（`54f5c736`） | 冒頭「derived from Hudson's original fast CD code」、改変部のみ「Boost Software License, Version 1.0」（今回再取得で確認） | awaiting relicensing | 派生元の権利者・許諾が表示にない。Boost 表示が派生元部分に及ぶか確定できない。写しは不可 |
| HuC ルート `LICENSE`（`54f5c736`） | 「next to impossible to make an exact statement on the legal status」と明記（今回再取得で確認） | awaiting relicensing（HuC 全体の前提） | HuCC の Boost 表示だけでは採用根拠にならない |
| HuCC `hucc-systemcard.asm`、`pcengine.inc`、`core-kernel.asm`、`scsitest.asm`（`54f5c736`） | Boost-1.0 表示（既存記録と一致） | spec-reference only | 呼出側・宣言・試験。上記ルート LICENSE の不明瞭さが残るため写さない |
| HuC `include/huc/cdrom.asm`（`_cd_execoverlay`、`54f5c736`） | ファイル冒頭にライセンス文言なし | awaiting relicensing | 表示が無いため許諾の有無を確認できない |
| Hu-Go! `pcecd.c`（`64ed226d`） | 3条項 BSD 系の本文、著作権者の行なし。`COPYING` は `LICENSE.bsd-modified` を参照するがツリーに無い（今回再取得で確認） | awaiting relicensing | 著作権者不明、参照先の全文も無い |
| Hu-Go! `bios.c`、`cd.c`（`64ed226d`） | `COPYING` により GPL-2.0 既定（今回再取得で `EXCEPTED` は `pcecd.c` のみ） | rejected | GPL-2.0 と MIT の ROM 方針は両立しない（[ledger L-10、L-11](oss-candidate-ledger.md)） |
| Hu-Go! の CD_STAT 分岐（HLE） | GPL-2.0 | rejected | ライセンスに加え、HLE の挙動は実機の証拠にならない（[cd-stat-contract](research/cd-stat-contract.md)） |
| nugget `openbios/cdrom/cdrom.c`（`c950e18a`） | MIT、PCSX-Redux authors | spec-reference only | ライセンスは許容だが PSX の ABI。PCE 用に移植できない。写す場合は MIT 表示が必要 |
| ares `pce/pcd/io.cpp`、`scsi.cpp`（`9408cb43`） | ルート LICENSE は ISC（今回再取得で確認） | spec-reference only | ISC は写せるが、C++ エミュレータ内部で firmware に不適合。既存方針 `REFERENCE_ONLY` を維持 |
| NeoCD-Libretro `src/hlebios.cpp` 他（`b1e04c73`） | 先頭に権利表示なし。ルート LICENSE.md は LGPL-3.0（既存記録） | awaiting relicensing | ファイル単位の権利者が不明。NEO-GEO の HLE で PCE の参照価値も低い |
| STUM 2025 の番地表（二次資料） | CC BY-NC-SA 4.0（既存記録） | rejected（番地を HYPOTHESIS として参照するのみ） | NC 条件。本体番地は未照合 |
| Geargrafx（`b49ae82a`、テスト用の外部実行系） | GPL-3.0（既存記録） | rejected（コード） / tooling のみ | ROM・リポジトリへ取り込まない。外部プロセスとして検証に使うのみ |
| MAME `pce_cd.cpp`（BSD-3、ファイル単位・既存記録） | 全体は GPL（既存記録） | spec-reference only | ファイル単位の BSD-3 は許容的だが、C++ 実装で ABI 不適合。版と hash は未計算 |
| cc65 runtime `udiv.s` 等（zlib 系、#308 L-16） | zlib 系（既存記録） | 対象外（CD API ではない） | ビルド依存の話。CD API の portable 候補にはしない |

portable に分類された候補は **0 件**。permissive なライセンス（ISC、MIT、Boost）のものも、ABI 未確定または技術的な不適合により採用しない。

## 3. API 間の実装依存

矢印は「左が確定しないと右の契約が書けない」を表す（根拠は各台帳）。

```
CD_RESET ($01, #10) ──┐
                      ├─> CD_STAT ($09, #18) ──> CD_READ ($03, #12) ──> 転送層 (#190)
CD_BASE ($02, #11) ───┘        │                     │                     │
                               │                     ├─ MPR3 退避 (B3)      └─ #149 で slot $03 に配線（#190 解除後）
                               │                     ├─ 割込み前提 (B7/R12)
                               │                     └─ エラー体系 (#189)
                               ├─> CD_SEEK ($04, #13)
                               └─> CD_SUBQ / CD_DINFO / CD_CONTNTS / CD_PCMRD ...

CD_READ ──> CD_EXEC ($05, #14) : 読込後の制御移譲・スタック・バンク（B2 が「戻る」未確定）
CD_READ / CD_EXEC ──> CD_BOOT ($00, #9) : 起動経路。MPR7 は呼出側が bank $00 に設定（台帳の SOURCE_FACT）
```

| 依存 | 現状 | 根拠 |
| --- | --- | --- |
| CD_READ ← CD_BASE | ブロック番号の基点（絶対 LBA か CD_BASE 相対か）が UNKNOWN | [188 A8](188-cd-read-transfer-mode-contract.md) |
| CD_READ ← CD_STAT | busy/timeout の戻り値が UNKNOWN。待ち方が未確定 | [cd-read-contract §2 T8](cd-read-contract.md) |
| CD_READ ← メディア状態 | 無媒体の観測は Geargrafx で実行不能（T7） | [cd-read-contract §2](cd-read-contract.md)、[#144/#169](cd-media-state.md) |
| CD_READ ← MPR3 | 呼出側が退避・復元。callee 内の MPR 状態は UNKNOWN | [188 B3](188-cd-read-transfer-mode-contract.md) |
| CD_EXEC ← CD_READ | 読込後の制御移譲の契約が UNKNOWN。ledger は実行可能な callee を未取得 | [cd-exec ledger](cd-exec-contract-ledger.md) C2 |
| CD_BOOT ← CD_RESET | 起動時の CD 初期化状態は観測点のみ（#4/#9） | [startup-checkpoint](startup-checkpoint.md) |

## 4. #190（CD_READ 転送層）の解除に必要な証拠

#190 の受入条件は「必須の参照 ABI が UNKNOWN なら実装を開始せず BLOCKED」である。以下の証拠がすべて揃うまで BLOCKED_ABI を維持する。

| # | 必要な証拠 | 現状 | 出典として使えないもの |
| --- | --- | --- | --- |
| E1 | 転送先モード（`_dh = 3` の意味、RAM 宛先の単位）と 2048 バイト1セクターの対応 | UNKNOWN（[188 A1–A5](188-cd-read-transfer-mode-contract.md)） | 呼出側コメント「Use MPR3」のみ |
| E2 | count の単位と count 0 の挙動 | UNKNOWN（A4、T8） | Hu-Go! の HLE（`$22`） |
| E3 | 基点（CD_BASE 相対か絶対 LBA か） | UNKNOWN（A8、#11） | 呼出側の計算のみ |
| E4 | 成功時の A/X/Y、失敗時のエラー種別（無媒体、範囲外、busy） | UNKNOWN（B1–B2、#189） | 合成 fixture（callee 未実行） |
| E5 | 戻り時の MPR0–7、スタック深さ、I フラグ前提 | UNKNOWN（B3–B8、R12） | HuCC のコメント |
| E6 | 上記を純正 callee で実行して測った結果（pinned Geargrafx、自己所有 BIOS をローカルのみで使用、バイトは非公開） | 未実施（`CALLEE_REAL_CORE_RUNS = 0`） | #5 の23シナリオ（デバイス層の READ(6) 診断。callee の証拠ではない。[#190 の 2026-10-21 判断](https://github.com/mao2009/oss-pce-cd-bios/issues/190)） |
| E7 | 転送ルーチンの出自の確認：独立に実装し、HuC elmer `cdrom.asm` の文言・構造を写していないこと | 未実施。elmer は awaiting relicensing | 派生元を写したコード |
| E8 | 無媒体・範囲外の実測（T7、#189） | Geargrafx では実行不能。別経路の証拠が必要 | — |

最低限の解除条件は **E1〜E6 が admissible な証跡で PASS** し、E7 が人間に確認されること。E8 は無媒体経路を扱う場合のみ必須である。#148 の呼出側台帳（merged）、#188・#189 の契約（merged）は依存として満たしているが、callee の ABI 証拠ではないため解除にはならない。

## 5. 次の作業（提案）

1. #190 は BLOCKED_ABI のまま。E1–E6 の観測計画を、自己所有 BIOS のローカル実行手順として別 Issue で起こす（本 PR では行わない）。
2. CD_EXEC と CD_BOOT は callee 候補の探索を先に行う。候補が見つからない限り、spec-reference only のまま。
3. HuC elmer `cdrom.asm` の派生元権利を、人間の法務レビューに回す（§2.1 の 1 行目）。
4. 未着手の CD API（§2 の下段）は、#308 台帳に候補を追加してから分類する。

## 6. 検証

| 区分 | 結果 | 理由 |
| --- | --- | --- |
| 候補ライセンスの再取得（HuC `LICENSE`、elmer `cdrom.asm` 冒頭、Hu-Go! `COPYING`） | PASS | pinned SHA で `gh api` から取得し、§2.1 の表示と一致 |
| ares の ISC 表示 | NOT_RUN（既存記録を引用） | 本 PR で再取得していない |
| 依存マップの各矢印 | PASS（台帳参照） | 引用先の文言と照合。新しい事実は追加していない |
| `git diff --check` | 実行 | docs のみ |
| 実コア・純正 BIOS | NOT_RUN | 調査のみ |

## 7. 範囲と限界

- コード・バイトのコピー、disassembly の掲載はしていない。
- 分類は本書の時点（2026-10-26）の既存記録に基づく。PR #422 以降の判定変更はない。
- 法的助言ではない。権利の最終判定は人間が行う。
