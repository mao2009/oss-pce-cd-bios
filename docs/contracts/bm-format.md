# BM_FORMAT 契約台帳: 破壊的操作・成功/異常時の返値・呼出元の証拠（Issue #302）

Refs #302 / 親 #34 / 後続 #303（非実行の安全モデル）/ #304（実コア診断、本PRでは未実施）
既存台帳（参照のみ、本PRでは変更しない）: `docs/research/302-bm-format-contract.md`（#402 / #548 で追加）

状態: **Contract 台帳（調査のみ）。実装なし。実行なし。VERIFIED 0 件。release_eligible=false。判定 BLOCKED_ABI。**

## 0. 範囲と語彙

* 対象: BM_FORMAT（BIOS 入口 `$00:E048`）と、同名の HuC ライブラリ実装。BM_FREE は前提確認のみ（`docs/305-bm-free-contract.md` 参照）。
* 状態語彙:
  * `CONFIRMED-by-source`: 固定リビジョンの公開ソース行に書かれた事実。**呼出側（ラッパー）の記述・コードの事実**であり、BIOS 側の挙動は含まない。
  * `HYPOTHESIS`: 名前・コメント・周辺コードからの推定。
  * `UNKNOWN`: 公開ソースに根拠がない、または確認できない。
  * `NOT_RUN`: 実行・実機・実コアによる確認を行っていない。
* 純正 BIOS のコード・逆アセンブル・バイト列は扱わない。本書は行番号と要旨のみを記録し、公開ソースの識別子（関数名・定数名・確認文字列）だけを引用する。
* 呼出側は 2 系統ある。混同すると安全判定を誤るため、本書では分けて扱う。
  * **経路 A（HuCC 系）**: `hucc-systemcard.asm` の `_bm_format` が BIOS 入口 `$E048` を呼ぶ。
  * **経路 B（HuC 系）**: `huc_bram.asm` の `_bm_format` が BIOS を呼ばず、BRAM を直接書き換える。`$E048` は経路 B では使われない。

## 1. 出典（固定リビジョン・ハッシュ照合）

固定リビジョン: `pce-devel/huc` @ `54f5c73606c1e7142e8095c727cab2767e2b3cb3`（tarball を取得し、ファイルごとに SHA-256 を計算）。

| key | パス | SHA-256（完全値） | ライセンス（ファイル内の記載） | 用途 |
| --- | --- | --- | --- | --- |
| huc:include/hucc/pcengine.inc | include/hucc/pcengine.inc | `b6a25d33bb787ea1fe550c6e04aff9f8ccaaa91b649a05a963be24b440b728a9` | Boost 1.0（L8） | 入口宣言（L494）、`_ax` 等の別名（L329-343）、`IFU_BRAM_LOCK/UNLOCK`（L204-205） |
| huc:include/hucc/hucc-systemcard.asm | 同 | `4258b7c3e8751127c0615af3178e431d81043c25fa6cb2b6b1d51b566aec7ea9` | Boost 1.0（L10）、Copyright John Brandwood 2024（L8） | 経路 A のラッパー（L54-61, L65, L667-698, L713-722） |
| huc:include/hucc/hucc-systemcard.h | 同 | `f2477642f867c3daeff00938534f3f8ef970d1a9403f44bd09e90894220b8a3b` | Boost 1.0（L13） | C 宣言（L111）、定数（L31-38） |
| huc:include/huc/huc_bram.asm | include/huc/huc_bram.asm | `95d1d5f4dcedde6ceb9c20f740364fc2ece7bb9f730a186dc158deacfeb0a515` | **ファイル内に記載なし → UNKNOWN** | 経路 B の `_bm_format`（L112-151）、判定（L754-770）、`_bm_testram`（L88-107）、解除・復元（L777-803） |
| huc:include/huc/startup.asm | include/huc/startup.asm | `584d130bb10bdb4a85ad054f458365ee1ae032b5688d651a17362e2121dadc8d` | 記載なし → UNKNOWN | L1253 で `huc_bram.asm` を include |
| huc:include/huc/equ.inc | include/huc/equ.inc | `3f9e521731904316193ba40182fa644e82511c667a267a032618911c28b847cd` | 記載なし → UNKNOWN | `__ax = $20F8`（L33）、`_ax = __ax`（L51） |
| huc:include/huc/system.inc | include/huc/system.inc | `29be0d4f0c6e460689fb8ba78bbf04e75aea326d19c3fb3040df9087d9d9d01c` | 記載なし → UNKNOWN | `bm_format = $E048`（L91） |
| huc:examples/asm/elmer/kickc/bios.h | 同 | `f99e5d63d9a4e97dd28e28f84b5b4742e0fc29aa0134af90f8872001e7cc2436` | Boost 1.0（L8） | 定数のみ（L52） |
| huc:doc/hucc/hucc-function-reference.md | 同 | `06ce76b902522456681ef95f42046da0ff4bb781f64c20a5ae7552fd08aae0f0` | 記載なし | 説明文（L1011-1012） |
| huc:doc/huc/clibref.txt | 同 | `da488fa6d53db37f0c7849265a9a60066fb9fb0bb970545e7cf5182faf4c6265` | 記載なし | 説明文（L640-648） |

