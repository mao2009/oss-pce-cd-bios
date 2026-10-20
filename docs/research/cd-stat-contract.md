# CD_STAT（スロット `$09`、候補エントリ `$E01B`）呼出側 ABI・戻り状態の根拠台帳（Issue #229）

**状態: 呼出先（System Card 3.0 の callee）の ABI は未確定。ROM 実装なし。**
親 Issue #18、本 Issue #229（Contract）。spec=HYPOTHESIS、provenance=PENDING、implementation=NOT_STARTED、verification=NOT_RUN。
**callee 側: BLOCKED_ABI。release_eligible=false。DEV_CALLABLE にしない。**

本台帳は、公開の呼出側ソースから見える事実と、呼出先の仮説・未確定を分けて記録する。
名前（`cd_stat`）や呼出側のコメント・分岐から呼出先の戻り値の意味を確定しない。未確定の欄には値を補わない。
PASS・OBSERVED・VERIFIED の判定は本台帳に含めない。

## 1. 規則

状態の意味:

- `SOURCE_FACT`: 固定版の公開ソースに書かれた事実。**呼出側（caller）の記述**か、**名前定義などの参照事実**に限る。呼出先の振る舞いには付けない。
- `HYPOTHESIS`: 呼出側の使い方や二次的な参照から推測した内容。呼出先の実行で確認していない。
- `UNKNOWN`: 出典が不足している。値の欄は `UNKNOWN` と書く。
- `BLOCKED_ABI`: 呼出先の ABI が未確定で、実行による測定もできない（実行可能な callee がない）。

禁止事項:

- `VERIFIED` と `OBSERVED` は使わない。呼出先を実行していないため、どの行にも付けられない。
- `PASS` と `DEV_CALLABLE` は使わない。CD_STAT は callable として実装されていない。
- `side` が `callee` の行は `HYPOTHESIS` / `UNKNOWN` / `BLOCKED_ABI` のみ。呼出側のソースから callee の事実を導かない。
- `UNKNOWN` の行は値を埋めない。`HYPOTHESIS` の行は、根拠の出典と「未確認」を明記する。
- 参照実装（Hu-Go など HLE）の振る舞いは、System Card の証拠にしない（`side` は `reference`）。

## 2. 出典

固定版（HuC、`pce-devel/huc`）:

- commit `54f5c73606c1e7142e8095c727cab2767e2b3cb3`
- 取得元: `https://codeload.github.com/pce-devel/huc/tar.gz/54f5c73606c1e7142e8095c727cab2767e2b3cb3`
- アーカイブ SHA-256: `9fe91254051381077bd4ea217b718ec033a52da185283ae9479c1abac365311b`（取得して一致を確認。展開物は読むだけで実行していない）
- 再確認（展開物は実行しない）:

  ```sh
  grep -rIn -E 'cd_stat' include examples
  ```

Hu-Go（参照専用、GPL-2）:

- repository `mckayemu/hugo`、commit `64ed226da2288738781743eeef228c069e8973a4`、path `bios.c`
- SHA-256 `db3f227e09a73457bc9b0f2c54a3922ab9dca1e301f1b5029690c0d210972f22`（`gh api` で commit と blob の内容を再確認。`docs/research/cd-contnts-contract.md` の記録と一致）

