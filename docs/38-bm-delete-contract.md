# $1C BM_DELETE 契約調査（Issue #38、G5 P0 案件）

- 対象: System Card 候補 slot `$1C` / 候補入口 `$E054` / 候補名 `BM_DELETE`（Issue #38）
- 状態: **契約 UNKNOWN。真正動作の再現根拠なし。判定 `BLOCKED_ABI`。実装なし。実行なし。**
  `release_eligible=false`。真正 ABI VERIFIED 0/81、参照 JSR 0/81、owner 承認 DEV_CALLABLE 0 を変えない。
- G5 位置づけ: 商用 IPL 起動経路の阻害点候補の 1 つ。観測された到達は
  **実験的 `$4A` 復帰の下でのみ** `$1E → $4A → $02 → $02 → $21 → $1C`（未実装停止）であり、
  真正 `$1C` の実行証拠ではない（§8）。
- 本書は呼出側（caller）の公開ソースの行番号とハッシュだけを引用する。
  純正 BIOS のバイト・逆アセンブル・商用媒体・参照 BIOS は扱わない。`./rom/` には触れていない。
- 削除は破壊的操作である。**根拠のない削除動作は実装しない。** 合成モデルは in-memory の
  mock に限り、実 BRAM・実セーブ・実デバイス・MMIO には一切触れない（§9）。

## 0. 語彙と判定ラベル

- `CONFIRMED-by-source`: 固定リビジョンの公開資料の指定行で直接確認できた事実。
  BIOS 側（callee）の挙動の証拠にはならない。情報源の種別をタグで明示する:
  `CALLER`（HuCC 呼出側の宣言・コメント・マクロ・記号定義）、`DIRECTLIB`（HuC の BIOS 非経由の直接実装＝経路 B）、
  `HLE`（Hugo エミュレータの実装または不在の事実）、`SECTABLE`（Stum 公開ジャンプ表の記載）。
  同一上流リポジトリ内の別ファイルの一致は、独立した呼出先の証拠とみなさない。
- `CALLER-EXPECTED`: 呼出側のコメント・定数に由来する期待値。真正 BIOS の確定契約ではなく、HYPOTHESIS 側に属する。
  本書では 3 値（`BM_OK / BM_NOT_FOUND / BM_NOT_FORMATTED`）の呼称として用いる。
- `HYPOTHESIS`: 出典はあるが、呼出先の事実としての裏付けがない推定。
- `UNKNOWN`: 公開ソースに記述がない、または確認できない。
- `NOT_RUN`: 実行・実機・実コアによる確認を行っていない。
- `PROJECT-INVENTED`: 本プロジェクトの合成定義。System Card の形式・値ではない。根拠にしない。
- `SIMULATED_NEXT_API`: 実験的・偽造的な復帰の下で観測された後続 API 番号。真正 ABI の証拠ではない。

呼出側のコメント・定数・宣言は、呼出先（BIOS）の仕様の証拠にならない。
固定公開資料で確認できた事実と、呼出先についての推定・不明は別行で分ける。

## 1. G5 文脈（サニタイズ済み・構造のみ）

| 項目 | 内容 |
| --- | --- |
| 到達鎖（CUE[1] 系） | `$1E → $4A → $02 → $02 → $21 → $1C`（`$1C` で未実装停止） |
| `$1E` | DEV `EX_GETVER` の実復帰が観測されている（真正 ABI ではない） |
| `$4A` | 実験的 preservation-only 実装（PR #824）または SIMULATED 復帰（PR #803）の下でのみ後続へ進む。真正 `$4A` は UNKNOWN / BLOCKED のまま |
| `$02` | 実験的実装（proto / 2-base）。商用 `$03` FIRST は本鎖では呼ばれていない |
| `$21` | DEV `EX_JOYSNS`。`$1C` の直前 API として観測された番号であり、因果の証明ではない |
| `$1C` | **真正契約 UNKNOWN のまま未実装停止**。到達の事実は「実験的 `$4A` 復帰と `$21` 復帰の下で `$E054` への JSR が起きた」ことに限る |
| cold/warm | 既存観測では一致（構造的事実。内容物の証拠ではない） |
| タイトル到達 | `G5_OBSERVED=false`。本書は何も変えない |

関連: Issue #38（本対象）、#802（G5 実測親）、#635（計測親）、#824（`$4A` 実験実装）、
#803（`$4A` SIMULATED 診断。後続 `$23 → $25` / `$02 → $02 → $21 → $1C` は SIMULATED_NEXT_API）、
#84（`$4A` 契約）、#34–#39（BURAM 系）。

## 2. 出典台帳

固定リビジョン: `pce-devel/huc` @ `54f5c73606c1e7142e8095c727cab2767e2b3cb3`、
`mckayemu/hugo` @ `64ed226da2288738781743eeef228c069e8973a4`。