* 3 つの HuCC ファイルの SHA-256 は `spec/api_contracts.json` の記録値（完全値）と一致した。
* リポジトリ直下の `LICENSE` は単一のライセンスではない。冒頭で一括ライセンスとはできないと述べ、個別に GPL・Simplified BSD・freeware の記述がある。よって、ファイル単位の記載がないファイルは **UNKNOWN** とする。
* 本書は HuC のソースを**コピーしない**。根拠は行番号で示す。

## 2. 破壊的操作の範囲（何が消えうるか）

### 2.1 経路 B（HuC ライブラリ、BIOS 非経由）で直接書き換える範囲

`_bm_format`（huc_bram.asm L119-151）は、BRAM 先頭のタグ 4 バイトが一致しないとき（L754-770）に、以下を書き換える。

| id | 書換え箇所 | 根拠 | 状態 |
| --- | --- | --- | --- |
| D-B1 | `$8000`〜`$8007`（8 バイト）を固定の識別値で上書き | L123-127（`_bm_id` を転記） | CONFIRMED-by-source（書込みの事実） |
| D-B2 | `$8010`、`$8011` を 0 にする | L128-129 | CONFIRMED-by-source（書込みの事実） |
| D-B3 | `$8005` に終端ポインタ（高位）を書く | L142-143 | CONFIRMED-by-source（書込みの事実） |
| D-B4 | 各バンク先頭 8 バイトを試験のため反転書込みする | `_bm_testram` L88-107、ループ L132-141 | CONFIRMED-by-source（書込みの事実） |
| D-B4b | D-B4 の試験後に元の値へ戻るか | 復元の経路（L104-105）から読み取れるが、全経路の追跡は未実施 | HYPOTHESIS |
| D-B5 | 既存エントリ（利用者データ）の連結リストがどこまで壊れるか | 上記 D-B1〜D-B3 は先頭付近のみ。エントリ列の走査・消去は `_bm_format` 内にない | HYPOTHESIS（データ本体は直接消去しないと読める。実機未確認） |
| D-B6 | 呼出側の zero page `$20F8`〜`$20FF`（8 バイト） | `_bm_testram` が `__ax,Y`（L93, L100）に書く。`__ax = $20F8`（equ.inc L33）、`_ax = __ax`（L51） | CONFIRMED-by-source |

* D-B6 は、本 Issue が指定する「$20F8-$20FF」の一部であり、呼出側の `_ax`〜`_dh` を**保存せずに破壊する**。経路 A の BIOS 側でこの範囲を使うかは UNKNOWN（B-ZP）。
* ドキュメント（`hucc-function-reference.md` L1012、`clibref.txt` L644-646）は「データは消去されず、ヘッダが示さなくなる」と書く。これは説明文であり、コードで検証していない（HYPOTHESIS）。

### 2.2 経路 A（HuCC、BIOS `$E048` 呼出し）の範囲

| id | 項目 | 状態 |
| --- | --- | --- |
| D-A1 | 呼出側は `bm_free` が非 0 のときだけ `bm_format` を呼ぶ（`hucc-systemcard.asm` L677-680）。これが唯一の事前判定 | CONFIRMED-by-source |
| D-A2 | BIOS が何バイトを消すか、既存エントリをどう扱うか | UNKNOWN |
| D-A4 | BIOS の MMIO 操作（`$FF:1803`/`$FF:1807` 系）の有無 | UNKNOWN（呼出側の wrapper には `$1807` 書込みがない: L675-698） |

