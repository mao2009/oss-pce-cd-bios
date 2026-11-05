# DEV専用 内部起動経路（Issue #501）

**状態: DEV専用・非互換。** 本番互換、純正 System Card ABI、DEV_CALLABLE 承認、ABI VERIFIED のいずれも主張しない。

**位置づけ:** 本経路は DEV 専用の内部起動経路の実コア検証である。純正 CD_READ($03) / CD_EXEC / CD_BOOT の互換実装ではなく、BIOS 経由の商用ゲーム起動成功を示すものでもない。
ホストは guest RAM へ直接データを注入しない。転送はすべて ROM 内 HuC6280 コードが READ(6) を発行して行う。

## 経路

reset → `dev_boot_reset`（1 バイトの SEI、リセットベクタの指す先）→ `dev_boot_entry`（直後のアドレス、最初の観測点）で MPR0/MPR1/SP を設定 → `cd_media_state`（LBA 0 を READ(6)、
合成CDのマジック一致を確認、結果 `MS_RES`）→ 結果が `$01` の場合のみ `cd_entry`（src/cd_load_exec_probe.s、
LBA 5 を READ(6) で 2048 バイト `$2800` へ転送、`PCEX` ヘッダ検証、`$3000` へコピー、ジャンプ）→ ペイロードが
マーカー `$3F00..` を書き、`$3F10` の戻りベクタで戻る → 戻り後にマーカー検査 → `cd_halt_ok`。

媒体確認が `$01` 以外の場合は `dev_media_halt`（無限ループ）で停止し、READ(6) LBA 5 は発行されない。

## 結果ブロックと RAM の割り当て（衝突なし）

| アドレス | 用途 | 所有 |
|---|---|---|
| `$2300..$231F` | cd_load_exec 結果ブロック（RES） | src/cd_load_exec_probe.s（cd_entry が毎回クリア） |
| `$2800..$2FFF` | 転送用ステージングセクタ（BUF）。媒体確認の読みの後に LBA 5 が上書き | 共有（逐次） |
| `$3000..$30FF` | ペイロード（EXEC） | src/cd_load_exec_payload.s |
| `$3F00..$3F06` | ペイロードのマーカー | 同上 |
| `$3F10` / `$3F12` | RETVEC / EXECVEC | src/cd_load_exec_probe.s |
| `$3F40..$3F46` | cd_media_state 結果ブロック（MS_RES） | 本経路のみ（`-D MS_RES=$3F40`） |

既定ビルド（`MS_RES` 未定義）の cd_media_state は従来どおり `$2300` を使う（reset_entry 経路、#144）。
`DEV_BOOT_INTERNAL` 未定義の cd_load_exec_probe は従来どおり `cd_entry` をリセットベクタとする。

## ビルドと検証

- ビルド: `make dev-boot-internal`（tools/build_dev_boot_internal.py）。リセットベクタ `$FFFE` が `dev_boot_reset` を指し、`dev_boot_entry` == `dev_boot_reset`+1 で、`dev_boot_reset` の先頭バイトが SEI（`$78`）であることを ROM バイトで検査する。
- 統合試験: `make dev-boot-internal-integration`（tests/integration/geargrafx_dev_boot_internal.py）。実 Geargrafx コア（pinned）で cold と warm の両方を実行する。
- 位置づけ: DEV専用。Issue #505 により `core-harness.yml` の追加ジョブ `dev-boot-internal-integration` が PR ごとに `make dev-boot-internal-integration` を実行する（回帰検出）。既存ジョブの条件・needs・required check は変更していない。`make test` / `make check` / `make test-real-core` には含まれない。この CI 結果は DEV 内部起動経路の回帰検出であり、純正互換や BIOS 経由の商用起動の成功を意味しない。
- 停止位置: 媒体確認が失敗した場合、判定は `dev_media_halt` での停止（PC と理由 `dev_media_halt` の両方）を要求する。別 PC や budget 停止は拒否される。
- 正例の判定: `dev_boot_entry` の到達、2 回の READ(6)（LBA 0 と LBA 5、SCSI 位相トレース）、RAM 2048 バイトの全バイト比較、ペイロード全体、マーカー、PC 順序、媒体確認結果 `$01`。
- 負例（実コアで拒否されること）: セクタ数 2 を要求する ROM（ゲストが `INVALID_REQUEST` = 9 で停止）、LBA 0 にペイロードを置いた媒体（媒体確認で `MEDIA_UNRECOGNISED`）、媒体なし（固定 Geargrafx では `NO_DATA` = `$03` で `dev_media_halt`。実機の主張ではない）。媒体なしの scenario `status` は `BLOCKED` であり、PASS 集計には含めない。一方、その cold/warm 拒否判定とミューテーション拒否が実行・成功したことは別フィールド `assertion_result: PASS` で記録する。マーカー誤り（ゲストが `PAYLOAD_MARKER_MISMATCH` で停止）、転送バイトの改ざん（判定側が差分を検出）、ヘッダのマジック不一致、観測の改変（判定側のミューテーション）。

## 非目標

- 純正 slot（`$00/$03/$05` 等）の公開 ABI、CD_READ(`$03`)、純正 BIOS API は実装しない。
- 実機、第二エミュレータ、本番ブート順序は未検証。
- `#145` / `#241`〜`#243` / `#291` の受入条件は緩めない。