| ID | 出典 | 種別・ライセンス | 確立すること | 扱い |
| --- | --- | --- | --- | --- |
| S1 | `huc:include/hucc/pcengine.inc`（SHA-256 `b6a25d33…728a9`、`spec/api_contracts.json` 記録値と一致）L494–499（`bm_read=$00:E04E` L496、`bm_write=$00:E051` L497、`bm_delete=$00:E054` L498、`bm_files=$00:E057` L499）、L329–343（`_ax=$F8:20F8` L329、`_bx` L333、`_cx` L337、`_dx` L341）、L395（`parameter_buf=$F8:2260,8` *UNDOCUMENTED*）、L204–205（`IFU_BRAM_LOCK=$FF:1803`、`IFU_BRAM_UNLOCK=$FF:1807`） | 呼出側宣言。BSL-1.0（ファイル L8）。PENDING | 名前と入口の対応、呼出側レジスタ名、BRAM 窓の記号 | REFERENCE_ONLY。BIOS 側の証明ではない |
| S2 | `huc:include/hucc/hucc-systemcard.asm`（SHA-256 `4258b7c3…ea9`、一致）L54–61（`BM_OK=0` L54、`BM_NOT_FOUND=1` L55、`BM_BAD_CHECKSUM=2` L56、`BM_DIR_CORRUPTED=3` L57、`BM_FILE_EMPTY=4` L58、`BM_FULL=5` L59、綴り誤り `BM_NOT_FORMATED=$FF` L60、正書 `BM_NOT_FORMATTED=$FF` L61）、L65（`__bm_error .ds 1`）、L775–790（`_bm_delete.1` マクロ。コメント L775–785、本体 L787–790） | 呼出側ラッパー。BSL-1.0（L10）、Copyright John Brandwood 2024（L8）。PENDING | 削除の呼出規約（呼出側）の全文 | REFERENCE_ONLY。コードを複製しない |
| S3 | `huc:include/hucc/hucc-systemcard.h`（SHA-256 `f2477642…90894220b8a3b`、一致）L31–38（BM 定数）、L115（`bm_delete(unsigned char *name<_ax>)` 宣言） | 呼出側 C 宣言。BSL-1.0（L13）。PENDING | 引数が名前ポインタ 1 つのみ | REFERENCE_ONLY |
| S4 | `huc:include/huc/huc_bram.asm`（SHA-256 `95d1d5f4…0a515`）L10–47（BRAM 形式コメント）、L326（`_bm_id`）、L350–353（`_bm_delete` は `lib2_bm_delete` への転送のみ）、L479–503（`lib2_bm_delete` 実体。open 成功後に memmove 的圧縮・end 調整）、L689–748（`lib2_bm_open`。走査・12 バイト名比較・size 検査・`#1/#3/#4/$FF` 分岐）、L750–771（`lib2_bm_enable`。タグ 4 バイト照合、不一致は `#$FF`）、L773–791（`lib2_bm_unlock`。`sei`・MPR4=`$F7`・`csl`・`$48/$75/$80` 順書込み）、L793–804（`lib2_bm_disable`。MPR4 復元・`bram_lock` 読取り・`csh`・`cli`） | **BIOS を呼ばない直接実装（経路 B）**。ファイル内ライセンス記載なし → UNKNOWN。PENDING | HuC ライブラリが BIOS 非経由で何をするかの事実。BIOS の仕様ではない | REFERENCE_ONLY。経路 A（BIOS `$E054`）と混同しない |
| S5 | `huc:include/huc/system.inc` L91–96（`bm_delete=$E054` L95）、`huc:include/huc/equ.inc` L236–237（`bram_lock=cd_port+3`、`bram_unlock=cd_port+7`）、L33–52（`__ax=$20F8` 等） | 記号定義。ライセンス記載なし → UNKNOWN。PENDING | 同一上流 `pce-devel/huc` 内の別系統の記号定義。S1 との一致は記号の再掲であり、独立した呼出先証拠ではない | REFERENCE_ONLY |
| S6 | Stum「PC Engine CD-ROM² BIOS Method offsets」（2025-07-28、CC BY-NC-SA 4.0 表示。事実引用のみ、本文は転載しない） | 公開ジャンプ表。二次資料 | 行 `bm_delete $E054`、本体番地 `1.0=$F951 / 2.0=$FA10 / 2.1=$FA1A / 3.0=$FA1A / TG2.0=$FA33 / TG3.0=$FA33`。JSR 列は全世代で `$E054` 一致 | REFERENCE_ONLY。名前・入口対応のみ。世代別本体番地の差異は「単一挙動を仮定できない」根拠 |
| S7 | `hugo:bios.c` @ `64ed226d`（SHA-256 `db3f227e…210972f22`）L65–70（`BM_FORMAT 0x18 … BM_DELETE 0x1C BM_FILES 0x1D` セレクタ定義）、L212–213（デバッグ名 `"BM_DELETE"`）、L982–998（HLE `BM_FREE` 実装あり）、L1091–1095（`default:` は未処理時に対象アドレスへ原本バイトを復元） | HLE エミュレータ。GPL-2.0 既定（`COPYING`）。PENDING、REJECTED（ROM 組込み不可） | `handle_bios()` の `switch` に `BM_DELETE` の `case` が無い（`BM_FREE` のみ実装）。名前ラベルの存在と挙動の不在 | REFERENCE_ONLY。HLE は実 BIOS の証明にならない。コードを複製しない |
| S8 | 自リポジトリ台帳 `spec/api_slots.json` id 28、`spec/api_contracts.json` slot 28（`mapping_status: HYPOTHESIS`、contract 全項目 UNKNOWN）、`spec/dev_implementations.json` id 28（`state: NOT_READY`、実装 null）、`docs/api-inventory.md` L53（`$1C/$E054/BM_DELETE` 一覧）、`tools/reference_bios_scenarios.py` L23（`BM_DELETE` を危険操作集合に分類。呼出しではない） | プロジェクト記録 | 現状は全面 UNKNOWN / NOT_READY / BLOCKED のまま。本書は変更しない | 記録のみ |
| S9 | G5 既存観測: PR #824 本文（CUE[1] `$1E→$4A→$02→$02→$21→$1C` halt、cold/warm 一致。`$4A` は preservation-only 実験実装）、PR #803（SIMULATED `$4A` 下の後続。真正 ABI ではない）、`docs/g5-commercial-reobservation-2026-11-18.md`、`docs/g5-evidence-2026-11-18.md` | 構造のみのサニタイズ済み観測 | `$1C` 到達は実験的 `$4A` 復帰の条件付き。真正 `$1C` の入出力・復帰の証拠ではない | OBSERVED（到達構造のみ）。内容物・生トレースは扱わない |
| S10 | BURAM 兄弟台帳 `docs/305-bm-free-contract.md`、`docs/contracts/bm-free.md`、`docs/contracts/bm-format.md`、`docs/research/302-bm-format-contract.md`、`docs/84-4a-contract.md`、`docs/497-mpr1-stack-contract.md`、`docs/173-safety-monitor-limits.md` | プロジェクトの契約・安全文書 | 呼出側/呼出先の分け方、MPR1 許可表（`$F8` のみ PASS）、MMIO 事前阻止不能の結論 | 方法の参照。値の流用はしない |

未取得: Zeograd `pce_bios.html` は本調査時に HTTP 403 で取得できず、行番号を確認していない。
名前の根拠には `docs/api-inventory.md` の既存記載（Zeograd・Stum を挙げる一覧）を参照のみとし、
本書の判定には使わない。呼出先（System Card BIOS）の公開仕様・第 2 の独立した呼出先根拠は未取得。
`./rom/`、商用情報、参照 BIOS は使用していない。

## 3. 呼出規約の台帳（確認事実と呼出先の UNKNOWN の分離）

### 3.1 入口・名称・世代差（公開仕様・由来）

| ID | 項目 | 事実（出典） | 判定 |
| --- | --- | --- | --- |
| R01 | 候補入口 | 呼出側は `bm_delete = $00:E054, FUNC` と等号定義（S1 L498。S5 L95 も `$E054` と一致するが、S1・S5 は同一上流由来のため一致は記号の再掲であり、独立した呼出先証拠にはならない） | CONFIRMED-by-source（呼出側の等号） |
| R02 | 名前と入口の対応 | `bm_delete` の名前は S1・S2・S3・S5・S6・S7 で一致するが、呼出先の同一性は未検証（S8 も `HYPOTHESIS`） | HYPOTHESIS |
| R03 | 世代差 | S6 の本体番地は世代ごとに異なる（1.0 `$F951`、2.0 `$FA10`、2.1/3.0 `$FA1A`、TG `$FA33`）。JSR 列は全世代 `$E054` | CONFIRMED-by-source（表の値）。挙動の同一性は UNKNOWN であり、単一挙動の仮定は禁止 |
| R04 | Hugo の扱い | S7 は `BM_DELETE 0x1C` のセレクタ名を持つが、`handle_bios()` に挙動 `case` が無い（`BM_FREE` のみ実装 L982–998。未処理は `default` L1091–1095 へ） | CONFIRMED-by-source（不在の事実。挙動の根拠にはならない） |
| R05 | BM_FILES との対照 | S1 L499・S5 L96 は `bm_files=$E057` を定義するが、S2 に `_bm_files` ラッパーは無い（`grep` で一致なし）。DELETE にはラッパー（R06）がある | CONFIRMED-by-source（存在・不在の事実） |

