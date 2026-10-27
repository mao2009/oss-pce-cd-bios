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
- `bash -c`、`sh -c`、`zsh -c` などの文字列、`eval`・`source`・`.` の引数を再帰的に検査する。ただし、これらに渡すコマンド置換・プロセス置換（`$(...)`、バッククォート、`<(...)`、`>(...)`）は中身を知れないため拒否する。`-c` を伴わないシェルの起動、`cmd`、`powershell -EncodedCommand`、`-File` も拒否
- PowerShell の `iex`、`Invoke-Expression`、文字列または変数を対象とする `&` 呼び出し演算子（`& "x"`、`& $cmd`）は中身を検査できないため拒否。`pwsh -c "$(...)"` も拒否
- `git -c alias.*` と、環境変数（`GIT_CONFIG_*` など）による alias 設定を拒否。`git -C dir` や `--git-dir` の後に続く破壊的なサブコマンドは通常どおり拒否
- `.claude/` への書き込みを拒否: `>` `>|` `>>` の後の書き込み先（クォートと引用符外のバックスラッシュ回避を除いた形と、生の形の両方で判定。例: `.cl\aude/f` は `.claude/f` として拒否）が `.claude` を含む場合、`cd .claude` の後の書き込み、`tee`・`cp`・`mv`・`install`・`rsync`・`touch`・`mkdir`・`ln`・`chmod`・`curl`・`wget`・`tar`・`sed -i`・`dd of=` などの引数に `.claude` を含む場合、`python`・`perl`・`node` 等の引数に `.claude` を含む場合。読み取り（`cat`、`ls`、`git status`）は許可
- 書き込み先が静的に決まらない場合を拒否: 書き込み先に未展開の `$NAME`、`${NAME}`、`$(...)`、バッククォート、glob（`*?[]{}`）を含む場合（例: `echo x > docs/$NAME.md`、`X=.claude; echo x > $X/f`）。書き込み系コマンド（`tee`・`cp`・`touch` など）の引数に `$` やバッククォートを含む場合も拒否する（`sed -i 's/a$/b/'` のような正規表現も拒否される。過剰な拒否である）。`echo $X` のような読み取りは許可
- 64 KB（UTF-8 バイト数）を超えるコマンドは "too large to inspect" として拒否する。正規表現を使わず語の線形走査で判定するため、上限内の入力は処理時間が抑えられる
- `git push` の refspec 削除（`:branch`、`+:branch`）と `--prune` を拒否
- `python -c` で削除・プロセス起動・書き込みに該当する語（`rmtree`、`remove`、`unlink`、`subprocess`、`open(` など）を含むもの
- PowerShell の `Remove-Item`/`ri`/`del`/`erase` で `-Recurse`/`-Force` の略記（`-r`、`-fo` など）を含むもの（単一ファイルの場合は rm と同じ規則）
- `unlink`、`shred`、`rmdir`、`rd`、`dd`、`mkfs`、`truncate`、`cmd`
- コマンド置換 `$(...)` とバッククォートの中身も同じ規則で検査し、一つでも拒否なら全体を拒否。コマンド位置に置換がある場合も拒否
- 空の引用符の組（`r""m`）は除去してから判定する
- 解析できない入力（不正な JSON、`tool_input` や `command` の欠落、空文字列、未閉じの引用符、`$'...'`、NUL や CR などの制御文字、UTF-8 として復号できない標準入力など）はすべて拒否

## fail-closed の動作

- 入力の解析失敗、内部例外は拒否（`run_hook` が捕捉）
- `run_hook` の 5 秒スレッドタイムアウトは best-effort である。Python のバイトコードを実行中の処理は途中で打ち切られて拒否されるが、C 実装の処理（正規表現など）は GIL を保持したまま走り、5 秒を過ぎても打ち切れない（検証: C 側の処理は 42 秒かかり、結果は拒否ではなく完了した判定になった）。したがって時間の上限はこのタイムアウトに頼らない。実際の上限は 64 KB のサイズ上限と線形の走査である
- フック側のタイムアウト（20 秒、`.claude/settings.json`）と起動失敗は `onFailure: "block"` により拒否（Claude Code 側の仕組み。セッション内では未確認）
- 拒否理由には種別と理由のみを表示する。コマンド文字列は出力しない（トークンらしき文字列を含んでいても漏れない）

## 残存リスク（必ず読むこと）

- 文字列ベースの PreToolUse フックは任意のコード実行を止められない。iex と Invoke-Expression は拒否するが、任意のインタプリタ（`python3 script.py`、`node -e`、`perl -e`、`ruby`、`busybox` などの多機能バイナリ）の実行は許可される
- Make ターゲット（`make clean`）、`source file` や `. file` による通常ファイルの実行、`awk` の `system()`、`.git/config` に定義された alias（`-c` 以外）は検査しない
- `.claude/` への書き込み拒否は文字列による判定であり、完全ではない。`.claude` を指す symlink やジャンクション（Windows）は検出できない。変数、未知のコマンド、間接的な書き方（`eval`、インタプリタ経由など）で文字列判定を回避した書き込みは通りうる。書き込み先が未展開なら拒否する規則はあるが、それで網羅されるわけではない
- 同じく、`python`・`perl`・`node` などで組み立てたパスへの書き込みは、引数に `.claude` が現れない限り検出できない
- `python -c` の検査は語の一致による近似であり、回避できる書き方がある
- `.claude/worktree-owners.json` は暗号や署名で守られていない。書き込みの拒否規則だけで守られており、この規則を回避する書き方があれば所有者の記録は改ざんされうる
- `.claude/` 配下は同じリポジトリ内のエージェントが編集できる。フック定義そのものを変更されると保護は外れる。そのため、ユーザー側の設定（`~/.claude/settings.json`）で同じフックを有効化するのは、リポジトリ所有者が行うこと
- 追加の対策として、OS レベルの保護を推奨する: 読み取り専用マウント、別の OS アカウントでの実行、コンテナ内での実行
- Windows では shell 形式のフックに Git Bash が必要。見つからない場合は `onFailure` により拒否される
- セッション内で実際にブロックされることは未確認。これは所有者が確認すること（検証では破壊的コマンドを実行しない）
