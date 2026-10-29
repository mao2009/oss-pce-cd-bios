# Issue #188: CD_READ の転送先モードと count 境界の参照台帳（非実行）

確認日: 2026-10-29。親: #12 / #11。対象は1セクター・1モード（`_dh = 3`）に限る。
本書は既存文書の重複ではなく、項目ごとの確信度・UNKNOWN 件数・#190 の開始可否を1枚にまとめた台帳である。詳細な表は既存文書を参照する（§7）。

## 0. 判定

* **実観測: 未実施。** 純正 BIOS の実行 0 件、`$E009` の呼出 0 件（`CALLEE_REAL_CORE_RUNS = 0`）。安全境界（#173、#365 / #366 / #367）が未検証のため。
* **転送先モードの確定: 未確定。** 確定しているのは「HuCC の公開 caller が `_dh` に何の値を書くか」（呼出側の事実）のみ。callee（BIOS）がその値をどう解釈するかは確定していない。
* **#190 の開始: 不可。** 理由は §5。

## 1. 出典と確認状態

| ID | 出典 | 内容 | 本 run での確認 |
| --- | --- | --- | --- |
| S1 | HuCC `pce-devel/huc` @ `54f5c73606c1e7142e8095c727cab2767e2b3cb3`、`include/hucc/hucc-systemcard.asm` | 公開 caller。`system cd_read` の呼出しは 4 箇所（L297、L325、L388、L446） | 取得し sha256 `4258b7c3…` の一致を確認（854 行）。以下の [C] はこの取得物で確認 |
| S2 | 同 `include/hucc/core-kernel.asm`（L593-594 の上書きローダ） | 既存文書の記載 | 本 run では未再確認（既存文書参照のみ） |
| S3 | Issue #188 本文 | 1モード・1セクターに限定、PR 1件、実観測は UNKNOWN 台帳で扱う | `gh issue view` で確認 |
| S4 | 既存文書: `docs/188-cd-read-transfer-mode-contract.md`（PR #503 MERGED）、`docs/188-cd-read-mode-safety.md`（PR #420 MERGED）、`docs/cd-read-mode-contract.md`（PR #389 MERGED）、`docs/cd-read-contract.md` | 静的契約、安全判定、転送層の計測 T1-T4（Geargrafx `b49ae82a`） | 参照のみ。本 run では再実行していない |
| S5 | Issue 状態（本 run 確認） | #190 OPEN、#149 OPEN、#173 OPEN、#11 OPEN、#12 OPEN、#365 / #366 / #367 OPEN | `gh issue view` で確認 |

記号: [C] は S1 の命令（コード）、[K] は S1 のコメント・宣言由来（HYPOTHESIS の根拠にしかならない）、[M] は既存の転送層計測（callee の結果ではない）。

## 2. モードの項目と確信度

| ID | 項目 | 根拠 | 状態 |
| --- | --- | --- | --- |
| M-1 | `cd_read` の呼出し 4 箇所の `_dh` 値は `$FF`、`$FE`、`0`、`3` | [C] S1 L294-297、L322-325、L384-388、L440-446 | 確定（呼出側） |
| M-2 | `cd_loadbank` は `_dh = 3` を書く（コメント「Use MPR3.」） | [C] S1 L440-441 | 確定（呼出側） |
| M-3 | `_dh = 3` は RAM bank `_bl` への転送を意味する | [K] 宣言 `bank<_bl>`（L425）とコメント | HYPOTHESIS |
| M-4 | `_dh = 0` は「バイト単位」の読み出しである | [C] `stz <_dh`（L384）。単位「バイト」は [K]（コメント「Signal bytes, not sectors.」） | 呼出し形式は確定、単位は HYPOTHESIS |
| M-5 | `_dh = $FE` / `$FF` は VRAM 転送の信号である | [C] 値の書込み。VRAM の意味は [K]（コメント「Signal a VRAM load.」） | 値は確定、意味は HYPOTHESIS |
| M-6 | HuCC の確認範囲（S1）に `_dh = 2` の cd_read 呼出しはない | [C] S1 の `cd_read` 呼出しは 4 箇所のみ（M-1） | 確定（S1 の範囲に限る。`include/huc/` は未検査） |
| M-7 | `_dh = 2` の意味（既存 `cd-read-mode-contract.md` R3 の記述） | 出典は Hu-Go! HLE のみで、ライセンス未確定のため根拠に使わない | UNKNOWN |
| M-8 | 各 `_dh` 値を callee がどう解釈するか（`3` を含む全値） | callee を実行していない | UNKNOWN |
| M-9 | `_dh = 3` の宛先が RAM か VRAM か | M-3 の仮説の裏付けなし | UNKNOWN |

