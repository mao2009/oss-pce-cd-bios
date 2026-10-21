# EX_BCDBIN（$3D / 候補 $E0B7）ブラックボックス試験の分類表

Issue #71。本書は **main `1cd5582` 時点**の試験資産を棚卸ししたスナップショットである。後続の #416 / PR #423 は 2026-10-21 に main へ統合され、入力 C・X/Y・SP の追加実コア試験を実施済み。最新の検証状況は `docs/ex-bcdbin.md` を参照する。以下の「未実行」「未カバー」は棚卸し時点を表す。
純正 BIOS の `EX_BCDBIN` は**未確定**（`spec/api_contracts.json` slot 61 の契約はすべて UNKNOWN、エントリ `$E0B7` は HYPOTHESIS）であり、
本書は純正 BIOS の挙動を一つも主張しない。純正 BIOS のルーチンを実行する試験は承認されていないため、**BLOCKED** とする。
本書で「期待値」と呼ぶのは、本プロジェクトが独自に定めた仕様（`docs/ex-bcdbin.md`）に基づく値だけである。
Geargrafx の出力を純正 BIOS の期待値として扱うことはしない。

## 1. 既存の EX_BCDBIN 資産（棚卸し）

| 資産 | 内容 | 状態 |
| --- | --- | --- |
| `src/ex_bcdbin.s` | 独自仕様の内部ルーチン（packed BCD 1 バイト → 0〜99、不正は C=1 で A 不変） | 実装済み（#328 マージ） |
| `docs/ex-bcdbin.md` | 独自仕様、検証方法、契約項目の確定状況（監査表） | マージ済み（#328、#382） |
| `tests/host/test_ex_bcdbin.py` | 参照モデル（全256入力、境界値、BIN→BCD→BIN 往復） | `make test` で実行（ホスト専用） |
| `tests/fixtures/bcdbin_harness.s`、`bcdbin.cfg` | 全256入力を呼ぶ HuCARD ハーネス（非 BIOS） | マージ済み |
| `tests/integration/geargrafx_bcdbin.py` | 実コア実行。cold/warm 両方で全256入力、変異3種（陽性対照つき） | `make test-real-core` と CI で実行 |
| `tests/integration/test_ex_bcdbin_real_core.py` | 実コア経路の負例（core 不在、ca65/ld65 不在、起動失敗、タイムアウト） | `make integration-test`、Linux のみ完全 |
| `.github/workflows/core-bcdbin.yml` | 専用ワークフロー（主たる実コア証拠） | main にマージ済み |
| `docs/api-inventory.md` 86 行 | `$3D` / `$E0B7` / EX_BCDBIN の台帳行 | 既存 |

既存の子 Issue・PR：#328（独自仕様の追加、マージ）、#382（契約項目の監査、マージ）。
棚卸し時点では EX_BCDBIN 専用の未マージ PR は無かった。後続 PR #423 は main 統合済み。#410 のブランチには触れていない。

### 本ブランチで再実行した結果（WSL Ubuntu、clean clone、HEAD `1cd5582`）

| 試験 | 結果 |
| --- | --- |
| `python3 -m unittest tests.host.test_ex_bcdbin -v` | 3 件 OK |
| `python3 tests/integration/geargrafx_bcdbin.py -v`（exit 0） | 2 件 OK（全256入力 cold/warm、変異3種） |

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

R-01〜R-07 は、純正 BIOS の JSR と観測の承認が得られるまで実行しない。承認は本 PR では与えられていない。

### 3.2 独自仕様ルーチン `ex_bcdbin`（正常・境界・エラー）

入力は packed BCD 1 バイト（上位ニブル = 十の位、下位ニブル = 一の位）。
期待値：有効な入力は C=0、A = 十の位 × 10 + 一の位（0〜99）。どちらかのニブルが 9 を超える入力は C=1、A は入力のまま。

| ID | シナリオ | 代表入力 | 区分 | 期待値（basis） | 状態 |
| --- | --- | --- | --- | --- | --- |
| N-01 | 最小正常値 | `$00` | REAL_CORE_OSS + HOST_FIXTURE | A=0, C=0（SPEC） | 実行済み（全256） |
| N-02 | 最大正常値 | `$99` | REAL_CORE_OSS + HOST_FIXTURE | A=99, C=0（SPEC） | 実行済み |
| N-03 | 有効 100 件の全件 | `$00`〜`$99` のうち十・一ともに 0〜9 | REAL_CORE_OSS + HOST_FIXTURE | 100 件 C=0（SPEC） | 実行済み |
| B-01 | 十の位の境界（有効） | `$09`、`$10`、`$19`、`$90` | REAL_CORE_OSS + HOST_FIXTURE | `$09`=9、`$10`=10、`$19`=19、`$90`=90（SPEC） | 実行済み |
| B-02 | 一の位の境界（無効） | `$0A`、`$0F`、`$1A` | REAL_CORE_OSS + HOST_FIXTURE | C=1、A 不変（SPEC） | 実行済み |
| B-03 | 十の位の境界（無効） | `$9A`、`$A0`、`$A9`、`$FF` | REAL_CORE_OSS + HOST_FIXTURE | C=1、A 不変（SPEC） | 実行済み |
| E-01 | 一の位のみ不正（十の位 0〜9） | 60 件（十 0〜9 × 一 A〜F） | REAL_CORE_OSS + HOST_FIXTURE | C=1、A 不変（SPEC） | 実行済み |
| E-02 | 十の位のみ不正（一の位 0〜9） | 60 件（十 A〜F × 一 0〜9） | REAL_CORE_OSS + HOST_FIXTURE | C=1、A 不変（SPEC） | 実行済み |
| E-03 | 両方不正 | 36 件（十 A〜F × 一 A〜F） | REAL_CORE_OSS + HOST_FIXTURE | C=1、A 不変（SPEC） | 実行済み |
| I-01 | 入力 C の影響（C=0 と C=1 の両方で同じ出力） | 有効・無効の混在 | REAL_CORE_OSS（間接） | 出力は入力 C に依存しない（SPEC の入力条件に含まれない） | 実行は間接的。入力 C は制御していない（§4） |
| I-02 | X / Y の保存（呼出前後の値） | X=`$5A`、Y=`$A5` のみ | REAL_CORE_OSS | X、Y 不変（SPEC） | 実行済み（他の値は未確認、§4） |
| I-03 | スタックの均衡と SP の復元 | 256 回の反復後 SP=`$FF` | REAL_CORE_OSS | SP 不変、スタック以外に書かない（SPEC） | 実行済み |
| I-04 | 呼出時の SP が `$FF` 以外 | 例：SP=`$80` | REAL_CORE_OSS | SP 不変（SPEC） | 未実行（§4） |
| U-01 | D フラグ = 1 | 任意 | BLOCKED | 仕様上未定義（SPEC） | 未実行（期待値なし） |
| U-02 | N / Z / V フラグ | 任意 | BLOCKED | 仕様上未定義（SPEC） | 未実行（期待値なし） |

