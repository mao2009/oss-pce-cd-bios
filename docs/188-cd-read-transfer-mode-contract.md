# Issue #188: CD_READ の1モード・1セクターの転送先と count 境界（静的契約整理）

親: #12 / #11。前提: #148（CLOSED、呼出側台帳）、#139（CLOSED、契約台帳）。後続: #189（無媒体・範囲外）、#190（独立 RAM 転送 routine）、#149（slot `$03` 接続）。
既存文書（参照のみ・書き換えない）: [cd-read-contract.md](cd-read-contract.md)、[cd-read-diagnostic.md](cd-read-diagnostic.md)、[cd-read-mode-contract.md](cd-read-mode-contract.md)、[188-cd-read-mode-safety.md](188-cd-read-mode-safety.md)、[research/148-cd-read-abi.md](research/148-cd-read-abi.md)、[abi-contracts.md](abi-contracts.md)（CD_READ 節）。

## 優先観測候補（D1 の決定）

* **優先観測候補: `_dh = 3`**（RAM bank `_bl` を MPR3 で扱う。コードの事実: 呼出側が `_dh` に入れた 1バイト値を、**非0のときだけ** `_al` へ複写する。コードから確定できるのはこの条件付きコピーまでで、count の単位は導けない。「セクター」であることはコメント/宣言由来の HYPOTHESIS（A4）。）。オーナー承認（PR #503 の条件付き承認）により、#188 の最初の観測対象とする。
* 根拠は公開 HuCC 呼出側（`include/hucc/` の `hucc-systemcard.asm` の cd_read 呼出し）のみ。確認範囲は `include/hucc/` の2ファイルで、`include/huc/` は未検査。
* これは **観測対象の選定** であり、純正 System Card 3.0 callee の ABI 確定ではない。callee の成功値、エラー、転送先、count 境界は UNKNOWN のまま。
* HuC ライブラリ（`include/huc/`）と Hu-Go! は、ライセンス・出典審査が終わるまで正式な ABI 根拠として使わない。本書では、既存文書間の出典表記の食い違いを記録するためにのみ参照する。
* 既存の `_dh = 2` 契約（[cd-read-mode-contract.md](cd-read-mode-contract.md) R3）の HuCC 出典は訂正した（§4 D1）。

## 0. 判定と範囲

* **実観測: 未実施。純正 BIOS の実行 0 件、`$E009` の呼出 0 件。** 安全ゲート（#173 の子 #365/#366/#367 が OPEN、slot 3 の reviewed gate 記録と独立承認なし）が未充足のため。
* 本書は **静的な読み取りのみ** の契約整理。新規の Geargrafx 実行、新規の実機観測はない。
* 根拠は次の3種類に分けて記す。
  * **[C] 公開 caller ソース**: HuCC `pce-devel/huc@54f5c73606c1e7142e8095c727cab2767e2b3cb3` の呼出側コード。確認範囲は `include/hucc/` の2ファイル（`hucc-systemcard.asm` `4258b7c3…`、`core-kernel.asm` `ae183017…`。sha256 は `spec/api_contracts.json` と一致）のみ。`include/huc/` は未検査（[cd-base-abi-ledger.md](cd-base-abi-ledger.md):146、:151 の方針により、存在の事実も根拠に使わない）。行番号は取得したファイルに対するもの。
    - **[C] は「命令がそう書いている」ことだけを指す。** 同一ソース内のコメント・宣言（例: `_cd_loadbank.4` の宣言 `unsigned int sectors<_al>`（L425）、コメント `; Get #sectors (0=whole file).`（L436））は命令の事実ではない。**HYPOTHESIS（コメント/宣言由来）** として扱い、単独では [C] の根拠にしない。count の単位（セクターか否か）はこの区別に従う。
  * **[D] 既存文書**: 本リポジトリの既存文書・台帳（`tools/cd_read_contract.py` の定数を含む）。
  * **[M] 実測（既存の観測証跡のみ）**: 転送層の READ(6) を Geargrafx（`b49ae82a`）で実行した結果。callee の実測ではない。
* 「確定」は **呼出側の入力・事実**（HuCC の cd_read 呼出側コードの**命令がそう書いている**こと。コメント・宣言は含まれない）に限る。[M] の転送層計測は callee の確定として読まない。callee（BIOS）がそれをどう解釈するかは、すべて未確定。

## 1. 対象モードの選定

