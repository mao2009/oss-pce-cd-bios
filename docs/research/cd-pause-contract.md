# CD_PAUSE (`$08`、候補 `$E018`) 呼出契約の根拠台帳（Issue #254）

親: #17。対象は呼出側（caller）の証拠と、被呼出側（System Card の CD_PAUSE 本体）の未確定項目の整理のみ。

**状態: BLOCKED_ABI。release_eligible=false。DEV_CALLABLE なし。VERIFIED / PASS の主張なし。provenance は PENDING。**
System Card の CD_PAUSE は一度も実行していない。本台帳の全項目は `SOURCE_FACT` / `HYPOTHESIS` / `UNKNOWN` のいずれかである。
`SOURCE_FACT` は「出典が呼出側または記述にそう書いている」ことであり、被呼出側の挙動の証拠ではない。

## 0. 規則

| 状態 | 意味 |
| --- | --- |
| `SOURCE_FACT` | 固定 revision の公開ソース本文、または公開ページ本文にある記述そのもの。被呼出側の挙動には使わない。 |
| `HYPOTHESIS` | 第三者の表・文書・HLE 実装からの推測。実行で確認していない。 |
| `UNKNOWN` | 出典が不足している。推測で埋めない。 |

- 禁止する状態語: `VERIFIED` / `OBSERVED` / `PASS` / `DEV_CALLABLE`。status 欄に現れた場合は `tests/host/test_cd_pause_contract.py` が失敗する。
- 実行証拠（#118 系の READ 計測、ホスト算術、合成テスト）は CD_PAUSE の証拠にしない。
- ライセンス不明または未確定の出典は、内容を引用せず、場所と記述の存在だけを記録する（`docs/provenance.md`、`docs/oss-audit-systemcard.md`）。
- `./rom/` の内容は読んでいない。System Card の BIOS バイト、逆アセンブル、商用データは使っていない。

## 1. 出典

| ID | 出典（固定 revision） | 場所 | ライセンス・出自 | 扱い |
| --- | --- | --- | --- | --- |
| S1 | pce-devel/huc `54f5c73606c1e7142e8095c727cab2767e2b3cb3`、`include/hucc/pcengine.inc` | 476 行（`cd_pause = $00:E018, FUNC`）。330–343 行（`_al`〜`_dh` の別名） | BSL-1.0 の表示。著作権者名なし。出自は PENDING | SOURCE_FACT（定義の存在のみ） |
| S2 | 同 `include/hucc/hucc-systemcard.asm` | 107（宣言コメント）、109–112（`_cd_pause .macro` / `system cd_pause` / `cly` / `.endm`） | BSL-1.0（John Brandwood 2024）。出自は PENDING | SOURCE_FACT（呼出側マクロ） |
| S3 | 同 `include/hucc/hucc-systemcard.h` | 90（`extern unsigned char __fastcall __macro cd_pause( void );`） | BSL-1.0（同上） | SOURCE_FACT（C 側の宣言） |
| S4 | 同 `include/hucc/core-kernel.asm` | 488–516（`call_bios`。500 の `sty`、502–507 の MPR7 退避と切替、509 の `jsr $E000`、511–514 の MPR7 復元、516 の `rts`） | BSL-1.0（John Brandwood 2021–2024） | SOURCE_FACT（呼出ラッパー） |
| S11 | 同 `include/hucc/core.inc` | 41–44（`system .macro`: `ldy #<` の後に `jsr call_bios`） | BSL-1.0（John Brandwood 2021–2024）。出自は PENDING | SOURCE_FACT（呼出側マクロ） |
| S5 | 同 `include/hucc/hucc-systemcard.asm` | 145–159（`_cd_unpause`。`cd_play` を呼ぶ。CD_PAUSE は呼ばない） | BSL-1.0 | SOURCE_FACT（再開経路は CD_PLAY） |
| S12 | 同 `include/hucc/hucc-systemcard.asm` | 28–35（`CD_CURRPOS = %11000000` は 33 行。`CD_LEADOUT` と同値）、155（`_cd_unpause` が `_bh` に `CD_CURRPOS` を設定） | BSL-1.0。出自は PENDING | SOURCE_FACT（定数の値。`$C0` の出典） |
| S6 | 同 `doc/hucc/hucc-function-reference.md` | 924–925（`cd_pause( void );` の説明） | ルート LICENSE は混在・不確実と既存監査に記録。PENDING | SOURCE_FACT（文書の記述のみ。挙動の証拠ではない） |
| S7 | 同 `doc/huc/clibref.txt` | 758–762（`cd_pause();   [3.01+]`） | 同上 | SOURCE_FACT。`[3.01+]` は HuC コンパイラの版であり System Card の版ではない |
| S8 | Stum（Michael Stum）「PC Engine CD-ROM² BIOS Method offsets」`https://www.stum.de/2025/pcenginebiosoffsets/` | 表の `cd_pause` 行（JSR 列 `$E018`、1.0 `$F01B`、2.0 `$EF94`、2.1 `$EF94`、3.0 `$EF94`、TG2.0 `$EFAD`、TG3.0 `$EFAD`）。本文冒頭の MPR の記述 | ページ末尾に CC BY-NC-SA 4.0 の表示。値は引用のみ、表を複製しない | HYPOTHESIS（第三者の表。未検証）。MPR の記述も HYPOTHESIS |
| S9 | Zeograd「PC-Engine Documenation: The System card」`https://www.zeograd.com/download/pce_bios.html` | `CD_PAUSE, Function #$08` の一覧行。本体の IN/OUT 記述はない。冒頭の「system-card 3.0 を指す」 | ライセンス表示は確認できず。PENDING | SOURCE_FACT（記述の存在）。CD_PAUSE の本体は UNKNOWN |
| S10 | mckayemu/hugo `64ed226da2288738781743eeef228c069e8973a4`、`bios.c` | 574–589（`case CD_PAUSE`。HLE で A=0 を設定し N/T/Z を更新して RTS） | GPL-2.0（`docs/cd-oss-survey.md`）。REFERENCE-ONLY。コードは引用・翻訳しない | HYPOTHESIS（HLE。BIOS の ABI 証拠ではない） |

