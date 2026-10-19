# CD_SEEK 相当のデバイス層観測 (#215) — 実行記録 (BLOCKED)

状態: **BLOCKED（実コア実行なし）**。自作 CD に対する Geargrafx 固定版 (`b49ae82a012eade566d72e9d61c9d95a5caa4da3`) の
位置変更・状態遷移は、この実行環境では観測していない。以下は固定版ソースの読解 (SOURCE_READ) と、実行できない理由、
再開条件の記録であり、PASS / VERIFIED とは扱わない。

親: [#13](https://github.com/mao2009/oss-pce-cd-bios/issues/13) CD_SEEK (slot `$04`, 候補 `$E00C`)。関連: #5 (READ(6) 診断), #15 (CD-DA 観測), #6/#138。

## 境界（混同しないこと）

| 項目 | 本書での扱い |
| --- | --- |
| 純正 System Card の CD_SEEK (`$E00C`) の引数・返値・完了条件 | UNKNOWN。`spec/api_contracts.json` の候補のみ。純正 BIOS のバイトは実行していない |
| Geargrafx のデバイス動作（SCSI 層の位置変更・待ち） | SOURCE_READ のみ。実コア実行は未実施 (NOT_RUN) |
| 実機・第 2 エミュレータ | NOT_RUN |

純正 CD_SEEK の呼出しは行っていない。ここで「位置変更」と呼ぶのは Geargrafx の SCSI/CD-DA 層の内部状態であり、
System Card API の意味とは別物である。

## 実行不能の原因（BLOCKED）

1. **WSL 経由の実行が拒否された。** この worktree 専用エージェントから `wsl.exe` を呼ぶコマンドは実行ガードで拒否された
   （worktree 外の git 操作の可能性を理由とする）。固定版コアと cc65 のセットアップは WSL 側 (ユーザーのホーム配下) の手順
   (`tools/setup.py geargrafx`, `tools/setup.py cc65`) が前提であり、このエージェントから起動できなかった。
2. **Windows ホストに ROM ビルド用ツールがない。** `make` / `ca65` / `ld65` が PATH に無く、`tools/setup.py` は `fcntl` を
   使うため Windows では動かない（既存メモリの記録と一致）。自作 ROM (`make dev-cdda`) をビルドできない。
3. **固定版コアのバイナリが無い。** `<repo>/rom/Geargrafx/Geargrafx.exe` は存在するが、版と SHA が固定版と照合できず、
   リリース版の GUI バイナリであるため、証拠としては使わない（`tools/dependencies.json` の固定版ではない）。

探索したパス（名称のみ）: worktree 直下（`Makefile`, `tools/`, `tests/`, `docs/` を確認）、リポジトリ外の既定 tools ディレクトリ
（存在せず）、`<repo>/rom/`（`Geargrafx/`, 市販ゲームのフォルダ, `syscard3.pce` の存在のみ確認。中身は読んでいない）、PATH 上の
`make` / `ca65` / `ld65`（無し）、`python`（3.14 あり、`gcc` は WinLibs の mingw のみ）。

## 再試行記録 (2026-10-19)

この節は時点 `50d3e4c` の再試行記録（結果は NOT_RUN）である。下記「実測記録」の提示結果（対象 `655244f`）とは、時点と対象が異なる。

- 対象: 前回の再試行時点の PR #220 ヘッドは `50d3e4c`（履歴として残す。以後の文書コミットで PR ヘッドは進むため、
  現在の対象 SHA は下記の手順で実行開始時に取得して固定する）。`feat/215-cdseek-device-observation` は別 worktree（locked）で
  チェックアウト中のため、本 worktree は detached HEAD で同一コミットを使用し、`HEAD:feat/215-cdseek-device-observation` へ
  fast-forward で push した（PR #220 の Draft は維持）。
- WSL: `wsl.exe` の呼出しは再度、実行ガードで拒否された（「worktree 隔離エージェントの plain command で wsl を実行するため、
  git 操作の可否を示せない」）。ガードは迂回していない。WSL のディストリ一覧・固定版の有無は未確認（NOT_RUN）。
- Windows 側: `make` / `ca65` / `ld65` は PATH に無し（従来どおり）。`<repo>/rom/Geargrafx/Geargrafx.exe` は
  5,742,080 バイト、SHA-256 `742B2D8D21060368134324E1CE6C32A9751E7A9776549ED78448ECA93E6E8563`、更新日時 2026-10-16。
  固定版 (`b49ae82`) のビルド証跡がなく、`tools/dependencies.json` の SHA とも照合できないため、**証拠として使わない**。
- 既存 CD-DA 統合テスト (`make dev-cdda-integration`): 固定版コアで実行できないため **NOT_RUN**（0 件 PASS / 0 件 FAIL）。
- 正例・負例・timeout・IRQ の実測: **なし**（すべて NOT_RUN / NOT_TESTED のまま）。

### ユーザーが実行する手順（`!` 経由、エージェントは実行しない）

WSL のホーム配下に clean clone を作り、固定版を取得して既存 CD-DA 診断を実行する。対象は PR #220 の HEAD。
文書内に SHA を直書きしない（文書更新のたびに古くなるため）。代わりに、実行開始時に `git ls-remote` で
`feat/215-cdseek-device-observation` の先頭 SHA を取得し、`TARGET_SHA` として固定する（PR の `headRefOid` と同じ値）。
実行中に PR ヘッドが進んでも、証拠として使うのは開始時に固定した `TARGET_SHA` のみとする。

共通の方針:

- `GIT_DIR` / `GIT_WORK_TREE` は**設定しないこと**（既存の作業ツリーを参照すると検証にならない）。手順は、これらが設定されていれば停止する。
- 作業ディレクトリは `mktemp -d` で毎回新規作成する。既存のディレクトリ・ファイル（ほかの作業ツリー、ツールキャッシュ、ログ）は削除・上書きしない。
- ログは `$HOME/cdseek215-logs/` に `mktemp` で一意なファイル名として保存する（既存ログは上書きしない）。リポジトリ外。
- `TOOLS_DIR` はリポジトリ外の `$HOME` 配下に固定する（既定の `/tmp` は使わない）。
- SHA の取得手段: `git ls-remote`（`git` があれば WSL でも Git Bash でも可）を正とする。WSL に `gh` がある場合のみ、
  `gh pr view 220 --json headRefOid --jq .headRefOid` と一致することを追加で確認する。`gh` が無い環境では ls-remote の結果のみを使う。

**手順 A（推奨）: WSL のターミナルで直接貼り付けて実行する。** `set -euo pipefail` により、途中で失敗するとサブシェルごと終了し、
終了コードが非 0 になる。成功時のみ最後に `EXIT_OK` を出す。

```bash
(
set -euo pipefail
if [ -n "${GIT_DIR:-}" ] || [ -n "${GIT_WORK_TREE:-}" ]; then
  echo "GIT_DIR または GIT_WORK_TREE が設定されています。これらを unset した新しいシェルで実行してください" >&2
  exit 1
fi
REPO_URL="https://github.com/mao2009/oss-pce-cd-bios.git"
PR_BRANCH="feat/215-cdseek-device-observation"
TARGET_SHA="$(git ls-remote "$REPO_URL" "refs/heads/$PR_BRANCH" | cut -f1)"
[[ "$TARGET_SHA" =~ ^[0-9a-f]{40}$ ]] || { echo "SHA の取得に失敗しました" >&2; exit 1; }
if command -v gh >/dev/null 2>&1; then
  test "$(gh pr view 220 --repo mao2009/oss-pce-cd-bios --json headRefOid --jq .headRefOid)" = "$TARGET_SHA"
fi
W="$(mktemp -d "$HOME/cdseek215-work.XXXXXX")"
LOG_DIR="$HOME/cdseek215-logs"
mkdir -p "$LOG_DIR"
LOG="$(mktemp "$LOG_DIR/cdda-${TARGET_SHA:0:12}.XXXXXX.log")"
echo "TARGET_SHA=$TARGET_SHA" | tee -a "$LOG"
git clone "$REPO_URL" "$W/repo"
cd "$W/repo"
git checkout --detach "$TARGET_SHA"
test "$(git rev-parse HEAD)" = "$TARGET_SHA"
export TOOLS_DIR="$HOME/oss-pce-cd-bios-tools"
python3 tools/setup.py cc65
python3 tools/setup.py geargrafx
python3 tools/setup.py geargrafx --debugger
test "$(cat "$TOOLS_DIR/geargrafx-b49ae82a012eade566d72e9d61c9d95a5caa4da3-debugger/.verified-archive-sha256")" = "a80a3eae26a8efcd994350e84bbe73c975e406ced41f8f2404c88b00d4217f68"
test "$(cat "$TOOLS_DIR/cc65-71746c829e77f74c2b601a7d16821170c2610df3/.verified-archive-sha256")" = "2172a1fc74bf6f3026e0f9427d4159508cd801abcb35ce591394bb911adaabe4"
make dev-cdda-integration MODE=debug 2>&1 | tee -a "$LOG"
echo "EXIT_OK TARGET_SHA=$TARGET_SHA WORK=$W LOG=$LOG" | tee -a "$LOG"
)
```

**手順 B: Windows の Git Bash から実行する場合。** Git Bash のパス変換を止めるため `MSYS_NO_PATHCONV=1` を付け、
手順 A の本体（外側の `(` と `)` を除く）を heredoc で `bash -l -s` の標準入力として WSL に渡す。`-l` でログイン シェルの PATH を読ませる。
終了コードは `wsl exit=` の行で確認する（非 0 なら BLOCKED）。この手順は本文書の作成時点では実行していない（未検証）。

```bash
MSYS_NO_PATHCONV=1 wsl.exe -- bash -l -s <<'CDSEEK_EOS'
set -euo pipefail
if [ -n "${GIT_DIR:-}" ] || [ -n "${GIT_WORK_TREE:-}" ]; then
  echo "GIT_DIR または GIT_WORK_TREE が設定されています。これらを unset した新しいシェルで実行してください" >&2
  exit 1
fi
REPO_URL="https://github.com/mao2009/oss-pce-cd-bios.git"
PR_BRANCH="feat/215-cdseek-device-observation"
TARGET_SHA="$(git ls-remote "$REPO_URL" "refs/heads/$PR_BRANCH" | cut -f1)"
[[ "$TARGET_SHA" =~ ^[0-9a-f]{40}$ ]] || { echo "SHA の取得に失敗しました" >&2; exit 1; }
if command -v gh >/dev/null 2>&1; then
  test "$(gh pr view 220 --repo mao2009/oss-pce-cd-bios --json headRefOid --jq .headRefOid)" = "$TARGET_SHA"
fi
W="$(mktemp -d "$HOME/cdseek215-work.XXXXXX")"
LOG_DIR="$HOME/cdseek215-logs"
mkdir -p "$LOG_DIR"
LOG="$(mktemp "$LOG_DIR/cdda-${TARGET_SHA:0:12}.XXXXXX.log")"
echo "TARGET_SHA=$TARGET_SHA" | tee -a "$LOG"
git clone "$REPO_URL" "$W/repo"
cd "$W/repo"
git checkout --detach "$TARGET_SHA"
test "$(git rev-parse HEAD)" = "$TARGET_SHA"
export TOOLS_DIR="$HOME/oss-pce-cd-bios-tools"
python3 tools/setup.py cc65
python3 tools/setup.py geargrafx
python3 tools/setup.py geargrafx --debugger
test "$(cat "$TOOLS_DIR/geargrafx-b49ae82a012eade566d72e9d61c9d95a5caa4da3-debugger/.verified-archive-sha256")" = "a80a3eae26a8efcd994350e84bbe73c975e406ced41f8f2404c88b00d4217f68"
test "$(cat "$TOOLS_DIR/cc65-71746c829e77f74c2b601a7d16821170c2610df3/.verified-archive-sha256")" = "2172a1fc74bf6f3026e0f9427d4159508cd801abcb35ce591394bb911adaabe4"
make dev-cdda-integration MODE=debug 2>&1 | tee -a "$LOG"
echo "EXIT_OK TARGET_SHA=$TARGET_SHA WORK=$W LOG=$LOG" | tee -a "$LOG"
CDSEEK_EOS
echo "wsl exit=$?"
```

前提: WSL に `git` `python3` と、`tools/setup.py` が検査する `curl` `make` `gcc` `g++` `ar` があること（検査されるのはこの 5 つ。`git` `python3` は別途確認する）。不足時は `tools/setup.py` が不足ツール名を表示して止まるので、その表示を記録する。
`setup.py` は取得アーカイブの SHA-256 を検証し、展開後のソースは `.source-integrity.json` と照合する。失敗時はそのまま BLOCKED として記録し、再試行前に原因を記録する。

ログ全文はコミットしない（純正 BIOS・市販 ROM 由来のバイトを含めない）。ログは `$HOME/cdseek215-logs/cdda-<SHA先頭12桁>.*.log`（リポジトリ外）に残り、作業ディレクトリは `$HOME/cdseek215-work.*`（リポジトリ外・未削除）に残る。不要になった作業ディレクトリは利用者が確認のうえ手動で削除する。

#### 結果記録欄（実行後にユーザーが記入する。エージェントは記入しない）

判定規則: 手順の終了コードが 0 かつログに `PASS real Geargrafx CD-DA observation` がある場合のみ、下表の各ケースを `cdda-evidence.json` の `tests.<キー>.status` どおりに記録する（ログには個別 status は出ない）。
それ以外は全体を FAIL/BLOCKED とし、個別ケースを PASS にしない。evidence の最上位 `"status": "PASS"` はチェック通過後に書かれるため独立した根拠ではない。

| 区分 | 既存テストのキー（`tests/integration/geargrafx_cdda.py`） | 期待 status | 実測 status（記入） | 補足（記入） |
| --- | --- | --- | --- | --- |
| 実行対象 SHA（開始時固定） | `TARGET_SHA`（ログ 1 行目） | — | | ls-remote の取得値。`gh` で照合した場合はその結果も補足に記入 |
| 作業ディレクトリ / ログ | — | — | | `cdseek215-work.*` と `cdseek215-logs/cdda-*.log` の実パスは記入しない（ファイル名のみ） |
| 正例 | `mixed_mode_script` | PASS | | `samples_verified_against_bin`、`cdda_play` |
| 負例 1 | `tampered_pcm_byte_in_lba20` | PASS_EXPECTED_FAILURE | | ログの rejected_because |
| 負例 2 | `channels_swapped_lba21_sample30` | PASS_EXPECTED_FAILURE | | 同上 |
| 負例 3（データトラック上の `$D8`） | `play_on_data_track` | PASS_EXPECTED_FAILURE | | `cdda_state_after_d8`、`nonzero_samples` |
| 負例 4（`$D8` byte1=0 で pause） | `d8_byte1_zero_seeks_paused` | PASS_EXPECTED_FAILURE | | `cdda_state_after_d8` |
| timeout | 固定 instruction budget（`STEP_BUDGET`）、ホスト側 `subprocess` 900 秒 | 超過時は FAIL。PASS にしない | | `TimeoutExpired` の有無（ログを検索して記録） |
| IRQ | 既存診断は想定外の割り込みを検出して報告するのみ（完了通知は IRQ 経由で待たない） | NOT_TESTED | NOT_TESTED（変更しないこと） | 想定外割り込みの有無をログで確認して補足に記録 |
| 無媒体・媒体外 LBA の明示ケース | 現行テストに無い | NOT_IMPLEMENTED | NOT_IMPLEMENTED | 追加は未着手 |

完了後に次も記録する（`cdda-evidence.json` は `<作業ディレクトリ>/repo/build/debug/cdda-probe/` 配下。`<作業ディレクトリ>` は EXIT_OK 行の `WORK=` に出力される）:
`git rev-parse HEAD`（`TARGET_SHA` と一致すること）、`$TOOLS_DIR/geargrafx-b49ae82…-debugger/.verified-archive-sha256`（`a80a3eae…`）、
`$TOOLS_DIR/cc65-71746c8…/.verified-archive-sha256`（`2172a1fc…`）、evidence の `rom_sha256`・`geargrafx_revision`・`disc.bin_sha256`・`disc.cue_sha256`、ログの終了コード。
実行後に PR ヘッドが変わっていても、証拠の対象は開始時の `TARGET_SHA` である。変化したことは補足に別途記録する（判定には使わない）。
固定版の `git rev-parse` は使わない（取得物は git リポジトリではない）。

## 実測記録（ユーザーが提示した結果、2026-10-19）

以下は、オーナーが手順 A（`### ユーザーが実行する手順`）を WSL で実行して提示した結果である。**エージェントが自ら実行して得た結果ではない。** 再実行はしていない。ログ全文・ROM・BIOS のバイトはこの文書に含めない。

### 実行環境（提示された値）
- 対象 SHA: 提示されたログの `TARGET_SHA` 行と `EXIT_OK` 行に `655244fddd826b28c407f44dc9d7b725d3038abd` が記録されている。
  これはオーナーが提示したログの値であり、エージェントが再実行して得た値ではない。基準 HEAD `655244f`（PR #220）と一致する。
  `655244f` から本記録時点の PR ヘッドまでの差分は文書のみ（`git diff 655244f..<ヘッド>` は `docs/cdseek-device-observation.md` のみ）であり、検証対象のコードは同一。
- Geargrafx revision: `b49ae82a012eade566d72e9d61c9d95a5caa4da3`（固定版、デバッガ版）
- Geargrafx アーカイブ SHA-256: `a80a3eae26a8efcd994350e84bbe73c975e406ced41f8f2404c88b00d4217f68`
- cc65 アーカイブ SHA-256: `2172a1fc74bf6f3026e0f9427d4159508cd801abcb35ce591394bb911adaabe4`（手順 A の照合対象）

### 合成 CD と診断 ROM（自作・非公開バイトなし）
- 診断 ROM SHA-256: `1f96fc3f173e3ee52f1955f1741c3f85f70b53987e0e1c75d023f2e1403e6206`
- 合成 CD BIN SHA-256: `52809b19c577a9ace46a1306f8dde7730a1b92bb0f98661b34c8feeb3c5da25f`
- 合成 CD CUE SHA-256: `ee9a8c89e4f46eb89d9a3e8cd5d7234797adc845b0a24c884dadb55673655d1d`

### 既存 CD-DA 診断の結果（提示された値）
| 区分 | キー | 提示された status | 扱い |
| --- | --- | --- | --- |
| 全体 | — | PASS | 手順の終了コード 0 と `PASS real Geargrafx CD-DA observation` の提示に基づく |
| 正例 | `mixed_mode_script` | PASS | 音声照合 29685 サンプル一致 |
| 負例 1 | `tampered_pcm_byte_in_lba20` | PASS_EXPECTED_FAILURE | 期待どおり拒否 |
| 負例 2 | `channels_swapped_lba21_sample30` | PASS_EXPECTED_FAILURE | 期待どおり拒否 |
| 負例 3 | `play_on_data_track` | PASS_EXPECTED_FAILURE | 期待どおり拒否 |
| 負例 4 | `d8_byte1_zero_seeks_paused` | PASS_EXPECTED_FAILURE | 期待どおり拒否 |
| timeout | 固定 instruction budget、ホスト側 900 秒 | 提示なし（未確認） | 超過の有無は未報告。FAIL 扱いの判定には未使用 |
| IRQ | — | NOT_TESTED | 変更なし |
| 無媒体・媒体外 LBA | — | NOT_IMPLEMENTED | 未実装 |

負例 1〜4 の `PASS_EXPECTED_FAILURE` は、**検証器（`tools/cdda_verify.py`）が期待どおり不合格を検出したこと**を意味する。
SCSI コマンド（`$D8` など）が拒否されたことを意味しない。負例は、判定器が誤った観測を通さないことの確認である。

### この結果で言えること／言えないこと
- **言える:** 固定版 Geargrafx（デバッガ版）上で、自作の合成 CD に対する既存 CD-DA 診断（音声再生の開始、負例の拒否、`$D8` の byte1=0 による一時停止）が、提示された記録のとおり成立した。
- **言えない:** 純正 BIOS の CD_SEEK の互換性、callee（`$E00C`）の入力・出力・失敗の意味、ABI の確定、BLOCKED_ABI の解除。これらは本観測の対象外である。
- **未検証:** timeout の超過有無、IRQ の挙動、媒体外 LBA の負例、`$D8` による有効 LBA 指定の状態遷移の追加検証。

## 追加シナリオ（#215 Phase C-2、実コア未実行 = NOT_RUN）

既存プローブ（`src/cdda_probe.s`、変更なし）を使い、既存の実行経路とは別の `tests/integration/geargrafx_cdda_scenarios.py`
（`make dev-cdda-scenarios-integration`）で次の 3 件を実行する。`$D8`（コマンド表の 1 件目）の LBA 3〜5 バイトを書き換えた
ROM の派生物をテスト用に作るだけで、新しい ROM は作らず、コミットもしない。結果は `cdda-scenarios-evidence.json` に書き、
既存の `cdda-evidence.json` には触れない。

| 区分 | evidence キー | 入力 | 期待（出典） | 判定の扱い | 実コア結果 |
| --- | --- | --- | --- | --- | --- |
| A 有効な音声 LBA | `valid_audio_start_lba40` | `$D8` play、LBA 40（音声トラック LBA 16〜79 の内側、先頭ではない） | `$D8` 直後の START_LBA = 40、CURRENT_STATE = PLAYING（mode 1 のため `StartAudio(lba, pause=false)`）。シークは LBA 40 への 1 回のみ。PCM は BIN の LBA 40 から連続。`$DA` 後 PAUSED、`$D9` 後 STOPPED。（`scsi_controller.cpp` 544-570、`AudioLBA` 856-866、`cdrom_audio_inline.h` 135-140） | 既存 `check_all` に開始 LBA を渡す | NOT_RUN |
| B 媒体範囲外 LBA | `invalid_audio_start_lba_at_medium_end` | `$D8` play、LBA 80（媒体セクタ数 80 の先頭の外） | `start_lba >= GetSectorCount()` の分岐で GOOD を返し、`StartAudio` を呼ばない（`scsi_controller.cpp` 544-552）。よって CD-DA の状態・START_LBA・位置は `$D8` 前（READ(6) 後の IDLE）から変化せず、シークも音声サンプルも出ない。媒体セクタ数は TOC の `sector_count`（`cdrom_media_inline.h` 143-152）で、CUE/BIN では最終トラックの end_lba + 1（`cdrom_cuebin_image.cpp` 1165-1184、1205-1210）。STATUS 相の GOOD は `StartStatus`（226-234）による。 | `check_invalid_audio_start`。`$D8` 直後と wait 後の両方で不変を確認 | NOT_RUN |
| C 予算超過 | `timeout_budget_exhausted` | 無変更の ROM、予算 1,000,000 命令（通常は 12,000,000） | プローブは `reason=budget` を報告し、判定器は `(budget)` で拒否しなければならない。予算超過を PASS にしない。ソースの要求はなし（本リポジトリのプローブの上限） | `PASS_EXPECTED_FAILURE` は「判定器が予算超過を拒否した」の意味。ゲストの成功ではない | NOT_RUN |

UNKNOWN（未確定のまま残す項目。推測で埋めない）:

- `TRACE_SCSI_PROBLEM_INVALID_AUDIO_START_LBA` の発生（trace 出力）は、本プローブが trace を取らないため観測しない。
- SCSI フェーズの数値の意味は本書では確定しない。`scsi_phase` は生の値を記録する。
- 媒体外 LBA の無媒体（no-disc）の経路は本シナリオの対象外（従来どおり NOT_IMPLEMENTED）。

## 追加シナリオの実行手順（手順 C、PR ヘッド固定）

手順 A と同じく、`TARGET_SHA` を `git ls-remote` で実行開始時に固定し、`EXIT_OK` 行に出す。既存の作業ディレクトリ・ログ・
`cdda-evidence.json` は上書きしない。新しい作業ディレクトリ（`cdseek215-scn-work.*`）、新しいログ（`cdda-scenarios-*.log`）、
新しい evidence（`cdda-scenarios-evidence.json`）を使う。`GIT_DIR` / `GIT_WORK_TREE` は設定しないこと。

```bash
(
set -euo pipefail
if [ -n "${GIT_DIR:-}" ] || [ -n "${GIT_WORK_TREE:-}" ]; then
  echo "GIT_DIR または GIT_WORK_TREE が設定されています。これらを unset した新しいシェルで実行してください" >&2
  exit 1
fi
REPO_URL="https://github.com/mao2009/oss-pce-cd-bios.git"
PR_BRANCH="feat/215-cdseek-device-observation"
TARGET_SHA="$(git ls-remote "$REPO_URL" "refs/heads/$PR_BRANCH" | cut -f1)"
[[ "$TARGET_SHA" =~ ^[0-9a-f]{40}$ ]] || { echo "SHA の取得に失敗しました" >&2; exit 1; }
if command -v gh >/dev/null 2>&1; then
  test "$(gh pr view 220 --repo mao2009/oss-pce-cd-bios --json headRefOid --jq .headRefOid)" = "$TARGET_SHA"
fi
W="$(mktemp -d "$HOME/cdseek215-scn-work.XXXXXX")"
LOG_DIR="$HOME/cdseek215-logs"
mkdir -p "$LOG_DIR"
LOG="$(mktemp "$LOG_DIR/cdda-scenarios-${TARGET_SHA:0:12}.XXXXXX.log")"
echo "TARGET_SHA=$TARGET_SHA" | tee -a "$LOG"
git clone "$REPO_URL" "$W/repo"
cd "$W/repo"
git checkout --detach "$TARGET_SHA"
test "$(git rev-parse HEAD)" = "$TARGET_SHA"
export TOOLS_DIR="$HOME/oss-pce-cd-bios-tools"
python3 tools/setup.py cc65
python3 tools/setup.py geargrafx
python3 tools/setup.py geargrafx --debugger
test "$(cat "$TOOLS_DIR/geargrafx-b49ae82a012eade566d72e9d61c9d95a5caa4da3-debugger/.verified-archive-sha256")" = "a80a3eae26a8efcd994350e84bbe73c975e406ced41f8f2404c88b00d4217f68"
test "$(cat "$TOOLS_DIR/cc65-71746c829e77f74c2b601a7d16821170c2610df3/.verified-archive-sha256")" = "2172a1fc74bf6f3026e0f9427d4159508cd801abcb35ce591394bb911adaabe4"
make dev-cdda-scenarios-integration MODE=debug 2>&1 | tee -a "$LOG"
EVIDENCE="$W/repo/build/debug/cdda-probe/cdda-scenarios-evidence.json"
python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); [print("CASE", k, v["status"]) for k,v in d["tests"].items()]' "$EVIDENCE" | tee -a "$LOG"
echo "EXIT_OK TARGET_SHA=$TARGET_SHA WORK=$W LOG=$LOG EVIDENCE=$EVIDENCE" | tee -a "$LOG"
)
echo "wsl exit=$?"
```

判定規則: 次の 3 つがすべて成り立つ場合のみ、下表の各ケースを `CASE` 行の status どおりに記録する。
(1) `wsl exit=0`、(2) ログに `PASS real Geargrafx CD-DA scenarios` がある、(3) `CASE` 行に 3 件のキーがすべてある。
いずれか欠ける場合は全体を FAIL/BLOCKED とし、個別ケースを PASS にしない。`EXIT_OK` 行は `TARGET_SHA` を含むため、対象 SHA の照合に使う。

| 区分 | evidence キー | 期待 status | 実測 status（記入） | 補足（記入） |
| --- | --- | --- | --- | --- |
| 実行対象 SHA（開始時固定） | `TARGET_SHA`（ログ 1 行目） | — | | `ls-remote` の値 |
| A 有効な音声 LBA | `valid_audio_start_lba40` | PASS | | |
| B 媒体範囲外 LBA | `invalid_audio_start_lba_at_medium_end` | PASS | | `scsi_phase_after_d8` の生の値 |
| C 予算超過 | `timeout_budget_exhausted` | PASS_EXPECTED_FAILURE | | `rejected_because` |

## 固定版ソースの読解 (SOURCE_READ, `src/scsi_controller.cpp` @ `b49ae82`)

`ScsiController::ExecuteCommand` のディスパッチは次の opcode のみ（それ以外は `Debug` 出力のみで無視）:

| opcode | 名称 | デバイス層での意味 |
| --- | --- | --- |
| `$00` | TEST UNIT READY | 媒体の有無に関係なく GOOD（`cd-media-state.md` と同じ） |
| `$03` | REQUEST SENSE | 参照のみ |
| `$08` | READ(6) | 位置変更あり。`SeekTime(current, target)` と `NotifyScsiReadStart` からシーク時間を計算し、初回読み出し位相に乱数の遅延を加える |
| `$D8` | AUDIO START POSITION | `AudioLBA()` が媒体のセクタ数以上なら CD-DA 状態は変えず STATUS GOOD を返す（SCSI フェーズは STATUS に進む）。それ以外は CD-DA の開始位置を設定 |
| `$D9` | AUDIO STOP POSITION | 停止位置 |
| `$DA` | AUDIO PAUSE | 一時停止 |
| `$DD` | READ SUBCODE Q | 絶対/相対 MSF の応答 |
| `$DE` | READ TOC | モード 0/1/2。トラック種別 (`$04` データ / `$00` 音声) を返す |

**SEEK 専用コマンド (`$0B` / `$2B`) は固定版にディスパッチされていない。** つまり Geargrafx の「シーク」は独立した
SCSI コマンドではなく、READ(6) の内部計算と CD-DA の開始位置設定（`$D8`）に分かれて現れる。
`AudioSeekCompleted()` は `SCSI_PHASE_BUSY` のときに STATUS GOOD を出す、という待ちの完了経路を持つ。

本診断の「位置変更」の観測点候補は次のとおり（いずれも未実行）:

- `$D8` の `CURRENT_STATE` / `START_LBA` / `SEEK_CYCLES`（`cdda-core-observation.md` と同じ観測点を流用可能）
- READ(6) のシーク・遅延サイクル。通常は `SeekTime` 由来だが、LaserActive が有効なときは `NotifyScsiReadStart` の値で上書きされ、初回のみ乱数の初期位相が加算される（ログ上は `SCSI CMD Read: ... seek cycles` の debug 行。実行時にのみ確認できる）

## 観測計画（再開後に実施するもの）

既存の `make dev-cdda-integration`（#15、`src/cdda_probe.s`、`tests/integration/geargrafx_cdda.py`）が `$D8` の
開始位置と状態遷移をすでに観測している。新規 ROM を追加すると重複するため、**まず既存 CD-DA 診断に次の 2 件を追加する**方針とする。

| 区分 | ケース | 期待（SOURCE_READ に基づく仮説、未検証） | 判定 |
| --- | --- | --- | --- |
| 正例 | `$D8` で有効な音声 LBA (16 以上) を指定 | `CURRENT_STATE` が PLAYING、`START_LBA` が指定値 | 既存判定（PCM 一致）を流用 |
| 負例 | `$D8` で媒体のセクタ数以上の LBA | GOOD を返し、CD-DA 状態は変わらない（PLAYING にならない） | CD-DA 状態の不変と `TRACE_SCSI_PROBLEM_INVALID_AUDIO_START_LBA` の発生を確認 |
| 負例 | 無媒体（既存の no-disc 経路） | READ 系は CHECK CONDITION ではなく GOOD のまま（ソース上は TEST UNIT READY も GOOD）。**実行証拠なし**。判定は BLOCKED 扱い | `cd-media-state.md` の NOT_RUN 項目を継続 |
| timeout | 既存のゲスト側有界ループ（24-bit カウンタ）を流用 | 上限超過は FAIL/TIMEOUT として記録し、PASS にしない | 既存 `cdread` と同じ規則 |
| IRQ | 既存 CD-DA 診断は**ポーリングのみ** | IRQ 経由の完了通知は観測していない | NOT_TESTED |

## 再開条件

1. WSL 上のホーム配下のクリーンクローンで `python3 tools/setup.py geargrafx` と `python3 tools/setup.py cc65` を完了する
   （`TOOLS_DIR` はリポジトリ外。`GIT_DIR` / `GIT_WORK_TREE` は export しない）。
2. `make dev-cdda-integration MODE=debug` を実行し、固定版の core revision、ROM の SHA-256、`bin_sha256` を証拠として保存する。
3. 上記の正例・負例を `tests/integration/geargrafx_cdda.py` に追加し、結果を `docs/cdseek-device-observation.md` に追記する。
4. 合成 CD は `tools/make_cd_image.py` が生成する自作ディスクのみを使う。純正 BIOS・市販 ROM・抽出データはコミットしない。

## 次に実装可能な範囲

- 追加シナリオ（有効 LBA・媒体範囲外 LBA・予算超過）は実装済みだが、実コア未実行（NOT_RUN）。下記「追加シナリオ」と「追加シナリオの実行手順」を参照。
- 無媒体の観測は、#5 の no-disc 経路（`SetCDROMType(STANDARD)`）と同じ制約（別ローダー）を明記したうえで行う。

## 変更範囲

- 既存の ROM ソース（`src/cdda_probe.s`）と既存の ROM は変更していない。
- `tools/cdda_verify.py`: `check_playback` に開始 LBA の引数を追加（既定値は従来どおり 16）。`check_all` に同じ引数を追加し、
  共通の検査を `check_run` に分離。`check_invalid_audio_start` を追加。
- `tests/integration/geargrafx_cdda.py`: `execute` に予算の引数を追加（既定値は従来どおり）。既存の `main` は変更していない。
- `tests/integration/geargrafx_cdda_scenarios.py`（新規）: 追加シナリオの実行経路。`cdda-scenarios-evidence.json` を書く。
- `tests/host/test_cdda_scenarios.py`（新規）: 合成観測による判定ロジックの検査。実コアの証拠ではない。
- `Makefile`: `dev-cdda-scenarios-integration` を追加。
- 既存 4 負例の判定期待値・期待する拒否理由・evidence のキーとスキーマは変更していない。