対象は **`_dh = 3`**（RAM bank `_bl` を呼出側が MPR3 で扱う。コードの事実: 呼出側が `_dh` に入れた 1バイト値を、非0のときだけ `_al` へ複写する。count の単位はコードから導けず、「セクター」であることはコメント/宣言由来の HYPOTHESIS）とする。

| 候補 | 公開 caller での使用 | 1セクター観測の扱い | 判断 |
| --- | --- | --- | --- |
| `_dh = 3` | [C] HuCC `_cd_loadbank.4`（`hucc-systemcard.asm` L425-457）。L440-441 で `_dh=3`。L436-438 で `_dh` の値を非0のときだけ `_al` へ複写（コードの事実） | [HYPOTHESIS] count の単位がセクターであることは宣言 `sectors<_al>`（L425）とコメント（L436）に基づく仮説であって、コードの事実ではない（A4）。「1セクター指定が自然」という判断はこの仮説に依る | **採用（候補）** |
| `_dh = 0` | [C] HuCC L384 `stz`（`_dh = 0`）。L381-382 が `_ax` に読出し長を入れる（コードの事実。1回の読み出し長は最大 `$2000`: L369 `cmp.h #$2000` / L372 `sbc.h #$2000` / L375 `lda.h #$2000`）。単位が「バイト」なのはコメント（L381「Set read length in bytes.」/ L384「Signal bytes, not sectors.」）由来 | 1セクター = 2048 バイト = `$0800`。count 単位が別軸（[HYPOTHESIS]、コメント/宣言由来） | 別モード（対象外） |
| `_dh = 2` | [C] 確認範囲（`include/hucc/` の2ファイル）の `cd_read` 呼出側に使用箇所なし（後述 D1） | 既存文書 [cd-read-mode-contract.md](cd-read-mode-contract.md) / [188-cd-read-mode-safety.md](188-cd-read-mode-safety.md) の対象。既存文書間の出典表記が食い違っていた（訂正前の cd-read-mode-contract.md R3 は HuCC を挙げていた。#503 で訂正済み（§4 D1）。HuCC 側に `_dh=2` が無いことは確認済み） | 既存契約の不一致は #503 で訂正済み（D1） |
| `$FE` / `$FF` | [C] L294 `lda #$FF` / L322 `lda #$FE` を `_dh` に書く（コメントは「Signal a VRAM load.」） | VRAM 系 | 対象外 |
| `6` | [C] 上書きローダ（`core-kernel.asm` L593-594） | MPR6 系 | 対象外 |

**D1（決定・オーナー承認）**: 優先観測候補は `_dh = 3` とする（冒頭「優先観測候補」参照）。`_dh = 2` について既存文書間で出典表記が食い違っていた。[cd-read-mode-contract.md](cd-read-mode-contract.md) の R3 行（origin/main で :31。#503 より前の版では :30）は、訂正前は「HuCC, Hu-Go HLE」を出典としていたが、HuCC が `_dh=2` を書かないことを確認したため訂正した（§4）。[cd-read-contract.md](cd-read-contract.md) の Destination mode 行は、#503 の時点（当時の行番号 :137）では主張を変えず出典欄に訂正注記のみを追記した。その後 #574（ce85915）で**一行を二行に分割**した（origin/main では :147 が `_dh` 0/3/6/`$FE`/`$FF`、:148 が `_dh` 1/2/4/5）。`_dh` 1/2/4/5 は HYPOTHESIS から **UNKNOWN** に下げた（§4 D1）。[abi-contracts.md](abi-contracts.md):138（「Hu-Go model; HuCC overlay loader also uses 6」）は本 PR の差分対象外で、食い違いを含めて事実として残す。`cd_read` の HuCC 呼出側（`include/hucc/` の2ファイル、ledger C06-C10、[research/148-cd-read-abi.md](research/148-cd-read-abi.md) §2.1）で確認できる `_dh` の値は `0 / 3 / $FE / $FF / 6` のみ。`include/huc/` は未検査。

## 2. 1モード・1セクターの契約表（`_dh = 3`）

根拠欄の略号: [C] / [D] / [M]（§0 参照）。状態は `HYPOTHESIS` / `UNKNOWN` / `BLOCKED` と「確定（呼出側）」の表記に限る。転送層の既存証跡は [M] と書き、`VERIFIED` という語は本書では使わない（[cd-read-contract.md](cd-read-contract.md) の宣言的 VERIFIED とは別物として扱う）。§2.3 は「呼出側の根拠」を **「コードの事実 [C]」** と **「コメント/宣言由来（HYPOTHESIS）」** の別列に分ける。両者を混ぜず、転送層の [M] とも混ぜない（[M] は callee の確定として読まない、§0）。