除外（読み取り・引用をしない）: `include/huc/cdrom.asm`（ライセンス不明として除外）、`include/huc/system.inc`、`examples/asm/elmer/kickc/bios.h`（ライセンス未確認）。
注記: 固定 revision 全体を `grep` した際、除外ファイルの行が検索結果に一度出力された。その内容は本台帳の根拠に使っていない。

## 2. 呼出側の経路（S1–S5 から導く事実）

HuC のマクロ `_cd_pause` は引数を設定せず、`system cd_pause` の後に `cly` を実行する。`system` は `ldy #<cd_pause`（Y=`$18`）の後に `call_bios` を呼ぶ。
`call_bios` は Y を自己書換え用に保存し、MPR7 を退避して bank 0 を MPR7 に割り当て、`jsr $E000`（下位バイトのみ自己書換え）を実行し、戻ると MPR7 を復元して RTS する。
この経路の命令列から次が言える（呼出側の挙動。被呼出側の挙動ではない）:

- BIOS の入口で A は呼出直前の値、X は呼出直前の値、Y は `$18`（S11 のマクロと S4 の `sty`）。wrapper は A を `pha`/`pla` で保存する。X と Y は wrapper で変更しない（Y は `sty` の後も変わらない）。
- BIOS の戻り後、A は BIOS の値のまま。X も BIOS の値のまま。Y は `cly` で 0 になる。
- 戻り後の N=0、Z=1 は `cly` による。C/V/D/I/T は wrapper とマクロの命令で変更されない。

## 3. フィールド台帳