### 3.2 A/X/Y/P・MPR・stack（呼出規約）

| ID | 項目 | 事実（出典） | 判定 |
| --- | --- | --- | --- |
| R06 | 入力（呼出側） | `_bm_delete.1` は `system bm_delete` の前に `_ax` 以外を設定しない。C 宣言も `name<_ax>` 1 引数のみ（S2 L787–790、S3 L115） | CONFIRMED-by-source（呼出側は名前ポインタのみ） |
| R07 | 呼出先が入力を読むか・どれを読むか | 出典なし。`_ax` の指す 12 バイトを BIOS が読むか、MPR 可視性・長さ検証の有無は不明 | UNKNOWN |
| R08 | 戻り値の置き場所（呼出側） | 呼出し後に A を `__bm_error`（BSS 1 バイト、S2 L65）に格納し、`cly` で Y=0 にする（S2 L789–790）。A は BIOS の返値が残り、Y は破壊される。X はマクロ内で触らない | CONFIRMED-by-source（呼出側の読み先・破壊） |
| R09 | 呼出先が A に状態を返すか・X/Y/P をどうするか | 出典なし | UNKNOWN |
| R10 | 成功コード | 呼出側定数 `BM_OK = 0`（S2 L54、S3 L31） | CONFIRMED-by-source（呼出側の定数） |
| R11 | 呼出先が成功時に A=0 を返すか | 出典なし（R10 は呼出側の定数） | HYPOTHESIS（CALLER-EXPECTED。確定前は BLOCKED_ABI） |
| R12 | 存在しない対象のコード | 呼出側コメントは `BM_NOT_FOUND`（S2 L783、値 1 は L55）を返すと書く | CONFIRMED-by-source（呼出側コメントの存在＝CALLER-EXPECTED）。呼出先の事実は HYPOTHESIS（§4） |
| R13 | 未フォーマットのコード | 呼出側コメントは `BM_NOT_FORMATTED=$FF`（S2 L785、定義 L61）を返すと書く | CONFIRMED-by-source（呼出側コメントの存在＝CALLER-EXPECTED）。呼出先の事実は HYPOTHESIS |
| R14 | `__bm_error` の事後条件 | 呼出側コメントは `BM_OK / BM_NOT_FOUND / BM_NOT_FORMATTED` のいずれか（S2 L785） | HYPOTHESIS（CALLER-EXPECTED。呼出側コメントのみ。`_bm_read` コメント L737–739 の不一致例もあり鵜呑みにしない §6） |
| R15 | MPR（呼出側） | `_bm_delete.1` 本体に `tma/tam` は無い（S2 L787–790）。ポインタは `_ax`（`$F8:20F8`）経由で渡す | CONFIRMED-by-source（呼出側は MPR を変えない） |
| R16 | MPR 前提（BIOS 側） | Stum の一般 discipline は MPR7=`$00` 固定・MPR0=`$FF`・MPR1=`$F8` を要求（S6 本文）。G5 観測でも `$4A`/`$21` 到達時に MPR1=`$F8`（S9）。`$1C` 固有の前提は不明 | HYPOTHESIS（一般 discipline のみ。`$1C` 固有は UNKNOWN） |
| R17 | stack・復帰（呼出側の機構） | `system` 呼出しは JSR 経由のテーブル呼出し（S6 の JSR 列の趣旨）。呼出側マクロは `php/plp`・スタック退避をしない | CONFIRMED-by-source（呼出側の機構）。BIOS 側の保存・復帰・SP 変化は UNKNOWN |
| R18 | `system` の展開（JSR 先・バンク・MPR 退避復帰） | 定義は取得 3 ファイルに無い | UNKNOWN |
| R19 | P・割込み（呼出側） | マクロは `sei/cli` をしない（S2 L787–790）。BIOS 側は不明 | CONFIRMED-by-source（呼出側）／呼出先 UNKNOWN |
| R20 | フラグ（Carry/Zero 等の意味） | 出典なし。HuC 経路 B（S4）は `clc/sec` で成否を返すが BIOS 経路 A とは別物 | UNKNOWN（経路 B の流用禁止） |

### 3.3 work RAM（`$20F8`–`$20FF` 等）

| ID | 項目 | 事実（出典） | 判定 |
| --- | --- | --- | --- |
| R21 | 引数ポインタの置き場所 | `name` へのポインタは `_ax = $F8:20F8` の 2 バイト（S1 L329、S3 L115 の `name<_ax>`） | CONFIRMED-by-source |
| R22 | 他の疑似レジスタ | `_bx=$F8:20FA`、`_cx=$F8:20FC`、`_dh` を含む `_dx=$F8:20FE`（S1 L333–341）。DELETE マクロは設定しない（R06） | CONFIRMED-by-source（配置と未使用の事実） |
| R23 | BIOS が work RAM を使うか・保存するか | 出典なし。`parameter_buf=$F8:2260`（*UNDOCUMENTED*、S1 L395）が BIOS の退避域かは不明 | UNKNOWN |
| R24 | 呼出側の破壊範囲 | マクロ終了時に Y=0（`cly`）、`__bm_error` 1 バイト上書き。`_ax`–`_dx` の保存はしない | CONFIRMED-by-source（呼出側の副作用のみ） |

### 3.4 バックアップメモリへのアクセス

