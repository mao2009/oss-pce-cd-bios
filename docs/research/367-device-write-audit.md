# Issue #367: デバイス起点 RAM 書込みの源泉監査とブロック転送の停止点（ソース監査）

## 目的と状態

親 #173 の V07（W17 デバイス起点書込みの列挙）と V04（TII/TIN/TDD/TIA/TAI の命令途中停止点）のうち、
**ソースの読み取りだけで確定できる部分**を記録する。

- 対象: pinned Geargrafx `b49ae82a012eade566d72e9d61c9d95a5caa4da3`（ツールディレクトリの読み取り専用参照。リポジトリへの複製はしない）。
- 実行なし。純正 BIOS・ROM・JSR は一切実行しない。
- 状態: **BLOCKED / release_eligible=false**。ゲートは開けない。
- 機械可読な記録: `tests/fixtures/367_device_write_sites.tsv`（検証: `tests/host/test_367_device_write_audit.py`）。

## 結論

1. 検索範囲では、CPU 以外が**実行時に** WRAM（`m_wram`、32 KB）へ書く経路は見つからなかった。`m_wram` への書込みはリセット時の初期化のみ（`memory.cpp:123,125`）。
2. デバイス起点の書込み先は ADPCM RAM（64 KB の別配列）、VRAM、VDC 内部の SAT である。いずれも MPR 経由の WRAM には届かない。
3. 未トレース: 外部バックアップ RAM（`mb128.cpp:141,143`）と Arcade Card の書込み起動経路は、起動元を確認していない（UNKNOWN）。
4. ブロック転送は **1 バイト = 1 回の `RunInstruction` 呼出し**の状態機械として実装されている。命令途中で制御が戻る経路はソース上に存在する。実測は未実施（NOT_RUN）。
5. **注意:** `GG_TESTING` 定義時は `Memory::Write` が `m_test_memory` へ書いて即 return する（`memory_inline.h:267-270`）。ハーネスがこのビルドを使うと MPR 検査そのものが素通りする。使用ビルドの定義は未確認（UNKNOWN）。

## 検索方法と限界

- 検索語: `m_wram`、`m_cdrom_ram`、`m_adpcm_ram`、`m_vram`、`m_sat`、`m_memory->Write`、`Memory::Write`、`DMA`、`GetRAM`、`block_transfer`、`m_transfer_state`。
- 限界: 全ファイルを精読していない。検索で見つからない間接書込み（関数ポインタ経由、マクロ展開）は除外できていない。ステート復元（`memory.cpp:498`）はデバイス起点ではないが RAM を書き換えるため、別途扱う。

## デバイス起点書込みの列挙（W17）

| ID | 場所 | 書込み先 | 役割 | WRAM 到達 | 判定 |
|----|------|----------|------|-----------|------|
| DEV-01 | `adpcm_inline.h:173`（`UpdateDMA` :191-224 が SCSI データを取得） | ADPCM RAM（`adpcm.h:95`） | device_store | なし（別配列） | NOT_MAIN_RAM |
| DEV-02 | `huc6270.cpp:630`（VRAM DMA） | VRAM（`dest < 0x8000` のみ） | device_store | なし | NOT_MAIN_RAM |
| DEV-03 | `huc6270.cpp:607`（SATB 転送、VRAM から内部 `m_sat` へ） | VDC 内部 SAT | device_store | なし | NOT_MAIN_RAM |
| DEV-04/05 | `memory.cpp:123,125` | WRAM | reset_store | — | RESET_ONLY |
| DEV-06/07 | `memory.cpp:151,153` | CDROM RAM | reset_store | — | RESET_ONLY |
| UNK-01/02 | `mb128.cpp:141,143` | 外部バックアップ RAM | unknown_store | 未確認 | UNKNOWN |

除外（CPU 起点）: `cdrom.cpp:274`（SCSI データレジスタへの CPU 書込み）、`Memory::Write` 経由の全 CPU 書込み、`huc6280_opcodes_inline.h` の命令ごとの `m_memory->Write`。
Arcade Card（`arcade_card_mapper.cpp`）は `Mapper::Write` 経由の CPU バンク書込みと見られるが、起動元は未検証（UNKNOWN）。

## ブロック転送の 1 命令内構造（W15 / V04 の前提）

