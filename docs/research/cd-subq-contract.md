# CD_SUBQ（repo 棚卸しの候補スロット `$0A`、入口候補 `$E01E`）呼出側 ABI・呼出契約 根拠台帳（Issue #259）

**状態: callee の ABI は未確定。ROM 実装なし。** `release_eligible=false`、callee 状態は `BLOCKED_ABI`、
`DEV_CALLABLE` なし、`VERIFIED` / `PASS`（参照 BIOS の振る舞い）なし。親 Issue #19。
verification は NOT_RUN（callee を実行していない）。

本台帳は、公開ソースから読める呼出側（caller）の事実と、呼出先（callee）の仮説・未確定を分けて記録する。
名前（`cd_subq`）や入口番地だけで ABI を確定しない。不明な欄は `UNKNOWN` のまま残し、値を補わない。

## 1. 規則

状態の意味:

- `SOURCE_FACT`: 固定版ソースの本文に書かれた**呼出側**の事実（宣言、呼出し経路、検索結果）。呼出先の振る舞いではない。
- `HYPOTHESIS`: 第三者の公開記事などの記述。検証していない。根拠なしに昇格しない。
- `UNKNOWN`: 出典が不足している。値の欄は `UNKNOWN` とする。
- `BLOCKED`: 測定には実行可能な呼出先が必要で、それがないため測定できない（本件では全 callee 項目がこれに当たる）。

禁止事項:

- `VERIFIED` と `OBSERVED` は使わない。callee を実行していないため、どの行にも付けられない。
- 呼出側のソースから callee の入出力を導かない。`call_bios` の経路が正しくても、`cd_subq` の入出力は分からない。
- 参照 BIOS、市販 ROM、逆アセンブリ、BIOS バイトを使わない。公開記事の記述は要約のみで、本文を転載しない。

## 2. 出典

HuCC（`pce-devel/huc`）の固定版を使う。

- 固定版: commit `54f5c73606c1e7142e8095c727cab2767e2b3cb3`
- 取得元: `https://codeload.github.com/pce-devel/huc/tar.gz/54f5c73606c1e7142e8095c727cab2767e2b3cb3`
- アーカイブ SHA-256: `9fe91254051381077bd4ea217b718ec033a52da185283ae9479c1abac365311b`（取得して一致を確認。展開物は読むだけで実行していない）
- 再確認: 展開して次を実行する（展開物は実行しない）。

  ```sh
  grep -rIn -E 'E01E|cd_subq|CDROM_READSUBQ' include examples
  ```

| ID | 出典 | 行 | ライセンス | 用途・状態 |
| --- | --- | --- | --- | --- |
| S1 | `include/hucc/pcengine.inc` | 478（`cd_subq = $00:E01E, FUNC`）、330–343（`_al`〜`_dh`） | BSL-1.0（ファイル冒頭 8–10 行。著作権者表示なし） | 呼出側の宣言。SOURCE_FACT |
| S2 | `include/hucc/core-kernel.asm` | 500–516（`call_bios`） | BSL-1.0（Copyright John Brandwood 2021-2024） | 呼出し経路（MPR7 の退避と復元）。SOURCE_FACT |
| S3 | `include/hucc/core.inc` | 41–45（`system` マクロ: `ldy #<\1`、`jsr call_bios`） | BSL-1.0（Copyright John Brandwood 2021-2024） | マクロ展開。SOURCE_FACT |
| S5 | `examples/asm/elmer/include/cdrom.asm` | 140（`CDROM_READSUBQ = $DD`）、2010（SCSI 応答の `Track SubQ Flags` コメント） | BSL-1.0（Modifications copyright John Brandwood 2022）。ただし冒頭に「Hudson の Legend of Xanadu 2 由来のコードを基にする」とあり、元コードの権利状態は未確認 | SCSI 層の定数定義のみ。`$DD` を使う箇所は固定版に無い（SOURCE_FACT の根拠にしない、disposition PENDING） |
| X1 | `include/huc/system.inc` | 75（`cd_subq = $E01E`） | 表示なし（冒頭はコメントのみ） | 除外（ライセンス不明。`include/huc/` は #11 / #214 と同じく根拠にしない） |
| X2 | `examples/asm/elmer/kickc/bios.h` | 36 | 冒頭に Boost 1.0 の表示 | 名前の宣言のみ。除外（#214 と同じ扱い） |
| X3 | Hu-Go `bios.c`（mckayemu/hugo @64ed226d、GPL-2） | 未読 | GPL-2 | `CD_SUBA` の名称は `docs/abi-contracts.md` の記録を引用するのみ。HLE であり ABI の証拠にしない。コードは読まず転載しない |
| Z1 | Zeograd System Card notes（https://www.zeograd.com/download/pce_bios.html） | — | 表示なし（ページにライセンスの記載なし） | 第三者の要約。取得は要約ツール経由で、本文の逐語照合はしていない。HYPOTHESIS のみ |
| Z2 | Stum 2025（https://www.stum.de/2025/pcenginebiosoffsets/） | — | 記事 CC BY-NC-SA 4.0、コード MIT（ページ記載）。NC 条件があるため本文を転載しない | 番地と名称の対応のみ。SUBQ の入出力は記載なし。HYPOTHESIS（番地の対応）のみ |