| ID | 項目 | 事実（出典） | 判定 |
| --- | --- | --- | --- |
| R25 | 窓・施錠記号（呼出側の定義） | `$F7` 窓・`$1EE000` 対応、`IFU_BRAM_LOCK=$FF:1803`、`IFU_BRAM_UNLOCK=$FF:1807` の記号定義（S1 L102 付近の注記はプロジェクト注記、`pcengine.inc` L204–205、S5 の `bram_lock/unlock`） | CONFIRMED-by-source（記号の存在）。実窓の挙動は本書では未再確認 |
| R26 | 経路 A（BIOS `$E054`）の MMIO | 呼出側マクロ内に `$1807` 書込み・MPR4 切替えは無い（S2 L787–790）。BIOS 内部で解除・施錠・MPR4=`$F7` 切替えを行うかは不明 | UNKNOWN |
| R27 | 経路 B（HuC 直接実装）の MMIO | `lib2_bm_unlock` は `sei`・MPR4 保存→`$F7` 切替え→`csl`→`$48/$75/$80` 順の `bram_unlock` 書込み（S4 L777–789）。`lib2_bm_disable` は MPR4 復元・`bram_lock` 読取り・`csh`・`cli`（S4 L797–804） | CONFIRMED-by-source（経路 B の事実。**BIOS の動作ではない**） |
| R28 | 経路 B の MPR・割込み副作用 | MPR4 を 1 バイト `bm_mpr4` に保存（入れ子に弱いと読めるが追跡未了）、割込みは入状態を復元せず `cli` で許可にする | CONFIRMED-by-source（経路 B）。BIOS 経路への適用は禁止 |
| R29 | 実 BRAM の形式（ヘッダ・割当表・ディレクトリ） | S4 コメント L10–47（`HUBM` タグ、`$8000` 基準の end/next ポインタ、0x10 エントリヘッダ、0x02 終端）は**経路 B の実装が読む形式**であり、BIOS の形式の証拠ではない | HYPOTHESIS（経路 B の形式としてのみ。以降の合成 mock との混同禁止） |

### 3.5 削除対象の指定方法

| ID | 項目 | 事実（出典） | 判定 |
| --- | --- | --- | --- |
| R30 | 名前は 12 バイト | S2 L775–781 コメント：先頭 2 バイトは uniqueness 値（zeroes であるべき）、残り 10 バイトは ASCII 名の空白パディング | CONFIRMED-by-source（呼出側の約束） |
| R31 | BIOS の照合規則 | 12 バイト全体比較か、uniqueness を無視するか、大文字小文字・空白・NUL 終端の扱い、不正名の拒否の有無は不明 | UNKNOWN |
| R32 | バッファ・長さ・offset 引数の有無 | DELETE には無い（S2 L775、S3 L115）。`_bx/_cx/_dx` は使わない（R06、R22）。READ/WRITE の 4 引数（`buffer<_bx> name<_ax> offset<_dx> length<_cx>`、S2 L729/L752）とは異なる | CONFIRMED-by-source（引数体系の差異） |
| R33 | ポインタの MPR 可視性・12 バイト可読性の検証 | 出典なし | UNKNOWN |

### 3.6 成功・失敗結果／存在しない対象・保護対象・不正入力

| ID | 項目 | 事実（出典） | 判定 |
| --- | --- | --- | --- |
| R34 | 呼出側の 3 値 | コメントは `BM_OK / BM_NOT_FOUND / BM_NOT_FORMATTED`（S2 L783–785）。`__bm_error` も同 3 値（L785） | CONFIRMED-by-source（呼出側コメントの存在＝CALLER-EXPECTED）。呼出先の返値集合は未確定であり、確定前は BLOCKED_ABI（§5.2-4、§10-1。R38 の `2/3/4/5` は UNKNOWN のまま） |
| R35 | 存在しない対象 | 呼出側の期待（CALLER-EXPECTED）は `BM_NOT_FOUND=1`（R12）。S4 の `lib2_bm_open` は走査し尽くすと `.x1` で `#1`（S4 L736） | HYPOTHESIS（BIOS が 1 を返す根拠は呼出側コメントのみ。確定前は BLOCKED_ABI） |
| R36 | 未フォーマット | 呼出側の期待（CALLER-EXPECTED）は `$FF`（R13）。S4 の enable はタグ不一致で `#$FF`＋`sec`（S4 L768 `.x1`） | HYPOTHESIS（BIOS の `$FF` は未確認。確定前は BLOCKED_ABI） |
| R37 | 保護対象（書込み禁止・ロック中の扱い） | 「保護対象」の概念は取得ソースに無い。`BM_FILE_EMPTY/FULL/BAD_CHECKSUM/DIR_CORRUPTED` 定数はあるが DELETE コメントは挙げない（R14） | UNKNOWN |
| R38 | `BM_BAD_CHECKSUM=2`・`BM_DIR_CORRUPTED=3`・`BM_FILE_EMPTY=4`・`BM_FULL=5` を DELETE が返すか | 出典なし。S4 の open は `3`（破損、L743 `.x3`）・`4`（空ファイル、`size==16`、L746 `.x4`）を返しうるが、経路 A の BIOS が同値を返すかは不明 | UNKNOWN |
| R39 | 不正入力（NULL ポインタ、12 バイト未満、非 ASCII、NUL 混じり、uniqueness 非 zero、マップ外 MPR） | 出典なし | UNKNOWN |
| R40 | 空ファイル（size `==16`）の削除 | S4 の open は空ファイルを L746 `.x4`（`#4`）で拒否するが、BIOS 経路が同じかは不明。HuCC コメントは `FILE_EMPTY` に触れない | UNKNOWN |

## 4. BURAM 系の既存 API との依存関係

| API（slot/入口） | DELETE との関係（確認事実のみ） | 判定 |
| --- | --- | --- |
| BM_FORMAT（`$18/$E048`） | 呼出側 `_bm_format` は `bm_free` 非 0 時のみ FORMAT を呼ぶ（S2 L677–680）。DELETE に事前確認は無い（R06）。FORMAT の確認文字列 `!BM FORMAT!`（11 バイト）は DELETE に無い | CONFIRMED-by-source（経路の差異）。BIOS 間の依存は UNKNOWN |
| BM_FREE（`$19/$E04B`） | 呼出側は容量確認に使うが、DELETE マクロは `bm_free` を呼ばない。FREE の単位（user bytes コメント）・事後条件は呼出先未確認（S10 兄弟台帳） | 呼出側の使われ方のみ。依存の事実は UNKNOWN |
| BM_READ（`$1A/$E04E`） | 同一 12 バイト名規則を共有（S2 L729–739）。`_bm_exist` は READ（長さ 0）で存在確認する冗長経路（S2 L798–829） | CONFIRMED-by-source（名前規則の共有）。BIOS 内部の共有は UNKNOWN |
| BM_WRITE（`$1B/$E051`）・`bm_create`（WRITE 転送） | 同一 12 バイト名規則（S2 L752–766、L831–853）。`bm_create` は WRITE への転送であり DELETE とは逆方向 | CONFIRMED-by-source（名前規則の共有）。UPSERT/排他の有無は UNKNOWN |
| BM_FILES（`$1D/$E057`） | 入口定義はあるが S2 にラッパーが無い（R05）。列挙・終了条件は全面 UNKNOWN のまま | UNKNOWN |
| `bm_check` / `lib2_bm_enable` | S2 L609–660 の `_bm_check`（BRAM 存在確認、MPR3 退避つき。L615 `.proc`–L660 `.endp`）、S4 L750–771 の enable（タグ 4 バイト照合）。DELETE マクロは check/enable を呼ばない | CONFIRMED-by-source（DELETE に事前 check は無い）。BIOS 内部の有無は UNKNOWN |
| `__bm_error`（S2 L65） | 全 BURAM ラッパーが共有する 1 バイト。DELETE も上書きする（S2 L789） | CONFIRMED-by-source（呼出側の共有状態）。スレッド・再入の安全性は UNKNOWN |

