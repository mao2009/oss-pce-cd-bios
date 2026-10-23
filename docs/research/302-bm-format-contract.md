# Issue #302: BM_FORMAT 呼出側ABI台帳（破壊的操作境界と成功/異常返値）

Refs #302 / 親 #34 / 関連 #303（合成FORMAT安全モデル）/ #304（診断、本PRでは未実施）。
状態: **調査のみ。実装なし。VERIFIED なし。release_eligible=false。**

## 0. 範囲と分類ルール

* 対象は BM_FORMAT（候補 `$18` / `$00:E048`）。BM_FREE は前提確認のみ（§2 C4）。
* 状態語彙: `CONFIRMED-by-source`（固定リビジョンの公開ソース行で確認した**呼出側の記述・コード**の事実。callee の挙動は含まない）、`HYPOTHESIS`（名前・コメントからの推定）、`UNKNOWN`（公開ソースに記載なし）、`BLOCKED_ABI`（診断に必要な事実が欠ける）。
* 各表の行は状態を 1 つだけ持つ。複数の事実を含む項目は行を分けた（a/b/c の接尾辞）。
* 本書の値は公開 HuCC ソース（`pce-devel/huc` @ `54f5c73`）の呼出側ラッパーに由来する。HuCC のソースはファイルごとにライセンスが異なり、一括して「MIT 系」とは言えない。固定リビジョンで確認した事実:
  * `include/hucc/pcengine.inc`: ヘッダ 8-10 行「Distributed under the Boost Software License, Version 1.0.」。
  * `include/hucc/hucc-systemcard.asm`: ヘッダ 10-12 行に同じ Boost 1.0 の文言。著作権表示「Copyright John Brandwood 2024」。
  * `include/hucc/hucc-systemcard.h`: ヘッダ 13-15 行に同じ Boost 1.0 の文言。
  * リポジトリ直下 `LICENSE`（45 行）: 単一のライセンスではない。冒頭で「コンパイラとアセンブラの法的地位について正確な記述は困難」と述べる。MagicKit アセンブラは「freeware」と記述。`tgemu/src/license` は GPL バージョン 2 の全文。テストケースは GCC 由来として GPL と記述。Ulrich Hecht による変更部分は Simplified BSD と記述。
  * コンパイラ本体 `src/huc/code.c`: ライセンスヘッダなし。GPL と判定できる記述を確認できないため、ライセンスは **UNKNOWN**。
* 合成 image の署名 `SYNBM` と最小容量 64 バイトは `tools/bm_format_model.py`（#395）のプロジェクト内部値であり、System Card の値ではない。本書では根拠に使わない。
* System Card 本体（純正 BIOS）の実装は参照していない。純正 BIOS のバイト列・逆アセンブルは扱わない。

## 1. 出典（固定リビジョン・sha256 照合済み）

固定リビジョン: HuCC `pce-devel/huc@54f5c73606c1e7142e8095c727cab2767e2b3cb3`。
取得後にローカルで sha256 の完全値を計算し、`spec/api_contracts.json` の記録値（完全値）と一致することを確認した。表の略記は先頭 8 桁と末尾の数桁。

| 出典 (key) | 種別 | sha256 照合 | 用途 |
| --- | --- | --- | --- |
| `huc:include/hucc/pcengine.inc` | CALLER（宣言） | 一致（完全値 `b6a25d33…728a9`、api_contracts.json:12） | `bm_format = $00:E048, FUNC`（L494）、`bm_free`=`$E04B`（L495）、`IFU_BRAM_LOCK/UNLOCK`（L204-205）、`_ax`/`_al`（L329-330） |
| `huc:include/hucc/hucc-systemcard.asm` | CALLER（ラッパー） | 一致（完全値 `4258b7c3…ea9`、api_contracts.json:21） | `_bm_format`（L675-698）、`_bm_free`（L713-722）、BM 定数（L54-61）、`_bm_check`（L615-）|
| `huc:include/hucc/hucc-systemcard.h` | CALLER（C 宣言） | 一致（完全値 `f2477642f867c3daeff00938534f3f8ef970d1a9403f44bd09e90894220b8a3b`、api_contracts.json:30） | BM 定数（L31-38）と `bm_format` 宣言（L111）を根拠として引用する。ラッパー本体の根拠は asm 行のみ。引用行は下記 |
| `hugo:bios.c` | HLE | 本書の対象外 | HLE の selector であり原本ではない（`api_contracts.json` slot 24 の注記どおり） |
| 第三者の BIOS offset 表 | 第三者 | 未取得 | `$E048` の入口番地の二次確認は未実施（UNKNOWN） |

