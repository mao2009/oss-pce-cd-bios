# Issue #188: CD_READ の1モード・1セクターの転送先と count 境界（静的契約整理）

親: #12 / #11。前提: #148（CLOSED、呼出側台帳）、#139（CLOSED、契約台帳）。後続: #189（無媒体・範囲外）、#190（独立 RAM 転送 routine）、#149（slot `$03` 接続）。
既存文書（参照のみ・書き換えない）: [cd-read-contract.md](cd-read-contract.md)、[cd-read-diagnostic.md](cd-read-diagnostic.md)、[cd-read-mode-contract.md](cd-read-mode-contract.md)、[188-cd-read-mode-safety.md](188-cd-read-mode-safety.md)、[research/148-cd-read-abi.md](research/148-cd-read-abi.md)、[abi-contracts.md](abi-contracts.md)（CD_READ 節）。

## 0. 判定と範囲

* **実観測: 未実施。純正 BIOS の実行 0 件、`$E009` の呼出 0 件。** 安全ゲート（#173 の子 #365/#366/#367 が OPEN、slot 3 の reviewed gate 記録と独立承認なし）が未充足のため。
* 本書は **静的な読み取りのみ** の契約整理。新規の Geargrafx 実行、新規の実機観測はない。
* 根拠は次の3種類に分けて記す。
  * **[C] 公開 caller ソース**: HuCC `pce-devel/huc@54f5c73606c1e7142e8095c727cab2767e2b3cb3` の呼出側コード。今回このリビジョンのファイルを取得し、`spec/api_contracts.json` の sha256 と一致することを確認した（`hucc-systemcard.asm` `4258b7c3…`、`core-kernel.asm` `ae183017…`）。行番号は取得したファイルに対するもの。
  * **[D] 既存文書**: 本リポジトリの既存文書・台帳（`tools/cd_read_contract.py` の定数を含む）。
  * **[M] 実測（既存の観測証跡のみ）**: 転送層の READ(6) を Geargrafx（`b49ae82a`）で実行した結果。callee の実測ではない。
* 「確定」は **呼出側が何を書くか** と **転送層の既存計測** に限る。callee（BIOS）がそれをどう解釈するかは、すべて未確定。

## 1. 対象モードの選定

対象は **`_dh = 3`（RAM bank `_bl` を呼出側が MPR3 で扱う。count は `_al` のセクター数）** とする。

| 候補 | 公開 caller での使用 | 1セクター観測の扱い | 判断 |
| --- | --- | --- | --- |
| `_dh = 3` | [C] HuCC `_cd_loadbank.4`（`hucc-systemcard.asm` L425-452）。L440-441 で `_dh=3`、count は `_al` | count がセクター単位で、1セクター指定が自然 | **採用（候補）** |
| `_dh = 0` | [C] HuCC L384 `stz`。count は `_ax` のバイト数 | 1セクター = 2048 バイト = `$0800`。count 単位が別軸 | 別モード（対象外） |
| `_dh = 2` | [C] **HuCC 呼出側に使用箇所なし**（後述 D1） | 既存文書 [cd-read-mode-contract.md](cd-read-mode-contract.md) / [188-cd-read-mode-safety.md](188-cd-read-mode-safety.md) の対象。源は Hu-Go! HLE のみ | 既存契約との不一致として未決（D1） |
| `$FE` / `$FF` | [C] VRAM 転送（L322 / L294） | VRAM 系 | 対象外 |
| `6` | [C] 上書きローダ（`core-kernel.asm` L593-594） | MPR6 系 | 対象外 |

**D1（未決・owner 判断）**: 既存の #188 契約 `_dh = 2` は HuCC の呼出側（ledger C06-C10、`research/148-cd-read-abi.md` §2.1）で使われる値ではない。HuCC が書く `_dh` の値は `0 / 3 / $FE / $FF / 6` のみで、`2` は Hu-Go! HLE にしか現れない。本書は既存文書を書き換えず、対象を `_dh = 3` とする候補を示す。`_dh = 2` を #188 の観測対象に残すか、`_dh = 3` に切り替えるかは owner が決める。どちらにしても #189 / #190 はこの決定に依存する。

