# CD_FADE（`$0F`、候補 `$E02D`）呼出側 ABI・戻り値の出典別裁定（Issue #282）

親 Issue: #24（CD_FADE）。台帳の形式は [cd-base-abi-ledger.md](../cd-base-abi-ledger.md) に従う。

## 1. 結論

- 本書は公開ソースの文書調査のみ。System Card の CD_FADE は実行していない。実行可能な呼出先がないため **BLOCKED_ABI を維持**する。
- `release_eligible=false`。`DEV_CALLABLE` は付けない。BIOS 側の振る舞いに対する実行証拠はないため、VERIFIED や PASS は主張しない。
- 記述できるのは **呼出側（HuCC の固定版）の ABI を仮説として整理したもの** だけである。入口番地 `$00:E02D`、入力 A（type）、呼出し手順、void 宣言は HYPOTHESIS（呼出側の記述。出自が PENDING の出典に基づく）として扱う。
- BIOS 側の戻り値、フラグ、作業 RAM、MPR/SP の副作用、IRQ、完了条件、タイムアウトは UNKNOWN または HYPOTHESIS にとどめる。
- **CD-DA（CD 音楽）の音量フェードが対象かは UNKNOWN。** 公開出典が互いに矛盾する（F13）。CD-DA 対象を根拠付きで示せる出典はない。

## 2. 証拠区分

- `SOURCE_FACT`: APPROVED（人間が裁定済みで、承認者と日付がある）出典の本文に書かれている事実のみ。現時点で APPROVED の出典は無いため、本書の SOURCE_FACT は 0 件。PENDING の出典（S1–S10）だけに基づく事実は HYPOTHESIS または UNKNOWN とする。BIOS の振る舞いの証拠にはしない。
- `HYPOTHESIS`: 出典からの推測。未実測。
- `UNKNOWN`: 出典が不足している。推測で埋めない。
- 本書では実行証拠を示す区分を使わない。OBSERVED と PASS は、実行可能な呼出先の測定を経てから付ける。

## 3. 出典（固定版・取得日 2026-10-19）

HuC は `pce-devel/huc` の commit `54f5c73606c1e7142e8095c727cab2767e2b3cb3` を使う。アーカイブ SHA-256 は `9fe91254051381077bd4ea217b718ec033a52da185283ae9479c1abac365311b`（台帳 §2 と一致）。
展開物は読み取りのみで、実行していない。

| ID | 出典（固定版内のパス） | 行 | ライセンス | 本書での用途 | 状態 |
| --- | --- | --- | --- | --- | --- |
| S1 | `include/hucc/pcengine.inc` | 202、215–221、483 | BSL-1.0（ヘッダー 8 行目） | 入口定義 `cd_fade = $00:E02D`、IFU_AUDIO_FADE の注釈 | 転載なし。行番号と要約のみ。出自は PENDING |
| S2 | `include/hucc/hucc-systemcard.asm` | 41–46（CDFADE 定数）、119–128（宣言コメントとマクロ） | BSL-1.0（John Brandwood 2024） | 入力 A、許容値、時間の呼出側記述 | PENDING（S1 と同じ） |
| S3 | `include/hucc/hucc-systemcard.h` | 13、92 | BSL-1.0 | `void` 宣言（入力 `<acc>`） | PENDING（S1 と同じ） |
| S4 | `include/hucc/core.inc` | 41–44（`system` マクロ） | BSL-1.0（John Brandwood 2021–2024） | Y に入口の下位バイトを入れて call_bios を呼ぶ | PENDING（S1 と同じ） |
| S5 | `include/hucc/core-kernel.asm` | 488–516（`call_bios`） | BSL-1.0（同上） | MPR7 の切替え、A の保存、戻り値の経路 | PENDING（S1 と同じ） |
| S6 | `doc/huc/whats.new` 462、`doc/huc/huc3_doc.htm` 124 | 一行 | HuC ルート `LICENSE` は「混在・不確実」と記す | 「ADPCM/PCM のみ、CD 音楽ではない」（462）と「PCM と ADPCM の音楽をフェードする」（124）の矛盾 | PENDING。根拠にしない。矛盾の記録のみ |
| S7 | `doc/hucc/hucc-function-reference.md` 930–931 | 一行 | 未確認（PENDING） | 「CD-ROM 音声をフェードする」という記述 | PENDING。矛盾の記録のみ |
| S8 | Zeograd System Card notes（`https://www.zeograd.com/download/pce_bios.html`）、「CD_FADE, Function #$0F」 | 325–343（OUT は 338、その値は 340 行の N/A） | 表示なし（PENDING） | 入力の値一覧、「6.0 秒／2.5 秒」、戻り値 N/A の注記 | PENDING。部分的に RE に基づくノート。証拠にしない |
| S9 | Stum 2025 offsets（`https://www.stum.de/2025/pcenginebiosoffsets/`） | 212–213 | 文章 CC BY-NC-SA 4.0 | `cd_fade` の入口 `$E02D` の一致 | PENDING。地図としての参照のみ。revision 別の内部アドレスは記載しない |
| S10 | Hu-Go `bios.c` @`64ed226da2288738781743eeef228c069e8973a4` | 50、184–185 | GPL-2（参照のみ） | `CD_FADE 0x0F` の定義と名前文字列のみ。処理本体は見つからない | PENDING。HLE であり、BIOS の証拠にしない。コードは転載しない |

