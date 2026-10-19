# CD_SEEK（スロット `$04`、候補 `$E00C`）呼出側ABI・位置指定 根拠台帳（Issue #214）

**状態: callee の ABI は未確定。ROM 実装なし。** spec=UNKNOWN（callee）、caller 側は一部 SOURCE_FACT、
implementation=NOT_STARTED、verification=NOT_RUN。親 Issue #13、関連 #3 / #5 / #6 / #8 / #11 / #12 / #173。

本台帳は、呼出側（caller）から見える事実と、呼出先（callee）の仮説・未確定を分けて記録する。
名前（`cd_seek`）だけで ABI を確定しない。未確定の欄には値を補わない。

## 1. 規則

状態の意味:

- `SOURCE_FACT`: 固定版ソースの本文に書かれた**呼出側**の動作（何を書き、どの命令で呼ぶか）。呼出先の振る舞いではない。
- `CALLER_INFERENCE`: 呼出側の書き方からの推論。呼出先の確認ではない。
- `HYPOTHESIS`: 検証すべき仮説。根拠は呼出側のコメントや命名だけ。
- `UNKNOWN`: 出典が不足している。値の欄は `UNKNOWN` と書く。
- `BLOCKED`: 測定が必要だが、実行可能な呼出先がないため測定できない。

禁止事項:

- `VERIFIED` と `OBSERVED` は使わない。callee を実行していないため、どの行にも付けられない。
- 側 `C`（callee）と `R`（関係）の行は `HYPOTHESIS` / `UNKNOWN` / `BLOCKED` のみ。呼出側のソースから callee の事実を導かない。
- 状態が `UNKNOWN` / `BLOCKED` の行は、値の欄を `UNKNOWN` とする。
- ホスト算術（§4 のテスト）は BIOS の証拠ではない。表の値が MSF として成立するかどうかの判定にだけ使う。

## 2. 出典

HuCC（`pce-devel/huc`）の固定版を使う。

- 固定版: commit `54f5c73606c1e7142e8095c727cab2767e2b3cb3`
- 取得元: `https://codeload.github.com/pce-devel/huc/tar.gz/54f5c73606c1e7142e8095c727cab2767e2b3cb3`
- アーカイブ SHA-256: `9fe91254051381077bd4ea217b718ec033a52da185283ae9479c1abac365311b`（取得して一致を確認。展開物は読むだけで実行していない）

| ID | 出典（固定版内のパス） | 行 | ライセンス表示 | 用途 |
| --- | --- | --- | --- | --- |
| S1 | `include/hucc/pcengine.inc` | 467–472（`cd_seek = $00:E00C, FUNC`） | BSL-1.0（著作権者の表示なし。冒頭 8–10 行はライセンス表示のみ） | 名前と入口の宣言（呼出側） |
| S2 | `include/hucc/core-kernel.asm` | 500–516（`call_bios`） | BSL-1.0（8 行に "Copyright John Brandwood 2021-2024."） | 呼出し経路（trampoline） |
| S3 | `include/hucc/core.inc` | 41–45（`system` マクロ: `ldy #<\1` と `jsr call_bios`） | BSL-1.0（8 行に "Copyright John Brandwood 2021-2024."） | マクロの展開 |
| S4 | `examples/asm/elmer/ted2-core-gulliver/gulliver.asm` | 316–335（seek の引数設定と `system cd_seek`）、346–371（read の引数設定と `system cd_read`、367 行）、536 行以降（`gulliver_lba` 表。本台帳には表の値を転載しない） | BSL-1.0（8 行に "Copyright John Brandwood 2022."） | 呼出側の実例 |
| S5 | `examples/asm/elmer/include/movie.asm` | 246（`BUFFER_1ST_BANK = $6A`） | BSL-1.0（13 行 "Modifications copyright John Brandwood 2022." は改変部分の表示。元のファイルの権利状態は未確認） | S4 が include する定数 |
| S6 | `examples/asm/elmer/ted2-test-sherlock/sherlock-hack.asm` | 170（ゲーム側の呼出し点へのフック）、404–408（`begin_seek` → `jmp $E00C`） | BSL-1.0（12 行 "Copyright John Brandwood 2024."）。ただしゲーム実行コードへのパッチ | 補助のみ。引数の設定はこの断片に無いので証拠にしない |
| S7 | `include/huc/equ.inc` | 34–44（`__al` などの別名で `$20F8`〜`$20FF` を定義。`_al` などの名前は S1 の `pcengine.inc` 330–343 が定義） | 表示なし（下記の注） | 番地の定義のみ |

