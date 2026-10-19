# CD_SEARCH（スロット `$07`、候補 `$E015`）呼出側ABI・根拠台帳（Issue #249）

対象: System Card の `CD_SEARCH`（CD検索・再生位置変更）の呼出規約。親 Issue は #16。
**状態: ABI は未確定。ROM 実装なし。`BLOCKED_ABI` を維持する。`release_eligible=false`。**

## 1. 結論と範囲

- 本書は文書とソースの調査のみ。System Card の呼出先（`$E015` 以降）は **一度も実行していない**。
  実コア（Geargrafx）でも実機でも、`CD_SEARCH` を呼ぶゲストコードは実行していない。
- 出典の判定は次の三区分。
  - `SOURCE_FACT`: 固定版の公開ソース、または公開ページの本文・表に書かれている事実。出典 ID を付ける。
  - `HYPOTHESIS`: 出典から推測したもの。呼出先の実行で確認していない。
  - `UNKNOWN`: 出典が不足している。値を推測で埋めない。
- 側の記号: `K` は呼出側（SDK の規約）、`C` は呼出先（System Card の振る舞い）、`H` は Hu-Go の HLE（エミュレータの模擬。BIOS の証拠ではない）、
  `M` は出典間の不一致。
- 呼出先の入口 `$E015` と、公開表から得た実装アドレス（§4 の C2、C3）は `HYPOTHESIS` である。これらを `JSR` で呼ぶことは禁止する。
- 商用BIOS（System Card）のバイト列、逆アセンブル、市販ROMのデータは使っていない。`./rom/` は読んでいない。

## 2. 出典

| ID | 出典 | 固定 revision・取得 | 行 | ライセンス | 用途 |
| --- | --- | --- | --- | --- | --- |
| S1 | HuC `include/hucc/pcengine.inc` | `pce-devel/huc` `54f5c73606c1e7142e8095c727cab2767e2b3cb3`。アーカイブ SHA-256 は `9fe91254051381077bd4ea217b718ec033a52da185283ae9479c1abac365311b`（`docs/cd-base-abi-ledger.md` と同一） | 388、330–343、475 | BSL-1.0（ヘッダ。著作権者の表示なし） | 名前の宣言（呼出側）、`_al`〜`_dh`、`cd_searching` |
| S2 | HuC `include/hucc/core.inc` | 同上 | 37–42 | BSL-1.0 | `system` マクロ（呼出側の経路） |
| S3 | HuC `include/hucc/core-kernel.asm` | 同上 | 486–516（`call_bios`、MPR7 の退避・復元） | BSL-1.0（John Brandwood） | 呼出側の trampoline |
| S4 | Hu-Go `bios.c` | `mckayemu/hugo` `64ed226da2288738781743eeef228c069e8973a4`。取得 tar の SHA-256 は `e52a6acd0a53f6485fa022341b00c72f28414b3a4bb276523bae9312fb05fc1a` | 37（ID 定義）、168、631–665（`CD_SEARCH` の HLE） | `COPYING` により GPL-2（例外: `pcecd.c` のみ BSD） | **HLE。BIOS の証拠に使わない。H 行のみ** |
| S5 | Stum「PC Engine CD-ROM² BIOS Method offsets」（2025-07-28） | <https://www.stum.de/2025/pcenginebiosoffsets/>。2026-10-19 に閲覧。「Method Addresses across CD-ROM² System Cards」表と本文 | 表の `cd_search` 行（JSR、1.0、2.0、2.1、3.0、TG2.0、TG3.0 の 7 列） | 本文 CC BY-NC-SA 4.0、コード MIT（ページ末尾の表示）。**数値のみ採録し、本文は引用しない** | 実装アドレスの候補（HYPOTHESIS） |
| S6 | Zeograd「PC-Engine Documentation: The System card」 | <https://www.zeograd.com/download/pce_bios.html>。2026-10-19 に閲覧 | `CD_SEARCH` の見出し（行番号は要約取得の未検証の所見のため記載しない） | **ページに明示なし（UNKNOWN）。参照のみ、内容は引用しない** | 見出しの有無の確認のみ。要約取得の所見は根拠として固定しない |

除外（根拠に使わない）:

