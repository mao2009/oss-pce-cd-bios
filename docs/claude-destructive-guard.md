# Claude Code 破壊的コマンド検出フック（PreToolUse guard）

対象: `.claude/hooks/guard_destructive.py`（Python 3 標準ライブラリのみ）
設定: `.claude/settings.json` の `PreToolUse` エントリ（matcher: `Bash|PowerShell`）
テスト: `python3 -m unittest tests/host/test_claude_destructive_guard.py`

## 仕様の根拠（Claude Code 公式ドキュメント）

- 参照: https://code.claude.com/docs/en/hooks （`docs.claude.com` から遷移）
- 入力: stdin の JSON。`tool_name`、`tool_input.command`、`cwd` を使う
- 拒否: stdout に `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "..."}}`、終了コード 0
- 許可: 何も出力しない（終了コード 0）。`allow` を返すと権限確認を迂回するため使わない
- `onFailure: "block"`: 公式ドキュメントによれば v2.1.295 以降で有効。フックの起動失敗、終了コード 0/2 以外、タイムアウト、不正な JSON をすべて拒否扱いにする
- 検証した Claude Code のバージョン: 2.1.311（ドキュメントに基づく。セッション内での実ブロックは未検証）

## 拒否するもの

- `rm`: 単一の通常ファイルで、リポジトリ内、かつ `.git` と `.claude` の外のものだけ許可。再帰（`-r`/`-R`/`-d`/`--recursive`）は常に拒否。ワイルドカード、未展開の `$VAR`、`~user`、シンボリックリンクも拒否
- `git clean` のフラグに `f` を含むもの、`git reset --hard`/`--merge`、`git checkout -f`/`--`/`.`、`git restore`（`--staged` のみ除く）、`git switch -f`/`--discard-changes`、`git branch -D`/`-f`、`git stash clear`/`drop`、強制・削除の `git push`、`git worktree prune`
- `git worktree remove`: 所有者が `.claude/worktree-owners.json` に記録されている場合のみ、`--force` なしで許可。レジストリがない・壊れている場合は全て拒否
- `find -delete`、`find -exec` 内の拒否対象コマンド
- `xargs`、`sudo`、`env`、`command`、`nohup`、`time`、`timeout`、`nice` などのラッパーを剥がして再判定
- `bash -c`、`sh -c`、`zsh -c` などの文字列を再帰的に検査。`-c` を伴わないシェルの起動、`cmd`、`eval`（引数を再検査）、`powershell -EncodedCommand`、`-File` は検査できないため拒否
- `python -c` で削除・プロセス起動・書き込みに該当する語（`rmtree`、`remove`、`unlink`、`subprocess`、`open(` など）を含むもの
- PowerShell の `Remove-Item`/`ri`/`del`/`erase` で `-Recurse`/`-Force` の略記（`-r`、`-fo` など）を含むもの（単一ファイルの場合は rm と同じ規則）
- `unlink`、`shred`、`rmdir`、`rd`、`dd`、`mkfs`、`truncate`、`cmd`
- コマンド置換 `$(...)` とバッククォートの中身も同じ規則で検査し、一つでも拒否なら全体を拒否
- 解析できない入力（不正な JSON、`tool_input` や `command` の欠落、空文字列、未閉じの引用符、`$'...'` など）はすべて拒否

## fail-closed の動作

- 入力の解析失敗、内部例外、5 秒の内部タイムアウトは拒否（`run_hook` が捕捉）
- フック側のタイムアウト（20 秒）と起動失敗は `onFailure: "block"` により拒否
- 拒否理由には種別と理由のみを表示する。コマンド文字列は出力しない（トークンらしき文字列を含んでいても漏れない）

## 残存リスク（必ず読むこと）

- 文字列ベースの PreToolUse フックは任意のコード実行を止められない。`python3 script.py`、`node -e`、`perl -e`、Make ターゲット（`make clean`）、リダイレクト `>` による上書き、`cp`/`mv` による上書きは検査しない
- `python -c` の検査は語の一致による近似であり、回避できる書き方がある
- `.claude/` 配下は同じリポジトリ内のエージェントが編集できる。フック定義そのものを変更されると保護は外れる。そのため、ユーザー側の設定（`~/.claude/settings.json`）で同じフックを有効化するのは、リポジトリ所有者が行うこと
- 追加の対策として、OS レベルの保護を推奨する: 読み取り専用マウント、別の OS アカウントでの実行、コンテナ内での実行
- Windows では shell 形式のフックに Git Bash が必要。見つからない場合は `onFailure` により拒否される
- セッション内で実際にブロックされることは未確認。これは所有者が確認すること（検証では破壊的コマンドを実行しない）
