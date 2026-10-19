# AD_RESET ABI 証拠台帳（Issue #292）

スロット `$10`、候補エントリ `$E030`、親 Issue #25。**状態: ABI は未確定。callee（System Card の AD_RESET 本体）は未実行。`BLOCKED_ABI`、`release_eligible=false`、`DEV_CALLABLE` 不可。VERIFIED / PASS の主張はしない。**

本台帳は、公開 caller（呼出側のソース）から読める事実だけを記録する。呼出先（BIOS）の振る舞いは、どの項目も実測していないため確定しない。`docs/cd-base-abi-ledger.md` の確度規則に従う。

## 1. 規則

確度の意味は `docs/cd-base-abi-ledger.md` §1 と `docs/cd-seek-abi-ledger.md` §1 に従う。SOURCE_FACT の定義は #321（CD_FADE 台帳）と同一の一つの定義を使う（下記）:

- `SOURCE_FACT`: SOURCE_FACT = a caller-side fact written in a pinned primary source. It is independent of provenance PENDING. It does NOT indicate BIOS callee behavior.
  （訳: 固定版の一次出典に書かれた呼出側の事実。出典の provenance が PENDING かどうかとは独立。BIOS 呼出先の振る舞いを示さない。）出典が第三者の記述のみ、または再実装・開発者の主張である場合は `HYPOTHESIS` とする。ライセンス上の利用可否、呼出先の実測、実装可能性は別の軸であり、SOURCE_FACT はそのいずれも示さない。
- `HYPOTHESIS`: 出典の記述から推測したが、呼出先を実行して確認していない。
- `UNKNOWN`: 出典が不足している。推測で埋めない。成功値・失敗値も定義しない。

除外する証拠:

- ホスト側の算術、合成テスト、DEV ROM の挙動は BIOS の証拠ではない。
- `include/huc/` 配下（`cdrom.asm`、`startup.asm`）は、ライセンス表示がなく、`docs/cd-base-abi-ledger.md` §2 の決定により根拠に使わない。
- `examples/asm/elmer/kickc/bios.h` は、ライセンス未確認のため根拠に使わない（名前の宣言のみ）。
- Hu-Go `bios.c` は GPL-2 で、HLE（ホスト側の代替実装）であり、BIOS の ABI の証拠にならない。内容は読まず、本台帳では引用しない。
- Stum と Zeograd の記述は、単独では確度 `HYPOTHESIS` 以下とする（§2.3）。

## 2. 出典

provenance 区分（`docs/provenance.md` の per-source 審査）: 本台帳の全出典は **PENDING**（人間のレビュアと決定日は未記録）。出典の採否（PENDING）は SOURCE_FACT の付与とは独立であり、APPROVED への変更は人間のレビュアの決定に属する（本台帳は変更しない）。区分が APPROVED になるまで、本台帳の結論は設計入力に限る。

### 2.1 HuCC（`pce-devel/huc`、固定版）

- 固定版: commit `54f5c73606c1e7142e8095c727cab2767e2b3cb3`
- 取得元: `https://codeload.github.com/pce-devel/huc/tar.gz/54f5c73606c1e7142e8095c727cab2767e2b3cb3`
- アーカイブ SHA-256: `9fe91254051381077bd4ea217b718ec033a52da185283ae9479c1abac365311b`（取得時に一致を確認）
- 展開物は実行していない。解析は `grep` と `sed -n` による読取りのみ。

| ID | 出典（固定版内のパス） | 行 | ライセンス表示 | provenance | 使い方 |
| --- | --- | --- | --- | --- | --- |
| H1 | `include/hucc/pcengine.inc` | 485（`ad_reset	= $00:E030, FUNC`、ファイル内の他の System Card 関数と同じ形式） | BSL-1.0（ヘッダ 8–10 行。著作権者: ファイル内に記載なし） | PENDING（著作権者 未記録） | エントリ番地の宣言 |
| H2 | `include/hucc/hucc-systemcard.asm` | 476–480（`_ad_reset` マクロが `system ad_reset` を展開。オペランドなし） | BSL-1.0（John Brandwood 2024、ヘッダ 8、10–12 行） | PENDING | 呼出側マクロ |
| H3 | `include/hucc/hucc-systemcard.h` | 101（`extern void __fastcall __macro ad_reset( void );`） | BSL-1.0（John Brandwood 2024、ヘッダ 11、13–15 行） | PENDING | 呼出側の C 宣言（引数・戻り値の宣言のみ） |
| H4 | `include/hucc/core.inc` | 41–44（`system` マクロ: `ldy #<\1` の後に `jsr call_bios`） | BSL-1.0（John Brandwood 2021–2024、ヘッダ 8、10–12 行） | PENDING | 呼出側の selector 設定 |
| H5 | `include/hucc/core-kernel.asm` | 488–516（`call_bios`、コメント 488–498、本体 500–516） | BSL-1.0（John Brandwood 2021–2024、ヘッダ 8、10–12 行） | PENDING | 呼出側の MPR7 の切替えと復元、A の退避 |

