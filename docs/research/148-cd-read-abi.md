# Issue #148: CD_READ ($03 / 候補 `$E009`) 呼出側ABI台帳

Refs #148 / 親 #12。状態: **調査のみ。実装なし。VERIFIED の新規付与なし。既存 VERIFIED(T1-T5, ホスト転送層) は変更しない。** §2 に呼出側使用台帳 (revision+line ピン留め) を追加し、`tests/host/test_cd_read_contract.py` で機械検査する。

## 0. 範囲と分類ルール

* 「caller前提」(呼出側が呼出前に満たす条件) と「callee副作用」(BIOSが呼出後に変える状態) を別表にする。
* 状態語彙は [cd-read-contract.md](../cd-read-contract.md) に従う。実コア上でSystem Card 3.0 calleeを実行した証拠は現在ゼロなので、callee項目は HYPOTHESIS / UNKNOWN / BLOCKED のみ。
* VERIFIED は #118 のホスト転送層(`$1800-$1804` 経由のSCSI READ(6))に限る。`$E009` の実行ではない。
* 商用BIOSのバイト列・逆アセンブルは参照していない。引用は公開ソースの言い換え。

## 1. 出典

固定リビジョン: HuCC `pce-devel/huc@54f5c73606c1e7142e8095c727cab2767e2b3cb3`。各行の sha256 は
`spec/api_contracts.json` の `sources` と一致することを取得時に確認済み (下記)。本節の行番号はすべてこの
リビジョンに対するもので、呼出側使用台帳 (§2) の各行が `revision` + `line` でピン留めする。