除外する出典:

- `examples/asm/elmer/kickc/bios.h` 30 行: 名前の宣言のみ。ライセンス表示は Boost 1.0（8–10 行）。除外の理由は #11 と同じ扱い（呼出しの根拠に使わない）。
- `examples/asm/elmer/cd-core-scsitest/scsitest.asm` 1023 行の「Perform a cd_seek」と 1100 行の「Read 1 sector, i.e. cd_seek」: 同ファイルの自作ルーチン `test_cd_read`（SCSI READ）への注釈であり、System Card の呼出しではない。
- `examples/asm/elmer/ted2-test-sherlock/dat2csv.c` 334、338 行: ログ文字列のみ。
- Hu-Go! `bios.c`: 固定版ツリーに該当ファイルがない（`**/bios.c` の検索で 0 件）。`doc/huc/huc3_doc.htm` 344 行の言及のみで、HLE である根拠にはしない。
- `include/huc/system.inc` 69 行: `cd_seek = $E00C` の宣言のみ。

注（ライセンス）: BSL-1.0 は Boost Software License 1.0 の SPDX 識別子で、各ファイル冒頭の "Boost Software License, Version 1.0" の表示と同じ。著作権表示（氏名と年）はファイルごとに異なるため、上表の欄に個別に記した。固定版ルートの `LICENSE` は、コレクション全体の法的地位を「正確に述べることはほぼ不可能」とし、例外として MagicKit、TGEmu、GCC 由来のテスト、Ulrich Hecht の変更部分（BSD 系）だけを挙げる。ファイル単位のライセンス表示（BSL-1.0）がない S7（`equ.inc`）は、法的状態を未確定として扱う。

固定版全体で `cd_seek` を含む行は 9 行。呼出し（`system cd_seek` と `jmp $E00C`）は S4 と S6 の 2 箇所だけである。
`docs/oss-reuse-audit-r3.md` の「呼び出し無し」は `hucc-systemcard.asm` と `core-*.asm` の範囲の記録であり、
固定版全体の記録ではない。本台帳がその範囲を examples まで広げた。

## 3. 項目別台帳