| ID | 出典（固定版内のパス） | 行 | ライセンス表示 | 用途・注意 |
| --- | --- | --- | --- | --- |
| S1 | `include/hucc/pcengine.inc` 477（`cd_stat = $00:E01B, FUNC`）、329–343（`_ax`〜`_dh` の作業 RAM 定義）、`include/huc/system.inc` 74（`cd_stat = $E01B`） | — | `pcengine.inc`: 著作権行なし、8–10 行目に Boost 1.0 の記載。`system.inc`: 先頭 14 行に表示なし（いずれも確認済み） | 名前と入口の宣言のみ。根拠は名前と入口に限る |
| S2 | `include/hucc/core-kernel.asm` 500–516（`call_bios`） | 8 行目 "Copyright John Brandwood 2021-2024." | 10–12 行目に Boost 1.0 を明記（展開物で確認） | 呼出し経路（MPR7 の退避・復元、`jsr $E000` の選択） |
| S3 | `include/hucc/core.inc` 41–43（`system` マクロ: `ldy #<\1`、`jsr call_bios`） | 8 行目 "Copyright John Brandwood 2021-2024." | 10 行目 Boost 1.0 を明記（展開物で確認） | マクロの展開（Y の設定） |
| S4 | `include/hucc/hucc-systemcard.asm` 464–469（464 行目は関数シグネチャのコメント `cd_status( unsigned char mode<acc> )`、466–469 は `_cd_status.1` マクロ: `system cd_stat`、`cly`） | 8 行目 "Copyright John Brandwood 2024."、10 行目 Boost Software License 1.0 | Boost 1.0（ヘッダに明記） | HuCC 側ラッパの入出力（mode は A で渡す、戻り後に Y を 0 にする） |
| S5 | `examples/asm/elmer/cd-core-scsitest/scsitest.asm` 681–691（busy 確認 `cla`・`system cd_stat`・`cmp #$00`・`bne`、ready 確認 `lda #1`・`system cd_stat`・`cmp #$00`・`beq`）、693–695（`ldy #60`、`jsr wait_nvsync`、`bra`） | 10 行目 "Copyright John Brandwood 2024-2025."、12 行目 Boost 1.0 | Boost 1.0（ヘッダに明記） | **唯一の BIOS 呼出側の実例。** 自作の CD 試験プログラムで、CD_STAT の仕様ではない |
| S6 | Hu-Go `bios.c`（参照専用） 39（`CD_STAT 0x09`）、172–173（名前文字列）、591–603（`CD_STAT` 分岐、`case` 内の `rts();` は 602、`break;` は 603） | ファイル内に表示なし。`COPYING` は GPL v2 を「LICENSE.gpl」参照として記すが、同コミットのツリーに `LICENSE.gpl` はなく未確認 | GPL-2（参照専用） | HLE の挙動。**BIOS の ABI の証拠ではない。コードの転載や翻訳はしない** |

除外する出典（§5 に理由を記す）:

- `include/huc/cdrom.asm` 491–508（`_cd_status` の入力説明コメント）
- `examples/asm/elmer/cd-core-scsitest/scsitest.asm` 1501–1512（`test_stat`）、1390–1404（`test_number16`）、593–634（`log_irq2`）
- `include/hucc/hucc-systemcard.h` 99、`examples/asm/elmer/kickc/bios.h` 35（宣言のみ）

## 3. 項目別台帳