`hucc-systemcard.h` の引用箇所（sha256 は上表のとおり一致を確認済み、ref 54f5c73 の L37, L38, L111 を読んで確認）:

* L38 `BM_NOT_FORMATTED 0xFF`: 行 C6 の補足（同値）。
* L31-38 BM 定数: §2.1 の境界説明。
* L37 `BM_NOT_FORMATED 0xFF`（綴り誤り）: §5 の不整合。
* L111 `bm_format` 宣言: 参照のみ。台帳の状態判定には使わない。

## 2. 呼出側使用台帳

各行の行番号は、特記しない限り `hucc-systemcard.asm` を指す。BIOS 側の挙動は別表（§3）。

| id | 項目 | 呼出側の事実（引用の言い換え） | 行 | 状態 |
| --- | --- | --- | --- | --- |
| C1a | 入口の宣言 | `bm_format = $00:E048, FUNC` と宣言する | pcengine.inc:494 | CONFIRMED-by-source |
| C1b | 呼出し経路 | ラッパーは `system bm_format` マクロで呼ぶ | asm:687 | CONFIRMED-by-source |
| C1c | BIOS 実入口との一致 | `$E048` が System Card の実入口と一致するか。第三者の番地表は未取得 | — | HYPOTHESIS |
| C2 | 引数レジスタ | 引数は `_ax`（`$F8:20F8` の 2 バイト、低位先）にパスワードへのポインタを置く。A/X/Y の直接入力はない | asm:682-685 / pcengine.inc:329 | CONFIRMED-by-source |
| C3a | 確認文字列の存在 | 確認文字列は `"!BM FORMAT!"`（11 バイト、`db` で定義、NUL 終端の指定なし） | asm:698 | CONFIRMED-by-source |
| C3b | BIOS 側の検証 | BIOS がこの文字列を比較するか、比較長、不一致時の返値。呼出側に情報なし | — | UNKNOWN |
| C4a | 事前確認の分岐 | `system bm_free` の結果 A を `tax` で X に写し、0 なら `.done` へ進み、bm_format を呼ばず X=0 で返す。非 0 の場合のみ `bm_format` を呼ぶ | asm:677-680 | CONFIRMED-by-source |
| C4b | 「0」の意味 | 0 を「フォーマット済み」とする解釈。呼出側の別箇所（`_bm_free` マクロ）は A≠0 を error として扱うため、0 の意味は呼出側だけでは一意に決まらない | asm:677-680 / asm:714-718 | HYPOTHESIS |
| C5a | 成功時の返値（呼出側） | `bm_format` の A が 0 なら X=0（BM_OK）で返す | asm:687-691 | CONFIRMED-by-source |
| C5b | BIOS の実返値 | 成功時に BIOS が返す A/X の値。呼出側は A==0 のみ見る | asm:687-690 | UNKNOWN |
| C6 | 異常時の返値（呼出側） | `bm_format` の A が非 0 なら X=`$FF`（BM_NOT_FORMATTED）に置換して返す。BIOS の実コードは X に残らない | asm:692-694 / 定義 asm:61（同値が hucc-systemcard.h:38） | CONFIRMED-by-source |
| C7 | 返値の記録（呼出側） | `__bm_error` は asm:688 で BIOS の A を保存するが、asm:694 の `stx` で X（`$FF` または 0）に上書きされる。BIOS の実コードは `__bm_error` にも残らない。`.ds 1` は asm:65、戻りは `leave` で X→A（asm:696） | asm:65, 688, 694-696 | CONFIRMED-by-source |
| C8a | MPR（呼出側） | ラッパーは MPR を変更しない（tma/tam 命令なし）。`.password` のアドレスは `lda.l/.h #` で得るのみ | asm:675-698 / asm:682-685 | CONFIRMED-by-source |
| C8b | MPR（BIOS 側） | ポインタが BIOS から見える MPR 配置。未観測 | — | UNKNOWN |
| C9a | 退避レジスタ（呼出側） | ラッパーは A/X/Y を書き換え、P を退避せず（php/plp なし）、スタックも退避しない（`leave` で戻る） | asm:675-698 | CONFIRMED-by-source |
| C9b | 退避レジスタ（BIOS 側） | BIOS が A/X/Y/P を退避するか（B2 参照） | — | UNKNOWN |
| C10a | 解除シーケンス（呼出側） | ラッパー本文に `IFU_BRAM_UNLOCK`（`$FF:1807`）への書込みはない。解除シーケンスは `_bm_check` 側にある | asm:675-698 / pcengine.inc:205 / asm:625-630 | CONFIRMED-by-source |
| C10b | BIOS の MMIO 解除 | BIOS が内部で `$1807` 書込みによる解除、`$1803` による施錠を行うか | — | UNKNOWN |
| C11 | 容量要件 | 呼出側は長さを渡さず、容量の前提も記載しない。最小容量 64 バイトは #395 の内部値であり根拠にしない | asm:667-673 | UNKNOWN |
| C12 | 破壊範囲 | 呼出側コメントは「フォーマット済みなら OK、そうでなければフォーマット」とのみ書く。何が消えるかは記載なし | asm:669 | UNKNOWN |