| ID | 側 | 項目 | 状態 | 値 | 出典 | 理由・確度 | 残る識別テスト |
| --- | --- | --- | --- | --- | --- | --- | --- |
| K1 | K | 呼出し経路 | SOURCE_FACT | `Y=$0C` で `call_bios` へ入り、`$E000+Y`（`$E00C`）を `jsr` する。呼出し前に MPR7 を `$00` に切り替え、戻りで元の値へ戻す。A は pha/pla で素通しする | S2、S3 | HuCC の trampoline の本文（呼出側の実装）。callee の入口は C1 | — |
| K2 | K | `$20F8`（`_al`） | SOURCE_FACT | 呼出側は 1 を書く | S4 316–317 | 書込みの事実のみ。意味は C3 | 入力感度試験（§7） |
| K3 | K | `$20FA`（`_bl`） | SOURCE_FACT | 呼出側は `BUFFER_1ST_BANK`（`$6A`、S5）を書く | S4 318–319、S5 246 | 値の意味は C3 | 同上 |
| K4 | K | `$20FF`（`_dh`） | SOURCE_FACT | 呼出側は 3 を書く。同じ値を `cd_read` の前にも書く（S4 348–349） | S4 320–321 | 意味は UNKNOWN（C3） | 同上 |
| K5 | K | `$20FC` / `$20FD` / `$20FE`（`_cl` / `_ch` / `_dl`） | SOURCE_FACT | `gulliver_lba` の 3 バイトを `_dl`、`_ch`、`_cl` の順に書く（添字 `X` は映像番号の 4 倍。`dd` 1 件を 4 バイトとみなす添字計算。PCEAS の `dd` 規約は未確認） | S4 313–332 | 書込みの事実のみ。`$20F8`〜`$20FF` の定義は `include/huc/equ.inc` 34–44 行（S7、`__al` などの別名）と `pcengine.inc` 330–343 行（S1） | 同上 |
| K6 | K | 3 バイト値の桁の重み | CALLER_INFERENCE | `_dl` が最下位、`_cl` が最上位 | S4 357–364 | 呼出側は read の前に 24 ビットで `adc #1`、`adc #0`、`adc #0` の順に加算しており、`_dl` を最下位として扱っている。呼出側内の一貫性のみで、PCEAS の `dd` の順序規約は未確認 | 同上 |
| K7 | K | 戻り値の使用 | SOURCE_FACT | 呼出側は `system cd_seek` の後で A、X、Y、P を読まず、エラー判定もない。次の命令は `irq_cnt` の読出し（表示用） | S4 335–339 | 「戻り値が無い」とは言えない。呼出側が読まないという事実のみ | C2、C8 |
| K8 | K | 割込みの扱い | SOURCE_FACT | 呼出し前に `irq_cnt` を 0 にする。`SEI` / `CLI` で囲まない | S4 333 | 呼出側の事実。callee の割込み方針は C10 | C10 |
| K9 | K | 完了の待ち方 | HYPOTHESIS | 呼出側は「時間が一定でなく、セクタを読んだ直後に終わる」と書く | S4 318–320 | 呼出側の期待。同期の戻りか、非同期かは不明 | 完了時刻の測定（§7） |
| K10 | K | 呼出側の MPR とスタック | SOURCE_FACT | trampoline は MPR7 の切替えの前後で A を push/pull して素通しし、JSR の後で MPR7 を復元する。呼出側のスタック使用は K1 の範囲内 | S2 502–514 | callee のスタック使用は C7 | C7 |
| C1 | C | 入口 | HYPOTHESIS | `$E00C` | S1 | 呼出側の等号。callee は実行していない | §7 の入口確認 |
| C2 | C | A、X、Y、P の戻り | UNKNOWN | UNKNOWN | — | callee 未実行。K7 は呼出側の事実のみ | 戻り値の実測（BLOCKED、§7） |
| C3 | C | `$20F8`〜`$20FF` の入力の意味 | HYPOTHESIS | UNKNOWN（名前からの推定。呼出側のコメントは「読込み」を示すが、件数やモードの意味は書かれていない） | S4 316–321、S5 | 呼出側のコメントと同形の書き方のみ。callee の解釈は未確認 | 入力感度試験（§7） |
| C4 | C | 位置の基準（絶対 LBA、INDEX 01 相対、MSF 起点、基準値相対） | UNKNOWN | UNKNOWN | S4、S5 | 候補は四つ（§4、#11 §4 と同じ式）。K5 と K6 の表は MSF として全件は成立しない（§4 のテスト）。ただし callee がどれを使うかは未確認 | INDEX 01 ≠ 0 の合成ディスクで READ LBA を比べる（§7） |
| C5 | C | 数値表現（BCD、binary） | UNKNOWN | UNKNOWN | S4 | 表の全件は BCD MSF でも binary MSF でも成立しない。binary の 24 ビット LBA という読みは CALLER_INFERENCE の候補にとどまる | C4 と同じ |
| C6 | C | トラック番号、INDEX 01 との関係 | UNKNOWN | UNKNOWN | S4 | 呼出側 S4 は `cd_base`（CD_BASE）を呼ばずに `cd_seek` と `cd_read` を使う。基準値の継承は観測できない | §5（R1） |
| C7 | C | 保存されるレジスタ、MPR、スタック | UNKNOWN | UNKNOWN | — | K1 の MPR7 復元は trampoline の事実。callee 側の変化は未知 | MPR0–7 と作業 RAM の前後差分（§7） |
| C8 | C | 失敗値、busy、timeout | UNKNOWN | UNKNOWN | S4（判定なし） | 呼出側はエラー処理を書いていない。#12 の CD_READ の失敗規約とは別に確認する | 無ディスク、範囲外 LBA、読込み中の seek（§7） |
| C9 | C | 作業 RAM への書込み範囲 | UNKNOWN | UNKNOWN | — | 呼出側は `$20F8`〜`$20FF` を入力に使うが、callee が書き換えるかは未確認 | 前後差分（§7） |
| C10 | C | 割込み（IRQ）の扱い | UNKNOWN | UNKNOWN | — | K8 は呼出側の事実のみ | IRQ 有無での比較（§7） |
| C11 | C | 読込みの副作用（バッファへの書込み） | HYPOTHESIS | 呼出側コメントに従えば、seek の中で 1 セクタの読込みが起きる | S4 316–320 | コメントのみ。`_bl` への書込みが実際に起きるかは未確認 | 読込み先バンクの観測（§7） |
| R1 | R | CD_BASE（#11）との関係 | UNKNOWN | UNKNOWN | S1、S2、S4、S5 | 固定版で `cd_base`（`$E006`）を呼ぶのは `include/hucc/core-startup.asm` 300 行の 1 箇所だけ（2ISO 経路）。S4 は `cd_base` を呼ばずに `cd_seek` と `cd_read` を使う。CD_SEEK が CD_BASE の基準を使うかは、ソースから決まらない | #11 の枠組みで、CD_BASE の後の `cd_seek` と READ LBA を比べる（callee 実行が前提） |
| R2 | R | CD_READ（#12）との関係 | HYPOTHESIS | seek と read が同じアドレス空間を指す（呼出側の仮定） | S4 335、357–367 | 呼出側は seek に表の値、続く read に同じ表の値に 1 を加えた値（`adc #1`）を与え、同じ 24 ビット表現（K5）を使う。#12 台帳（`docs/cd-read-contract.md`）の「Block number」行（HYPOTHESIS）と整合するが、観測していない | seek と read の READ LBA の比較（§7） |
| R3 | R | 観測可能性 | BLOCKED | UNKNOWN | — | 実行可能な System Card の `$E00C` が無い。`cd-base-abi-ledger.md` §4a の DEV ROM の測定は `$E006` だけで、`$E00C` は未測定。固定版 Geargrafx での `$E00C` の実行経路も未確認 | NOT_RUN（§7） |