### 2.1 呼出側の入力（caller が何を書くか）

| # | 項目 | 呼出側で確定していること | 根拠 | 状態（呼出側） | callee 側の解釈 |
| --- | --- | --- | --- | --- | --- |
| A1 | 転送先の種類 | `_dh = 3` を書く（L440-441）。コメントは「Use MPR3.」（L440） | [C] | 確定（入力値） | UNKNOWN（callee が 3 を RAM bank 転送と解釈するかは未観測） |
| A2 | 転送先の基準 | `_bl` と `_bx` は本ラッパ内で書かれない（L425-457 に `_bl` / `_bx` の書込みなし。呼出時の値をそのまま保持する） | [C] | 確定（`_bx` 未設定）。**「bank」の語は宣言 L425 `unsigned char bank<_bl>` に基づく HYPOTHESIS（コメント/宣言由来）**。コードの事実ではない | MPR3 窓内のオフセットを callee が既定値（0 等）で扱うか: UNKNOWN |
| A3 | 宛先のアドレス単位（byte / sector） | 記述なし | [C] | UNKNOWN | UNKNOWN |
| A4 | count の単位 | コードの事実: L436-438 は `_dh` の値を読み、**非0のときだけ** `_al` へ 1バイト値を複写する。導出できるのは「呼出側が AL に 1バイト値を入れる」ことまで。count の単位はコードから導けない | [C]（コードの事実のみ） | 単位: UNKNOWN（「セクター」は宣言 `sectors<_al>`（L425）とコメント「Get #sectors (0=whole file).」（L436）に基づく **HYPOTHESIS（コメント/宣言由来）**。コードの事実ではない。D2 参照） | `_al` をセクター数として解釈するか: UNKNOWN（[D] `abi-contracts.md` の表は HYPOTHESIS の扱い） |
| A5 | 転送層の1セクター（count 1） | READ(6) で LBA 0 / 5 / 15 の1セクターを受信し、バイトが一致する | [M] `single_sector_lba5`、`first_sector_lba0`、`last_sector_lba15`（[cd-read-contract.md](cd-read-contract.md) §2 T1） | 転送層のみ（[M]、Geargrafx のみ） | callee の `_al = 1` 解釈: UNKNOWN |
| A6 | 「1セクター」の呼出側の使用 | 確認範囲の2ファイルに、`_dh = 3` の呼出で count 1 を渡す箇所はない。件数の計算元を示すコードも確認範囲にない（根拠なし） | 根拠なし | UNKNOWN（count 1 の caller 使用例なし）。count の数値がセクター単位と確定できるか否かは、A4 のとおり HYPOTHESIS（コメント/宣言由来）のまま | UNKNOWN |
| A7 | VRAM 転送の有無 | なし。VRAM 転送は `$FE` / `$FF` で信号する（命令は L294 `lda #$FF` / L322 `lda #$FE` と `sta <_dh`。**「VRAM」の語はコメント（L294/L322「Signal a VRAM load.」）由来**） | [C]（L294 `$FF`、L322 `$FE` と本ラッパの `_dh=3` の対比。VRAM である根拠はコメント/宣言由来で HYPOTHESIS） | 確定（呼出側は `$FE`/`$FF` を指定しない） | UNKNOWN（3 が VRAM に書かないかは未観測） |
| A8 | 基点（block number）の作り方 | `_cl:_ch:_dl` は呼出側で作る（`get_file_info` / `_add_sectors`、[D] `research/148-cd-read-abi.md` C15-C16）。絶対 LBA とみなすか CD_BASE 相対か | [C][D] | 確定（呼出側の計算） | UNKNOWN（#11 CD_BASE と未解決の矛盾、[D] `research/148-cd-read-abi.md` §6） |

### 2.2 呼出側が読む・保存する状態