重要: 経路 B（S4）の `lib2_bm_delete` は `lib2_bm_open` 成功を前提に
`memmove` 的圧縮（dest=curr、src=next、len=`end-next+2`、end ポインタを `-size`）を行う（S4 L479–503）。
これは **HuC ライブラリの直接実装の事実**であり、BIOS `$E054` が同じ圧縮をする証拠ではない。
断片化（fragmentation）の有無・圧縮の有無は §5 の UNKNOWN に残す。

## 5. 削除処理の副作用・データ保全・失敗時の状態

### 5.1 副作用（分かっていること・いないこと）

| ID | 項目 | 状態 |
| --- | --- | --- |
| E01 | 呼出側マクロ自体の副作用 | `__bm_error` 上書き＋Y=0 のみ（R24）。CONFIRMED-by-source |
| E02 | BIOS の消去範囲（何バイト消すか、後続エントリを詰めるか、終端ポインタを動かすか） | UNKNOWN。S4 の圧縮は経路 B の事実であり流用禁止 |
| E03 | 断片化の扱い（詰めない／詰める／フリーリスト化） | UNKNOWN。「ファイル削除と断片化を調べる」は Issue #38 の課題のまま |
| E04 | チェックサム・ヘッダの更新 | UNKNOWN（S4 の `lib2_bm_checksum` 関数 L806–840 は経路 B のもの） |
| E05 | MMIO（`$1803/$1807`、MPR4=`$F7`）の有無 | UNKNOWN（R26）。`docs/173` の結論どおり固定 Geargrafx は MMIO 書込みを副作用前に阻止できないため、実行検証は BLOCKED |
| E06 | 割込み・完了条件・所要時間 | UNKNOWN |
| E07 | `$20F8`–`$20FF`・`$2260` 等の work RAM への書込み | UNKNOWN（R23） |
| E08 | 削除後の空き容量の変化（FREE との整合） | UNKNOWN（FREE 自体が BLOCKED_ABI） |

### 5.2 データ保全の不変条件（真正実装に要求する条件。現状は検証不能）

真正削除が将来検証される場合、最低限以下を満たさなければならない。
いずれも現状は **要求条件であり観測事実ではない**。

1. 成功時は指定 1 件のみが消え、他のエントリの名前・データ・順序が保たれる（順序保持自体も BIOS 側未確認のため、検証時に確定する）。
2. 失敗時は BRAM 全体がバイト同一である（失敗時非変更＝atomicity）。失敗の名称（呼出側期待ラベルの NOT_FOUND / NOT_FORMATTED、または将来確定するその他の値）は CALLER-EXPECTED であり、真正の返値集合の確定ではない（R34–R38、§10-1）。
3. 終端・空き管理の更新は成功時のみ。失敗時は終端ポインタ・チェックサム域を含め一切変わらない。
4. 呼出し元への返値集合は、独立証拠により確定するまでは UNKNOWN とし、3 値への限定を要求しない。確定前は `BLOCKED_ABI` を維持し、未知の真正応答を拒否条件にしない（R38 の `2/3/4/5` は UNKNOWN のまま）。
5. 名前ポインタの 12 バイト外・BRAM 域外への読み書きをしない。MPR・work RAM の破壊は文書化された範囲に限る。

### 5.3 失敗時の安全性（fail-closed の要否）

- DELETE の呼出側には FORMAT のような二重防御（`bm_free` 事前判定＋確認文字列）が無い。
  12 バイト名 1 本で直接 JSR する経路であり、誤認・範囲外・部分更新の危険が高い。
  よって真正契約が確定するまで `$E054` への JSR は BLOCKED を維持する（§10）。
- 存在しない対象・未フォーマット・破損・空ファイル・不正入力のいずれも（名称は CALLER-EXPECTED のラベルであり真正集合の確定ではない）、
  失敗時に BRAM を変えないことを合成 mock で先に定義し（§9）、真正側は UNKNOWN のままにする。
- 異常終了（電源断・リセット・IRQ 割込み中の再入）は取得ソースに記述が無く、全面 UNKNOWN。
  再入・二重削除・削除中読取りの安全性は未確定とする。

## 6. 不整合・注意点（記録のみ。挙動の根拠にしない）

- I-1: `_bm_read` のコメントは「Returns either BM_OK, BM_NOT_FOUND, BM_BAD_CHECKSUM or BM_NOT_FORMATTED」
  （S2 L737）と書く一方、「After return `__bm_error` = BM_OK or BM_NOT_FORMATTED」（L739）と書く。
  `NOT_FOUND`・`BAD_CHECKSUM` が `__bm_error` に残るかはコメントだけでは決まらない。
  DELETE の L783–785 は 3 値で一致しているが、兄弟の不一致があるためコメントの鵜呑みは禁止。
  3 値の一致は CALLER-EXPECTED としての一致であり、真正の返値集合の確定ではない（§5.2-4、§10-1）。
- I-2: `BM_NOT_FORMATED` は綴り誤り（S2 L60、S3 L37。「HuC incorrect spelling」注記あり）。
  値は `$FF` で `BM_NOT_FORMATTED` と同値。
- I-3: S4 の open は空ファイル（size `==16`）を `#4`（`BM_FILE_EMPTY`）で拒否するが（S4 `.x4`）、
  HuCC の DELETE コメントは `FILE_EMPTY` に触れない。空ファイルの削除可否は UNKNOWN。
- I-4: 同名 `_bm_format` が HuCC（BIOS 経由）と HuC（直接実装）に二重定義される問題は
  FORMAT 台帳の既知論点（K-10）。DELETE については HuC 側 `_bm_delete` が `maplibfunc` 転送のみで
  同名衝突は確認されていないが、リンク選択条件の一般論として UNKNOWN に残す。
- I-5: S7（Hugo）の `BM_FREE` HLE は `WRAM[4..7]-0x12` の算術であり（S7 L982–998）、
  実 BRAM の読取りではない。HLE 算術を BIOS の容量・削除効果の根拠にしない。
- I-6: S6 の世代別本体番地の差異は、DELETE の挙動が世代で同一という仮定を禁じる。
  `$4A` のような名称差異は DELETE には無いが、本体差異はある。

## 7. 仕様根拠の集計

固定公開資料で直接確認できた事実（R/E ID）と、呼出先についての HYPOTHESIS / UNKNOWN の対応（§3–§5 の ID 基準）。