固定版の再確認（展開物は実行しない）:

```sh
grep -rIn -E 'ad_reset|\$E030' include/hucc doc/hucc
```

HuCC の `ad_reset` 呼出側は、`include/hucc/` 内ではマクロ定義（H2）のみである。`include/huc/` には呼出しがあるが除外する（§2.4）。

### 2.2 公開 example の再実装（BSL-1.0 のファイルヘッダ）

| ID | 出典（固定版内のパス） | 行 | ライセンス表示 | provenance | 使い方 |
| --- | --- | --- | --- | --- | --- |
| E1 | `examples/asm/elmer/include/adpcm.asm` | 56–73（`adpcm_reset`、コード 63–73。56 行に "BIOS AD_RESET" の注記、72 行に "BIOS sets 2KHz playback!" の注記） | BSL-1.0（John Brandwood 2022、ヘッダ 10–14 行） | PENDING | 開発者による**再実装**。BIOS ではない（§3 で扱う） |
| E2 | `examples/asm/elmer/rom-bare-clktest/clktest.asm` | 446–463（E1 とほぼ同一の `adpcm_reset`） | BSL-1.0（John Brandwood 2026、ヘッダ 8、10–12 行） | PENDING | E1 と同じ。独立の証拠ではなく同じ著者の複製 |

E1 と E2 は、呼出先（BIOS）ではなく、同じ著者が書いた**再実装**の内容である。そこから読めるのは、その再実装が何を書くかだけである。BIOS の振る舞いの根拠にはしない。

### 2.3 公開の説明（単独では根拠にしない）

| ID | 出典 | ライセンス | provenance | 記述された事実 | 扱い |
| --- | --- | --- | --- | --- | --- |
| P1 | Stum 2025 System Card method offsets（`https://www.stum.de/2025/pcenginebiosoffsets/`） | CC BY-NC-SA 4.0（ページ注記。ブログ掲載コードは MIT と記載） | PENDING | 名前 `ad_reset`、エントリ `JSR $E030`。本体番地は版ごとに記載（1.0: `$F3E0`、2.0: `$F375`、2.1/3.0: `$F37F`、TG2.0/TG3.0: `$F398`）。入力・戻り・副作用は記載なし | 単独では根拠にしない（§1）。エントリ番地の照合の補助のみ。本体番地は HYPOTHESIS（第三者の記述で、実行していない）。引用はせず要点のみ。 |
| P2 | Zeograd System Card notes（`https://www.zeograd.com/download/pce_bios.html`） | ページに著作権・ライセンス表示なし | PENDING | `$E030` は関数番号 `$10` × 3 + `$E000`。目的は ADPCM コントローラのリセットに関するものと記述。入力・出力・レジスタ・副作用・エラーは未記載 | 目的の一文のみ HYPOTHESIS 扱い。ライセンス表示がないため引用しない。 |

### 2.4 除外した出典

- `include/huc/startup.asm` 486 行（`jsr ad_reset` の呼出し）と `include/huc/cdrom.asm` 541–547 行: ライセンス表示がないため除外。`cd-base-abi-ledger.md` §2 の決定に従う。この呼出しが公開 caller の最も有用な例だが、根拠にできない。ライセンス判断が出るまで扱わない。
- `doc/hucc/hucc-function-reference.md`（`ad_reset` / `ad_trans` の説明文）: ファイル内にライセンス表示がないため除外。`ad_trans` の説明は本台帳で使わない。
- `examples/asm/elmer/include/movie.asm` 1341、1497 行: ファイルヘッダ（"derived from Hudson's original HuVIDEO player code in Gulliver Boy"）が、Hudson 由来コードからの派生を明記している。`docs/provenance.md` の除外規則（"Do not silently incorporate proprietary-derived code"）に従い、**REJECTED**。
- `examples/asm/elmer/kickc/bios.h`: 名前の宣言のみ。ライセンス未確認で除外。
- Hu-Go `bios.c`: GPL-2 の HLE。BIOS の ABI の証拠にならないため、読まず、引用しない。
- `doc/huc/clibref.txt`、`doc/huc/huc3_doc.htm`: ライセンス表示なし。引用しない。
- `docs/cd-base-abi-ledger.md` 等の本リポジトリの台帳は、同じ固定版を参照する。本台帳は、その規則のみを再利用し、内容は独立に記録する。