| # | 項目 | 呼出側で確定していること | 根拠 | 状態 | callee 側 |
| --- | --- | --- | --- | --- | --- |
| B1 | 戻り値 | `system cd_read` の後、コードを `X` へ移し（`tax`）、MPR3 を戻してから `A` に戻す（`txa`） | [C] L448-451 | 確定（呼出側の戻り処理） | 成功時 A の値: UNKNOWN。`_cd_loaddata.4` の `tax`/`bne`（L389-390）は別関数の慣習で callee の根拠ではない（#188 完了条件 3） |
| B2 | 失敗時 A とエラーコード体系 | 呼出側は非0を透過する | [C] | UNKNOWN | UNKNOWN（no disc / bad block / read error / busy の値なし。[D] `abi-contracts.md` CD_READ 節） |
| B3 | MPR3 | 呼出側が `tma3`/`pha` で退避し、`tam3` で戻す（L443-444、L449-450） | [C] | 確定（呼出側の防御） | 成功時 MPR3 の状態: UNKNOWN。失敗時に MPR3 が壊れる: HYPOTHESIS（コメント L448-451 「System Card function has a bug which destroys the value on a CD error」） |
| B4 | MPR0-2 / 4-7 | 呼出側は観測しない | [D] `research/148-cd-read-abi.md` C32 | UNKNOWN | UNKNOWN |
| B5 | X / Y | 呼出側は戻り後に `cly`（L453）で Y を 0 にし、`leave`（L455）で X を A に移す。Y:X 形式の戻り値の上位は呼出側が書く | [C] | 呼出後の X/Y 保存: UNKNOWN | UNKNOWN |
| B6 | `$20F8-$20FF` | 本ラッパは `_cl` / `_si` を使う。呼出前後の退避は本ラッパ内で見えない | [C] | UNKNOWN | UNKNOWN（[D] C22 / C34） |
| B7 | IRQ 前提 | 本ラッパ内に `cli` / `sei` がない（L425-457）。上書きローダだけ `cli` を置く | [C]（[D] C17） | 呼出し時の P.I は不明 | CLI の要否: UNKNOWN |
| B8 | stack | JSR 入口・RTS 出口のみ観測。BIOS 内の深さは未観測 | [D] | UNKNOWN | UNKNOWN |
| B9 | flags（N/Z/C/T/D） | 本ラッパは戻り後の flags で分岐しない（`tax` の後に `txa` で A を戻すだけ） | [C] | 観測なし | UNKNOWN |

### 2.3 count と範囲の境界

| 入力 | 呼出側の根拠: コードの事実 [C] | 呼出側の根拠: コメント/宣言由来（HYPOTHESIS） | 転送層の既存計測 [M]（callee の確定ではない） | callee の結果 | 状態 |
| --- | --- | --- | --- | --- | --- |
| count 1、LBA 0 / 5 / 15 | count 1 を渡す caller 使用例はない（A6）。コードの事実として言えるのは L436-438 の条件付きコピー（`_dh` が非0のときだけ `_al` に 1バイト値を入れる）まで | 単位がセクターであること（A4）。count 1 が「1セクター」に相当するかはこの仮説の下でも未検証 | T1: 1セクターを受信（`first_sector_lba0`、`single_sector_lba5`、`last_sector_lba15`） | UNKNOWN | 転送層のみ正例。callee は未観測 |
| count 0 | 呼出側は `_al` を書かない分岐（L437 `beq` → L438 の `sta <_al` を飛ばす）。呼出前の `_al` の値をそのまま保持する | 「0 = whole file」は呼出側コメント（L436）の記述であり、コードの事実ではない | T8 相当（count 0 は未計測） | UNKNOWN | 未確定（呼出側の分岐の結果が BIOS に届く値は UNKNOWN） |
| count 2 / 3 | 呼出側に例なし（A6） | 単位がセクターであること（A4 の仮説のまま） | T2: 3 セクター連続受信（`three_sectors_lba5`）。ただし `_dh = 3` の経路ではない | UNKNOWN | 対象外（別 Issue） |
| count 4 以上 | 呼出側に例なし | 単位がセクターであること（A4 の仮説のまま） | T8: 未計測 | UNKNOWN | UNKNOWN |
| LBA 16 以上（媒体の範囲外） | 対象外（#189） | - | T3: STATUS のみ、0 バイト、宛先は未変更（`invalid_sector_lba16`、`invalid_sector_lba_max`） | UNKNOWN | #189 |
| 末尾跨ぎ（LBA 14 x3 など） | 対象外（#189） | - | T4: 末尾まで届いて STATUS（`read_past_disc_end_lba14_x3`） | UNKNOWN | #189 |
| 無媒体 | 対象外（#189） | - | 未計測（Geargrafx では実行不能、T7） | UNKNOWN / BLOCKED | #189 |

## 3. 観測証跡の分類（今回の新規実測はゼロ）