## 2. 1モード・1セクターの契約表（`_dh = 3`）

根拠欄の略号: [C] / [D] / [M]（§0 参照）。状態は `VERIFIED` / `HYPOTHESIS` / `UNKNOWN` / `BLOCKED`。`VERIFIED` は本書では付けない（台帳の `VERIFIED` 語彙は転送層の宣言的な主張に限られ、本モードには該当しない）。

### 2.1 呼出側の入力（caller が何を書くか）

| # | 項目 | 呼出側で確定していること | 根拠 | 状態（呼出側） | callee 側の解釈 |
| --- | --- | --- | --- | --- | --- |
| A1 | 転送先の種類 | `_dh = 3` を書く（L440-441）。コメントは「MPR3 を使う」 | [C] | 確定（入力値） | 3 を RAM bank 転送と解釈するか: HYPOTHESIS（[D] `abi-contracts.md` の表は「3 = bank `_bl`, MPR3」と記す） |
| A2 | 転送先の基準 | `_bl` に bank を入れる（呼出元の入力）。`_bx` は本ラッパ内で書かれない（L425-452 に `_bx` の書込みなし） | [C] | 確定（`_bx` 未設定） | MPR3 窓内のオフセットを callee が既定値（0 等）で扱うか: UNKNOWN |
| A3 | 宛先のアドレス単位（byte / sector） | 記述なし | [C] | UNKNOWN | UNKNOWN |
| A4 | count の単位 | `_al` にセクター数。コメントは「#sectors」（L436-438 の分岐は `_dh` をセクター数として読み、非0のときだけ `_al` に複写する） | [C] | 確定（単位はセクター） | `_al` をセクター数として解釈するか: HYPOTHESIS（[D] `abi-contracts.md` の表） |
| A5 | 転送層の1セクター（count 1） | READ(6) で LBA 0 / 5 / 15 の1セクターを受信し、バイトが一致する | [M] `single_sector_lba5`、`first_sector_lba0`、`last_sector_lba15`（[cd-read-contract.md](cd-read-contract.md) §2 T1） | 転送層のみ（宣言的 VERIFIED、Geargrafx のみ） | callee の `_al = 1` 解釈: UNKNOWN |
| A6 | 「1セクター」の呼出側の使用 | HuCC は `_dh = 3` の呼出で count 1 を渡す箇所を示していない（呼出側はファイル長から可変に計算） | [C] | UNKNOWN（count 1 の caller 使用例なし） | UNKNOWN |
| A7 | VRAM 転送の有無 | なし。VRAM 転送は `$FE` / `$FF` で信号する | [C]（L294 `$FF`、L322 `$FE` と本ラッパの `_dh=3` の対比） | 確定（呼出側は VRAM を指定しない） | 3 が VRAM に書かないこと: HYPOTHESIS |
| A8 | 基点（block number）の作り方 | `_cl:_ch:_dl` は呼出側で作る（`get_file_info` / `_add_sectors`、[D] `research/148-cd-read-abi.md` C15-C16）。絶対 LBA とみなすか CD_BASE 相対か | [C][D] | 確定（呼出側の計算） | UNKNOWN（#11 CD_BASE と未解決の矛盾、[D] `research/148-cd-read-abi.md` §6） |

### 2.2 呼出側が読む・保存する状態