| 区分 | 件数 | 内訳 |
| --- | --- | --- |
| CONFIRMED-by-source | 22 | R01、R03、R04、R05、R06、R08、R10、R12、R13、R15、R17、R19、R21、R22、R24、R25、R27、R28、R30、R32、R34（いずれも固定公開資料で直接確認できた事実。真正 callee の証拠ではない。R12–R14・R34 の 3 値は CALLER-EXPECTED としての確認である）、E01 |
| HYPOTHESIS | 7 | R02、R11、R14、R16、R29、R35、R36（R11・R14・R35・R36 は CALLER-EXPECTED としての推定。確定前は BLOCKED_ABI） |
| UNKNOWN | 19 | R07、R09、R18、R20、R23、R26、R31、R33、R37、R38、R39、R40、E02、E03、E04、E05、E06、E07、E08 |

CONFIRMED-by-source 22 件の情報源の内訳（各 ID の出典種別。§0 のタグ定義による）:

| 種別タグ | 意味 | 該当 ID |
| --- | --- | --- |
| CALLER | HuCC 呼出側の宣言・コメント・マクロ・記号定義（S1/S2/S3/S5）。S1・S5 は同一上流由来であり一致は記号の再掲 | R01、R05、R06、R08、R10、R12、R13、R15、R17、R19、R21、R22、R24、R25、R30、R32、R34、E01（18 件） |
| DIRECTLIB | HuC の BIOS 非経由の直接実装（S4。経路 B の事実であり経路 A の証拠ではない） | R27、R28（2 件） |
| HLE | Hugo HLE の実装・不在の事実（S7。実 BIOS の証明にならない） | R04（1 件） |
| SECTABLE | Stum 公開ジャンプ表の記載（S6。名前・入口対応のみ） | R03（1 件） |

注記: R17 は S2 の機構（`php/plp`・スタック退避なし）が主であり、S6 の JSR 列の趣旨を借用している。
件数は CALLER に計上する。§4 の表の CONFIRMED 行（FORMAT・READ・WRITE・check・`__bm_error` の 5 行）は
CALLER 由来だが R/E 番号を持たないため、本集計の対象外である。

厳密な行数よりも分類の原則を優先する。同じ話題が呼出側と呼出先に分かれる場合は別行に数える。
VERIFIED（真正 BIOS の確認）は 0 件。`release_eligible=false`。

## 8. G5 到達の扱い（真正契約と SIMULATED の分離）

- `$1C` 到達の観測（S9）は、実験的 `$4A` 復帰（preservation-only）または SIMULATED `$4A` 復帰の
  条件付きである。真正 `$4A` の ABI は UNKNOWN のままなので、`$1C` への JSR が起きたことは
  真正 `$1C` 契約の証拠にならない。到達構造（番号列・停止分類・cold/warm 一致・MPR1=`$F8`）のみ OBSERVED とし、
  入出力・復帰値・メモリ内容は UNKNOWN のままにする。
- `$21`（EX_JOYSNS）の復帰後に `$1C` が呼ばれたことは、ゲームが入力取得後に削除を求めたという
  因果の証明ではない。番号の前後関係のみを記録し、意味づけはしない。
- G5 タイトル到達の判定は `G5_OBSERVED=false` のまま。本書は互換性の主張を一切しない。

## 9. 合成テスト設計（自作 mock BRAM のみ。実 BRAM・実 BIOS・MMIO 不使用）

### 9.1 設計原則（安全境界）

1. mock はホスト上の in-memory 抽象モデル（エントリの順序リスト＋終端ポインタ等の PROJECT-INVENTED 管理域）に限る。
   実 BRAM 形式（S4 コメントの `HUBM` 等）を System Card の形式として主張しない。
   バイトレイアウトを仮定する場合は `PROJECT-INVENTED` と明記し、BIOS の根拠にしない。
2. `$E054` への JSR、実 BRAM 窓（`$F7`）のマップ、MMIO（`$1803/$1807`）の操作、
   `sei/cli`・MPR 切替えを伴う実行は一切行わない（NOT_RUN）。固定 Geargrafx でも実行しない。
   理由は `docs/173` の阻止不能結論（E05）と削除の破壊性（§5.3）である。
3. 判定器は fail-closed とする。`MockStatus`・`HarnessOutcome` の域外値・型不一致・容量外・破損は FAIL とし、
   全 mock ケース PASS でも総合判定は `BLOCKED_ABI`（真正 ABI の PASS ではない）。
4. mock の削除意味論は S4（経路 B）の圧縮を**参考**にするが、BIOS の仕様として採用しない。
   mock の選択（圧縮する）は PROJECT-INVENTED であり、真正の断片化挙動は UNKNOWN のまま残す。

### 9.2 mock 定義（PROJECT-INVENTED。BIOS の値ではない）

- 用語と返値域の分離:
  - `MockStatus`: `mock_delete` の返値域。`{OK, NOT_FOUND, NOT_FORMATTED}`。
    S2 L783–785 の呼出側 3 値に対応する CALLER-EXPECTED 由来の PROJECT-INVENTED 選択であり、
    真正 BIOS の返値集合の主張ではない（§5.2-4、§10-1）。
  - `HarnessOutcome`: 判定層（入力検証・監査）の結果域。`{REFUSED, CORRUPTED}`。
    PROJECT-INVENTED の入力拒否・検証失敗であり、`MockStatus` とは別の型・別の層である。
  - 判定フロー `judge(image, name)`（`CORRUPTED` が `REFUSED` に優先する）:
    0. `structure_gate(image)`: スナップショット不能（非 dict・キー欠落・`entries` 非 list・
       非 dict 件・`name`/`data` 非 bytes 系）なら `CORRUPTED`＋空画像規約。例外を投げない。
    1. `audit_image(image)`: 下記 CLEAN 不変条件を満たさなければ `CORRUPTED`＋入力同一スナップショット。
       `mock_delete` は呼ばない。画像は不変（N5）。`formatted == false` は監査対象外であり、
       `mock_delete` が `(NOT_FORMATTED, 同一画像)` を返す正常系（N2）と区別する。
       画像も名前も不正な二重不良は `CORRUPTED` とする（`REFUSED` ではない）。
    2. `validate_input(name)`: `name` が `bytes` かつ `len == 12` でなければ `REFUSED`。
    3. `mock_delete(clean_image, name)`: `formatted == false` なら `(NOT_FORMATTED, 同一画像)`（N2）。
       名前完全一致の entry が無ければ `(NOT_FOUND, 同一画像)`（N1、N3、N7 の 2 回目）。
       あれば当該 1 件を除去し後続を詰めて `(OK, 新画像)`（P1–P4、N6、N7 の 1 回目）。
