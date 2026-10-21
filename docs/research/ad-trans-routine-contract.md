# AD_TRANS 周辺の再利用ルーチン `adt_dma_sector` の内部契約（Issue #300、親 #26）

**状態: 開発専用の自作内部契約。System Card の ABI ではない。** `release_eligible=false`。
BIOS スロット `$11`（AD_TRANS、`$E033`）へは接続しない。#301 は BLOCKED_ABI のまま。
**用語:** 「実コア PASS」は、記載した特定の run（コミットと manifest sha256 で指す）について、`make setup` 後の実コア実行が終了コード 0 で判定 `PASS_ADT_ROUTINE_FOUR_CALLS` を返したことだけを指す。契約の解除状態は別であり、§12 の解除条件で判定する。上記の定義は `make setup` 後の実行を要求するが、§11 の各 run（9b5c044、60b15e7、4a7ffb9、8d04c9f を含む）の記録には `make setup` の実施が書かれていないため、これらの run が定義を満たすかは未確認である。解除条件 1 は本 PR のテスト専用 cfg と `make dev-rom` で充足し、検証コード b6fd593（c71941f と `src/adt_dma_routine.s` の sha が同一）で再確認した（ld65 警告 0 件）。過去の clean clone で出た APITABLE / FALLBACK 警告 2 件（§11）は、本 PR の検証範囲では再現していない。「未検証」は、解除条件を満たす run がまだ無いことを指す（§12）。

**実コア実行の結果（run ごと）:**

- 9b5c044: PASS（`PASS_ADT_ROUTINE_FOUR_CALLS`）。manifest sha256 `04e25ba2e3399d55875d65fb697619dd690ea63b6ddc9e467bd9c69203ad7f6a`。
- 60b15e7: clean clone で `--selftest` / 実行 / `--verify` すべて終了コード 0。`PASS_ADT_ROUTINE_FOUR_CALLS`、12 件の改変を拒否。manifest sha256 `caf6ec58562540c17fbd4ccbe5ebd316f86dcea76732304025dd0a7c7604fe24`。
- 4a7ffb9: clean clone で終了コード 0。manifest sha256 `4c703dfce567c586b58e1b92a90c61d341eeffcae302f04fd76cc2e1fba3112a`。60b15e7 からの差分は本契約文書のみで、コードは同じ。
- c80d310: driver のコメントを変えたため、それ以前に生成された manifest の driver_sha256 と一致せず、c80d310 では `--verify` が失敗する。
- a8f924e（本 PR）: 修正後の実コア実行は終了コード 0、ld65 警告 0 件、raw_sha256 `38e0db5b…`、steps 7783467、判定 `PASS_ADT_ROUTINE_FOUR_CALLS`。`--verify` は終了コード 0。cfg に 1 行を追加すると `--verify` は終了コード 1（linker cfg differs）で失敗し、元に戻すと成功する。
- 8d04c9f（c80d310 以降のコード。コードは c80d310 と同一、差分は文書のみ）: clean clone で `--selftest`、実行、`--verify` すべて終了コード 0。`PASS_ADT_ROUTINE_FOUR_CALLS`、12 件の改変を拒否。manifest sha256 `7d9428e6099add5a40d8116423c628e2ba2b8902bbc051679e328ac1a77aa9e3`。cc65 のリンカ警告 2 件（APITABLE、FALLBACK）は再び出た。この run で manifest を再生成し、§12 の条件 3（`--verify` 終了コード 0）を満たした。

- 検証コード b6fd593（`src/adt_dma_routine.s:198` のコメント修正を含む。後続の c71941f は docs のみで、`src/adt_dma_routine.s` の sha は両者で同一）: 検証は作業ツリー `/home/loach/d-w01-410` で実施した（4d2eafa を checkout し、`:198` の 1 行を修正した状態。clone 直後の `git status` は clean）。ログは作業ツリーの記録で、process の終了コードは `make-setup.rc` 以外に記録していない（以下の終了コードは作業中の表示で確認）。`make setup` 終了コード 0（cc65 `71746c829e77f74c2b601a7d16821170c2610df3`、geargrafx `b49ae82a012eade566d72e9d61c9d95a5caa4da3`（本体とデバッガ版）、actionlint `v1.7.7` の READY を確認）。`make dev-rom` は debug / release とも終了コード 0、ld65 警告 0 件。`python3 tests/integration/geargrafx_ad_trans_routine.py --out build/ad-trans-routine` は終了コード 0、判定 `PASS_ADT_ROUTINE_FOUR_CALLS`、steps 7783467、raw_sha256 `38e0db5b…`。修正前に同一入力で 3 回実行し、3 回とも同じ raw_sha256 だった。修正後の最終実行も同じ値。最終 manifest sha256 `5775eb8e…`。`--verify` 終了コード 0。cfg に 1 行を追加すると `--verify` は終了コード 1（linker cfg differs）で失敗し、`git checkout` で元に戻すと終了コード 0 で成功する。