## 3. 呼出側から読める事実（公開 caller）

以下は、HuCC の呼出側（H2〜H5）と、開発者の再実装（E1、E2）の記述である。BIOS の振る舞いではない。項目 1〜6 は呼出側の記述で `SOURCE_FACT`、項目 7、8 は再実装・開発者の記述で `HYPOTHESIS` とする（出典が PENDING でも SOURCE_FACT は付く。採用の可否は別問題）。

1. **エントリ番地**（H1、SOURCE_FACT）: `ad_reset` は `$00:E030` として宣言されている。
2. **呼出し経路**（H4、H5、SOURCE_FACT）: `system ad_reset` マクロは `Y = $30` を設定し、`call_bios` を呼ぶ。`call_bios` は Y の値を `jsr $E000` の下位バイトに書き込み（自己書換え）、MPR7 に System Card のバンクを割り当ててから `jsr` し、戻ると MPR7 を復元する。
3. **A の扱い**（H5、SOURCE_FACT）: `call_bios` は入口で A を push し（502 行）、MPR7 の切替えの後に pull して BIOS に渡す（507 行）。戻りでは、BIOS 後の A を push し（511 行）、MPR7 を復元した後に pull する（514 行）。よって A は呼出側から BIOS へ渡り、BIOS から呼出側へ戻る。ただし BIOS が A をどう使うかは UNKNOWN。
4. **X の扱い**（H5、SOURCE_FACT）: `call_bios` は X に触れない。BIOS が X を読むか書くかは UNKNOWN。
5. **副作用の不記載**（H5 の `Uses:` 注記（492 行）、SOURCE_FACT）: `call_bios` のコメントは、呼出される関数が何を使うかを「Whatever the System Card function uses!」と記し、副作用を明記していない。源泉側が副作用を規定していないことの記録であり、副作用なしの証拠ではない。
6. **引数・戻り値の宣言**（H2、H3、SOURCE_FACT）: C 宣言は `void` で、引数・戻り値はない。これは呼出側の宣言であり、BIOS が引数を読まないことの証拠ではない。
7. **再実装の内容**（E1 のコード 63–73 行、HYPOTHESIS（再実装の記述。BIOS の記述ではない））: 開発者の `adpcm_reset` は、ADPCM 制御レジスタに reset の値を書いて 0 に戻し、DMA 制御、IRQ マスク（一部ビットを clear）、再生速度レジスタを書く。これは再実装が行う書込みの記録であり、BIOS の書込みの記録ではない。
8. **再実装のコメント**（E1 の 56、59、72 行、HYPOTHESIS）: "BIOS AD_RESET" と "BIOS sets 2KHz playback!" は、開発者による BIOS 振る舞いの主張であり、本台帳では確認していない。E2 は同じ記述を複製している。

## 4. 項目別の台帳

入力・戻り・副作用は、`spec/api_contracts.json` の AD_RESET の項目と Issue #292 の項目に対応させる。呼出側（wrapper・宣言）、再実装、BIOS 側が混ざる項目は a（呼出側または再実装）と b（BIOS 側）に分け、各行に確度を一つだけ付ける。

