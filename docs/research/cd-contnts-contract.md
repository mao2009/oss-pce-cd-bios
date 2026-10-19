# CD_CONTNTS（スロット `$0C`、候補エントリ `$E024`）呼出側 ABI・呼出契約の根拠台帳（Issue #264）

**状態: 呼出先（System Card 3.0 の callee）の ABI は未確定。ROM 実装なし。**
親 Issue #21、子 Issue #264（Contract）。spec=HYPOTHESIS、provenance=PENDING、implementation=NOT_STARTED、verification=NOT_RUN。
**callee 側: BLOCKED_ABI。release_eligible=false。DEV_CALLABLE にしない。**

本台帳は、公開の呼出側ソースから見える事実と、呼出先の仮説・未確定を分けて記録する。
名前（`cd_contnts`）や呼出側のコメントから呼出先の ABI を導かない。未確定の欄には値を補わない。

## 1. 規則

状態の意味:

- `SOURCE_FACT`: 固定版の公開ソースに書かれた事実。**呼出側（caller）の記述**か、**名前定義などの参照事実**に限る。呼出先の振る舞いには付けない。
- `HYPOTHESIS`: 出典から推測した、または二次出典の要約にだけある内容。呼出先の実行・人手の原文照合で確認していない。
- `UNKNOWN`: 出典が不足している。値の欄は `UNKNOWN` と書く。
- `BLOCKED_ABI`: 呼出先の ABI が未確定で、実行による測定もできない（実行可能な callee がない）。

禁止事項:

- `VERIFIED` と `OBSERVED` は使わない。呼出先を実行していないため、どの行にも付けられない。
- `PASS` と `DEV_CALLABLE` は使わない。CD_CONTNTS は callable として実装されていない。
- `side` が `callee` の行は `HYPOTHESIS` / `UNKNOWN` / `BLOCKED_ABI` のみ。呼出側のソースから callee の事実を導かない。
- `UNKNOWN` の行は値を埋めない。`HYPOTHESIS` の行は、根拠の出典と「未確認」を明記する。
- 二次出典（Web の要約取得）の内容は、人手で原文を照合するまで `SOURCE_FACT` に昇格しない。

## 2. 出典

固定版（HuC、`pce-devel/huc`）:

- commit `54f5c73606c1e7142e8095c727cab2767e2b3cb3`
- 取得元: `https://codeload.github.com/pce-devel/huc/tar.gz/54f5c73606c1e7142e8095c727cab2767e2b3cb3`
- アーカイブ SHA-256: `9fe91254051381077bd4ea217b718ec033a52da185283ae9479c1abac365311b`（取得して一致を確認。展開物は読むだけで実行していない）
- 再確認（展開物は実行しない）:

  ```sh
  grep -rIn -E 'cd_contnts' include examples
  grep -rIn -E 'call_bios|system.*macro' include/hucc
  ```

