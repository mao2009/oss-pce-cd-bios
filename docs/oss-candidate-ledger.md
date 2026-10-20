# OSS 候補の由来・ライセンス台帳（ファイル単位, Issue #308）

- 対象 Issue: [#308](https://github.com/mao2009/oss-pce-cd-bios/issues/308)（Refs のみ。Closes は使わない）。親: [#105](https://github.com/mao2009/oss-pce-cd-bios/issues/105)
- 基準: `origin/main` = `6407bd4`。調査日: 2026-10-20。
- 位置づけ: **台帳（調査のみ）**。コードのコピー・翻訳・ROM への取り込みは行っていない。確定欄（disposition）は L-01〜L-09、L-12〜L-19 が **PENDING**（人間の承認待ち）。L-10・L-11 は「提案: RESTRICTED」（確定ではない）、L-20 は「—」（対象外）。本書は APPROVED 判定を一切含まない。
- 既存記録: [oss-reuse-audit-r3.md](oss-reuse-audit-r3.md)、[oss-reuse-audit-r4.md](oss-reuse-audit-r4.md)、[cd-oss-survey.md](cd-oss-survey.md)、[reuse-inventory.md](reuse-inventory.md)。本台帳は既存記録の事実を**ファイル単位の一覧に統合**するもので、既存の詳細（API 別判定・計測値）は再掲せず参照する。
- 凡例:
  - 「今回計測」: 本 PR の調査で `gh api`（pinned SHA 指定）から取得し、blob SHA・サイズ・ヘッダ・ツリーを確認した項目。
  - 「既存記録」: r3 / r4 / cd-oss-survey で記録された値。本 PR で pinned 内容から再計算し一致を確認したものは「再計算一致」と併記する。
  - 「未計算」: 本 PR で対象ファイルを取得・計算していない項目。推測で埋めていない。
  - 「UNKNOWN」: 確認できなかった項目。推測で埋めていない。
  - 確定欄の表記: `PENDING` は未確定。「提案: …」は人間判定前の提案。「—」は対象外。APPROVED / RESTRICTED の確定値は本書に無い。
- `reuse_mode`（参照と採用の分離）: `DIRECT` / `ADAPTED` / `REFERENCE_ONLY` / `REJECTED`。`REFERENCE_ONLY` は仕様・挙動の参照に限り、コードを写さない。

## 0. 採用状況（source reference と code adoption の分離）

| 区分 | 件数 | 内容 |
| --- | --- | --- |
| 採用コード（ROM・CI 成果物に入るもの） | **0** | 本リポジトリは本 PR で第三者コードを一切追加しない |
| 採用候補（`ADAPTED` 提案、または依存として監査要） | 4 群 | HuCC math（L-01）、HuCC VDC・入力群（L-04、L-05）、HuCC 推移依存（L-07）、cc65 ca65 算術（L-16）。確定欄はすべて PENDING |
| 参照のみ（`REFERENCE_ONLY`） | 多数 | HuC elmer `cdrom.asm`（L-08）、nugget（L-13）、NeoCD（L-14）、Cult-of-GBA（L-15）、Hu-Go! `pcecd.c`（L-09）ほか |
| 採用不可（`REJECTED`）または ROM 混入禁止 | 4 群 | Hu-Go! `bios.c` / `cd.c`（GPL-2.0 既定。L-10, L-11）、Geargrafx（GPL-3.0、外部試験のみ。L-17）、GPL 系参照のコード（MAME / Mednafen / beetle-pce。L-19）、patch 系（L-20） |

## 1. ファイル単位の台帳

| ID | 候補 | 対象ファイル | リポジトリ @ commit | blob / hash | SPDX・ライセンス表示（ファイル単位） | 著作権者（ヘッダ） | 依存・生成・リンク | 改変履歴 | reuse_mode | disposition | 根拠 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| L-01 | HuCC | `include/hucc/hucc-math.asm` | pce-devel/huc @ `54f5c73606c1e7142e8095c727cab2767e2b3cb3` | blob `0cee925a0822ff224ea1eb89eb86abcecf3613e7`、SHA-256 `e259826d63a11019e83e86f0906a60c8f3d547505dde498b736a05397bf40e55`、14021 B（今回計測） | `BSL-1.0`（ヘッダ「Boost Software License, Version 1.0」） | John Brandwood 2021-2024 | ヘッダが指す `LICENSE_1_0.txt` は `include/hucc/` に無い（既存記録 r4） | 上流版のまま（本調査で改変なし） | ADAPTED 候補（SDCC 式 ABI の変換が必要） | PENDING | 今回ヘッダ確認、r4 |
| L-02 | HuCC | `include/hucc/hucc-systemcard.asm` | 同上 | SHA-256 `4258b7c3e8751127c0615af3178e431d81043c25fa6cb2b6b1d51b566aec7ea9`（既存記録、再計算一致） | `BSL-1.0`（既存記録） | John Brandwood（既存記録） | 呼び出し側ラッパ。System Card 本体ではない | — | REFERENCE_ONLY（ABI・テスト設計の参照） | PENDING | r3 |
| L-03 | HuCC | `include/hucc/core-startup.asm` | 同上 | SHA-256 `fa340fca7d88a3603b1b55a8d5359b81e414dce527ac0ac7fd4837f0a5f5b37a`（既存記録、再計算一致） | `BSL-1.0`（既存記録） | John Brandwood（既存記録） | MPR 構成は HuCC ランタイム前提 | — | REFERENCE_ONLY | PENDING | r3 |
| L-04 | HuCC | `include/hucc/vdc.asm` | 同上 | SHA-256 `4cc4c903dae56a8cc3bc9be73dce13a8152900c784903c27a9d233b0f29f9a09`（既存記録、再計算一致） | `BSL-1.0`（既存記録は `Boost 1.0` と表記。同一扱いの可否は UNKNOWN, r3 §3.2） | John Brandwood 2021-2025 | `include "common.asm"`、`"vce.asm"` に依存（今回ツリーで確認は未実施、既存記録） | — | ADAPTED 候補（依存込みで監査要） | PENDING | r4 |
| L-05 | HuCC | `include/hucc/joypad.asm` | 同上 | SHA-256 `7c867b777aa0c40f9960940234f93cb37fcbf2ee89bea720c9a947a3f1b16357`（既存記録、再計算一致） | `BSL-1.0`（既存記録は `Boost 1.0` と表記） | John Brandwood 2019-2025 | HuCC 独自のポート配列 | — | ADAPTED 候補 | PENDING | r4 |
| L-06 | HuCC | `include/hucc/core-kernel.asm` | 同上 | SHA-256 `ae183017c59c7adc3fe8851e7483c324b0387720bc757f618643a00a18942015`（既存記録、再計算一致） | `BSL-1.0`（既存記録は `Boost 1.0` と表記） | John Brandwood 2021-2024 | `include "joypad.asm"`。VDC 割り込みは System Card 版から変更（既存記録） | — | REFERENCE_ONLY（割り込み契約が食い違う恐れ） | PENDING | r4 |
| L-07 | HuCC | `include/hucc/common.asm`、`include/hucc/vce.asm` | 同上 | SHA-256 `c16d337cbe8186cb4f2470a643e082b3af495141fc39b06b48891bd882bef5a7`（`common.asm`）、`6a88c2e90ea3e4a747fc35be976cf5d1a67b35c53d4d0a73676c82c6f2d28d38`（`vce.asm`）（既存記録、再計算一致） | `BSL-1.0`（既存記録は `Boost 1.0` と表記） | John Brandwood 2021-2024 / 2021-2025 | `vdc.asm` の推移的依存 | — | 依存として監査要 | PENDING | r4 |
| L-08 | HuC elmer | `examples/asm/elmer/include/cdrom.asm` | pce-devel/huc @ `54f5c73606c1e7142e8095c727cab2767e2b3cb3` | SHA-256 `a1de6902f821500b6d10e282c8898e98c22d4e105d07bb0bb6cddb716399ce0d`、blob `d8f304edc7078fd8c604844f1e5394831219a393`（既存記録、再計算一致） | 改変部のみ `BSL-1.0`（既存記録は `Boost 1.0`）。ベースは Hudson 原コード由来で**表示なし** | 改変部: John Brandwood 2022 | リポジトリ全体は `NOASSERTION`（既存記録） | ヘッダ記載の派生経緯 | REFERENCE_ONLY（ベースの権利が UNKNOWN のため採用不可扱い） | PENDING | cd-oss-survey、r3 |
| L-09 | Hu-Go! | `pcecd.c` | mckayemu/hugo @ `64ed226da2288738781743eeef228c069e8973a4` | blob `780eba35a171c0402741fd4f099314fc3906b6f9`、20589 B（今回計測）。SHA-256 `7ada6b45432209fe74fec80dfda075bdb9a3a8a7c80d6694d4a3160d0360cdd9`（既存記録、再計算一致） | `COPYING` が「GPL 互換の modified BSD」を例外指定。ヘッダは 3 条項の BSD 系文面（今回確認） | **著作権者行なし**（今回確認） | `#include "pce.h"`（同一ツリー内） | 上流版 | REFERENCE_ONLY（著作権者 UNKNOWN、`COPYING` が参照する `LICENSE.bsd-modified` は pin に無い） | PENDING | 今回ヘッダ・ツリー確認 |
| L-10 | Hu-Go! | `bios.c` | 同上 | blob `a8c4efabbb7c01fd15ce47ae194cc282d68f5478`、SHA-256 `db3f227e09a73457bc9b0f2c54a3922ab9dca1e301f1b5029690c0d210972f22`（今回計測） | `COPYING` により GPL-2.0 既定 | ヘッダの著作権者行は既存記録どおり無し | HLE BIOS | — | REJECTED（MIT ROM へ組込み不可） | 提案: RESTRICTED（人間が判定） | 今回ツリー、COPYING 確認 |
| L-11 | Hu-Go! | `cd.c` | 同上 | blob `af675c16fefe27da1a5488550294acf06623baa4`、SHA-256 `31229a81006c18ab2647cea208b2971f3dcc6a7c9c94002a9a5fcccd6b6feb0c`（今回計測） | GPL-2.0 既定 | 既存記録どおり | 時刻変換のみ（60 行、既存記録） | — | REJECTED | 提案: RESTRICTED | 今回ツリー、r3 |
| L-12 | Hu-Go! | `COPYING` | 同上 | blob `ff40833852479105a93dc7d6789d2450ab19414f`、SHA-256 `7062ee213db7328032976669dc4cc728f998d19e217b7a4e558ab0e356e30ec3`（今回計測、218 B） | 「Hu-Go! は GPL-2.0。例外は pcecd.c（modified BSD）」 | — | `LICENSE.gpl` と `LICENSE.bsd-modified` を参照するが、**両ファイルは pin のツリーに存在しない**（今回ツリー確認） | — | — | PENDING（NOTICE 欠落のため条件を確定できない） | 今回確認 |
| L-13 | nugget OpenBIOS | `openbios/cdrom/cdrom.c`、`statemachine.c`、`helpers.c` | pcsx-redux/nugget @ `c950e18a168944ec2d4e6d3c408fc224317483a7` | SHA-256 `312a415e8b872446898345c4b016b244adc18493bbbeb46876a5d1a8f788ca0b`（`cdrom.c`）、`b49830ea775c92d996d1eb2cf7ae6f93e5960481e9716af6e009427e23f140b3`（`statemachine.c`）、`d7ee3bb3d613097aba5455b05da524ec15c52012ec19bee2efeaa24193330596`（`helpers.c`）（既存記録、再計算一致） | `MIT`（ヘッダ・リポジトリ LICENSE、既存記録） | 「Copyright (c) 2020 PCSX-Redux authors」（既存記録） | PSX ABI。`openbios` 内の他依存は UNKNOWN | — | REFERENCE_ONLY | PENDING | r3 |
| L-14 | NeoCD-Libretro | `src/hlebios.cpp`、`cdromtoc.cpp`、`cdromcontroller.cpp` | libretro/neocd_libretro @ `b1e04c738cb48a1dae0574b8877f6a116d270ca1` | SHA-256 `e84e98091500feebaf34cae4876f28158c0eb82439030b68063ac8ec8b3dcec5`（`hlebios.cpp`）、`8c628fffa443e03590c1c9c17991bc7fc91752bc92ce8b71e2e0913f4be7e41f`（`cdromtoc.cpp`）、`3b4af97b6d761f90244aa4a42f2f865305bf2b6f369f1f8178fbefabfa0ef034`（`cdromcontroller.cpp`）（r4、再計算一致） | ファイル表示なし。ルート `LICENSE.md` は LGPL-3.0（r4） | ファイル単位は **UNKNOWN** | 68000 / NEO-GEO 前提。static 組込みは未承認 | — | REFERENCE_ONLY | PENDING | r4 |
| L-15 | Cult-of-GBA BIOS | `bios_calls/math/{division,sqrt,arctan}.s` | Cult-of-GBA/BIOS @ `a30e9a96df083628b650724b7d4d7112b4070b98` | SHA-256 `f0f8f4230cc4c92e64763d3bb9a0eaadc604593caa175c0b6edae89dee30100f`（`division.s`）、`810d0ba2f11bd455f82631a34530ff8cffd2d03bc2a1b655a3f0cc51b8235944`（`sqrt.s`）、`cd61cc12a544497bfe28d980c07e936fb631f7072a27b016a442a14e96b0dc60`（`arctan.s`）（r4、再計算一致） | ファイル表示なし。ルート `LICENSE` は MIT（r4） | ルート: DenSinH and fleroviux（r4） | ARM 命令列。HuC6280 へは書き直しが必要 | — | REFERENCE_ONLY（アルゴリズム概念のみ） | PENDING | r4 |
| L-16 | cc65 runtime | `libsrc/runtime/udiv.s`、`umul16x16r32.s`、`mulax3.s` | cc65/cc65 @ `71746c829e77f74c2b601a7d16821170c2610df3` | blob `4115382c80c13a4fefefe77b5aae944df1c01e10`（`udiv.s`、1671 B、今回計測）、`cfcf82d9ea18c1e2e27c5a28f17bf52ef526f0b9`（`umul16x16r32.s`）、`3423796053e377e2dcafabf89643ff92ae39c412`（`mulax3.s`）（既存記録、再計算一致）。SHA-256 は `udiv.s` `256c162ae7b387a4273f026612559a72c1c843b5eeb2c2902d42b706afa9746d`、`umul16x16r32.s` `cac8f1dc5e3b6534826b5dfc0a982cb607173cf3d189aad15052972a219dfb5e`、`mulax3.s` `954d7cdbc5cfe810744c62c3a794093cd26aa589dd335ef2e74cd0786d70afcc`（今回計測） | ルート `LICENSE` は zlib 系（blob `88cacf1486f5b3eca553241204427913008887ab`、808 B。今回、zlib License 文面を確認）。ファイル単位の表示は未確認（ヘッダに license 行なし） | `udiv.s`: Ullrich von Bassewitz 1998、`umul16x16r32.s`: 同 2010、`mulax3.s`: 同 2001（今回ヘッダ確認）。プロジェクト全体は cc65 authors（既存記録） | `zeropage.inc`、`ptr1`/`ptr3`/`sreg`/`ptr4`、`popptr1` を import（r3 §ca65） | — | ADAPTED 候補（zero-page・calling convention の監査が前提） | PENDING | 今回 blob 計測、r3 |
| L-17 | Geargrafx（試験用） | 実コア本体（`src/scsi_controller.cpp`、`cdrom.cpp` ほか） | drhelius/Geargrafx @ `b49ae82a012eade566d72e9d61c9d95a5caa4da3` | SHA-256 `2dbb6e14c7d794de66c0fd39498ed3d46b46af6bc26ea380db48bc284b9363db`（`scsi_controller.cpp`）、`b80300d2dc6fac0f854ac6169769557867eff9451650b86b7ce4207a9f751536`（`cdrom.cpp`）（今回計測）。「ほか」のファイルは未計算 | GPL-3.0（既存記録） | Ignacio Sanchez 2024（既存記録） | 外部試験依存。ROM にリンクしない | — | REFERENCE_ONLY（外部試験のみ。再配布時は GPL-3.0 を遵守） | PENDING | cd-oss-survey |
| L-18 | ares（参照） | `ares/pce/pcd/io.cpp`、`scsi.cpp` | ares-emulator/ares @ `9408cb43d4948fc3ea6e152a307a34348df3fe04` | SHA-256 `9e3f7da8642f7c8784be17f57ad74d87d3eb500863922cbe2341c828ea8c7bc1`（`io.cpp`）、`2fa6bd1f29d6b7d1191e1f61aa57c3a6d553f3f4bf9d953dc9bae9643545bfe1`（`scsi.cpp`）（今回計測） | ISC（既存記録。ファイル単位は未再読） | ares team（既存記録） | C++ エミュレータ | — | REFERENCE_ONLY | PENDING | cd-oss-survey |
| L-19 | GPL 系参照（採用不可） | MAME `pce_cd.cpp`（BSD-3、ファイル単位）、Mednafen / beetle-pce（GPL-2.0 以降） | 未計算（既存記録に commit 値なし） | 未計算 | 上記。MAME 全体は GPL | 既存記録 | — | — | REJECTED（コード）／REFERENCE_ONLY（仕様のみ、コピー・翻訳なし） | PENDING | r3、cd-oss-survey |
| L-20 | patch 系（除外） | `mathieulh/PCE_CD_BIOSES_NO_AUTOBOOT` の IPS | 既存記録 | — | 元 ROM に依存（著作権物の派生） | — | — | — | REJECTED（対象外） | — | cd-oss-survey |

注記:
- `SHA-256` と `blob`（Git オブジェクト SHA-1）は別のハッシュである。本書の完全値は、本 PR で pinned SHA から取得した内容を再計算した値（SHA-256 は 64 桁、blob は 40 桁の小文字 16 進）。既存記録の切り詰め値（`…`）はすべて再計算値と一致することを確認した。
- 「未計算」は本 PR で対象ファイルを取得・計算していない項目（L-17 の「ほか」のファイル、L-19 の commit と hash）。
- 「今回計測」の値は本 PR の `gh api` 取得時点（2026-10-20）に限る。

## 2. LICENSE / NOTICE 監査

| 候補 | 確認した LICENSE / NOTICE | 結果 | 残課題 |
| --- | --- | --- | --- |
| pce-devel/huc | GitHub 判定 `NOASSERTION`（既存記録）。ルート LICENSE は旧来の作者の利用条件が明示されていない旨を述べる（r4） | 代表 SPDX を一つに定められない | `LICENSE_1_0.txt` を `include/hucc/` に同梱するかは人間判断。NOTICE 有無は**未確認** |
| mckayemu/hugo | `COPYING`（blob `ff40833852479105a93dc7d6789d2450ab19414f`、今回確認）。`AUTHORS`（blob `456c4e112fd88208a86437729219b935b9e516cd`、SHA-256 `a9109bcf5d7e235a50e65061f9c431f09f21d92a28f30cf554f795a8a1d9a03c`、今回読了）。`LICENSE.gpl`、`LICENSE.bsd-modified` は**ツリーに無い**（今回確認） | GPL-2.0 既定。`pcecd.c` のみ modified BSD 例外。`AUTHORS` は第三者由来として unzip（Gilles Vollant）、zlib、SDL（LGPL 表記）、SDLnet（Sam Lantinga ほか）を挙げる | modified BSD 例外の全文を pin から確定できない（L-09 は REFERENCE_ONLY）。第三者コンポーネント（zlib、SDL、SDLnet、unzip）のライセンス表示（LICENSE / NOTICE）の同梱状況は **UNKNOWN**（pin のツリー全体は未列挙）。`pcecd.c` の著作権者は `AUTHORS` からも特定できない（UNKNOWN） |
| cc65/cc65 | ルート `LICENSE`（blob `88cacf1486f5b3eca553241204427913008887ab`、今回確認） | zlib 系。表示保持・改変表示の条件 | NOTICE の有無は**未確認**。ROM 出力時の表示方法を人間が決める |
| nugget | ルート `LICENSE` は MIT（既存記録） | MIT の表示保持が必要 | ファイル単位の表示は既存記録のみ |
| NeoCD-Libretro | ルート `LICENSE.md` は LGPL-3.0（既存記録） | ファイル表示なし | ファイル単位の権利者は UNKNOWN |
| Cult-of-GBA | ルート `LICENSE` は MIT（既存記録） | 表示保持が必要 | `bios.bin` の由来は UNKNOWN |

NOTICE 相当の確認は、本 PR では Hu-Go! の `COPYING` と `AUTHORS` を今回確認し、`LICENSE.gpl` / `LICENSE.bsd-modified` の不在を確認した（ツリー全体の列挙は未実施）。他候補の NOTICE は**未確認（UNKNOWN）**。

## 3. 依存ライブラリ（ファイル間の推移依存）

| 起点 | 依存先 | 状態 |
| --- | --- | --- |
| HuCC `vdc.asm` | `common.asm`、`vce.asm` | 既存記録。推移依存の完全監査は未了 |
| HuCC `core-kernel.asm` | `joypad.asm`（`common.asm` 等も連鎖） | 既存記録 |
| cc65 `umul16x16r32.s` | `zeropage.inc`（cc65 の zero-page 割当） | 既存記録。割当衝突の確認は未了 |
| cc65 `udiv.s` | `popptr1`、`ptr4` | 既存記録 |
| Hu-Go! `pcecd.c` | `pce.h`（同一ツリー） | 今回確認。`pce.h` の監査は未実施 |
| HuC elmer `cdrom.asm` | ベースの Hudson 原コード | UNKNOWN（権利者不明） |

ビルド・試験ツール（cc65 / ca65、Geargrafx）は `docs/toolchain.md` と `docs/emulator-geargrafx.md` の管理下にあり、ROM へリンクしない前提（`docs/provenance.md` の規則に従う）。

## 4. ROM 混入防止の確認

| 条件 | 本台帳の判定 |
| --- | --- |
| GPL-2.0 / GPL-3.0 のコードを MIT の ROM に入れない | L-10、L-11（GPL-2.0）、L-17（GPL-3.0、外部試験のみ）は組込み対象外 |
| LGPL・権利者不明のコードを入れない | L-14（LGPL-3.0 / 表示なし）、L-08（ベース権利不明）、L-09（著作権者なし）は PENDING。承認まで組込み不可 |
| 不明ライセンスを「許諾」とみなさない | 表示が無い候補（L-14 の 3 ファイル）は UNKNOWN 扱い |
| 現行 ROM への追加 | 0 件。本 PR は文書のみ |

## 5. 範囲と限界

- 今回の `gh api` 取得（pinned SHA 指定）では、L-01〜L-18 の対象ファイル、Hu-Go! の `AUTHORS`、HuC elmer の `LICENSE_1_0.txt` を取得し、SHA-256 と blob を再計算した。ヘッダ・ツリーの確認は「今回計測」の項目に限る。既存記録の本文・API 別判定・計測値は再計測していない。
- ファイル本文は、ヘッダと関連行の確認に留めた。コードを本文にコピーしていない。
- 「APPROVED」「RESTRICTED」の最終判定は人間が行う。本台帳の「提案」欄は判断材料にすぎない。
- SPDX は GitHub 判定や各ファイルのヘッダ文言から推定した識別子であり、法的評価ではない。

## 6. 検証記録（本 PR）

| 区分 | 結果 | 理由 |
| --- | --- | --- |
| `gh api` による pinned blob・ヘッダ・ツリー確認 | PASS（今回計測の項目のみ） | 上表「今回計測」の列に限る |
| pinned 内容からの SHA-256・blob 再計算 | PASS（既存記録の切り詰め値は全て一致） | 再計算値は上表に完全値で記載。取得できない項目は「未計算」 |
| 既存記録の再計測（本文・API 別判定） | NOT_RUN | r3 / r4 / cd-oss-survey の値を引用。再計測は行っていない |
| `make check` / `make test` | NOT_RUN（本 PR は docs のみ。実行は PR 作成後の CI に委ねる） | ローカル実行は今回の手順に含めていない |
| 実コア（Geargrafx）での検証 | NOT_RUN | 本台帳は実装の検証ではない |

## 7. 残作業

1. L-09 の `LICENSE.bsd-modified` 全文を、pin 以外の出典（上流の別コミット等）から取得し、modified BSD 例外の条件を確定する。
2. L-08 `cdrom.asm` のベース（Hudson 原コード）の権利状況を確認する。確認できなければ REFERENCE_ONLY のまま。
3. L-14 の 3 ファイルの権利者を、リポジトリの履歴または上流への照会で特定する。
4. cc65 の zero-page 依存（L-16）と HuCC の推移依存（L-04〜L-07）を監査し、ABI 適合を Geargrafx 実コアで確認する（BLOCKED_ABI の解除は行わない）。
5. 各行の disposition を、人間のレビューで PENDING / APPROVED / RESTRICTED に確定する（承認者と日付を記録）。L-10・L-11 の「提案: RESTRICTED」も同様に人間が確定する。
6. Hu-Go! の `AUTHORS`（pin、blob `456c4e112fd88208a86437729219b935b9e516cd`、SHA-256 `a9109bcf5d7e235a50e65061f9c431f09f21d92a28f30cf554f795a8a1d9a03c`。今回読了）は、本体が fpce（Bero 作）と Xpce（Hmmx）を基にし、CD エミュレーションに Nyef・Ixion の寄与があると記す。あわせて第三者由来として unzip（Gilles Vollant）、zlib、SDL（LGPL 表記）、SDLnet を挙げる。これらの第三者コンポーネントのライセンス表示（LICENSE / NOTICE）は pin 内で未確認（UNKNOWN）であり、LICENSE / NOTICE 監査の残課題とする。`AUTHORS` は `pcecd.c` の著作権者を特定しないため、L-09 の権利者は UNKNOWN のまま照合を続ける。
7. HuCC の `LICENSE_1_0.txt` は `include/hucc/` に無いが、同一 pin の `examples/asm/elmer/LICENSE_1_0.txt`（blob `36b7cd93cdfbac762f5be4c6ce276df2ea6305c2`、SHA-256 `c9bff75738922193e67fa726fa225535870d2aa1059f91452c411736284ad566`、1338 B）に Boost 1.0 全文がある。同梱の要否・出典の扱いを人間が判断する。
