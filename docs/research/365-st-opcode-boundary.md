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

## 追加する境界範囲（#365 の限定完了条件）

`geargrafx_st_boundary.py` は、既存三ケースに加えて、guest が `TAM #$04` で MPR2 を `$F8` に変え、CPU page 2 (`$4010`) で fetch する ST2 を扱う。モニタは停止 PC、ページ、MPR、`Memory::Read(PC)` の opcode を同時に記録し、CPUが fetch するマッピングと検査対象を一致させる。

実コアの RED 変異は、(a) opcode 検査全体を外す、(b) 先頭 PC の検査を外す、(c) ST2 を禁止集合から外す、(d) TAM 後に再検査しない、の各々で ST の VDC 副作用が着地することを要求する。通常の `$3E50` RAM 書込みは引き続き許可する。

## #365 の外に残る BLOCKED

この限定された single-step synthetic monitor は、自由実行の全命令列に対する VDC interceptor ではない。#366 の IRQ handler entry、#367 の block transfer / device-originated write、任意の reference BIOS JSR は対象外である。`tools/reference_jsr_gate.py` と `BLOCKED_ABI` / `release_eligible=false` は変更しない。

#198 の結論（ST0/1/2 の WriteFromCPU 迂回は到達不能化以外に緩和策なし）は、本 PR では覆していない。