## 3. 成功時・異常時の戻り値と副作用

### 3.1 経路 A（呼出側ラッパー、`hucc-systemcard.asm` L675-698）

| id | 項目 | 呼出側の事実 | 行 | 状態 |
| --- | --- | --- | --- | --- |
| A-1 | 入口 | `bm_format = $00:E048, FUNC`（pcengine.inc L494）。ラッパーは `system bm_format` で呼ぶ（asm L687） | pcengine.inc:494 / asm:687 | CONFIRMED-by-source（宣言と呼出し） |
| A-2 | BIOS 実体が `$E048` か | 第三者の番地表は未取得 | — | UNKNOWN（既存台帳 C1c は HYPOTHESIS） |
| A-3 | 引数 | 確認文字列へのポインタを `_ax`（`$F8:20F8`、2 バイト、低位先）に置く（asm L682-685） | asm:682-685 / pcengine.inc:329 | CONFIRMED-by-source |
| A-4 | 確認文字列 | `"!BM FORMAT!"`（11 バイト、NUL 終端の指定なし） | asm:698 | CONFIRMED-by-source |
| A-5 | 確認文字列の BIOS 側検証 | 比較するか、比較長、不一致時の返値 | — | UNKNOWN |
| A-6 | 事前判定 | `bm_free` の A を `tax` し、0 なら `bm_format` を呼ばず X=0 で返す（asm L677-680） | asm:677-680 | CONFIRMED-by-source |
| A-7 | 事前判定の 0 の意味 | 0 を「フォーマット済み」とみなす。`_bm_free` マクロは A≠0 を error として扱う（asm L714-718） | asm:714-718 | HYPOTHESIS（「フォーマット済み」の意味は BIOS 側未確認） |
| A-8 | 成功時の返値（呼出側） | `bm_format` の A=0 なら X=0（BM_OK）で返す（asm L687-691） | asm:687-696 | CONFIRMED-by-source |
| A-9 | 異常時の返値（呼出側） | A≠0 なら X=`$FF`（`BM_NOT_FORMATTED`）に置換して返す（asm L692-694）。コメント（asm L671）は `BM_NOT_FOUND` と書くが、コードは置換する | asm:671 / 692-694 / 定義 asm:61 | CONFIRMED-by-source |
| A-10 | 戻り値の受け渡し | `.done` で `stx __bm_error`、`cly`、`leave` により X→A（asm L694-696） | asm:694-696 | CONFIRMED-by-source |
| A-11 | 異常時の BIOS 実返値 | BIOS が A/X/Y/P で何を返すか。呼出側は A==0 のみ見る | — | UNKNOWN |
| A-12 | 呼出側のレジスタ | A/X/Y を破壊する。Y は `cly` で 0 になる（asm L695）。P は退避しない（php/plp なし）。スタックも退避しない | asm:675-698 | CONFIRMED-by-source |
| A-13 | BIOS 側の退避 | BIOS が P/A/X/Y を保存するか | — | UNKNOWN |
| A-14 | MPR | ラッパーは MPR を変更しない（tma/tam なし）。`.password` のアドレスは `lda.l/.h #` で得る（asm L682-685） | asm:675-698 | CONFIRMED-by-source（呼出側） |
| A-15 | BIOS 側の MPR 前提 | ポインタが BIOS から見える配置 | — | UNKNOWN |
| A-16 | 割込み状態 | ラッパーは `sei`/`cli` を行わない。BIOS 側は未確認 | — | UNKNOWN |
| A-17 | 容量要件 | 呼出側は長さを渡さない。最小容量 64 バイト等は #395 の合成モデルの内部値であり、根拠にしない | asm:667-673 | UNKNOWN |
| A-18 | 失敗時の副作用（呼出側） | `__bm_error` は `$FF` で上書きされる（asm L694）。`.ds 1` は asm L65 | asm:65, 688, 694 | CONFIRMED-by-source |
| A-19 | 失敗時の副作用（BIOS 側） | BIOS が行う副作用。未観測 | — | UNKNOWN |

### 3.2 経路 B（HuC ライブラリ、`huc_bram.asm` L112-151）

