# OSS 再利用監査 r3（Issue #105）: API 別候補表と出典・ライセンス

- 対象 Issue: [#105](https://github.com/mao2009/oss-pce-cd-bios/issues/105)（Refs のみ。Closes は使わない）
- 基準: `origin/main` = `c7c1d33`（2026-10-16 に取得）
- 調査日: 2026-10-16。各ソースは `gh api` で pinned SHA の内容を取得し、SHA-256 を計算して確認した。
- 既存記録: [oss-audit-systemcard.md](oss-audit-systemcard.md)、[cd-oss-survey.md](cd-oss-survey.md)、[reuse-inventory.md](reuse-inventory.md)。本書は既存記録と重複する事実を繰り返さず、今回確認した差分のみを追加する。
- 位置づけ: **調査のみ**。コードのコピー・翻訳・ROM への取り込みは行っていない。全候補は承認前であり、本書の判定は採用承認ではない。
- 判定区分: そのまま再利用可能 / 改変して再利用可能 / 仕様やテストの参考のみ / 採用不可 / ライセンス判断待ち。
- 「未確認」は確認できなかった項目を示す。推測で埋めていない。

## 1. 今回確認したソース（pinned）

| ソース | リポジトリ / SHA | ファイル | SHA-256 / blob | 今回の確認 |
| --- | --- | --- | --- | --- |
| HuCC pcengine.inc | pce-devel/huc `54f5c73606c1e7142e8095c727cab2767e2b3cb3` | `include/hucc/pcengine.inc` | `b6a25d33…728a9`（既存記録と一致） | `cd_*` ベクタ名、未文書化 RAM 変数 |
| HuCC system card wrapper | 同上 | `include/hucc/hucc-systemcard.asm` | `4258b7c3…c7ea9`（既存記録と一致） | CD 呼び出し側の戻り値・MPR 退避 |
| HuCC startup | 同上 | `include/hucc/core-startup.asm` | `fa340fca…5b37a`（既存記録と一致） | MPR マップ表、`cd_base` 呼び出し |
| HuCC stage1 | 同上 | `include/hucc/core-stage1.asm` | 未計測（今回は参照のみ） | MPR7 の扱い |
| HuC elmer cdrom | 同上 | `examples/asm/elmer/include/cdrom.asm` | `a1de6902…ce0d`、blob `d8f304ed…a393`（今回初計測） | SCSI 層のリトライ・タイムアウト |
| Hu-Go! pcecd.c | mckayemu/hugo `64ed226da2288738781743eeef228c069e8973a4` | `pcecd.c` | `7ada6b45…cdd9`（既存記録と一致） | セクタ転送経路、タイムアウト有無 |
| Hu-Go! cd.c | 同上 | `cd.c` | `31229a81…feb0`（既存記録と一致） | 時刻変換のみ（60 行） |
| PCSX-Redux nugget OpenBIOS | pcsx-redux/nugget `c950e18a168944ec2d4e6d3c408fc224317483a7` | `openbios/cdrom/cdrom.c` | `312a415e…ca0b`、blob `6ce570ad…`（今回初計測） | 各ファイルのヘッダ、待機処理 |
| 同上 | 同上 | `openbios/cdrom/statemachine.c` | `b49830ea…40b3`（今回初計測） | 初期化の有界待ち |
| 同上 | 同上 | `openbios/cdrom/helpers.c` | `d7ee3bb3…0596`（今回初計測） | ヘッダのみ確認 |
| 同上 | 同上 | `LICENSE` | blob `490e7cb3…`、GitHub 判定 SPDX `MIT` | リポジトリ全体のライセンス |

注記:
- pce-devel/huc の GitHub 判定 SPDX は `NOASSERTION`（今回確認）。リポジトリのライセンスを機械的に一つのSPDXとして扱えないことの裏付けとなる。
- `cdrom.asm` と `pcengine.inc` の `gh api` 内容の SHA-256 は、既存マニフェストに無いファイルについて今回初めて計算した。マニフェスト（`api_contracts.json` の `sources`）への登録は本 PR の範囲外とし、必要なら別途行う。

## 2. API 別 OSS 再利用候補表

凡例: **判定**は §3 のライセンス監査と併せて読む。「ライセンス判断待ち」は、技術的に有用でもライセンス・出典が承認されるまで ROM へ取り込まないことを意味する。

| API（候補スロット） | 候補（ソース） | 今回確認した事実 | 判定 | 移植難易度 | ABI 依存 | 実装削減効果（推定・未計測） |
| --- | --- | --- | --- | --- | --- | --- |
| CD_RESET（`$E003`, slot `$01`） | HuC `cdrom.asm` | `cdr_reset` 後、`.wait_busy` で 16bit カウンタ 1024 回まで SCSI BSY 解除を待つ。尽きたら `CDERR_NO_DRIVE`。その後 `.wait_ready` で `cdr_stat` を回す。 | 仕様やテストの参考のみ（コピーはライセンス判断待ち） | 中 | 高（HuC の `call`/`cdr_*` 規約） | 中。待ち回数の設計例として有用 |
| CD_RESET | HuC `pcengine.inc` | `cd_reset = $00:E003`。未文書化の `cd_not_rdy`（`$F8:2247`, CDERR_NOT_READY 時にセット）、`cd_searching`（`$F8:2248`）の名前あり。 | 仕様やテストの参考のみ | — | — | 低（定義の確認のみ） |
| CD_RESET | Hu-Go! `bios.c` / `cd.c` | 今回は未再読（`cd.c` は時刻変換のみ、60 行で確認）。 | 採用不可（GPL-2.0） | — | — | — |
| CD_BASE（`$E006`, slot `$02`） | HuC `core-startup.asm` | `SUPPORT_2ISO` 時のみ呼び出し。`_al`=最終トラック（tnomax）、`_bh`=`$80`、`_cl`=2 をセットしてから `cd_base`。 | 仕様やテストの参考のみ（呼び出し例） | — | 高 | 低。引数の使用例に限る。意味・戻り値は**未確認** |
| CD_BASE | Hu-Go! | 未確認（今回の対象外） | — | — | — | — |
| CD_READ（`$E009`, slot `$03`） | HuC `hucc-systemcard.asm` `cd_loaddata` | 8KB 単位で分割して `cd_read` を呼ぶ。`_dh=0` でバイト指定。戻り値は X（0 以外でエラー）。成功時のみ `_add_sectors`。 | 仕様やテストの参考のみ | 低（呼び出し側） | 高（PCEAS/HuCC マクロ） | 低。これは System Card 機能の呼び出し側であり、本体の代替にならない |
| CD_READ | HuC `hucc-systemcard.asm` `cd_loadbank` | エラー後に MPR3 を退避値で復元する。コメントに「System Card の CD エラー時に値を破壊するバグ」と記載。 | 仕様やテストの参考のみ | — | 高 | — 既存記録の MPR3 仮説の**出典確認**であり、新しい実測ではない |
| CD_READ | HuC `cdrom.asm`（SCSI READ(6) 層） | `.retry_command` で `scsi_initiate` からやり直すリトライ経路が複数あり（例: 行 807–829 付近）。タイムアウト時 `bra scsi_initiate`（行 496 付近）は、その節の中では試行回数の上限が見えない。 | ライセンス判断待ち（ベースの出所不明。Boost-1.0 は改変部のみ） | 中〜高 | 高（HuC6280 の PCEAS 記法） | 高（SCSI プロトコル層そのものを含むため、承認されれば削減効果は最大）。ただしリトライ上限は**自前で設計すること** |
| CD_READ | Hu-Go! `pcecd.c` | `$1801` のセクタ数でセクタ単位に読む。`cd_sectorcnt==0` ではログ出力を確認（後続の分岐は未確認）。grep の結果、タイムアウト・待ち・リトライの識別子は見つからなかった。 | ライセンス判断待ち（BSD 例外あり。著作権者行なし） | 中 | 高（Hu-Go! の内部 API） | 低〜中（HLE 的で、実機のタイミングの参照には弱い） |
| CD_READ | PCSX-Redux nugget `cdrom.c` | `waitForCDRomIRQCompletion()` は `while (irqs < 5)` で**上限なし**に IRQ 完了を待つ（行 138–150）。 | 仕様やテストの参考のみ（PSX ABI）。タイムアウト無しの例として避けるべき見本 | — | 高（PSX の CDROM レジスタ） | 低 |
| CD_SEEK（`$E00C`, slot `$04`） | HuC `pcengine.inc` のみ | `cd_seek = $00:E00C` の定義のみ。`hucc-systemcard.asm`、`core-*.asm` に `cd_seek` の呼び出しは見当たらなかった（ファイル内 grep）。 | 未確認（候補なし） | — | — | — |
| CD_EXEC（`$E00F`, slot `$05`） | HuC `pcengine.inc` のみ | `cd_exec = $00:E00F` の定義のみ。ラッパ・呼び出し無し（同上）。 | 未確認（候補なし） | — | — | — |
| 起動時の MPR 管理 | HuC `core-startup.asm`（コメント表） | HuCARD: MPR0=`$FF`、MPR1=`$F8`、MPR2–6 がバンク `$00`–`$04`、MPR7=`$00`。CD-RAM 構成: MPR2–6=`$80`–`$84`、MPR7=`$80` または System Card バンク `$00`。`core_boot` は bank 2 を MPR7 に入れる。 | 仕様やテストの参考のみ（HuCC ランタイム構成であり System Card の既定値ではない） | — | 高（HuCC のカーネル配置前提） | 中（起動時のマップ表の検証観点として有用） |
| 起動時の MPR 管理 | PCSX-Redux nugget | PSX の OpenBIOS。MPR に相当する概念なし。 | 対象外 | — | — | — |
| セクタ転送・タイムアウト | Hu-Go! `pcecd.c` | 上記。タイムアウト処理は確認できず（grep 上）。 | ライセンス判断待ち | — | — | — |
| セクタ転送・タイムアウト | nugget `statemachine.c` | 初期化の待ちは `int wait = 30000` の有界カウンタ（行 766–768）。 | 仕様やテストの参考のみ（MIT。ヘッダ確認済み） | 低 | 高（PSX） | 中（有界待ちの設計パターンの見本） |

## 3. 出典・ライセンス監査結果

### 3.1 候補別

| 候補 | 対象ファイル | 著作権者（ヘッダ） | ファイル単位ライセンス | リポジトリ / LICENSE | 残る未確認 | 判定 |
| --- | --- | --- | --- | --- | --- | --- |
| HuCC `include/hucc/`（pce-devel/huc） | `hucc-systemcard.asm`、`core-startup.asm`、`core-stage1.asm`、`pcengine.inc`、`core.inc` | John Brandwood（2021–2024）。`pcengine.inc` は著作権者行なし（既存記録） | BSL-1.0（ヘッダ。既存記録、今回再確認） | GitHub 判定 `NOASSERTION`（今回確認）。ルート LICENSE は混在（既存記録） | 推移的依存、ベースの系譜 | ライセンス判断待ち（コピー・改変）。事実の参照は可 |
| HuC `examples/asm/elmer/include/cdrom.asm` | `cdrom.asm` | ヘッダに Hudson 原コードからの派生、改変部は John Brandwood 2022 | 改変部のみ Boost-1.0（ヘッダ。今回確認）。ベースは出所不明 | 上記と同じ `NOASSERTION` | ベースの権利、Hudson 原コードの範囲 | ライセンス判断待ち |
| Hu-Go! `bios.c`、`cd.c` | — | ヘッダに著作権者行なし（既存記録） | `COPYING` により GPL-2.0 が既定（既存記録） | `COPYING` は GPL-2.0 既定。`LICENSE.gpl` は pin に無い（既存記録） | — | 採用不可（MIT ROM への取り込み不可） |
| Hu-Go! `pcecd.c` | — | 著作権者行なし。BSD 本文のみ（既存記録） | Modified BSD（`COPYING` の例外。既存記録） | ライセンス本文は pin に無い（既存記録） | 著作権者の特定 | ライセンス判断待ち |
| PCSX-Redux nugget `openbios/cdrom/*.c` | `cdrom.c`、`statemachine.c`、`helpers.c` | 「Copyright (c) 2020 PCSX-Redux authors」（ヘッダ、今回確認） | MIT（ヘッダ、今回確認） | リポジトリ LICENSE は MIT（GitHub 判定、今回確認）。別リポジトリの `pcsx-redux` は GPL-2.0（既存記録） | 他ファイルの依存、`openbios` 内の混在有無 | 仕様やテストの参考のみ（MIT で改変・再配布の条件は明確だが、PSX ABI のため即利用可能ではない） |
| ares `ares/pce/pcd/io.cpp`、`scsi.cpp` | — | ares team（既存記録） | ISC（既存記録） | 既存記録 | 今回未再読 | 仕様やテストの参考のみ |
| MAME `src/mame/nec/pce_cd.cpp` | — | Wilbert Pol / Angelo Salese（既存記録） | ファイル単位 BSD-3（既存記録） | MAME 全体は GPL（既存記録） | 今回未再読 | ライセンス判断待ち（参照のみ） |
| Geargrafx | — | Ignacio Sanchez（既存記録） | GPL-3.0 以降（既存記録） | GPL-3.0（既存記録） | — | 採用不可（ROM へ取り込まない。試験用の外部依存のみ） |
| Mednafen / beetle-pce | — | 既存記録 | GPL-2.0 以降（既存記録） | — | — | 採用不可 |
| NeoCD-Libretro | — | 既存記録（今回未再読） | LGPL-3.0（既存記録） | — | — | ライセンス判断待ち |
| Ootake | — | — | — | GitHub 検索で権威あるアップストリームを特定できず（`gh search repos` の結果は無関係なフォーク中心） | 上流の特定 | 未確認 |

### 3.2 ライセンス上の注意（今回の確認で追加された点）

1. **pce-devel/huc は GitHub 判定が `NOASSERTION`**。ファイル単位の BSL・Boost・BSD の混在を、リポジトリラベルで代表させてはならない。
2. **nugget は MIT が一貫している**。ただし PSX ABI のため、技術的に流用できる部分は限られる。MIT の条件（著作権表示と許諾文の保持）は採用時に必ず満たす。
3. **BSL-1.0 と Boost-1.0 は同一内容の別名として扱ってよいかは未確認**。既存記録は BSL と書いており、今回もそのまま扱う。
4. **ライセンス判断は人間が行う**。本書は判定の材料であり、承認ではない。

## 4. 実装優先順位への提案

前提: 既存の方針（独自実装を基本とし、承認された OSS 再利用を優先する）に従う。すべて人間の承認待ち。

1. **CD_RESET / CD_READ のタイムアウトは「有界」かつ「試行上限付き」で設計する**。
   根拠: HuC `cdrom.asm` は 16bit 有界カウンタ（1024 回）を使うが、リトライ経路の一部に試行上限が見えない。nugget の IRQ 待ちは上限なし。両方の欠点を避けた設計にする。コードは書かず、仕様として `abi-contracts.md` の CD_RESET / CD_READ 節へ反映する候補。
2. **HuC `cdrom.asm` の SCSI 層（READ(6)、REQ/ACK）は、ライセンス判断が下りれば最大の削減効果が見込める**。ただし現時点では判断待ち。判断が下りるまでは、既存記録の REFERENCE_ONLY を維持する。
3. **System Card 呼び出し側（`hucc-systemcard.asm`）は実装の代替にならない**。CD_READ の引数規約と MPR3 退避の観点を、テスト設計（API 契約テスト）の参考にとどめる。
4. **起動時 MPR の検証には HuCC の構成表を仕様参照として使う**。ただし、System Card 既定値との一致は Geargrafx 実コアで確認するまで仮説とする。
5. **nugget は、MIT の許諾が明確なため将来の設計・テストの参考候補**。PSX ABI に依存しない有界待ちのパターンに限って参照する。
6. **Hu-Go! `bios.c`、`cd.c`、Geargrafx、Mednafen は採用対象から外す**（GPL）。

## 5. 未確認項目（一覧）

- CD_SEEK / CD_EXEC の System Card 側の意味・戻り値（pinned ソースに呼び出しが無い）。
- CD_BASE の `_al` / `_bh` / `_cl` の意味（HuCC の呼び出し例のみ）。
- Hu-Go! の CD_RESET 相当・CD_BASE 相当（今回未再読）。
- ares、MAME、NeoCD の今回の再確認（既存記録のみ）。
- HuC `cdrom.asm` のリトライ経路で、試行上限が上位ルーチンに存在するか（今回は当該節のみ確認）。
- nugget の他の `openbios` ファイルの依存と混在有無。
- Ootake の上流リポジトリ、ライセンス。
- 実機（PCE / System Card）での、リセット時間・タイムアウト値・未定義 RAM 変数の挙動。すべて未測定。
- 「実装削減効果」は推定であり、実測していない。

## 6. 検証

本変更は文書のみ（`docs/` 配下）。検証は次のとおり実施する（結果は最終報告に記載）。
- `make check`、`python3 -m unittest discover -s tests/host`、`git diff --check`。
- `make dev-cdread` の debug / release（本変更に影響しないが、念のため記録）。