固定版全体で `E01E`、`cd_subq`、`CDROM_READSUBQ` を検索した結果は次のとおり。
`cd_subq` の呼出し（`system cd_subq`、`jsr cd_subq` 等）は **0 件**。出現は宣言（S1、X1、X2）と、
SCSI 定数の定義（S5 の 140 行）だけである。SCSI の `$DD` を使う命令列も固定版に無い。

## 3. 項目台帳

| ID | 項目 | 状態 | 値 | 出典 | 理由・確度 |
| --- | --- | --- | --- | --- | --- |
| F1 | 名称（HuCC 宣言） | SOURCE_FACT | `cd_subq`（`$00:E01E`、FUNC） | S1 478 行 | 呼出側の宣言のみ。名前が ABI を意味するとは限らない。 |
| F2 | 固定版内の SUBQ 呼出し箇所 | HYPOTHESIS | 0 件（固定版の検索結果） | §2 の grep コマンド | 出典 1 行に対応しない否定結果のため SOURCE_FACT にしない。§2 のコマンドで再現して確かめる。SUBQ 固有の呼出側 ABI（入力の設定）は、この固定版からは得られない。 |
| F3 | 入口番地 | SOURCE_FACT | `$E01E`（宣言） | S1 478 行 | 宣言の値。`$E01E` が実際に System Card の入口であることは未確認（callee 未実行）。 |
| F4 | 関数番号 `$0A` と入口の対応（`$1E = $0A × 3`） | HYPOTHESIS | 関数番号 `$0A`、入口は関数番号 × 3 | Z1 | Z1 の番地計算規則による。Z1 の要約のみで検証していない。 |
| F5 | 呼出し経路（一般） | SOURCE_FACT | `ldy #<cd_subq` の後 `jsr call_bios`。`call_bios` は MPR7 を System Card 側に切替え、呼出し後に復元する | S2 500–516、S3 41–45 | SUBQ 固有の事実ではない。Y は下位バイトのみを渡す。呼出先が何を読むかは別。 |
| F6 | 入力レジスタ A / X / Y / P（SUBQ） | UNKNOWN | UNKNOWN | — | 呼出側が設定する例がない。Y は F5 の経路上の値であり、callee の入力とは別。 |
| F7 | 入力 BX（バッファのアドレス） | HYPOTHESIS | BX がバッファのアドレスを持つ（Z1 の要約、zeropage 関連と記載） | Z1 | 第三者記述のみ。HuCC の `_bx`（`$F8:20FA`）は呼出側の作業スロットであり、callee のレジスタとの対応は未確認。 |
| F8 | 作業スロット `$20F8`〜`$20FF` の入出力 | UNKNOWN | UNKNOWN | — | S1 の `_al` 等の別名は HuCC のライブラリ規約。BIOS の入力の証拠ではない。 |
| F9 | バッファ 0 バイト目 | HYPOTHESIS | 「一時停止中か」の真偽値（Z1 の要約） | Z1 | 第三者記述。検証なし。 |
| F10 | バッファ 1 バイト目以降の内容 | UNKNOWN | UNKNOWN | Z1（記述なし） | Z1 は記述していない。記述がないことは、無いことの証拠ではない。 |
| F11 | 戻りのレジスタとフラグ（C/Z/N/V） | UNKNOWN | UNKNOWN | — | 呼出側に戻り値の利用例がない。 |
| F12 | 呼出し後の MPR7（呼出側の期待） | SOURCE_FACT | `call_bios` が呼出し後に呼出側の MPR7 を復元する | S2 | 呼出側の経路の事実。callee 自身の MPR 使用ではない。 |
| F13 | callee の MPR・スタックの使用 | UNKNOWN | UNKNOWN | — | 実行していない。 |
| F14 | IRQ・割込みの扱い | UNKNOWN | UNKNOWN | — | 出典に記述がない。 |
| F15 | 副作用（RAM、VDC、CD ドライブ状態） | UNKNOWN | UNKNOWN | Z1、Z2（記述なし） | Z1 は「副作用なし」と読める要約だが、バッファ以外の記述がないだけで、無いことの証拠ではない。 |
| F16 | 失敗条件（無ディスク、範囲外等）と返却 | UNKNOWN | UNKNOWN | — | 出典になし。 |
| F17 | 別名 `CD_SUBA`（Hu-Go 側の表記） | HYPOTHESIS | `CD_SUBA` という名称（`docs/abi-contracts.md` の記録） | X3（記録の引用） | HLE の名称。入口の証拠ではない。X3 は読んでいない。名称の不一致は未解決（§4）。 |
| F18 | 第三者の名称 `CD_SUBQ`（Z1） | HYPOTHESIS | `CD_SUBQ` | Z1 | F1 と同じ名称。独立した証拠ではない。 |
| F19 | System Card 3.0 での `$E01E` の存在と名称 | UNKNOWN | UNKNOWN | S1、Z2 | S1 は改訂番号を示さない。Z2 は改訂ごとに番地が異なると述べるが、3.0 の対応を示していない。 |
| F20 | SCSI 層の `$DD`（READ SUBCODE-Q）と CD_SUBQ の関係 | UNKNOWN | UNKNOWN | S5 140 行 | 定数の定義のみで使用箇所がない。BIOS 関数との関係は出典から決められない。 |

