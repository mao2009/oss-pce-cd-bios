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
- `git worktree remove`: **常に拒否**。`.claude/worktree-owners.json` に所有者が記録されていても許可しない。`--force` の有無、対象パス、Owner 承認の有無にかかわらず、このフック経由の削除は禁止（所有者登録は削除権限を意味しない）
- `find -delete`、`find -exec` 内の拒否対象コマンド
- `xargs`、`sudo`、`env`、`command`、`nohup`、`time`、`timeout`、`nice` などのラッパーを剥がして再判定
- ラッパー自身の書き込みも検査する: GNU `time -o`（指定ファイルへ計測結果を書き込む）は形にかかわらず常に拒否。`flock` のロックファイルは、存在しなければ新規作成されるため、`.claude/` 内と `.git` 内に作る場合は拒否する（リポジトリ外のロックファイルは従来どおり許可。数値のみの場合は開いているファイル記述子で、何も作成しない）。`nohup` はカレントディレクトリが `.claude/` 内の場合、`nohup.out` への追記が起きるため拒否。`flock`・`nohup` の書き込み判定では、存在するカレントディレクトリの symlink を実体パスに解決し、`.claude/`・`.git` への symlink 経由の誤許可を防ぐ
- `bash -c`、`sh -c`、`zsh -c` などの文字列、`eval`・`source`・`.` の引数を再帰的に検査する。ただし、これらに渡すコマンド置換・プロセス置換（`$(...)`、バッククォート、`<(...)`、`>(...)`）は中身を知れないため拒否する。`-c` を伴わないシェルの起動、`cmd`、`powershell -EncodedCommand`、`-File` も拒否
- PowerShell の `iex`、`Invoke-Expression`、文字列または変数を対象とする `&` 呼び出し演算子（`& "x"`、`& $cmd`）は中身を検査できないため拒否。`pwsh -c "$(...)"` も拒否
- `git -c alias.*` と、環境変数（`GIT_CONFIG_*` など）による alias 設定を拒否。`git -C dir` や `--git-dir` の後に続く破壊的なサブコマンドは通常どおり拒否
- `.claude/` への書き込みを拒否: `>` `>|` `>>` の後の書き込み先（クォートと引用符外のバックスラッシュ回避を除いた形と、生の形の両方で判定。例: `.cl\aude/f` は `.claude/f` として拒否）が `.claude` を含む場合、`cd .claude` の後の書き込み、`tee`・`cp`・`mv`・`install`・`rsync`・`touch`・`mkdir`・`ln`・`chmod`・`curl`・`wget`・`tar`・`sed -i`・`dd of=` などの引数に `.claude` を含む場合、`python`・`perl`・`node` 等の引数に `.claude` を含む場合。読み取り（`cat`、`ls`、`git status`）は許可
- 書き込み先が静的に決まらない場合を拒否: 書き込み先に未展開の `$NAME`、`${NAME}`、`$(...)`、バッククォート、glob（`*?[]{}`）を含む場合（例: `echo x > docs/$NAME.md`、`X=.claude; echo x > $X/f`）。書き込み系コマンド（`tee`・`cp`・`touch` など）の引数に `$` やバッククォートを含む場合も拒否する（`sed -i 's/a$/b/'` のような正規表現も拒否される。過剰な拒否である）。`echo $X` のような読み取りは許可
- 64 KB（UTF-8 バイト数）を超えるコマンドは "too large to inspect" として拒否する。正規表現を使わず語の線形走査で判定するため、上限内の入力は処理時間が抑えられる
- `git push` の refspec 削除（`:branch`、`+:branch`）と `--prune` を拒否
- `python -c` で、次の危険語（部分一致の正規表現。`rmtree`、`remove`、`unlink`、`rmdir`、`removedirs`、`system`、`subprocess`、`popen`、`shutil`、`spawn`、`exec`、`eval`、`__import__`、`write`、`truncate`、`ctypes`、`socket`、`replace`、`rename`、`chmod`、`chown`、`symlink`、`copy`、`move`、`dump`（単語境界。`dumps` は除く）、`link`、`fileio`、`touch`、`mkdir`、`makedirs`、`mkfifo`、`mknod`、`utime`、`getattr`、`globals`、`locals`、`vars`、`builtins`、`__dict__`）を含むものは拒否する。
  - 部分一致のため、`str.replace`、`copy.deepcopy`、`vars()`、`print('touchscreen')` も拒否される。これは既知の過剰拒否であり、今回は ALLOW 範囲を拡張しない（Owner 判断、#629）。
  - `open` を使う場合の例外: builtin `open(path)` と `open(path, 'r'|'rb'|'br'|'rt'|'tr')` だけが AST で読み取り専用と判定でき、かつ他の呼び出しが `print(...)` と `read`・`readline`・`readlines`・`close`・`load`・`loads`・`dumps` のメソッド呼び出しだけの場合に限り許可する。これ以外の呼び出しを含むもの（例: `open('a'); os.fork()`）、属性への代入・削除（例: `json.load = …`）、`print` の再定義、書き込みモード、非リテラルモード、キーワード引数、別名・シャドーイング、不正な構文は拒否する（main の挙動を維持）（#626 / #629）
