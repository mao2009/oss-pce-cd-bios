# フレームトレーサーの計測表

対象：`tests/integration/geargrafx_frame_tracer.{cpp,py}`（A1：#619、PR-A #620、PR-B #623、PR-C）

このトレーサーは、固定版 Geargrafx（`tools/dependencies.json` の revision）を通常のフレーム実行で動かし、その観測結果を出力する。結果は合成媒体上の診断にすぎない。BIOS の互換性、実機での挙動、商用ソフトの動作を示す証拠ではない。正しさは同一実行内の不変条件だけで判定する。電源投入時の乱数（MPR など）の影響があるので、2回の実行結果を比べて一致を判定することはしない。

## ステータス

| ステータス | 意味 |
|---|---|
| `MEASURED` | 同一実行内の不変条件で裏付けられた、正確な値 |
| `REPORTED_BY_CORE` | コアが報告する設定値。使用量の実測ではない |
| `NOT_MEASURED` | 正確な値が得られない。`value` は持たず、`reason` だけを持つ |
| `LOWER_BOUND` | 読めたエントリだけから計算した下限。測定対象そのものの値ではなく、必ず別の行に置く |

## 項目

| 項目 | ステータス | 意味 |
|---|---|---|
| `core_capacity` | REPORTED_BY_CORE | `TraceLogger::GetCapacity()` の値。`requested` は要求値（0 は既定値） |
| `output_ring_dropped` | MEASURED | 出力リングの欠落件数。独立カウンタで `pushed == retained + dropped` を確認できた場合に限る |
| `max_frame_core_entries` | MEASURED（不連続があれば NOT_MEASURED） | 1フレームに記録されたコアトレースエントリ数の最大値。容量で切り詰める前の値なので、容量を超えうる |
| `core_trace_dropped_frames` | MEASURED（不連続があれば NOT_MEASURED） | 読み出す前にコアが上書きしたフレームの数。`lost_entries` に件数を併記する |
| `max_frame_scsi_events` | 欠落フレームも不連続フレームも 0 のときだけ MEASURED。それ以外は NOT_MEASURED | 1フレームの SCSI エントリ数（コマンド、フェーズ、レスポンスのフィルタ後）の最大値 |
| `scsi_events_lower_bound` | LOWER_BOUND（`max_frame_scsi_events` が NOT_MEASURED のときだけ存在する） | 実際に読めた SCSI エントリだけから求めたフレーム最大値。真の最大値はこの値以上 |

## 「フレーム」の定義

フレーム n は、n−1 回目の `RunToVBlank` が返った時点のサンプルから、n 回目の `RunToVBlank` が返った時点のサンプルまでの区間を指す。

- フレーム1は、トレーサーの `TraceLogger::Reset()`（フレーム1の直前）から、最初の `RunToVBlank` が返るまでの区間。
- warm reset を要求した場合、`ResetMedia(true)` はそのフレームの `RunToVBlank` の直前に実行され、同じフレームに含まれる。
- 区間内で発生した RUN 入力などの公開 API 呼び出しも、その区間に含まれる。
- debug ビルドの `RunToVBlank` はブレークポイントで早く返ることがある。このトレーサーはブレークポイントを設定しないが、厳密には上記の「サンプル間」が定義であり、ビデオフレームと常に一致するとは限らない。

## `summary` と `measurements`

- トレースログの `summary` は、C++ が出力した END のうち allowlist 内のフィールドだけを保存したもの。ステータスを持つのは `measurements` だけ。`capability`/`events` も allowlist 内フィールドのみ。
- 未知フィールド（トップレベル・ネスト含む）は `parse_records()` で拒否される。私的パス・媒体名・ハッシュ・任意文字列が混入した出力は公開ログに保存されない。
- 例外経路（job 作成・probe build・run_job・parse・cleanup）の失敗理由は固定の汎用文言で公開され、絶対パス・媒体ファイル名・ハッシュ・生の OS/コンパイラ錯誤は含まない。詳しい診断は非公開ローカル診断に限定する。
- `summary.max_frame_core_entries` は、容量で切り詰めた後の保持数の最大値（旧フィールド）。`measurements.max_frame_core_entries` は切り詰める前の値で、旧フィールドは `retained_max` として併記している。
- `summary.max_frame_scsi_events` が正確なのは、`measurements.max_frame_scsi_events` が MEASURED のときだけ。それ以外の場合は `scsi_events_lower_bound` の値と同じ（下限）になる。
- `summary.core_trace` などの生の値を、そのまま測定値として引用しない。