確定は M-1、M-2、M-4（呼出し形式）、M-5（値）、M-6 の計 5 件。いずれも呼出側の事実に限る。

## 3. count 境界の項目と確信度

| ID | 項目 | 根拠 | 状態 |
| --- | --- | --- | --- |
| C-1 | `cd_loadbank` は `_dh` が非 0 のときのみ `_al` に 1 バイト値を複写する | [C] S1 L436-438（`beq` で複写を飛ばす） | 確定（呼出側） |
| C-2 | count の単位はセクターである | [K] 宣言 `sectors<_al>`（L425）、コメント「Get #sectors (0=whole file).」（L436） | HYPOTHESIS |
| C-3 | callee が `_al` を count として読むか、その単位 | 未観測 | UNKNOWN |
| C-4 | count 1 を `_dh = 3` で渡す caller 例 | S1 に例なし | UNKNOWN |
| C-5 | count 1、LBA 0 / 5 / 15 の READ(6) 転送（転送層） | [M] T1 の 3 件（送達 1 セクター、STATUS 正常） | 転送層のみ計測（callee 未確定） |
| C-6 | count 0 は `_al` を書き換えずに callee へ渡る | [C] C-1 の帰結 | 呼出し形式は確定、callee の結果は C-7 |
| C-7 | count 0 の callee 挙動（whole file の扱いを含む） | [K]「whole file」はコメントのみ。callee 未観測 | UNKNOWN |
| C-8 | count 2 / 3 の callee 挙動 | 未観測（[M] T2 は別の READ(6) 経路） | UNKNOWN |
| C-9 | count 4 以上、256 付近の境界 | 未観測（[M] T8 未計測） | UNKNOWN |
| C-10 | mode 0 の 1 回あたりの上限 `$2000` バイト | [C] S1 L369-375 の分割ループ | 確定（mode 0 の呼出側のみ） |
| C-11 | mode 3 の 1 回あたりの上限 | S1 に根拠なし | UNKNOWN |
| C-12 | LBA 16 以上（媒体範囲外）の callee 挙動 | [M] T3 は転送層で 0 バイト・STATUS のみ。callee 未観測（#189） | UNKNOWN（callee）。転送層は計測済 |
| C-13 | 末尾跨ぎ（LBA 14 x3）の callee 挙動 | [M] T4 は転送層。callee 未観測（#189） | UNKNOWN（callee）。転送層は計測済 |
| C-14 | 無媒体の戻り値と副作用 | 転送層で未計測（T7）。callee 未観測（#189） | UNKNOWN |

## 4. 宛先・戻り値・基点の項目