### 2.1 呼出側ロジックから読み取れる境界（HYPOTHESIS）

* 呼出側の唯一の二段防御は「`bm_free` が非 0 のときだけ `bm_format` を呼ぶ」（C4a）と「確認文字列をポインタで渡す」（C2、C3a）。
* `bm_free` の非 0 はすべて `bm_format` 呼出しへ進む（C4a）。`BM_*` 定数（hucc-systemcard.asm:54-61、hucc-systemcard.h:31-38 にも同値）には `BM_BAD_CHECKSUM=2`、`BM_DIR_CORRUPTED=3` などもあるため、**破損しているが利用者データが残るメディアでも、呼出側ラッパーは非 0 を見てフォーマットへ進む**。これは HYPOTHESIS（BIOS が破損時にどの値を返すかが UNKNOWN のため）。
* したがって「破壊的操作の確認」は呼出側では `bm_free` の 0/非 0 判定のみ。BIOS 側が確認文字列を検証するかどうかが、直接 JSR した場合の境界を決める（UNKNOWN）。

## 3. BIOS 側（callee）の状態

| id | 項目 | 状態 | 理由 |
| --- | --- | --- | --- |
| B1 | 実行可能な System Card 上での呼出観測 | UNKNOWN | 固定 Geargrafx 実コアでの BM_FORMAT 実行証拠はゼロ |
| B2 | 成功時の A/X/Y/P | UNKNOWN | 呼出側は A==0 のみ見る（C5b） |
| B3 | 異常時の返値の種類（確認不一致、未フォーマット、容量不足など） | UNKNOWN | 呼出側ラッパーのコメント（asm:671）は BM_NOT_FOUND を返すと書くが、コードは BM_NOT_FOUND を返さず `$FF` に置換する（C6）。BIOS の実値は未観測 |
| B4 | 確認文字列の比較長と不一致時の挙動 | UNKNOWN | 呼出側の文字列のみ（C3b） |
| B5 | 書換え範囲（何バイトを消すか、既存ファイルの扱い） | UNKNOWN | 呼出側に記載なし（C12） |
| B6 | MMIO 副作用（`$1803-$1807` の解除・施錠） | UNKNOWN | C10b 参照。`docs/173` のゲートでは MMIO 書込みを事前阻止できない |
| B7 | 割込み・完了条件・所要時間 | UNKNOWN | 記載なし |

