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

探索したパス（名称のみ）: worktree 直下（`Makefile`, `tools/`, `tests/`, `docs/` を確認）、`C:/Users/<user>/oss-pce-cd-bios-tools`
（存在せず）、`<repo>/rom/`（`Geargrafx/`, 市販ゲームのフォルダ, `syscard3.pce` の存在のみ確認。中身は読んでいない）、PATH 上の
`make` / `ca65` / `ld65`（無し）、`python`（3.14 あり、`gcc` は WinLibs の mingw のみ）。

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

- 既存 CD-DA 診断への負例 2 件（無効 LBA の `$D8`、状態不変の確認）の追加。ROM 側の変更は `src/cdda_probe.s` の小さな分岐のみを想定。
- 無媒体の観測は、#5 の no-disc 経路（`SetCDROMType(STANDARD)`）と同じ制約（別ローダー）を明記したうえで行う。

## 変更範囲

本 PR はドキュメントのみ。コード・ROM・テストは追加していない。理由は、実コア上で実行していない観測を
テストとして書いても PASS 根拠にならず、既存の CD-DA 診断に統合する方が重複が少ないため。