| ID | 出典（固定版内のパス） | 行 | ライセンス表示 | 用途・注意 |
| --- | --- | --- | --- | --- |
| S1 | `include/hucc/pcengine.inc` | 330–343（`_al`〜`_dh` の定義）、480（`cd_contnts = $00:E024, FUNC`） | BSL-1.0（8 行。著作権者の表示なし） | 名前と入口の宣言 |
| S2 | `include/hucc/core-kernel.asm` | 500–516（`call_bios`） | BSL-1.0（8 行 "Copyright John Brandwood 2021-2024."） | 呼出し経路（MPR7 の退避・復元） |
| S3 | `include/hucc/core.inc` | 41–43（`system` マクロ: `ldy #<\1`、`jsr call_bios`） | BSL-1.0（8 行 "Copyright John Brandwood 2021-2024."） | マクロの展開 |
| S4 | `examples/asm/elmer/cd-core-scsitest/scsitest.asm` | 681–697（`cd_stat` の待機ループ）、699（`system cd_contnts`）、700–703（`cmp`、`bne`、TOC 取得の表示） | BSL-1.0（10 行 "Copyright John Brandwood 2024-2025."） | **唯一の呼出側の実例。** 自作の CD 試験プログラムであり、CD_CONTNTS の仕様ではない |
| S5 | Hu-Go! `bios.c`（`mckayemu/hugo` @ `64ed226d`、blob `a8c4efab…`） | 47（`CD_CONTENTS 0x0C` の定義）、178–179（名前文字列）、1091–1094（`default:` 未処理の分岐） | ファイル内にライセンス文はなし。リポジトリの `COPYING` は GPL v2。`bios.c` の SHA-256 は `db3f227e…` で、`docs/oss-audit-systemcard.md` の記録と一致 | **GPL-2、参照専用の HLE。BIOS の ABI の証拠ではない。コードの転載や翻訳はしない** |
| S6 | Zeograd System Card notes（`https://www.zeograd.com/download/pce_bios.html`） | 見出しのみ | 取得内容に著作権表示・ライセンス文なし（WebFetch の要約による。原文未確認） | **二次出典。** CD_CONTNTS の説明はない |
| S7 | Stum 2025 System Card method offsets（`https://www.stum.de/2025/pcenginebiosoffsets/`） | 表（JSR 列と版別列） | 要約では「ブログ本文は CC BY-NC-SA 4.0」とあるが、原文未照合。表の値は WebFetch の要約による | **二次出典。** 版ごとの所在の差異だけを記録する（数値は転載しない。§5 参照） |

除外する出典:

- `include/huc/cdrom.asm`: `cdr_contents` のコメントに「BIOS CD_CONTENTS」の語がある。ライセンス不明（`docs/provenance.md`、`docs/oss-audit-systemcard.md` の方針）のため、内容は根拠にしない。
- `include/huc/system.inc`（`cd_contnts = $E024` の宣言のみ）、`examples/asm/elmer/kickc/bios.h`（名前の宣言のみ、ライセンス未確認）: 根拠にしない。
- `examples/asm/elmer/cd-core-scsitest/scsitest.asm` の `test_number16`（cd_stat と cd_dinfo の IRQ2 試験、1390 行以降）: CD_CONTNTS を対象にしていないため、IRQ の根拠にしない。

## 3. 項目別台帳