## 4. 実コア診断に対する判定（#304）

**判定: BLOCKED_ABI（診断は NOT_RUN。ログなし）。** 本判定は台帳の行数には含めない。

理由:

1. **返値の BIOS 側事実が欠ける**（B2、B3）。正例の成功判定と負例の異常判定の基準が、呼出側の「0 か非 0 か」しかない。
2. **確認の強制力が不明**（B4）。負例「確認なし」が BIOS 上で拒否されるかを観測の前に決められない。
3. **破壊範囲が不明**（B5）。合成 image でも、どの領域が消えたかを判定するための期待値を定義できない。
4. **安全ゲートが成立しない**。`docs/173-safety-monitor-limits.md` の結論どおり、固定 Geargrafx は MMIO 書込みを副作用前に阻止する汎用フックを持たず、`slot-1e` 以外の BIOS JSR 実行も BLOCKED のまま。BM_FORMAT の JSR が `$1803-$1807` 系の MMIO 副作用を起こすかは未確認（B6）だが、起こしうる場合にゲートで防げないため、ゲートが成立していない。
5. 純正 BIOS は公開 CI に存在しない（private）。ローカル実コアの前提も本 PR では満たさない。

本 PR では **実コアを起動しない**。合成 image の診断ハーネスも作らない（前提の BIOS 側事実が無く、期待値を定義できないため）。

## 5. 発見した不整合

* `hucc-systemcard.asm:671` のコメントは「BM_OK, BM_NOT_FOUND」を返すと書くが、コードは `BM_NOT_FORMATTED`（$FF）を返す（asm:692）。呼出側ドキュメントの誤記の可能性（HYPOTHESIS）。
* `BM_NOT_FORMATED` は綴り誤り（HuC 側。`hucc-systemcard.asm:60` の定義と、その行のコメント「HuC incorrect spelling.」に明記）。同じ定義が `hucc-systemcard.h:37` にもある。定数は `$FF` で `BM_NOT_FORMATTED`（asm:61）と同値。

## 6. 次のステップ

* BIOS 側の事実（B2-B5）は、ライセンス確認済みの私的 System Card を使う別 Issue（maintainer 許可のもの）でのみ観測可能。公開リポジトリ内では観測しない。
* #304 は、B2-B5 が観測で埋まるまで BLOCKED_ABI のまま。`api_contracts.json` の slot 24 は、本 PR では変更しない（ledger 変更禁止）。
* 呼出側だけで既に決まっている事実（C1a-C12）は、`api_contracts.json` の更新案として別途提案する（本 PR の範囲外）。

## 7. 状態集計

台帳行は C1a-C12（呼出側、分割後 20 行）と B1-B7（BIOS 側 7 行）の計 27 行。各行の状態は 1 つ。

| 状態 | 行数 | 行 |
| --- | --- | --- |
| CONFIRMED-by-source | 11 | C1a, C1b, C2, C3a, C4a, C5a, C6, C7, C8a, C9a, C10a |
| HYPOTHESIS | 2 | C1c, C4b |
| UNKNOWN | 14 | C3b, C5b, C8b, C9b, C10b, C11, C12, B1-B7 |
| 合計 | 27 | |

* §4 の #304 判定（BLOCKED_ABI）は台帳行ではなく別判定として 1 件。
* VERIFIED は 0。release_eligible=false。

## 8. #303 合成判定ゲートとの関係（PR #509）