## 4. ホスト単体テスト（表と値の不変条件）

`tests/host/test_cdseek_abi_ledger.py` は次を検査する。これは台帳の規則と判定器の挙動を確かめるもので、BIOS の証拠ではない。表の算術は合成値で検査し、S4 の実表は本リポジトリに含めない。

- 状態の語彙に `VERIFIED` と `OBSERVED` が無い。側 `C` と `R` の行は `HYPOTHESIS` / `UNKNOWN` / `BLOCKED` のみ。
- 状態が `UNKNOWN` / `BLOCKED` の行は値を `UNKNOWN` とする。
- `SOURCE_FACT` / `CALLER_INFERENCE` / `HYPOTHESIS` の行は、§2 に定義した出典 ID（S1〜S6）を一つ以上引用する。
- 入口の値は、S1 の等号 `cd_seek = $00:E00C` を `call_bios` の `$E000 + Y`（`Y=$0C`）で再計算した値と一致する。
- 合成表（テスト内の値）で、件数 38、24 ビット、先頭 37 件の単調増加、末尾 1 件の順序外れを検査する。判定器（binary MSF / BCD MSF）は合成値で合格と不合格を確かめる。
- 固定版の S4 表については、取得時にホストで一回限り確認した（表は本リポジトリに含めない）: 38 件（`$00`〜`$25`）、すべて 24 ビットの整数。`$00`〜`$24` の映像表 37 件は単調増加し、`$25` は read 速度試験用のセクタで映像の LBA ではないため順序を外れる。38 件中 34 件（例: `$018660`）は BCD MSF でも binary MSF でも成立しない。この確認はテストとして再現されないため、再確認は §5 の手順で行う。
  このため表全体を MSF として読むことはできず、C4 と C5 は UNKNOWN のままとする。

