# CD_STAT 相当のデバイス層観測（Issue #231、親 #18）

**状態: 判定器と host テストのみ。実コア観測は NOT_RUN。**
spec=HYPOTHESIS、implementation=DEV_OBSERVATION_ONLY、verification=HOST_ONLY（合成レコード）、real_hardware=NOT_RUN。
CD_STAT（`$09`、候補 `$E01B`）の呼出先 ABI は未確定であり、本件では呼ばない。純正 BIOS・商用ゲーム・`./rom/` は読まず、使わない。

## 1. 範囲と非範囲

| 項目 | 扱い |
| --- | --- |
| 合成媒体あり / 媒体なし の正負対照をデバイス層（HuC6280 のデバイス操作経路）で観測 | 対象。実装の本体は #144 の `src/cd_media_state.s`（再利用、再実装しない） |
| 状態・phase・PC・SP/P/MPR・step・RAM（結果ブロックと 2048 バイトのバッファ）の記録 | 判定器が必須項目として要求する。収集器（実コア runner）は本 PR に含めない |
| bounded timeout | 判定器が step 予算を要求し、予算超過・`reason=budget` を拒否する |
| 純正 CD_STAT の呼出し（`JSR $E01B`）、ABI の断定、slot 配線 | 対象外。0 回 |
| #232（CD_STAT ROM-Primitive） | 実装しない。開始条件の評価のみ（§5） |

## 2. #144 との関係（重複実装しない）

- 媒体判定の SCSI 手順（READ(6) LBA 0、select/command/data/status、bounded poll）は #144 の `src/cd_media_state.s` のまま。本 PR では変更しない。
- 判定そのもの（cold/warm の一致、paired control の一方向性、NO_DATA と DATA_IN の整合）は `tools/build_cd_media_state.py` の `judge` / `check_pair` をそのまま使う。
- 本 PR で追加するのは `tools/cd_stat_observation.py` の `judge_observation` だけで、次の 2 点を加える。
  1. 各 RUN が `reason != budget` かつ `step <= budget`（bounded timeout の負例を含む）。
  2. `pc`, `sp`, `p`, `mpr`, `step`, `reason`, `result_hex`, `buffer_hex` の欠落を BLOCKED として拒否。
- #144 の実コア結果（`tests/integration/geargrafx_cd_media_state.py`）を #231 の実コア PASS として扱わない。#231 の実コア観測は別途の実行記録が必要。

## 3. 本回の検証

| 検証 | コマンド | 結果 |
| --- | --- | --- |
| 新規ホスト単体（正例・負例・timeout 境界・欠落・短いバッファ） | `python -m unittest tests.host.test_cd_stat_observation -v` | 11 件 OK（合成レコード、実コアではない） |
| 既存の CD 関連ホスト試験 | `python -m unittest discover -s tests/host -p 'test_cd_*.py'` | 318 件、OK（skipped=4、cc65 等の環境依存） |
| 全体ホスト試験 | `python -m unittest discover -s tests/host` | 1971 件中、失敗 59・エラー 6 はすべて `test_artifact_guard*`（Windows 環境で本回確認。本 PR の変更ファイルは参照しない。main での再確認は未実施） |
| 実コア（固定版 Geargrafx + ca65） | `make dev-cd-media-state-integration`、#231 用の観測 runner | **NOT_RUN**（下記 §4） |

## 4. 実コアが NOT_RUN の理由

1. **WSL 実行が実行ガードで拒否された。** 本 worktree 専用エージェントから `wsl.exe` を呼ぶコマンドは、`MSYS_NO_PATHCONV=1` 付きでも、worktree 内の `cd` 付きでも拒否された。ガードは迂回していない。
2. **Windows ホストに ca65 / ld65 / make がない。** 自作 ROM の再ビルドはできない（`docs/cdseek-device-observation.md` と同じ状況）。
3. したがって、固定版 Geargrafx の版・SHA、ROM SHA、合成 CD SHA、観測コマンドの終了コード、実コア上の正例・負例・timeout の結果は、いずれも**記録できない**。

### 次の実行（オーナーまたは WSL 環境で実施する場合）

```sh
make setup
make dev-cd-media-state                       # build/<mode>/cd-media-state を生成（#144 の既存手順）
make dev-cd-media-state-integration           # 固定版 Geargrafx、媒体あり/なしの対照（#144 の既存結果の再確認）
```

#231 固有の観測 runner（PC・RAM・phase・step の収集）は未実装で、上記は #144 の既存結果の再確認にすぎない。
#231 を実コア PASS とするには、runner が §2 の必須項目を出力し、`judge_observation` を通すこと、ならびに固定版 Geargrafx の版・SHA・ROM SHA・終了コードを記録することが必要。

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
| 実コアの正例・負例 | **未充足（本回 NOT_RUN）** | §4 |

**判定: #232 は BLOCKED。着手しない。** 解除条件は次のとおり。
1. callee の ABI（A の入力、戻り A と X/Y、保存レジスタ）を公開根拠で確定するか、公開可能な実行可能 callee で実測する（`cd-stat-contract.md` §6）。
2. 媒体なし・busy・不正 mode の実測で F24・F25 を埋める。
3. §4 の実コア記録（正例・負例・timeout）を取得する。

## 6. 残課題

- #231 固有の観測 runner（PC・RAM・phase の収集）の実装。
- 実コア実測（WSL 環境での実行）。
- 実機・第 2 エミュレータ: NOT_RUN。