| ID | side | 項目 | status | 値 | 出典 | 理由・確度 |
| --- | --- | --- | --- | --- | --- | --- |
| F01 | caller | Y（エントリの選択） | SOURCE_FACT | `$1B`（JSR 先 `$E01B`） | S3 41–43、S2 500–509、S1 477 | `system` マクロが `ldy #<cd_stat` を出し、`call_bios` が Y を `jsr $E000` の下位バイトに書く。`$E000+Y=$E01B`。 |
| F02 | caller | A（mode、busy 確認） | SOURCE_FACT | `$00` | S5 681–684、S4 464 | busy 確認の経路で `cla` の後に呼ぶ。この一経路の事実であり、呼出先の入力要件ではない。 |
| F03 | caller | A（mode、ready 確認） | SOURCE_FACT | `$01` | S5 688–691 | ready 確認の経路で `lda #1` の後に呼ぶ。この一経路の事実である。 |
| F04 | callee | A の解釈（mode） | HYPOTHESIS | `$00` と `$01` の 2 経路のみ観測。他の値は UNKNOWN | S5 681–691、S4 464（呼出側の使い方から推測） | 呼出先が A をどう読むかは未確認。HuC の注記（`cdrom.asm` 除外）は根拠にしない。 |
| F05 | callee | X の入力要件 | UNKNOWN | UNKNOWN | — | S5 681–691 では X を設定しない。呼出先の読取りは不明。 |
| F06 | callee | P（フラグ）の入力要件 | UNKNOWN | UNKNOWN | — | 呼出先が入力フラグを読むかは不明。 |
| F07 | caller | `$20F8`–`$20FF`（`_al`〜`_dh`）の入力 | SOURCE_FACT | S5 681–691 の経路では書込みなし | S5 681–691（書込みなし）、S1 `pcengine.inc` 329–343（定義のみ） | S5 の 1 経路の事実に限る。ツリー全体の否定ではない: `include/hucc/blkmap.asm` 367–368 は同じ領域（`_al`・`_ah`）への作業パラメータの設定を、コメントによれば示す。定義は参照事実であり、呼出先の入力の根拠にはしない。 |
| F08 | callee | `$20F8`–`$20FF` の入力・出力 | UNKNOWN | UNKNOWN | — | 呼出先の読取り・書込みの有無と位置は不明。 |
| F09 | caller | 戻り A の分岐（busy 確認） | SOURCE_FACT | A≠0 で待機を続け、A=0 で次へ進む | S5 681–684 | 呼出側の分岐の記述。呼出先の戻り値の証拠ではない。 |
| F10 | caller | 戻り A の分岐（ready 確認） | SOURCE_FACT | A=0 で ready へ進み、非 0 で約 1 秒待って再試行する | S5 688–695、`include/hucc/common.asm` 58–61（`wait_nvsync`: Y=60 回の VBLANK 待ち。約 1 秒は 60 Hz 想定） | 呼出側の分岐の記述。再試行の上限は呼出側に無い。 |
| F11 | callee | 戻り A の意味（busy 確認） | HYPOTHESIS | `$00` を非 busy とみなす | S5 681–684（呼出側の期待から推測） | 呼出先の実行で未確認。 |
| F12 | callee | 戻り A の意味（ready 確認） | HYPOTHESIS | `$00` を ready、非 0 を未 ready またはエラーとみなす | S5 688–691（呼出側の期待から推測） | 呼出先の実行で未確認。非 0 の値の内訳は F25 で UNKNOWN。 |
| F13 | callee | 戻り X・Y の意味 | UNKNOWN | UNKNOWN | — | 出力の有無は不明。 |
| F14 | caller | 戻りフラグ N/Z（wrapper による） | SOURCE_FACT | N/Z は復元された戻り A で決まる | S2 511–514（`pha`、`lda #$00`、`tam7`、`pla`） | 戻り時の `pla` が N/Z を決める。呼出先が設定した N/Z は保たれない。 |
| F15 | caller | 戻り Y（HuCC ラッパ） | SOURCE_FACT | ラッパは戻り後に Y を `$00` にする | S4 466–469（`cly`） | HuCC ラッパの記述。呼出先の Y の出力とは無関係。 |
| F16 | callee | 戻りフラグ C/V/D/I | UNKNOWN | UNKNOWN | S2 500–516 | 呼出先が設定するかは不明。wrapper 命令が C/V/D/I を変えるかは命令仕様と照合していない。 |
| F17 | callee | 戻り機構（SP と復帰） | HYPOTHESIS | 呼出先は RTS で `call_bios` へ戻る | S2 500–516（`jsr` 直後に後処理がある構造） | wrapper の構造からの推測で、呼出先の実行では未確認。呼出先が残すスタックの量は UNKNOWN。 |
| F18 | caller | MPR7（呼出し中の値） | SOURCE_FACT | 呼出し中 MPR7=`$00`、戻り時に呼出側の値へ復元 | S2 502–512（`tma7`、`sta`、`cla`、`tam7`、`lda`、`tam7`） | wrapper が MPR7 を退避し、bank 0 を見せてから復元する。 |
| F19 | callee | MPR0–6（呼出し中の値） | UNKNOWN | UNKNOWN | — | 二次出典の値は本台帳に転載しない（§5）。原文照合まで値を記録しない。 |
| F20 | callee | 作業 RAM の副作用・出力バッファ | UNKNOWN | UNKNOWN | S5（呼出側は結果バッファを読まない） | 呼出先が作業 RAM に書くかは不明。 |
| F21 | caller | IRQ（呼出し周辺の操作） | SOURCE_FACT | S5 681–691 の範囲に IRQ マスクの操作はない | S5 681–695 | 呼出側の記述の範囲に限る。呼出し前の全体の IRQ 状態は、この台帳の範囲外。 |
| F22 | callee | IRQ とイベント順序 | UNKNOWN | UNKNOWN | S5 1390–1404（「CD_STAT が IRQ2 を出すか」を問う試験。結果の記録はない） | IRQ2 の発生有無は未確認。問いのみで結果がないため根拠にしない。 |
| F23 | caller | 媒体の有無・待機の扱い | SOURCE_FACT | 呼出側は reset 後に busy を待ち、ready を待つ。媒体なしを区別する分岐はない | S5 675–695 | 呼出側の記述。媒体なし時の呼出先の戻り値は F24 で UNKNOWN。 |
| F24 | callee | 媒体なし・待機中の戻り値 | UNKNOWN | UNKNOWN | — | 出典がない。実測も未実施（NOT_RUN）。 |
| F25 | callee | 失敗条件・エラー値（無ディスク、不正な mode、タイムアウト） | UNKNOWN | UNKNOWN | — | 出典がない。失敗時の A・フラグ・出力の有無は不明。 |
| F26 | reference | Hu-Go の `CD_STAT` 分岐 | HYPOTHESIS | A=0 を設定して N/Z を更新し、mode 入力は読まない（HLE） | S6 591–603 | 参照専用（GPL-2）。コード内の TODO が未完成を示す。BIOS 実機の挙動は未確認で、BIOS の ABI の根拠にしない。 |
| F27 | name | 名称（綴り） | SOURCE_FACT | HuC `cd_stat`、Hu-Go `CD_STAT`、本リポジトリ `CD_STAT` | S1 477、S6 39・172–173、`docs/api-inventory.md` 34 行 | 綴りは一致。`$E01B` の入口は S1 の宣言のみで確認。Hu-Go（S6）は関数番号 `0x09` で分岐し `$E01B` を示さないため、入口の一致の根拠にしない。 |