| 証跡 | 種類 | 内容 | 本書での扱い |
| --- | --- | --- | --- |
| HuCC 呼出側（L294-297、L322-325、L384-388、L425-457、`core-kernel.asm` L593-594） | [C] 公開 caller | `_dh` の書込み値と、その前後の `_al` / `_bl` / MPR3 退避 | 呼出側の事実として採用（命令が書いていること）。コメント・宣言の単位解釈は含まない。callee の値域の根拠にしない |
| 転送層 READ(6)（T1-T4） | [M] 既存実測（Geargrafx `b49ae82a`） | 1-3 セクターの受信、範囲外 STATUS、末尾跨ぎ | バスの振る舞いのみ。`$E009` の結果ではない |
| `CALLEE_REAL_CORE_RUNS = 0`（`tools/cd_read_contract.py`） | [D] 既存の機械記録 | callee の実行回数 0 | 本モードの callee 項目はすべて UNKNOWN / HYPOTHESIS |
| stum.de / Zeograd | [D] 第三者 | `$E009` の入口、`recbase0` 相対の主張 | 入口の HYPOTHESIS にのみ使用。基点は UNKNOWN（A8） |

## 4. 既存文書との整合（参照のみ）

* **D1**: 訂正済み（#503 の時点）。[cd-read-mode-contract.md](cd-read-mode-contract.md) R3 の出典から HuCC を外した。`_dh=2` について HuCC が書かないことは `include/hucc/` の2ファイルの範囲で確認した。現在の R3 は **UNKNOWN**（#574 で HYPOTHESIS から変更。出典は Hu-Go! HLE のみで、参照のみ・ライセンス未確定）。
  [cd-read-contract.md](cd-read-contract.md) の Destination mode は、#503 の時点では出典欄への訂正注記のみだったが、#574 で次の二行に**分割**された（origin/main。主張の状態はそれぞれ下記）。
  * `_dh` 0 / 3 / 6 / `$FE` / `$FF`（origin/main :147）: **HYPOTHESIS のまま**。根拠は HuCC 呼出側の書込み（`_dh=0` は `hucc-systemcard.asm` L384、`_dh=3` は L440-441、`_dh=$FE` は L322、`_dh=$FF` は L294、`_dh=6` は `core-kernel.asm` L593-594）。これは「呼出側がその値を書く」ことの根拠であり、callee がその値をどう解釈するかの根拠ではない。callee の実行は未実施（`CALLEE_REAL_CORE_RUNS = 0`）。行番号は doc-cited、本リポジトリでは未検証（§4 D5）。
  * `_dh` 1 / 2 / 4 / 5（origin/main :148）: **HYPOTHESIS から UNKNOWN へ下げた**（#573 / #574）。根拠は Hu-Go! HLE のみ（参照のみ、ライセンス未確定）。HuCC は `_dh=2` を書かないことを確認している。
  [abi-contracts.md](abi-contracts.md):138 は**変更していない**（食い違いを含めて事実として残す）。
* 2026-10-29 注記: PR #574（ce85915）で cd-read-mode-contract の R3・R8 は UNKNOWN に変更された。その後 PR #576（72de119、Issue #575）で `tools/cd_read_contract.py` の RAM_MODE_ITEMS の R3・R8 も UNKNOWN に揃え、R6 の出典文を HuCC 呼出側の期待に書き換えた（R6 の状態は HYPOTHESIS のまま）。
* **D5（R6 の参照先の関数名）**: [cd-read-mode-contract.md](cd-read-mode-contract.md) R6 が引く HuCC の行の所属関数は次のとおり。関数名と範囲は [cd-base-abi-ledger.md](cd-base-abi-ledger.md) S4・S5 と [cd-exec-contract-ledger.md](cd-exec-contract-ledger.md) H1b の記載に基づく **doc-cited、本リポジトリでは未検証**（HuCC ソースはこのリポジトリにない）。
  * `hucc-systemcard.asm` L389-390（`tax` / `bne .done`）: 関数 `_cd_loaddata.4`（ledger S4 では `_cd_loaddata`、344–408 行の内側。`cd_read` の呼出しは 388 行）。
  * `core-kernel.asm` L600-601（`cmp #0` / `bne .retry`）: 関数 `exec_overlay`（H1b では 578–614 行。ledger S5 では上書き読込み、`cd_read` は 596 行）。
