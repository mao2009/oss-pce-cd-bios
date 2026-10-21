# Issue #302: BM_FORMAT 呼出側ABI台帳（破壊的操作境界と成功/異常返値）

Refs #302 / 親 #34 / 関連 #303（合成FORMAT安全モデル）/ #304（診断、本PRでは未実施）。
状態: **調査のみ。実装なし。VERIFIED なし。release_eligible=false。**

## 0. 範囲と分類ルール

* 対象は BM_FORMAT（候補 `$18` / `$00:E048`）。BM_FREE は前提確認のみ（§2 C4）。
* 状態語彙: `CONFIRMED-by-source`（固定リビジョンの公開ソース行で確認した**呼出側の記述・コード**の事実。callee の挙動は含まない）、`HYPOTHESIS`（名前・コメントからの推定）、`UNKNOWN`（公開ソースに記載なし）、`BLOCKED_ABI`（診断に必要な事実が欠ける）。
* 本書の値は公開 HuCC ソース（OSS、MIT 系の SDK）の呼出側ラッパーに由来する。System Card 本体（純正 BIOS）の実装は参照していない。純正 BIOS のバイト列・逆アセンブルは扱わない。
* 合成 image の署名 `SYNBM` と最小容量 64 バイトは `tools/bm_format_model.py`（#395）のプロジェクト内部値であり、System Card の値ではない。本書では根拠に使わない。

## 1. 出典（固定リビジョン・sha256 照合済み）

固定リビジョン: HuCC `pce-devel/huc@54f5c73606c1e7142e8095c727cab2767e2b3cb3`。
取得後にローカルで sha256 を計算し、`spec/api_contracts.json` と `docs/research/148-cd-read-abi.md` の記録値と一致することを確認した。

| 出典 (key) | 種別 | sha256 照合 | 用途 |
| --- | --- | --- | --- |
| `huc:include/hucc/pcengine.inc` | CALLER（宣言） | 一致 `b6a25d33…728a9` | `bm_format = $00:E048, FUNC`（L494）、`bm_free`=`$E04B`（L495）、`IFU_BRAM_LOCK/UNLOCK`（L204-205）、`_ax`/`_al`（L329-330） |
| `huc:include/hucc/hucc-systemcard.asm` | CALLER（ラッパー） | 一致 `4258b7c3…ea9` | `_bm_format`（L675-698）、`_bm_free`（L713-722）、BM 定数（L54-61）、`_bm_check`（L615-）|
| `huc:include/hucc/hucc-systemcard.h` | CALLER（C 宣言） | 未取得 | 本台帳では未使用（NOT_RUN） |
| `hugo:bios.c` | HLE | 本書の対象外 | HLE の selector であり原本ではない（`api_contracts.json` slot 24 の注記どおり） |
| 第三者の BIOS offset 表 | 第三者 | 未取得 | `$E048` の入口番地の二次確認は未実施（UNKNOWN） |

## 2. 呼出側使用台帳

各行は呼出側ラッパーの行番号（`hucc-systemcard.asm`）を指す。BIOS 側の挙動は別表（§3）。