## 4. System Card 3.0 固有の不一致と注意（明示）

1. **mode の解釈は呼出側の使い方だけ**: 呼出側（S5）は A=0 と A=1 の二経路しか示さない。他の値の意味は UNKNOWN（F04、F25）。
2. **Hu-Go は BIOS の証拠ではない**: Hu-Go の `CD_STAT` は A=0 を返す HLE で、mode を読まない（F26）。3.0 の戻り値の根拠にしない。
3. **HuC の注記は採らない**: HuC の `cdrom.asm` に busy/ready の説明コメントがあるが、「NOT IMPLEMENTED YET」の注記を伴い、ライセンス表示も確認できないため採らない（§5）。
4. **呼出側の期待と呼出先の仕様を分ける**: S5 の分岐（F09、F10）は呼出側の期待であり、3.0 の戻り値の仕様ではない。

## 5. 除外・未転載

- `include/huc/cdrom.asm` 491–508: `_cd_status` の入力説明（`$00` で busy 確認、他の値で ready 確認）はあるが、ファイルは「NOT IMPLEMENTED YET」の注記を含み、ライセンス表示を確認できない。`docs/provenance.md` の方針に従い根拠にしない。
- `scsitest.asm` 1501–1512 `test_stat`: 名前に「BIOS CD_STAT」とあるが、本体は試験プログラム独自の SCSI 処理であり、`$E01B` を呼ばない。根拠にしない。
- `scsitest.asm` 1390–1404 `test_number16`、593–634 `log_irq2`: IRQ2 の試験と記録処理。結果は記録されていないため、F22 の問いの出典として参照するにとどめる。
- 二次出典（Zeograd、Stum の System Card メソッド表）: 本回は取得していない（NOT_RUN）。版別の所在や値は転載しない。
- ROM の内容、逆アセンブル、BIOS のバイト列は読んでいない。`./rom/` は参照していない。純正 `syscard3.pce` は使わない。

## 6. 残る UNKNOWN と決着条件

| 項目 | 決着の方法 |
| --- | --- |
| F05–F08（入力・作業 RAM）、F13（戻り X・Y）、F20（副作用） | 公開できる実行可能な callee でのみ測定できる。現時点では実行可能な callee がないため BLOCKED_ABI |
| F04、F11、F12（mode と戻り A の意味） | 無ディスク、busy 中、不正な mode の各ケースで実測する。成功値は定義しない |
| F16、F17（フラグ、スタック） | 呼出し前後の P・SP を全比較する |
| F19（MPR0–6） | 呼出し中の MPR を実測で比較する。二次出典の値は使わない |
| F22（IRQ）、F24（媒体なし・待機）、F25（失敗条件） | IRQ 禁止の有無を変えた試験、媒体なしの合成ケース、busy 中の呼出しで測定する |

## 7. 次の作業

1. 呼出側の残りの経路（HuC の他の例、他の公開 caller）を調べ、mode の値の範囲を広げる。
2. 版別の原文照合（System Card 1.0、2.x、3.0、TG3.0）。人手で行い、結果をこの台帳に記録する。
3. 公開可能な callee の確保は、#1（ROM 配置）・#6（実コア試験基盤）・#8（来歴レビュー）と合わせて決める。
4. 合成 CD メディアの試験は、本台帳の UNKNOWN の決着条件に従う。
