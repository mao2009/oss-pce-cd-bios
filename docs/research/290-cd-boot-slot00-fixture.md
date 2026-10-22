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

`tests/host/test_cdboot_slot00_fixture.py` に、次の 3 群（計 10 件）を追加した。モジュールの挙動は変えていない。

- 媒体の境界（`Slot00MediaBoundaryTests`、4 件）: 無媒体（None）と空ファイル（`b""`）は別物で、空ファイルは MALFORMED。1 バイト短い・長い・マジックのみ・マジックが 1 バイトずれた媒体は MALFORMED。完全な 1 セクタのみが BOOT_CANDIDATE。末尾 1 バイトを変えた媒体は、分類は BOOT_CANDIDATE のままだが、SHA が一致しないため judge は REJECTED。
- UNKNOWN と BLOCKED（`Slot00UnknownVsBlockedTests`、5 件）: 未計測項目（`boot_target` など）は UNKNOWN のまま有効。具体値を入れると BLOCKED。結果が UNKNOWN の自己申告は BLOCKED、値の矛盾は REJECTED。証拠が無い場合は全ケースで BLOCKED。
- 成功非報告（`Slot00NeverReportsSuccessTests`、1 件）: 媒体 SHA・boot・error_code・executed_callee・schema・slot の全組合せを総当たりし、MODEL_CONSISTENT になるのは真の自己申告と完全一致する場合だけであることを確認。

負例（変異検査、一時コピーで実施、`unittest` の failures 数。サブテスト単位）:

| 変異 | 変異の内容（置換前 → 置換後） | 失敗数 | 失敗したテストメソッド数 |
| --- | --- | --- | --- |
| M1 | `if len(media) == SECTOR and media.startswith(MAGIC):` → `if media.startswith(MAGIC):` | 3 | 1 |
| M2 | `if error is not None and (not isinstance(error, str) or error not in ERROR_CODES):` → `if error is not None and not isinstance(error, str):` | 6 | 3 |
| M3 | `return "REJECTED", ["media identity mismatch: ..."]` → `return "MODEL_CONSISTENT", ["media identity mismatch: ..."]` | 11 | 4 |
| M4 | `for field in ("boot_target", "return_path", "stack", "irq")` → `for field in ()`（未計測項目の検査を外す） | 16 | 1 |
| M5 | `if evidence is None:` の BLOCKED 返却 → `return "MODEL_CONSISTENT", ["missing"]` | 4 | 2 |
| M6 | `if evidence["schema"] != EVIDENCE_SCHEMA or evidence["slot"] != SLOT:` → `if False:` | 3 | 2 |

M2 の失敗 6 件の内訳は、#344 の既存テスト（`test_malformed_missing_or_unknown_evidence_is_blocked`）が 2 件、新規テストが 4 件である。M6 は、新規の総当たりと既存テストの両方で検出された。

限界: 境界の追加検査も合成バイトのみで、実媒体・実コア・純正 slot `$00` の実行は行っていない（NOT RUN）。`MODEL_CONSISTENT` は実行の証拠ではない。

追補（Wave 2026-10-22）: 許可外アドレス（`entry_candidate` が `$E000` 以外）・別 slot・未承認 schema の case を `validate_case` が拒否し `judge` が BLOCKED にすることを `test_case_outside_the_single_slot00_api_is_blocked`（4 サブテスト）で検査した。変異 M7（`validate_case` の schema/slot/entry_candidate 検査を `if False:` に置換）は追加前は全テスト通過（未検出）、追加後は失敗数 4 で検出される。モジュールの挙動は変えていない。