manifest sha256 は run ごとに変わる。manifest には platform、python、command が含まれ、環境と実行によって異なるためである。したがって再現の判定は sha の一致ではなく、verdict と各観測の一致で行う。

本契約は、PR #406 の HuC6280 ゲスト DMA 経路を独立ルーチンとして切り出した自作の取り決めである。
呼出元（`src/adt_dma_routine_boot.s`）と呼出先（`src/adt_dma_routine.s`）の内部契約として定義する。

## 1. 範囲

- 対象: 最小 1 セクター（2048 バイト）、固定転送モード（READ(6)、転送数 1 固定、宛先は ADPCM RAM の先頭からの連続書込み）。
- 対象領域（region）: ルーチン自体に region 分岐はない（BIOS entry を呼ばないことを `src/` の grep で確認）。region 依存の有無（実機・実 CD 媒体・実 BIOS）は未確認。合成ディスクに region の指定はない（manifest に region の記載なし）。
- 対象環境: pinned Geargrafx `b49ae82a012eade566d72e9d61c9d95a5caa4da3` の合成ディスク（manifest の `disc: SYNTHETIC`、`build_image(sectors=16)`）。実機・実 BIOS・実 CD ドライブは対象外で、未確認。
- 含まないもの: 複数セクター、ジャンプテーブル接続、`$E030`〜`$E03F` の BIOS API への接続、CD_BASE / CD_SEEK との順序保証。
- 根拠の区分: 呼出側の手順は PR #406（`src/ad_trans_dma_probe.s`）で実測された SCSI / ADPCM DMA の手順を踏襲する。
  ただし #406 の実測は本 Issue の新ルーチンでは未確認である（§11）。

## 2. ファイル構成

| ファイル | 役割 |
| --- | --- |
| `src/adt_dma_routine.s` | 再利用ルーチン `adt_dma_sector`（起動処理を含まない） |
| `src/adt_dma_routine_boot.s` | 起動処理（MPR、スタック、結果領域の初期化）と 4 回の呼出し |
| `tests/integration/geargrafx_ad_trans_routine.py` | 実コア駆動、判定器、改変試験（`--selftest` は判定器のみ） |

リンク順は boot を先頭にする（`cd_entry` を `$F000` に置くため）。

## 3. 入力（呼出し前にメモリへ書く）

パラメータブロック `PB = $2200`（作業 RAM、CPU `$2200`）。ルーチンは読むだけで書き換えない。

| 番地 | 名前 | 値の範囲 | 意味 |
| --- | --- | --- | --- |
| `$2200` | `PB_LBA_HI` | `$00`〜`$1F` | LBA の bit 20〜16。`$20` 以上は code 9 |
| `$2201` | `PB_LBA_MID` | 任意 | LBA の bit 15〜8 |
| `$2202` | `PB_LBA_LO` | 任意 | LBA の bit 7〜0 |
| `$2203` | `PB_DST_LO` | 任意 | ADPCM RAM 宛先の下位バイト |
| `$2204` | `PB_DST_HI` | `$00`〜`$F8` | 宛先の上位バイト。宛先 + 2048 ≤ `$10000` を要求し、`$F8` のときは下位が `$00` |
| `$2205` | `PB_ARM` | `0` または `1` | `1` で ADPCM DMA を有効化。`0` は負例用の入力（DMA を決して有効化しない）。その他は code 9 |
| `$2206` | `PB_SLOT` | `0`, `8`, `16`, `24` | 結果スロットのオフセット（`$2300` からの相対）。それ以外は code 9（結果はスロット 0 へ書く） |

