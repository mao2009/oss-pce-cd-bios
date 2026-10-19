# CD_EXEC ABI 契約台帳（Issue #239、slot `$05`、候補 `$E00F`）

**状態: 呼出先は未実装・未実行。ROM 配線なし。slot `$05` は呼出不可のまま。**
本台帳は、公開 HuCC 等の呼出側ソースから得られる事実と、CD_EXEC 本体の振る舞いに関する推測・未確定を分けて記録する。
既存の `spec/api_contracts.json`、`docs/abi-contracts.md`、`docs/cd-base-abi-ledger.md` は変更しない。

## 1. 要点

- 固定版（HuC/HuCC、`54f5c736`）の全体で `cd_exec` / `CD_EXEC` / `E00F` を呼ぶ箇所はない（A1、grep 範囲は §4）。SDK のオーバーレイ実行は `cd_execoverlay` から
  `exec_overlay` へ入り、`cd_read` を直接呼んでから `jmp $4000` する（A2）。CD_EXEC の呼出側の期待はソースに存在しない。
- よって CD_EXEC の引数・戻り値・MPR/SP/IRQ/PC 変化は **UNKNOWN**。候補は §6 に列挙するが昇格しない。
- 成功時非復帰は **HYPOTHESIS**（SDK の説明と `jmp` からの推測のみ）。失敗時の戻りは CD_EXEC について出典がなく **UNKNOWN**（B2）。SDK の `cd_execoverlay` の二つの実装（HuC と HuCC）は失敗時の戻りで食い違うが、どちらも別関数であり CD_EXEC の証拠ではない。
- 呼出先の実行による確認は **BLOCKED**（E1）。危険な純正 JSR は 0 回のまま。

## 2. 規則

確度の意味:

- `SOURCE_FACT`: 固定版ソースの本文に書かれた事実。**呼出側の記述に限る**。呼出先（CD_EXEC 本体）の振る舞いには付けない。
- `HYPOTHESIS`: 出典からの推測。呼出先は未実行。
- `UNKNOWN`: 出典が不足、または出典同士が矛盾する。候補は書いても確定扱いしない。
- `BLOCKED`: 実行条件（呼出先の実装、ROM、実行許可）が満たされず、測定できない。

`OBSERVED` / `VERIFIED` / `PASS` は本台帳では使わない。実行証拠がないためである。
区分は `caller`（呼出側）、`callee`（CD_EXEC 本体）、`repo`（本リポジトリの状態）の三つ。
`callee` の行は `SOURCE_FACT` にしない（判定は `tests/host/test_cdexec_contract_ledger.py` が行う）。

## 3. 出典

固定版を使う。アーカイブは新しい空ディレクトリに展開し、展開物は実行しない。