- python の引数が静的に確定しないコマンド: コマンド列に python の語が含まれ、その区間に未展開の glob・`$`・バッククォートなどが含まれる場合、python へ渡る引数を確定できないため拒否する（X1）。既知の過剰拒否（Owner 受容済み、#632 レビュー MEDIUM M2）: `rg` のようなプログラム実行経路（`--pre`）を持つツールは「プログラムを実行しないコマンド」の一覧に入れられないため、`rg python3 *.py` のように python の語がデータ引数でも、展開を含む区間は拒否される。展開を含まない形（`rg python3 README`）と、実行経路を持たないツール（`grep python3 *.py`）は許可される
- PowerShell の `Remove-Item`/`ri`/`del`/`erase` で `-Recurse`/`-Force` の略記（`-r`、`-fo` など）を含むもの（単一ファイルの場合は rm と同じ規則）
- `unlink`、`shred`、`rmdir`、`rd`、`dd`、`mkfs`、`truncate`、`cmd`
- コマンド置換 `$(...)` とバッククォートの中身も同じ規則で検査し、一つでも拒否なら全体を拒否。コマンド位置に置換がある場合も拒否
- 空の引用符の組（`r""m`）は除去してから判定する
- 解析できない入力（不正な JSON、`tool_input` や `command` の欠落、空文字列、未閉じの引用符、`$'...'`、NUL や CR などの制御文字、UTF-8 として復号できない標準入力など）はすべて拒否

## 書き込み（Round A の方針）

通常の開発に必要な書き込みは、すべてのパスがリポジトリ規則（`tar_path_ok`）を満たす場合に許可する。規則: リポジトリ内、リテラル（glob・変数・置換なし）、`..` なし、`.claude/` と `.git/` ではない、リポジトリの外へ出るシンボリックリンクを通らない。

- 許可: `cp`、`mv`、`mkdir`、`touch`、`tee`（`SAFE_WRITE_OPTS` に列挙されたオプションのみ、オペランドは上の規則を満たすこと）、`sort -o`/`--output`、`tar -c`（出力先が上の規則を満たすこと）、リダイレクト `>`・`>>`・`&>`・`>|` の先が上の規則を満たすこと。`2>&1`・`>&2`・`>&-` と `/dev/null` は許可
- 拒否（方針として維持）: `ln`、`rsync`、`scp`（別の場所へのリンクや複写は argv から検査できない）、`sort -T`/`--temporary-directory`、`ssh -E`/`-S`、`ssh -o ControlPath`、`find -fprint`/`-fls`、`git diff --output`・`format-patch -o`・`archive -o`、`git bundle`、`git config` の書き込み、`gzip`、`tar` の読み取り・展開
- 拒否: 規則を満たさない `cp`/`mv` 等（`.claude/`、`.git/`、`../`、絶対パス、glob、未展開の変数）、リダイレクト先が規則を満たさない場合、1 コマンドあたり 64 を超えるリダイレクト（処理時間の上限）
- 残存リスク（TOCTOU）: 検査は実行前の文字列とファイルシステムの状態に対して行う。検査後、実行までの間にパスを差し替えられる（シンボリックリンク、置換）可能性は、フックのみでは防げない。これは残存リスクとして受け入れ、記録する

## fail-closed の動作

- 入力の解析失敗、内部例外は拒否（`run_hook` が捕捉）
- `run_hook` の 5 秒スレッドタイムアウトは best-effort である。Python のバイトコードを実行中の処理は途中で打ち切られて拒否されるが、C 実装の処理（正規表現など）は GIL を保持したまま走り、5 秒を過ぎても打ち切れない（検証: C 側の処理は 42 秒かかり、結果は拒否ではなく完了した判定になった）。したがって時間の上限はこのタイムアウトに頼らない。実際の上限は 64 KB のサイズ上限と線形の走査である
- フック側のタイムアウト（20 秒、`.claude/settings.json`）と起動失敗は `onFailure: "block"` により拒否（Claude Code 側の仕組み。セッション内では未確認）
- 拒否理由には種別と理由のみを表示する。コマンド文字列は出力しない（トークンらしき文字列を含んでいても漏れない）

## 残存リスク（必ず読むこと）

