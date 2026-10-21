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

## 4. 境界・UNKNOWN と BLOCKED の区別・成功非報告（追加検査）

`tests/host/test_cdboot_slot00_fixture.py` に、次の 3 群（計 11 件）を追加した。モジュールの挙動は変えていない。

- 媒体の境界（`Slot00MediaBoundaryTests`）: 無媒体（None）と空ファイル（`b""`）は別物で、空ファイルは MALFORMED。1 バイト短い・長い・マジックのみ・マジックが 1 バイトずれた媒体は MALFORMED。完全な 1 セクタのみが BOOT_CANDIDATE。末尾 1 バイトを変えた媒体は、分類は BOOT_CANDIDATE のままだが、SHA が一致しないため judge は REJECTED。
- UNKNOWN と BLOCKED（`Slot00UnknownVsBlockedTests`）: 未計測項目（`boot_target` など）は UNKNOWN のまま有効。具体値を入れると BLOCKED。結果が UNKNOWN の自己申告は BLOCKED、値の矛盾は REJECTED。証拠が無い場合は全ケースで BLOCKED。
- 成功非報告（`Slot00NeverReportsSuccessTests`）: 全組合せ（媒体 SHA・boot・error_code・executed_callee）を総当たりし、MODEL_CONSISTENT になるのは真の自己申告と完全一致する場合だけであることを確認。absent・malformed で boot を主張する記録は MODEL_CONSISTENT にならない。

負例（変異検査、一時コピーで実施）: 長さ検査の削除、UNKNOWN 結果の扱いの変更、同一性不一致の REJECTED 化、未計測項目の検査の削除、証拠なしの MODEL_CONSISTENT 化の 5 件を入れると、いずれも追加テストが失敗した。

限界: 境界の追加検査も合成バイトのみで、実媒体・実コア・純正 slot `$00` の実行は行っていない（NOT RUN）。`MODEL_CONSISTENT` は実行の証拠ではない。