除外: `include/huc/`（`cdrom.inc`、`system.inc`）と `examples/asm/elmer/kickc/bios.h` は、ライセンスが不明なため内容を根拠にしない（台帳 S6 と同じ扱い）。
`./rom/` は読んでいない。BIOS バイト列、逆アセンブルは使っていない。

## 4. フィールド裁定（フィールド表）

呼出側（HuC 固定版）の記述に基づく行と、BIOS 側（呼出先）の未検証の振る舞いの行を分けて読むこと。呼出側の行は最大でも HYPOTHESIS、呼出先の行（F02、F07、F10、F12、F13、F16、F18、F19、F22、F24、F27、F28、F31–F34、F36）は HYPOTHESIS か UNKNOWN で、SOURCE_FACT にしない。

| ID | 項目 | 判定 | 根拠 | 備考 |
| --- | --- | --- | --- | --- |
| F01 | 呼出側が定義する入口 `$00:E02D` | HYPOTHESIS | S1 483 | 呼出側の定義。BIOS 実体の証拠ではない |
| F02 | BIOS の実体が `$E02D` で入口として動くか | UNKNOWN | — | 実行可能な呼出先がない（BLOCKED_ABI）。S8・S9 の一致はノート間の一致のみ |
| F03 | 呼出し経路（Y に下位バイト、`call_bios` 経由の JSR） | HYPOTHESIS | S4 41–44、S5 500–509 | マクロ `system` が Y を設定して `call_bios` を呼ぶ |
| F04 | 呼出し中の MPR7 | HYPOTHESIS | S5 502–513 | 呼出し中は MPR7 をバンク `$00` に切替え、呼出し後に元へ戻す |
| F05 | 入力 A は type | HYPOTHESIS | S2 119–128、S3 92 | `unsigned char type<acc>` |
| F06 | 入力の許容値 `$00`/`$08`/`$0A`/`$0C`/`$0E` | HYPOTHESIS | S2 42–46 | 呼出側の定義一覧のみ。S8（PENDING）は根拠にしない |
| F07 | 許容値以外の入力の挙動 | UNKNOWN | — | 出典に記述がない |
| F08 | 値 `$00` が「フェードの解除」を意味するか | HYPOTHESIS | S2 121、S8 331 | 呼出側記述と S8 注記の一致のみ。BIOS の挙動は未確認 |
| F09 | 呼出側が記す時間（`$08`/`$0A` は 6 秒、`$0C`/`$0E` は 2.5 秒） | HYPOTHESIS | S2 122–125 | 呼出側の記述であり、BIOS の実測ではない |
| F10 | BIOS が実際にその時間でフェードするか | HYPOTHESIS | S8 332–335 | 呼出側記述と S8 ノートの一致のみ |
| F11 | 呼出側が PCM と ADPCM を対象と記すこと | HYPOTHESIS | S2 121–125 | 呼出側の注釈の読みのみ。S6（PENDING、124 行は S6 462 と矛盾）は根拠にしない |
| F12 | 「PCM」が指す信号経路の実体 | UNKNOWN | — | 出典に実体の記述がない |
| F13 | CD-DA（CD 音楽）音量フェードの対象か | UNKNOWN | S6 462、S7 930–931、S6 124 | 出典が矛盾（CD 音楽ではない／CD-ROM 音声をフェード）。呼出側の値に CD-DA 用の値はない |
| F14 | IFU の CD オーディオ選択ビットの注釈（`$FF:180F`、bit `$02` は ADPCM または CDAUDIO） | HYPOTHESIS | S1 202、215–218 | ハードウェア定義の注釈であり、cd_fade の挙動ではない |
| F15 | 値 `$08`–`$0E` と IFU ビット（TRIG `$08`、TYPE `$02`、SELN `$04`）の対応 | HYPOTHESIS | S1 217–221、S2 42–46 | ビットの一致のみ。cd_fade が IFU を書くことの証拠ではない |
| F16 | cd_fade が `$FF:180F` を書くか | UNKNOWN | — | 呼出側は IFU に触れない。BIOS 側は未確認 |
| F17 | 呼出側の戻り値の扱い（void、直後に A を読まない） | HYPOTHESIS | S2 119–128、S3 92 | 呼出側の宣言とマクロ |
| F18 | BIOS の戻り値（A/X/Y） | HYPOTHESIS | S8 340（OUT の値は N/A） | 戻り値なしの注記のみ。実測なし |
| F19 | BIOS のエラー返値とフラグ（C/I/D/V/T） | UNKNOWN | — | 出典に記述がない |
| F20 | 呼出し後の A | HYPOTHESIS | S5 502–514 | ラッパーが入力 A を保存して渡し、BIOS の出力 A を保存して戻す。出力 A は BIOS の A |
| F21 | 呼出し後の P のうち N と Z | HYPOTHESIS | S5 514 | 最後の `pla` が N と Z を決める。値は F20 の A による |
| F22 | 呼出し後の P のうち C/I/D/V/T | UNKNOWN | — | BIOS 依存 |
| F23 | ラッパーが X に触れるか | HYPOTHESIS | S5 500–516 | 触れない（ラッパー内に X の操作がない） |
| F24 | BIOS が X と Y を変更するか | UNKNOWN | — | 出典に記述がない |
| F25 | 呼出し前の Y | HYPOTHESIS | S4 41–44、S5 500 | Y は入口の下位バイトに使われる。呼出し後の Y は F24 |
| F26 | ラッパーが MPR0–6 に触れるか | HYPOTHESIS | S5 502–513 | 触れない（MPR7 のみ操作） |
| F27 | BIOS が MPR0–6 を変更するか | UNKNOWN | — | 出典に記述がない |
| F28 | BIOS の作業 RAM の使用範囲 | UNKNOWN | — | 出典に記述がない |
| F29 | スタックの対称性（ラッパー内の push と pull） | HYPOTHESIS | S5 502、507、511、514 | ラッパー自身は対称。BIOS のスタック使用量は UNKNOWN |
| F30 | ラッパーが I フラグを変更するか | HYPOTHESIS | S5 500–516 | 変更しない（SEI/CLI の記述なし）。対照的に EX_SETVEC は PHP/SEI を使う（S4 47–53） |
| F31 | BIOS の IRQ 副作用 | UNKNOWN | — | 出典に記述がない |
| F32 | 完了条件とタイムアウト | UNKNOWN | — | 呼出側は呼出し後に完了を待たない（S2 の宣言とマクロに待ち処理なし）。BIOS 側は未確認 |
| F33 | 無ディスク、再生中トラックの有無による挙動 | UNKNOWN | — | 出典に記述がない |
| F34 | フェード中の他の CD 呼出し（CD_PLAY 等）との相互作用 | UNKNOWN | — | 出典に記述がない |
| F35 | HuC 固定版内の cd_fade 呼出し | HYPOTHESIS | 固定版 `54f5c736` の `git grep`（`cd_fade`、`CDFADE_`、`E02D`、大小無視）。許容出典（S1–S5、BSL-1.0 の `include/hucc/`）に呼出しはなく、定義（S1 483、`pcengine.inc`）と呼出し手順を包むマクロ（S2 127–128、`hucc-systemcard.asm`）のみ | 除外源には呼出しがある: `include/huc/cdrom.asm` の 87 行（ライセンス不明のため根拠に使わない）。`examples/asm/elmer/kickc/bios.h` の 41 行は除外源の定義。`doc/` 内の一致は散文と宣言のみ |
| F36 | Hu-Go HLE の処理本体 | UNKNOWN | S10 50、184–185 | 定義と名前のみ。処理本体は見つからなかった。HLE であり BIOS の証拠ではない |