本節は #303 の合成判定層（`tools/bm_format_verdict.py`）と本台帳の関係を記録する。合成結果は BIOS 互換性の証拠ではない。

### 8.1 合成期待値と BIOS ABI の分離

| 区分 | 項目 | 出所 | 状態 |
| --- | --- | --- | --- |
| 合成（モデル専用） | 署名 `SYNBM\x01\x00\x00`、最小容量 64、ステータス名（OK など） | `tools/bm_format_model.py`（#395） | synthetic。BIOS の値ではない（§0） |
| 合成（モデル専用） | 不正署名の拒否 `REJECTED_INVALID_SIGNATURE`、OK 時の「署名あり・残りゼロ埋め・容量維持」 | `tools/bm_format_verdict.py`（#509） | synthetic。契約からの根拠はない |
| BIOS ABI | 入口、確認文字列の比較、返値、容量要件、破壊範囲、MMIO 副作用 | 本台帳 C3b, C5b, C11, C12, B1-B7 | UNKNOWN |

判定層のすべての期待値は上段の合成値のみで定義する。期待値は作者の合成モデル由来であり、BIOS ABI 由来ではない。下段の BIOS ABI を根拠にした PASS は存在しない。総合判定は `BLOCKED_ABI` で、`release_eligible` は常に `false`。

### 8.2 入口の比較（#395 `bm.format_image` と PR #509 `guarded_format`）

| 項目 | #395 `bm.format_image`（回復） | PR #509 `guarded_format`（拒否） |
| --- | --- | --- |
| 入口 | `bm.format_image(image, allow_destructive=...)` | `bm_format_verdict.guarded_format(image, allow_destructive=...)`。委譲先は `bm.format_image` |
| 保証 | 非 bytes は TypeError。未確認は `REFUSED_UNCONFIRMED`、64 バイト未満は `INSUFFICIENT_CAPACITY`、有効署名は `ALREADY_FORMATTED`、それ以外は `OK`（署名を書き残りゼロ埋め） | 上記に加え、確認付き・64 バイト以上・署名が不正・非ゼロのとき `REJECTED_INVALID_SIGNATURE`（拒否） |
| 保証しないこと | 不正署名のイメージが利用者データかどうかの判断。BIOS の挙動 | この層を経由しない呼出し（直接呼出し）の拒否。BIOS の挙動。回復と拒否のどちらが正しいか |
| 返値 | `(status, bytes)`。OK 以外は入力と同じ bytes | `(status, bytes)`。拒否と委譲先の非 OK は入力と同じ bytes |
| 入力変更 | なし（`bytes(image)` の複製に対して処理。bytearray も書き換えない） | なし（拒否パスは複製を返す。委譲先は上段と同じ） |
| I/O | なし（メモリ内のみ） | なし（メモリ内のみ） |
| 非ゼロ・不正署名（確認付き、64 バイト以上） | OK（回復） | `REJECTED_INVALID_SIGNATURE`（拒否） |
| 全ゼロ（blank）のイメージ | OK | OK（委譲のため同じ） |
| 状態 | #395 で固定（`test_invalid_signature_is_recovered_by_format`） | 暫定（owner 決定 A は OPEN） |
| BIOS ABI | UNKNOWN（#302） | UNKNOWN（#302） |

### 8.3 入口ごとの保証と迂回

| 呼出し経路 | 保証 | 迂回（バイパス） |
| --- | --- | --- |
| `guarded_format` | 確認付き・64 バイト以上・非ゼロの不正署名を拒否し、バイトを保持する | なし（この経路の内側）。ただし呼出側が直接 `bm.format_image` を使えばゲートを通らない |
| `bm.format_image` 直接 | 有効署名・容量不足・未確認の拒否は #395 どおり | **迂回あり**: 非ゼロの不正署名を OK で回復し、署名を書く |

