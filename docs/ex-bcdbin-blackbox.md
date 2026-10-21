# EX_BCDBIN（$3D / 候補 $E0B7）ブラックボックス試験の分類表

Issue #71。本書は main `c906728` を基点とした **EX_BCDBIN の試験資産と検証状態の分類表**である。
自作ルーチン `ex_bcdbin` の検証状態は #416 / PR #423（main 統合 `71bfa0f`）により更新済みで、詳細は `docs/ex-bcdbin.md` の「検証」節と「契約項目の確定状況」節が正である。
試験資産の棚卸し（§1）は、ハーネスと `src/ex_bcdbin.s` の由来である main `1cd5582` を基準とする。

純正 BIOS の `EX_BCDBIN` は**未確定**（`spec/api_contracts.json` slot 61 の契約はすべて UNKNOWN、エントリ `$E0B7` は HYPOTHESIS）であり、
本書は純正 BIOS の挙動を一つも主張しない。本書は純正 BIOS の ABI を VERIFIED とも System Card 互換とも主張しない。
純正 BIOS のルーチンを実行する試験は承認されていないため、**BLOCKED** とする。
本書で「期待値」と呼ぶのは、本プロジェクトが独自に定めた仕様（`docs/ex-bcdbin.md`）に基づく値だけである。
Geargrafx の出力を純正 BIOS の期待値として扱うことはしない。

## 1. 既存の EX_BCDBIN 資産（棚卸し）

| 資産 | 内容 | 状態 |
| --- | --- | --- |
| `src/ex_bcdbin.s` | 独自仕様の内部ルーチン（packed BCD 1 バイト → 0〜99、不正は C=1 で A 不変） | 実装済み（#328 マージ）。`src/overrides/slot_3D.s` 経由で DEV ROM の `$3D` 上書きに接続（20afb23、ABI UNVERIFIED、release_eligible: false） |
| `docs/ex-bcdbin.md` | 独自仕様、検証方法、契約項目の確定状況（監査表） | マージ済み（#328、#382、#416 の条件マトリクス節） |
| `tests/host/test_ex_bcdbin.py` | 参照モデル（全256入力、境界値、BIN→BCD→BIN 往復） | `make test` で実行（ホスト専用） |
| `tests/fixtures/bcdbin_harness.s`、`bcdbin.cfg` | 全256入力を呼ぶ HuCARD ハーネス（非 BIOS） | マージ済み |
| `tests/integration/geargrafx_bcdbin.py` | 実コア実行。cold/warm 両方で全256入力、変異3種（陽性対照つき） | `make test-real-core` と CI で実行 |
| `tests/fixtures/bcdbin_cond_harness.s`、`tests/integration/geargrafx_bcdbin_conditions.py` | 入力 C・X/Y・呼出時 SP を変える条件マトリクス（#416） | main 統合済み（71bfa0f） |
| `tests/integration/test_ex_bcdbin_real_core.py` | 実コア経路の負例 5 件（core 不在、ca65/ld65 不在、起動失敗、タイムアウト） | `make integration-test`、Linux のみ完全 |
| `.github/workflows/core-bcdbin.yml` | 専用ワークフロー（主たる実コア証拠） | main にマージ済み |
| `docs/api-inventory.md` | `$3D` / `$E0B7` / EX_BCDBIN の台帳行 | 既存 |

関連 Issue・PR：#328（独自仕様の追加、マージ）、#382（契約項目の監査、マージ）、#416 / PR #423（入力 C・X/Y・呼出時 SP の実コア試験、マージ）。
#410 のブランチには触れていない。

### 1.1 検証の根拠（リポジトリ内で再確認できるもの）