| ID | side | 項目 | status | 値 | 出典 | 理由・確度 |
| --- | --- | --- | --- | --- | --- | --- |
| F01 | caller | Y（エントリの選択） | SOURCE_FACT | `$24`（JSR 先 `$E024`） | S3 41–43、S2 500–509、S1 480 | `system` マクロが `ldy #<cd_contnts` を出し、`call_bios` が Y を `jsr $E000` の下位バイトに書く。`$E000+Y=$E024`。 |
| F02 | caller | A（呼出し直前の値、この経路） | SOURCE_FACT | `0` | S4 688–697 | `cd_stat` の戻り値を `cmp #$00` で確認し `beq` で分岐した経路。この一経路の事実であり、呼出先の入力要件ではない。 |
| F03 | callee | A の入力要件 | UNKNOWN | UNKNOWN | — | 呼出先が A を読むか、どの値を要求するかは不明。 |
| F04 | callee | X の入力要件 | UNKNOWN | UNKNOWN | S4（X は設定されていない） | 呼出側も X を設定していない。要件は不明。 |
| F05 | callee | P（フラグ）の入力要件 | UNKNOWN | UNKNOWN | — | 呼出先が入力フラグを読むかは不明。 |
| F06 | callee | `$20F8`–`$20FF`（`_al`〜`_dh`）の入力 | UNKNOWN | UNKNOWN | S4（呼出側は設定しない）、S1 330–343（定義のみ） | 呼出側の書込みはない。呼出先の読取りは不明。S5 の `_al`/`_bx` の使用は CD_DINFO の HLE であり、$0C の根拠にしない。 |
| F07 | callee | `$20F8`–`$20FF` の出力 | UNKNOWN | UNKNOWN | — | 出力の有無・位置は不明。 |
| F08 | caller | 戻り A の分岐（呼出側の期待） | SOURCE_FACT | A≠0 で `cd_reset` へ戻る、A=0 で TOC 取得済みとして進む | S4 699–703 | 呼出側の分岐の記述。呼出先の戻り値の証拠ではない。 |
| F09 | callee | 戻り A の意味 | HYPOTHESIS | 0 を成功とみなす | S4 700–701（呼出側の期待から推測） | 呼出先の実行で未確認。失敗コードの値は F21 で UNKNOWN。 |
| F10 | callee | 戻り X・Y の意味 | UNKNOWN | UNKNOWN | — | 出力の有無は不明。 |
| F11 | caller | 戻りフラグ N/Z（wrapper による） | SOURCE_FACT | N/Z は復元された戻り A で決まる | S2 511–514（`pha`、`lda #`、`tam7`、`pla`） | 戻り時の `lda #` と `pla` が N/Z を上書きする。呼出先の N/Z の値とは無関係。 |
| F12 | callee | 戻りフラグ C/V/D/I | UNKNOWN | UNKNOWN | S2 500–516 | 呼出先が設定するかは不明。wrapper の命令（`lda`、`pla`、`pha`、`tam`、`tma`、`cla`）は C/V/D/I を変えないと読めるが（HYPOTHESIS）、命令仕様との照合は未実施。 |
| F13 | callee | 戻り機構（SP と復帰） | HYPOTHESIS | 呼出先は RTS で `call_bios` へ戻る | S2 500–516（`jsr` 直後に後処理がある構造） | wrapper の構造からの推測。呼出先の実行で未確認。呼出先が残すスタックの量は UNKNOWN。 |
| F14 | caller | MPR7（呼出し中の値） | SOURCE_FACT | 呼出し中 MPR7=`$00`、戻り時に呼出側の値へ復元 | S2 503–514（`tma7`、`sta`、`cla`、`tam7`、`lda`、`tam7`） | wrapper が MPR7 を退避し、bank 0 を見せてから復元する。 |
| F15 | callee | MPR0–6（呼出し中の値） | HYPOTHESIS | 版の前提として MPR0=`$FF`、MPR1=`$F8` を仮定する記述がある | S7（二次出典の要約） | 二次出典の要約であり、CD_CONTNTS 固有の根拠ではない。原文照合まで昇格しない。 |
| F16 | callee | 作業 RAM の副作用・出力バッファ | UNKNOWN | UNKNOWN | S4（TOC の読取りは行わない） | 結果を格納するバッファの位置・形式は不明。 |
| F17 | callee | IRQ とイベント順序 | UNKNOWN | UNKNOWN | S4 681–697（`wait_nvsync` は呼出側の待機）、S4 1390 行以降は他関数の試験 | 呼出し中の IRQ マスクと、IRQ2 の発生順序は不明。 |
| F18 | caller | 呼出し前の事前条件（ドライブ状態） | SOURCE_FACT | `cd_stat` で busy=0、ready=0 を確認してから呼ぶ | S4 681–697 | 呼出側の前提。CD_CONTNTS の要件であるとは言えない。 |
| F19 | callee | busy・タイムアウト・所要時間 | UNKNOWN | UNKNOWN | — | 呼出先の待機条件は不明。 |
| F20 | caller | 失敗時の呼出側の処理 | SOURCE_FACT | 非 0 で `cd_reset` に戻り、待機からやり直す | S4 699–701、681–697 | 呼出側の動作記述。 |
| F21 | callee | 失敗条件・エラー値（無ディスク、不正な入力、タイムアウト） | UNKNOWN | UNKNOWN | — | 出典がない。失敗時の A・フラグ・出力の有無は不明。 |
| F22 | name | 名称（綴り） | SOURCE_FACT | HuC `cd_contnts`、Hu-Go `CD_CONTENTS`、本リポジトリ `CD_CONTNTS` | S1 480、S4 699、S5 47・178–179、`docs/api-inventory.md` 37 行 | 綴りが二種類ある（`docs/abi-contracts.md` 54 行も記録）。綴りが同じ関数を指すかは未確認。 |
| F23 | callee | ルーチンの所在（版ごと） | HYPOTHESIS | 版ごとに値が異なる。3.0 の列は 2.x と同じ値で、1.0 および TG3.0 の列とは異なる | S7（二次出典の要約） | JSR の入口（`$E024`）は全列で同じ。ルーチンの位置は版で異なると読める。**数値は本台帳に転載しない**（§5）。 |
| F24 | reference | Hu-Go の `$0C` の処理 | SOURCE_FACT | 名前定義のみ。`default:` で未処理扱い（元のバイト列へ戻す） | S5 47、178–179、1091–1094 | 参照専用（GPL-2）。HLE の対象外であり、BIOS の振る舞いの根拠にしない。 |
| F25 | reference | Zeograd の CD_CONTNTS の説明 | UNKNOWN | UNKNOWN | S6 | 見出しのみで、入出力・失敗条件の説明はない。 |

