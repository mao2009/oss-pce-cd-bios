# AD_TRANS（スロット `$11`、候補 `$E033`）公開 caller 由来の ABI・副作用 根拠台帳（Issue #297）

**状態: callee の ABI は未確定。ROM 実装なし。** spec=UNKNOWN（callee）、caller 側は一部 SOURCE_FACT、
implementation=NOT_STARTED、verification=NOT_RUN、release_eligible=false。親 Issue #26、関連 #11（CD_BASE）/ #12（CD_READ）/ #13（CD_SEEK）/ #25（AD_RESET）/ #33（AD_STAT）。

本台帳は、公開 caller（HuCC 固定版のマクロ）から見える事実と、BIOS（System Card の `$E033`）の仮説・未確定を分けて記録する。
callee を実行していないため、callee の行に SOURCE_FACT や VERIFIED / OBSERVED / PASS は付けない。未確定の欄には値を補わない。

## 1. 規則

状態の意味:

- `SOURCE_FACT`: 固定版ソースの本文に書かれた**呼出側**の動作（何を書き、どの命令で呼ぶか）。呼出先の振る舞いではない。
- `HYPOTHESIS`: 検証すべき仮説。根拠は第三者文献の記載、または呼出側の期待だけ。
- `UNKNOWN`: 出典が不足している、または出典同士が一致しない。値の欄は `UNKNOWN` と書く。
- `BLOCKED`: 測定が必要だが、実行可能な呼出先がないため測定できない。値の欄は `UNKNOWN` と書く。

§3 の側: `K` = 呼出側（固定版ソースの本文）、`C` = 呼出先（BIOS）、`R` = 関係。

禁止事項:

- `VERIFIED`、`OBSERVED`、`PASS`、`DEV_CALLABLE` は使わない。`release_eligible` は false のまま。
- 側 `C`（callee）と `R`（関係）の行は `HYPOTHESIS` / `UNKNOWN` / `BLOCKED` のみ。呼出側のソースから callee の事実を導かない。
- ホスト試験は台帳の規則だけを検査する。BIOS の証拠ではない。

## 2. 出典

### 2.1 HuCC（`pce-devel/huc`）固定版

- 固定版: commit `54f5c73606c1e7142e8095c727cab2767e2b3cb3`（取得して SHA-256 `9fe91254051381077bd4ea217b718ec033a52da185283ae9479c1abac365311b` の一致を確認。展開物は読むだけで実行していない）
- 取得元: `https://codeload.github.com/pce-devel/huc/tar.gz/54f5c73606c1e7142e8095c727cab2767e2b3cb3`

| ID | 出典（固定版内のパス） | 行 | ライセンス表示 | 用途 |
| --- | --- | --- | --- | --- |
| S1 | `include/hucc/hucc-systemcard.asm` | 488–503（`_ad_trans.4` マクロ）、476–480（`ad_reset`） | BSL-1.0（John Brandwood 2024） | 呼出側マクロ（主要な根拠） |
| S2 | `include/hucc/hucc-systemcard.h` | 101–102（`ad_reset`、`ad_trans` の宣言） | BSL-1.0（John Brandwood 2024） | 宣言の引数レジスタ割付け |
| S3 | `include/hucc/core-kernel.asm` | 500–516（`call_bios`）、531–552（`get_file_info` / `get_file_lba`） | BSL-1.0（John Brandwood 2021–2024） | 呼出し経路と入力の組立て |
| S4 | `include/hucc/hucc-systemcard.asm` | 594–600（`_add_sectors`） | BSL-1.0（S1 と同じファイル） | sect_offset の 24 bit 加算 |
| S5 | `include/hucc/core.inc` | 41–45（`system` マクロ: `ldy #<\1`、`jsr call_bios`） | BSL-1.0（John Brandwood 2021–2024） | マクロの展開 |
| S6 | `include/hucc/pcengine.inc` | 330–343（`_al`〜`_dh` の番地 `$F8:20F8`〜`$F8:20FF`）、486（`ad_trans = $00:E033, FUNC`） | BSL-1.0（著作権者の表示なし） | 番地と入口の宣言（呼出側） |
| S8 | `include/hucc/core-startup.asm` | 300（`jsr cd_base`） | BSL-1.0（S3 と同じ系統の表示） | CD_BASE 呼出しの有無（R1） |