| 根拠 | 結果 | 確認方法 |
| --- | --- | --- |
| CI run `39543895624`（head `d777e65`、本 PR の docs のみの版） | `bcdbin-core` pass、`guard-bcdbin-core` pass（全256入力 cold/warm、変異3種の実コア試験） | `gh run view 39543895624` |
| ホスト試験（本 HEAD、Windows） | `tests.host.test_ex_bcdbin`：3 件 OK | `python -m unittest tests.host.test_ex_bcdbin -v` |
| 実コア負例（本 HEAD、Windows） | `tests.integration.test_ex_bcdbin_real_core`：5 件中 3 件 OK、2 件 SKIP（cc65 不在） | `python -m unittest tests.integration.test_ex_bcdbin_real_core -v` |
| 実コア 256 入力の再実行 | 本セッションでは行わない（Windows では BLOCKED、終了コード 3）。証拠は上記 CI run | `python tests/integration/geargrafx_bcdbin.py` を Linux で実行 |

## 2. 分類の定義

- **HOST_FIXTURE**：Python の参照モデルに対する試験。Geargrafx を使わない。
- **REAL_CORE_OSS**：本プロジェクトの ROM（ハーネス）を Geargrafx で実行する試験。純正 BIOS ではない。
- **REFERENCE_LOCAL**：純正 BIOS のルーチンを承認済みの手順で観測した結果。**本書では 0 件**。
- **BLOCKED**：期待値が UNKNOWN、または純正 BIOS の実行が未承認のため実行しない。

## 3. 分類表

期待値の根拠（basis）の略号：`SPEC` = `docs/ex-bcdbin.md` の独自仕様、`UNK` = 未確定（`api_contracts.json`）。

### 3.1 純正 BIOS EX_BCDBIN（候補 `$E0B7`）

| ID | シナリオ | 区分 | 期待値 | basis | 状態 |
| --- | --- | --- | --- | --- | --- |
| R-01 | `$E0B7` の呼出方法（引数レジスタ） | BLOCKED | UNKNOWN | UNK | 未実行（JSR 未承認） |
| R-02 | 戻り値（A、C）の意味 | BLOCKED | UNKNOWN | UNK | 未実行 |
| R-03 | 破壊・保存レジスタ、P フラグ | BLOCKED | UNKNOWN | UNK | 未実行 |
| R-04 | 不正 BCD 入力時の挙動（エラー通知方法） | BLOCKED | UNKNOWN | UNK | 未実行 |
| R-05 | MPR / バンク前提、スタック使用量 | BLOCKED | UNKNOWN | UNK | 未実行 |
| R-06 | D フラグ = 1 時の挙動 | BLOCKED | UNKNOWN | UNK | 未実行 |
| R-07 | 候補エントリ `$E0B7` の実在（jump table 経由の到達） | BLOCKED | UNKNOWN | HYPOTHESIS のみ（`pcengine.inc` のソース一致、実行証明なし） | 未実行 |

R-01〜R-07 の BLOCKED の根拠：純正 `$E0B7` への JSR は、安全性の前提である #173（JSR 監視の fail-closed 境界）と #228（実行 gate）の承認が前提である（`docs/ex-binbcd-measurement-plan.md` 15 行・52 行、`docs/ex-binbcd-blackbox-cases.md` 73 行と同種の gate。いずれも `$E0B4`（EX_BINBCD）向けの計画であり、`$E0B7` に直接適用される文書ではない）。
ABI の確定（呼出規約・戻り値・破壊レジスタ）には、オーナー判断 #324（ABI 証拠の 3 軸分離と DEV 専用実装台帳の導入可否、OPEN）が必要である。
本 PR では承認が与えられていないため、R-01〜R-07 は実行しない。

### 3.2 独自仕様ルーチン `ex_bcdbin`（正常・境界・エラー）

入力は packed BCD 1 バイト（上位ニブル = 十の位、下位ニブル = 一の位）。
期待値：有効な入力は C=0、A = 十の位 × 10 + 一の位（0〜99）。どちらかのニブルが 9 を超える入力は C=1、A は入力のまま。