- `include/huc/system.inc` 72 行（`cd_search = $E015`）: 名前の宣言のみ。`include/huc/` のライセンスは `docs/cd-base-abi-ledger.md` で「不明（除外）」とされている。
- `include/huc/cdrom.asm`: ライセンス不明のため除外（issue の指定）。
- `examples/asm/elmer/kickc/bios.h` 33 行: ライセンス未確認。名前の宣言のみ。
- `docs/cdda-*.md`、`docs/research/150-cd-play-abi.md`: 本リポジトリ内の整理であり、外部出典ではない。

HuC の固定版全体で `cd_search` を単語として検索すると、一致は `pcengine.inc` の 475 行（equate）、`include/huc/system.inc` 72 行、
`examples/asm/elmer/kickc/bios.h` 33 行（除外）の三箇所だけである。`cd_searching` の一致は `pcengine.inc` 388 行の一箇所だけである。

## 3. 呼出側の事実（K 行）と H 行（HLE）

| ID | 側 | 項目 | 判定 | 値 | 出典 | 理由・確度 |
| --- | --- | --- | --- | --- | --- | --- |
| K1 | K | 呼出側の `cd_search` 呼出し | SOURCE_FACT | 0 件（BSL 側の固定版） | S1 475 | 単語検索の結果。equate のみで、呼出しがない |
| K2 | K | `system` マクロの呼出し経路 | SOURCE_FACT | `ldy #<target` の後に `call_bios` を呼ぶ。`call_bios` は `$E000+Y` を `jsr` する | S2 37–42、S3 500–516 | 全 BIOS 呼出し共通の経路。`cd_search` 固有ではない |
| K3 | K | `cd_search` の場合の `Y` の値 | HYPOTHESIS | `$15`（`$E015` の下位バイト） | S1 475、S3 500–516 | K2 の規則から導いた値。`cd_search` の呼出しが固定版にないため確認できない |
| K4 | K | `_al`〜`_dh`（`$20F8`〜`$20FF`）の名前 | SOURCE_FACT | `_al`/`_ah`/`_bl`/`_bh`/`_cl`/`_ch`/`_dl`/`_dh` の名前のみ | S1 330–343 | 名前の宣言。`cd_search` の入力として書く呼出側はない |
| K5 | K | `cd_searching`（`$F8:2248`）のコメント | SOURCE_FACT | コメントは「*UNDOCUMENTED* 1 when background cd_search() requested」。書込み・読出しは固定版にない | S1 388 | コメントの事実。書いている箇所は見つからない |
| K6 | K | `cd_searching` の意味（バックグラウンド検索） | HYPOTHESIS | 非同期（background）の検索を要求したことを示すと推測 | S1 388 | 根拠はコメントのみ。実装者の言葉であり、呼出先の仕様ではない |
| K7 | K | MPR7 の退避・復元（呼出側） | SOURCE_FACT | `call_bios` は呼出前に MPR7 を `$00` に切り替え、呼出後に元の値へ戻す | S3 500–516 | 呼出側の trampoline の事実。呼出先が MPR を変えるかは C7 |
| K8 | K | 戻り値・割込みの呼出側の扱い | UNKNOWN | UNKNOWN | — | `cd_search` の呼出側がないため、戻りを読む箇所も IRQ の扱いもない |
| H1 | H | 識別子 `CD_SEARCH` の番号 | SOURCE_FACT | `$07` | S4 37 | HLE の定義。BIOS の番号の証拠ではない |
| H2 | H | HLE の `_al` の扱い | SOURCE_FACT | `_al` を BCD のトラック番号として読む | S4 631–637 | HLE の記述。BIOS の入力の意味ではない。HLE の作者は仕組みが不明だと注記している |
| H3 | H | HLE の `_bh` の扱い | SOURCE_FACT | `_bh` の bit 1（値 `$02`）が立つとトラックを再生する | S4 631–665 | HLE の記述。BIOS の証拠ではない |
| H4 | H | HLE の停止・戻り | SOURCE_FACT | 再生を止めてから（必要なら）再生し、A を 0 にして N/T/Z を更新して RTS。エラー経路はない | S4 631–665 | HLE の記述。BIOS の戻り値の証拠ではない |

## 4. 呼出先の契約フィールド（C 行）

呼出先は実行していない。C 行は `HYPOTHESIS` か `UNKNOWN` だけである（`SOURCE_FACT` にしない）。

