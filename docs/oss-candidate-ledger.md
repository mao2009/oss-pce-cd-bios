# OSS 候補の由来・ライセンス台帳（ファイル単位, Issue #308）

- 対象 Issue: [#308](https://github.com/mao2009/oss-pce-cd-bios/issues/308)（Refs のみ。Closes は使わない）。親: [#105](https://github.com/mao2009/oss-pce-cd-bios/issues/105)
- 基準: `origin/main` = `6407bd4`。調査日: 2026-10-20。
- 位置づけ: **台帳（調査のみ）**。コードのコピー・翻訳・ROM への取り込みは行っていない。全行 **PENDING**（人間の承認待ち）。本書は APPROVED 判定を一切含まない。
- 既存記録: [oss-reuse-audit-r3.md](oss-reuse-audit-r3.md)、[oss-reuse-audit-r4.md](oss-reuse-audit-r4.md)、[cd-oss-survey.md](cd-oss-survey.md)、[reuse-inventory.md](reuse-inventory.md)。本台帳は既存記録の事実を**ファイル単位の一覧に統合**するもので、既存の詳細（API 別判定・計測値）は再掲せず参照する。
- 凡例:
  - 「今回計測」: 本 PR の調査で `gh api`（pinned SHA 指定）から取得し、blob SHA・サイズ・ヘッダ・ツリーを確認した項目。
  - 「既存記録」: r3 / r4 / cd-oss-survey で記録済みの値をそのまま引いた項目（再計測なし）。
  - 「UNKNOWN」: 確認できなかった項目。推測で埋めていない。
- `reuse_mode`（参照と採用の分離）: `DIRECT` / `ADAPTED` / `REFERENCE_ONLY` / `REJECTED`。`REFERENCE_ONLY` は仕様・挙動の参照に限り、コードを写さない。

## 0. 採用状況（source reference と code adoption の分離）

| 区分 | 件数 | 内容 |
| --- | --- | --- |
| 採用コード（ROM・CI 成果物に入るもの） | **0** | 本リポジトリは本 PR で第三者コードを一切追加しない |
| 採用候補（`ADAPTED` / `DIRECT` 提案） | 4 群 | HuCC math（L-01）、HuCC VDC/入力/カーネル群（L-04〜L-07）、cc65 ca65 算術（L-16）。すべて PENDING |
| 参照のみ（`REFERENCE_ONLY`） | 多数 | HuC elmer `cdrom.asm`（L-08）、nugget（L-13）、NeoCD（L-14）、Cult-of-GBA（L-15）、Hu-Go! `pcecd.c`（L-09）ほか |
| 採用不可（`REJECTED`）または ROM 混入禁止 | 3 群 | Hu-Go! `bios.c` / `cd.c`（GPL-2.0 既定。L-10, L-11）、Geargrafx（GPL-3.0、外部試験のみ。L-17）、patch 系（L-19） |

## 1. ファイル単位の台帳

| ID | 候補 | 対象ファイル | リポジトリ @ commit | blob / hash | SPDX・ライセンス表示（ファイル単位） | 著作権者（ヘッダ） | 依存・生成・リンク | 改変履歴 | reuse_mode | disposition | 根拠 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| L-01 | HuCC | `include/hucc/hucc-math.asm` | pce-devel/huc @ `54f5c73606c1e7142e8095c727cab2767e2b3cb3` | blob `0cee925a…`、14021 B（今回計測） | `BSL-1.0`（ヘッダ「Boost Software License, Version 1.0」） | John Brandwood 2021-2024 | ヘッダが指す `LICENSE_1_0.txt` は `include/hucc/` に無い（既存記録 r4） | 上流版のまま（本調査で改変なし） | ADAPTED 候補（SDCC 式 ABI の変換が必要） | PENDING | 今回ヘッダ確認、r4 |
| L-02 | HuCC | `include/hucc/hucc-systemcard.asm` | 同上 | SHA-256 `4258b7c3…`（既存記録） | `BSL-1.0`（既存記録） | John Brandwood（既存記録） | 呼び出し側ラッパ。System Card 本体ではない | — | REFERENCE_ONLY（ABI・テスト設計の参照） | PENDING | r3 |
| L-03 | HuCC | `include/hucc/core-startup.asm` | 同上 | SHA-256 `fa340fca…`（既存記録） | `BSL-1.0`（既存記録） | John Brandwood（既存記録） | MPR 構成は HuCC ランタイム前提 | — | REFERENCE_ONLY | PENDING | r3 |
| L-04 | HuCC | `include/hucc/vdc.asm` | 同上 | SHA-256 先頭 `4cc4c903dae5`（既存記録） | `Boost 1.0`（`BSL-1.0` と同一扱いの可否は UNKNOWN, r3 §3.2） | John Brandwood 2021-2025 | `include "common.asm"`、`"vce.asm"` に依存（今回ツリーで確認は未実施、既存記録） | — | ADAPTED 候補（依存込みで監査要） | PENDING | r4 |
| L-05 | HuCC | `include/hucc/joypad.asm` | 同上 | SHA-256 先頭 `7c867b777aa0`（既存記録） | `Boost 1.0`（既存記録） | John Brandwood 2019-2025 | HuCC 独自のポート配列 | — | ADAPTED 候補 | PENDING | r4 |
| L-06 | HuCC | `include/hucc/core-kernel.asm` | 同上 | SHA-256 先頭 `ae183017c59c`（既存記録） | `Boost 1.0`（既存記録） | John Brandwood 2021-2024 | `include "joypad.asm"`。VDC 割り込みは System Card 版から変更（既存記録） | — | REFERENCE_ONLY（割り込み契約が食い違う恐れ） | PENDING | r4 |
| L-07 | HuCC | `include/hucc/common.asm`、`include/hucc/vce.asm` | 同上 | SHA-256 先頭 `c16d337cbe81`、`6a88c2e90ea3`（既存記録） | `Boost 1.0`（既存記録） | John Brandwood 2021-2024 / 2021-2025 | `vdc.asm` の推移的依存 | — | 依存として監査要 | PENDING | r4 |
| L-08 | HuC elmer | `examples/asm/elmer/include/cdrom.asm` | pce-devel/huc @ `54f5c736…` | SHA-256 `a1de6902…ce0d`、blob `d8f304ed…a393`（既存記録） | 改変部のみ `Boost 1.0`。ベースは Hudson 原コード由来で**表示なし** | 改変部: John Brandwood 2022 | リポジトリ全体は `NOASSERTION`（既存記録） | ヘッダ記載の派生経緯 | REFERENCE_ONLY（ベースの権利が UNKNOWN のため採用不可扱い） | PENDING | cd-oss-survey、r3 |
| L-09 | Hu-Go! | `pcecd.c` | mckayemu/hugo @ `64ed226da2288738781743eeef228c069e8973a4` | blob `780eba35…`、20589 B（今回計測）。SHA-256 `7ada6b45…cdd9`（既存記録） | `COPYING` が「GPL 互換の modified BSD」を例外指定。ヘッダは 3 条項の BSD 系文面（今回確認） | **著作権者行なし**（今回確認） | `#include "pce.h"`（同一ツリー内） | 上流版 | REFERENCE_ONLY（著作権者 UNKNOWN、`COPYING` が参照する `LICENSE.bsd-modified` は pin に無い） | PENDING | 今回ヘッダ・ツリー確認 |
| L-10 | Hu-Go! | `bios.c` | 同上 | blob `a8c4efab…`（今回計測） | `COPYING` により GPL-2.0 既定 | ヘッダの著作権者行は既存記録どおり無し | HLE BIOS | — | REJECTED（MIT ROM へ組込み不可） | 提案: RESTRICTED（人間が判定） | 今回ツリー、COPYING 確認 |
| L-11 | Hu-Go! | `cd.c` | 同上 | blob `af675c16…`（今回計測） | GPL-2.0 既定 | 既存記録どおり | 時刻変換のみ（60 行、既存記録） | — | REJECTED | 提案: RESTRICTED | 今回ツリー、r3 |
| L-12 | Hu-Go! | `COPYING` | 同上 | blob `ff408338…`（今回計測、218 B） | 「Hu-Go! は GPL-2.0。例外は pcecd.c（modified BSD）」 | — | `LICENSE.gpl` と `LICENSE.bsd-modified` を参照するが、**両ファイルは pin のツリーに存在しない**（今回ツリー確認） | — | — | PENDING（NOTICE 欠落のため条件を確定できない） | 今回確認 |
| L-13 | nugget OpenBIOS | `openbios/cdrom/cdrom.c`、`statemachine.c`、`helpers.c` | pcsx-redux/nugget @ `c950e18a…` | SHA-256 `312a415e…`、`b49830ea…`、`d7ee3bb3…`（既存記録） | `MIT`（ヘッダ・リポジトリ LICENSE、既存記録） | 「Copyright (c) 2020 PCSX-Redux authors」（既存記録） | PSX ABI。`openbios` 内の他依存は UNKNOWN | — | REFERENCE_ONLY | PENDING | r3 |
| L-14 | NeoCD-Libretro | `src/hlebios.cpp`、`cdromtoc.cpp`、`cdromcontroller.cpp` | libretro/neocd_libretro @ `b1e04c73…` | SHA-256 先頭 `e84e9809…`、`8c628fff…`、`3b4af97b…`（r4） | ファイル表示なし。ルート `LICENSE.md` は LGPL-3.0（r4） | ファイル単位は **UNKNOWN** | 68000 / NEO-GEO 前提。static 組込みは未承認 | — | REFERENCE_ONLY | PENDING | r4 |
| L-15 | Cult-of-GBA BIOS | `bios_calls/math/{division,sqrt,arctan}.s` | Cult-of-GBA/BIOS @ `a30e9a96…` | SHA-256 先頭 `f0f8f423…`、`810d0ba2…`、`cd61cc12…`（r4） | ファイル表示なし。ルート `LICENSE` は MIT（r4） | ルート: DenSinH and fleroviux（r4） | ARM 命令列。HuC6280 へは書き直しが必要 | — | REFERENCE_ONLY（アルゴリズム概念のみ） | PENDING | r4 |
| L-16 | cc65 runtime | `libsrc/runtime/udiv.s`、`umul16x16r32.s`、`mulax3.s` | cc65/cc65 @ `71746c829e77f74c2b601a7d16821170c2610df3` | `udiv.s` blob `4115382c80c13a4f…`、1671 B（今回計測）。`umul16x16r32.s` `cfcf82d9…`、`mulax3.s` `3423796…`（既存記録） | ルート `LICENSE` は zlib 系（blob `88cacf14…`、808 B。今回、zlib License 文面を確認）。ファイル単位の表示は未確認（ヘッダに license 行なし） | `udiv.s`: Ullrich von Bassewitz 1998、`umul16x16r32.s`: 同 2010、`mulax3.s`: 同 2001（今回ヘッダ確認）。プロジェクト全体は cc65 authors（既存記録） | `zeropage.inc`、`ptr1`/`ptr3`/`sreg`/`ptr4`、`popptr1` を import（r3 §ca65） | — | ADAPTED 候補（zero-page・calling convention の監査が前提） | PENDING | 今回 blob 計測、r3 |
| L-17 | Geargrafx（試験用） | 実コア本体（`src/scsi_controller.cpp`、`cdrom.cpp` ほか） | drhelius/Geargrafx @ `b49ae82a…` | 既存記録 | GPL-3.0（既存記録） | Ignacio Sanchez 2024（既存記録） | 外部試験依存。ROM にリンクしない | — | REFERENCE_ONLY（外部試験のみ。再配布時は GPL-3.0 を遵守） | PENDING | cd-oss-survey |
| L-18 | ares（参照） | `ares/pce/pcd/io.cpp`、`scsi.cpp` | ares-emulator/ares @ `9408cb43…` | 既存記録 | ISC（既存記録。ファイル単位は未再読） | ares team（既存記録） | C++ エミュレータ | — | REFERENCE_ONLY | PENDING | cd-oss-survey |
| L-19 | GPL 系参照（採用不可） | MAME `pce_cd.cpp`（BSD-3、ファイル単位）、Mednafen / beetle-pce（GPL-2.0 以降） | 既存記録 | 既存記録 | 上記。MAME 全体は GPL | 既存記録 | — | — | REJECTED（コード）／REFERENCE_ONLY（仕様のみ、コピー・翻訳なし） | PENDING | r3、cd-oss-survey |
| L-20 | patch 系（除外） | `mathieulh/PCE_CD_BIOSES_NO_AUTOBOOT` の IPS | 既存記録 | — | 元 ROM に依存（著作権物の派生） | — | — | — | REJECTED（対象外） | — | cd-oss-survey |

注記:
- `SHA-256`（既存記録）と `blob`（Git オブジェクト SHA-1）は別のハッシュである。ファイル単位の完全な照合には、取得元の pinned 内容を再計算すること（r3 の検証手順）。
- 「今回計測」の値は本 PR の `gh api` 取得時点（2026-10-20）に限る。

## 2. LICENSE / NOTICE 監査

| 候補 | 確認した LICENSE / NOTICE | 結果 | 残課題 |
| --- | --- | --- | --- |
| pce-devel/huc | GitHub 判定 `NOASSERTION`（既存記録）。ルート LICENSE は旧来の作者の利用条件が明示されていない旨を述べる（r4） | 代表 SPDX を一つに定められない | `LICENSE_1_0.txt` を `include/hucc/` に同梱するかは人間判断。NOTICE 有無は**未確認** |
| mckayemu/hugo | `COPYING`（blob `ff408338…`、今回確認）。`LICENSE.gpl`、`LICENSE.bsd-modified` は**ツリーに無い**（今回確認） | modified BSD 例外の全文を pin から確定できない | 例外文の出典を別途確認するまで、L-09 は REFERENCE_ONLY に留める |
| cc65/cc65 | ルート `LICENSE`（blob `88cacf14…`、今回確認） | zlib 系。表示保持・改変表示の条件 | NOTICE の有無は**未確認**。ROM 出力時の表示方法を人間が決める |
| nugget | ルート `LICENSE` は MIT（既存記録） | MIT の表示保持が必要 | ファイル単位の表示は既存記録のみ |
| NeoCD-Libretro | ルート `LICENSE.md` は LGPL-3.0（既存記録） | ファイル表示なし | ファイル単位の権利者は UNKNOWN |
| Cult-of-GBA | ルート `LICENSE` は MIT（既存記録） | 表示保持が必要 | `bios.bin` の由来は UNKNOWN |

NOTICE ファイルの有無は、本 PR では Hu-Go! のツリーのみ今回確認した（`LICENSE` / `COPYING` 系のエントリのみ）。他候補は**未確認（UNKNOWN）**。

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

- 今回の `gh api` 計測は、L-01、L-09、L-10、L-11、L-12、L-16 の一部（blob・ヘッダ・ツリー）に限る。その他の値は r3 / r4 / cd-oss-survey の既存記録を引いたもので、本 PR で再計測していない。
- ファイル本文は、ヘッダと関連行の確認に留めた。コードを本文にコピーしていない。
- 「APPROVED」「RESTRICTED」の最終判定は人間が行う。本台帳の「提案」欄は判断材料にすぎない。
- SPDX は GitHub 判定や各ファイルのヘッダ文言から推定した識別子であり、法的評価ではない。

## 6. 検証記録（本 PR）

| 区分 | 結果 | 理由 |
| --- | --- | --- |
| `gh api` による pinned blob・ヘッダ・ツリー確認 | PASS（今回計測の項目のみ） | 上表「今回計測」の列に限る |
| 既存記録の再計測 | NOT_RUN | r3 / r4 / cd-oss-survey の値を引用。再計測は行っていない |
| `make check` / `make test` | NOT_RUN（本 PR は docs のみ。実行は PR 作成後の CI に委ねる） | ローカル実行は今回の手順に含めていない |
| 実コア（Geargrafx）での検証 | NOT_RUN | 本台帳は実装の検証ではない |

## 7. 残作業

1. L-09 の `LICENSE.bsd-modified` 全文を、pin 以外の出典（上流の別コミット等）から取得し、modified BSD 例外の条件を確定する。
2. L-08 `cdrom.asm` のベース（Hudson 原コード）の権利状況を確認する。確認できなければ REFERENCE_ONLY のまま。
3. L-14 の 3 ファイルの権利者を、リポジトリの履歴または上流への照会で特定する。
4. cc65 の zero-page 依存（L-16）と HuCC の推移依存（L-04〜L-07）を監査し、ABI 適合を Geargrafx 実コアで確認する（BLOCKED_ABI の解除は行わない）。
5. 各行の disposition を、人間のレビューで PENDING / APPROVED / RESTRICTED に確定する（承認者と日付を記録）。
6. Hu-Go! のツリーにある `AUTHORS`（pin、blob 未記録）は、本体が fpce（Bero 作）と Xpce（Hmmx）を基にし、CD エミュレーションに Nyef・Ixion の寄与があると記す。L-09 `pcecd.c` の権利者・来歴を照合する起点として確認する（今回は未読）。
7. HuCC の `LICENSE_1_0.txt` は `include/hucc/` に無いが、同一 pin の `examples/asm/elmer/LICENSE_1_0.txt`（blob `36b7cd93…`、1338 B）に Boost 1.0 全文がある。同梱の要否・出典の扱いを人間が判断する。
