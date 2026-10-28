# 信頼された main のポリシー検査：オーナー承認手順（Issue #511 / #544 の bootstrap）

この文書はオーナーが手作業で行う手順である。コードを変更しただけでは信頼境界は完成しない。
以下の手順が完了するまで、run: 承認（tools/artifact_guard_run_allowlist.json）と policy-guard は
信頼された根拠として扱わない。PR #529 と #545 はいずれもドラフトのままであり、マージしない。

## 1. 初回導入の境界（自己保護はできない）

- policy-guard ワークフロー（`.github/workflows/policy-guard.yml`）は、それ自身が main にマージされるまで
  main 側で実行されない。導入 PR の中では、PR 側のワークフローが自分を検査することはできない。
- したがって、初回導入の信頼は「main にマージされた時点の内容」と「オーナーがそれを確認した事実」にしか置けない。
- 導入 PR の内容は、導入前に人間が全ファイルを読む（特に `.github/workflows/` と `tools/policy_guard.py`、
  `tools/artifact_guard.py`、`tools/artifact_guard_run_allowlist.json`、`Makefile`）。
- 導入後も、ワークフローの sparse-checkout が承認表（`tools/artifact_guard_run_allowlist.json`）を
  `.trusted-main` に含むことを確認する。含まれない場合、ガードは fail closed で全 CI を拒否する（想定どおりの安全側）。

## 2. 必須チェックの登録（リポジトリ設定。本手順は変更しない）

1. 必須ステータスチェックの名前は `policy-guard` とする（ワークフローの `name` と job 名の両方）。
2. チェックの発行元（GitHub Actions の integration ID）を、ルールセットで固定する。
   他のアプリが同名のチェックを出しても受け付けない（`check_record_problems` の `app_id` 検査に対応）。
3. 「スキップされたチェックを成功扱いにしない」設定を確認する。skipped は不合格として扱う。
4. チェックの対象は PR の head SHA とする。別の SHA の結果を再利用しない（stale 結果の拒否）。
5. ルールセットと権限は、オーナーが GitHub の画面または管理 API で設定する。AI エージェントは変更しない。

## 3. GitHub App の identity 固定

- 検査結果の `app_id` は、オーナーが確認した GitHub Actions の App ID に固定する。
- 別のアプリや手動で作られた同名の結果は、この ID と一致しないため拒否される。
- 固定値を変更する場合も、本手順の承認を経る。

## 4. 対象 HEAD SHA への対応

- 検査は `pull_request_target` で base SHA の信頼コピー（`.trusted`）を実行し、PR の head SHA は
  データとしてのみ読む（`.candidate`）。
- マージ直前には、チェック結果の head SHA が PR の現在の head SHA と一致することを確認する。
  一致しない場合（PR が更新された、結果が古い）は、再実行されるまでマージしない。

## 5. チェックが無い・SKIPPED の場合

- policy-guard の結果が存在しない、名前が違う、`skipped` / `cancelled` / `neutral` である場合は、
  不合格として扱う。PR の作成者がワークフローを編集して skip させた場合も同様に拒否される（ケース f）。
- 例外として承認することはしない。原因を直してから再実行する。

## 6. ポリシー変更のレビュー手順

ポリシー変更とは、次のいずれかを含む PR である。

- `tools/artifact_guard.py`、`tools/policy_guard.py`、`tools/artifact_guard_run_allowlist.json`
- `.github/workflows/`、`.github/actions/`
- 特権ジョブが実行するファイル（`Makefile`、`tools/`、`tests/` の実行される部分、`scripts/`、
  ローカル action）。`policy_guard.py` の閉包（closure）で求められるもの。

手順:

1. 変更は独立したポリシー PR として出す。通常の開発 PR と混ぜない。
2. 変更内容を、独立したレビュー（PR の差分と要件だけを与えた、作成者と別の人またはエージェント）で確認する。
   レビュー結果の CRITICAL / MAJOR が 0 であることを確認する。
3. オーナーが、差分の全行と閉包の一覧（`policy_guard.closure_of` の出力）を確認して承認する。
4. 承認後に main へマージする。その後、通常開発 PR の policy-guard が main の内容と一致して通る。
5. 閉包に含まれるファイルを変えない通常開発 PR は、本ポリシーで停止しない（特権ジョブが実行しないファイルは対象外）。

## 7. 初回導入時の信頼境界

- 信頼境界は「main にマージされた policy-guard と承認表」である。
- 閉包検査は静的解析であり、実行時に組み立てられるパス、外部ツリー（`tools/setup.py` が
  チェックサム検証付きで取得する Geargrafx のソース）、ネットワーク送信そのものは証明できない。
  外部ツリーは `tools/environment.py` の DEPENDENCIES のチェックサムに依存する。
- 特権ジョブの定義（write 権限、environment、pull_request_target）を変える場合も、ポリシー変更として扱う。
- 非特権ジョブ（`contents: read` のみで secret を持たない検証ジョブ）は閉包の対象外である。
  その run: は承認表で文字列一致のみ検査される。非特権ジョブの出力は、信頼されたガードが公開前に検査する。
  この境界を狭めるか（ジョブ分割で非特権 CI を完全に分離するか）は、オーナーの判断事項である。

## 8. 完了の判定

次の全てを満たした時点で、オーナーは信頼境界が成立したと判断してよい。

- [ ] 導入 PR の全ファイルを読んだ。
- [ ] main にマージされ、policy-guard が main で成功した。
- [ ] 必須チェック `policy-guard` がルールセットで登録され、app_id が固定された。
- [ ] skipped / missing / 別 SHA の結果が拒否されることを、テストまたは手動の試行で確認した。
- [ ] 承認表の sparse-checkout が `.trusted-main` / `.trusted` に含まれている。
- [ ] 本文書のオーナー承認欄に記入した。

承認欄: ____________ 日付: ____________