| # | 項目 | 呼出側で確定していること | 根拠 | 状態 | callee 側 |
| --- | --- | --- | --- | --- | --- |
| B1 | 戻り値 | `system cd_read` の後、コードを `X` へ移し（`tax`）、MPR3 を戻してから `A` に戻す（`txa`） | [C] L443-452 | 確定（呼出側の戻り処理） | 成功時 A = 0: HYPOTHESIS（[D] Hu-Go HLE・他の HuCC ラッパの `TAX/BNE` から。本モードの callee 観測なし） |
| B2 | 失敗時 A とエラーコード体系 | 呼出側は非0を透過する | [C] | UNKNOWN | UNKNOWN（no disc / bad block / read error / busy の値なし。[D] `abi-contracts.md` CD_READ 節） |
| B3 | MPR3 | 呼出側が `tma3`/`pha` で退避し、`tam3` で戻す（L443-444、L449-450） | [C] | 確定（呼出側の防御） | 成功時 MPR3 の状態: UNKNOWN。失敗時に MPR3 が壊れる: HYPOTHESIS（コメント L448-451 「System Card function has a bug which destroys the value on a CD error」） |
| B4 | MPR0-2 / 4-7 | 呼出側は観測しない | [D] `research/148-cd-read-abi.md` C32 | UNKNOWN | UNKNOWN |
| B5 | X / Y | 呼出側は X に戻りコードを書く。Y は使わない | [C] | 呼出後の X/Y 保存: UNKNOWN | UNKNOWN |
| B6 | `$20F8-$20FF` | 本ラッパは `_cl` / `_si` を使う。呼出前後の退避は本ラッパ内で見えない | [C] | UNKNOWN | UNKNOWN（[D] C22 / C34） |
| B7 | IRQ 前提 | 本ラッパ内に `cli` / `sei` がない（L425-452）。上書きローダだけ `cli` を置く | [C]（[D] C17） | 呼出し時の P.I は不明 | CLI の要否: UNKNOWN |
| B8 | stack | JSR 入口・RTS 出口のみ観測。BIOS 内の深さは未観測 | [D] | UNKNOWN | UNKNOWN |
| B9 | flags（N/Z/C/T/D） | 本ラッパは戻り後の flags で分岐しない（`tax` の後に `txa` で A を戻すだけ） | [C] | 観測なし | UNKNOWN（Hu-Go HLE は N/Z を A から設定、HYPOTHESIS のみ） |

### 2.3 count と範囲の境界

| 入力 | 呼出側の根拠 | 転送層の既存計測 [M] | callee の結果 | 状態 |
| --- | --- | --- | --- | --- |
| count 1、LBA 0 / 5 / 15 | [C] 単位はセクター（A4）。1セクター直接指定の caller 使用なし（A6） | T1: 1セクターを受信（`first_sector_lba0`、`single_sector_lba5`、`last_sector_lba15`） | UNKNOWN | 転送層のみ正例。callee は未観測 |
| count 0 | [C] 呼出側コメント「0 = whole file」（L436）。ただし `_dh` が 0 のとき `_al` を書かない分岐（L437-438）のため、BIOS は呼出前の `_al` を受け取る | T8 相当（count 0 は未計測） | UNKNOWN | 未確定（呼出側の分岐の結果が BIOS に届く値は UNKNOWN） |
| count 2 / 3 | [C] 呼出側に例なし（A6） | T2: 3 セクター連続受信（`three_sectors_lba5`）。ただし `_dh = 3` の経路ではない | UNKNOWN | 対象外（別 Issue） |
| count 4 以上 | 呼出側に例なし | T8: 未計測 | UNKNOWN | UNKNOWN |
| LBA 16 以上（媒体の範囲外） | 対象外（#189） | T3: STATUS のみ、0 バイト、宛先は未変更（`invalid_sector_lba16`、`invalid_sector_lba_max`） | UNKNOWN | #189 |
| 末尾跨ぎ（LBA 14 x3 など） | 対象外（#189） | T4: 末尾まで届いて STATUS（`read_past_disc_end_lba14_x3`） | UNKNOWN | #189 |
| 無媒体 | 対象外（#189） | 未計測（Geargrafx では実行不能、T7） | UNKNOWN / BLOCKED | #189 |

## 3. 観測証跡の分類（今回の新規実測はゼロ）