転送数は 1 セクターに固定する（CDB の転送数バイトは定数 1）。

## 4. 出力

- `A` = 結果コード（`0` が成功）。
- `P.C` = `1` のとき失敗（`A != 0`）、`0` のとき成功。
- 結果スロット（8 バイト、`$2300 + PB_SLOT`）:

| オフセット | 内容 |
| --- | --- |
| +0 | 結果コード（A と同じ） |
| +1 | 戻り時のステージ（§5） |
| +2 | SCSI status バイト（0 = GOOD） |
| +3 | SCSI message バイト（0 = COMMAND COMPLETE） |
| +4 | 戻り時の `$180B`（ADPCM_DMA） |
| +5 | 戻り時の `$180C`（ADPCM_STAT）。A、B、C では 0（wait_adpcm_drained による）。D、E では C から引き継がれる 0（D、E はバスに触れない）。bit2 の意味は仮説（HYPOTHESIS）。初出は `src/ad_trans_dma_probe.s:20` のコメント。ルーチンと判定器（`src/adt_dma_routine.s:17`、`:370`、判定器 `tests/integration/geargrafx_ad_trans_routine.py:159`）は同じ仮定を用いる。実機・Geargrafx の読出し経路での検証は未了（§10） |
| +6 | `1` = バス空き かつ DMA と書込みスロットが空き、`0` = いずれかが不成立、`$FF` = バスに触れていない |
| +7 | 引数 `PB_ARM` のエコー |

## 5. 結果コード

| コード | 意味 | 戻り時のステージ | バスの扱い |
| --- | --- | --- | --- |
| 0 | 成功（DMA 完了、GOOD、COMMAND COMPLETE、バス空き） | 9 | 触れた（復帰不要） |
| 1 | リセット後にバスが空かない | 0 | 触れた（復帰を実行） |
| 2 | SELECT 後に COMMAND フェーズに入らない | 3 | 触れた（復帰） |
| 3 | コマンドバイトの REQ が来ない | 4 | 触れた（復帰） |
| 4 | DATA-IN フェーズに入らない | 5 | 触れた（復帰） |
| 6 | status が GOOD でない、または message が COMMAND COMPLETE でない | 8 | 触れた（復帰） |
| 7 | status / message フェーズの待ちがタイムアウト | 8 | 触れた（復帰） |
| 8 | message 後にバスが空かない | 8 | 触れた（復帰） |
| 9 | 入力不正（LBA、arm、宛先） | 0 | 触れていない（復帰不要、`bus_ok=$FF`） |
| 10 | コマンドバイトの ACK 失敗 | 4 | 触れた（復帰） |
| 11 | DMA 無効のため DATA-IN が終わらない（データフェーズが排出されない） | 7 | 触れた（復帰） |
| 12 | ADPCM の DMA または書込みスロットが空かない | 7 | 触れた（復帰） |

`stage` は失敗した位置を示す。`code 9` は実際に入力検査で弾いた場合のみ使う。

## 6. レジスタ、MPR、スタック、ゼロページ（契約）

- 呼出し前の前提: `MPR0 = $FF`（I/O）、`MPR1 = $F8`（作業 RAM）、`MPR7` はルーチンのあるバンク、`D` フラグはクリア。
  `P.I` は呼出側が決める（ルーチンは IRQ を使わない。割込みは判定器で失敗扱い）。
- 保存するもの: `MPR0`〜`MPR7`、`P.I`、`P.D`。
- 破壊するもの: `A`、`X`、`Y`、ゼロページ `$00`〜`$0B`（作業 RAM の `$2000`〜`$200B`）、`$2210`〜`$2215`（CDB）、結果スロット。
- スタック: 呼出側の JSR（boot）→ `adt_dma_sector` → `ack_byte` → `wait_phase` → `tick` の最大 4 段で、戻り番地 8 バイト。呼出側は 8 バイト以上の空きを用意する（既定では `$21FF` から下へ伸びる）。
- ゼロページ割り当て: `tmo0-2` = `$00-$02`、`wmask` = `$03`、`want` = `$04`、`stg` = `$05`、`bus_ok` = `$06`、`code` = `$07`、`sst` = `$08`、`smsg` = `$09`、`rp` = `$0A-$0B`。

## 7. 有界 timeout