固定版の `include/hucc` で `cd_base` の呼出しは S8 の 1 箇所だけ。`ad_trans` の呼出しは S1 のマクロ（501 行）だけで、
`include/hucc` 内に AD_TRANS の別の呼出しは見つからなかった（`grep -rIn 'ad_trans' include/hucc`）。

### 2.2 第三者文献（呼出側ソースではない。HYPOTHESIS の根拠に限る）

| ID | 出典 | 取得 | 内容 | ライセンス・来歴 |
| --- | --- | --- | --- | --- |
| S7 | Zeograd, 「PC-Engine Documenation: The System card」 `https://www.zeograd.com/download/pce_bios.html`（AD_TRANS 節は本文 357 行付近） | 2026-10-19 に取得、SHA-256 `6966984ba89a825f63bafda6b65d79052d424137aedb3835c53e5e3e3cab6fd2` | 「system-card 3.0 を対象」。AD_TRANS の IN（bx 宛先、dh 真偽、cl/ch/dl 24 bit ブロック、al ブロック数）と OUT（A = エラー状態、`$04` = エラー・ADPCM busy）。「レジスタを破壊しうる」の一般記述を含む。筆者は一部を RE で推定と明記 | 本文に著作権・ライセンス表示を確認できず。RE 由来の記述は `docs/api-inventory.md` の扱いに従い provenance PENDING |
| S9 | Stum, 「PC Engine CD-ROM² BIOS Method offsets」 `https://www.stum.de/2025/pcenginebiosoffsets/`（2025-07-28） | 2026-10-19 に取得、SHA-256 `81a4c1c1b2c38d4a20628b639eec5a706bbd76cdc7bcc5539a3da38e196121c0` | 関数表に `ad_trans` → `$E033` の対応。引数・戻り値の記述はない | 著作: Michael Stum。サイトは CC BY-NC-SA 4.0 と表示。事実の引用のみで本文は転載しない |

`docs/api-inventory.md` の AD_TRANS 行（`$11` / `$E033`）は本台帳の対象を示す内部の参照であり、出典ではない。

### 2.3 除外した出典（根拠に使わない）

- `include/huc/cdrom.asm` 573–586（ad_trans の wrapper）: ライセンスが不確実（ルート `LICENSE` は全体を「正確に述べることはほぼ不可能」とする）。内容を根拠にしない。
- `src/huc/pragma.c` 127 行（レジスタ割付けの pragma）: ファイル冒頭にライセンス表示がない。根拠にしない。
- `doc/hucc/hucc-function-reference.md`、`doc/huc/clibref.txt`（AD_TRANS の説明文）: ライセンス表示を確認できず、根拠にしない。
- `examples/asm/elmer/include/cdrom.asm` 1255–1380（`cdr_ad_trans`）: 冒頭に Hudson の Legend of Xanadu 2 由来のコードという表示があり、来歴が未監査。また本体は SCSI を直接発行する再実装で、`$E033` を呼ばない。根拠にしない。
- `examples/asm/elmer/doc/syscard-cd-ipl.txt`: System Card の逆アセンブル表記。公開禁止の対象であり、根拠にしない（検索出力に 1 行だけ現れたが、使っていない）。
- `examples/asm/elmer/kickc/bios.h`: 名前の宣言のみ。根拠にしない（CD_SEEK 台帳と同じ扱い）。
- Hu-Go `bios.c`（mckayemu/hugo @64ed226d、GPL-2）: HLE であり BIOS ABI の証拠にならない。本件では取得・読解していない。