| ID | 項目 | 状態 | 内容 | 出典 | 備考 |
| --- | --- | --- | --- | --- | --- |
| F01 | 引数の有無 | SOURCE_FACT | `_cd_pause` は引数を設定しない（C 側は `void`） | S2 109–112、S3 90 | 呼出側の事実のみ |
| F02 | 入力 A（BIOS 入口の値） | SOURCE_FACT | 呼出直前の A がそのまま入る（wrapper が保存・復元） | S4 502–514 | マクロは A を設定しない |
| F03 | 入力 A の BIOS による読取り | UNKNOWN | 読むかどうか未確認 | - | 入力感度の実測が必要 |
| F04 | 入力 X（BIOS 入口の値） | SOURCE_FACT | 呼出直前の X がそのまま入る（wrapper は X を変更しない） | S4 500–516 | |
| F05 | 入力 X の BIOS による読取り | UNKNOWN | 未確認 | - | |
| F06 | 入力 Y（BIOS 入口の値） | SOURCE_FACT | `$18`（`ldy #<cd_pause` の下位バイト）。BIOS がこの値を読むかは別 | S11 41–44、S2 110、S4 500 | `$18` の意味づけは UNKNOWN |
| F07 | 入力 Y の BIOS による読取り | UNKNOWN | 未確認 | - | |
| F08 | 入力 P（BIOS 入口の N/Z） | SOURCE_FACT | `pla` で復元された呼出側 A により N/Z が決まる。C/V/D/I/T は呼出側の値のまま | S4 509–514 | |
| F09 | `$20F8`〜`$20FF`（`_al`〜`_dh`）の呼出側の書込み | SOURCE_FACT | マクロは書き込まない（引数なし） | S1 330–343、S2 109–112 | 別名の定義のみ |
| F10 | `$20F8`〜`$20FF` の BIOS による読取り・書込み | UNKNOWN | 未確認 | - | 作業 RAM 差分の実測が必要 |
| F11 | 戻り A（wrapper 経路） | SOURCE_FACT | BIOS の A がそのまま返る（`pha`/`pla`）。C 型は `unsigned char` | S3 90、S4 511–514 | |
| F12 | 戻り A の意味（成功・失敗の値） | UNKNOWN | 値の意味は出典になし。HuC は値を読まない | S2 | Zeograd の `[$00] ok / [$01] error` は CD_PLAY(`$06`) の項目であり、CD_PAUSE に転用しない（S9） |
| F13 | 戻り X | SOURCE_FACT（経路）／UNKNOWN（意味） | 経路上 BIOS の X がそのまま返る。意味は未確認 | S4 | 呼出側は X を読まない |
| F14 | 戻り Y | SOURCE_FACT | `cly` により 0 | S2 111 | BIOS の Y は破棄される |
| F15 | 戻り N/Z | SOURCE_FACT | `cly` により N=0、Z=1 | S2 111 | マクロ後の P は BIOS の N/Z に依存しない |
| F16 | 戻り C/V/D/I/T | SOURCE_FACT（経路）／UNKNOWN（BIOS の値） | wrapper とマクロは変更しない。BIOS の戻り時の値は未確認 | S4、S2 | |
| F17 | SP（呼出側から見た差分） | SOURCE_FACT（経路）／UNKNOWN（BIOS 内部） | wrapper は 1 バイトの `pha`/`pla` の対で、BIOS が釣り合っていれば差分は 0 | S4 | BIOS の使用量は未確認 |
| F18 | MPR7 | SOURCE_FACT | wrapper が bank 0 を割り当て、呼出側の値を復元する | S4 502–514 | 「呼出側の MPR7 の保存」であり、BIOS の保存規則ではない |
| F19 | MPR0・MPR1 の前提 | HYPOTHESIS | 「BIOS は MPR0=`$FF`、MPR1=`$F8` を前提とする」と第三者の本文が記す | S8（本文） | 未検証 |
| F20 | MPR2〜MPR6 の BIOS による変更 | UNKNOWN | 未確認 | - | |
| F21 | IRQ・CLI/SEI | SOURCE_FACT（経路）／UNKNOWN（BIOS） | wrapper とマクロに SEI/CLI はない。BIOS の割込み状態の変更は未確認 | S2、S4 | |
| F22 | 副作用の対象（CD-DA 状態、停止位置、再開位置） | HYPOTHESIS | 文書は「CD-DA 再生中の一時停止」と記す（対象の説明）。挙動は未確認 | S6、S7 | 再生していない場合の挙動は UNKNOWN |
| F23 | 再開との関係 | SOURCE_FACT（経路）／UNKNOWN（BIOS） | HuC の `cd_unpause` は CD_PAUSE ではなく CD_PLAY を呼ぶ（`$C0`）。CD_PAUSE の後に再開する正しい経路は未確認 | S5、S12 | `$C0`（CD_CURRPOS）は S12 の 33 行で定義される（`$C0` の出典）。pcengine.inc（S1）には定義がない。CD_PLAY の開始位置種別としての意味は `docs/research/150-cd-play-abi.md` §1 参照 |
| F24 | 失敗条件（ディスクなし、データトラック、非再生中の呼出し） | UNKNOWN | 出典に本体の記述がない（S9 は CD_PAUSE の本体を持たない） | S9 | HLE（S10）は常に A=0 で、エラー経路がない。証拠にしない |
| F25 | 入口エントリ `$E018` | HYPOTHESIS | 第三者の表は全 revision で JSR 列 `$E018` と記す。HuC の定義と一致 | S1 476、S8 | 本体アドレスは未検証 |
| F26 | 本体アドレス（3.0 は `$EF94`、TG3.0 は `$EFAD`） | HYPOTHESIS | 第三者の表の値。実機・実コアでは未確認。ROM を読んでいない | S8 | 本台帳ではこの値を実装・検証に使わない |
| F27 | 同期・完了待ち・タイムアウト | UNKNOWN | 出典なし | - | |
| F28 | HLE の戻り（Hu-Go） | HYPOTHESIS | A=0 を返す、N/T/Z を再計算する。HLE であり証拠にならない | S10 | GPL-2.0。引用・翻訳しない |