## 4. System Card 3.0 固有の不一致（明示）

公開ソースから確認できた不一致は次の三点である。3.0 に固有かどうかは、いずれも確定していない。

1. **名称**: HuCC と Z1 は `CD_SUBQ`、Hu-Go（X3、記録の引用）は `CD_SUBA`。HLE の名称であり、どちらが正しいかは決められない（F1、F17、F18）。
2. **入力の有無**: Z1 は BX にバッファのアドレスを置くと記述するが、HuCC は SUBQ を呼ばず、Z2 は入出力を記載しない（F7、F2）。
3. **改訂**: HuCC の宣言は System Card の改訂を示さず、Z2 は番地が改訂ごとに異なると述べる。3.0 での存在と名称は未確認である（F19）。

これらは、後述の検証で決着させる対象である。どれも、本台帳では解決しない。

## 5. 残る UNKNOWN と決着の条件

| 項目 | 決着の条件 |
| --- | --- |
| F6、F8、F10、F11、F13〜F16 | 実行可能な System Card の CD_SUBQ を、作業 RAM・MPR・レジスタの全差分とともに測定する。ただし callee 実行は #3 / #12 系の所管と重複させない。 |
| F7、F9 | バッファのアドレスを変えた実測（BX の入力感度）で確かめる。 |
| F19、名称の不一致 | 3.0 のバイナリを利用者が所有し、そのローカル環境で測定する（リポジトリに含めない）。 |
| F20 | SCSI `$DD` の使用箇所を、SUBQ 測定と対応づける。固定版に使用箇所がないため、別の出典が必要である。 |

以上の測定はいずれも実行可能な callee を要する。現時点ではリポジトリに実行可能な callee がないため、
全 callee 項目は `BLOCKED_ABI` のままとする。

## 6. 次の作業

1. 実行可能な呼出先の確保（利用者所有の BIOS 経路、リポジトリには含めない）。
2. ライセンス判断（maintainer）: `examples/asm/elmer/include/cdrom.asm` の Hudson 由来部分、Hu-Go `bios.c`（GPL-2）、Z1 / Z2 の扱い。判断が出るまで、これらを SOURCE_FACT の根拠にしない（S5 は定義の有無のみに使う）。
3. 上記 §5 の測定を、呼出側 ABI の観測項目として `docs/cd-base-abi-ledger.md` §4a と同じ形式で定める。