- 待ちは `wait_phase`、`wait_adpcm_drained` の 2 種類で、どちらも `TMO_HI = 8` による 8 × 65536 ポールを上限にする（`tick` は 3 バイトのカウンタ）。
- 1 回の呼出しに含まれる待ちは定数個。成功経路の待ちは 22 回（`wait_phase` 21、`wait_adpcm_drained` 1）。
  失敗経路は失敗位置までの待ちに、復帰の 2 回（`wait_phase` 1、`wait_adpcm_drained` 1）を加える。
  全体の上限は「待ち回数 × 524288 ポール」で、コードから算出した上界であり、実測はしていない（§11）。
- `delay` は定数時間（外側 `$20` × 内側 256）。

## 8. 異常終了時の復帰条件

1. 入力不正（code 9）はバスに触れる前に返す。復帰は行わない（`bus_ok = $FF`）。
2. バスに触れた後の失敗はすべて `fail` を通る。`fail` は次を行う。
   - ADPCM DMA を無効化（`$180B = 0`）。
   - SCSI バスリセット（`$1804` の bit 1 を 1 にしてから 0 に戻す。間に `delay`）。
   - バス空きを有界待ち。次に `wait_adpcm_drained` で DMA と書込みスロットの空きを有界待ち。
   - 両方成立すれば `bus_ok = 1`、不成立なら `bus_ok = 0`。戻り値は元の失敗コードを保つ。
3. 成功経路は復帰を行わない。status と message の後、バス空きを確認して `bus_ok = 1` とする。
4. 呼出側は `bus_ok` を見て次の呼出しの可否を判断する。`bus_ok = 0` の状態で次の呼出しを行うと、ルーチン冒頭のリセットで回復を試みるが、保証はしない。

## 9. 呼出側の規則（boot）

- 4 回の呼出しを同一ルーチンで行う: A 正例（`$0100`、arm 1）、B 負例（`$0900`、arm 0）、C 復帰正例（`$1100`、arm 1）、D 入力不正（LBA `$200005`、上位バイト `$20` が範囲外、`$1500`、arm 1）。
- A が成功した直後、A の 8 バイトの記録（スロット 0）を `$2320`〜`$2327` へ複写する（E がスロット 0 を上書きするため）。
- E は D の後に行う: 無効スロット `4`（LBA 5、`$1500`、arm 1）。コード 9 がスロット 0 に書かれ、A の記録は消える（§3 の規則どおり）。E はバスに触れず、DMA を武装しない。
- A が失敗したら `cd_halt_fail` へ抜ける（正例が成立しないと他の判定が無意味なため）。
- 全呼出し後は `cd_halt_ok` で停止する。結果の正否は判定器が結果スロットを読んで決める（ゲストの停止位置では決めない）。

## 10. 判定器（`geargrafx_ad_trans_routine.py`）

- 期待値は契約（§5）から判定器が再計算する。ゲストが報告する理由文は判定に使わない。
- 判定の順序: 停止位置 → 結果記録（A は `$2320` の複写、B、C、D はスロット 8、16、24、E はスロット 0） → ADPCM RAM（`$0100` と `$1100` の 2048 バイト、それ以外ゼロ） → 書込みポインタ（`$1900`） → DMA オフ。
- 各記録は code、stage、SCSI status / message、戻り時の `$180B`、`$180C` の bit2、bus_ok、arm を検査する。
- `$180C` の bit2 を 0 とする根拠（呼出しごと。コードは `src/adt_dma_routine.s`）:
  - bit2 の意味（書込みスロット空き）は仮説（HYPOTHESIS）である。初出は `src/ad_trans_dma_probe.s:20` のコメント（`docs/research/ad-trans-diagnostic-plan.md` には `$180C`、ADPCM_STAT、bit2 の記述がない）。ルーチンと判定器（`src/adt_dma_routine.s:17`、`:370`、`src/ad_trans_dma_probe.s:265`、`tests/integration/geargrafx_ad_trans_routine.py:159`）は同じ仮定を用いる。実機・Geargrafx の読出し経路での検証は未了。本契約は bit2 の意味を独自に定義せず、判定器はこの仮定に依る。
  - A と C: 呼出し A と C の戻り時 bit2 = 0 は、bit2 の意味が何であれ成り立つ、wait の構造から導かれる観測である。成功経路は `wait_adpcm_drained` の成功の後にだけ進み（呼出し `src/adt_dma_routine.s:216`、判定 `:217` の bcc）、その後は SCSI の status / message / バス空きの待ちしかなく、`$180C` への書込みも待ちもない。ただし、成功判定から戻り時の読出しまでの間にハードウェアが bit2 を立てないことは、実コアの観測に基づく仮定である。
  - B: 失敗後の `recover` は、`wait_adpcm_drained` が成功したときだけ `bus_ok = 1` とする（呼出し `src/adt_dma_routine.s:306`、判定 `:307` の bcs、`bus_ok = 1` の設定は `:308`（`lda #1`）と `:309`（`bus_ok` への格納））。戻り時の bit2 = 0 も、この wait の構造から導かれる。
  - D と E: 入力不正は `bad_request` で返り、バスにも DMA にも触れない。戻り時の `$180C` は読出しのみで、ルーチンは書かない。値は C の戻り時の状態がそのまま残ったものである。契約はこれを保証せず、実コアの観測に基づく仮定である。