## 4. System Card 3.0 固有の不一致

1. **版の指定が一致しない。** HuC の定義（S1）、マクロ（S2、S3）、呼出ラッパー（S4）は System Card の版を示さない。`[3.01+]`（S7）は HuC コンパイラの版であり、System Card 3.0 の証拠ではない。
2. **同じ入口でも本体が版ごとに異なる（HYPOTHESIS）。** 第三者の表（S8）は `$E018` を全版で同じ JSR 先とする一方、本体は 1.0 `$F01B`、2.0/2.1/3.0 `$EF94`、TG2.0/TG3.0 `$EFAD` と記す。3.0 と TG3.0 の本体は一致しない。
3. **3.0 を名乗る一次情報に CD_PAUSE の本体がない（UNKNOWN）。** S9 は「3.0 を指す」と冒頭で書くが、CD_PAUSE は一覧行のみで IN/OUT の記述がない。CD_PAUSE の 3.0 固有の引数・戻り値・失敗条件は、この出典では確定できない。
4. **Hu-Go は版を持たない HLE である。** S10 は 3.0 の証拠にならない。
5. **CD_PLAY の規約を転用しない。** `[Reg A] error status`（S9）は CD_PLAY(`$06`) の項目であり、CD_PAUSE の戻り規約を示さない。

## 5. 決着に必要な観測（未実施）

| 項目 | 必要な観測 |
| --- | --- |
| F03、F05、F07、F10、F20 | 呼出し前後の A/X/Y/P、MPR0〜7、`$20F8`〜`$20FF`、作業 RAM の全差分（実行可能な System Card の CD_PAUSE が必要） |
| F12、F13、F24 | CD-DA 再生中、非再生中、ディスクなし、データトラック再生中の各呼出後の A/X/Y/P |
| F22、F23 | CD_PLAY → CD_PAUSE → CD_PLAY(`$C0`) の前後の CD-DA 状態と位置 |
| F25、F26 | 版ごとの実機またはユーザー所有の System Card での入口と本体の対応。リポジトリに BIOS は含めない |

## 6. 非実行範囲

- System Card の CD_PAUSE（`$E018`）は実行していない。`rom/` の内容は読んでいない。
- 実コア（Geargrafx）による呼出し、実機確認は行っていない。
- CD_PAUSE の HuC6280 実装、互換性宣言、`spec/api_contracts.json` と `docs/api-inventory.md` の変更はしていない。
- 親 #17 の受け入れ条件は置き換えない。
