# Issue #148: CD_READ ($03 / 候補 `$E009`) 呼出側ABI台帳

Refs #148 / 親 #12。状態: **調査のみ。実装なし。VERIFIED の新規付与なし。既存 VERIFIED(T1-T5, ホスト転送層) は変更しない。**

## 0. 範囲と分類ルール

* 「caller前提」(呼出側が呼出前に満たす条件) と「callee副作用」(BIOSが呼出後に変える状態) を別表にする。
* 状態語彙は [cd-read-contract.md](../cd-read-contract.md) に従う。実コア上でSystem Card 3.0 calleeを実行した証拠は現在ゼロなので、callee項目は HYPOTHESIS / UNKNOWN / BLOCKED のみ。
* VERIFIED は #118 のホスト転送層(`$1800-$1804` 経由のSCSI READ(6))に限る。`$E009` の実行ではない。
* 商用BIOSのバイト列・逆アセンブルは参照していない。引用は公開ソースの言い換え。

## 1. 出典

| 出典 | 種別 | 用途 |
| --- | --- | --- |
| HuCC `pce-devel/huc@54f5c736` `hucc-systemcard.asm` L283-459, `core-kernel.asm` L486-552 | caller側ラッパ(第三者) | レジスタ入力、MPR退避、CLI |
| Hu-Go! `mckayemu/hugo@64ed226d` `bios.c` L378-573 | HLE(実BIOSではない) | モード、A/N/Z/T |
| [stum.de PC Engine CD-ROM² BIOS Method offsets](https://stum.de/2025/pcenginebiosoffsets/) | 第三者の公開表 (今回取得) | `cd_read` のジャンプ表入口 `$E009`、本体 `$EC6D`(1.0) / `$EC05`(2.0, 2.1, 3.0) / `$EC1E`(TG2.0, TG3.0)。BIOS呼出一般の前提: MPR7=`$00` を変えない、MPR0=`$FF`・MPR1=`$F8` を仮定 |
| [stum.de「Why do PC Engine CD-ROM² games have a second data track?」](https://www.stum.de/2026/pc-engine-second-data-track/) (2026-05-01, CC BY-NC-SA 4.0) | 第三者の二次資料 (監査で追加。#146 の W1) | `cd_read` は `recbase0` を加算して実LBAを得る / 与えるLBAはデータトラック先頭相対 / 16回読込み失敗後は `recbase1` を使う (`retry?`=`$2273`)。根拠は著者の解析で、一次資料(逆アセンブル/マニュアル)の特定は不可 |
| [PCE nesdev wiki CPU memory map](https://pce.nesdev.org/wiki/CPU_memory_map) | 第三者wiki | MPRとメモリ配置の一般論のみ。本文は今回の監査でも未取得(CD_READ固有の記述の有無は未確認。根拠には使わない) |

公式System Cardマニュアル(1.0)は公開URLを特定できなかった。stum.de は同マニュアルを所持すると述べるが本文にレジスタ規約は無い(取得結果で確認)。

## 2. caller前提(呼出側が用意する入力)

| 項目 | 内容 | 状態 | 出典 / 確定に必要な観測 |
| --- | --- | --- | --- |
| 入口 | JSR `$E009` (ジャンプ表; 本体アドレスはリビジョンで異なる) | HYPOTHESIS | stum.de。実コアで3.0を実行し、`$E009` から本体へ到達するPC軌跡を取る |
| ブロック番号 | `_cl:_ch:_dl` = 上位:中位:下位 (`$20FC-$20FE`) | HYPOTHESIS | HuCC。既知の異なる値(例: 0, 5, 15)で呼び、読まれたセクタをSCSI READ(6)のCDB LBAで観測 |
| 基点(絶対LBA か CD_BASE相対か) | 二次資料と HLE は「CD_READ が基準値(`recbase0`)を加算する相対LBA」を示す。Hu-Go! `bios.c` L388-392 は `$2274+3*[$2273]` から3バイトを加算 (`$2273`=stum.de の `retry?` と一致)。ただし stum.de の根拠は不明、Hu-Go! は実BIOSでなく、両者が独立かも不明。**#146 台帳との不一致は §6 に記録** | HYPOTHESIS (実コアのcallee観測は UNKNOWN) | CD_BASE(#11) 設定前後で同一ブロック番号を呼び、READ(6)のLBAの差を観測 |
| 転送モード `_dh` | 0=ローカルバイト(`_ax`), 1=ローカルセクタ(Hu-Goのみ), 2-6=RAMバンク(`_bl`開始, `_al`セクタ。HuCC が使うのは 3 と 6 のみで、2-6 の範囲は Hu-Go のみ), `$FE`=VRAMバイト, `$FF`=VRAMセクタ | HYPOTHESIS | HuCC(`_dh`=0,3,6,`$FF` を使用)+Hu-Go(0-6,`$FE`,`$FF`)。モード0-255を総当たりし、各戻りA・書込先メモリ差分を記録(実コア+System Card 3.0) |
| 全モード一覧、無効モードの結果 | - | UNKNOWN | 上記総当たりで未定義モードのA値と副作用を観測 |
| count 0 / バンク跨ぎ / VRAM奇数バイト / 上限 | Hu-Goは`$FF`・count 0でA=`$22`(HLEのみ) | HYPOTHESIS / UNKNOWN | 合成ディスク(#118のmake_cd_image)でcount=0,1,255,256とバンク末尾開始を実行し書込範囲を比較 |
| 事前MPR状態 | BIOS呼出一般: MPR7=`$00`, MPR0=`$FF`, MPR1=`$F8` | HYPOTHESIS (第三者表) | MPR0/1を意図的に変えて呼び、挙動差(戻りA、書込先、暴走)を観測 |
| 事前I/Oの状態(`$1800`系) | - | UNKNOWN | 同上。BIOSが先頭で読む`$1800`ビットをメモリ読出トレースで取得 |
| IRQ(I/CLI) | HuCCのオーバーレイローダがCLI後に呼ぶ。必須か否か不明 | HYPOTHESIS / UNKNOWN | SEI状態/CLI状態で同一呼出を実行し、成否・完了までのサイクルを比較 |
| 入力のA/X/Y | HuCCは`_ax`等をZP `$20F8-$20FF`に置く。A/X/Y自体が入力かは不明 | UNKNOWN | 呼出直前にA/X/Yを異値にして結果が変わるか観測 |

## 3. callee副作用・戻り値

| 項目 | 内容 | 状態 | 確定に必要な観測 |
| --- | --- | --- | --- |
| 成功時A | 0 | HYPOTHESIS | 実コアで呼出直後のAを記録 |
| 失敗時A、エラーコード体系 | 非0。個別値は不明 | UNKNOWN | 負例(範囲外LBA、媒体なし、読取エラー)ごとにAを記録 |
| P(N/Z/C/T/I/D) | Hu-GoはN/ZをAから設定、T=0 (HLE) | HYPOTHESIS | RTS直後のPをトレース(HuCCのTAX/BNEはBIOSのZ/Cを示さない) |
| X/Y保存 | 不明 | UNKNOWN | 呼出前後のX/Yを比較 |
| `$20F8-$20FF` | HuCCは呼出前後で`_bx.._dl`を退避=呼出側防御。BIOSが書き換える/保存するかは不明 | UNKNOWN | 呼出前後の`$20F8-$20FF`ダンプ差分、およびBIOS内のこの領域への書込トレース |
| 成功時MPR | 不明 | UNKNOWN | 呼出前後の MPR0-7 を全て記録 |
| 失敗時MPR | HuCCコメント「エラー時にMPR3を壊す」 | HYPOTHESIS | 失敗を起こす負例の前後でMPR3(とMPR2-6)を記録 |
| スタック | JSR/RTSの外側のみ。内部深さ・バランス | HYPOTHESIS / UNKNOWN | 呼出前後のSと最小Sを記録 |
| 完了方式/タイミング | Hu-Goは同期ループ | HYPOTHESIS | 呼出から戻りまでのサイクル数と、その間のIRQ発生有無 |
| ディスク末尾・count境界の動作 | 転送層(T3/T4)のみ VERIFIED(Geargrafx)。callee側の戻りAと部分書込は不明 | UNKNOWN | #118と同じ合成媒体(LBA0-15)で、`$E009`経由 LBA13 x3 (末尾跨ぎ)、LBA16以降を呼び、戻りAと書込済みバイト数を観測 |
| 無媒体時 (#133) | 転送層はGeargrafxでno-media実行不能(既存台帳T7) | UNKNOWN / BLOCKED | 無媒体で3.0を起動できる実コア設定の確立。またはエラーAの実機/別コア観測 |

## 4. 再現手順と負例(将来の実測用の設計。今回は未実行)

1. 合成媒体: `tools/make_cd_image.py` の16セクタfixture(LBA 0-15, 各セクタ固有パターン)を使う(#118と同一; BIOS/商用データなし)。
2. 呼出観測: 実コア(固定リビジョンGeargrafx)で、利用者が私的に用意したSystem Card 3.0 を `rom/` から読取専用で読み込み、既存の自作プローブROMから `JSR $E009` を実行。呼出前後のA/X/Y/P/S、MPR0-7、`$20F8-$20FF`、書込先を記録。BIOSバイトは成果物・CIに含めない。
3. 正例: LBA0, 5, 15 のcount 1; LBA5 count 3; 各モード(RAM/VRAM)。
4. 負例: LBA16, 最大LBA, LBA13 x3(末尾跨ぎ), count 0, 未定義`_dh`, 異なるMPR0/1, SEI/CLI差, 媒体なし。誤ったセクタ要求を同一判定器が拒否することも確認。
5. 判定: callee項目のVERIFIED化は(a)実コアでのSystem Card実行観測、(b)#124/#153の認証経路、の両方が必要。単一のGeargrafx観測は実機の証明にならない。

## 5. 実装可否判断

* CD_READ本体・`$E009` 配線: **不可(BLOCKED)**。入力、モード一覧、エラー体系、副作用が全てHYPOTHESIS/UNKNOWN。
* caller側のうち HuCC由来のスタブ規約(ブロック番号3バイト、`_dh`モード、成功A=0)は文書化のみ可。コード化は検証後。

## 6. 台帳間の不一致 (記録。解消済みではない)

* **CD_READ の基点 (本書 §2) と #146 台帳 (146 項目2/3/4)**: 本書は当初「基点: UNKNOWN」としたが、#146 は stum.de を根拠に「相対(`recbase0` 加算)」を HYPOTHESIS とした。監査で Hu-Go! HLE にも同じ加算があることを確認し、本書も HYPOTHESIS に揃えた。どちらも実コアのcallee実行なし。
* **2ISO 経路の矛盾 (`docs/cd-base-abi-ledger.md` §3 補足)**: HuCC は 2ISO 時に `recbase1` のみを設定する (`_cl=2`)。#146 は「通常読込みは `recbase0`、`recbase1` は失敗後のみ」の読み方で矛盾が成立しない**可能性**を挙げるが、これは推論で未測定。**矛盾は OPEN のまま**。`_cl=1/2` 後の CD_READ の LBA 比較と失敗注入の実測が必要。
* #146 と本書は、CD_BASE の `_cl` の意味 (`$01`=recbase0, `$02`=recbase1) を stum.de のみから得ており、独立した二つの証拠ではない。

## 7. 検証結果

* 追加の実コア実行: なし。新規VERIFIED: なし。
* `make check` の結果はPR本文に記載。