| id | 項目 | 実装の事実 | 行 | 状態 |
| --- | --- | --- | --- | --- |
| B-1 | 入口 | BIOS を呼ばない。`_bm_enable` は `lib2_bm_enable` へのマップ呼出し（L397-399） | huc_bram.asm:397-399 | CONFIRMED-by-source |
| B-2 | 引数 | なし。確認文字列も確認フラグもない | huc_bram.asm:119-151 | CONFIRMED-by-source |
| B-3 | 判定基準 | BRAM 先頭 4 バイトのタグ一致のみで判定する（L757-762）。エントリ列の整合性は見ない | huc_bram.asm:754-770 | CONFIRMED-by-source |
| B-4 | コメントとの関係 | コメント「既にフォーマット済みなら OK」（L114）は、タグ一致時の早期 return（L121, L146-151）と対応する。タグ不一致なら、エントリ列が整合していても無条件にフォーマットする | huc_bram.asm:114, 121, 754-770 | CONFIRMED-by-source |
| B-5 | 成功時の返値 | 常に A=0、X=0、C=0。`bm_error`=0（L147-151） | huc_bram.asm:147-151 | CONFIRMED-by-source |
| B-6 | 異常時の返値 | `_bm_format` には失敗経路がない（`.err` は `_bm_free` にのみ存在: L177-182）。試験結果（`X`）はループの分岐にのみ使われ、戻り値には反映されない（L132-141） | huc_bram.asm:132-151, 177-182 | CONFIRMED-by-source |
| B-7 | 失敗を示す返値の不在と文書の不整合 | 文書（clibref L648、hucc-function-reference L1012）は「BRAM の状態を返す」と書くが、実装は失敗を返さない | 同 | CONFIRMED-by-source（不整合） |
| B-8 | 呼出側のレジスタ | A/X/Y/C を変える（Y は試験ループで変わる: L90, L98-99）。P の I フラグは `cli` により 0 になる（L802） | huc_bram.asm:90, 98, 802 | CONFIRMED-by-source |
| B-9 | MPR4 | `tma #4` で MPR4 を `bm_mpr4` に保存し、BRAM バンク（`$F7`）へ `tam #4`（L779-782）。解除で復元（L798-799） | huc_bram.asm:777-803 | CONFIRMED-by-source（1 バイト保存のため入れ子呼出しに弱いと読める: HYPOTHESIS） |
| B-10 | 割込み | `sei`（L778）で禁止し、解除で `cli`（L802）で許可。入った時の状態は復元されない | huc_bram.asm:778, 802 | CONFIRMED-by-source |
| B-11 | BRAM 不在時の挙動 | 試験で何が起きるか。`X` の値による分岐はフォーマット経路へ進むのみ | — | UNKNOWN |
| B-12 | 実機での結果 | 観測なし | — | NOT_RUN |

## 4. 呼出元（caller）の証拠

固定リビジョンの tarball 全体に対して、大文字小文字を無視して `bm_format`・`BM_FORMAT`・`bm_free`・`E048` を検索した。結果は以下の通り。`E048` の残りのヒットは CRC 表の数値であり無関係。GitHub のコード検索 API は 0 件を返したが、索引の完全性は未確認のため、tarball 検索を一次証拠とする。

| id | 場所 | 種別 | 内容 | 状態 |
| --- | --- | --- | --- | --- |
| K-1 | `include/hucc/hucc-systemcard.asm` L675-698 | 呼出側（経路 A） | BIOS `$E048` を呼ぶ唯一のラッパー | CONFIRMED-by-source |
| K-2 | `include/hucc/hucc-systemcard.h` L111 | C 宣言 | `unsigned char __fastcall bm_format(void)` | CONFIRMED-by-source |
| K-3 | `include/hucc/pcengine.inc` L494 | 入口宣言 | `$00:E048` | CONFIRMED-by-source |
| K-4 | `include/huc/huc_bram.asm` L119-151 | 呼出側（経路 B） | BIOS 非経由の直接実装 | CONFIRMED-by-source |
| K-5 | `include/huc/startup.asm` L1253 | include | K-4 を取り込む | CONFIRMED-by-source（取込みの事実。条件分岐の有無は未確認） |
| K-6 | `include/huc/system.inc` L91 | 記号定義のみ | `bm_format = $E048`。呼出し箇所なし | CONFIRMED-by-source |
| K-7 | `examples/asm/elmer/kickc/bios.h` L52 | 定数定義のみ | 呼出し箇所なし | CONFIRMED-by-source |
| K-8 | `doc/hucc/hucc-function-reference.md` L1011-1012、`doc/huc/clibref.txt` L640-648 | 説明文 | 動作の説明（K-4 の実装と一致しない点は §5） | CONFIRMED-by-source（文書の記述） |
| K-9 | 本リポジトリ | 呼出し | 製品コードからの呼出しはない。`tools/reference_bios_scenarios.py` L23 は `BM_FORMAT` を危険操作の集合に入れる（呼出しではなく分類） | CONFIRMED-by-source |
| K-10 | 同名 `_bm_format` の二重定義 | 衝突 | K-1（hucc-systemcard.asm L675）と K-4（huc_bram.asm L119）が同名のシンボルを定義する。どちらが何のツールチェーンで選ばれるかは未確認 | UNKNOWN（リンク選択条件） |

