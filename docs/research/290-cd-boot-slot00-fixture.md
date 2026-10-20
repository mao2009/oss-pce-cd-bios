# Issue #290: slot $00（CD_BOOT、候補 `$E000`）の非実行フィクスチャと起動可否モデル

状態: **定義と非実行モデルのみ。純正 slot `$00` は実行しない。System Card 互換性も起動成功も主張しない（起動可否は常に BLOCKED）。**
親は #9。実装は `tools/cdboot_slot00_fixture.py`、検査は `tests/host/test_cdboot_slot00_fixture.py`。

## 1. フィクスチャ（すべて独自生成、メモリ上のみ）

| case_id | 媒体 | モデル分類 | 期待する自己申告 |
| --- | --- | --- | --- |
| `slot00_valid_synthetic` | 独自マジック（`OSS-PCE-SYNTH-CD-BOOT`）を置いた 2048 バイト | `BOOT_CANDIDATE` | boot_claimed=true、error_code=null |
| `slot00_absent` | 媒体なし | `NO_MEDIA` | boot_claimed=false、error_code=`NO_MEDIA` |
| `slot00_malformed` | マジックを壊した 2048 バイト | `MALFORMED` | boot_claimed=false、error_code=`MALFORMED` |

- 媒体バイトは `synthetic_media()` が生成し、ファイルには書かない。ISO9660 の `CD001` とraw sync のパターンを含まない（テストで確認）。
- 分類は独立した `classify_media()` で媒体バイトから決める。フィクスチャのラベルから期待値を作らない。
- 起動後の挙動（移譲先、スタック、IRQ、復帰）はすべて UNKNOWN。`execution` は NOT_RUN。

## 2. 判定（`judge`）

| 結果 | 条件 |
| --- | --- |
| BLOCKED | 自己申告が無い、形式不正、未知の結果（`UNKNOWN` や未定義の error_code）、`executed_callee` が false 以外、SHA が不正 |
| BLOCKED（構造検査） | ケース側の期待 SHA や分類の改ざん（`validate_case` が malformed case と判定し、judge は BLOCKED） |
| REJECTED | 形式は正しいが、媒体 SHA 不一致（なりすまし・別媒体）、モデルと矛盾する boot_claimed や error_code |
| MODEL_CONSISTENT | モデルとだけ一致する。実行の証拠ではなく、PASS でもない |

`execution_status()` は常に BLOCKED を返す（CD_BOOT の ABI は BLOCKED_ABI）。

## 3. 境界

- 自己申告は LOCAL_ONLY。`MODEL_CONSISTENT` を実コアの観測や System Card 互換の証拠として扱わない。
- 実コアでの slot `$00` 実行、媒体の実読取り、BIOS 経由のブートは未実施（NOT RUN）。
- `rom/` の私的入力は読まない。公開テストは合成データのみで動く。
