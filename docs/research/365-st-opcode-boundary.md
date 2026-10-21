# #365 ST0/ST1/ST2 命令境界の副作用前遮断（opcode 検査）の判定記録

関連: 親 #173（BLOCKED 維持）、先行実測 PR #368 / #383（マージ済み）、#198（closed）。
本記録は #365 の受入条件を既存実測に対して照合し、残作業を明示するためのもの。ゲートは変更しない（`BLOCKED_ABI`、`release_eligible=false` を維持）。

## 実測の由来

- 対象: pinned Geargrafx debugger ビルド `b49ae82a012eade566d72e9d61c9d95a5caa4da3`、合成 BIOS / 合成 CD、MPR0 = $00（$FF ではない）。
- 純正 BIOS・実 ROM・予算付き JSR 実行はなし。
- 再実行: WSL で `python3 tests/integration/geargrafx_st_boundary.py`（main 1cd5582 の #383 プローブ、無改変）。結果は PASS、EVIDENCE 6 行。
- 6 行を `tests/fixtures/365-st-boundary-evidence.json` に無改変で保存（`observation_kind: REAL_CORE_EXECUTION`、`bios: SYNTHETIC`）。

## 測定値（合成 ROM）

| case | 結果 | 命令数 | 停止 PC | opcode | VDC AR | VDC R | resume 拒否 |
|---|---|---|---|---|---|---|---|
| st0-entry | refused | 0 | $3E00 | $03 | $0→$0 | $0→$0 | 3/3 |
| st1-unknown | refused | 1 | $3E10 | $13 | $0→$0 | $0→$0 | 3/3 |
| st2-unknown | refused | 1 | $3E10 | $23 | $0→$0 | $0→$0 | 3/3 |
| st0-unguarded（変異: 監視無効） | ok | 2 | $3E02 | - | $0→$7 | - | - |
| st1-unguarded（変異） | ok | 3 | $3E12 | - | - | $0→$5A | - |
| st2-unguarded（変異） | ok | 3 | $3E12 | - | - | $0→$A500 | - |

陽性対照: 許可域の RAM 書込み `$3E50` は非拒否で着地（PASS）。

## 受入条件の照合

| 条件 | 判定 | 根拠 |
|---|---|---|
| 1 命令ごとの停止で各 ST 直前に止まる（合成 ROM） | 充足（限定） | 上表。ST は JMP 経由の未知 PC（$3E10）でも拒否。任意の自由実行ゲストへの一般化は未実測 |
| 停止中の VDC（AR/R）不変 | 充足（限定） | refused 行で before == after |
| 陽性対照が通る | 充足 | `$3E50` 着地 |
| 変異テスト（ゲート/opcode 検査を外す） | 部分 | 監視無効（ゲート外し）は実コアで副作用着地と判定。opcode 検査だけを外した実コア変異は未作成。ホスト側の代理テスト（`test_removed_opcode_check_proxy_is_rejected`）のみ |
| 機械可読・jsr_observation スキーマ・observation_kind 明示 | 未充足 | `observation_kind` は EVIDENCE 行と fixture に明示。ただし `tools/jsr_observation.py` v1 は A/X/Y/P/SP/PC と IRQ・return を必須とし、プローブはレジスタを採取しないため、値を埋めずには準拠できない（UNKNOWN 不可） |
| 失敗/未実測は BLOCKED 維持 | 充足 | 本 PR はゲートを変更しない |

## 本 PR の範囲

- `tests/host/test_365_st_boundary_record.py`: fixture を #383 の判定器（`check_refused` / `check_unguarded` / `mutation_test_armed`）で再判定する 7 テスト。実コアも BIOS も起動しない。
- `tests/fixtures/365-st-boundary-evidence.json`: 上記 6 行の無改変記録。
- 本記録（`docs/research/365-st-opcode-boundary.md`）。

検証: WSL で `python3 -m unittest -v tests/host/test_365_st_boundary_record.py` → 7 tests OK。`make host-test` → 2071 tests OK（expected failures=1）、新規 7 件を含む。

## 残作業（#365 完了まで）

1. 任意 PC・先頭命令を含む自由実行での opcode 識別（ゲートの一般化）。現状は単一ステップの合成ゲストのみ。
2. opcode 検査のみを外す実コア変異の作成と、拒否されないことの確認。
3. プローブのレジスタ採取を追加するか、`jsr_observation` の別スキーマ（ST 境界用）を新設して機械可読記録を準拠させる。共有ツールの変更は別 Issue で扱う。
4. `geargrafx_st_boundary.py` を `make integration` 経路へ組み込むか判断する（現状は手動実行）。
5. #366（IRQ 入口）、#367（ブロック転送・デバイス起点書込）の結果と統合し、#173 の解除判断は独立レビュー後に限る。

#198 の結論（ST0/1/2 の WriteFromCPU 迂回は到達不能化以外に緩和策なし）は、本 PR では覆していない。