* **D2**: HuCC `_cd_loadbank.4` の宣言（L425）は `unsigned int sectors<_al>` と書いているが、本体（L436-438）は `_dh` を読むだけで、**非0のときだけ** `_al` へ 1バイト値を複写する。宣言・コメント（`sectors` / 「Get #sectors (0=whole file).」）は count の単位を規定するコードではなく、コードから確定できるのは「呼出側が AL に 1バイト値を入れる」ことまでである。したがって callee が count として読むレジスタは UNKNOWN のままで、count の単位も導出できない（「セクター」であることは HYPOTHESIS（コメント/宣言由来）、A4）。本 D2 は A4 の区分と矛盾しない。
* **D3**: 基点（CD_BASE 相対か絶対か）の矛盾は [research/148-cd-read-abi.md](research/148-cd-read-abi.md) §6 の通り未解決。#11 の測定まで UNKNOWN。
* **D4**: 既存の [188-cd-read-mode-safety.md](188-cd-read-mode-safety.md) の判定 BLOCKED は本書でも変わらない。本書は判定を変更しない。

## 5. #189 / #190 / #149 への引き継ぎ

未確定として残す項目。

* **#189（無媒体・範囲外の戻りと副作用）**: count 0 の BIOS 挙動（A4 / count 0 行）、無媒体の戻り（T7）、範囲外の戻り（T3 の callee 版）、末尾跨ぎの部分書込み。`_dh` の決定（D1）に依存する。
* **#190（独立 RAM 転送 routine）**: 宛先アドレス（A2 / A3）、戻り値の規約（B1 / B2）、MPR3 の扱い（B3）、IRQ 前提（B7）、LBA 基点（A8、#11）。callee の解釈が UNKNOWN のまま実装すると、ABI を推測で固定することになる。実装は行わない。
* **#149（slot `$03` 接続と実コア正負例）**: 上記すべて。さらに #173 系（#365-367）が閉じるまで実行不可（BLOCKED）。

## 6. 判定

* 本 PR（文書のみ）: 静的契約整理。オーナーが条件付きで承認。条件（1）D1 は `_dh = 3` を優先観測候補とする決定で満たした。条件（2）表のレビューは独立レビューで行う（Draft 解除はオーナーが判断）。判定は CONDITIONAL のまま。
* #188 の実観測（callee の1セクター・1モード観測）: **BLOCKED**。根拠: #173 の子 #365 / #366 / #367 が OPEN、slot 3 の reviewed gate 記録と独立承認がない（[188-cd-read-mode-safety.md](188-cd-read-mode-safety.md) §1 G1-G2）。

## 7. 整合確認

* 参照した Issue の状態（確認日 2026-10-22）: #12 OPEN、#11 OPEN、#139 CLOSED、#148 CLOSED、#149 OPEN、#173 OPEN、#189 OPEN、#190 OPEN、#365 / #366 / #367 OPEN、#389（PR）MERGED。
* PR #574 の head commit（記録上）は `89a2fcd` と `ed1ec32`。squash 後の `origin/main` の ce85915 の文書内容は、head `89a2fcd` の文書内容と一致する（確認済み）。ce85915 の変更対象は文書 4 ファイルのみ（`docs/188-cd-read-transfer-mode-contract.md`、`docs/cd-base-contract.md`、`docs/cd-read-contract.md`、`docs/cd-read-mode-contract.md`）で、`tools/` と `tests/` は含まない。
* `docs/cd-read-mode-contract.md` の R3・R8 は #574 で UNKNOWN に変更した。`tools/cd_read_contract.py` の R3・R8 は #574 の時点では `HYPOTHESIS` のままだったが、PR #576（72de119、Issue #575）で UNKNOWN に揃えた。本書の時点の origin/main では、文書と tools の R3・R8 は一致する。
* 本書の検証記録（本修正の作成時。基点 origin/main `72de119`、worktree 上の全リポジトリ木）:
  * 相対 Markdown リンク: 対象 5 文書（本書、`cd-base-contract.md`、`cd-read-contract.md`、`cd-read-mode-contract.md`、`cd-base-abi-ledger.md`）の相対リンク 29 件を検査し、欠落 0。
  * host 試験（`python -I`、`tests/host`）: `test_cd_read_contract` 56 件 OK、`test_cd_read_mode2_fixture` 7 件 OK、`test_cd_read_v16_contract` 45 件 OK、`test_cd_base_model` 28 件 OK。
  * HuCC ソース（`include/hucc/`）はこのリポジトリにないため、本書と各台帳の HuCC 行番号は**未検証**（文書に引用されたもの）。
* 「#573」は Issue 番号である（PR 番号ではない）。
* 過去の記録（参考）: CD_READ 関連 3 モジュールの当時の実行件数は、PR #503 のコメントにある。これは PR #503 の時点の記録であり、本修正の検証結果ではない。本書には件数を転記しない。
