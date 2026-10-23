# Issue #306: BM_FREE 合成フィクスチャの証拠記録（完了条件 2）

本書は Issue #306（BM_FREE Fixture 段階）の完了条件 2「出典（URL・revision・行）と PASS/FAIL/NOT_RUN/BLOCKED の記録」を満たすための記録である。

- **全ケースは合成（SYNTHETIC）である。** 対象は `tools/bm_free_fixture.py` と `tools/buram_free_judge.py` の合成イメージ（2048 byte、128 byte ブロック、15 データブロック、プロジェクトの創作）。System Card の形式でも、実 BIOS の出力でもない。
- 「MEASURED」は、合成入力に対してホストの `unittest` を実行した結果だけを指す。BIOS の実行結果ではない。
- 実バックアップ RAM、実ユーザーのセーブ、実 BIOS、ROM バイトには一切触れていない。BIOS の FORMAT（`bm_format` / `$E048`）も実行していない。
- `release_eligible=false`。BM_FREE の API は `BLOCKED_ABI` のままである（下記 §3）。

## 1. 出典（#305 契約台帳）

| 項目 | 出典 | 位置 |
| --- | --- | --- |
| 呼出側の定数・宣言（S1–S3） | `docs/305-bm-free-contract.md` §1 出典表 | L15–L17（S1: pce-devel/huc revision `54f5c736…`、S2: `hucc-systemcard.asm`、S3: `hucc-systemcard.h`） |
| 呼出先の判定（R01–R21） | `docs/305-bm-free-contract.md` §2 契約台帳 | L28–L50。R03 / R05 / R08 / R10 / R13 は CONFIRMED-by-source（呼出側の宣言のみ）。R04 / R06 / R09 / R12 / R14 / R17 / R18 は UNKNOWN または HYPOTHESIS |
| 合成モデルの扱い（S8） | `docs/305-bm-free-contract.md` §1 出典表 S8、§2.1 | L22、L54–L60（`PROJECT-INVENTED`。台帳の判定に使わない） |
| API の状態 | `spec/api_contracts.json` slot 25、`spec/api_slots.json` id 25 | `mapping_status: HYPOTHESIS`、引数・戻り値・副作用は UNKNOWN |
| 安全ゲート | `docs/305-bm-free-contract.md` §4 | L70–L77（判定 BLOCKED_ABI、プローブは NOT_RUN） |

## 2. ケース別の記録（ホストの合成テスト）

実行対象: `tests/host/test_bm_free_fixture.py`。`python3 -m unittest discover -s tests/host -p 'test_bm_free*' -v` の結果のうち、fixture ファイルの件数は **22 件**（同パターンの contract ファイル 6 件を含めた合計は 28 件）。件数は上記コマンドの実行結果から数えた。

期待値はすべて手計算のリテラルである（検算: 「SBR1」の和 0x53+0x42+0x52+0x31 = 0x118、版 0x01 を足して 0x119、下位バイトが 0x19。以後は使用ブロック数を足す）。`tools/bm_free_fixture.py` の各フレームも同じ手計算のリテラル（`_frame` の引数）で、`bj._checksum` や `bj.make_image` からは作らない。したがってチェックサム式を変えると、フィクスチャのケースも失敗する（§4）。

「証明すること」は、そのテストが判定器の挙動について言える範囲。「証明しないこと」は、合成の範囲外（実 BIOS、実 RAM、実セーブ、System Card 形式）であり、これらはテストでは主張しない。