| ID | シナリオ | 代表入力 | 区分 | 期待値（basis） | 状態 |
| --- | --- | --- | --- | --- | --- |
| N-01 | 最小正常値 | `$00` | REAL_CORE_OSS + HOST_FIXTURE | A=0, C=0（SPEC） | 実行済み（全256） |
| N-02 | 最大正常値 | `$99` | REAL_CORE_OSS + HOST_FIXTURE | A=99, C=0（SPEC） | 実行済み（全256） |
| N-03 | 有効 100 件の全件 | `$00`〜`$99` のうち十・一ともに 0〜9 | REAL_CORE_OSS + HOST_FIXTURE | 100 件 C=0（SPEC） | 実行済み（全256） |
| B-01 | 十の位の境界（有効） | `$09`、`$10`、`$19`、`$90` | REAL_CORE_OSS + HOST_FIXTURE | `$09`=9、`$10`=10、`$19`=19、`$90`=90（SPEC） | 実行済み（全256） |
| B-02 | 一の位の境界（無効） | `$0A`、`$0F`、`$1A` | REAL_CORE_OSS + HOST_FIXTURE | C=1、A 不変（SPEC） | 実行済み（全256） |
| B-03 | 十の位の境界（無効） | `$9A`、`$A0`、`$A9`、`$FF` | REAL_CORE_OSS + HOST_FIXTURE | C=1、A 不変（SPEC） | 実行済み（全256） |
| E-01 | 一の位のみ不正（十の位 0〜9） | 60 件（十 0〜9 × 一 A〜F） | REAL_CORE_OSS + HOST_FIXTURE | C=1、A 不変（SPEC） | 実行済み（全256） |
| E-02 | 十の位のみ不正（一の位 0〜9） | 60 件（十 A〜F × 一 0〜9） | REAL_CORE_OSS + HOST_FIXTURE | C=1、A 不変（SPEC） | 実行済み（全256） |
| E-03 | 両方不正 | 36 件（十 A〜F × 一 A〜F） | REAL_CORE_OSS + HOST_FIXTURE | C=1、A 不変（SPEC） | 実行済み（全256） |
| I-01 | 入力 C の影響（入力 C = 0 / 1 で同じ出力） | 条件マトリクスの入力 C = 0 と 1 | REAL_CORE_OSS（制御あり） | 出力は入力 C に依存しない（SPEC の入力条件に含まれない） | 実行済み（#416、各条件 16 入力、cold/warm。手動実行で CI・Makefile 対象外） |
| I-02 | X / Y の保存（呼出前後の値） | 条件マトリクスの 6 組（`CASES` 参照。例：`$5A`/`$A5`、`$00`/`$00`、`$FF`/`$FF`、`$12`/`$34`、`$AB`/`$CD`、`$00`/`$FF`） | REAL_CORE_OSS（制御あり） | X、Y 不変（SPEC） | 実行済み（#416、列挙した 6 組のみ。手動実行で CI 対象外。任意の X/Y 値は未確認） |
| I-03 | スタックの均衡と SP の復元 | 256 回の反復後 SP=`$FF` | REAL_CORE_OSS | SP 不変、スタック以外に書かない（SPEC） | 実行済み |
| I-04 | 呼出時の SP | SP=`$FF`、`$80`、`$C0`（条件マトリクス） | REAL_CORE_OSS（制御あり） | 各呼出の直後に SP 不変（SPEC） | 実行済み（#416、列挙した 3 値のみ。手動実行で CI 対象外） |
| U-01 | D フラグ = 1 | 任意 | BLOCKED | 仕様上未定義（SPEC） | 未実行（期待値なし） |
| U-02 | N / Z / V フラグ | 任意 | BLOCKED | 仕様上未定義（SPEC） | 未実行（期待値なし） |

HOST_FIXTURE 側の補足：`test_model_matches_oracle_for_all_256_inputs`（参照モデルと独立な式の一致）と `test_boundaries`（§3.2 の境界値）で、N/B/E の期待値をホストでも確認する。
REAL_CORE_OSS 側：全256入力を cold と warm の 2 回の実コア実行で確認する（計 512 観測）。
実コアの RAM 比較は `check_ram` が行い、変異3種が必ず不一致になることも確かめる（陽性対照つき）。
条件マトリクス（#416）は、入力 C（0 / 1）、X/Y（6 組）、呼出時 SP（`$FF` / `$80` / `$C0`）を変えて、8 条件 × 16 入力 × cold/warm を実コアで確認する。全 256 入力ではない。