## 5. 不整合・注意点

* **I-1**: `hucc-systemcard.asm` L671 のコメントは `BM_NOT_FOUND` を返すと書くが、コードは `BM_NOT_FORMATTED`（`$FF`）に置換する（L692-694）。ドキュメントの誤記の可能性（HYPOTHESIS）。
* **I-2**: `BM_NOT_FORMATED` は綴り誤り（asm L60、h L37。HuC 側の注記あり）。定数値は `$FF` で `BM_NOT_FORMATTED` と同じ。
* **I-3**: 経路 B は、文書が約束する「ステータスを返す」（K-8）と、実装の「失敗を返さない」（B-6, B-7）が一致しない。
* **I-4**: 経路 B の判定はタグ 4 バイトのみ（B-3）。コメントの「フォーマット済みなら OK」は、エントリ列の整合性を含むと読める。文書化された意図と判定基準が一致しない（HYPOTHESIS）。
* **I-5**: 経路 B の `_bm_testram` は zero page `$20F8`〜`$20FF` を書く（D-B6）。本 Issue の対象範囲そのものであり、安全モデルの前提（§6 P-5）に入れる必要がある。
* **I-6**: 同名の `_bm_format` が 2 系統ある（K-10）。実行時にどちらの挙動になるかで、確認文字列の有無が変わる。

## 6. #303 の非実行安全モデルが必要とする前提

以下は後続の実装が満たすべき前提である。本 Issue では実装・実行をしない。

* **P-1**: 呼出し経路を 2 つに分けて扱う。経路 A（BIOS `$E048`）と経路 B（HuC 直接書換え）。経路 B は BIOS を呼ばないため、`$E048` 向けの保護では防げない。
* **P-2**: 確認文字列（A-4）は経路 A の BIOS 呼出しにのみ現れる。経路 B には確認がない（B-2）。実行可否の判定は経路ごとに別々に定める。
* **P-3**: 経路 A の事前判定は `bm_free` の 0/非 0 のみ（A-6）。非 0 でも BIOS 側の拒否が未確定（A-5, A-11）であり、安全判定の根拠にしない。
* **P-4**: 副作用範囲（D-B1〜D-B6、D-A2）の期待値は、実機の根拠がない。合成モデル（#395、#509）の値を BIOS の ABI として扱わない。
* **P-5**: 経路 B は `$20F8`〜`$20FF` と `bm_mpr4`（1 バイト）、割込み状態（B-9, B-10）を呼出側から見て変える。安全モデルは、呼出し前後のこれらの状態を観測対象に含める必要がある。
* **P-6**: 呼出し前に BRAM への物理的な書込みを止める手段が、固定 Geargrafx にはない（`docs/173-safety-monitor-limits.md` の結論）。このため、実行を伴う検証は本 Issue の範囲外。
* **P-7**: 実行・試験は一切行わない（本 Issue の指示）。経路 B のソース解析はここまでとし、動的な試験は別 Issue で maintainer が許可した場合にのみ行う。

## 7. 未解決論点（UNKNOWN）

UNKNOWN は表内 11 件（A-2, A-5, A-11, A-13, A-15, A-16, A-17, A-19, D-A2, D-A4, B-11）と、表外 4 件（K-10, LIC-1〜3）の計 15 件。