| id | 項目 | 呼出側の事実（引用の言い換え） | 行 | 状態 |
| --- | --- | --- | --- | --- |
| C1 | 入口 | `bm_format = $00:E048, FUNC` と宣言。ラッパーは `system bm_format` マクロで呼ぶ | pcengine.inc:494 / asm:687 | CONFIRMED-by-source（呼出側宣言）。BIOS 実入口との一致は HYPOTHESIS |
| C2 | 引数レジスタ | 引数は `_ax`（`$F8:20F8` の 2 バイト、低位先）にパスワードへのポインタを置く。A/X/Y の直接入力はない | asm:682-685 / pcengine.inc:329 | CONFIRMED-by-source（呼出側の書込み） |
| C3 | 破壊的操作の確認文字列 | 確認文字列は `"!BM FORMAT!"`（11 バイト、`db`、NUL 終端なし）。BIOS がこの文字列を比較するか、比較長、不一致時の返値は未確認 | asm:698 | 文字列の存在は CONFIRMED-by-source。BIOS 側の検証は UNKNOWN |
| C4 | 事前確認 | ラッパーは先に `system bm_free` を呼ぶ。A（`bm_free` の返値）が 0 なら**フォーマットせず** BM_OK で返す。非 0 の場合のみ `bm_format` を呼ぶ | asm:677-680 | CONFIRMED-by-source（呼出側の分岐）。「0=フォーマット済み」の意味は HYPOTHESIS |
| C5 | 成功時返値 | `bm_format` の A が 0 なら X=0（BM_OK）で返す | asm:687-691 | CONFIRMED-by-source（ラッパーの扱い）。BIOS の実返値は UNKNOWN |
| C6 | 異常時返値 | `bm_format` の返値が非 0 なら X=`$FF`（BM_NOT_FORMATTED）に置換して返す。BIOS が返した実コードは捨てられる | asm:692-694 / pcengine.inc:61 | CONFIRMED-by-source |
| C7 | 返値の二重記録 | `__bm_error` に返値を保存（`.ds 1`、asm:65）。戻り値は `leave` で X→A | asm:65, 694-696 | CONFIRMED-by-source |
| C8 | MPR | ラッパーは MPR を変更しない。`.password` のアドレスは `lda.l/.h #` で得るのみ。ポインタが BIOS から見える MPR 配置は UNKNOWN（`api_contracts` は HYPOTHESIS 扱い） | asm:682-685 | UNKNOWN（BIOS 側の読み取り経路） |
| C9 | 退避レジスタ | ラッパーは A/X/Y を破壊し、P/スタックは退避しない（`leave` で戻る）。BIOS 側の退避は UNKNOWN | asm:675-698 | CONFIRMED-by-source（ラッパーのみ） |
| C10 | 事前ロック解除 | `bm_format` 経路には `IFU_BRAM_UNLOCK`（`$FF:1807`）書込みがない。解除シーケンスは `_bm_check`（asm:615-660）側にある。BIOS が内部で解除するかは UNKNOWN | pcengine.inc:204-205, asm:626-630 | CONFIRMED-by-source（呼出側の不在）。BIOS 側は UNKNOWN |
| C11 | 容量要件 | 呼出側には容量の前提がない（`bm_format` は長さを渡さない）。最小容量の記載なし | asm:667-673 | UNKNOWN（64 バイトは #395 の内部値であり、根拠にしない） |
| C12 | 破壊範囲 | 呼出側コメントは「フォーマット済みなら OK、そうでなければフォーマット」とのみ書く。何が消えるか（ディレクトリのみか、全領域か）は記載なし | asm:669 | UNKNOWN |

### 2.1 呼出側ロジックから読み取れる境界（HYPOTHESIS）

* 呼出側の唯一の二段防御は「`bm_free` が非 0 のときだけ `bm_format` を呼ぶ」（C4）と「確認文字列をポインタで渡す」（C2、C3）。
* `bm_free` の非 0 はすべてフォーマット対象として扱われる。`__bm_error` の定数（pcengine.inc:54-61）には `BM_BAD_CHECKSUM=2`、`BM_DIR_CORRUPTED=3` などもあるため、**破損しているが利用者データが残るメディアでも、呼出側ラッパーは非 0 を見てフォーマットへ進む**。これは HYPOTHESIS（BIOS が破損時にどの値を返すかが UNKNOWN のため）。
* したがって「破壊的操作の確認」は呼出側では bm_free の 0/非 0 判定のみ。BIOS 側が確認文字列を検証するかどうかが、直接 JSR した場合の境界を決める（UNKNOWN）。

## 3. BIOS 側（callee）の状態

