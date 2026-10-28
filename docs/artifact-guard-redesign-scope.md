# artifact guard の全面再設計: 別 PR の範囲（Issue #511 / PR #529）

PR #529 は run: 承認（完全一致、承認表は信頼された main 側から読む）の最小修正だけを扱う。
全面再設計は bootstrap の後に別 PR で行う（オーナー判断）。この文書はその別 PR の範囲を定める。

## #529 で閉じたこと（最小修正）

- `tools/check.py` の `make check` 経路が `check_guard_placement` を承認表なしで呼んでいた（fail-open）。
  `enforced_check` を通すように変更し、`tests/host/test_artifact_guard_check_enforced_audit.py` で確認する。

## 別 PR の範囲（#529 では扱わない）

1. Python の import: `tools/` 配下のモジュールを trusted 側が import する経路（`sys.path`、`PYTHONPATH`、同名モジュールの持ち込み）。
   #529 は script ディレクトリを `sys.path` から外すだけで、`import` の全経路は検査していない。
2. シェル: `run:` の文字列の解釈（承認は完全一致だが、承認済みスクリプトが読むファイルや環境変数は見ていない）。
3. Makefile: `make` のターゲットが何を実行するか（`make check` から呼ばれるスクリプトの中身は読まない）。
4. ローカル action: `uses: ./...`（現在は拒否されるが、許可する設計は未定）。
5. 間接呼び出し: 承認済みの `run:` が呼ぶ `tools/*.py`、`tests/*`、`.github/` 配下のスクリプト（内容のハッシュを固定していない）。
6. 動的実行の fail-closed: `eval`、`bash -c`、`$(...)` で組み立てたコマンド、`source` / `.` で読むファイル。
   現状は既知の未保証項目（`tools/artifact_guard.py` の `_run_write_problem` の NOT GUARANTEED 一覧）として記録するだけで、拒否はしない。

## #545 との関係（重ならない）

PR #545（Issue #544 の policy-guard）は次だけを扱う: 信頼された main のポリシー検査ワークフロー（`pull_request_target`、
base 側の定義を使う）、ガード・承認表・ワークフローの main との一致検査、承認済み `run:` が呼ぶスクリプトのハッシュ検査、
必須チェック結果の検査。上の 1〜6 は #545 の範囲に含まれない。#545 は #529 の上に積まれているため、
`tools/check.py` の 1 行変更は #545 と同じファイルに触れる可能性がある（rebase で解決する）。