## 4. System Card 3.0 固有の不一致（明示）

1. **綴りの不一致**: HuC は `cd_contnts`、Hu-Go は `CD_CONTENTS`、本リポジトリは `CD_CONTNTS`（F22）。同じ `$0C` を指すことは、入口 `$E024` の一致だけでは確認できない。
2. **版による所在の差**: 二次出典の表では、3.0 の列のルーチン所在が 2.x と同じで、1.0 および TG3.0 の列と異なる（F23）。3.0 の所在を 2.x や 1.0 から推定しない。3.0 の所在は、人手で原文を照合するまで HYPOTHESIS のままである。
3. **3.0 の振る舞いの証拠がない**: Zeograd に CD_CONTNTS の説明はない（F25）。Hu-Go は HLE の対象外（F24）。HuC の唯一の呼出側（S4）は自作の試験プログラムで、3.0 の仕様を述べるものではない。
4. **呼出側の期待と呼出先の仕様を分ける**: S4 の分岐（F08、F20）は呼出側の期待であり、3.0 の戻り値の仕様ではない。

## 5. 除外・未転載

- 二次出典（S7）の版別のルーチン所在の数値は、本台帳に転載しない。呼出先のコードを読む作業を誘発しないため、版間の差異の有無だけを記録した。
- ROM の内容、逆アセンブル、BIOS のバイト列は読んでいない。`./rom/` は参照していない。
- 純正 `syscard3.pce` は使わない。

## 6. 残る UNKNOWN と決着条件

| 項目 | 決着の方法 |
| --- | --- |
| F03–F07（入力）、F10（戻り X・Y）、F16（副作用）、F21（失敗条件） | 公開できる実行可能な callee でのみ測定できる。現時点では実行可能な callee がないため BLOCKED_ABI |
| F09（戻り A の意味） | 無ディスク、不正なトラック、busy のケースで実測する。成功値は定義しない |
| F12、F13（フラグ、スタック） | 呼出し前後の P・SP を全比較する |
| F17、F19（IRQ、busy） | 割込み禁止の有無を変えた試験、busy 中の呼出しの試験 |
| F22（綴り）、F23（所在） | 人手で版別の原文を照合する。ライセンスの扱いは maintainer が判断する |

## 7. 次の作業

1. 版別の原文照合（S7 の 1.0、2.x、3.0、TG3.0 の列）。人手で行い、照合結果をこの台帳に記録する。
2. 公開可能な callee の確保は、#1（ROM 配置）・#6（実コア試験基盤）・#8（来歴レビュー）と合わせて決める。
3. 合成 CD メディアの試験（#265 Fixture）は、本台帳の UNKNOWN の決着条件に従う。
