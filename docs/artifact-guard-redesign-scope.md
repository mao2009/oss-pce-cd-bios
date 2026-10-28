# artifact guard の全面再設計: 別 PR の範囲（Issue #511 / PR #529）

PR #529 は run: 承認（完全一致、承認表は信頼された main 側から読む）の最小修正だけを扱う。
全面再設計は bootstrap の後に別 PR で行う（オーナー判断）。この文書はその別 PR の範囲を定める。

## #529 の監査結果

- 信頼された main 側のガード（ci.yml / api-source.yml / pages.yml などの guard ジョブ）は、全ワークフローの `run:` を
  main 側の承認表（完全一致、ダイジェスト）で検査する。監査で注入した 16 件（run: 追加、名前変更、if: 追加、flow 形式、
  アンカー、重複キー、github-script、ローカル uses、composite action、トップレベル env / defaults、大文字の .YML など）
  はすべて拒否された。承認表は候補（PR）の木ではなく信頼された main の木から読まれる。
- 残存: `tools/check.py` の `make check`（ci.yml の verify ジョブ）は `check_guard_placement` を承認表なしで呼ぶ（fail-open）。
  ただし公開の門は信頼された guard ジョブであり、verify ジョブのみで注入された run: はそこで拒否される。
  `make check` を強制するには信頼された承認表が必要で、検査を候補の木の承認表に向けると自己承認になるため、
  #529 では変更しない。別 PR で扱う。

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