| 出典 (key) | 種別 | sha256 (`spec/api_contracts.json`) | 用途 |
| --- | --- | --- | --- |
| `huc:include/hucc/pcengine.inc` | CALLER | `b6a25d33bb787ea1fe550c6e04aff9f8ccaaa91b649a05a963be24b440b728a9` | `cd_read = $00:E009, FUNC` (L471)、ワーク領域 `$20F8-$20FF` の別名 (L329-343) |
| `huc:include/hucc/hucc-systemcard.asm` | CALLER | `4258b7c3e8751127c0615af3178e431d81043c25fa6cb2b6b1d51b566aec7ea9` | `_dh`/`_ax`/`_bx`/LBA三つ組の設定、MPR3/4 退避、戻り値の `TAX`/`CLY`/`LEAVE` |
| `huc:include/hucc/hucc-systemcard.h` | CALLER | `f2477642f867c3daeff00938534f3f8ef970d1a9403f44bd09e90894220b8a3b` | 各ラッパの `<reg>` 制約 (L87-99) |
| `huc:include/hucc/core.inc` | CALLER | `6949584b6d7312f59a4e59a1a4d696070a7278bbce7386f8d06fd63abdb14475` | `system` マクロ = `ldy #<\1; jsr call_bios` (L41-44) |
| `huc:include/hucc/core-kernel.asm` | CALLER | `ae183017c59c7adc3fe8851e7483c324b0387720bc757f618643a00a18942015` | `call_bios` (L500-516)、`get_file_lba` (L539-547)、`exec_overlay` (L578-614) |
| `huc:include/hucc/core-startup.asm` | CALLER | `fa340fca7d88a3603b1b55a8d5359b81e414dce527ac0ac7fd4837f0a5f5b37a` | 起動時 MPR0=`$FF` / MPR1=`$F8` (L125-128) |
| Hu-Go! `mckayemu/hugo@64ed226d` `bios.c` | HLE | `db3f227e09a73457bc9b0f2c54a3922ab9dca1e301f1b5029690c0d210972f22` | 参照のみ（ライセンス未確定、HLE であり実 BIOS の証拠にならない。根拠に使わない。`docs/cd-base-abi-ledger.md` §1）。HLE のモード/A/N/Z (原本ファームウェアではない。台帳 §2 には**含めない**) |
| [stum.de PC Engine CD-ROM² BIOS Method offsets](https://stum.de/2025/pcenginebiosoffsets/) | 第三者の表 | - | `cd_read` のジャンプ表入口 `$E009`、本体 `$EC6D`(1.0) / `$EC05`(2.0, 2.1, 3.0) / `$EC1E`(TG2.0, TG3.0)。BIOS呼出一般の前提: MPR7=`$00` を変えない、MPR0=`$FF`・MPR1=`$F8` を仮定 |
| [stum.de「Why do PC Engine CD-ROM² games have a second data track?」](https://www.stum.de/2026/pc-engine-second-data-track/) (2026-05-01, CC BY-NC-SA 4.0) | 第三者の二次資料 (監査で追加。#146 の W1) | - | `cd_read` は `recbase0` を加算して実LBAを得る / 与えるLBAはデータトラック先頭相対 / 16回読込み失敗後は `recbase1` を使う (`retry?`=`$2273`)。根拠は著者の解析で、一次資料(逆アセンブル/マニュアル)の特定は不可 |
| [PCE nesdev wiki CPU memory map](https://pce.nesdev.org/wiki/CPU_memory_map) | 第三者wiki | - | MPRとメモリ配置の一般論のみ。本文は今回の監査でも未取得(CD_READ固有の記述の有無は未確認。根拠には使わない) |

公式System Cardマニュアル(1.0)は公開URLを特定できなかった。stum.de は同マニュアルを所持すると述べるが本文にレジスタ規約は無い(取得結果で確認)。

## 2. 呼出側使用台帳 (caller usage ledger)

本節のみが #148 のcaller側成果物。各行は独立した**呼出側出典を `revision` + `line` でピン留め**し、呼出側が
CD_READ 呼出の前後に何をするかだけを記録する。callee の実装・副作用は混ぜない (§3)。機械可読な正本は
`tools/cd_read_contract.py` の `CALLER_LEDGER` で、本表と一致することを `tests/host/test_cd_read_contract.py`
が検査する。`direction` の意味:

* `CALLER_IN` = 呼出前に呼出側が書く入力 (callee がそれを読むかは HYPOTHESIS)。
* `CALLER_OUT` = 呼出後に呼出側が読む戻り値 (callee がそう返すかは HYPOTHESIS)。
* `CALLER_SAVE` = 呼出側が自前で退避/復元する (呼出側防御。callee の保存の証明ではない)。
* `CALLER_DECLARATION` = 宣言/プロトタイプ (呼出前に実際に書かれた命令ではない)。
* `UNOBSERVED` = 引用した呼出側が観測していない → `UNKNOWN`。

### 2.1 ピン留めした行

| id | 出典 @54f5c73 : 行 | slot | direction | 状態 |
| --- | --- | --- | --- | --- |
| C01 | pcengine.inc : 471 | `$E009` 入口 | CALLER_IN | HYPOTHESIS |
| C02 | core.inc : 42 | Y | CALLER_IN | HYPOTHESIS |
| C03 | core-kernel.asm : 509 | Y | CALLER_IN | HYPOTHESIS |
| C04 | pcengine.inc : 329 | `$20F8-$20FF` | CALLER_DECLARATION | HYPOTHESIS |
| C05 | hucc-systemcard.h : 95 | `$20F8-$20FF` | CALLER_DECLARATION | HYPOTHESIS |
| C06 | hucc-systemcard.asm : 384 | `_dh` | CALLER_IN | HYPOTHESIS |
| C07 | hucc-systemcard.asm : 441 | `_dh` | CALLER_IN | HYPOTHESIS |
| C08 | hucc-systemcard.asm : 322 | `_dh` | CALLER_IN | HYPOTHESIS |
| C09 | hucc-systemcard.asm : 294 | `_dh` | CALLER_IN | HYPOTHESIS |
| C10 | core-kernel.asm : 593 | `_dh` | CALLER_IN | HYPOTHESIS |
| C11 | hucc-systemcard.asm : 381 | `_ax` | CALLER_IN | HYPOTHESIS |
| C12 | hucc-systemcard.asm : 436 | `_al` | CALLER_IN | HYPOTHESIS |
| C13 | hucc-systemcard.asm : 361 | `_bx` | CALLER_IN | HYPOTHESIS |
| C14 | core-kernel.asm : 589 | `_bl/_bh` | CALLER_IN | HYPOTHESIS |
| C15 | core-kernel.asm : 539 | `_cl/_ch/_dl` | CALLER_IN | HYPOTHESIS |
| C16 | hucc-systemcard.asm : 594 | `_dl/_ch/_cl` | CALLER_IN | HYPOTHESIS |
| C17 | core-kernel.asm : 585 | P.I | CALLER_IN | HYPOTHESIS |
| C18 | core-startup.asm : 125 | MPR0/MPR1 | CALLER_IN | HYPOTHESIS |
| C19 | core-kernel.asm : 503 | MPR7 | CALLER_SAVE | HYPOTHESIS |
| C20 | hucc-systemcard.asm : 346 | MPR3/MPR4 | CALLER_SAVE | HYPOTHESIS |
| C21 | hucc-systemcard.asm : 443 | MPR3 | CALLER_SAVE | HYPOTHESIS |
| C22 | hucc-systemcard.asm : 386 | `$20FA-$20FE` | CALLER_SAVE | HYPOTHESIS |
| C23 | hucc-systemcard.asm : 389 | A | CALLER_OUT | HYPOTHESIS |
| C24 | hucc-systemcard.asm : 390 | A/NZ | CALLER_OUT | HYPOTHESIS |
| C25 | core-kernel.asm : 600 | A | CALLER_OUT | HYPOTHESIS |
| C26 | hucc-systemcard.asm : 300 | Y:A | CALLER_OUT | HYPOTHESIS |
| C27 | hucc-systemcard.asm : 448 | MPR3 | CALLER_SAVE | HYPOTHESIS |
| C28 | hucc-systemcard.asm : 388 | X | UNOBSERVED | UNKNOWN |
| C29 | hucc-systemcard.asm : 388 | Y | UNOBSERVED | UNKNOWN |
| C30 | hucc-systemcard.asm : 388 | P (N/V/Z/C/D/B/T) | UNOBSERVED | UNKNOWN |
| C31 | hucc-systemcard.asm : 388 | S (stack) | UNOBSERVED | UNKNOWN |
| C32 | hucc-systemcard.asm : 388 | MPR0-2,5,6 | UNOBSERVED | UNKNOWN |
| C33 | hucc-systemcard.asm : 388 | IRQ/completion | UNOBSERVED | UNKNOWN |
| C34 | hucc-systemcard.asm : 388 | `$20F8-$20F9,$20FF` | UNOBSERVED | UNKNOWN |

各行の短い根拠 (`note`) は `CALLER_LEDGER` を参照。要点:

* **入口と選択子**: 呼出側は `cd_read` = `$00:E009` を equ で持ち (C01)、`system` マクロが Y にベクタ下位
  バイトを積んで `call_bios`→`jsr $E000` する (C02/C03)。Y は `$E000` ジャンプ表の選択子であり、CD_READ の
  引数ではない。
* **転送モード `_dh`**: 呼出側が使う値は 0 (C06)、3 (C07)、`$FE` (C08)、`$FF` (C09)、6 (C10) のみ。HuCC に
  他のモードの使用箇所はない。モード値の意味は呼出側の用法からの推定で、callee の値域は未測定。
* **カウント/行き先**: `_ax` (C11)、`_al` (C12)、`_bx` (C13)、`_bl/_bh` (C14)。
* **ブロック番号 (LBA三つ組)**: `get_file_lba` が `_cl/_ch/_dl` を作り (C15)、`_add_sectors` が加算する
  (C16)。`_cl` は先にオーバーレイ番号として渡り、後で LBA 上位に上書きされる (C15 の note)。
* **MPR/IRQ**: 呼出側は MPR7 を退避し bank 0 を張る (C19)、MPR3/4 を自前で退避する (C20/C21)、CLI する (C17)。
  起動時の MPR0=`$FF`・MPR1=`$F8` は一般前提 (C18)。

### 2.2 呼出側が設定する値 と callee が要求する値 の区別

§2.1 の `CALLER_IN` は「呼出側がそのバイトを書いた」という**呼出側の観測**であって、「callee がそのバイトを
入力として読む」ことの証明ではない。両者を混同しないための分離:

* 呼出側が書く (CALLER_IN): `_dh`, `_ax`/`_al`, `_bx`/`_bl`/`_bh`, `_cl`/`_ch`/`_dl`, Y 選択子, CLI, MPR0/1 前提。
* callee が要求する (CALLEE_REQUIRED): **UNKNOWN**。実コアで System Card の callee を実行した観測はゼロで、
  どの入力が必須かを示す証拠はない。HuCC の `note`/コメントも呼出側の防御にすぎない。
* **基点の解釈**: `_cl/_ch/_dl` を絶対LBA とみなすか `recbase0` 相対とみなすかは callee 側の解釈で、stum.de (#146 の W1) が
  「相対 (`recbase0` 加算)」を示すのみ (実コアの callee 実行ではない)。Hu-Go! HLE は参照のみで根拠に数えない。
  HYPOTHESIS のままとし、#146 台帳との不一致は §6 に記録する。
* 呼出側が読む戻り値 (CALLER_OUT): A (C23-C25) のみ。X/Y は呼出側が上書きする (C26)。P フラグは読まれない
  (C30)。C26 は callee の Y を読むのではなく、CLY で Y をゼロにして LEAVE (X→A) で返値を整形する
  **caller 側の返値整形**であり、callee の返値使用とは分ける。

### 2.3 観測していない項目 = UNKNOWN

C28-C34 は「呼出側が観測していない」ことの記録であり、`UNKNOWN` のまま固定する。callee の戻り値 X/Y、
P フラグ (N/V/Z/C/D/B/T)、スタック S、MPR0-2/5/6、IRQ/完了、`$20F8-$20F9`/`$20FF` の呼出後状態は、
実コアで callee を実行して前後を比較するまで UNKNOWN である。§3 の副作用表もこれに対応する。

## 3. callee副作用・戻り値

| 項目 | 内容 | 状態 | 確定に必要な観測 |
| --- | --- | --- | --- |
| 成功時A | 0。根拠は呼出側 C23/C24 の `TAX`/`BNE` 分岐（呼出側の慣習）のみで、callee の成功値の証明ではない。Hu-Go! HLE は参照のみ | HYPOTHESIS（根拠が弱い。出典は確認済み） | 実コアで呼出直後のAを記録 |
| 失敗時A、エラーコード体系 | 非0。個別値は不明 | UNKNOWN | 負例(範囲外LBA、媒体なし、読取エラー)ごとにAを記録 |
| P(N/Z/C/T/I/D) | 出典は Hu-Go! HLE のみ（参照のみ、根拠に使わない）。HuCC の TAX/BNE は BIOS の Z/C を示さない | UNKNOWN | RTS直後のPをトレース。呼出側台帳 C30 は P を UNOBSERVED とする |
| X/Y保存 | 不明 | UNKNOWN | 呼出前後のX/Yを比較。呼出側台帳 C28(X) / C29(Y) |
| `$20F8-$20FF` | HuCCは`_cd_loaddata.4`で`$20FA-$20FE`の5バイト(`_bl,_bh,_cl,_ch,_dl`)のみ退避=呼出側防御。`_ax`/`_dh`は退避せず各ループで再設定する。BIOSが書き換える/保存するかは不明 | UNKNOWN | 呼出前後の`$20F8-$20FF`ダンプ差分、およびBIOS内のこの領域への書込トレース。呼出側台帳 C22 / C34 |
| 成功時MPR | 不明 | UNKNOWN | 呼出前後の MPR0-7 を全て記録。呼出側台帳 C32 (MPR0-2/5/6 は UNOBSERVED) |
| 失敗時MPR | HuCCコメント「エラー時にMPR3を壊す」 | HYPOTHESIS | 失敗を起こす負例の前後でMPR3(とMPR2-6)を記録。呼出側台帳 C21/C27 |
| スタック | JSR/RTSの外側のみ。内部深さ・バランス | HYPOTHESIS / UNKNOWN | 呼出前後のSと最小Sを記録 |
| 完了方式/タイミング | 出典は Hu-Go! HLE のみ（参照のみ、ライセンス未確定、根拠に使わない） | UNKNOWN | 呼出から戻りまでのサイクル数と、その間のIRQ発生有無 |
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
* §2 の呼出側使用台帳は `revision` + `line` でピン留めし、`tools/cd_read_contract.py` の `CALLER_LEDGER` と
  `validate_caller_ledger()` で機械検査する。これは呼出側の観測であって callee の実装許可ではない。

## 6. 台帳間の不一致 (記録。解消済みではない)

* **CD_READ の基点 (本書 §2.2 の基点メモ) と #146 台帳 (146 項目2/3/4)**: 本書は当初「基点: UNKNOWN」としたが、#146 は stum.de を根拠に「相対(`recbase0` 加算)」を HYPOTHESIS とした。本書も HYPOTHESIS に揃えた。ただしその根拠は stum.de (W1) のみであり、Hu-Go! HLE の同じ加算は参照のみで数えない。どちらも実コアのcallee実行なし。
* **2ISO 経路の矛盾 (`docs/cd-base-abi-ledger.md` §3 補足)**: HuCC は 2ISO 時に `recbase1` のみを設定する (`_cl=2`)。#146 は「通常読込みは `recbase0`、`recbase1` は失敗後のみ」の読み方で矛盾が成立しない**可能性**を挙げるが、これは推論で未測定。**矛盾は OPEN のまま**。`_cl=1/2` 後の CD_READ の LBA 比較と失敗注入の実測が必要。
* #146 と本書は、CD_BASE の `_cl` の意味 (`$01`=recbase0, `$02`=recbase1) を stum.de のみから得ており、独立した二つの証拠ではない。

## 7. 検証結果

* 追加の実コア実行: なし。新規VERIFIED: なし。
* 追加した判定テスト (`tests/host/test_cd_read_contract.py`): 呼出側台帳の全行が `spec/api_contracts.json` の
  CALLER_SOURCE を `revision` + `line` でピン留めすること、出典欠落/未知ソース/HLE ソースの行を拒否すること、
  呼出側の主張を VERIFIED へ昇格できないこと、UNOBSERVED 行が UNKNOWN のままであること、本表と
  `CALLER_LEDGER` の id が一致することを検査する。
* `make check` / host テストの結果はPR本文に記載。

## 変更履歴

- 2026-10-29（PR #571 の修正）: Hu-Go! HLE を補強とした記述を「参照のみ」へ変更した。§3 の P フラグと完了方式/タイミングは、出典が Hu-Go! HLE のみのため UNKNOWN へ下げた。成功時A は出典（呼出側 C23/C24）を明記し、根拠が弱いことを注記した。HYPOTHESIS の判定は変えていない。旧版の Hu-Go! HLE を根拠とした記述は撤回する。
