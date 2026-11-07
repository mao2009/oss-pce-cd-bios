# EX_GETFNT 公開資料に基づく呼出契約の調査（#707）

調査日: 2026-11-07。対象は API `EX_GETFNT`、slot `$20`
（32）、候補入口 `$E060`。親 Issue は [#42](https://github.com/mao2009/oss-pce-cd-bios/issues/42)。
後継実装は [#708](https://github.com/mao2009/oss-pce-cd-bios/issues/708)。

本書は **公開資料の照合のみ**。参照 BIOS を実行していない。
実行を要する観測はすべて `BLOCKED` とする。改変 System Card
の特許実装と純正 System Card 3.0 の契約を混同しない。

## 分類の定義

各項目の分類は次の 5 値のいずれか。判定は公開資料のみによる。

| 分類 | 意味 |
|---|---|
| `SOURCE_FACT` | ピン留めした一次資料（HuCC 呼出側定義など）が明示する呼出側事実。callee の挙動としては使わない。 |
| `CORROBORATED` | 独立な 2 出典以上が一致する（著者・系統が別）。 |
| `HYPOTHESIS` | 単一出典の記述、または出典からの推論。信頼度は `SOURCE_SUPPORTED` に留まる。 |
| `UNKNOWN` | 信頼すべき出典がない。 |
| `CONFLICT` | 出典相互で食い違う、または対立する読みがある。 |
| `BLOCKED` | 判定に参照 BIOS の実行や逆アセンブルが必要で、安全ゲート（#173）と方針による。判定不能として記録するため、本表では判定と併記する。 |

`SOURCE_FACT` / `HYPOTHESIS` は、筆者が調査した出典の述べたことであり、
純正 callee の実測ではない。本表の `HYPOTHESIS` を `OBSERVED` や
免責の根拠に昇格させない。`spec/api_contracts.json` の slot 32 行
（`implementation_status: NOT_STARTED`、`verification: NOT_RUN`）は
変更しない。

## 出典台帳（すべて PENDING）

全出典の provenance は `PENDING`。コピー・改変・ROM 組込みの
許可は本書では与えない。第三者資料の複製はしない。

| ID | 出典（固定版・URL・確認日） | 扱い |
|---|---|---|
| S1 | HuCC `include/hucc/pcengine.inc`、`pce-devel/huc`、`54f5c73606c1e7142e8095c727cab2767e2b3cb3`、行 503 `ex_getfnt = $00:E060, FUNC`。確認日 2026-11-07。URL `https://github.com/pce-devel/huc/blob/54f5c73606c1e7142e8095c727cab2767e2b3cb3/include/hucc/pcengine.inc` | 呼出側定義。`PENDING` |
| S2 | Hu-Go! `bios.c`、`mckayemu/hugo`、`64ed226da2288738781743eeef228c069e8973a4`、`#define EX_GETFNT 0x20` と名称関数。確認日 2026-11-07。URL `https://github.com/mckayemu/hugo/blob/64ed226da2288738781743eeef228c069e8973a4/bios.c` | 名称のみ。HLE 実装なし。`PENDING` |
| S3 | Zeograd `pce_bios.html`、`https://www.zeograd.com/download/pce_bios.html`。確認日 2026-11-07。`EX_GETFNT, Function #$20` の見出しがあるが `IN/OUT` の記述なし（空節） | 不記載の記録。`PENDING` |
| S4 | Stum `pcenginebiosoffsets`、2025-07-28、`https://stum.de/2025/pcenginebiosoffsets/`。確認日 2026-11-07。CC BY-NC-SA 4.0（ブログ） | 版別入口表。`PENDING` |
| S5 | `dshadoff/SYSCARD_Translatefont`、`a9a517e1a7f9f23d2c922bfc82d41e6b3a6d992f`、`README` 第 1–2 節と `xlateFont.asm`。確認日 2026-11-07。BSL-1.0。URL `https://github.com/dshadoff/SYSCARD_Translatefont` | 改変実装の作者説明と実装。`PENDING`。IPS パッチは複製しない |
| S6 | PC Engine FX フォーラム、topic 23355.0、`https://www.pcengine-fx.com/forums/index.php?topic=23355.0`。著者 dshadoff（S5 と同作者）。確認日 2026-11-07 | 利用者説明。S5 と独立ではない。`PENDING` |
| S7 | Geargrafx 固定版 `b49ae82a012eade566d72e9d61c9d95a5caa4da3`、`docs/system-card-rom.md` と `tools/dependencies.json` | 本書で実行しない。`PENDING` |

S5 と S6 は同作者（dshadoff）であり、独立出典として数えない。S4 は
著者の非公式表記であり、公式名を確定しない（S4 の 4 追加名は
非公式である旨が api-inventory に記録）。S3 は本契約について
何も述べないこと自体が記録である。

## 項目別判定（F01–F17）

判定列は §の分類表の 5 値。`S` は出典 ID。`callee` 行は
`SOURCE_FACT` にしない。

| 項目 | 項目名 | 判定 | 根拠（S） | 限制と読替え |
|---|---|---|---|---|
| F01 | System Card バージョン対応 | `HYPOTHESIS` | S4 は `ex_getfnt` の内部先を 1.0 `$F1A3`、2.0 `$F122`、2.1 `$F124`、3.0 `$F124`、TG `$F13D` と列挙。S5 は JPN 3.0 を `$F124`（`ORGTRGT`）、USA 3.0 を `$F13D`（`ORGTRGT`）とし、S4 の 3.0 値と一致。入口 `$E060` は S1・S4・S5 で一致 | 内部先は S4 単独が主。S5 の一致は JPN/USA 各 1 点のみ。1.0/2.0 の内部先は 1 出典のみ。版別特性は未確実。ジャンプ入口が不変であることのみ `CORROBORATED` とする |
| F02 | 12×12・16×16 の有無 | `HYPOTHESIS` | S5 README「most games use either 12x12 or 16x16 Japanese fonts」「The 12x12 and 16x16 fonts in the Super System Card」は両サイズの存在を、S6 は「$FF = transfer mode ($00 for 16x16; $01 for 12x12)」とする。S4 は入口値のみでサイズを述べない。S5・S6 は同作者であり独立ではない | 大小 2 サイズの存在は単一系統（S5/S6 同作者）の主張である。Stum の版表は入口値のみでサイズを支えない。数値自体は出典の主張のままであり、形状の一致は保証しない |
| F03 | 文字コード（Shift-JIS） | `HYPOTHESIS` | S5 README: 2 バイト SJIS。S6: 「Kanji code … SJIS」。S5 の `_AL=$F8`（LSB）・`_AH=$F9`（MSB）注記も 2 バイト SJIS を示す | 単一系統（S5/S6 同作者）のみ。HuCC・Hu-Go! 側にラッパ記述なし（S1/S2）。JIS X 0208 ではなく SJIS 文字列として渡す点は疑いの余地が小さいが、純正 callee の実測ではない |
| F04 | 文字コードのバイト順 | `HYPOTHESIS` | S5: 「SJIS character value in ZP @ $F8/F9 (LSB first)」。S6: 「$F8 holds LSB、$F9 holds MSB」。スクリプト上の順序は MSB 先行（例 `$8352` がスクリプト上 `83 52`）の注意記あり | S5・S6 同作者のため独立ではない。出典の LSB ファーストの主張は信頼できるが、純正 callee の検証はない |
| F05 | モード指定 | `HYPOTHESIS` | S5: `$FF` に 0=16×16、1=12×12。S6: 同値。S5 の `xlateFont.asm` は `$FF` を `_DH`（字集合）とし、0/1 を `NORMAL: JMP ORGTRGT`（元のコードへ委譲）で 2/3 を新規処理へ分岐。この分岐は S5 改変の事実であり、元の 0/1 意味を直接証明しない | 0/1 の意味付けは出典の主張による。`$FF` 以外のモードの扱いは後述（F14） |
| F06 | 出力先指定 | `HYPOTHESIS` | S5: `$FA/$FB` に出力先ポインタ。S6: 同値。S5 の `_BL=$FA`・`_BH=$FB` 注記も同値 | 同上。同作者の主張。出力の「サイズ」指定は `$FF` であって `$FA/$FB` にはサイズなし |
| F07 | グリフサイズ | `HYPOTHESIS` | S5 は 12×12・16×16 サイズの存在を述べるが、ピクセル寸法の実測値なし。S6 も同値 | サイズ自体（F02）は信頼で高いが、元の格納寸法・パディング量は未検証（F08・F11） |
| F08 | 1 行あたりの格納バイト数 | `UNKNOWN` | S6 は「32-byte buffer」のみで、行当たり値の内示なし。S5 の新規 8×幅処理は 2 bytes/行（行ごとに glyph byte と `0`）であるが、これは改変処理の事実で元の行当たり値を示さない | 16×16 で 16 行×2 bytes=32 は自然だが、12×12 で 24 bytes / 32 bytes（末尾 8 bytes 埋め）など代替が排除できない。元の行当たり値としては UNKNOWN |
| F09 | bit 順序（MSB/LSB） | `UNKNOWN` | 出典に記述なし | — |
| F10 | 行順序 | `UNKNOWN` | 出典に記述なし | — |
| F11 | パディング | `UNKNOWN` | 出典に記述なし | S5 の新規 8×12 処理は 24 bytes 後に 8 bytes の 0 埋め（`NI ESU`）を行うが、これは改変側の選択であり、元のパディングではない |
| F12 | A/X/Y/P への影響 | `UNKNOWN` | 出典に記述なし | — |
| F13 | SP / MPR 保存 | `UNKNOWN` | 出典に記述なし（S5 の新規処理は MPR の保存・回復を行うが、これは改変側の事実） | 純正 callee が MPR を変更・保存するかは UNKNOWN。S5 の `BANK_STORE=$22BA`（syscard's EX_GETFNT puts old bank here の注記）は改変作者の主張で、純正の保存先の事実ではない（`HYPOTHESIS` として参照のみ） |
| F14 | 不正文字の動作 | `UNKNOWN`（判定は `BLOCKED`） | S5 の `xlateFont.asm` は `$FF` が 4 以上なら `JMP FNTERR`（JPN `$F129`、USA `$F142`）へ跳ぶ。これは改変側が元のエラー先と推定したアドレスへ迂回する実装事実である。元の `$F129` の挙動は本書では読まない（逆アセンブル・実行は行わない） | 不正文字（不正 Shift-JIS）の動作としては UNKNOWN。不正モードの迂回先は改変側の推測であり、純正のエラー_returns は UNKNOWN |
| F15 | 未収録文字の動作 | `UNKNOWN`（判定は `BLOCKED`） | 出典に記述なし | — |
| F16 | IRQ・作業 RAM への副作用 | `UNKNOWN` | 出典に記述なし | — |
| F17 | 戻り値・エラー状態 | `UNKNOWN` | 出典に記述なし。S3 に記述なし。S5 の新規処理は `$EE`（`RET_CODE`）に 0/1 を入れ、`EXIT: LDA <RET_CODE / RTS` とする。これは改変処理の事実であり、元の戻り値ではない | 元の `A`・フラグ等の戻り状態は UNKNOWN |

### 解釈上の注意

- **`$E060` のジャンプテーブル入口**: `E000 + 3 × slot`（slot `$20`）は
  `CORROBORATED`。S1 の定義式、S4 の表、S5 の `FNTPTCH=$E060`
  での `JMP FNTORG0`、S6 の `$E060` 記載が一致する。内部先
  （$F124 等）は F01 の通り信頼度が下がる。
- **呼び出し前条件**: S4 はインストール直後において MPR#7=`$00`、
  MPR#0=`$FF`、MPR#1=`$F8` と BIOS が想定する旨を述べる。
  これは呼出側の事実の `HYPOTHESIS` であり、callee 側の
  保存規約（F13）を強めない。
- **Zeograd（S3）の記録**: `EX_GETFNT` の節が空欄であることは、
  公開ノートが本契約を記録していないことを示す。Zeograd による
  サポートは存在しない。S3 を根拠に「何もない」とは結論できないが、
  「Zeograd が解釈している」ことも言えない。
- **改変実装（S5）の扱い**: `xlateFont.asm` の新規モード 2/3
  （8×16 / 8×12）、`NUFONT` マーカ（`$FFAA`）、`JMP FNTERR` 等は
  すべて改変側の事実である。S5 を純正契約の証拠として
  引用しない。とくに新規処理の 2 bytes/行コピーと 8 bytes 埋めは
  元の出力形式の証拠ではない。ただし S5 が「元の呼び出し
  パラメータ」を前提としている点は、F03–F06 の主張の根拠となる。
- **同作者問題**: S5・S6 は同一著者（Dave Shadoff）であり、
  重要項目の `CORROBORATED` には数えない。上表はこのため
  F03–F11 を `HYPOTHESIS` / `UNKNOWN` に留める。著者が異なる
  独立資料（HuCC・Hu-Go!・Stum）は名前と入口のみで契約内容を
  支えない。

## 改変と純正の切り分け（S5）

| 項目 | 純正 System Card（本研究の対象） | 改変 S5（参照のみ、複製しない） |
|---|---|---|
| モード 0/1 | 16×16 / 12×12 とする出典の主張（`HYPOTHESIS`） | 新規に 2/3（8×16 / 8×12、ASCII のみ）を追加。`_DH` が 2 未満なら元の処理へ委譲、2–3 なら新規処理、4 以上なら `FNTERR` へ迂回 |
| 文字入力 | 2 バイト SJIS とする主張（`HYPOTHESIS`） | SJIS の代わりに単バイト ASCII を `$F9` に渡す拡張。`$F9` の値が `0x20–0x7F` の範囲で使う |
| 出力 | UNKNOWN（F08・F09） | 1 行 2 バイト（左が glyph、右は 0）で 8×12 は 24 bytes＋8 bytes の 0 埋め、8×16 は 32 bytes。BDF とは無関係な独自形式 |
| 識別 | なし | `NUFONT` 文字列を `$FFAA` に格納。`EX_GETVER` は不変 |
| バンク | UNKNOWN（F13） | FONTBANK=1 を MPR6（`$C000`）または MPR3（`$6000`）に切り替え、旧 MPR6/MPR3 を `$22BA` に保存して回復する。バッファの MSB が `$C0` 以上なら MPR3 経路へ切り替える |
| 戻り | UNKNOWN（F17） | `$EE` に 0（正常）または 1（範囲外）を入れ、`A=RET_CODE` で `RTS` |
| 作業 ZP | UNKNOWN（F16） | `$EC/$ED`（`SRCLOC`）、`$FC`（基底 MSB）、`$EE`（戻り）を使用 |

上表の改変側は、#708 における DEV 専用設計の参考にはなっても、
純正 ABI の実測・模倣根拠ではない。IPS パッチ（`xlateFont-JPN.ips` /
`xlateFont-USA.ips`）は複製しない。

## バージョン別入口（S4 + S5）

| バージョン | 入口 `$E060` の内部先 | 根拠 |
|---|---|---|
| 1.0 | `$F1A3` | S4 のみ |
| 2.0 | `$F122` | S4 のみ |
| 2.1 | `$F124` | S4。S5 JPN 3.0 の `ORGTRGT=$F124` とも一致 |
| 3.0 JPN | `$F124` | S4 + S5 (`ORGTRGT=$F124`) |
| 3.0 USA | `$F13D` | S4 の TG 3.0 値と S5 USA `ORGTRGT=$F13D` が一致 |
| TG 2.0/3.0 | `$F13D` | S4 のみ（USA は S5 一致） |

`FNTERR` は JPN `$F129`、USA `$F142`（`ORGTRGT+5`）であり、
S5 が元のエラー先と推定して迂回するアドレスである（S5 の事実）。
S4 はこのアドレスを列挙しない。内部先の確定は `HYPOTHESIS` に留める。

## 未確定と残存問題

- ビットマップの寸法・順序・パディング（F07–F11）は UNKNOWN。
  実測には参照 BIOS の実行が必要で、`BLOCKED` である。
- レジスタ・SP/MPR・副作用・エラー（F12–F17）はすべて UNKNOWN。
  純正 ZP の使用範囲（`$20F8–$20FF` の `al/ah/bl/bh/cl/ch/dl/dh` の
  慣習を既存台帳から借りてはならない）や、戻りフラグは UNKNOWN。
- 文字集合（System Card の収録文字）は UNKNOWN。出典は文字集合表を
  与えない。#707 の東雲監査は素材側の収録文字であり、
  純正との一致ではない（設計文書 §3）。
- #42 の要求である「A/X/Y/P・ZP・MPR・SP・副作用・エラー」の
  体系は未解決。#708 は DEV 専用として独自定義しなければならず、
  この UNKNOWN を解消するまでは System Card 互換の主張はしない。
- 救済として異常系・未収録字の無破壊・リターン条件を DEV 契約で
  明確にすること。未知条件を「成功」として扱わない設計原則を踏襲する。