## 4. 未カバー（ギャップ）

1. **条件マトリクスの網羅範囲**：入力 C（0 / 1）、X/Y（6 組）、SP（3 値）は列挙した値のみであり、任意の X/Y 値や任意の SP 値について主張しない。各条件の入力は 16 個である。
2. **純正 BIOS（R-01〜R-07）**：すべて BLOCKED。期待値は UNKNOWN で、観測しても比較できない。
3. **D = 1（U-01）、N/Z/V（U-02）**：仕様上未定義のため、期待値を持たない。

## 5. 記録（実行・固定値）

すべて Geargrafx / 純正 BIOS を含まない ROM の記録である。純正 BIOS の期待値ではない。

- ハーネス（`tests/fixtures/bcdbin_harness.s`）の起動時設定：`cld`、`ldx #$ff` / `txs`（SP=`$FF`）、MPR0=`$FF`（`tam #$01`、I/O）、MPR1=`$F8`（`tam #$02`、作業 RAM）。X=`$5A`、Y=`$A5` で `ex_bcdbin` を呼ぶ。
- RAM 配置（反復 n、入力 n、作業 RAM 基準 `$2206`、`bcdbin_harness.s` 27〜33 行）：`$2206+n` = A、`$2306+n` = 戻り時の P、`$2406+n` = X（`$5A`）、`$2506+n` = Y（`$A5`）。`$2000` は最終的に 0、`$2001` は最後の X（`$5A`）。
- 終了時（`bcdbin_harness.s` 36〜42 行）：SP を `$2606` に書く（`$FF`）。`tii` で `marker`（`"PCE!"`）を `$2200`〜`$2203` にコピーし、`$2204` に `$F8`（`tma #$02` = MPR1 の値）、`$2205` に `$5A` を書く。
- 副作用：スタック page 1 への書込（PHX/PHY の 2 バイトを反復ごとに戻す）。作業 RAM 以外への書込はない。
- 入口アドレス（`ex_bcdbin` = `$E053`、JSR `$E018`）はビルド生成物 `rom.map` の値であり、リポジトリには含まれない。本書では再確認していない。
- ハッシュ（`git show <commit>:<path> | sha256sum` で再現可能なブロブのハッシュ）：
  - `src/ex_bcdbin.s` の main `c906728` 版：`b38a14aa873a8552288062cb5dd1a23f71e95211cadc4c5b048c0d1b3005f858`（`1cd5582` 版は `2ab6cf7c…fe2049`。20afb23 で変更）
  - `tests/fixtures/bcdbin_harness.s`：`86bac707132764d2906bfd21d4badae14ef742e96a7ea7f757502e19fca57095`
  - `tests/fixtures/bcdbin.cfg`：`c8f19226d8afa2e4f3afe3e493da739528ae6d8f150bf7fa53d47c1b0f91dcde`
- 結果：§1.1 のとおり。実コアのログ・ROM・Geargrafx core のハッシュは、本リポジトリ外の記録であり、本書では主張しない。

## 6. 次の未実行単位

本 PR はドキュメントのみで、新しい試験は追加しない。
§3.2 の I-01、I-02、I-04 は #416 / PR #423 で制御された実コア試験として実施し、main に統合済みである（自作ルーチンに限る）。
残るのは R-01〜R-07（純正 BIOS の実行承認が前提。#173 / #228 の gate と #324 の ABI 判断）と、U-01 / U-02（仕様上未定義）である。
R-01〜R-07 は実装の対象にしない。DEV ROM の `$3D` 接続は ABI UNVERIFIED のままであり、純正互換の根拠にはしない。