| ケース | テスト名 | 合成入力 | 期待 | 証明すること | 証明しないこと | 状態 |
| --- | --- | --- | --- | --- | --- | --- |
| 正常ヘッダ・全ブロック空き | `test_all_free_fixed_bytes_count_15` | SYNTHETIC | OK、空き 15 | 手計算の全空きフレームを OK と数える | 実 BIOS の判定結果 | PASS（MEASURED） |
| 正常ヘッダ・ブロック 0 使用 | `test_block0_used_fixed_bytes_count_14` | SYNTHETIC | OK、空き 14 | 使用 1 件で空きが 1 減る | 使用の意味（実ブロック番号との対応） | PASS（MEASURED） |
| 正常ヘッダ・ブロック 1,2,14 使用 | `test_used_blocks_1_2_14_fixed_bytes_count_12` | SYNTHETIC | OK、空き 12 | 離れた位置の使用も数える | 任意の使用パターン | PASS（MEASURED） |
| 正常ヘッダ・全ブロック使用 | `test_all_used_fixed_bytes_count_0` | SYNTHETIC | OK、空き 0 | 全使用でも空き 0 を OK と返す（満杯は不正扱いしない） | 満杯時の BIOS 側の挙動 | PASS（MEASURED） |
| 未初期化（FF / 00） | `test_erased_and_blank_images_are_unformatted_with_no_free_count` | SYNTHETIC | UNFORMATTED、空きなし | 消去済み・空の画像を未初期化とし、空き数を返さない | 未初期化画像に対する実 BIOS の応答 | PASS（MEASURED） |
| 不正ヘッダ（magic / 予約 / checksum / 表値 0x02 / garbage） | `test_invalid_headers_are_rejected_without_a_free_count` | SYNTHETIC | INVALID_HEADER、空きなし | 5 種の不正を空き数なしで拒否する | 他の不正形式（網羅ではない） | PASS（MEASURED） |
| シナリオ表（正常・未初期化・不正・サイズ） | `test_scenarios_match_expected_verdict` | SYNTHETIC | 各シナリオの期待判定 | フィクスチャ表の 8 件が手計算の期待判定と一致する。チェックサム変更を検出する（§4） | 表に無い入力 | PASS（MEASURED） |
| 容量境界（0 / 1 / 全ブロック） | `test_capacity_boundaries_zero_one_and_all_blocks` | SYNTHETIC | 正確な空き数 | 空き数 15 / 14 / 0 の境界 | 15 ブロックを超える容量 | PASS（MEASURED） |
| 索引範囲外の書込み拒否（builder） | `test_block_index_outside_capacity_is_refused_by_the_builder` | SYNTHETIC | ValueError | 合成ビルダーが索引 15 を拒否する。`bj.make_image` の範囲チェックだけを見る | 判定器の正しさ（期待値は手計算でない） | PASS（MEASURED） |
| サイズ境界（0 / 2047 / 2049） | `test_size_boundaries_are_rejected_not_counted` | SYNTHETIC | BAD_SIZE、空きなし | 長さ違いを BAD_SIZE で拒否する | 2048 以外の実機イメージ | PASS（MEASURED） |
| 表値境界（0x01 は OK、0x02 は拒否） | `test_table_byte_boundary_0x01_ok_and_0x02_rejected` | SYNTHETIC | 0x02 のみ拒否 | 表値の許容範囲の両端 | 0x00 / 0x01 以外の意味づけ | PASS（MEASURED） |
| 偽陽性（ヘッダ・表の全単一ビット反転） | `test_no_single_bit_flip_in_header_or_table_is_accepted` | SYNTHETIC | 全反転が非 OK、反転数 248 | 31 バイト × 8 ビットの単一ビット反転を全て拒否する | 2 ビット以上の反転、表の外（バイト 31 以降） | PASS（MEASURED） |
| 型の拒否（str / ReadOnlyImage / 整数） | `test_judge_refuses_str_and_fixture_objects` | SYNTHETIC | TypeError | 判定器は bytes / bytearray だけを受け付ける | 他の型の網羅 | PASS（MEASURED） |
| 型の拒否（fixture 構築時の非 bytes） | `test_fixture_refuses_non_bytes_construction` | SYNTHETIC | TypeError | フィクスチャの構築時に型を検査する | フィクスチャ外の入力経路 | PASS（MEASURED） |
| 読取りの範囲外 | `test_fixture_read_out_of_range_is_refused` | SYNTHETIC | IndexError | 範囲外・負の読取りを拒否し、境界内の読取りは成功する | 読取り以外の経路 | PASS（MEASURED） |
| 書込み拒否（代入・削除） | `test_fixture_refuses_every_write` | SYNTHETIC | PermissionError | 代入と削除を拒否し、`__dict__` を持たない | 外部からの `__slots__` 迂回（CPython 依存の保証ではない） | PASS（MEASURED） |
| 呼出し元バッファの複製 | `test_fixture_copies_caller_buffer` | SYNTHETIC | 元の変更が反映されない | 生成後に呼出し元が書き換えても、フィクスチャは不変 | 他のエイリアス経路 | PASS（MEASURED） |
| 判定器が bytearray 入力を変えない | `test_judge_does_not_mutate_a_bytearray_input` | SYNTHETIC | 判定後も入力が同一 | 判定器が呼出し側の bytearray（パディング含む）を書き換えない | 判定器の内部状態 | PASS（MEASURED） |
| 判定中の I/O なし・入力非変更 | `test_judging_never_mutates_the_fixture_and_attempts_no_write` | SYNTHETIC | I/O の試行 0 件 | `open` / `os.open` / `os.write` を差し替えた状態で判定しても I/O が起きない | 他の I/O 経路（例: `os.read`、C 拡張）の網羅 | PASS（MEASURED） |
| fixture とジャッジの禁止 import と許可リスト | `test_fixture_and_judge_have_no_forbidden_imports_and_match_allowlist` | SYNTHETIC | 禁止モジュール 0、import は許可リストと一致 | 両モジュールの top-level import が許可リストと完全一致する。judge は import 0 件、fixture は judge のみ | 動的 import（`__import__` 等）、間接的な I/O | PASS（MEASURED） |
| 手計算の定数（2 式） | `test_hand_layout_literals_are_consistent` | SYNTHETIC | 0x19、1920 バイト、2048 バイト | 手計算の header 和（0x19）と容量（15 × 128 = 1920）が一致する | 実 System Card の定数 | PASS（MEASURED） |
| bytes と bytearray の判定一致 | `test_bytes_and_bytearray_views_agree` | SYNTHETIC | 両者が同じ判定、全空きは (OK, 15) | 同じ入力の bytes / bytearray の判定が一致し、手計算値 15 と一致する | 他の型 | PASS（MEASURED） |