| ID | 出典（固定版・パス・行） | ライセンス表示 | 状態 |
| --- | --- | --- | --- |
| H1 | `pce-devel/huc` commit `54f5c73606c1e7142e8095c727cab2767e2b3cb3`、取得 `https://codeload.github.com/pce-devel/huc/tar.gz/54f5c73606c1e7142e8095c727cab2767e2b3cb3`、SHA-256 `9fe91254051381077bd4ea217b718ec033a52da185283ae9479c1abac365311b` | 下記 H1a–H1d を参照 | 出自は PENDING（`docs/oss-audit-systemcard.md`） |
| H1a | `include/hucc/pcengine.inc` 473 行（`cd_exec = $00:E00F, FUNC`）、330–343 行（`_al`〜`_dh`） | BSL-1.0（8–10 行） | 同上 |
| H1b | `include/hucc/core-kernel.asm` 531–549（`get_file_info` / `get_file_lba`）、578–614（`exec_overlay`） | BSL-1.0（John Brandwood 2021–2024） | 同上 |
| H1c | `include/hucc/hucc-baselib.asm` 75–83（`cd_execoverlay` マクロ。79 行に「does not return」） | BSL-1.0（John Brandwood 2024–2025） | 同上 |
| H1d | `include/hucc/core-stage1.asm` 117、171 行（`jmp exec_overlay`）。`include/huc/cdrom.asm` 312–329 行（`_cd_execoverlay`、失敗時の戻り）。`include/huc/system.inc` 70 行（`cd_exec = $E00F`） | `core-stage1.asm` は BSL-1.0 表示を確認していない。`cdrom.asm` と `system.inc` は表示未確認。両者は参照のみで、ライセンス未確認 | 同上 |
| H1e | `doc/huc/overlays.txt` 35–40 行（オーバーレイは「subroutine でも自動復帰機構もない」） | 文書。ライセンス未確認 | 同上 |
| G1 | `drhelius/Geargrafx` commit `b49ae82a012eade566d72e9d61c9d95a5caa4da3`、SHA-256 `a80a3eae26a8efcd994350e84bbe73c975e406ced41f8f2404c88b00d4217f68`、`LICENSE` は GPL-3.0 | GPL-3.0 | 参照のみ（否定検索。内容は採用しない） |
| X1 | Hu-Go `bios.c`、`examples/asm/elmer/kickc/bios.h`（該当定義を含むが、値・行の内容は引用しない） | 不明 | **除外**。`docs/cd-base-abi-ledger.md` §2 と同じ方針で、内容を根拠にしない |

## 4. 呼出側の調査（固定版 H1 のアーカイブ全体、`grep -rIn -E '\bcd_exec\b|\bCD_EXEC\b|E00F'`）

- ヒットは定義のみ: H1a（`pcengine.inc` 473 行）、H1d（`system.inc` 70 行）、X1（除外、内容は引用しない）。`cd_exec` / `CD_EXEC` / `E00F` を使う呼出し（`system` マクロ経由の引数を含む）は **0 箇所**。`57359` を含む行も 0 件（大文字小文字を区別しない検索）。
- `call_bios`（`core-kernel.asm` 500 行）は汎用の System Card 呼出し入口で、CD_EXEC 固有ではないため本 grep の対象外。
- `cd_execoverlay`（`hucc-baselib.asm` 81–83 行: `tax` の後 `jmp exec_overlay`）は、`$E00F` を呼ばずに `exec_overlay` へ入る。
- HuC 側の `_cd_execoverlay`（`cdrom.asm` 312–329 行）も `cd_overlay` という独自の読込み経路を使い、`$E00F` を呼ばない。
- 固定版の `examples/` と `test/manual/` 内の `cd_execoverlay` 呼出しは、いずれも SDK のオーバーレイ関数である（`examples/hucc/overlay/*.c` ほか）。

注（grep 範囲）: 上記の grep は H1 アーカイブ（本リポジトリ外、新しい空ディレクトリへ展開して検索）に対して前回行った検索の結果である。アーカイブは本リポジトリに含まれず、本 PR の範囲では再実行できない。A1 の結論は前回の検索結果に基づく。

以上から、固定版 HuCC の CD_EXEC に対する呼出側の期待（引数、戻り、保存レジスタ）は **存在しない**。
`exec_overlay` の記述は SDK 自身のローダの記述であり、CD_EXEC の契約ではない。

## 5. 契約台帳