| 証跡 | 種類 | 内容 | 本書での扱い |
| --- | --- | --- | --- |
| HuCC 呼出側（L294-297、L322-325、L384-388、L425-452、`core-kernel.asm` L593-594） | [C] 公開 caller | `_dh` の書込み値と、その前後の `_al` / `_bl` / MPR3 退避 | 呼出側の事実として採用。callee の値域の根拠にしない |
| 転送層 READ(6)（T1-T4） | [M] 既存実測（Geargrafx `b49ae82a`） | 1-3 セクターの受信、範囲外 STATUS、末尾跨ぎ | バスの振る舞いのみ。`$E009` の結果ではない |
| `CALLEE_REAL_CORE_RUNS = 0`（`tools/cd_read_contract.py`） | [D] 既存の機械記録 | callee の実行回数 0 | 本モードの callee 項目はすべて UNKNOWN / HYPOTHESIS |
| Hu-Go! HLE（`bios.c` L378-573） | [D] HLE（原本でない） | `_dh` のモード分岐（2 を含む）、A=0 など | 契約の根拠にしない（[D] `research/148-cd-read-abi.md` §1 で「含めない」） |
| stum.de / Zeograd | [D] 第三者 | `$E009` の入口、`recbase0` 相対の主張 | 入口の HYPOTHESIS にのみ使用。基点は UNKNOWN（A8） |

## 4. 既存文書との整合（参照のみ）

* **D1**: 既存の `_dh = 2` 契約（[cd-read-mode-contract.md](cd-read-mode-contract.md) R3）は、出典に「HuCC」を挙げるが、HuCC の呼出側には `_dh = 2` がない（§1）。本書は修正せず、owner 判断へ回す。
* **D2**: HuCC `_cd_loadbank.4` の宣言は `sectors<_al>` だが、本体は `_dh` からセクター数を読む（L436）。宣言とコードが一致しないため、count の入力レジスタは UNKNOWN とした（A4）。
* **D3**: 基点（CD_BASE 相対か絶対か）の矛盾は [research/148-cd-read-abi.md](research/148-cd-read-abi.md) §6 の通り未解決。#11 の測定まで UNKNOWN。
* **D4**: 既存の [188-cd-read-mode-safety.md](188-cd-read-mode-safety.md) の判定 BLOCKED は本書でも変わらない。本書は判定を変更しない。

## 5. #189 / #190 / #149 への引き継ぎ

未確定として残す項目。

* **#189（無媒体・範囲外の戻りと副作用）**: count 0 の BIOS 挙動（A4 / count 0 行）、無媒体の戻り（T7）、範囲外の戻り（T3 の callee 版）、末尾跨ぎの部分書込み。`_dh` の決定（D1）に依存する。
* **#190（独立 RAM 転送 routine）**: 宛先アドレス（A2 / A3）、戻り値の規約（B1 / B2）、MPR3 の扱い（B3）、IRQ 前提（B7）、LBA 基点（A8、#11）。callee の解釈が UNKNOWN のまま実装すると、ABI を推測で固定することになる。実装は行わない。
* **#149（slot `$03` 接続と実コア正負例）**: 上記すべて。さらに #173 系（#365-367）が閉じるまで実行不可（BLOCKED）。

## 6. 判定

* 本 PR（文書のみ）: 静的契約整理として **CONDITIONAL**。条件は (1) D1 の owner 判断（`_dh = 2` と `_dh = 3` のどちらを #188 の観測対象とするか）、(2) 本書の表を人間がレビューすること。
* #188 の実観測（callee の1セクター・1モード観測）: **BLOCKED**。根拠: #173 の子 #365 / #366 / #367 が OPEN、slot 3 の reviewed gate 記録と独立承認がない（[188-cd-read-mode-safety.md](188-cd-read-mode-safety.md) §1 G1-G2）。

## 7. 整合確認

* 参照した Issue の状態（確認日 2026-10-22）: #12 OPEN、#11 OPEN、#139 CLOSED、#148 CLOSED、#149 OPEN、#173 OPEN、#189 OPEN、#190 OPEN、#365 / #366 / #367 OPEN、#389（PR）MERGED。
* 本書の内部リンクは同ディレクトリの既存ファイルを指す。
* 実行したテストはない（文書のみの変更）。