| # | 項目 | 結論 | 確度 | 出典 | 備考・決着条件 |
| --- | --- | --- | --- | --- | --- |
| 1 | エントリ番地（名前 → `$E030`） | `$E030` と宣言されている | SOURCE_FACT | H1 | 呼出側の宣言。実行可能な callee で `$E030` に入ることを確認する（DEV ROM の `$E030` は BIOS ではない）。 |
| 2 | 本体番地（版別） | 版別の番地の候補 | HYPOTHESIS | P1 のみ | 第三者の記述のみ。実行しない限り HYPOTHESIS。 |
| 3a | 入力 A（呼出側） | wrapper は A を BIOS へ渡す | SOURCE_FACT | H5 502–507 | 呼出側の記述。 |
| 3b | 入力 A（BIOS） | BIOS が A を読むか不明 | UNKNOWN | — | 呼出先で A を変えた感度試験。 |
| 4a | 入力 X（呼出側） | wrapper は X に触れない | SOURCE_FACT | H5 | 呼出側の記述。 |
| 4b | 入力 X（BIOS） | BIOS が X を読み書きするか不明 | UNKNOWN | — | 同上。 |
| 5a | 入力 Y（呼出側） | Y は selector（下位バイト `$30`） | SOURCE_FACT | H4、H5 | 呼出側の記述。 |
| 5b | 入力 Y（BIOS） | BIOS 内の Y の使用は不明 | UNKNOWN | — | Y の変更でエントリが変わるかを確認。 |
| 6 | 入力 P | 呼出側の P の扱いの記述なし | UNKNOWN | — | 出典不足。 |
| 7 | 引数（C 宣言） | 引数なし（`void`） | SOURCE_FACT | H2、H3 | 呼出側の宣言。BIOS が引数を読まない証拠ではない。 |
| 8a | 戻り値（呼出側） | マクロ・宣言に戻り値がなく、呼出側は読まない | SOURCE_FACT | H2、H3 | 呼出側の記述。 |
| 8b | 戻り値 A/X/Y/P（BIOS） | BIOS の戻り値は不明 | UNKNOWN | — | 戻り値の実測。 |
| 9 | 破壊されるレジスタ | 呼出側のコメントは「使うものを使う」のみ。規定なし | UNKNOWN | H5（492 行） | 呼出前後のレジスタ差分。 |
| 10a | 保存（wrapper） | wrapper は MPR7 を保存・復元する。戻りの A は BIOS 後の値（502–514 行） | SOURCE_FACT | H5 | 呼出側の記述。 |
| 10b | 保存（BIOS） | BIOS が A/X/Y/P を保存するか不明 | UNKNOWN | — | 呼出前後の A、X、Y、P、MPR7 の比較。 |
| 11a | スタック（wrapper） | 入口で A を push し、出口で pull する | SOURCE_FACT | H5 | 呼出側の記述。 |
| 11b | スタック（BIOS） | BIOS のスタック使用は不明 | UNKNOWN | — | SP の前後比較。 |
| 12a | MPR（wrapper） | MPR7 を System Card のバンクに割り当て、戻りで復元する | SOURCE_FACT | H5 | 呼出側の記述。 |
| 12b | MPR（BIOS） | BIOS が他の MPR を変えるか不明 | UNKNOWN | — | MPR0〜7 の前後比較（成功・失敗経路）。 |
| 13a | 作業 RAM（再実装） | E1 の再実装は作業 RAM を書かない | HYPOTHESIS | E1 | 再実装の記述で BIOS の記述ではない。 |
| 13b | 作業 RAM（BIOS） | BIOS の RAM 書込みは不明 | UNKNOWN | — | RAM 全差分。 |
| 14a | ハードウェア副作用（再実装） | E1 は制御レジスタに reset を書き、DMA と再生速度を 0 にし、IRQ マスクの一部を clear する | HYPOTHESIS | E1、E2 | 再実装の内容。「BIOS も同じ」は開発者の主張。 |
| 14b | ハードウェア副作用（BIOS） | BIOS の書込みは不明 | UNKNOWN | — | 実行前後のハードウェアレジスタ差分。 |
| 15a | フラグ（wrapper） | wrapper は `pla` で A を戻すため、戻り時の N/Z は A に従う | SOURCE_FACT | H5 | 呼出側の記述。 |
| 15b | フラグ（BIOS） | BIOS 後の P は不明 | UNKNOWN | — | 戻り時の P の比較。 |
| 16a | IRQ（再実装） | E1 は IRQ マスクの一部を clear する | HYPOTHESIS | E1 | 再実装の内容。 |
| 16b | IRQ（BIOS） | BIOS が割込みを禁止・許可するか不明 | UNKNOWN | — | I フラグ、IRQ マスクの前後比較。 |
| 17 | タイムアウト | 出典に待機ループの記述がない | UNKNOWN | — | 完了条件の観測（busy の解除まで）。 |
| 18 | 失敗結果（errors） | エラー規約の記述がない（宣言とマクロのみ） | UNKNOWN | H2、H3 | 失敗条件での戻り値とフラグの実測。 |
| 19a | 同期（再実装のコメント） | E1 は、OKI チップが reset を 2 クロック以上保持する必要があると述べる | HYPOTHESIS | E1 56–60 行 | 開発者の記述。 |
| 19b | busy・同期（BIOS） | BIOS の busy 状態と同期は不明 | UNKNOWN | — | reset 保持時間と busy の解除の観測。 |

## 5. 確度の集計

行は 31 件（上表を数えた値）。SOURCE_FACT は呼出側の事実（出典の provenance 状態とは独立）で、BIOS の振る舞いの証拠ではない。