| ID | 区分 | 項目 | 確度 | 出典 | 決着条件 |
| --- | --- | --- | --- | --- | --- |
| A1 | caller | 固定版（`54f5c736`）の全体に、`cd_exec` / `CD_EXEC` / `E00F` を呼ぶ箇所はない | SOURCE_FACT | §4 の grep 結果の不在（範囲: `grep -rIn -E` で `cd_exec`、`CD_EXEC`、`E00F` の語を検索。ヒットは定義と X1 のみ）。網羅の証明は grep の文字列に一致する記述に限る。再現には H1 アーカイブが必要で、本 PR では再実行していない（§4 注） | 呼出側が現れる別リビジョンの確認（本台帳の範囲外） |
| A2 | caller | SDK のオーバーレイ実行は `$E00F` を経由せず、`exec_overlay` が `cd_read` を直接呼ぶ | SOURCE_FACT | H1c 81–83、H1b 578–614、H1d 117、171 | なし（呼出側の事実） |
| A3 | caller | `exec_overlay` は `cd_read` に、ISO ディレクトリ由来の長さ（`_al`/`_ah`）、位置（`_cl`/`_ch`/`_dl`）、先頭バンク（`_bl` = `core_1stbank`、`_bh` = 0）、モード `_dh` = 6 を渡す | SOURCE_FACT | H1b 531–549、589–594 | なし（`cd_read` 側の確認は `docs/cd-read-contract.md` の所管） |
| B1 | callee | 成功時、CD_EXEC は呼出側へ戻らない | HYPOTHESIS | SDK 側の推測のみ: H1b 614（SDK ローダの `jmp $4000`）、H1e 39 行（オーバーレイに自動復帰機構がない、文書）、H1d `cdrom.asm` 323 行（別関数 `cd_execoverlay` の `jmp ovlentry`。参照のみ・ライセンス未確認） | 成功した読込みの後、CD_EXEC の戻りアドレスに戻らないことを実行で測る |
| B2 | callee | 失敗時、CD_EXEC は呼出側へ戻る（戻り値は A） | UNKNOWN | CD_EXEC の出典なし（§4）。別関数の参考: HuC `cdrom.asm` 320–329 行（`cd_execoverlay` の失敗時 `rts`、参照のみ・ライセンス未確認）と HuCC `hucc-baselib.asm` 79 行（`cd_execoverlay` は「does not return, even if there's an error」）。両者は食い違うが、どちらも CD_EXEC ではない | 失敗入力（無ディスク、読込み失敗）で CD_EXEC の戻りの有無を実行で測る |
| C1 | callee | 入口時の引数、MPR、SP、IRQ 状態 | UNKNOWN | H1b 583（`tam7`）、585（`cli`）、H1a 330–343（`$20F8`〜のラベル） | CD_EXEC を実行して入口の全レジスタ、MPR、SP を記録する（呼出側のラベルは callee の入力を意味しない） |
| C2 | callee | 戻り時の保存・破壊レジスタ | UNKNOWN | 呼出側の記述なし（B2 が前提） | B2 が「戻る」と決まった後、入出力の差分を実行で測る |
| C3 | caller | 呼出側は、読込み後に MPR2 を `core_1stbank`（ソース注記では「CORE(not TM)」の既定）へ、MPR3〜MPR6 を `core_1stbank`+1〜+4 へ設定してから `$4000` へ入る。元の値へ戻すとは書かれていない | SOURCE_FACT | H1b 603–614 | なし（呼出側の事実） |
| C4 | callee | 読込みエラー時に MPRn が壊れる | UNKNOWN | CD_EXEC の出典なし。別経路の参考: HuCC `core-kernel.asm` 605–609 行のコメント（System Card のエラー処理の不具合で `cd_read` 先の MPRn が壊れうる、という SDK 側の記述。CD_EXEC のものではない。測定なし） | エラー経路で MPR0–7 の全差分を実行で測る（`docs/cd-read-contract.md` の CD_READ 側と同じ方法） |
| D1 | callee | 成功時の PC は読込み先（`$4000` 相当）へ移る | UNKNOWN | H1b 614 は SDK のローダの PC であり、CD_EXEC の PC ではない | 読込み後の PC と、読込み先アドレスの関係を実行で測る |
| D2 | callee | 引数の意味（トラック/セクタ、転送先、個数、`$20F8`–`$20FF` の入力） | UNKNOWN | 呼出側なし（A1）。§6 の候補のみ | 入力感度試験（入力バイトを一つずつ変える）を実行可能な callee で行う |
| E1 | callee | CD_EXEC 本体を実行して上記を確認する | BLOCKED | G1 の否定検索（`src/` と `platforms/libretro/*.cpp` に `E00F`、`cd_exec` なし。網羅の証明ではない）。実 BIOS は本リポジトリに無く、利用者所有の BIOS を使う経路は本 Issue の範囲外 | 実行可能な callee と、#173 の実行時遮断の証明を待つ |
| F1 | repo | slot `$05` は本リポジトリの ROM・API テーブルで呼出し可能になっていない | SOURCE_FACT | `grep -rIn -i 'E00F' src Makefile` は 0 件。`spec/api_contracts.json` slot 5 は `verification.status = NOT_RUN`、`implementation_status = NOT_STARTED` | 本台帳の範囲外（配線は別 Issue） |