- 改変試験は 12 件。各改変は宣言した検査 id でのみ拒否されなければならない。E の改変（スロット 0 の code を 9 から 0 へ書き換える）は `slot_e_code` で拒否される。
- テストの空白（本 PR で修正）: 修正前の `verify()` は manifest の `probe_source_sha256` を照合しなかった。本 PR で `probe_source_sha256` と `linker_cfg_sha256`（テスト専用 cfg）を照合するよう修正した。また A の記録（`$2320`）を改変する試験は無い。
- `--selftest` は契約から作った観測で判定器と改変表だけを検査する。コア実行の証拠ではない。

- 来歴の限界（a8f924e）: `--verify` は作業ツリーの sha と manifest の整合を確認する。manifest と成果物を同時に書き換えれば通るため、これは整合性の確認であり、来歴（どの実行が生成したか）の証明ではない。
- 時刻シードの範囲（a8f924e）: probe は `time()` を定数に固定する。Geargrafx の乱数シードは `time(NULL)` 由来のため、固定により SCSI 初期遅延の経路は 1 通りしか検査されない（シード掃引の結果は本リポジトリに記録していない）。判定は steps を読まないため、判定結果はこの固定の影響を受けない。

## 11. 証拠の区分

| 区分 | 状態 | 根拠 |
| --- | --- | --- |
| 実装（ルーチン、boot） | 作成済み | `src/adt_dma_routine.s`、`src/adt_dma_routine_boot.s` |
| 構文検査（cc65 アセンブル・リンク） | 9b5c044 の clean clone 実行で成功。（0b44e03 以前の記録）リンカ警告 2 件あり: `src/bank0.cfg:8` の `APITABLE` と `:9` の `FALLBACK` が空セグメント（この実行では影響は観測されなかった）。60b15e7 は同じ clean clone で終了コード 0（manifest sha256 `caf6ec58562540c17fbd4ccbe5ebd316f86dcea76732304025dd0a7c7604fe24`）。8d04c9f の clean clone 実行でも、同じ警告 2 件が出た | 9b5c044、60b15e7、8d04c9f の clean clone 実行の記録。c80d310 以降の変更はドキュメントのみ（コードは同一）で、この行の clean clone 実行は 8d04c9f までのものである |
| 判定器の自己検査 | 60b15e7 で `--selftest`（host のみ） | 契約から作った観測と改変表だけ。コアの証拠ではない |
| 実コア（pinned Geargrafx）の正例・負例・復帰・入力不正・無効スロット（証拠経路は直接実行のみ。`make routine-real-core` には含まれない） | 9b5c044 で PASS（`PASS_ADT_ROUTINE_FOUR_CALLS`、manifest sha256 `04e25ba2e3399d55875d65fb697619dd690ea63b6ddc9e467bd9c69203ad7f6a`、selftest / real-core / verify すべて終了コード 0）。60b15e7 は clean clone で終了コード 0（`PASS_ADT_ROUTINE_FOUR_CALLS`、manifest sha256 `caf6ec58562540c17fbd4ccbe5ebd316f86dcea76732304025dd0a7c7604fe24`）。4a7ffb9 は clean clone で終了コード 0（manifest sha256 `4c703dfce567c586b58e1b92a90c61d341eeffcae302f04fd76cc2e1fba3112a`）。8d04c9f は clean clone で `--selftest`、実行、`--verify` が終了コード 0（`PASS_ADT_ROUTINE_FOUR_CALLS`、manifest sha256 `7d9428e6099add5a40d8116423c628e2ba2b8902bbc051679e328ac1a77aa9e3`）。コードは c80d310 と同一。証拠の範囲: 合成ディスク（manifest の `disc: SYNTHETIC`、`tools/make_cd_image.py` の `build_image(sectors=16)`）、Geargrafx revision `b49ae82a012eade566d72e9d61c9d95a5caa4da3`（GPL-3.0、オブジェクトのリンクのみ） | 9b5c044 から ROM、probe、判定器が変わったため、9b5c044 の結果は 60b15e7 の証拠ではない。60b15e7 の結果は、その run の driver に対する証拠であり、c80d310 以降のコードの driver を覆わない（下記）。 |
| 独立レビュー | 別コンテキストのレビュー結果を PR 本文に記す | §12 |
| CI | PR の HEAD で確認 | PR 本文 |

