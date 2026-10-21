# CD_STAT 相当のデバイス層観測（Issue #231、親 #18）

**状態: 判定器・host テスト・実コア runner を追加。実コア（c719ee4、終了コード 0）で媒体あり/なしの対照、選択タイムアウト、空白データ盤の拒否を確認。§4.2 の負例も同じ runner で実コア実行し、期待する拒否理由を確認（§4.1、§4.2）。**
spec=HYPOTHESIS、implementation=DEV_OBSERVATION_ONLY、verification=HOST_ONLY（合成レコード）と実コア（§4.1、§4.2 の範囲のみ）、real_hardware=NOT_RUN、second_emulator=NOT_RUN、bios_abi=UNKNOWN。
CD_STAT（`$09`、候補 `$E01B`）の呼出先 ABI は未確定であり、本件では呼ばない。BIOS 互換は主張しない。純正 BIOS・商用ゲーム・`./rom/` は読まず、使わない。

## 1. 範囲と非範囲

| 項目 | 扱い |
| --- | --- |
| 合成媒体あり / 媒体なし の正負対照をデバイス層（HuC6280 のデバイス操作経路）で観測 | 対象。実装の本体は #144 の `src/cd_media_state.s`（再利用、再実装しない） |
| 状態・phase・PC・SP/P/MPR・step・RAM（結果ブロックと 2048 バイトのバッファ）の記録 | 判定器が必須項目として要求する。実コア runner `tests/integration/geargrafx_cd_stat_observation.py`（`make dev-cd-stat-observation-integration`）で収集する |
| bounded timeout | 判定器が step 予算を要求し、予算超過・`reason=budget` を拒否する |
| 純正 CD_STAT の呼出し（`JSR $E01B`）、ABI の断定、slot 配線 | 対象外。呼出し 0 回。これは宣言値 `cd_stat_called: False` であり、根拠は ROM ソース（`src/cd_media_state.s` に `$E01B` の参照がないこと）。実行時には計測・検証していない |
| #232（CD_STAT ROM-Primitive） | 実装しない。開始条件の評価のみ（§5） |

## 2. #144 との関係（重複実装しない）

- 媒体判定の SCSI 手順（READ(6) LBA 0、select/command/data/status、bounded poll）は #144 の `src/cd_media_state.s` のまま。本 PR では変更しない。
- 判定そのもの（cold/warm の一致、paired control の一方向性、NO_DATA と DATA_IN の整合）は `tools/build_cd_media_state.py` の `judge` / `check_pair` をそのまま使う。
- 本 PR で追加するのは `tools/cd_stat_observation.py` の `judge_observation` だけで、次の 2 点を加える。
  1. 各 RUN が `reason != budget` かつ `step <= budget`（bounded timeout の負例を含む）。
  2. `pc`, `sp`, `p`, `mpr`, `step`, `reason`, `result_hex`, `buffer_hex` の欠落を BLOCKED として拒否。
- #144 の実コア結果（`tests/integration/geargrafx_cd_media_state.py`）を #231 の実コア結果として扱わない。

## 3. 検証

| 検証 | コマンド | 結果 |
| --- | --- | --- |
| 新規ホスト単体（正例・負例・timeout 境界・欠落・短いバッファ・予算入力の検証） | `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.host.test_cd_stat_observation` | 11 件 OK（合成レコード、実コアではない） |
| runner の構文 | `python3 -B -m py_compile tests/integration/geargrafx_cd_stat_observation.py` | OK |
| 既存の CD 関連ホスト試験 | `python -m unittest discover -s tests/host -p 'test_cd_*.py'` | 322 件、OK（skipped=4、cc65 等の環境依存） |
| 全体ホスト試験 | `python -m unittest discover -s tests/host` | 1971 件中、失敗 59・エラー 6 はすべて `test_artifact_guard*`（Windows 環境で確認。本 PR の変更ファイルは参照しない。main での再確認は未実施） |
| 実コア（固定版 Geargrafx + ca65、clean clone） | `make dev-cd-stat-observation-integration` | c719ee4 で終了コード 0（§4.1）。§4.2 の負例も同じ実行で拒否を確認 |

## 4. 実コアの観測

### 4.1 実コアで確認した範囲（c719ee4、clean clone、終了コード 0）

ログ: `OBSERVED real Geargrafx device layer: disc -> MEDIA_SIGNATURE_OK, no disc -> NO_DATA; 12 records written`
evidence の SHA-256: `45862da4ec88df4d2109bdc49cc5c9480ec34ba3fa0e90fb7b0e52982eea674b`
固定版 Geargrafx: `b49ae82a012eade566d72e9d61c9d95a5caa4da3`。ROM と合成 CD の SHA-256 は evidence の `rom_sha256` / `disc` を参照する。