- 文字列ベースの PreToolUse フックは任意のコード実行の安全性を保証できない。`node -e` のような明示的に拒否する形式もある一方、許可された開発ツールや `python3 script.py` などが実行するスクリプト内部はこのフックからは検査できない。未知の外部コマンドも原則として引数がある場合は拒否する
- Make ターゲット（`make clean`）、`source file` や `. file` による通常ファイルの実行、`awk` の `system()`、`.git/config` に定義された alias（`-c` 以外）は検査しない
- `.claude/` への書き込み拒否は完全ではない。既知の書き込みコマンドでは書き込み先の実体パスや経由するシンボリックリンクを検査するが、検査後の置換（TOCTOU）、ジャンクション等のプラットフォーム固有の経路、許可されたツール内部で行われる間接的な書き込みまでは保証できない。未展開の変数等を拒否しても、網羅的な保護にはならない
- 同じく、`python`・`perl`・`node` などで組み立てたパスへの書き込みは、引数に `.claude` が現れない限り検出できない
- `python -c` の検査は危険語の一致と `open()` に限定した AST 判定の組合せであり、Python コード全体の AST allowlist ではない。次の回避経路が残る。#630 で継続し、Owner が暫定受容している（#629 のスコープ外）。
  - 従来から存在する（`main` でも PR HEAD でも ALLOW）: 反射による書き込み。例: `python3 -c "import operator, io; operator.attrgetter('op'+'en')(io)('a.txt','w')"`。`open()` を含まない形も同様で、例: `os.fork()`・`zipfile.ZipFile(p,'w')`・`sqlite3.connect(...)` のように危険語リストにない API は ALLOW のまま。
  - #629 で新たに増えた経路はない。`open()` を含む snippet は、`open()` 以外に呼び出しがあれば拒否する（`main` と同じ）。`open()` を読み取り専用で使う許可は、付随呼び出しが `print` と読み取り系メソッドだけの形に限られる。属性への代入・削除は拒否する。
  - 「書き込みモードはすべて拒否できる」とは主張しない。書き込みモードの直接指定、`io.open`・`FileIO` などの明示的な形は拒否するが、反射・間接呼び出し・危険語リスト外の API は検出できない。
  - 未列挙のファイル操作・間接呼び出しには残余リスクがある。OS レベルの保護（読み取り専用マウントなど）を併用すること。
- `.claude/worktree-owners.json` は暗号や署名で守られていない。書き込みの拒否規則だけで守られており、この規則を回避する書き方があれば所有者の記録は改ざんされうる
- `.claude/` 配下は同じリポジトリ内のエージェントが編集できる。フック定義そのものを変更されると保護は外れる。そのため、ユーザー側の設定（`~/.claude/settings.json`）で同じフックを有効化するのは、リポジトリ所有者が行うこと
- 追加の対策として、OS レベルの保護を推奨する: 読み取り専用マウント、別の OS アカウントでの実行、コンテナ内での実行
- Windows では shell 形式のフックに Git Bash が必要。見つからない場合は `onFailure` により拒否される
- セッション内で実際にブロックされることは未確認。これは所有者が確認すること（検証では破壊的コマンドを実行しない）
- TOCTOU: 上記「書き込み」の節のとおり。完全な防止は保証しない
- 未解決のミューテーション: `tests/host/test_claude_destructive_guard.py` の `KNOWN_UNRESOLVED_MUTANTS`（21 件）。各項目は到達不能・同値と判断したもの、または既存の fail-closed 規則により判定が変わらないものであり、同値だとは証明していない。検出されない変異の一覧と理由は同ファイルに記録する。`MutationRegisterTests` が件数を固定している
- ビルド・テストツール（所有者の判断, Round A 選択肢 4）: `make`、`npm`、`pytest`、`dotnet`、`node`、`cargo`、`rustc`、`gcc`、`clang`、`cmake`、`ninja`、`python` は信頼済みリポジトリでの利用に限り許可する。引数はリポジトリ内に限る（絶対パス、ドライブ相対、`~`、`..`、保護された名前、未展開の変数は拒否）。ツールごとに、コードを実行するオプション（`node -e`、`cmake -E`、`pytest -p`、`npm -g` など）と、リポジトリ外へ書く・公開するサブコマンド（`npm exec`、`npm publish`、`cargo install` など）を拒否する
- ビルド・テストツールの限界: これらは Makefile のレシピ、npm スクリプト、テストコード、ビルドスクリプトなど、プロジェクトのコードを実行する。フックはその中身を検査できず、ツールが内部で起こす操作には適用されない。OS レベルの隔離は後続の課題とする
- 名前にパスを含むプログラム（`./x`、`C:\...\x.exe`）は、引数の有無にかかわらず検査できないため拒否する
- git の長いオプションは、一意な接頭辞（`--forc`、`--del`）も同じ扱いで拒否する。`git -c` は色や出力形式のキーに限る。`git worktree remove` は拒否する