| id | 論点 | 必要な根拠 |
| --- | --- | --- |
| A-2 | `$E048` が BIOS 実体の入口か | 第三者番地表、または権利の確認できる System Card 資料 |
| A-5 | BIOS の確認文字列の検証（比較長、不一致時の返値、拒否の有無） | 同上 |
| A-11 | BIOS の成功/異常時の実返値（A/X/Y/P） | 観測（maintainer 許可の下） |
| A-13 | BIOS 側の P/A/X/Y の保存 | 同上 |
| A-15 | BIOS 側の MPR 前提 | 同上 |
| A-16 | BIOS 側の割込み状態 | 同上 |
| A-17 | 容量要件 | 同上 |
| A-19 | BIOS 側の失敗時副作用 | 同上 |
| D-A2 | BIOS の消去範囲 | 同上 |
| D-A4 | BIOS の MMIO 操作（`$1803`/`$1807`） | 同上 |
| B-11 | 経路 B で BRAM 不在時の挙動 | 試験は禁止（P-7）。ソース解析の追加 |
| K-10 | 同名 `_bm_format` のリンク選択 | HuC/HuCC のビルド手順の確認 |
| LIC-1 | `huc_bram.asm` のライセンス | HuC のライセンス表記の確認 |
| LIC-2 | `startup.asm`、`equ.inc`、`system.inc` のライセンス | 同上 |
| LIC-3 | `doc/` 説明文のライセンス | 同上 |

HYPOTHESIS のまま残す項目: A-7、D-B4b（試験領域の復元）、D-B5（エントリ列の破損範囲）。NOT_RUN: B-12。

## 8. 検証

* **V-1（ハッシュ）**: 3 つの HuCC ファイルの SHA-256 が `spec/api_contracts.json` の記録値と一致した（§1）。HuC の他ファイルは本書で計算した値を記録した。
* **V-2（参照行の実在）**: 本書の行参照は、取得したソースの該当行を表示して内容を照合した。主な照合は次の通り: asm L54-61, L65, L667-698, L713-722、pcengine.inc L204-205, L329-343, L494-495、h L31-38, L111、huc_bram.asm L88-107, L112-151, L177-182, L326, L397-399, L754-803、equ.inc L33, L51、system.inc L91、bios.h L52、startup.asm L1253。全件一致。
* **V-3（リポジトリ内の参照先）**: `docs/research/302-bm-format-contract.md`、`docs/173-safety-monitor-limits.md`、`docs/305-bm-free-contract.md`、`tools/bm_format_model.py`、`tools/bm_format_verdict.py`、`tools/reference_bios_scenarios.py`（L23）は実在を確認した。
* **V-4（実コア・実機）**: **NOT_RUN**。理由: 実行・試験を行わない指示。純正 BIOS は公開 CI に存在しない。固定 Geargrafx は BRAM 書込みを副作用前に阻止できない（P-6）。
* **V-5（テスト）**: コードを変更しないため、既存テストの実行は行っていない。

## 9. セキュリティ・ライセンス

* 純正 BIOS のコード、逆アセンブル、バイト列を本書に転記していない。`./rom/` の私有ファイルを読んでいない。
* 本書は HuC/HuCC の公開ソースを行番号で参照し、コードを複製していない。
* ライセンスは §1 の通り。未確認のファイルは UNKNOWN（LIC-1〜LIC-3）。
* 実 save、実 BRAM、実デバイスへの書込みは行っていない。BM_FORMAT は実行していない。

## 10. 状態集計

§2.1、§2.2、§3.1、§3.2 の表に含まれる 41 行の状態は次の通り（表の状態列を機械的に数えて確認）。

| 状態 | 行数 | 内訳 |
| --- | --- | --- |
| CONFIRMED-by-source | 26 | §2.1 の 5、§2.2 の 1、§3.1 の 10、§3.2 の 10 |
| HYPOTHESIS | 3 | D-B4b、D-B5、A-7 |
| UNKNOWN | 11 | §2.2 の 2、§3.1 の 8、§3.2 の 1（B-11） |
| NOT_RUN | 1 | B-12 |

表外の UNKNOWN は K-10 と LIC-1〜3 の 4 件。よって UNKNOWN の総数は 15 件。VERIFIED は 0 件。

## 11. マージ前に必要な確認

* 本 PR はドキュメントのみ。Draft のまま、maintainer が §7 の UNKNOWN を確認すること。
* `api_contracts.json` の更新は本 PR の範囲外。更新案は別途提案する。
* #303 の安全モデルは §6 の前提を満たしてから実装する。