注:

- A1〜A3、C3、F1 は呼出側・リポジトリの事実である。CD_EXEC 本体の振る舞いを示さない。
- B1 は「成功時に戻らない」を仮説として記録するのみである。`exec_overlay` の `jmp` は SDK 自身のローダの振る舞いで、CD_EXEC のものではない。
- HuC の `cd_execoverlay`（`cdrom.asm`）と HuCC の `cd_execoverlay`（`hucc-baselib.asm`）は失敗時の戻りについて食い違う。どちらも SDK 関数であり、CD_EXEC の挙動の出典にはならない。どちらが正しいかは判断しない。

## 6. 候補（未確定。昇格しない）

CD_EXEC が受け取り得る入力の候補は、固定版の SDK が `cd_read` へ渡す値に限る（A3）。次の候補は **UNKNOWN**（D2）:

- 位置（`_cl`/`_ch`/`_dl`）と長さ（`_al`/`_ah`）、または CD_EXEC 固有の別表現。
- 転送先バンク（`_bl`）と、MPR6 を使う `_dh` = 6 というモード。
- 入口の PC（`$4000` 相当）と、`$20F8`–`$20FF` の作業 RAM 入力。

これらは SDK のローダの入力であり、CD_EXEC の入力とは限らない。実行による確認まで、どれも確定扱いしない。

## 7. 検証

Host テストは台帳の形式と昇格禁止、および slot `$05` の未配線だけを確認する。BIOS の振る舞いは検証しない。

```sh
python3 -m unittest discover -s tests/host -p 'test_cdexec_contract_ledger.py' -v
make check
```

| コマンド | 結果 |
| --- | --- |
| `python3 -m unittest discover -s tests/host -p 'test_cdexec_contract_ledger.py' -v` | 結果は PR 本文の検証節に記録 |
| `make check` | 同上 |

NOT_RUN:

- 実 BIOS、エミュレータ、実機での CD_EXEC 実行（E1）。
- HuC、HuCC の別リビジョンでの呼出側の確認（B2 の前提の確認）。
- Hu-Go と KickC の内容（ライセンス未確定のため除外、X1）。

## 8. 残件

1. 実行可能な CD_EXEC の呼出先を得て、D2、C1、C2、D1 を入力感度試験で測る（#173 の実行時遮断が前提）。
2. B2 は CD_EXEC について実行でのみ決まる。SDK の `cd_execoverlay` 二実装の食い違いは CD_EXEC の証拠にならない。
3. C4 のエラー経路の MPR 破壊を、CD_READ 側の台帳（`docs/cd-read-contract.md`）と合わせて測る。
4. ライセンス未確定の出典（X1 と HuC の `cdrom.asm`、`system.inc` の表示）について maintainer の判断を得る。
5. **open item（owner 判断）**: `spec/api_contracts.json` の slot 5 レコード（main 上。本 PR の対象外のため変更しない）は、`name_observations` と `sources` に `hugo:bios.c` を挙げる（locator は「function selector defines, lines 30-133」）。本台帳は Hu-Go を X1 として除外している。両者は矛盾する。owner が、Hu-Go を台帳の出典に含めるか、slot 5 の `hugo:bios.c` 参照を外すかを決める。
