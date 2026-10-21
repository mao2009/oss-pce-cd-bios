# CD_STAT 相当のデバイス層観測（Issue #231、親 #18）

**状態: 判定器・host テスト・実コア runner を追加。実コア実行は NOT_RUN（WSL 実行が worktree ガードで拒否、§4）。**
spec=HYPOTHESIS、implementation=DEV_OBSERVATION_ONLY、verification=HOST_ONLY（合成レコード）、real_hardware=NOT_RUN、real_core=NOT_RUN。
CD_STAT（`$09`、候補 `$E01B`）の呼出先 ABI は未確定であり、本件では呼ばない。純正 BIOS・商用ゲーム・`./rom/` は読まず、使わない。

## 1. 範囲と非範囲

| 項目 | 扱い |
| --- | --- |
| 合成媒体あり / 媒体なし の正負対照をデバイス層（HuC6280 のデバイス操作経路）で観測 | 対象。実装の本体は #144 の `src/cd_media_state.s`（再利用、再実装しない） |
| 状態・phase・PC・SP/P/MPR・step・RAM（結果ブロックと 2048 バイトのバッファ）の記録 | 判定器が必須項目として要求する。実コア runner `tests/integration/geargrafx_cd_stat_observation.py`（`make dev-cd-stat-observation-integration`）で収集する。未実行 |
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
| 既存の CD 関連ホスト試験 | `python -m unittest discover -s tests/host -p 'test_cd_*.py'` | 322 件、OK（skipped=4、cc65 等の環境依存） |
| 全体ホスト試験 | `python -m unittest discover -s tests/host` | 1971 件中、失敗 59・エラー 6 はすべて `test_artifact_guard*`（Windows 環境で本回確認。本 PR の変更ファイルは参照しない。main での再確認は未実施） |
| 実コア（固定版 Geargrafx + ca65、WSL clean clone） | `make dev-cd-stat-observation-integration`（#231 runner、コミット `f17b428`） | **NOT_RUN**（下記 §4）。終了コード・evidence の SHA-256 は取得していない |

## 4. 実コアが NOT_RUN の理由

1. **WSL 実行が worktree ガードで拒否された（BLOCKED、迂回せず停止）。** 対象コミット `f17b428bc37a800d73f9eab9a51ec3ae764be0a2` を `wave5/06-231` へ push 後、WSL 側に clean clone（`/home/loach/w5-b3-409`）を作る `wsl.exe -d Ubuntu -u loach -- bash <script>` が「worktree 隔離エージェントの git 操作は worktree 内に限る」として拒否された。`MSYS_NO_PATHCONV=1` の付与のみを行い、ガードの回避・PowerShell 経由の迂回は行っていない。
2. したがって、WSL clone の HEAD・`git status`、固定版 Geargrafx（b49ae82a）の版・SHA、ROM SHA、合成 CD SHA、実コア上の各コマンドの終了コード、正例・負例・timeout の判定は、いずれも**記録できない**。evidence の SHA-256 も存在しない。
3. 実コアの runner（§1）は実装済みで、`geargrafx_cd_stat_observation.py` のホスト側の構文確認（`py_compile`）のみ済み。実行は未確認。

### 実行対象と負例（runner の内容、未実行）

| 区分 | 内容 |
| --- | --- |
| 正例 | 合成 CUE/BIN（`card:<cue>`）と無媒体（`card:-`）の paired control。`judge_observation` で `MEDIA_SIGNATURE_OK` / `NO_DATA` |
| 負例（判定で拒否） | 無媒体の盤面側、盤面と無媒体の入替、空白データ盤（`MEDIA_UNRECOGNISED` が盤面側に来る）、選択 timeout（`$1805` 誘導で `SELECT_TIMEOUT`） |
| 負例（実行で拒否） | 0 バイト BIN（probe 終了コード 4 を期待）、step 予算 1000 での `reason=budget` |
| 範囲外の入力 | judge の step 上限 1、budget 0 / -1 / True / 文字列（すべて `BLOCKED: step budget`） |
| 記録 | PC・SP・P・MPR・step・reason・結果状態・SCSI phase・RAM 2048 バイトの SHA-256（盤面バイトは記録しない） |

### 次の実行（WSL の clean clone で実施する場合）

```sh
git clone https://github.com/mao2009/oss-pce-cd-bios /home/loach/w5-b3-409 && cd /home/loach/w5-b3-409
git checkout --detach f17b428bc37a800d73f9eab9a51ec3ae764be0a2 && test -z "$(git status --porcelain)"
export TOOLS_DIR=/home/loach/oss-pce-cd-bios-tools
make dev-cd-stat-observation-integration; echo "exit=$?"    # build/debug/cd-stat-observation/cd-stat-observation-evidence.json
```

`make dev-cd-stat-observation-integration` は `dev-cd-media-state`（#144 の ROM ビルド）に依存する。
#231 を実コア PASS とするには、終了コード 0、evidence の SHA-256、固定版 Geargrafx の版・SHA、ROM SHA を PR 本文に記録すること。

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

- 実コア実測（WSL clean clone での `make dev-cd-stat-observation-integration`、worktree ガード解除後に実施）。
- 実機・第 2 エミュレータでの確認。
- 実機・第 2 エミュレータ: NOT_RUN。