注（ライセンス）: BSL-1.0 は Boost Software License 1.0 の SPDX 識別子。著作権表示（氏名と年）はファイルごとに異なるため、表に個別に記した。

## 3. 項目別台帳

| ID | 側 | 項目 | 状態 | 値 | 出典 | 理由・確度 | 残る識別テスト |
| --- | --- | --- | --- | --- | --- | --- | --- |
| K1 | K | 呼出し経路 | SOURCE_FACT | 呼出側が目標 `$E033` を設定する。マクロは `ldy #<ad_trans`（Y=`$33`）の後に `call_bios` を呼び、`call_bios` が `jsr $E000` の下位を Y で書き換える。MPR7 を `$00` に切替え、戻りで保存値に戻す | S1 501、S3 500–516、S5 41–44、S6 486 | マクロと trampoline の本文。入口の意味は C7 | — |
| K2 | K | `_cl` / `_ch` / `_dl`（入口時） | SOURCE_FACT | `get_file_lba` がファイル先頭 LBA を書き（`_cl` は、ファイル番号が `iso_128mb` 以上なら 1、それ以外は 0）、`_add_sectors` が sect_offset を 24 bit で加算する。入口の `_cl` はファイル番号ではなく LBA の上位バイト | S1、S3 539–552、S4 594–600 | 呼出側の組立て。24 bit の基準（ブロック番号の意味）は C4 | C4 |
| K3 | K | X（入口時） | SOURCE_FACT | `ldx <_cl` でファイル番号を X に入れる。`get_file_info` と以後の命令は X を変えない。入口の X はファイル番号 | S1 489–490、S3 531–537 | 命令列の読取り。BIOS が X を読むかは C7 | C7 |
| K4 | K | Y（入口時） | SOURCE_FACT | `_add_sectors` の前に Y を使うが、`system` が Y を `$33` に設定する。`call_bios` は Y を変えない。入口の Y は `$33` | S1 499–501、S3 500、S5 42 | 命令列の読取り。BIOS の入力意味は C7 | C7 |
| K5 | K | A（入口時） | SOURCE_FACT | `lda <_dh` の値（元の nb_sectors）が `beq` と `sta` の後も残り、`call_bios` の `pha`/`pla` で保存されて入口の A になる | S1 493–501、S3 501–512 | 命令列の読取り。BIOS の入力意味は C7 | C7 |
| K6 | K | P（入口時） | SOURCE_FACT | マクロは P を明示的に設定しない。入口の P は直前の演算（`beq`、`pla` など）の結果で、呼出側は意味のある値を渡さない | S1、S3 | 命令列の読取り | C7 |
| K7 | K | `_al`（ブロック数、8 bit） | SOURCE_FACT | `_dh` が 0 のとき `get_file_info` が書いたファイル長の下位 8 bit を使う。`_dh` が非 0 のときは `_dh` の値を `_al` に書く。`_ah`（上位）も呼出側が設定する。呼出側が書くのは `_al` と `_ah` で、BIOS がどちらを読むかは本行の範囲外 | S1 491–499、S3 531–537 | 255 セクタを超える全体読込みで `_ah` が BIOS の入力になるかは未確認（C3） | C3 |
| K8 | K | `_dh`（呼出し前） | SOURCE_FACT | マクロは呼出し前に `_dh` を 0 にする（`stz`）。HuCC の経路から BIOS に渡る `_dh` は常に 0 | S1 493–498 | `_dh` の意味は C6（第三者文献と不一致） | C6 |
| K9 | K | `_bx`（宛先） | SOURCE_FACT | マクロは `_bx` を設定も変更もしない。呼出側が事前に設定する前提（宣言 `ad_addr<_bx>`）。`get_file_info` と `_add_sectors` は `_bx` を使わない | S1、S2 102、S3 531–552 | 宛先の意味は C5 | C5 |
| K10 | K | 作業 RAM（呼出し前の書込み） | SOURCE_FACT | マクロが書く番地: `$20F8`（`_al`）、`$20F9`（`_ah`）、`$20FC`〜`$20FE`（`_cl`、`_ch`、`_dl`）、`$20FF`（`_dh` を 0 に）。書かない番地: `$20FA`〜`$20FB`（`_bx`） | S1、S3 531–552、S6 330–343 | 呼出側の書込み。BIOS の書込みは C11 | C11 |
| K11 | K | MPR とスタック（呼出側） | SOURCE_FACT | trampoline は MPR7 を `$00` にして呼び、戻りで保存値を復元する。`call_bios` は A を 1 バイト push し、`jsr` が戻り先を積む | S3 500–516 | BIOS 内部の MPR と SP は C11、C12 | C11、C12 |
| K12 | K | IRQ・待ち・再試行（呼出側） | SOURCE_FACT | マクロは SEI/CLI を使わず、割込み関連の作業 RAM を操作しない。戻りの後に待ちや再試行はなく、すぐ `cly` を実行する | S1 488–503 | BIOS 側の割込み方針は C13、完了は C14 | C13、C14 |
| K13 | K | 戻り値 A（呼出側） | SOURCE_FACT | マクロは `cly`（Y のみ）以外で A を変えず、戻りの A をそのまま残す。宣言は `unsigned char`。A の判定は本マクロにない | S1 502、S2 102 | 成功・失敗の判定は C8、C9 | C8、C9 |
| K14 | K | `ovl_index` の範囲（呼出側の前提） | SOURCE_FACT | `get_file_info` は `iso_dirlo` / `iso_dirhi` の表を X で添字参照し、範囲の検査がない | S3 531–552 | 有効なファイル番号は呼出側の前提（検査はない） | — |
| C1 | C | 入口 | HYPOTHESIS | `$00:E033`（呼出し時は `$E000 + 3 × $11`） | S6 486、S7（`multiply` の記述と AD_TRANS 節）、S9 関数表 | 呼出側の等号と第三者文献。BIOS 本体は未実行 | 入口の確認（BLOCKED、§6） |
| C2 | C | 入力 `_cl` / `_ch` / `_dl`（24 bit ブロック番号） | HYPOTHESIS | S7 は `cl` = blockadr >> 16、`ch` = blockadr >> 8、`dl` = blockadr と記す | S7（AD_TRANS の IN）、K2 | K2 の形と一致する。BIOS の解釈は未実行 | C4 と同じ |
| C3 | C | 入力 `_al`（ブロック数） | HYPOTHESIS | S7 は「読むブロック数」と記す。0 の意味、8 bit の上限、`_ah` の扱いは記載なし | S7、K7 | 0 の意味は UNKNOWN（C15 と合わせて確認） | §6 のブロック数試験 |
| C4 | C | ブロック番号の基準（絶対 LBA、INDEX 01 相対、MSF 起点、基準値相対） | UNKNOWN | UNKNOWN | S1、S3、S7 | K2 の値は ISO 9660 のファイル位置から作られる。BIOS がどの基準で読むかは未確定。CD_BASE（#11）と同じ問い | INDEX 01 ≠ 0 の合成ディスクで READ LBA を比べる（BLOCKED、§6） |
| C5 | C | 入力 `_bx`（ADPCM 宛先） | HYPOTHESIS | S7 は宛先のバッファアドレスと記す | S7（AD_TRANS の IN）、K9 | 宛先が ADPCM RAM のどの位置かは未確認（C16） | 宛先の観測（BLOCKED、§6） |
| C6 | C | 入力 `_dh`（宛先の設定フラグ） | UNKNOWN | UNKNOWN | S7（dh が偽なら宛先を設定しない、真なら bx に設定する）、S1 493–498（HuCC は dh を 0 にして bx を渡す） | 二つの記述は両立しない（矛盾）。S7 は RE 由来である。dh = 0 の呼出しで宛先が更新されるか、前回の宛先が残るかは未確定。宛先の副作用に直結する | `_dh` を 0 と 1 にし、`_bx` を変えた組で ADPCM 宛先を観測（BLOCKED、§6） |
| C7 | C | 入力 X / Y / A / P（BIOS 入口） | UNKNOWN | UNKNOWN | K3〜K6、S7（入力の記載なし） | 呼出側の X、Y、A は trampoline の結果。BIOS が読むかは文献に記載がない | 入力感度試験（BLOCKED、§6） |
| C8 | C | 出力 A（エラー状態） | HYPOTHESIS | S7 は A をエラー状態とし、`$04` を「エラー、ADPCM busy」と記す | S7（AD_TRANS の OUT） | RE 由来の第三者文献。他の値の意味は未確認 | busy の再現（BLOCKED、§6） |
| C9 | C | 成功値（0 の意味） | UNKNOWN | UNKNOWN | S7、S1 502（呼出側は A を判定しない） | 0 が成功であるという定義は、文献にも呼出側にもない | 無ディスク、範囲外、busy での A の実測（BLOCKED、§6） |
| C10 | C | 出力 X / Y / P | UNKNOWN | UNKNOWN | S7（一般記述「レジスタを破壊しうる」のみ） | AD_TRANS の OUT に X、Y、P の記載はない | 前後差分（BLOCKED、§6） |
| C11 | C | MPR・バンク・作業 RAM の副作用 | UNKNOWN | UNKNOWN | S7 一般記述、K10、K11 | 呼出側が復元するのは MPR7 だけ。BIOS 内部の MPR と作業 RAM の変化は未確認 | MPR0〜7 と作業 RAM の前後差分（BLOCKED、§6） |
| C12 | C | スタック使用量 | UNKNOWN | UNKNOWN | K11 | 未確認 | SP の前後差分（BLOCKED、§6） |
| C13 | C | IRQ・割込み（転送中、割込み禁止時） | UNKNOWN | UNKNOWN | K12、S7（記載なし） | 呼出側は割込みを制御しない。BIOS の割込み方針は未確認 | IRQ の有無での比較（BLOCKED、§6） |
| C14 | C | 完了と timeout（戻りの時点で転送が終わるか） | HYPOTHESIS | 戻りの時点で転送が完了している | K12（戻り後に待たない）、S1 | 呼出側の仮定。非同期か、timeout の有無は未確認 | 完了時刻と timeout の測定（BLOCKED、§6） |
| C15 | C | busy 以外の失敗（無ディスク、範囲外、読込み失敗） | UNKNOWN | UNKNOWN | S7（`$04` の記載のみ） | busy 以外の失敗は文献に記載がない | 失敗ケースの実測（BLOCKED、§6） |
| C16 | C | ADPCM RAM 宛先の範囲と上限（宛先 + ブロック数 × 2048 の溢れ） | UNKNOWN | UNKNOWN | K7、C5 | 未確認。セクタのバイト数も本台帳では確定していない | 境界試験（BLOCKED、§6） |
| R1 | R | CD_BASE（#11）との関係 | UNKNOWN | UNKNOWN | S8、S1 | 固定版 `include/hucc` で `cd_base` の呼出しは S8 の 1 箇所（2ISO 経路）。AD_TRANS の呼出側は CD_BASE を呼ばない（S1 にない）。基準の継承は観測できない | CD_BASE の後の AD_TRANS と READ LBA の比較（BLOCKED、§6） |
| R2 | R | CD_READ（#12）・CD_SEEK（#13）との関係 | HYPOTHESIS | 同じ 24 bit のブロック表現を使う | S1、S3、S7 | K2、C2 の形の一致だけで、観測していない | READ・SEEK と AD_TRANS の READ LBA の比較（BLOCKED、§6） |
| R3 | R | AD_RESET（#25、`$E030`）との順序 | UNKNOWN | UNKNOWN | S1 476–480（`ad_reset` マクロ） | マクロはあるが、AD_TRANS の前に必要な呼出し順序を示すソースはない | 順序を変えた実測（BLOCKED、§6） |
| R4 | R | AD_STAT（#33）と busy 状態の関係 | UNKNOWN | UNKNOWN | C8 | busy の判定を AD_STAT で行えるかは未確認 | AD_STAT と A の `$04` の比較（BLOCKED、§6） |
| R5 | R | 観測可能性（実行可能な呼出先） | BLOCKED | UNKNOWN | `docs/cd-base-abi-ledger.md` §4a | DEV ROM の `$E006` だけを測定済み。`$E033` は未測定。System Card の実 BIOS は本件で使わない | NOT_RUN（§6） |