計: fixture テスト 22 件中 PASS 22、FAIL 0、NOT_RUN 0、BLOCKED 0（ホストの合成ケースのみ）。

## 3. 合成では記録できない項目

| 項目 | 状態 | 理由 |
| --- | --- | --- |
| 実 BIOS の BM_FREE 呼出し（`$00:E04B`） | BLOCKED_ABI | 呼出し ABI が未確定（#305 R04–R21、slot 25 は UNKNOWN）。JSR 安全ゲートは BLOCKED（#305 §4、S7） |
| 実バックアップ RAM の読取り | NOT_RUN | 実機・実 ROM を使わないという本 Issue の範囲による |
| 実ユーザーのセーブ | NOT_RUN | 対象外。読み書きしていない |
| BIOS の FORMAT（`bm_format`） | NOT_RUN | 書込み経路であり、本 Issue では実行しない（#305 R16） |
| System Card の実形式との一致 | UNKNOWN | 合成モデルは PROJECT-INVENTED（#305 S8、R21）。根拠にならない |

判定: 本 Issue の完了条件 2 は「合成ケースの記録」として満たす。BIOS 実機の BM_FREE 結果は BLOCKED_ABI であり、PASS とは記録しない。

## 4. 変異テスト（scratch の複製のみ。ブランチは未変更）

判定器の `_checksum` に +1 した複製（scratch）で、フィクスチャ由来のケースが変化を検出するかを確認した。

| 実行 | 内容 | 終了コード | 結果 |
| --- | --- | --- | --- |
| control | 無変更の複製 | 0 | 28 件 OK |
| 変異 | `_checksum` に +1 | 1 | 11 件 FAIL（うち `test_scenarios_match_expected_verdict` の 2 件が fixture 由来の期待判定の不一致） |

フィクスチャのフレームは手計算のリテラルであり、`_checksum` からは作らない。そのため、チェックサムを変えると生成側ではなく期待値側の不一致として検出される。
