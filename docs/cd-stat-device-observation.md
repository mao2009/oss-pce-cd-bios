# CD_STAT 相当のデバイス層観測（Issue #231、親 #18）

**状態: 判定器・host テスト・実コア runner を追加。実コアで媒体あり/なしの対照、選択タイムアウト、空白データ盤を確認（939dcb0、§4.1）。最終コミットでの実コア再実行は未了（§4.3）。**
spec=HYPOTHESIS、implementation=DEV_OBSERVATION_ONLY、verification=HOST_ONLY（合成レコード）と実コア（§4.1 の範囲のみ）、real_hardware=NOT_RUN、second_emulator=NOT_RUN、bios_abi=UNKNOWN。
CD_STAT（`$09`、候補 `$E01B`）の呼出先 ABI は未確定であり、本件では呼ばない。BIOS 互換は主張しない。純正 BIOS・商用ゲーム・`./rom/` は読まず、使わない。

## 1. 範囲と非範囲

| 項目 | 扱い |
| --- | --- |
| 合成媒体あり / 媒体なし の正負対照をデバイス層（HuC6280 のデバイス操作経路）で観測 | 対象。実装の本体は #144 の `src/cd_media_state.s`（再利用、再実装しない） |
| 状態・phase・PC・SP/P/MPR・step・RAM（結果ブロックと 2048 バイトのバッファ）の記録 | 判定器が必須項目として要求する。実コア runner `tests/integration/geargrafx_cd_stat_observation.py`（`make dev-cd-stat-observation-integration`）で収集する |
| bounded timeout | 判定器が step 予算を要求し、予算超過・`reason=budget` を拒否する |
| 純正 CD_STAT の呼出し（`JSR $E01B`）、ABI の断定、slot 配線 | 対象外。0 回 |
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
| 新規ホスト単体（正例・負例・timeout 境界・欠落・短いバッファ） | `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.host.test_cd_stat_observation` | 11 件 OK（合成レコード、実コアではない） |
| runner の構文 | `python3 -B -m py_compile tests/integration/geargrafx_cd_stat_observation.py` | OK |
| 既存の CD 関連ホスト試験 | `python -m unittest discover -s tests/host -p 'test_cd_*.py'` | 322 件、OK（skipped=4、cc65 等の環境依存） |
| 全体ホスト試験 | `python -m unittest discover -s tests/host` | 1971 件中、失敗 59・エラー 6 はすべて `test_artifact_guard*`（Windows 環境で確認。本 PR の変更ファイルは参照しない。main での再確認は未実施） |
| 実コア（固定版 Geargrafx + ca65、clean clone） | `make dev-cd-stat-observation-integration` | 939dcb0 で終了コード 0（§4.1）。最終コミットでの再実行は未了（§4.3） |

## 4. 実コアの観測

### 4.1 観測済みの範囲（939dcb0、clean clone、終了コード 0）

ログ: `OBSERVED real Geargrafx device layer: disc -> MEDIA_SIGNATURE_OK, no disc -> NO_DATA; 12 records written`

| 観測 | 結果 |
| --- | --- |
| 媒体あり / なしの paired control（同一 ROM・同一ローダー、盤の有無のみ変更） | 盤あり → `MEDIA_SIGNATURE_OK`、盤なし → `NO_DATA` |
| 選択タイムアウト（`ms_select` を `$1805` へ向けた ROM） | 判定器が拒否（firmware エラー 0x82 = `SELECT_TIMEOUT`） |
| 空白データ盤（MODE1 ヘッダあり、ユーザデータ 0） | 状態 `MEDIA_UNRECOGNISED`、拒否理由 `('MEDIA_UNRECOGNISED', 'NO_DATA')` |

evidence（939dcb0 時点、変更前のフィールド構成）の SHA-256: `ca6f14d0a515b5a5a80021db8283a3fc08366e6d552c254f1dab50e362a545c1`。固定版 Geargrafx の版は `b49ae82a012eade566d72e9d61c9d95a5caa4da3`。ROM と合成 CD の SHA-256 は evidence の `rom_sha256` / `disc` を参照する。

### 4.2 ホスト合成テストのみで確認した負例（実コアでは未確認）

runner には以下の実行も含まれるが、本記録では実コアの観測として扱わない。判定器の挙動は合成レコードの試験（§3）でのみ確認した。

- 無媒体の盤面側（`no_media_as_disc_side`）、盤面と無媒体の入替（`swapped_pair`）
- 0 バイト BIN（`zero_byte_bin`、probe 終了コード 4 を期待）
- step 予算 1000 での `reason=budget`（`step_budget_exhausted`）、judge の step 上限 1（`step_over_judge_bound`）
- 範囲外の judge 入力: budget 0 / -1 / True / 文字列（すべて `BLOCKED: step budget`）

### 4.3 未了

- **最終コミット（本ドキュメントを含むコミット）での実コア再実行。** evidence のフィールド名を変更したため、939dcb0 の evidence の SHA-256 は最終コミットの値と一致しない。最終コミットの終了コード・evidence の SHA-256 は PR 本文に記録する。
- §4.2 の負例の実コア記録。

### 4.4 再実行手順

```sh
git clone <リポジトリの URL> <空のディレクトリ> && cd <空のディレクトリ>
git checkout --detach <対象コミット> && test -z "$(git status --porcelain)"
export TOOLS_DIR=<ツールの配置先>
make dev-cd-stat-observation-integration; echo "exit=$?"    # build/debug/cd-stat-observation/cd-stat-observation-evidence.json
sha256sum build/debug/cd-stat-observation/cd-stat-observation-evidence.json
```

`make dev-cd-stat-observation-integration` は `dev-cd-media-state`（#144 の ROM ビルド）に依存する。
#231 の実コア結果として記録するには、終了コード 0、evidence の SHA-256、固定版 Geargrafx の版・SHA、ROM SHA を PR 本文に記録すること。

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
| 実コアの正例・負例 | **一部充足** | §4.1（媒体あり/なし、選択タイムアウト、空白盤）。最終コミットの再実行と §4.2 の負例は未了 |

**判定: #232 は BLOCKED。着手しない。** 解除条件は次のとおり。
1. callee の ABI（A の入力、戻り A と X/Y、保存レジスタ）を公開根拠で確定するか、公開可能な実行可能 callee で実測する（`cd-stat-contract.md` §6）。
2. 媒体なし・busy・不正 mode の実測で F24・F25 を埋める。
3. 最終コミットの実コア再実行（§4.3）と、§4.2 の負例の実コア記録を取得する。

## 6. 残課題

- 最終コミットでの実コア再実行（§4.3）と、§4.2 の負例の実コア記録。
- 実機・第 2 エミュレータ: NOT_RUN。
