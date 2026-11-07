# Issue #308 Candidate-Inventory evidence: OSS reuse candidates

- 対象 Issue: [#308](https://github.com/mao2009/oss-pce-cd-bios/issues/308)（Refs のみ。Closes は使わない）。親: [#105](https://github.com/mao2009/oss-pce-cd-bios/issues/105)
- 基準: `origin/main` = `e846c24`（2026-11-07 取得）
- 位置づけ: **証拠整理のみ**。コードのコピー・翻訳・ROM への取り込みは行っていない。候補の disposition は台帳どおり (PENDING / RESTRICTED 提案 / —)。本 PR で承認済み (APPROVED) にはしない。System Card ABI 未確定は BLOCKED_ABI、`release_eligible=false`。
- 関連正本: [oss-candidate-ledger.md](../oss-candidate-ledger.md)（ファイル単位台帳）、[reuse/oss-candidate-verification-308.md](../reuse/oss-candidate-verification-308.md)（pinned パス再検証）、[reuse-inventory.md](../reuse-inventory.md)（初期候補一覧）、[cd-oss-survey.md](../cd-oss-survey.md)（CD-ROM² 調査）。

## 1. 候補インベントリ（要約）

| ID | 候補 | リポジトリ @ commit | 対象ファイル | SPDX / ライセンス表示（ファイル単位） | 著作権者（ヘッダ） | reuse_mode | disposition |
| --- | --- | --- | --- | --- | --- | --- | --- |
| L-01 | HuCC math | pce-devel/huc @ `54f5c73606c1e7142e8095c727cab2767e2b3cb3` | `include/hucc/hucc-math.asm` | `BSL-1.0` | John Brandwood 2021-2024 | ADAPTED 候補 | PENDING |
| L-02 | HuCC System Card 呼出側 | 同上 | `include/hucc/hucc-systemcard.asm` | `BSL-1.0` | John Brandwood | REFERENCE_ONLY | PENDING |
| L-03 | HuCC startup | 同上 | `include/hucc/core-startup.asm` | `BSL-1.0` | John Brandwood | REFERENCE_ONLY | PENDING |
| L-04 | HuCC VDC | 同上 | `include/hucc/vdc.asm` | `BSL-1.0` / `Boost 1.0`（同一扱い UNKNOWN） | John Brandwood 2021-2025 | ADAPTED 候補 | PENDING |
| L-05 | HuCC joypad | 同上 | `include/hucc/joypad.asm` | `BSL-1.0` / `Boost 1.0` | John Brandwood 2019-2025 | ADAPTED 候補 | PENDING |
| L-06 | HuCC kernel | 同上 | `include/hucc/core-kernel.asm` | `BSL-1.0` / `Boost 1.0` | John Brandwood 2021-2024 | REFERENCE_ONLY | PENDING |
| L-07 | HuCC 依存 | 同上 | `common.asm`, `vce.asm` | `BSL-1.0` / `Boost 1.0` | John Brandwood 2021-2024 / 2021-2025 | 依存として監査要 | PENDING |
| L-08 | HuC elmer SCSI | 同上 | `examples/asm/elmer/include/cdrom.asm` | 改変部のみ `BSL-1.0`、ベース Hudson 原コード表示なし | 改変部: John Brandwood 2022 | REFERENCE_ONLY | PENDING |
| L-09 | Hu-Go! pcecd.c | mckayemu/hugo @ `64ed226da2288738781743eeef228c069e8973a4` | `pcecd.c` | modified BSD 例外（`COPYING`）。著作権者行なし | UNKNOWN | REFERENCE_ONLY | PENDING |
| L-10 | Hu-Go! bios.c | 同上 | `bios.c` | GPL-2.0 既定（`COPYING`） | ヘッダに著作権者行なし | REJECTED | 提案: RESTRICTED |
| L-11 | Hu-Go! cd.c | 同上 | `cd.c` | GPL-2.0 既定 | 既存記録どおり | REJECTED | 提案: RESTRICTED |
| L-12 | Hu-Go! COPYING | 同上 | `COPYING` | GPL-2.0 既定。`LICENSE.gpl` / `LICENSE.bsd-modified` は pin に NOT_FOUND | — | — | PENDING |
| L-13 | nugget OpenBIOS | pcsx-redux/nugget @ `c950e18a168944ec2d4e6d3c408fc224317483a7` | `openbios/cdrom/{cdrom,statemachine,helpers}.c` | `MIT` | Copyright (c) 2020 PCSX-Redux authors | REFERENCE_ONLY | PENDING |
| L-14 | NeoCD-Libretro | libretro/neocd_libretro @ `b1e04c738cb48a1dae0574b8877f6a116d270ca1` | `src/{hlebios,cdromtoc,cdromcontroller}.cpp` | ファイル表示なし。ルート `LICENSE.md` は LGPL-3.0 | ファイル単位 UNKNOWN | REFERENCE_ONLY | PENDING |
| L-15 | Cult-of-GBA math | Cult-of-GBA/BIOS @ `a30e9a96df083628b650724b7d4d7112b4070b98` | `bios_calls/math/{division,sqrt,arctan}.s` | ファイル表示なし。ルート `LICENSE` は MIT | ルート: DenSinH and fleroviux | REFERENCE_ONLY | PENDING |
| L-16 | cc65 runtime | cc65/cc65 @ `71746c829e77f74c2b601a7d16821170c2610df3` | `libsrc/runtime/{udiv,umul16x16r32,mulax3}.s` | ルート `LICENSE` は zlib 系。ファイル単位表示未確認 | Ullrich von Bassewitz 1998/2010/2001 | ADAPTED 候補 | PENDING |
| L-17 | Geargrafx（試験用） | drhelius/Geargrafx @ `b49ae82a012eade566d72e9d61c9d95a5caa4da3` | `src/{scsi_controller,cdrom}.cpp` ほか | GPL-3.0 | Ignacio Sanchez 2024 | REFERENCE_ONLY（外部試験のみ） | PENDING |
| L-18 | ares（参照） | ares-emulator/ares @ `9408cb43d4948fc3ea6e152a307a34348df3fe04` | `ares/pce/pcd/{io,scsi}.cpp` | ISC（ファイル単位未再読） | ares team | REFERENCE_ONLY | PENDING |
| L-19 | GPL 系参照 | MAME / Mednafen / beetle-pce（pin 未記録） | 未取得（対象ファイル未取得） | UNKNOWN（既存記録の転記で、本 PR では LICENSE/COPYING 直接確認なし。推測で確定しない） | UNKNOWN | REJECTED（コード） | PENDING |
| L-20 | patch 系 | mathieulh/PCE_CD_BIOSES_NO_AUTOBOOT | IPS パッチ | 元 ROM 依存（著作権物の派生） | — | REJECTED（対象外） | — |

採用コード（ROM・CI 成果物への取り込み）は **0 件**。

## 2. 生成・リンク依存（生成物ではない、監査対象の推移依存）

| 起点 | 依存先 | 状態 |
| --- | --- | --- |
| HuCC `vdc.asm` | `common.asm`, `vce.asm` | 既存記録。完全監査未了 |
| HuCC `core-kernel.asm` | `joypad.asm`（連鎖） | 既存記録 |
| cc65 `umul16x16r32.s` | `zeropage.inc`, `ptr1`/`ptr3`/`sreg` | 既存記録。割当衝突未確認 |
| cc65 `udiv.s` | `popptr1`, `ptr4` | 既存記録 |
| Hu-Go! `pcecd.c` | `pce.h`（同一ツリー） | 今回確認。`pce.h` 監査未実施 |
| HuC elmer `cdrom.asm` | Hudson 原コード | UNKNOWN（権利者不明） |

## 3. 検証状態（PASS / FAIL / NOT_RUN / BLOCKED）

| 項目 | 結果 | 備考 |
| --- | --- | --- |
| pinned パスの取得と blob・SHA-256 照合（verification §1） | PASS | 41 行。NOT_FOUND は Hu-Go! の `LICENSE.gpl` / `LICENSE.bsd-modified` の 2 件のみ |
| 外部 pinned 再取得（p7、49 件） | PASS | 47 件 HTTP 200 で主張と一致。2 件 HTTP 404 |
| LICENSE / COPYING 冒頭確認（verification §2） | PASS（確認範囲は冒頭のみ） | 全文比較は未実施 |
| 台帳の file 単位の権利者・NOTICE 確定 | NOT_RUN | 人間の provenance 監査が必要 |
| L-19（MAME / Mednafen / beetle-pce）取得 | NOT_RUN | pin 未記録 |
| `make check` / `make test` | NOT_RUN | docs のみの変更。CI に委ねる |
| 実コア（Geargrafx）での検証 | NOT_RUN | 本台帳は実装の検証ではない |
| System Card ABI 確定 | BLOCKED_ABI | `release_eligible=false`。本 PR では解除しない |
| ROM への第三者コード混入 | BLOCKED | ライセンス未確定のため 0 件のまま |

## 4. ライセンス確認レベルの定義

| レベル | 意味 | 本 PR での扱い |
| --- | --- | --- |
| L0 | リポジトリ単位でのみ確認 (ルート LICENSE/COPYING) | L-10, L-11, L-12, L-14, L-15, L-16, L-18 |
| UNVERIFIED | pinned commit 未記録 / 対象ファイル未取得 / LICENSE・COPYING 直接確認なし → 検証状態 NOT_RUN | L-19 |
| L1 | ファイル単位のヘッダ/SPDX 表記を確認 | L-01, L-02, L-03, L-04, L-05, L-06, L-07, L-08, L-09, L-13, L-17 |
| L2 | ファイル単位の権利者・NOTICE を確定 | 未実施 (人間の provenance 監査が必要) |
| L3 | 取り込み可否の承認 (Owner) | 未実施 (PENDING のまま) |

- 本 PR は L0/L1 の整理のみ。L2/L3 は NOT_RUN。
- blob SHA / 行番号は既存台帳・verification 文書からの転記であり、本 PR で独立再取得は行っていない (NOT_RUN)。
- その他の候補の L0/L1 分類と検証証拠の整合を確認した: L0 の L-10/L-11/L-12/L-14/L-15/L-16/L-18 はいずれも
  ルート LICENSE/COPYING のみでファイル単位の確定はしていない (L-18 の ISC、L-15 の MIT、L-16 の zlib 系は
  ルート表示の転記でありファイル単位ではない)。L1 の各候補はファイル単位ヘッダ/SPDX 表記の記録がある。
  L-19 のみ pin・ファイル・LICENSE 直接確認のいずれもなく、UNVERIFIED/NOT_RUN に分離した。
- "PENDING" / "UNKNOWN" を根拠なく "APPROVED" に変更していない。

## 5. 合成 vs 実測の区別

| 区分 | 内容 | 本 PR での扱い |
| --- | --- | --- |
| 合成フィクスチャ / host モデル試験 | 本 PR は docs のみ。コード試験は実行していない | NOT_RUN（該当なし） |
| 実コア（Geargrafx）診断 | 外部試験依存として記録のみ。本 PR では実行していない | NOT_RUN |
| 商用ディスク構造観測 | 本 PR の対象外 | NOT_RUN |
| System Card BIOS API 実行 | ABI 未確定のため BLOCKED | BLOCKED_ABI |
| 商用ゲーム互換 | 本 PR の対象外 | NOT_RUN |

## 6. 安全条件

- GPL-2.0 / GPL-3.0 のコードを MIT の ROM に入れない（L-10, L-11, L-17）。
- LGPL・権利者不明のコードを入れない（L-14, L-08, L-09）。
- 不明ライセンスを「許諾」とみなさない（L-14 の 3 ファイルは UNKNOWN）。
- 純正 BIOS・市販 ROM・個人セーブのバイトは公開・書き換えしない。
- System Card ABI 未確定は BLOCKED_ABI、`release_eligible=false`。本 PR では解除しない。

## 7. 証拠参照（URL / revision / line）

- pce-devel/huc @ `54f5c73606c1e7142e8095c727cab2767e2b3cb3`
  - `include/hucc/hucc-math.asm`（blob `0cee925a0822ff224ea1eb89eb86abcecf3613e7`）
  - `examples/asm/elmer/include/cdrom.asm`（blob `d8f304edc7078fd8c604844f1e5394831219a393`、ヘッダ 8 行「derived from Hudson's original fast CD code…」、14 行「Modifications copyright John Brandwood 2022」、16 行「Distributed under the Boost Software License, Version 1.0」）
  - `include/hucc/pcengine.inc`（468 行 `cd_boot = $00:E000` 宣言 (467 行は `.if`)、cd-oss-survey / cd-reuse-decision-309 参照）
- mckayemu/hugo @ `64ed226da2288738781743eeef228c069e8973a4`
  - `COPYING`（blob `ff40833852479105a93dc7d6789d2450ab19414f`、218 B、GPL-2.0 既定 + pcecd.c 例外）
  - `pcecd.c`（blob `780eba35a171c0402741fd4f099314fc3906b6f9`、2–11 行 3 条項 BSD 系文面、著作権者行なし）
  - `bios.c`（blob `a8c4efabbb7c01fd15ce47ae194cc282d68f5478`）、`cd.c`（blob `af675c16fefe27da1a5488550294acf06623baa4`）
- pcsx-redux/nugget @ `c950e18a168944ec2d4e6d3c408fc224317483a7`
  - `openbios/cdrom/cdrom.c`（1–5 行 MIT、Copyright (c) 2020 PCSX-Redux authors）、`statemachine.c`（`wait = 30000` 有界待ち）、`helpers.c`
- libretro/neocd_libretro @ `b1e04c738cb48a1dae0574b8877f6a116d270ca1`（ルート `LICENSE.md` LGPL-3.0）
- Cult-of-GBA/BIOS @ `a30e9a96df083628b650724b7d4d7112b4070b98`（ルート `LICENSE` MIT、2020–2021 DenSinH and fleroviux）
- cc65/cc65 @ `71746c829e77f74c2b601a7d16821170c2610df3`（ルート `LICENSE` blob `88cacf1486f5b3eca553241204427913008887ab`、zlib 系）
- drhelius/Geargrafx @ `b49ae82a012eade566d72e9d61c9d95a5caa4da3`（GPL-3.0、外部試験のみ）
- ares-emulator/ares @ `9408cb43d4948fc3ea6e152a307a34348df3fe04`（ルート LICENSE ISC、ares team 2004-2025）

## 8. 未解決事項

1. L-09 の `LICENSE.bsd-modified` 全文の確定（pin に NOT_FOUND）。
2. L-08 のベース（Hudson 原コード）の権利状況。
3. L-14 の 3 ファイルの権利者特定。
4. cc65 の zero-page 依存（L-16）と HuCC の推移依存（L-04〜L-07）の ABI 適合確認（Geargrafx 実コア、BLOCKED_ABI 解除は行わない）。
5. 各行の disposition の人間レビュー確定（APPROVED / RESTRICTED / PENDING）。
6. Hu-Go! `AUTHORS` の第三者コンポーネント（unzip, zlib, SDL, SDLnet）の LICENSE/NOTICE 同梱状況。
7. HuCC `LICENSE_1_0.txt` の同梱要否。

## 9. 他 PR との競合

- #358（台帳導入）、#561（pinned パス再検証）と同一対象。本 PR はそれらを置き換えない。追加の証拠整理のみ。
- #309（CD API 別決定表）は本 PR の台帳の disposition を変更しない。
- 競合するファイル編集は行わない。追加ファイルとリンクのみ。

## 10. マージ推奨

- **Draft のまま維持**（オーナー承認待ち）。CI + 独立レビューを経て CRITICAL/MAJOR/MEDIUM を修正する。
- 本 PR は文書のみ。`make check` / `make test` は CI に委ねる。
- `release_eligible=false`、BLOCKED_ABI 解除なし。