* ゲートは `guarded_format` 経由の呼出しにしか効かない。したがって本ゲートは「完全な安全境界」ではない。安全境界とするには、呼出側を `guarded_format` に限定する別の措置（または #395 側の扱いの決定）が必要で、これは本 PR の範囲外。

### 8.4 owner 決定 A（未決定。承認ではない）

番号付きの OPEN 項目。owner が決めるまで、PR #509 の拒否は**暫定の保守的方針**であり、owner の承認ではない。承認の記録はない。

* **A（OPEN）** 確認付き・非ゼロ・不正署名・64 バイト以上のイメージを、どちらの振る舞いとするか。
  * **A1 拒否（PR の現方針、暫定）**: `REJECTED_INVALID_SIGNATURE`、バイト保持。帰結: #395 の `test_invalid_signature_is_recovered_by_format` は現挙動（回復）を固定しており、A1 を正とすると矛盾する。#395 のテストは本 PR で変更しない（スコープ外）ため、A1 採用時は別 Issue で #395 側の扱い（回復テストの書換え、または回復の撤回と呼出側のゲート必須化）を決める必要がある。
  * **A2 回復**: `OK`（署名を書き、残りをゼロ埋め）。帰結: 本 PR の `guarded_format` の拒否分岐を外し、#509 の拒否テスト（`test_nonzero_invalid_signature_is_rejected_and_kept`、`invalid_sig_*` の `CASES`）を回復の期待値に書き換える。#395 のモデルとテストは変更不要。判定層のその他の部分は変更なし。
* 判定が出るまでの現状: 拒否は**暫定**。どちらの判定でも BIOS ABI は UNKNOWN のまま（`BLOCKED_ABI`、`release_eligible=false`）。

### 8.5 #303 完了条件の証拠台帳

| # | 対象 | 根拠（source） | 種別 | 判定 |
| --- | --- | --- | --- | --- |
| E1 | 実 save/RAM への非書込み（モデルは in-memory のみ） | `tools/bm_format_model.py` の I/O 禁止と import 許可リストのテスト（#395、#509 の `test_verdict_module_imports_only_the_model`） | 合成（静的・単体テスト） | PASS（合成） |
| E2 | 正常系（blank 64 / 256、bytearray 入力、入力非改変） | `tests/host/test_bm_format_verdict.py`、`CASES` | 合成 | PASS（合成） |
| E3 | 不正署名（非ゼロ）の負例 | 同上。ゲートの拒否（`REJECTED_INVALID_SIGNATURE`）。#395 の回復と矛盾 | 合成 | ゲートの挙動は PASS（合成）。方針は owner 決定 OPEN |
| E4 | 容量境界（63 バイトの無署名・有効署名、空、64 バイト空白） | 同上 | 合成 | PASS（合成） |
| E5 | fail-closed 判定（OK は署名必須、結果欠落、型不一致、許可外ステータス（一致していても FAIL）、例外、スナップショット失敗、入力変更） | `judge`、`run_case`、テスト群。改修前は OK 署名なしと、一致する許可外ステータス（`BOGUS_STATUS`）を PASS にしていた（RED を確認） | 合成 | PASS（合成） |
| E6 | 根拠 URL / revision / line | `docs/research/302-bm-format-contract.md` §1（HuCC `54f5c73`）、§2 の行番号 | 出典（静的） | UNKNOWN 項目を含む（§3） |
| E7 | BIOS 上の実挙動（返値、確認、破壊範囲、MMIO） | 観測なし（B1 ほか） | 実測なし | NOT_RUN / BLOCKED_ABI |
| E8 | 実 FORMAT の実行、実 save への書込み | 方針により実施しない | 実測なし | NOT_RUN（対象外） |
| E9 | 総合判定 | `run_all()`（全ケース PASS でも総合は BLOCKED_ABI） | 合成 | BLOCKED_ABI、`release_eligible=false` |

PASS は合成データに対してのみ。BIOS 互換性の PASS は一つもない。