## 4. 証拠の区分

| 区分 | 本台帳での状態 | 根拠 |
| --- | --- | --- |
| 独立レビュー | NOT_RUN | 本 PR の作成者による自己確認だけ。独立レビュアーは未割当 |
| 関連 CI | ledger の規則の試験のみ。全体の CI と `make check` は NOT_RUN | `tests/host/test_adtrans_contract.py`（§5）。BIOS の証拠ではない |
| 実測 | BLOCKED | 実行可能な `$E033` がない（R5）。callee を実行していない |

本台帳の SOURCE_FACT は、すべて呼出側ソースの本文（S1〜S6）から読み取った。callee の値は、第三者文献（S7、S9）の HYPOTHESIS と、出典の不一致（C6）だけである。

## 5. ホスト試験

`tests/host/test_adtrans_contract.py` は次を検査する。BIOS の証拠ではない。

- §3 の項目は K1〜K14、C1〜C16、R1〜R5 の計 35 件で、欠落・重複・予期しない ID・側の取り違えがあれば失敗する。
- §3 の状態は許可された語彙（SOURCE_FACT、HYPOTHESIS、UNKNOWN、BLOCKED）に限る。側 `C` と `R` の行は SOURCE_FACT を使わない。
- 状態が UNKNOWN / BLOCKED の行は値を `UNKNOWN` とする。
- §3 に VERIFIED、OBSERVED、PASS、DEV_CALLABLE の語を書いた場合は失敗する。本文の `release_eligible=false` は残す。
- SOURCE_FACT / HYPOTHESIS の行は、§2 で定義した出典 ID（S1〜S9）を一つ以上引用する。