判定の数: SOURCE_FACT 0、HYPOTHESIS 21、UNKNOWN 15（合計 36 行。本表の判定列を数えた値）。S1–S5 の出自が PENDING のため、以前 SOURCE_FACT だった 15 行（F01、F03、F04、F05、F09、F14、F17、F20、F21、F23、F25、F26、F29、F30、F35）を HYPOTHESIS に、F36 を UNKNOWN に下げた。APPROVED の出典が裁定されるまで SOURCE_FACT は 0 件のままである。

## 5. 矛盾と決着条件

1. **CD-DA の対象（F13）**: S6（462）は CD 音楽向けでないと記し、S7（930–931）は CD-ROM 音声をフェードすると記す。S6（124）は PCM と ADPCM と記す。決着には、CD-DA 再生中に値 `$08`–`$0E` を与えたときの出力を実行可能な呼出先で測るしかない。
2. **入口 `$E02D` の BIOS 側の一致（F02）**: S1、S8、S9 の一致はすべてノートまたは定義で、BIOS の実行証拠ではない。
3. **時間（F09、F10）**: 呼出側記述と S8 は同じ数値を示すが、測定がない。
4. **戻り値（F18、F19）**: S8 の N/A は注記にとどまる。戻り値が無いとは言えない。
5. **IFU ビットの対応（F15、F16）**: ハードウェア注釈との一致は仮説。cd_fade の書込みを示す出典はない。