| 事実 | 根拠（pinned ソースの行） |
|------|--------------------------|
| 開始時に state=2、A/X/Y を push、src/dst/length を取得し、PC を -7 して同じオペコードを再実行させる | `huc6280_opcodes_inline.h:664-683`（`OPCodes_TransferStart`） |
| state 2 の 1 ステップで 1 バイトを `Memory::Write(dst, Read(src,true), true)` し、length を 1 減らす。length=0 で state=1 | `huc6280_opcodes_inline.h:525-535`（TAI を代表例）。他の 4 命令は :559, :589, :620, :650 の同型ブロック（命令ごとの対応は未精読） |
| state 1 で `OPCodes_TransferEnd` が state=0 にし、A/X/Y を復元 | `huc6280_opcodes_inline.h:687` 付近 |
| `RunInstruction` は 1 呼出し 1 ステップ。`instruction_completed = (m_transfer_state == 0)` | `huc6280_inline.h:56-85` |
| IRQ 受付は `m_transfer_state == 0` のときのみ。転送中の IRQ は保留される | `huc6280_inline.h:90` |
| `block_transfer` フラグは `InjectCycles` の増分（1 または 2）にだけ効く。MPR・mapper・memory_map_write・ハードウェアページの分岐は通常の CPU 書込みと同一 | `memory_inline.h:265-303`（:303 が増分） |
| `CheckMemoryHooks` は store の前に呼ばれる（フック登録時のみ） | `memory_inline.h:276-278` |

対応 ID: BLK-01/02/03/04/05（fixture 上の store 行。BLK-01 が TAI 代表、残りは同型）。

要点: 5 命令はいずれも、1 命令内で length 回の store を行い、**store の間に制御が戻る**。したがって「命令途中で停止できるか」は、駆動ループが `RunInstruction` 単位で止まるかに依存する。ソースは止められる構造を示すが、**実測はしていない（UNMEASURED）**。

## 観測条件（次段の実測に渡す条件）

- 停止単位: `RunInstruction` 1 回（= 1 バイト）。命令単位で止まるのは transfer 完了時のみ。
- 事前遮断の候補点: `CheckMemoryHooks`（store 前）。ただし `HasMemoryHooks` が真のときのみ有効。
- 判定対象: 各バイトの書込み先を、store 前に取得して MPR と窓の述語に通す。許可外が 1 つでもあれば、その store の副作用が無いことを観測する。
- 必要な前提（未確定）: (a) 駆動ループが `RunInstruction` を直接呼ぶか、(b) 使用ビルドで `GG_TESTING` が無効か、(c) `HasMemoryHooks` が有効化できるか。いずれも UNKNOWN。

## 受入条件との対応

| 受入条件 | 状況 | 根拠 |
|----------|------|------|
| 5 命令の命令途中停止の実測 | NOT_RUN（BLOCKED） | 実コア実行なし。構造はソースで確認（上表） |
| 許可外バイトが届かない負のテスト（MPR・memory_map_write） | 静的のみ（既存 PR #379 の `tools/block_xfer_audit.py`） | 本 PR では再実装しない |
| デバイス起点 RAM 書込みの源泉列挙 | 完了（検索範囲内）。UNK-01/02 は未確定 | 上表・fixture |
| 変異テストで偽 PASS が出ないこと | 本 PR の fixture 変異テスト（WRAM 書込み行の注入で検出されること） | `test_367_device_write_audit.py` |

## BLOCKED 理由と解除条件

BLOCKED 理由:
1. 命令途中停止の実測がない（V04 NOT_RUN）。駆動ループが共有のため、#365/#366 の変更待ち。
2. UNK-01/02（外部 BRAM、Arcade Card）の起動元が未トレース。
3. 使用ビルドの `GG_TESTING` 有無が未確認。

解除条件:
- 実コアの WSL 実行で、5 命令それぞれについて、store 前の停止と許可外書込みの不着を記録する（`tools/jsr_observation.py` のスキーマ）。
- UNK-01/02 の起動元を確認し、fixture の verdict を更新する。
- ビルド定義を確認し、`GG_TESTING` が無効であることを記録する。

次の候補: 駆動ループの確定後、V04 の実測を別 PR で行う（共有ループは変更しない）。