## ゲスト状態の検査範囲

`trace_api_guest_checks` と `trace_api_guest_changes` は、トレーサー自身の TraceLogger API 呼び出しの前後だけを比較した結果である。比較するのは CPU レジスタ、MPR、マスタークロック、タイマー、IDR/IRR、CD/SCSI の状態、観測対象の RAM で、セットアップ時に1回、以後フレームごとに1回行う。

- トレースを有効にしたことが `RunToVBlank` 内のコア動作に与える影響は、この検査の対象外。観測器が干渉しないことの主張ではない。
- warm reset（`RESET` 行）は、要求に基づいてゲストを意図的にリセットする操作であり、この検査の対象外。

## warm reset

`--warm-reset-frame N`（2..frames）を指定すると、フレーム N の `RunToVBlank` の直前に、固定版コアの `GeargrafxCore::ResetMedia(true)` を1回だけ呼ぶ。これは既存の API プローブと同じ warm reset である。

- **宣言**：CAP の `guest_mutation` は、リセットを要求しない場合は `false`、要求した場合は `"requested_warm_reset"` になる。どちらの値も、トレーサー自身がゲストのメモリやレジスタに書き込むことはないことを前提としている。読み手は、`guest_mutation` の値と `warm_reset_frame` および `RESET` 行が一致することを必須とする。
- **コアの挙動**：`Reset()` は、コアの状態を次のように変える。`preserve_ram` で戻るのはバックアップ RAM だけで、`RESET` 行の `ram_preserved` は `backup_ram_only` になる。
  - マスタークロックを 0 に戻す。
  - CPU、VDC、CD、SCSI をリセットする。
  - MPR を初期化し直す（コアの設定に従い、固定値または乱数）。
  - WRAM、CD RAM、カード RAM を書き換える（WRAM も、固定値または乱数）。
- **TraceLogger**：`Reset()` は TraceLogger に触れない。そのため、リセットをまたいでもカウンタは連続し、不連続フレームは発生しない（実コアテストで確認している）。
- **フレーム N の RAM**：フレーム N の RAM 差分には、リセット自体による書き換えが含まれる。そのため次のように扱う。
  - そのフレームの SCSI 相関は UNKNOWN とする。
  - `scsi_update_frames` には入れない（読み手が検証する）。
  - HANDOFF 候補は出さない。
- **`event_cycle`**：マスタークロックが 0 に戻るので、リセット後の REGION の `event_cycle` は小さくなる（巻き戻る）。`event_cycle` を実行全体で単調な時刻として扱わないこと。

## 容量の上限

`MAX_CORE_CAPACITY`（Python）は、固定版の `TRACE_BUFFER_SIZE`（100000）と同じ値である。C++ 側の上限は `TRACE_BUFFER_SIZE` そのもので、これを超える値は argv の段階で拒否する。この対応関係は次のテストで固定している。

- host テスト：C++ ソースの上限式を確認する。pinned ソースがある場合は `trace_logger.h` の値と照合する。
- 実コアテスト：容量 100000 での実行を確認する。

## REGION イベント

`region_kind` は次のいずれかになる。

- `baseline`：実行で最初の REGION。それ以前に欠落はない。
- `after_gap`：コアトレースの欠落または不連続の後に出る、最初の REGION。
- `transition`：既知の領域間の遷移。

`trace_gap` は、欠落が起きたフレームでは消えず、次の REGION まで持ち越す。そのため `from_region="unknown"`・`trace_gap=false` の REGION は `baseline` だけで、実行全体で先頭に1回しか現れない。

## CD wrapper の証拠ファイル

`build/<mode>/frame-tracer-cd/frame-tracer-measurements.json` は、開始時に `status: INCOMPLETE` で上書きされる。正常に完了した場合だけ `status: PASS`（`evidence_valid: true`）に書き換わる。BLOCKED や FAIL で終わった場合は、件数を含まない記録で上書きする。前回の成功時のファイルが今回の成功と取り違えられることはない。上書きするのはこのファイルだけで、同じディレクトリの他のファイルは削除も変更もしない。