HOST_FIXTURE 側の補足：`test_model_matches_oracle_for_all_256_inputs`（参照モデルと独立な式の一致）と `test_boundaries`（§3.2 の境界値）で、N/B/E の期待値をホストでも確認する。
REAL_CORE_OSS 側：全256入力を cold と warm の 2 回の実コア実行で確認する（計 512 観測）。
実コアの RAM 比較は `check_ram` が行い、変異3種が必ず不一致になることも確かめる（陽性対照つき）。

## 4. 未カバー（ギャップ）

1. **入力 C の制御（I-01）**：ハーネスは `jsr` の直前に C を設定していない。入力 C は直前の反復の出力 C に等しく、
   `$00` の入力 C だけは起動直後の値になる。両方の入力 C が現れるため間接的には観測されるが、制御した試験ではない。
2. **呼出時 SP（I-04）**：ハーネスは SP=`$FF` のみで呼ぶ。SP 非依存は仕様に明記されていないが、スタック使用は PHX/PHY の 2 バイトなので低リスク。
3. **X / Y の入力値（I-02）**：`$5A` / `$A5` のみ。ルーチンは X をループカウンタに使い `PLX` で戻すので、他の値でも同じはずだが未確認。
4. **純正 BIOS（R-01〜R-07）**：すべて BLOCKED。期待値は UNKNOWN で、観測しても比較できない。

## 5. 記録（実行・固定値）

すべて Geargrafx / 純正 BIOS を含まない ROM の記録である。純正 BIOS の期待値ではない。

- ハーネス起動時の CPU 状態：A=入力値、X=`$5A`、Y=`$A5`、SP=`$FF`（`ldx #$ff; txs`）、MPR0=`$FF`（I/O）、MPR1=`$F8`（作業 RAM）、MPR2〜7=未設定（リセット値のまま）、P=直前の反復の出力 P（C のみ意味を持つ）。
- ルーチン入口：`ex_bcdbin` = `$E053`（`rom.map`）。
- 呼出元の JSR：`$E018`〜`$E01A`。RTS 後の戻り先 PC = `$E01B`（`php` の番地）。
- RAM 差分（反復 n、入力 n、作業 RAM 基準 `$2206`）：`$2206+n` = A（参照モデルの値）、`$2306+n` = 戻り時の P、`$2406+n` = `$5A`、`$2506+n` = `$A5`。`$2000` は最終的に 0、`$2001` は最後の X（`$5A`）。
- 副作用：スタック page 1 への書込（PHX/PHY の 2 バイトを反復ごとに戻す）。反復後に `$2200`〜`$2204` へ `PCE!`、`$F8`、`$5A` を書き、`$2205` = `$5A`、`$2606` = `$FF`。
- ハッシュ（sha256）：
  - `src/ex_bcdbin.s`：`2ab6cf7c508ed01b953e0501e05214cd6b5a2536325c6563141a2bdd22fe2049`
  - `tests/fixtures/bcdbin_harness.s`：`86bac707132764d2906bfd21d4badae14ef742e96a7ea7f757502e19fca57095`
  - `tests/fixtures/bcdbin.cfg`：`c8f19226d8afa2e4f3afe3e493da739528ae6d8f150bf7fa53d47c1b0f91dcde`
  - ROM（ローカル ca65/ld65 でビルド）：`0241df2c180c36cdac89bb382bf39d8d1a1f8046ca640f635d125885fe8532fd`
  - Geargrafx core（`geargrafx_libretro.so`、rev `b49ae82`）：`38c9af41e76ab0df0bdd451d537ba770b2d1bc5e4644e9647f21b76170cad52c`
  - cc65 rev `71746c8`、リポジトリ HEAD `1cd5582`
- 結果：ホスト 3 件 OK、実コア 2 件 OK（exit 0）。実コアのログは作業ディレクトリ外（`/home/loach/w07-bcdbin-core.log`）に保存。

## 6. 次の未実行単位

本 PR はドキュメントのみで、新しい試験は追加しない（既存の実コア検証が全256入力を cold/warm で覆っているため）。
残るギャップのうち、制御された実コア試験で埋められるのは I-01、I-02（他の X/Y）、I-04 である（子 Issue #416）。
I-01 / I-02 / I-04 は子 Issue #416 / PR #423 として後続実施・main 統合済み（自作ルーチンに限る）。R-01〜R-07 は純正 BIOS の実行承認が前提であり、実装の対象にしない。
