# Issue #635: API入口の直接観測（合成対照の計測器）

**状態: DEV-only計測器 / 合成媒体のみ（商用はopt-inローカルのみ） / 観測専用ゲスト非変更。商用互換・BIOS ABI・`release_eligible=false` の主張なし。**

## 目的

停止時Xだけから最初の未実装APIを断定しないために、IPL payload（RAM `$3000-$DFFF`、PUBLIC ロード範囲）からBIOS領域（`$E000-$FFFF`）への最初の制御移行を直接記録する。

- 呼出し元PC、入口PC、ジャンプテーブル位置、SP差分によるJSR/JMP区分、入口直前A/X/Y/P/S・MPR・SP、戻り先推定（`caller+2`一致でJSRを裏付け）、IDR/IRR
- 商用ゲストのバイトをAPICALLに出力しない。APICALLはアドレス・レジスタ・差分・戻り真偽値（`stack_check`/`return_matches_caller`）のみ。戻り先の数値・スタックバイトはC++内部で判定する（MPR1=`$F8`前提。想定外はUNKNOWN）
- RUN/HITのRAM dump（`buffer_hex`等）：合成modeのみ生成（ローダー判定用）。商用modeではC++が生成しない（fail-closed境界）。商用出力にはRAM dumpが一切現れない

## 合成対照（`make api-entry-integration`）

6ケース cold+warm： `jsr_slot02`、`jsr_slot03`、`jmp_slot02`、`direct_stub`（テーブルを経ずX=`$02`でスタブへ）、`nocall`、`spin`（予算負例）。
さらに PUBLIC ローダーで高領域の呼出し元 5 ケース（`jsr_slot02_at_8000`、`jmp_slot02_at_9000`、`direct_stub_at_c000`、`nocall_at_8000`、`spin_at_9000`、cold+warm）で、監視範囲 `$3000-$DFFF` が `$8000/$9000/$C000` からの JSR/JMP/直行を検出し、`nocall`/`spin` を誤分類しないことを確認する。

- `jsr_slot02`: table `$E006`、slot2、delta2=JSR、戻り=`caller+2`、entry regs preset、halt X=2
- `jsr_slot03`: 同様にslot3
- `jmp_slot02`: table `$E006`、slot2、delta0=JMP
- `direct_stub`: tableでない（`$E24F`直達）、X=2でもDIRECTとして識別（停止時Xのみの判定との差を実証）
- `nocall`: APICALL 0件、`cd_halt_ok` halt
- `spin`: APICALL 0件、`budget`（haltでない。harnessは予算をPASSしない）

これらで「X=`2`ならslot2」と読むだけの分類と区別できることを証明する。合成で証明されない場合、商用のslot判定を確定扱いしない。

## 観測専用性

- ゲストRAM書込・レジスタ書込・割り込み投入・MMIO読取なし。状態はpinnedコアのstate pointer読取のみ（`memory->Read(first_pc)` は既存probeと同一のROM `SEI`先頭確認。副作用なし）。スタック2バイトは `GetWorkingRAM()` の読取（書込なし、MPR1前提）
- 入口観測はwatch比較のみで実行順序を変更しない。観測あり/なしの自動対照は本ハーネスには含まれない。二観測一致（本debugger vs Frame Tracer normal-frame）は手動で外部比較する（自動証明ではない）
- APICALLは上限8件（cycle毎）。超過時は `apicall_truncated=true` をRUNに記録する。先頭8件を超える呼出しは未観測として扱う

## 商用再測定（opt-in、手動・ローカル限定）

入力方式（Owner方針）: 商用媒体はリポジトリ直下の `./rom/` のみ（ローカル限定、gitignore 済み）。商用計測は明示的な `--commercial` を指定した場合のみ実行する。CUE 絶対パスの毎回指定は不要。`./rom/` 内の `*.cue` のみを検出対象とし（純正 BIOS・`*.pce` 等は媒体として扱わない）、1 件なら自動選択、複数件なら `--commercial-index` による利用者選択を要求し先頭を自動選択しない、0 件なら NOT_RUN とする。ディレクトリ外へのシンボリックリンク、パストラバーサル（`..` 等）、絶対参照、存在しない BIN、ディレクトリ参照を含む CUE は fail-closed で BLOCKED とする。媒体は読み取り専用に扱い、`./rom/` のファイルを変更・削除・移動しない。通常の `make check` / `make test` / CI は商用媒体なしで成立し、合成テストが維持される。

`geargrafx_api_entry.py --commercial --commercial-rom <DEV/PUBLIC loader> --commercial-output /tmp/opencode/issue635-private/<file>.json`（直接子のみ。専用親0700を事前に作成。上書き不可。`--commercial-labels` 省略時は loader の `.lbl` から自動生成し、明示時はその値を使用。複数 CUE 時は `--commercial-index <N>`）