| id | 項目 | 状態 | 理由 |
| --- | --- | --- | --- |
| B1 | 実行可能な System Card 上での呼出観測 | UNKNOWN | 固定 Geargrafx 実コアでの BM_FORMAT 実行証拠はゼロ |
| B2 | 成功時の A/X/Y/P | UNKNOWN | 呼出側は A==0 のみ見る |
| B3 | 異常時の返値の種類（確認不一致、未フォーマット、容量不足など） | UNKNOWN | 呼出側ヘッダは BM_NOT_FOUND を返すと書くが、コードは返さない（C6）。BIOS の実値は未観測 |
| B4 | 確認文字列の比較長と不一致時の挙動 | UNKNOWN | 呼出側の文字列のみ |
| B5 | 書換え範囲（何バイトを消すか、既存ファイルの扱い） | UNKNOWN | 呼出側に記載なし |
| B6 | MMIO 副作用（`$1803-$1807` の解除・施錠） | UNKNOWN | C10 参照。`docs/173` のゲートでは MMIO 書込みを事前阻止できない |
| B7 | 割込み・完了条件・所要時間 | UNKNOWN | 記載なし |

## 4. 実コア診断に対する判定（#304）

**判定: BLOCKED_ABI（診断は NOT_RUN。ログなし）。**

理由:

1. **返値の BIOS 側事実が欠ける**（B2、B3）。正例の成功判定と負例の異常判定の基準が、呼出側の「0 か非 0 か」しかない。
2. **確認の強制力が不明**（B4）。負例「確認なし」が BIOS 上で拒否されるかを観測の前に決められない。
3. **破壊範囲が不明**（B5）。合成 image でも、どの領域が消えたかを判定するための期待値を定義できない。
4. **安全ゲートが成立しない**。`docs/173-safety-monitor-limits.md` の結論どおり、固定 Geargrafx は MMIO 書込みを副作用前に阻止する汎用フックを持たず、`slot-1e` 以外の BIOS JSR 実行も BLOCKED のまま。BM_FORMAT の JSR が `$1803-$1807` 系の MMIO 副作用を起こすかは未確認（B6）だが、起こしうる場合にゲートで防げないため、ゲートが成立していない。
5. 純正 BIOS は公開 CI に存在しない（private）。ローカル実コアの前提も本 PR では満たさない。

本 PR では **実コアを起動しない**。合成 image の診断ハーネスも作らない（前提の BIOS 側事実が無く、期待値を定義できないため）。

## 5. 発見した不整合

* `hucc-systemcard.asm:671` のコメントは「BM_OK, BM_NOT_FOUND」を返すと書くが、コードは `BM_NOT_FORMATTED`（$FF）を返す（asm:692）。呼出側ドキュメントの誤記の可能性（HYPOTHESIS）。
* `pcengine.inc:60` の `BM_NOT_FORMATED` は綴り誤り（HuC 側、asm:60 のコメントに明記）。定数は `$FF` で同値。

## 6. 次のステップ

* BIOS 側の事実（B2-B5）は、ライセンス確認済みの私的 System Card を使う別 Issue（maintainer 許可のもの）でのみ観測可能。公開リポジトリ内では観測しない。
* #304 は、B2-B5 が観測で埋まるまで BLOCKED_ABI のまま。`api_contracts.json` の slot 24 は、本 PR では変更しない（ledger 変更禁止）。
* 呼出側だけで既に決まっている事実（C1-C12）は、`api_contracts.json` の更新案として別途提案する（本 PR の範囲外）。

## 7. 状態集計

台帳行は C1-C12（呼出側 12 行）と B1-B7（BIOS 側 7 行）の計 19 行。

| 状態 | 行数 | 内訳 |
| --- | --- | --- |
| CONFIRMED-by-source | 9 | C1, C2, C3*, C4, C5, C6, C7, C9, C10*（* は呼出側部分のみ。BIOS 側は UNKNOWN） |
| UNKNOWN | 10 | C8, C11, C12, B1-B7 |
| HYPOTHESIS（行内の注記） | 3 | C1 の BIOS 入口一致、C4 の「0=フォーマット済み」、§2.1 の破損時分岐 |
| BLOCKED_ABI（診断） | 1 | #304 の実コア診断 |

VERIFIED は 0。release_eligible=false。