| ID | 側 | 項目 | 判定 | 値 | 出典 | 理由・確度 |
| --- | --- | --- | --- | --- | --- | --- |
| C1 | C | 入口（候補 `$E015`） | HYPOTHESIS | `$E015` | S1 475、S5 表の JSR 列 | 名前と番号の対応は二出典で一致するが、呼出先の実行による確認はない |
| C2 | C | 3.0 の実装アドレス | HYPOTHESIS | `$EF34`（JSR 禁止） | S5 表（3.0 列） | 公開表の値。実行していない。`JSR` で呼ぶことは禁止 |
| C3 | C | 他の版の実装アドレス | HYPOTHESIS | 1.0 `$EFB1`、2.0 `$EF34`、2.1 `$EF34`、TG2.0 `$EF4D`、TG3.0 `$EF4D` | S5 表 | 公開表の値。C2 と同じく実行していない。世代差は §5 |
| C4 | C | 入力 `_al`〜`_dh` の意味（トラック、MSF、セクタ、フラグ） | UNKNOWN | UNKNOWN | — | 呼出側に書く箇所がない。H2、H3 は HLE であり根拠にできない |
| C5 | C | 入力 A、X、Y、P | UNKNOWN | UNKNOWN | — | 呼出側の入力の記述がない |
| C6 | C | 戻り値（A、X、Y、P、フラグ） | UNKNOWN | UNKNOWN | — | 呼出側が戻りを読まない（K8）。HLE の戻り（H4）は根拠にできない |
| C7 | C | MPR の前提 | UNKNOWN | UNKNOWN | — | S5 は数値のみを採録する方針のため本文の記述は根拠にしない。MPR の前提を示す出典が本台帳にない |
| C8 | C | スタックの使用量・深さ | UNKNOWN | UNKNOWN | — | 出典がない |
| C9 | C | IRQ・割込み許可（CLI/SEI）の前提 | UNKNOWN | UNKNOWN | — | 呼出側に `cd_search` の記述がない |
| C10 | C | 副作用（作業 RAM、バッファ、音声の停止） | UNKNOWN | UNKNOWN | — | 呼出先の書込みは未観測。H4 の音声停止は HLE の記述 |
| C11 | C | 失敗条件・エラー値（ディスクなし、範囲外、busy） | UNKNOWN | UNKNOWN | — | S6 に本文がなく、H4 にエラー経路はない |
| C12 | C | 再生中・停止中・一時停止中からの位置変更後の状態 | UNKNOWN | UNKNOWN | — | 出典がない（`docs/cdda-unresolved-facts.md` の未解決項目 9 と同じ） |
| C13 | C | 同期・完了待ち（即時復帰か、再生開始まで待つか） | UNKNOWN | UNKNOWN | — | K6 の「バックグラウンド」は HYPOTHESIS にとどまる |

## 5. 不一致（M 行）

| ID | 側 | 項目 | 判定 | 値 | 出典 | 理由・確度 |
| --- | --- | --- | --- | --- | --- | --- |
| M1 | M | 入口名の宣言 | SOURCE_FACT | HuC の宣言 `cd_search = $00:E015`（呼出先の入口名）。`$07` は S4 の HLE 定義のみ（H1） | S1 475 | HuC の宣言の事実のみ。振る舞いの一致ではない。他出典は名前の裏付けに限る: S5 表は HYPOTHESIS（C1）、S6 は見出しのみ（UNKNOWN 出典）、S4 は `$07` のみで `$E015` を含まない |
| M2 | M | System Card 3.0 固有の実装アドレス | HYPOTHESIS | S5 の表では 3.0 が `$EF34`、1.0 が `$EFB1`、TG3.0 が `$EF4D`。3.0 と TG3.0 は異なる | S5 表 | 二次資料の表が示す差。実装アドレスの正しさは未確認（C2、C3）。SOURCE_FACT にしない |
| M3 | M | HuC の equate の版の区別 | SOURCE_FACT | HuC は版を区別せず `$E015` を一つだけ宣言する | S1 475 | S5 は版ごとに異なるアドレスを載せる。HuC の equate はジャンプテーブルの番号として読める |
| M4 | M | Zeograd の `CD_SEARCH` の本文 | HYPOTHESIS | 見出しだけで本文がないと読める（要約取得による所見）。直前の段落は `CD_PLAY` の説明と読める（引用しない）。見出し群の後の入出力の記述は `CD_SUBQ` のものと読める | S6 | 未検証の要約に基づく。Zeograd は無許諾で内容を引用しない。`CD_SEARCH` の IN/OUT は UNKNOWN |
| M5 | M | 検索の意味（位置指定、トラック再生、非同期） | UNKNOWN | UNKNOWN | S1 388、S4 631–665、S6 | HuC は「バックグラウンドの検索」と書く。Hu-Go HLE は「トラック再生」として模擬する。Zeograd は本文がない。三つは一致せず、決着の出典がない |