| 観測 | 結果 | 確認されたコミット |
| --- | --- | --- |
| 媒体あり / なしの paired control（同一 ROM・同一ローダー、盤の有無のみ変更） | 盤あり → `MEDIA_SIGNATURE_OK`、盤なし → `NO_DATA` | 939dcb0 から確認 |
| 選択タイムアウト（`ms_select` を `$1805` へ向けた ROM） | 判定器が拒否（firmware エラー 0x82 = `SELECT_TIMEOUT`） | 939dcb0 から確認（拒否理由まで照合） |
| 空白データ盤（MODE1 ヘッダあり、ユーザデータ 0） | 状態 `MEDIA_UNRECOGNISED`、拒否理由 `('MEDIA_UNRECOGNISED', 'NO_DATA')` | c719ee4 で照合を追加。939dcb0 では拒否のみで、汎用メッセージ「paired judgment did not follow the disc」を照合していた |

### 4.2 負例の実コア実行（c719ee4、同じ runner、終了コード 0）

同じ runner は以下の負例も実コア（同一 ROM・同一ローダー）で実行し、各負例について runner の `rejected()` で期待する拒否理由（部分一致）を確認している。

- `no_media_as_disc_side`: 盤面側が無媒体の対照を拒否（理由: `not exactly one-sided`）
- `swapped_pair`: 盤あり/なしを入れ替えた対照を拒否（理由: `not exactly one-sided`）
- `zero_byte_bin`: 0 バイト BIN は実コアの読込前に拒否（probe 終了コード 4）
- `step_budget_exhausted`: 予算 1000 で打ち切った実コア記録（`reason=budget`）を judge が拒否（理由: `not bounded by the step budget`）
- `step_over_judge_bound`: 実コアの記録に対し judge の step 上限を 1 として拒否（理由: `not bounded by the step budget`）。上限値は judge への入力であり、実コアへの入力ではない
- `budget_zero` / `budget_negative` / `budget_boolean` / `budget_string`: judge の予算入力の型・範囲の検証（理由: `BLOCKED: step budget`）。これは判定器（`tools/cd_stat_observation.py`）の入力検証であり、実コアへの入力ではない。単体の確認は §3 の host 試験による

### 4.3 未了と注意

- 実機・第 2 エミュレータ: NOT_RUN（§1 の宣言値と一致）。
- c719ee4 以降のコミットでは、runner のコメントと docs のみを変更し、実行されるコードは変えていない。evidence の SHA-256 は c719ee4 の実行結果のとおり。
- 939dcb0 は evidence のフィールド構成が現行と異なる旧版（実機・第 2 エミュレータ・CD_STAT 呼出しの宣言がトップレベル、`status` は固定値 PASS）。その evidence の SHA-256 は `ca6f14d0a515b5a5a80021db8283a3fc08366e6d552c254f1dab50e362a545c1` であり、現行の evidence とは比較しない。

### 4.4 再実行手順

```sh
git clone <リポジトリの URL> <空のディレクトリ> && cd <空のディレクトリ>
git checkout --detach <対象コミット> && test -z "$(git status --porcelain)"
export TOOLS_DIR=<ツールの配置先>
make dev-cd-stat-observation-integration; echo "exit=$?"    # build/debug/cd-stat-observation/cd-stat-observation-evidence.json
sha256sum build/debug/cd-stat-observation/cd-stat-observation-evidence.json
```

`make dev-cd-stat-observation-integration` は `dev-cd-media-state`（#144 の ROM ビルド）に依存する。
実コア結果の記録項目は、終了コード、evidence の SHA-256、固定版 Geargrafx の版・SHA、ROM SHA。

## 5. #232 の実装開始条件の評価

#232 の着手条件は「caller/callee の入力・出力・保存レジスタが証拠付きで裁定済み」「既存 #144 の処理を再利用」「実コアの正例・負例」。`docs/research/cd-stat-contract.md` と照合した結果:

| 条件 | 判定 | 根拠 |
| --- | --- | --- |
| 呼出先の mode 入力（A）の解釈 | **未充足** | F04 HYPOTHESIS（呼出側の `$00`/`$01` 2 経路のみ） |
| 戻り A の意味（busy / ready） | **未充足** | F11・F12 HYPOTHESIS |
| 媒体なし・待機中の戻り値 | **未充足** | F24 UNKNOWN（出典なし、実測なし） |
| 失敗条件・エラー値 | **未充足** | F25 UNKNOWN |
| 作業 RAM・X/Y・フラグ | **未充足** | F05–F08、F13、F16 UNKNOWN |
| #144 再利用 | 充足（設計上） | `src/cd_media_state.s` |
| 実コアの正例・負例 | **充足**（本 PR の範囲） | c719ee4 の実コア実行。§4.1（正例、選択タイムアウト、空白盤）と §4.2（負例） |

**判定: #232 は BLOCKED。着手しない。** 解除条件は次のとおり。
1. callee の ABI（A の入力、戻り A と X/Y、保存レジスタ）を公開根拠で確定するか、公開可能な実行可能 callee で実測する（`cd-stat-contract.md` §6）。
2. 媒体なし・busy・不正 mode の実測で F24・F25 を埋める。

## 6. 残課題

- 実機・第 2 エミュレータ: NOT_RUN。
- 上記 §5 の解除条件 1 と 2（CD_STAT callee の ABI）。