- `MockBram`: `{entries: list[{name: bytes(12), data: bytes}], formatted: bool, capacity: int, end: int}`。
  `end` は PROJECT-INVENTED の終端ポインタ（使用量）。`entries` の順序が BRAM 上の並びに対応する。
  容量上限の例 2048 バイトは、S1 付近のプロジェクト注記 2 KiB との一致は偶然であり根拠ではない。
- CLEAN 不変条件（`audit_image` が検査する PROJECT-INVENTED 条件）:
  - `formatted` は `bool`、`capacity` は正の `int`、`end` は `0 <= end <= capacity` の `int`。
  - `end == mock_used(entries)`。ただし `mock_used = Σ(MOCK_ENTRY_OVERHEAD + len(data))`、
    `MOCK_ENTRY_OVERHEAD = 18`（S4 の 0x10 ヘッダ＋0x02 終端の算術を借りた mock 内定数。BIOS の値ではない）。
  - 全 entry の `name` は `bytes` かつ 12 バイト、`data` は `bytes`。
  - `name` の重複が無い。
  - N5 の破損は次の亜種で表現する（いずれも上記条件で表現可能）: N5a 終端超過（`end > capacity`）、
    N5b 管理不一致（`end != mock_used`）、N5c 不正名長（entry 名 ≠ 12B）、N5d 重複名。
    バイトレイアウト固有の破損（物理的重なり・0 サイズヘッダ等）は本抽象モデルでは表現しない。
    表現外の破損を論じる場合はモデル拡張が必要であり、現行 mock の対象外と明記する。
- 照合規則（mock 内）: `name` の 12 バイト約束（S2 L775–781 の借用）の下でバイト完全一致。
  先頭 2 バイトの zero 推奨は拒否条件にしない（B2）。真正の照合規則の主張ではない。
- `mock_free(image) = capacity - end`（P6・B4 用の mock 内算術。BIOS の FREE 値ではない）。
- `mock_add(image, name, data)`（P5・B4 用の準備操作。PROJECT-INVENTED。容量超過・重複名の場合は
  テスト自体を FAIL とし、BIOS 返値の主張にはしない）。
- 失敗時の mock 不変条件: `REFUSED`・`CORRUPTED`・`NOT_FOUND`・`NOT_FORMATTED` のいずれでも
  返却画像は入力とバイト同一。判定器は同一性を検査する。

### 9.3 正例（PASS の条件。mock のみ）

| # | 入力 | 期待 | 保全条件 |
| --- | --- | --- | --- |
| P1 | 1 件のみの画像から当該名を削除 | `OK`、空リスト | 他域なし。空きが初期値に戻る |
| P2 | 先頭件の削除（後続 2 件あり） | `OK`、後続 2 件が順序保持で詰まる | 生存者の名前・データ・順序が不変 |
| P3 | 中間件の削除 | `OK`、前後が詰まる | 前件・後件の内容不変 |
| P4 | 末尾件の削除 | `OK`、前件群不変 | 終端のみ短縮 |
| P5 | 削除後に同名を再作成（`mock_add` による PROJECT-INVENTED 追加） | `OK` 後に追加可 | 再利用で旧データが残らない |
| P6 | 削除後に `mock_free` の値が増加 | 空きが削除分だけ増加 | 増加量＝除去件の mock 占有量（`mock_free`・`mock_used` の mock 内算術） |

### 9.4 負例（fail-closed。画像不変が必須）

| # | 入力 | 期待 | 安全性 |
| --- | --- | --- | --- |
| N1 | 存在しない名 | `NOT_FOUND`、画像同一 | 生存者・終端・管理域の全同一 |
| N2 | 未フォーマット画像（`formatted=false`） | `NOT_FORMATTED`、画像同一 | 何も書かない |
| N3 | 空画像（formatted だが 0 件）への削除 | `NOT_FOUND`、画像同一 | 空であることを壊さない |
| N4 | 名が 12 バイトでない・非 bytes・空 | 入力検証層で `REFUSED`（`mock_delete` は呼ばない）、画像同一 | `HarnessOutcome` 層の拒否であり、BIOS の返値の主張ではない |
| N5 | 破損 mock（N5a 終端超過・N5b 管理不一致・N5c 不正名長・N5d 重複名） | 監査層で `CORRUPTED`（`mock_delete` は呼ばない）、画像同一 | 破損を「存在しない」と誤認しない。表現外の破損は対象外と明記（§9.2） |
| N6 | 空ファイル件（`data` 長 0）の削除 | mock 定義では `OK`（除去）とするが、真正の `FILE_EMPTY` 可否は UNKNOWN のまま記録 | mock の選択を BIOS に投影しない。負例表に置く理由は、真正の空ファイル削除を `OK` と主張しない点を固定するため（下記「分類注意」） |
| N7 | 同一対象の二重削除 | 1 回目 `OK`、2 回目 `NOT_FOUND`、2 回目で画像同一 | 再入の安全性の主張ではなく逐次 mock の性質 |

分類注意: N6 は mock 意味論の固定例（`data` 長 0 の件も除去する）であり、真正の空ファイル削除可否は
UNKNOWN のままである。負例表に置くのは、真正の `FILE_EMPTY` 判定を `OK` と誤読させないためである。
同様に N7 の 1 回目は mock 逐次性の固定であり、再入・割込み中の安全性の主張ではない。

### 9.5 境界条件

| # | 境界 | 期待（mock） | 注意 |
| --- | --- | --- | --- |
| B1 | 名ちょうど 12 バイト／11・13 バイト | 12 のみ `validate_input` を通過、他は `REFUSED`（`mock_delete` 不呼出） | 受理長は呼出側約束（R30）の借用 |
| B2 | 先頭 2 バイト `00 00`／非 zero | mock は両方受理し完全一致で照合。「zero であるべき」は推奨であり拒否条件にしない（S2 L775–781 の文言どおり） | BIOS の検証有無は UNKNOWN |
| B3 | 10 バイト名の空白パディング／NUL 混じり | mock はバイト一致のみ。NUL を特別扱いしない | 真正の扱いは UNKNOWN |
| B4 | 満杯画像（`end == capacity`）の末尾削除→`mock_add` による追加 | 削除で空きが生じ、追加が受理される | 容量値は mock 定義内（`capacity`・`end`・`mock_free`） |
| B5 | 最大件数・最大データ長の削除 | `OK` で全生存者の内容不変 | 性能・時間の主張はしない |
| B6 | 大文字小文字・空白の有無の類似名（`FOO` vs `FOO ` vs `foo`） | 別名として区別（完全一致） | 真正の照合規則は UNKNOWN のため、類似名の等価性を主張しない |
| B7 | 呼出し元バッファの複製・判定器の非変異 | 判定器 `judge` は入力画像・名前バッファを書き換えない（`bytearray` 渡しを含む）。I/O を行わない | BM_FREE 合成（S10）の同型検査を踏襲 |

