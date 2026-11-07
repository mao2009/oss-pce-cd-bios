# Issue #308 ファイル単位ライセンス監査（補遺）

- 対象: [#308](https://github.com/mao2009/oss-pce-cd-bios/issues/308)（Refs のみ。Closes は使わない）。親: [#105](https://github.com/mao2009/oss-pce-cd-bios/issues/105)
- 基準: `origin/main` = `8839b37f52f7bcaff47065e61ed55ef15bfa5be4`（2026-11-07）。PR #703（`9939ee5`）は `origin/main` の祖先であることを `git merge-base --is-ancestor` で確認済み。
- 位置づけ: [oss-candidate-ledger.md](../oss-candidate-ledger.md) と [issue-308-candidate-inventory.md](issue-308-candidate-inventory.md) の**補遺**。本書で `APPROVED` にしない。`release_eligible=false`、`BLOCKED_ABI` 維持。ROM・CI 成果物への第三者コードは **0 件**。コード本文はコピーしていない。
- 分類（本書用）: `CANDIDATE`（根拠と条件が確認済で検討可）/ `CONDITIONAL`（追加対応が必要）/ `HOLD`（未確認・疑義または組込み不可）。証拠状態: `VERIFIED_EVIDENCE` / `PARTIAL_EVIDENCE` / `UNKNOWN`。`CANDIDATE` は ROM 組込みの最終承認ではない。法的・Owner 判断が残るものは明示する。
- 方法: 各候補は pinned SHA の raw 原文（`https://raw.githubusercontent.com/{owner}/{repo}/{SHA}/{path}`）を取得し、Git blob SHA-1 と SHA-256 を再計算した（2026-11-07）。ヘッダの行番号は本監査での直接確認値である。改変履歴は GitHub `commits?path=...&sha={SHA}` の最新 1 件（HEAD 以前）である。HuCC・cc65・NeoCD・Cult-of-GBA・ares の対象ファイルは全文検索でも `SPDX-License-Identifier` 行を確認できなかった。Geargrafx の対象ファイルも SPDX 行なし（GPL 文言のみ）。`pcecd.c`/`pcecd.h` の BSD 文面・Hu-Go! 全体も SPDX 行なし。SPDX はファイルのライセンス文言からの対応付けであり、法的評価ではない。完全な blob/SHA 一覧は台帳・検証記録を参照し、本書では追加分のみ記す。

## 1. HuCC（pce-devel/huc @ `54f5c73606c1e7142e8095c727cab2767e2b3cb3`）

公式 URL: https://github.com/pce-devel/huc ・ pin commit: `54f5c73606c1e7142e8095c727cab2767e2b3cb3`（author `jbrandwood` 2026-09-10）。root `LICENSE`（2386 B, blob `473965af…`）は「SDK は複数出典、1980 年代起点、正確な法的地位は言明不能。例外は MagicKit freeware、TGEmu GPL、GCC GPL のみ。Ulrich Hecht の変更のみ Simplified BSD」と述べる（lines 1-45）。**root は BSL を全リポジトリに付与しない。`src/`・`tgemu/` 等は本監査の候補ではなく `UNKNOWN` とする。** BSL はファイルヘッダのみで付与された。

| ID | 対象ファイル (path) | ヘッダ著作権・ライセンス文言（直接確認） | SPDX 根拠 | 依存・生成 | 分類 / 証拠 |
| --- | --- | --- | --- | --- | --- |
| L-01 | `include/hucc/hucc-math.asm` (14021 B, blob `0cee925a…`, SHA-256 `e259826d…`) | 8 行 `Copyright John Brandwood 2021-2024.` 10 行 `Distributed under the Boost Software License, Version 1.0.` 11 行 `(See accompanying file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)`。17-21 行「SDCC-compatible routines, Y:A primary」。`include` なし | `BSL-1.0`（文言対応。SPDX 行なし） | 自立。生成物なし | **CANDIDATE / VERIFIED_EVIDENCE** |
| L-02 | `include/hucc/hucc-systemcard.asm` (20230 B, blob `027f8932…`, SHA-256 `4258b7c3…`) / `include/hucc/hucc-systemcard.h` (5522 B, blob `f907b4b0…`, SHA-256 `f2477642…`) | `.asm` 8 行 `Copyright John Brandwood 2024.` 10 行 BSL 文言。`.h` 13 行同文。呼出側マクロ／ライブラリ関数（16-22 行） | `BSL-1.0` | `include` なし。System Card 本体ではない | **CANDIDATE（REFERENCE_ONLY）/ VERIFIED_EVIDENCE** |
| L-03 | `include/hucc/core-startup.asm` (16736 B, blob `aef6da44…`, SHA-256 `fa340fca…`) | 8 行 `Copyright John Brandwood 2021-2024.` 10 行 BSL 文言 | `BSL-1.0` | 426/466 行 `include "core-kernel.asm"`、454 行 `include "core-stage1.s"`（pin では `include/hucc/core-stage1.s` は HTTP 404、ビルド生成の stage1 equates。ライセンスは UNKNOWN で対象外）。MPR 構成は HuCC 前提 | **CONDITIONAL / VERIFIED_EVIDENCE（ファイル）・PARTIAL（連鎖・生成物）** |
| L-04 | `include/hucc/vdc.asm` (31186 B, blob `68d95e93…`, SHA-256 `4cc4c903…`) | 10 行 `Copyright John Brandwood 2021-2025.` 12 行 BSL 文言 | `BSL-1.0` | 23 行 `include "common.asm"`、24 行 `include "vce.asm"` | **CONDITIONAL / VERIFIED_EVIDENCE** |
| L-05 | `include/hucc/joypad.asm` (29389 B, blob `b3e9639a…`, SHA-256 `7c867b77…`) | 8 行 `Copyright John Brandwood 2019-2025.` 10 行 BSL 文言 | `BSL-1.0` | 直接の `include` 指示文はなし。設定は `include/hucc/core-config.inc`（7685 B, blob `72bb5116…`, SHA-256 `3549e31b…`、8 行 `Copyright John Brandwood 2021-2024.`、10 行 BSL 文言）に依存。`core-kernel.asm` が 452 行で `include "joypad.asm"` する（L-06） | **CONDITIONAL / VERIFIED_EVIDENCE** |
| L-06 | `include/hucc/core-kernel.asm` (17483 B, blob `129bf9df…`, SHA-256 `ae183017…`) | 8 行 `Copyright John Brandwood 2021-2024.` 10 行 BSL 文言。30-32 行「VDC handler is changed from System Card's handler」 | `BSL-1.0` | 449 行コメント「configuration from `core-config.inc`」、452 行 `include "joypad.asm"` | **CONDITIONAL（REFERENCE_ONLY）/ VERIFIED_EVIDENCE** |
| L-07 | `include/hucc/common.asm` (7421 B, blob `04caaa30…`, SHA-256 `c16d337c…`) / `vce.asm` (14094 B, blob `333a7134…`, SHA-256 `6a88c2e9…`) | `common.asm` 10 行 / `vce.asm` 10 行 いずれも `Copyright John Brandwood …` + BSL 文言 | `BSL-1.0` | 直接 `include` なし。`common.asm` 27 行 `irq_cnt`「System Card variable」 | **CONDITIONAL / VERIFIED_EVIDENCE** |
| L-08 | `examples/asm/elmer/include/cdrom.asm` (45646 B, blob `d8f304ed…`, SHA-256 `a1de6902…`) | 8-9 行「derived from Hudson's original fast CD code in Legend of Xanadu 2 … Gulliver Boy」 14 行 `Modifications copyright John Brandwood 2022.` 16 行 BSL 文言（改変部のみ） | `BSL-1.0`（改変部のみ） | 80 行 `include "common.asm"`、88 行 `include "adpcm.asm"`。`adpcm.asm` は `examples/asm/elmer/include/adpcm.asm`（12613 B, blob `7e78e2e7…`, SHA-256 `17d39c93…`、10 行 `Copyright John Brandwood 2022.`、12 行 BSL 文言）。`common.asm` は `examples/.../common.asm` が pin で HTTP 404 のため、探索パス経由の `include/hucc/common.asm`（L-07）と推測され、未確定。ベースは Hudson 原コードで表示・権利者なし | **HOLD（REFERENCE_ONLY）/ PARTIAL_EVIDENCE** |

- NOTICE / ライセンス全文: `include/hucc/LICENSE_1_0.txt` は pin で HTTP 404（不存在）。`examples/asm/elmer/LICENSE_1_0.txt`（1338 B, blob `36b7cd93…`, SHA-256 `c9bff757…`）全文は BSL-1.0（lines 1-20：頒布物に著作権表示と本許諾文を含める。機械実行オブジェクトのみの場合の例外付き）。NOTICE ファイルは root ツリーに存在しない（`LICENSE` のみ）。`BSL-1.0` と旧記録の `Boost 1.0` は、pin の全ファイルが同一文言「Boost Software License, Version 1.0」を使うため同一扱いであることを直接確認した。
- 改変履歴（HEAD 以前・最新 1 件）: L-01 `12ee9cb7` 2024-07-29、L-02 `069d343a` 2026-06-17、L-03 `2e98c8fd` 2025-09-03、L-04 `08b1d09a` 2025-09-06、L-05 `352b7a8a` 2025-02-05、L-06 `532ef6c5` 2024-09-20、L-08 `60dc2d1b` 2025-04-05。いずれも `John Brandwood`。L-01 の「SDCC-compatible」は互換性表現であり、派生の明言ではない。SDCC 由来混入の有無は Owner が確認すること（§8）。
- ROM 条件: BSL-1.0 のソース・派生物配布では表示保持が要る。`LICENSE_1_0.txt` を配布物へ同梱するか、ハードウェア ABI へ変換する際の改変表示を人間が決める。ABI 変換（SDCC 式 `X:A`→`Y:A` 等）と MPR/IRQ の適合は未確認（`BLOCKED_ABI`）。

## 2. Hu-Go!（mckayemu/hugo @ `64ed226da2288738781743eeef228c069e8973a4`）

公式 URL: https://github.com/mckayemu/hugo ・ pin commit: `64ed226da2288738781743eeef228c069e8973a4`（author `mckaygerhard` 2016-01-12、全対象ファイルが同一 import）。全ファイルとも `SPDX-License-Identifier` 行なし。

| ID | 対象ファイル | ヘッダ・ライセンス（直接確認） | SPDX 根拠 / 権利者 | 依存・生成 | 分類 / 証拠 |
| --- | --- | --- | --- | --- | --- |
| L-09 | `pcecd.c` (20589 B, blob `780eba35…`, SHA-256 `7ada6b45…`) + `pcecd.h` (1571 B, blob `8e0a6eb8…`, SHA-256 `444b51ec…`、lines 1-23 同文 BSD) | `COPYING`（218 B, blob `ff408338…`, SHA-256 `7062ee21…`） lines 1-7「Hu-Go! is GPL-2.0 (see LICENSE.gpl). EXCEPTED pcecd.c … modified BSD (see LICENSE.bsd-modified)」。`pcecd.c` lines 1-23 は 3 条項 BSD 系文面だが**著作権者名・年・表示行なし**（13 行 `THE AUTHOR` の一般名詞のみ） | `UNKNOWN`（権利者不明）。`COPYING` の例外文言が唯一の根拠 | 25-26 行 `#include "pce.h"` `"utils.h"`。`pce.h` lines 5-15・52-78 は `config.h`・`sys_dep.h`・`hard_pce.h`・`cheat.h`・`debug.h`・`gfx.h`・`cleantyp.h`・`h6280.h`/`m6502.h`・`cd.h`・`allegro.h`・`info_dat.h`・`data.h` 等を連鎖させる。`utils.h`（1676 B, blob `d4ab4be6…`, SHA-256 `4b228c63…`） lines 2-5 は GPL v2 or later 文面、line 10 は「See the GNU Library General Public License for more details.」と別文言を指す。原文はそのまま記録し、SPDX は確定しない。`unzip.c`（Gilles Vollant 1998、zlib 依存）も同一ツリー。NOTICE なし | **HOLD / PARTIAL_EVIDENCE** |
| L-10 | `bios.c` (29130 B, blob `a8c4efab…`, SHA-256 `db3f227e…`) | lines 1-16 は `hard BIOS support source file` の説明のみ。ライセンス文なし。`COPYING` 既定により GPL-2.0（Version 2。`-only`/`-or-later` は `LICENSE.gpl` 不在のため UNKNOWN） | `GPL-2.0`（`COPYING` 既定。ファイル文言なし）/ ヘッダ権利者なし | 13-16 行 `h6280.h`・`globals.h`・`utils.h`・`pce.h` | **HOLD（REJECTED・提案 RESTRICTED）/ VERIFIED_EVIDENCE** |
| L-11 | `cd.c` (1188 B, blob `af675c16…`, SHA-256 `31229a81…`) | 1 行 `#include "cd.h"` のみ。ライセンス文なし。`COPYING` 既定により GPL-2.0。`cd.h`・`bios.h` とも表示なし | 同上 | `cd.h`（表示なし、エミュレータ CD 変数） | **HOLD（REJECTED・提案 RESTRICTED）/ VERIFIED_EVIDENCE** |
| L-12 | `COPYING` / `AUTHORS` | `AUTHORS`（1188 B, blob `456c4e11…`, SHA-256 `a9109bcf…`） lines 4-9「based on fpce（Bero）、Xpce（Hmmx）、unzip（Gilles Vollant）、zlib、SDL（LGPL 表記）、SDLnet」。lines 13-14 CD 寄与 Nyef・Ixion。`pcecd.c` 権利者は特定しない | — | `LICENSE.gpl`・`LICENSE.bsd-modified`・`LICENSE` いずれも pin ツリーに**不在**（HTTP 404、tree 列挙で `AUTHORS`・`COPYING` のみ） | **HOLD / VERIFIED_EVIDENCE（不在）** |

- ROM 条件: L-10・L-11 は GPL-2.0 のため MIT の ROM に静的組込みしない。L-09 は例外の全文も権利者も確定できず、条件付き組込みもできない。`pce.h`/`utils.h` の連鎖はエミュレータ実行時環境に結びつき、単独抽出は技術的にもライセンス的にもできない。REFERENCE_ONLY での仕様観察も、GPL コードの行単位翻訳は独立実装とはみなさない（[provenance.md](../provenance.md)）。

## 3. cc65（cc65/cc65 @ `71746c829e77f74c2b601a7d16821170c2610df3`）

公式 URL: https://github.com/cc65/cc65 ・ pin commit: `71746c829e77f74c2b601a7d16821170c2610df3`（author `robjustice` 2026-09-01）。root `LICENSE`（808 B, blob `88cacf14…`, SHA-256 `8e87ae93…`） lines 1-17 は zlib 系（origin 誤認禁止、改変の明示、表示の削除・改変禁止。謝辞は任意）。NOTICE なし。ファイル自体にライセンス文・SPDX 行はなし。

| ID | 対象ファイル | ヘッダ署名（直接確認） | 依存（直接確認） | 分類 / 証拠 |
| --- | --- | --- | --- | --- |
| L-16 | `libsrc/runtime/udiv.s` (1671 B, blob `4115382c…`, SHA-256 `256c162a…`) | 2 行 `Ullrich von Bassewitz, 07.08.1998`。ライセンス指定なし | 9 行 `.import popptr1`、10 行 `.importzp sreg, ptr1, ptr4`。`popptr1.s`（720 B, blob `90dca5e2…`, SHA-256 `21443db1…`、2 行 `Christian Kruger, 20-May-2018`）は 8 行 `.import incsp2`・10 行 `.importzp c_sp, ptr1` を連鎖。`libsrc/runtime/incsp2.s`（859 B, blob `395ea52c…`, SHA-256 `2cbedb25…`、2 行 `Ullrich von Bassewitz, 25.10.2000`）は `c_sp` に依存 | **CONDITIONAL / PARTIAL_EVIDENCE** |
| L-16 | `libsrc/runtime/umul16x16r32.s` (1661 B, blob `cfcf82d9…`, SHA-256 `cac8f1dc…`) | 2 行 `Ullrich von Bassewitz, 2010-11-03` | 10 行 `.include "zeropage.inc"`。実体は `asminc/zeropage.inc`（1020 B, blob `8d508fc5…`, SHA-256 `295b902f…`、4 行 `(C) Copyright 2002-2012, Ullrich von Bassewitz`、`.globalzp c_sp, sreg, ptr1-ptr4 …`）。`ptr1`/`ptr3`/`sreg` の zeropage 割当が必要 | **CONDITIONAL / PARTIAL_EVIDENCE** |
| L-16 | `libsrc/runtime/mulax3.s` (447 B, blob `34237960…`, SHA-256 `954d7cdb…`) | 2 行 `Ullrich von Bassewitz, 04.10.2001` | 9 行 `.importzp ptr1` のみ | **CONDITIONAL / PARTIAL_EVIDENCE** |

- 改変履歴（最新 1 件）: `udiv.s` `dd411efd` 2018-05-24（popptr1  adaptation）、`umul16x16r32.s` `f8f901b0` 2022-04-17、`mulax3.s` `f8f901b0` 2022-04-17、`zeropage.inc` `74a22621` 2025-06-27、`popptr1.s` `c72126e6` 2025-07-01。`LICENSE` `aeb84925` 2015-05-05（Simplified license）。生成物: なし（source のみ。cc65 ランタイムの ROM リンクは本リポジトリ方針で対象外）。
- ROM 条件: zlib 系の表示保持・改変表示をソースと ROM 配布物に残すかは Owner が決める。零ページ割当・呼出規約（`A:X` primary 等）と System Card ABI の差は未確認。ファイル自体に表示がないため、root 文言が `libsrc/runtime/*.s` に及ぶことを Owner が確認する。

## 4. その他の既存候補（要約だが証拠固定）

| ID | リポジトリ @ pin | 対象ファイル・ヘッダ（直接確認） | 依存・第三者由来 | 分類 / 証拠 |
| --- | --- | --- | --- | --- |
| L-13 | pcsx-redux/nugget @ `c950e18a168944ec2d4e6d3c408fc224317483a7` | `openbios/cdrom/{cdrom,statemachine,helpers}.c` いずれも lines 1-25 MIT 全文 `Copyright (c) 2020 PCSX-Redux authors`。`cdrom.h` も同文。root `LICENSE`（1075 B, blob `490e7cb3…`, SHA-256 `7e423e1a…`）は MIT `Copyright (c) 2019 PCSX-Redux authors` | `openbios/Makefile` lines 7-60: boot/card/cdrom/charset/fileio/gpu/handlers/kernel/main/pio/shell/sio0/tty/patches 等約 50 ファイル＋ `common/psxlibc/fastmemset.s`。`.gitmodules` は `psxlua`・`xmake-psx`・`libcester`。README lines 9-13「意図的に original 準拠の quirks/bug を含む」 | **CONDITIONAL（REFERENCE_ONLY）/ VERIFIED_EVIDENCE** |
| L-14 | libretro/neocd_libretro @ `b1e04c738cb48a1dae0574b8877f6a116d270ca1` | `src/{hlebios,cdromtoc,cdromcontroller}.cpp`（78705 B / 24161 B / 10710 B）は全文検索でも `copyright`/`SPDX`/`license`/`author`/`permission is hereby` の語なし（`#include` で開始）。`hlebios.h`（13057 B）・`cdrom.h`・`cdromcontroller.h` も同文なし。root `LICENSE.md`（7693 B, blob `fcf85b87…`, SHA-256 `e16063f1…`）は LGPL-3.0 | `hlebios.cpp` 6-8 行 3rdparty musashi/ym/z80。`cdromtoc.cpp` は chd/flac/mp3/ogg/wav。README Credits: Musashi、MAME、libretro-common | **HOLD / VERIFIED_EVIDENCE（表示なし）** |
| L-15 | Cult-of-GBA/BIOS @ `a30e9a96df083628b650724b7d4d7112b4070b98` | `bios_calls/math/{division,sqrt,arctan}.s`（1776 B / 770 B / 4850 B）は全文検索でも `copyright`/`SPDX`/`license`/`permission is hereby` の語なし（先頭からコード `swi_DivArm:` 等）。root `LICENSE`（1068 B, blob `fae47d5e…`, SHA-256 `761675b9…`）は MIT `Copyright 2020-2021 DenSinH and fleroviux`。README「Written completely in ARM assembly from scratch」 | ARM 専用。`make.sh`（160 B, blob `2e1cec9a…`）は `arm-none-eabi-as`＋`objcopy` で `bios.bin`（16384 B, blob `ec03b9e3…`, SHA-256 `61af6e8c…`）を生成。`bios.bin` は生成物 | **CONDITIONAL（REFERENCE_ONLY）/ PARTIAL_EVIDENCE** |
| L-17 | drhelius/Geargrafx @ `b49ae82a012eade566d72e9d61c9d95a5caa4da3` | `src/{scsi_controller,cdrom}.cpp` lines 1-18 いずれも `Copyright (C) 2024 Ignacio Sanchez`＋GPL-3.0 文言（version 3 or any later）。SPDX 行なし。root `LICENSE`（35149 B, blob `f288702d…`, SHA-256 `3972dc97…`）は GPL-3.0 全文 | 外部試験依存。ROM にリンクしない | **HOLD（外部試験のみ）/ VERIFIED_EVIDENCE** |
| L-18 | ares-emulator/ares @ `9408cb43d4948fc3ea6e152a307a34348df3fe04` | `ares/pce/pcd/{io,scsi}.cpp`（6249 B / 10269 B）は全文検索でも `copyright`/`SPDX`/`license`/`permission is hereby` の語なし（先頭からコード `auto PCD::read…` 等）。`pcd.hpp`（14691 B）も文言なし。root `LICENSE`（41598 B, blob `a0edf8fc…`, SHA-256 `a1053dec…`、859 行）は複数ライセンスの集合。先頭 lines 1-17 は `Copyright (c) 2004-2025 ares team, Near et al`＋ISC 型文言、その後 Stack-less JIT・libchdr・zstd・Apache-2.0・MPL-2.0 等を連結 | `pcd.hpp` は Thread/Node/VFS/Session等の全体枠組に依存 | **HOLD / PARTIAL_EVIDENCE** |
| L-19 | MAME / Mednafen / beetle-pce（pin 未記録） | 未取得。`LICENSE` 直接確認なし | — | **HOLD / UNKNOWN（NOT_RUN）** |
| L-20 | mathieulh/PCE_CD_BIOSES_NO_AUTOBOOT（IPS） | 元 ROM 依存の派生 | — | **HOLD（対象外）/ UNKNOWN** |

- L-13 の生成物: `openbios.elf` / `openbios.bin`（Makefile・docker）。L-15 の `bios.bin` は生成物であり、ソース候補として扱わない。生成物・リンク済バイナリのライセンスはソースと切り離せないため、本監査ではいずれもコピー・組込み対象外とした。
- L-14 の LGPL 混在: root が LGPL-3.0 でも、表示なしファイルの静的 ROM 組込みは事前承認ではない。ファイル単位の権利者が不明な以上、通常の組込み条件を確定できない。
- 改変履歴（HEAD 以前・最新 1 件）: L-13 `cdrom.c` `b780703f` 2025-09-01、`statemachine.c` `bffda67a` 2022-10-26、`helpers.c` `676e77d7` 2024-10-20。L-14 `hlebios.cpp` `b1e04c73` 2026-10-03、`cdromtoc.cpp` `7a6dc2f5` 2026-07-31、`cdromcontroller.cpp` `3aa984f8` 2022-02-13。L-15 `division.s` `ec3d933f` 2020-10-25、`sqrt.s` `ae627d1c` 2021-11-13、`arctan.s` `7ef0ad0e` 2020-11-28。L-17 `scsi_controller.cpp` `e9d9a889` 2026-10-06、`cdrom.cpp` `e9d9a889` 2026-10-06。L-18 `io.cpp` `a57cce6b` 2025-09-22、`scsi.cpp` `b308958b` 2026-01-23。Hu-Go! の対象ファイルは全て pin と同一の `64ed226d` 2016-01-12（import）であり、ファイルごとの差異はない。

## 5. 生成・リンク・依存の整理（横断）

- HuCC 連鎖: `vdc.asm`→`common.asm`＋`vce.asm`（いずれも BSL）、`core-kernel.asm`→`joypad.asm`＋設定 `core-config.inc`（いずれも BSL）、`core-startup.asm`→`core-kernel.asm`＋生成物 `core-stage1.s`（pin で 404、ライセンス UNKNOWN）。`elmer/cdrom.asm`→`common.asm`（解決先未確定）＋`adpcm.asm`（BSL、12613 B）。連鎖全体の ABI・割当・割込みは未検証。
- cc65 連鎖: `udiv.s`→`popptr1.s`→`incsp2`・`c_sp`、`umul16x16r32.s`→`zeropage.inc`（`asminc/`）。`zeropage.s` が実体の割当だが、本監査では通常の割当衝突確認は行っていない。
- Hu-Go! 連鎖: `pcecd.c`→`pce.h`→エミュレータ全体、`bios.c`→`h6280.h`・`globals.h`・`utils.h`・`pce.h`。単独抽出は連鎖 GPL の混入を伴う。
- nugget 連鎖: `openbios` 全体（Makefile `SRCS`）と `.gitmodules` サブモジュール。CDROM 3 ファイルだけの抽出は連鎖確認が不可欠。
- NeoCD 連鎖: 3rdparty コア（Musashi/YM/Z80）、libretro-common（CHD/FLAC/Ogg）、`chdfile` 等。Credits の第三者由来は root LGPL に吸収されない。
- ares 連鎖: `pcd.hpp` のフレームワーク。root が多ライセンス連結のため、書面落としによる誤用防止として HOLD とする。
- 生成物: Cult `bios.bin`（16384 B, blob `ec03b9e3…`）、nugget `openbios.elf`/`openbios.bin`、Geargrafx `.so`。いずれも生成物自体を台帳にコピーせず、再ビルド時の権利を源泉に結ぶ。

## 6. 改変・表示・ROM 組込みの条件（共通）

- 本リポジトリは MIT（`LICENSE`）。第三者の権利を本 MIT で再ライセンスしない。コピー・改変・ROM へのコンパイル・ソースとバイナリの配布は、各自の義務を満たすことが条件。
- BSL-1.0（HuCC）/ zlib 系（cc65）/ MIT（nugget・Cult root）は、ソース再配布時の表示保持が中核条件。ROM 配布物への同梱方法は Owner が決める。
- GPL-2.0（Hu-Go! 既定）/ GPL-3.0（Geargrafx）/ LGPL-3.0（NeoCD root）は、静的ファームウェアへの無条件組込みを承認するものではない。GPL/LGPL は本 MIT ROM と混ぜない。HOLD・REJECTED を維持する。
- 表示なし・権利者不明（NeoCD 3 ファイル、ares 2 ファイル、pcecd.c）は、ライセンスが許すとみなさず `UNKNOWN`・HOLD とする。Cult 3 ファイルも表示なしのため直接コピーは不可。`REFERENCE_ONLY` としても root MIT の表示付与が条件であり、分類は `CONDITIONAL`（`PARTIAL_EVIDENCE`）とする。README・リポジトリ表記だけで確定しない。
- `release_eligible=false`・`BLOCKED_ABI` は解除しない。`VERIFIED`・`DEV_CALLABLE` 等への昇格は本書の範囲外。

## 7. 検証状態

| 項目 | 結果 | 備考 |
| --- | --- | --- |
| pinned raw 再取得と blob/SHA-256 再計算（§1-§4） | PASS | 本書列挙ファイルは台帳値と一致。追加分（`zeropage.inc`、`popptr1.s`、`incsp2.s`、`pcecd.h`、`utils.h`、`core-config.inc`、`adpcm.asm`、`bios.bin`、`make.sh`、`LICENSE.md` 等）は本書に完全値で記録 |
| ヘッダ行の直接確認（著作権・ライセンス文言） | PASS（確認範囲はヘッダ） | 本文の全行は読んでいない。SPDX 行は対象ファイルに存在せず、SPDX は文言対応であることを明記 |
| LICENSE/COPYING/NOTICE の適用範囲確認 | PARTIAL（範囲内 PASS／全文未確認） | §1-§4 の範囲では root 文言とファイルヘッダの対応を確認した。LICENSE/COPYING 等の全文取得・比較は行っていないため、適用範囲の全体確定は `UNKNOWN` として保留し、PASS とは記録しない。Hu-Go! の参照先 2 件は pin で不在を確認（HTTP 404） |
| 連鎖依存（`include`・`#include`・Makefile・`.gitmodules`） | PASS（列挙範囲） | 完全なツリー列挙は行っていない。連鎖ファイル自体のライセンスは連鎖先で別途確認が要る |
| 改変履歴の最新 1 件確認 | PASS | 履歴の全件は調査していない。pin の著者・日時を記録した |
| 実 Geargrafx での ABI 適合 | NOT_RUN | 本書は実装の検証ではない。`BLOCKED_ABI` 解除はしない |
| `make check` / `make test` | NOT_RUN（本 PR は docs のみ。CI に委ねる） | CI の最新 HEAD で確認すること |
| ROM への第三者コード混入 | BLOCKED（0 件） | ライセンス未確定のため継続 |

## 8. Owner 判断事項

1. HuCC BSL の `LICENSE_1_0.txt` 同梱: `include/hucc/` に不存在のため、配布時に `examples/asm/elmer/LICENSE_1_0.txt` の全文をどこに置くか。人間の決定待ち（現在 PENDING）。
2. L-01 の「SDCC-compatible」: 派生の有無、SDCC の上流権利との関係を人間が確認する。明言がなければ派生扱いにしない。
3. L-09 `pcecd.c`: 権利者と modified BSD 全文が pin から確定できない。別ソースへの照会または `REFERENCE_ONLY` 観察継続を決める。推測で確定しない。
4. cc65 の root `LICENSE` の `libsrc/runtime/*.s` への適用: ファイル文言なしのため、人間の法的確認が要る。特に `udiv.s` 連鎖（`popptr1.s`・`incsp2`）と `mulax3.s`/`umul16x16r32.s` の zeropage 割当（`ptr1` 等）の含有範囲を決める。これら3ファイルはこの未確認により `CONDITIONAL` とし、`CANDIDATE` とはしない。
5. L-13 nugget: MIT 表示の保持は可能だが、retail BIOS の quirks 模倣に当たる部分の境界を人間が決める。直接コピーはせず設計参照に限定する。
6. L-14・L-15・L-18 の表示なしファイル: 権利者特定の照会（履歴・上流）を行うか、REFERENCE_ONLY に留めるか。
7. L-19 の pin 固定: MAME・Mednafen・beetle-pce の対象ファイル・commit を決め、再取得してから監査する（現在は `UNKNOWN`・`NOT_RUN`）。
8. 全 CANDIDATE / CONDITIONAL の `APPROVED` 化: 本書は検討条件の確定までであり、最終承認は承認者・日付付きの人間の処分（`APPROVED` / `RESTRICTED` / `PENDING`）が必要である。

## 9. 既存台帳との整合

- 本書は [oss-candidate-ledger.md](../oss-candidate-ledger.md) §1 の blob/SHA 一致を再確認し、`zeropage.inc` の実体パス（`asminc/zeropage.inc`）を補った。L-04/L-05 の旧記録 `Boost 1.0` と本 pin の `Boost Software License, Version 1.0` 文言が同一であることを直接確認した。
- [reuse/oss-candidate-verification-308.md](../reuse/oss-candidate-verification-308.md) §1 の 41 行（NOT_FOUND 2）は本監査の再計算と一致する。L-19 は両者とも `NOT_RUN`・`UNKNOWN` で整合する。
- [issue-308-candidate-inventory.md](issue-308-candidate-inventory.md) の L0/L1/UNVERIFIED 分類は本分類の前提であり、本書が上書きしない。disposition（`PENDING` / 提案 `RESTRICTED` / `—`）は本書では変更しない。
- 候補の追加は行っていない。台帳に既出の候補のみを証拠固定した。L-17 の「ほか」のファイルは未列挙であり、本書でも対象外とする。