## 6. System Card 3.0 固有の不一致（明示）

- 3.0 の入口 `$E015` の名前は HuC の宣言が根拠である（M1、SOURCE_FACT）。Stum の表は名前の裏付けとして HYPOTHESIS、Zeograd は見出しのみ（M4、HYPOTHESIS）、Hu-Go は `$07` のみを示し `$E015` を示さない。
- 3.0 の実装アドレスとして公開表が示す `$EF34` は、1.0 の `$EFB1` と TG3.0 の `$EF4D` と異なる（M2、HYPOTHESIS）。
  この値は二次資料の表だけに基づき、実行で確認していない（C2）。
- Zeograd は 3.0 を対象とする記述があると読めるが、`CD_SEARCH` の本文がない（M4）。3.0 の引数と戻り値は出典からは得られない。
- Hu-Go の HLE は 3.0 固有の記述を持たない。トラック再生として模擬するが、これを 3.0 の振る舞いの根拠にはしない（H2〜H4）。

## 7. 実装に対する残る UNKNOWN（ブロック要因）

- C4（入力の意味）、C5、C6（戻り値）、C10（副作用）、C11（失敗条件）、C12（状態遷移）、C13（同期）が未確定である。
  これらは呼出先を実行しない限り確定しない。
- 実行可能な System Card の `$E015` は本リポジトリにない。利用者所有の BIOS を使う経路は本作業の範囲外である。
- 公開された出典の間で、検索の意味が一致しない（M5）。Hu-Go HLE は根拠にできない。
- 実装は行わない。`CD_SEARCH` の `JSR`、表の実装アドレスの直接呼出し、推測による ABI の実装は禁止する。

## 8. 禁止事項と状態

- `BLOCKED_ABI` を維持する。`release_eligible=false`。`DEV_CALLABLE` にしない。
- 呼出先の振る舞いに `VERIFIED`、`OBSERVED`、`PASS` を付けない。本書にはこれらの判定はない。
- 公開表の値（S5）は一次証拠ではない。`provenance_status` は PENDING のままとする。
- S4（Hu-Go）は GPL-2 のため、本書では識別子と行番号の参照に限る。ロジックを移植しない。
- S5 の表は数値だけを採録し、本文を引用しない（CC BY-NC-SA 4.0 の本文）。

## 9. 検査

`tests/host/test_cd_search_contract.py` は次を検査する（BIOS の証拠ではなく、台帳の規則のみ）。

- 判定列は `SOURCE_FACT` / `HYPOTHESIS` / `UNKNOWN` のいずれかで、`VERIFIED` / `OBSERVED` / `PASS` がない。
- 側 `C` の行は `SOURCE_FACT` にならない。`UNKNOWN` の行の値は `UNKNOWN` である。
- 側 `H` の行は HLE と明記する。`SOURCE_FACT` / `HYPOTHESIS` の行は出典 ID（S1〜S6）を引用する。
- C1〜C13 の既定の判定を固定する（一つでも昇格すると失敗する）。
- `JSR $EF` のような、表の実装アドレスを呼ぶ記述がないこと。
- `BLOCKED_ABI` と `release_eligible=false` の記載があること。

実行: `python3 -m unittest discover -s tests/host -p 'test_cd_search_contract.py' -v`

## 10. 次の作業

1. 呼出先の実行による測定（実行可能な BIOS が利用できる場合のみ）。測定項目は C4〜C13。
2. 二次資料の版差（M2）は、実行による確認まで HYPOTHESIS のままとする。
3. ライセンス判断（maintainer）: S4（Hu-Go）、S5（Stum の本文）、S6（Zeograd）の採否。