### 9.6 判定器の fail-closed 規則（mock 専用）

- 返値域の分離: `mock_delete` の返値は `MockStatus {OK, NOT_FOUND, NOT_FORMATTED}` のみ
  （CALLER-EXPECTED 由来の PROJECT-INVENTED）。入力検証の拒否 `REFUSED` と監査の失敗 `CORRUPTED` は
  `HarnessOutcome` 層の結果であり、`MockStatus` には含めない。
- FAIL 条件: `MockStatus`・`HarnessOutcome` の両域外の値、型不一致、結果欠落、モデル例外、
  入力画像の変異、成功時の生存者不一致（順序・内容）、失敗時（`NOT_FOUND`・`NOT_FORMATTED`・
  `REFUSED`・`CORRUPTED`）の非同一、CLEAN 違反画像に対する `mock_delete` 呼出し。
- 全 mock ケース PASS でも総合判定は `BLOCKED_ABI`、`release_eligible=false`。
  参照 ABI VERIFIED への昇格は行わない。mock の PASS は「mock 定義の自己無矛盾」のみを証明し、
  BIOS 互換性の PASS は 1 件もない。

## 10. 次の実装に必要な検証（解除条件。現状は全て未満た）

真正 `$E054` の実装・実行には、以下のすべてが人間の承認付きで揃う必要がある。
現状はいずれも未取得のため、本書は `BLOCKED_ABI` を維持する。

1. 呼出先の第 2 独立根拠: 呼出側ラッパーではない System Card 側の公開仕様・資料による
   R07・R09・R11・R13・R23・R26・R31・R37–R40・E02–E08 の確定。特に真正の返値集合は独立証拠により
   確定するまで UNKNOWN とし、呼出側 3 値（CALLER-EXPECTED）を真正の受入条件にしない。
   未確定の間は `BLOCKED_ABI` を維持する（§5.2-4）。
2. JSR 安全ゲートの人間判断: `$E054` 実行の許可記録（現状は `spec/reference_jsr_gate.json` に項目なし＝許可なし）。
3. MMIO 事前阻止の成立: `docs/173` の阻止不能結論を覆すフック、または実行を伴わない検証計画の承認。
4. 破壊範囲の期待値: E02–E04 の確定なくして削除の正誤判定は定義できない。mock 値を流用しない。
5. 世代差の確定: S6 の本体差異に対する世代別挙動の確認（3.0 対象の単一化の可否）。
6. 台帳更新の別途提案: `spec/api_contracts.json` slot 28・`spec/dev_implementations.json` id 28 の
   変更は本書の範囲外（ledger 変更禁止）。別途 owner 承認の提案とする。
7. G5 再観測の分離: 真正 `$1C` 実装後の再観測は、実験的 `$4A`・`$21` の条件と切り分けて記録する。
   SIMULATED_NEXT_API を真正進展として計上しない。

## 11. 検証記録

- ハッシュ照合: S1–S3 の SHA-256 3 件は `spec/api_contracts.json` の記録値と一致（§2 表に完全値の先頭・末尾を記載）。
  S4（`95d1d5f4…0a515`）・S5・S7（`db3f227e…210972f22`）は本調査で取得・計算した値である。
- 行番号照合: S1 L329/L333/L337/L341/L395/L494–499/L204–205、S2 L54–61/L65/L677–680/L775–790、
  S3 L31–38/L115、S4 L10–47/L326/L350–353/L479–503/L689–748/L750–771/L773–791/L793–804、S5 L91–96・`equ.inc` L236–237、
  S7 L65–70/L212–213/L982–998/L1091–1095 は取得ファイルで `grep -n`／`Read` により確認した。
- Stum 行 `bm_delete $E054` および世代別本体番地は本調査の取得内容（/tmp 取得、事実引用のみ）で確認。
  Zeograd は 403 のため NOT_RUN（未確認）として記録し、判定に使わない。
- リポジトリ内参照: `docs/api-inventory.md` L53、`spec/api_slots.json` id 28、
  `spec/api_contracts.json` slot 28、`spec/dev_implementations.json` id 28、
  `tools/reference_bios_scenarios.py` L23 の存在を確認。本書ではいずれも変更しない。
- 実コア・実機の実行: **NOT_RUN**。`$E054` の JSR、実 BRAM の読み書き、実セーブの操作、
  Geargrafx での `$1C` 実行は一切行っていない。G5 構造観測（S9）は既存記録の引用であり、
  本書のための再測定ではない。
- ホストテスト: 本書は文書のみの追加である。mock（§9）のコード・テストは本 PR に含めない。
  将来 mock を追加する場合は in-memory・I/O 禁止・import 許可リストの同型検査を付けること。

## 12. ライセンス・ provenance

- HuC/HuCC 由来の引用は行番号・識別子・ハッシュに限り、コードを複製していない。
  S1–S3 は BSL-1.0（PENDING、REFERENCE_ONLY）。S4・S5 はファイル内ライセンス記載なし（UNKNOWN、REFERENCE_ONLY）。
  S6 は CC BY-NC-SA 4.0 表示の二次資料（事実引用のみ・本文非転載）。S7 は GPL-2.0（REJECTED＝ROM 組込み不可、REFERENCE_ONLY）。
- APPROVED 判定は一切含まない。全行 PENDING または UNKNOWN のまま。
- 純正 BIOS・商用ディスク・参照 BIOS のバイト・逆アセンブル・資産を転記していない。
  `./rom/` の有無の確認・列挙・読取りも行っていない。
- 実 save・実 BRAM・実デバイスへの書込みは行っていない。BM_DELETE の実行はしていない。

## 13. 最終判定

- **判定: `BLOCKED_ABI`。** 真正の削除動作（呼出規約・照合規則・消去範囲・断片化・返値・安全性）に
  根拠が無いため、削除動作の実装は禁止する。`spec` 台帳・ジャンプテーブル・ビルド設定は変更しない。
- ABI 確定範囲: 固定公開資料で直接確認できた事実のみ（CONFIRMED-by-source 22 件。内訳は §7 のとおり
  CALLER 18・DIRECTLIB 2・HLE 1・SECTABLE 1 であり、いずれも真正 callee の証拠ではない）。
  呼出先の事実は HYPOTHESIS 7 件・UNKNOWN 19 件相当（§7）であり、削除の正誤を定義できない。
- G5 の `$1C` 到達は実験的 `$4A` 復帰の条件付き構造観測にとどまり、真正契約の証拠ではない。
  G5 の次の阻害点として `$1C` を記録することはできるが、実装開始の許可にはならない。
- 次の実装開始可能性: **開始不可**。§10 の 7 条件が揃うまで、mock 設計（§9）の範囲を超えない。
  根拠のない削除動作を実装しないこと。