| 確度 | 件数 | 行 |
| --- | --- | --- |
| SOURCE_FACT | 10 | 1、3a、4a、5a、7、8a、10a、11a、12a、15a |
| HYPOTHESIS | 5 | 2、13a、14a、16a、19a |
| UNKNOWN | 16 | 3b、4b、5b、6、8b、9、10b、11b、12b、13b、14b、15b、16b、17、18、19b |

合計 31 行（10 + 5 + 16）。BIOS 側の行（b）と出典不足の行はすべて UNKNOWN で、BIOS の振る舞いに SOURCE_FACT はない。

## 5a. 証拠の区分（レビュー・CI・実測）

| 区分 | 状態 | 内容 |
| --- | --- | --- |
| 独立レビュー | NOT_RUN | 別エージェントによる台帳レビューは行っていない。 |
| provenance 審査 | PENDING | §2 の全出典はレビュア・決定日が未記録。 |
| 関連 CI | 実施（台帳の形式のみ） | 本 PR は `tests/host/test_ad_reset_source_fact.py` を追加する。実行: `python -I -m unittest discover -s tests/host -p test_ad_reset_source_fact.py -v`（`python -I` では `tests.host...` 形式の import はできない）。`make test` は `$(PYTHON) -m unittest discover -s tests/host -v`（Makefile 16 行）で `tests/host` を探索するため、同じテストを実行する。CI ワークフローは変更しない。検査内容: §1 の SOURCE_FACT 定義の文言、§4 の表の行数 31 と確度別件数（SOURCE_FACT 10 / HYPOTHESIS 5 / UNKNOWN 16）、b 行（BIOS 側）に SOURCE_FACT がないこと、SOURCE_FACT の段落に APPROVED を条件とする記述がないこと。検査しない内容: 出典の内容の正しさ、行番号、BIOS の振る舞い、b の接尾辞を持たない BIOS 側の行（2、6、9、17、18）の分類。 |
| 実測（callee 実行） | NOT_RUN | System Card の AD_RESET を実行していない（BLOCKED_ABI）。 |
| 固定版ソースの読取り | 実施 | §2 の commit と SHA-256 を確認し、`grep` と `sed -n` で行番号を確認した（展開物は実行していない）。 |

## 6. 実装に影響する残る UNKNOWN

- **入力 A/X/Y/P と戻り値（行 3b〜8b）**: 呼出先が読むか書くかを知らないと、呼出側が `ad_reset` の前後で何を保持すべきかを決められない。
- **busy 状態と完了条件（行 17、19b）**: reset の後に ADPCM 再生・転送を始めてよい条件が無い。`ad_trans` や `ad_play` の前提が決まらない。
- **失敗結果（行 18）**: 失敗の表現がないため、呼出側の成否判定を作れない。
- **IRQ とフラグ（行 15b、16b）**: 割込みの状態が reset 後にどう変わるかが不明で、IRQ を使う経路の前提が決まらない。

これらは、実行可能な System Card の AD_RESET（callee）を得た後、行 3b〜19b の観測を同じ条件で実測することで決着する。この PR は、その実測を行わない。

## 7. 境界

- 本台帳は呼出先（callee）を実行していない。BLOCKED_ABI は解除しない。
- `release_eligible=false` を維持する。`DEV_CALLABLE` にはしない。
- VERIFIED、PASS、互換の主張はしない。callee 側は未実測（NOT_RUN）のため未検証である。出典の provenance 審査は全件 PENDING で、これは別の問題である。
- System Card の BIOS バイト、逆アセンブル、市販媒体のデータは、本台帳に含めない（`AGENTS.md` と `docs/provenance.md` の除外）。
- 固定版のソースは、読取りと行番号の確認のみに使った。展開物を実行していない。
- 共有ファイル（`spec/api_contracts.json`、`docs/api-inventory.md`、`docs/status.md`、`Makefile`）は変更していない。

## 8. 次の作業

1. provenance 審査（maintainer）: §2 の全出典に APPROVED / PENDING / RESTRICTED を付け、レビュア・日付を記録する。`include/huc/startup.asm` と `include/huc/cdrom.asm`（H の呼出側の最も有用な例）の採否も、この審査で判断する。判断が出るまで、その内容は根拠にしない。
2. 実行可能な AD_RESET callee を得た後、§4 の行 3b〜19b を同じ条件で測定する。
3. Stum 2025 の本体番地（行 2）は、実行して確認するまで HYPOTHESIS のままとする。