| ID | 項目 | 根拠 | 状態 |
| --- | --- | --- | --- |
| D-1 | `cd_loadbank` は `_bx` を書かない | [C] S1 L425-457 | 確定（呼出側） |
| D-2 | `_bx` と bank 内オフセットを callee が既定値として扱うか | 未観測 | UNKNOWN |
| D-3 | 宛先の終端規約（1 セクター書込み量） | 未観測 | UNKNOWN |
| D-4 | `cd_loadbank` は MPR3 を退避・復元する | [C] S1 L443-444、L449-450 | 確定（呼出側の防御） |
| D-5 | 成功時に callee が MPR3 を変えるか | 未観測 | UNKNOWN |
| D-6 | 失敗時に MPR3 が壊れる | [K] コメント「destroys the value on a CD error」 | HYPOTHESIS |
| D-7 | `_bl` の bank から MPR3 窓への対応 | 未観測（M-3 の仮説に依存） | UNKNOWN |
| R-1 | 呼出側は callee の戻り X を A に移して返す（`tax`、`txa`、`leave`） | [C] S1 L448-455 | 確定（呼出側） |
| R-2 | mode 0 の呼出側は X の非 0 をエラーとみなす | [C] S1 L389-390 付近 | 確定（呼出側）。成功値の根拠にはならない |
| R-3 | 成功時の A の値 | 根拠なし（R-2 の慣習は別関数の分岐） | UNKNOWN |
| R-4 | エラーコードの体系（no disc、bad block、read error、busy） | 根拠なし | UNKNOWN |
| R-5 | 戻り時の N / Z / C / T | 根拠なし | UNKNOWN |
| R-6 | X / Y の保存 | 根拠なし。呼出側は `cly` で Y を 0 にする | UNKNOWN |
| R-7 | `$20F8-$20FF` の保存 | 根拠なし | UNKNOWN |
| R-8 | 呼出し前の CLI 前提 | [C] ラッパ内に `cli` / `sei` なし（S1 L425-457） | UNKNOWN |
| B-1 | 呼出側は `get_file_info` と `_add_sectors` で `_cl:_ch:_dl` を作る | [C] S1 L430-434 | 確定（呼出側の計算） |
| B-2 | 基点が絶対 LBA か CD_BASE 相対か | #11 と未解決（既存 `research/148-cd-read-abi.md` §6） | UNKNOWN |

## 5. #190 の開始可否

**判定: 開始不可。** #190（1 セクター RAM 転送の独立 HuC6280 処理）は、次の理由で開始できない。

1. 転送先モードの意味（M-3、M-9）と宛先（D-2、D-3、D-7）が未確定。独立実装は宛先アドレスと終端規約を前提にする。
2. 戻り値の規約（R-3、R-4、R-5）と MPR の復帰（D-5）が未確定。これを埋めると、純正 callee の挙動を推測で固定することになる。
3. count の単位（C-2、C-3）が仮説のまま。1 セクターの件数を実装側で決められない。
4. 純正 BIOS を使った検証は、#173 系（#365 / #366 / #367）が OPEN のため BLOCKED。

開始の条件: M-3、M-9、D-2、D-3、C-2、C-3、R-3 の 7 項目を、独立な出典（ライセンス確認済みの文献または公開 caller）で確定すること。`VERIFIED` への格上げは、純正 callee の実測によってのみ行う。

## 6. 次に安全に行える検証（非実行）

純正 BIOS の実行は行わない。いずれも静的な作業。

1. `include/huc/` を含む公開 caller 全体で `cd_read` の呼出しを列挙し、`_dh` の値の出現表を作る（ライセンス審査を先に行い、根拠は呼出側の事実に限る）。M-6 の範囲を広げる目的。
2. C-2 の単位を、命令（`_al` を読む callee の側）と宣言（コメント）で分離した出典表を作る。独立文献が得られない場合は UNKNOWN のまま残す。
3. `tests/fixtures/` に mode 3 / count 1 の非実行フィクスチャを追加する（期待値はすべて UNKNOWN、転送層の計測値のみ既存 T1-T3 を参照）。既存の mode 2 フィクスチャ（PR #420）との対比のため。
4. #365 / #366 / #367 の閉鎖とレビューを待つ。それまで callee の実行は行わない。

## 7. 既存文書との関係（参照のみ、本 PR では編集しない）

* 静的契約: `docs/188-cd-read-transfer-mode-contract.md`（§2 の表と §4 の D1-D4）。
* 安全判定: `docs/188-cd-read-mode-safety.md`（G1-G6）。
* モード 2 の契約: `docs/cd-read-mode-contract.md`（R1-R12）、`docs/cd-read-contract.md`（T1-T4）。
* 機械可読な台帳: `tools/cd_read_contract.py`、`tests/host/test_cd_read_contract.py`。
* CD_BASE（#186、#187）の担当範囲は B-2 のみ参照し、本書では判定しない。

## 8. 制約の遵守

* 純正 BIOS のコード、逆アセンブル、バイト列は本書に転記していない。
* 私有 ROM（`rom/`）は読んでいない。
* 公開 caller の行番号は S1 の取得物に対するもの。コードの引用は命令の事実に限った。
* 推測で確定させた項目はない。確認できない項目はすべて UNKNOWN または HYPOTHESIS と記した。