**c80d310 以降のコードの扱い:** c80d310 で driver のコメントを変えたため、それ以前に生成された manifest の driver_sha256 と一致せず、c80d310 では `--verify` が失敗する。8d04c9f の clean clone での実コア実行で manifest を再生成し、`--verify` が終了コード 0 となった（上記 run 一覧の 8d04c9f）。これにより §12 の条件 3 を 8d04c9f の run で満たした。コードは c80d310 と 8d04c9f で同一である。

## 12. UNKNOWN と解除条件

- UNKNOWN（推測で埋めない）: 実機・実 BIOS での意味、AD_TRANS（`$E033`）の入力意味（C1〜C16）、SCSI バスリセットが Geargrafx の負例後の状態をどこまで戻すか（HYPOTHESIS: リセットで戻る）、SEL が COMMAND フェーズの待ちの間にクリアされないこと（Geargrafx でのみ観測。実機は未確認）。
- 解除条件:
  1. cc65（pinned）が使える環境で `src/adt_dma_routine.s` と `src/adt_dma_routine_boot.s` が警告なくアセンブルとリンクできること（`cd_entry` が `$F000`）。本 PR では、テスト専用の `tests/fixtures/ad_trans_routine_guest.cfg` でリンクし、`src/bank0.cfg` は変更しない。BOOT（$F000）と VECTORS（$FFF6）の配置は `src/bank0.cfg` と同一で、この経路の警告は 0 である。`make dev-rom` は b6fd593 で終了コード 0・ld65 警告 0 件（`build_rom.py` が生成する api_table.s が APITABLE/FALLBACK を定義する）。
  2. `make setup` の後、`python3 tests/integration/geargrafx_ad_trans_routine.py --out build/ad-trans-routine` が終了コード 0 で、判定 `PASS_ADT_ROUTINE_FOUR_CALLS` を返すこと。
  3. `--verify` が終了コード 0 であること。c80d310 以降のコードでは、8d04c9f の clean clone での実コア実行で満たした（§11「c80d310 以降のコードの扱い」）。60b15e7 の結果は c80d310 以降の driver を覆わない。8d04c9f の生成と検証は同じ run 内で行った（同一 run で生成した manifest に対する検査）。driver の同一性は、その run の manifest の driver_sha256 で示される。
  4. 解除状態: 条件 1 は本 PR のテスト専用 cfg で充足（b6fd593 で再確認。冒頭の run 一覧を参照）。条件 2 は b6fd593 の run で充足（`make setup` 終了コード 0 と実行の終了コード 0。上記 run 一覧）。8d04c9f の clean clone での実行は判定 `PASS_ADT_ROUTINE_FOUR_CALLS`、終了コード 0 であったが、`make setup` の実施が記録されていないため、この実行を条件 2 の充足とはみなさない。条件 3 は 8d04c9f の run で満たした。§11 の「実コア PASS」は特定 run の結果であり、この解除状態とは別である。条件 1〜3 は検証コード b6fd593 の run で満たした。解除の判断は独立レビューに委ね、本 PR では `release_eligible=false` を維持する。