## 6. 検証

- 実行: `python3 -m unittest discover -s tests/host -p 'test_cd_fade_contract.py' -v`（WSL のクリーン clone。結果は PR 本文と Issue コメントに記す）。
- テストは、表の判定列が SOURCE_FACT、HYPOTHESIS、UNKNOWN のいずれか一つだけであること、表に OBSERVED、VERIFIED、PASS、DEV_CALLABLE が無いこと、`BLOCKED_ABI` と `release_eligible=false` の記載があることを確かめる。F01–F36 の判定はテスト内で一行ずつ固定し、件数（0/21/15）も確かめる。判定の昇格（F13 を SOURCE_FACT、F02 を HYPOTHESIS、F18 を SOURCE_FACT へ）と、PENDING 出典（S1–S10）を根拠にした SOURCE_FACT も失敗させる。
- NOT_RUN: `make setup`、`make build`、`make test`、`make check`（指示により実行しない）。実コア（Geargrafx）、実機、CI の最新 HEAD、独立レビューは未実施。

## 7. 限界と省いたもの

- Stum のリビジョン別内部アドレス（`$F3DA` 等）は記載しない。BIOS 内部の推定で、本書の範囲外。
- Zeograd と Stum はライセンスの裁定が PENDING。Hu-Go は GPL-2 で参照のみ。HuC の出典（S1–S5）も PENDING（台帳と同じ）。S1–S10 はいずれも APPROVED の裁定を受けていないため、本書に SOURCE_FACT は無い。
- 実行可能な System Card の CD_FADE 呼出先は無い。したがって F02、F07、F12、F16、F19 以降の BIOS 側項目はすべて未確定のまま残る。
- 本書は裁定台帳であり、ABI の正しさを示すものではない。実装・API 互換の主張はしない。