または、ローダーを手動で用意せず `--commercial-auto-loader` で自動構築する（推奨）。選択した CUE からデータトラック INDEX 01 の絶対 LBA を **stat-only** で導出し（`commercial_media.data_track_index01_lba`: CUE テキストのみ読み、BIN は lstat のサイズのみ使用し内容を読まない。MODE1/2352 トラックが正確に 1 件でない場合は BLOCKED。導出値はログ・証拠 JSON に出さない）、`build_ipl_loader_bios` で PUBLIC（既定）または DEV ローダーを `build/`（gitignore）に構築してから probe する。`--commercial-auto-loader` と `--commercial-rom` は相互排他である:

```sh
make dev-rom
python3 tests/integration/geargrafx_api_entry.py --commercial --commercial-auto-loader --commercial-loader-format public --commercial-output /tmp/opencode/issue635-private/commercial-public.json
python3 tests/integration/geargrafx_api_entry.py --commercial --commercial-auto-loader --commercial-loader-format dev --commercial-output /tmp/opencode/issue635-private/commercial-dev.json
```

合成 SEL（265）で構築したローダーを商用 CUE に使うと、情報セクタの位置が合わず IPL 検証で止まる（`PRE_API_BLOCKER_OBSERVED`）。これはローダーの SEL 不一致であり、商用 IPL 形式の証拠ではない。自動構築はこの不一致を解消する。

PUBLIC ローダーの拡張（`IPL_PUBLIC` のみ。DEV ローダーは従来どおり）:
- バイト 16..31: DEV はゼロ必須、PUBLIC は無視（公開仕様で未確定の領域を DEV 固有のゼロ要求で拒否しない。14..15 の扱いと同一の理由）
- IPLBLN: DEV 1..10、PUBLIC 1..31
- ロード先終端: DEV `$8000`、PUBLIC `$E000`。コピー/読戻しは MPR2..6 の領域（`$40/$60/$80/$A0/$C0`）まで対応
- MPR バイト: DEV は `$80..$87` 生値、PUBLIC は 0..7 インデックスを `$80+index` へ写像（#691 の黒箱観測）

判定は複数呼出し列にも対応する: cold/warm で同じ slot+method 列、すべての JSR が戻り一致で裏付けられ、最後の呼出しでスタブ halt する場合に `API_ENTRY_CONFIRMED` とする。JMP は最終呼出しのみ許可する（JMP は戻らない）。

worktree から実行する場合、`./rom/`（gitignore、worktree 間共有なし）が本 worktree に無いため、main worktree の `./rom/` を read-only フォールバックとして参照する（`git rev-parse --git-common-dir` で解決。primary と同一の検証を行い、書込みはしない）。どちらも無い場合は NOT_RUN であり、「存在するから走る」自動実行はしない。

残存 TOCTOU（検証→probe 間の差替）: probe はパスで再オープンする path-based API のため、Python 検証と probe の使用は原子にならない。緩和策として probe 前後で CUE/BIN の lstat（dev/ino/size/mtime/ctime）を照合し、変化があれば FAIL（結果は信頼しない）。ただし probe 前の狭い check-then-open 窓自体は Python 単独では消去できず、ローカルで `./rom/` への書込権を持つ並行プロセスが前提の残存リスクとして残る（Owner 受領済みの既知制限）。`--rom-dir` はテスト用フックであり、`OSS_PCE_ALLOW_ROM_DIR=1` なしでは fail-closed で拒否する。本番運用は `./rom/` の自動解決のみを用いる。

書込みは親fd固定（`O_DIRECTORY|O_NOFOLLOW` + fstat + `dir_fd` で `O_EXCL|0600` 作成）。親置換後もfd固定先へ書く（symlinkターゲットへ逃げない）。部分書込失敗時は部分ファイルを残しFAIL（削除しない。未完成として扱う）。親dir自体の移動（rename）に対する完全防御はしていない（残存リスクとしてはfd固定のinodeへ書く）

判定は次を区別する：`API_ENTRY_CONFIRMED`（入口観測+停止確認）、`PRE_API_BLOCKER_OBSERVED`（入口0件+IPL拒否halt）、`UNKNOWN`（入口なし理由不明）、`INCOMPLETE`（切断）、`BUDGET`（予算）、`NOT_RUN`（入力なし）、`BLOCKED`（入力/器の安全条件不成立）、`FAIL`（器異常）。「正常終了」と「最初のAPI特定」を混同しない。商用証拠 JSON は構造化要約のみ（verdict/apicalls/runs）であり、CUE/BIN の内容・名称・絶対パス・ハッシュを含まない。

## 既知の制限

- JSR/JMP区分はSP差分と戻り先一致による分類であり、オペコード復元ではない。JSRの戻り先一致はMPR1=`$F8`前提。非JSRのスタック2バイトは任意内容のためharnessは比較しない
- IRQ区別は入口PC（vector `$FFFx`かtableか）とSP差分による分類。IDR/IRRは記録するが、割り込み注入実験（統合）は未実施（＝NOT_RUN。安全条件未構成のため断定しない）
- ジャンプテーブル範囲は `$E000-$E0F2`（81*3）。境界外のBIOS到達はtableでない
- B11表示・B12タイトルは本計測器の対象外
