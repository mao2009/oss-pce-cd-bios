# OSS 再利用監査 r4（Issue #105）: r3 で未監査だった候補の補完

- 対象 Issue: [#105](https://github.com/mao2009/oss-pce-cd-bios/issues/105)（Refs のみ）
- 前提: [oss-reuse-audit-r3.md](oss-reuse-audit-r3.md)（CD 系 API・HuCC CD 周辺・nugget）を重複して再掲しない。本書は Issue の初期候補のうち r3 が扱っていない **HuCC math/VDC/入力/カーネル、NeoCD-Libretro、Cult-of-GBA BIOS** のファイル単位の確認結果のみ追加する。
- 調査日: 2026-10-16。各ファイルは `gh api`（raw）で pinned SHA の内容を取得し、SHA-256 の先頭 12 桁を記録した。
- 位置づけ: **調査のみ**。コードのコピー・翻訳・ROM への取り込みは行っていない。**全候補は「未承認 / human approval required」**であり、本書は採用承認ではない。ライセンス表示がないことは許諾ではない。
- 確認できなかった項目は UNKNOWN と記す。

## 1. 確認したソース（pinned）

| ソース | リポジトリ @ commit | ファイル（SHA-256 先頭12桁） | ヘッダ上の著作権・ライセンス（今回確認） |
| --- | --- | --- | --- |
| HuCC math | pce-devel/huc @ `54f5c73606c1e7142e8095c727cab2767e2b3cb3` | `include/hucc/hucc-math.asm`（618 行, `e259826d63a1`） | John Brandwood 2021-2024、Boost Software License 1.0 |
| HuCC VDC | 同上 | `include/hucc/vdc.asm`（1266 行, `4cc4c903dae5`） | John Brandwood 2021-2025、Boost 1.0。`include "common.asm"`、`"vce.asm"` に依存 |
| HuCC joypad | 同上 | `include/hucc/joypad.asm`（1103 行, `7c867b777aa0`） | John Brandwood 2019-2025、Boost 1.0 |
| HuCC kernel | 同上 | `include/hucc/core-kernel.asm`（640 行, `ae183017c59c`） | John Brandwood 2021-2024、Boost 1.0。`include "joypad.asm"`（行 452）。System Card 互換の IRQ 設計（`irq1_hook`） |
| HuCC 依存 | 同上 | `include/hucc/common.asm`（295 行, `c16d337cbe81`）、`include/hucc/vce.asm`（658 行, `6a88c2e90ea3`） | 各ファイル冒頭に John Brandwood、Boost 1.0 の表示を確認（`common.asm` 2021-2024、`vce.asm` 2021-2025）。内容の再配布可否は未判断 |
| NeoCD-Libretro | libretro/neocd_libretro @ `b1e04c738cb48a1dae0574b8877f6a116d270ca1` | `src/hlebios.cpp`（`e84e98091500`）、`src/cdromtoc.cpp`（`8c628fffa443`）、`src/cdromcontroller.cpp`（`3b4af97b6d76`） | **3 ファイルとも冒頭に著作権・ライセンス表示なし**（`#include` から開始）。ルート `LICENSE.md` は LGPL-3.0（FSF の本文）。GitHub 判定 SPDX は `NOASSERTION` |
| Cult-of-GBA BIOS | Cult-of-GBA/BIOS @ `a30e9a96df083628b650724b7d4d7112b4070b98` | `bios_calls/math/{division,sqrt,arctan}.s`（`f0f8f4230cc4`、`810d0ba2f11b`、`cd61cc12a544`） | ファイル内に表示なし。ルート `LICENSE` は MIT「Copyright 2020 - 2021 DenSinH and fleroviux」。GitHub 判定 `MIT` |

追加で判明した事実:
- pce-devel/huc のルート `LICENSE`（blob `473965af…`）の冒頭は、HuC SDK は出自の異なる複数の資料に基づき、旧来の作者の多くが利用条件を明示していない旨を述べる。したがって上表の Boost 1.0 は**当該ファイルのヘッダ表示**の事実にとどまり、リポジトリ全体や依存ファイルへの許諾を意味しない（ライセンス上の評価は UNKNOWN、人間が判断）。
- pin 内の `LICENSE_1_0.txt` は `examples/asm/elmer/` と `src/tools/*` の下にのみ存在（blob `36b7cd93…`、同一内容）。ヘッダが指す「accompanying file」は `include/hucc/` の隣には**存在しない**。ライセンス本文の同梱方法は UNKNOWN で、採用時は人間が確認する。
- Cult-of-GBA のルートに **`bios.bin`（バイナリ）** がある。由来・派生関係は UNKNOWN。本件では参照も取り込みも対象外とする。

## 2. API 別の候補判定（r3 の補完）

| API / 領域 | 候補 | 事実 | 判定 | ABI 依存・ブロッカー |
| --- | --- | --- | --- | --- |
| 計算系（乗除算） | HuCC `hucc-math.asm` | SDCC 互換、Y:A を主レジスタとする低速な 8/16bit 乗除算（ヘッダ記載）。Boost 1.0。 | ヘッダ上は Boost 1.0 だが、再利用の可否は UNKNOWN（**未承認 / human approval required**）。System Card の該当 API の引数・戻り値との適合は未検証 | 中。ABI の差の吸収が必要。本文同梱方法（上記）が未解決。実コア検証なし |
| VDC | HuCC `vdc.asm` | 恒久マップ領域への配置が前提（ヘッダ記載）。`common.asm`、`vce.asm` に依存。TIA 転送のサイクル依存。 | 仕様やテストの参考のみ（取り込みは**未承認**）。依存と TIA タイミングの適合を人が判断 | 高。依存ファイルを含む追加監査が必要 |
| 入力 | HuCC `joypad.asm` | 2/6 ボタン・マウス・マルチタップ。HuC ライブラリ比のサイクル数を記載。 | ヘッダ上は Boost 1.0 だが、再利用の可否は UNKNOWN（**未承認 / human approval required**）。System Card の `JOY_*` 契約との適合は未検証 | 中〜高。HuCC 独自のポート配列・メモリ配置 |
| 起動・IRQ | HuCC `core-kernel.asm` | System Card 互換を意図した `irq1_hook` 設計。HuCARD は MPR7、CD は MPR1 常駐。VDC 割り込みハンドラは System Card のものから変更されている（ヘッダ記載）。 | 仕様やテストの参考のみ（**未承認**）。ROM の割り込み契約と食い違う可能性があるため、直接取り込みは推奨しない | 高 |
| CD 系 API（NeoCD） | NeoCD-Libretro `hlebios.cpp` / `cdromtoc.cpp` / `cdromcontroller.cpp` | NEO・GEO CD の HLE と CD 状態機械。ファイル冒頭に権利表示なし。LGPL-3.0（ルート）。 | 仕様やテストの参考のみ。ROM への取り込みは**採用不可に近い（LGPL の組込み形態評価と別許諾が必要）／human approval required** | 高。68000 の NeoGeo ABI で PCE の ABI と無関係。ファイル単位の著作権者は UNKNOWN |
| 計算系（除算・平方根・逆正接） | Cult-of-GBA `bios_calls/math/*.s` | ARM 命令列。ファイル内表示なし、ルートは MIT（DenSinH, fleroviux）。 | 仕様やテストの参考のみ（アルゴリズム概念の参照。HuC6280 にはそのまま使えない）。MIT の表示保持条件を採用時に履行 | 高（ARM。移植は書き直し）。**未承認** |

## 3. ライセンス上の注意（追加分）

1. 「ファイルにヘッダがない」ことは、リポジトリ全体のライセンスが当該ファイルに及ぶと自動的に確定することも、許諾がないと確定することも意味しない。NeoCD のファイル単位の権利は UNKNOWN とし、人間が判断する。
2. Boost 1.0 のヘッダが指す `LICENSE_1_0.txt` が `include/hucc/` に無い。再利用する場合は、Boost 本文と著作権表示を NOTICE として同梱する運用を人間が決める。
3. 本書は Issue の「GBA BIOS はアルゴリズムの参考」「NeoCD は LGPL」という既存記録と矛盾しない（[reuse-inventory.md](reuse-inventory.md) 参照）。

## 4. 受け入れ条件との対応（#105）

| 条件 | 状態 |
| --- | --- |
| 再利用優先規則の文書化と「独立実装のみ」の矛盾解消 | `CONTRIBUTING.md`「Reuse first」、`docs/provenance.md` で既に記載。リポジトリ内 grep（独立実装のみ／clean-room 系の記述）で矛盾する文言は見つからず。追加修正なし |
| 最初の候補のファイル単位ライセンス / 由来監査 | r3 と本書でヘッダレベルを実施。**依存・生成コードの完全な追跡は未了** |
| 各優先 API の候補 / 不採用理由 | r3（CD 系）と本書（計算・VDC・入力・IRQ）で記録。残る API（PSG、ADPCM、CD-DA、バックアップ RAM など）は**未調査（UNKNOWN）** |
| 採用コードの出典・commit・改変履歴 | 採用なし。該当せず |
| 未承認・不明ライセンスの ROM への取り込み禁止 | 守られている（コード取り込みなし） |
| 実行証拠 | 実コア検証は**なし**。本変更は文書のみ |

## 4b. 検証記録（2026-10-16 再確認）

`gh api`（raw, `?ref=<pinned SHA>`）で取得し直し、全ファイルの SHA-256 先頭 12 桁と行数が上表と一致することを確認した。
- 確認済み: 上表のヘッダ文言（著作権年・Boost 1.0 の 3 行）を `hucc-math/vdc/joypad/core-kernel/common/vce.asm` で確認。URL 例: `https://github.com/pce-devel/huc/blob/54f5c73606c1e7142e8095c727cab2767e2b3cb3/include/hucc/vdc.asm`。`LICENSE_1_0.txt` の所在（`examples/asm/elmer/`、`src/tools/{pce2png,sym2inc,wav2vox}/`、blob `36b7cd93…`）と `include/hucc/` に無いことを tree API で確認。
- 確認済み（NeoCD @ `b1e04c73…`）: 3 ファイルとも冒頭が `#include` で権利表示なし、ルート `LICENSE.md` は LGPL-3.0、GitHub 判定 `NOASSERTION`。
- 確認済み（Cult-of-GBA @ `a30e9a96…`）: `division/sqrt/arctan.s` に copyright/license の語なし、ルート `LICENSE` は MIT「Copyright 2020 - 2021 DenSinH and fleroviux」、GitHub 判定 `MIT`、ルートに `bios.bin` あり。
- 未検証（UNKNOWN）: System Card API との ABI 適合、各行の「再利用可能」判定の法的評価、Cult-of-GBA の ARM コードがアルゴリズム参考以上に使えるか。上表の「改変して再利用可能」は Boost ヘッダ表示に基づく暫定記述であり、すべて**未承認 / human approval required**。

## 5. 未確認項目（BLOCKED / UNKNOWN）

- `common.asm`、`vce.asm` を含む `vdc.asm` / `core-kernel.asm` の推移的依存の完全な監査。
- Boost ライセンス本文の同梱方法（`include/hucc/` に本文なし）。
- NeoCD の 3 ファイルの著作権者、`3rdparty` 配下の混在。
- Cult-of-GBA `bios.bin` の由来。
- PSG・ADPCM・CD-DA・バックアップ RAM の候補調査。
- 機械可読な一覧（マニフェスト）への登録（本書は文書のみ。別途判断）。