## 6. 残る UNKNOWN と次の識別テスト

| 残る項目 | 識別テスト | 前提 |
| --- | --- | --- |
| C4、R1: ブロック番号の基準 | INDEX 01 ≠ 0 の合成ディスクで、AD_TRANS の READ LBA を `docs/cd-base-abi-ledger.md` §4 の四候補と比べる | 実行可能な `$E033`（BLOCKED、R5） |
| C5、C6: 宛先 `_bx` と `_dh` | `_dh` を 0 と 1 にして `_bx` を変えた呼出しで、ADPCM 宛先を観測する | 同上 |
| C3、C15、C16: ブロック数、失敗、宛先の範囲 | `_al` を 0、1、255、256 以上（`_ah` 付き）で試し、読込み量と戻りを記録する | 同上 |
| C7、C10: 入力と出力の X / Y / P | 呼出し前後の X、Y、P を入力感度試験で比べる | 同上 |
| C8、C9、R4: busy と成功値 | busy 中の再呼出し、無ディスク、範囲外での A の実測 | 同上 |
| C11、C12、C13: 副作用、スタック、IRQ | 呼出し前後の MPR0〜7、作業 RAM、SP の全差分。IRQ の有無での比較 | 同上 |
| C14: 完了と timeout | 戻り時点での転送完了の確認と、待ち時間の測定 | 同上 |
| R2、R3: CD_READ・CD_SEEK・AD_RESET との順序 | 各呼出し順序での READ LBA と戻り値の比較 | 同上 |

いずれも、実行可能な callee を得るまで BLOCKED である。System Card の実 BIOS は本件でリポジトリに含めず、実行もしない。
ROM への実装と純正 BIOS の JSR は本台帳の範囲に含めない。