## 5. 独立検証（再確認の手順）

HuCC 固定版は、アーカイブを新しい空ディレクトリに展開して読む。展開物は実行しない。

```sh
curl -sL -o huc.tar.gz https://codeload.github.com/pce-devel/huc/tar.gz/54f5c73606c1e7142e8095c727cab2767e2b3cb3
sha256sum huc.tar.gz    # 9fe91254051381077bd4ea217b718ec033a52da185283ae9479c1abac365311b
tar xzf huc.tar.gz -C <空ディレクトリ>
grep -rIn -E 'cd_seek|cd_base|cd_read' <空ディレクトリ>
```

本台帳の作成時に、上記で展開して行番号を確認した。#11（`docs/cd-base-abi-ledger.md` §2）と #12（`docs/cd-read-contract.md`）の記述は、
この固定版で `cd_base` と `cd_read` の呼出し箇所を見直した。#11 台帳（`docs/cd-base-abi-ledger.md` §3 の (4)）の「CD_READ の呼出し 5 箇所」は HuCC（`include/hucc`）に限った数（`hucc-systemcard.asm` の 4 箇所と `core-kernel.asm` の 1 箇所）であり、
`include/huc` には別に 6 箇所（`ac_lib.asm`、`cdrom.asm` の 3 箇所、`startup.asm` の 2 箇所）があり、examples には S4 の 367 行と `ipl-scd.asm` の 236 行がある（`examples/asm/elmer/doc/syscard-cd-ipl.txt` の `jsr cd_read` は逆アセンブル表の文字列で、ソースではない。この件数は文書内の全件ではない）。

## 6. 依存関係と PR の境界

- #211〜#213 が変更中の契約判定コード（`tools/contracts.py` など）は変更しない。本 PR は docs と host テストだけで、ROM 実装と純正 BIOS の JSR を含まない。
- 依存: 本台帳は #11 と #12 の既存の表記を参照するが、ファイルは変更しない。#213 の判定コードには依存しない。

## 7. 残る UNKNOWN と次の識別テスト

| 残る項目 | 識別テスト | 前提 |
| --- | --- | --- |
| C3、K2〜K4: `_al` / `_bl` / `_dh` の意味 | `_al`、`_bl`、`_dh`、`_cl` / `_ch` / `_dl` を一つずつ変えた CD_SEEK の後の READ 系の観測（作業 RAM を監視） | 実行可能な callee（BLOCKED、R3） |
| C4、C5: 位置の基準と数値表現 | INDEX 01 ≠ 0 の合成ディスクで、seek 後の READ LBA を `docs/cd-base-abi-ledger.md` §4 の四候補と比べる | 同上 |
| R1: CD_BASE との関係 | CD_BASE の後の CD_SEEK と READ LBA、CD_BASE なしの場合との比較 | 同上 |
| R2: seek と read のアドレス空間 | `X` の seek の後の `X+1` の read で、READ LBA を比べる | 同上 |
| C2、C8: 戻り値、busy、timeout | 無ディスク、範囲外 LBA、読込み中の seek | 同上 |
| C7、C9、C10: 副作用、割込み | CD_SEEK の前後の MPR0–7 と作業 RAM の全差分、IRQ 有無での比較 | 同上 |
| C11: 読込み副作用 | 読込み先バンクの観測 | 同上 |

いずれも、実行可能な callee を得るまで NOT_RUN（BLOCKED）である。ROM への実装や、純正 BIOS の JSR は、この台帳の範囲に含めない。
