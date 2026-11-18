# #365 任意PC ST命令の副作用前遮断 — main 04922be2 再検証記録

- Issue: #365（親 #173、関連 #367/#459、計画 #361 V01/V02）
- 先行実測: PR #368（既知PC pre-block、合成ROM限定）、PR #732（任意PC v2プローブ、17 armed + 22 mutation + pos-write、pinned core）
- 本記録: 既存実装の重複なし。再利用のみ。新規基盤・本番機構なし。検証用パッチを本番安全機構とみなさない。
- 範囲: 合成ROMのみ。商用媒体・参照BIOS・`rom/` 私的入力は一切不使用（`rom/` は本worktreeに存在しない）。
- ゲート: `release_eligible=false`、`BLOCKED_ABI`、reference JSR 0/81、genuine ABI VERIFIED 0/81 を維持。#173/#367/#459 の安全ゲートは解除しない。

## 実行条件（OBSERVED）

| 項目 | 値 |
|---|---|
| 開始HEAD / 終了HEAD | `04922be2` / `04922be2`（コード無変更、検証のみ） |
| pinned core | `b49ae82a012eade566d72e9d61c9d95a5caa4da3`（debugger ビルド、独立 `/tmp` checkout を直接改変せず） |
| BIOS | SYNTHETIC（MPR0=$00、$FF ではない。純正BIOS JSRなし） |
| 実行様式 | debug single-step。各命令境界で `Memory::TryPeek(PC)` により opcode `0x03/0x13/0x23` を実行前に検査。先頭命令を含む全境界が対象。PC ヒントなし |
| コマンド | `python3 tests/integration/geargrafx_st_boundary.py`（`make st-boundary` と同一、無改変で実行） |
| 日時（UTC） | 2026-11-18（JST 2026-11-18 午後に相当） |

## 実測表（OBSERVED、pinned real core、40 EVIDENCE行）

armed 17 は全て副作用前に拒否／fail-closedし、resume 3/3 拒否。VDC・RAM・実行数は停止前後で不変。

| case | 停止PC | exec | opcode | VDC AR | VDC R | VDC SR | resume | 判定 |
|---|---|---|---|---|---|---|---|---|
| st0-entry（先頭） | $3E00 | 0 | $03 | 0→0 | 0→0 | 0→0 | 3/3 | refused |
| st1-entry（先頭） | $3E00 | 0 | $13 | 0→0 | 0→0 | 0→0 | 3/3 | refused |
| st2-entry（先頭） | $3E00 | 0 | $23 | 0→0 | 0→0 | 0→0 | 3/3 | refused |
| st1-unknown（JMP先未知PC） | $3E10 | 1 | $13 | 0→0 | 0→0 | 0→0 | 3/3 | refused |
| st2-unknown（JMP先未知PC） | $3E10 | 1 | $23 | 0→0 | 0→0 | 0→0 | 3/3 | refused |
| st1-branch-unknown（BNE taken） | $3E10 | 2 | $13 | 0→0 | 0→0 | 0→0 | 3/3 | refused |
| st0-jsr-rts（JSR復帰直後） | $3E03 | 3 | $03 | 0→0 | 0→0 | 0→0 | 3/3 | refused |
| st1-page-boundary（$1FFF） | $1FFF | 1 | $13 | 0→0 | 0→0 | 0→0 | 3/3 | refused |
| st0-rom-page0（$0000） | $0000 | 1 | $03 | 0→0 | 0→0 | 0→0 | 3/3 | refused |
| st1-unknown-far（$3E20） | $3E20 | 1 | $13 | 0→0 | 0→0 | 0→0 | 3/3 | refused |
| st2-mpr2-unknown（TAM後$4010） | $4010 | 3 | $23 | 0→0 | 0→0 | 0→0 | 3/3 | refused |
| st1-unused-bank（$6010） | $6010 | 3 | $13 | 0→0 | 0→0 | 0→0 | 3/3 | refused |
| st0-bbr3-hwpage-skip（BBR3 skip→$3F83） | $3F83 | 4 | $03 | 0→0 | 0→0 | 0→0 | 3/3 | refused |
| irq2-handler-st0-survey（IRQ受入→handler $3F80） | $3F80 | 2 | $03 | 0→0 | 0→0 | 0→0 | 3/3 | refused |
| irq2-core-stop-survey（同上、core stop on） | $3F80 | 2 | $03 | 0→0 | 0→0 | 0→0 | 3/3 | refused |
| st0-hwpage-fetch-blocked（固定JMP→$4000） | $4000 | 3 | TryPeek失敗 | 5→5 | 0→0 | 63→63 | 3/3 | fetch_blocked（fail-closed、step発行なし） |
| st0-bbr3-hwpage-taken（BBR3 taken→$4002） | $4002 | 4 | TryPeek失敗 | 5→5 | 0→0 | 63→63 | 3/3 | fetch_blocked（fail-closed、step発行なし） |
| pos-write（陽性対照） | $3E05 | 3 | — | 0→0 | 0→0 | 0→0 | — | allowlisted_ram_write_landed（$3E50 $00→$5A、非拒否で着地） |

全 armed 行で `mmio_read_attempts=0`、`ram_unchanged_during_resume=true`、`fetch_via=peek`（hwpage 2行は fail-closed の peek 失敗が正規形）、VDC read buffer `$FFFF` 不変。`detected_pc` は実測の拒否／停止PCと一致（期待値の転記ではない）。

## RED→GREEN（OBSERVED）

検査無効化の RED 負例（22 mutation run）は全て禁止効果を観測可能にし、armed 分類器が全 run を拒否（killed）。代表：

| 変異種 | 例 | 観測（RED） | 分類器 |
|---|---|---|---|
| -unguarded（監視off） | st1-unknown-unguarded | `ok`、R 0→$5A 着地 | `side_effect_landed_monitor_disabled` |
| -no-entry-check（先頭検査外し） | st0-entry-no-entry-check | `ok`、AR 0→$07 着地 | 同上 |
| -no-recheck（TAM後再検査なし） | st2-mpr2-unknown-no-recheck | `ok`、R 0→$A500 着地 | 同上 |
| -wrong-opcode（ST2欠落の単一リスト実証） | st2-mpr2-unknown-wrong-opcode | `ok`、R 着地 | 同上 |
| -knownpc-only（既知PC限定の回帰） | st0-jsr-rts-knownpc-only 他7行 | `ok`、非登録PC（$3E20/$1FFF/$0000/$6010/$3F83/$3F80ほか）で着地 | 同上 |
| -resume-allowed（ラッチ解除） | st1-branch-unknown-resume-allowed | `resume_refused=0`、R 着地、実行継続 | 同上 |
| -unsafe-read（TryPeek→Read置換） | hwpage/BBR3-taken 各1行 | hazard 着地、SR $3F→$00（$4000）／unsafe fetch自体（$4002）、`mmio_read_attempts=1` | `unsafe_fetch_side_effect_landed_mutation_killed` |
| -peek-fail-step（失敗時もstep） | hwpage/BBR3-taken 各1行 | `fetch_open_stepped`、実行継続 | 同上 |
| -no-hazard-latch（ラッチ除去） | hwpage/BBR3-taken 各1行 | `mmio_read_attempts=4`（初回+resume 3回の反復Readを機械検出） | 同上 |

GREEN 条件：armed 17 が上表の refused／fail-closed で停止し、VDC 不変・resume 拒否・RAM 不変を全て満たすこと。`make st-boundary` は EXIT 0（`armed: 17 modes refused/failed-closed before side effect, resume refused 3/3`、`mutation: ... rejected every mutated run`、`positive control: ... landed`）。

## CPU/VDC 状態比較（OBSERVED）

- armed 拒否時：`before.pc`→`after.pc == stop_pc == refused_pc`（hwpage系は `detected_pc == stop_pc`）、A/X/Y/P/SP/MPR はゲスト prologue の測定値どおり（MPR 遷移は `st2-mpr2/hwpage/bbr3/unused-bank` の4形状のみ、それ以外は完全安定）。VDC AR/R/SR・read buffer は上表のとおり不変（hwpage系 SR=63 維持は fail-closed の整合性アンカー。hazard の証明は hazard flag・実行数・page内停止・反復Read数が担う）。
- 変異着地時：ST0 は AR 0→$07、ST1 は R 0→$5A、ST2 は R 0→$A500 のいずれかが着地（上記 RED 表）。`$4000` unsafe-read 変異のみ SR $3F→$00 の device hazard 署名。`$4002`（data port）Read は SR を触らない（$3F→$3F）が hazard 判定＋実行数＋page内停止で hazard と判定し、SR 不変による偽PASSはしない。
- pos-write：LDA/STA/BRA ゲストが `$3E05` で停止、`ram_diff=[{address:15952, after:90, before:0}]` 単一・`ram_diff_count=1`・`truncated=false`、VDC 全不変、`refused/fetch_blocked/hazard=false`、`mmio_read_attempts=0`。

## 偽PASSなし（mutation / falsification、OBSERVED）

- live probe 内蔵の 22 変異 run は全 killed（上記 RED 表。偽PASSがあれば probe が非0終了する設計）。
- 追加の host 側改ざん検査（本worktreeで live 行に対して実施）：armed 行の `refused_opcode` 改ざん（st0/st1/st2 各1）・VDC 改ざん（AR 0→7）・unguarded 行の refused 偽装はいずれも `ValueError` で拒否（kill 確認）。`-resume-allowed` 行は `resume_refused=0`・`ok`・R 着地でラッチの load-bearing を実証。
- host 記録テスト：`python3 -m unittest tests.host.test_365_st_boundary_record` → 141 tests OK（fixture 40行 = armed 17 + mutation 22 + pos-write 1 の再判定＋改ざん拒否を含む）。

## host / real-core / CI（OBSERVED と INFERRED の区別）

- host（OBSERVED）：`test_365_st_boundary_record` 141 tests OK。本worktreeでの全 host スイート `python3 -m unittest discover -s tests/host` → 5613 tests OK（skipped=3、expected failures=3）。
- real-core（OBSERVED）：`tests/integration/geargrafx_st_boundary.py` EXIT 0、EVIDENCE 40行・JSR_OBSERVATION 40行（hazard 6行は `JSR_OBSERVATION_EXCLUDED` が正本）。`observation_kind: REAL_CORE_EXECUTION`、`bios: SYNTHETIC`、core `b49ae82a…`。商用・参照BIOS由来の観測なし。
- CI（INFERRED／UNKNOWN）：`tools/contracts.py` PASS（81 candidate contracts consistent; BIOS verification NOT_RUN）。required CI（`make test` / `make test-real-core` / `make check` 全系）および独立レビューは本 Draft PR では未完了（UNKNOWN）。Owner 承認までマージ禁止。

## 機械可読証拠（本記録の要約行、EVIDENCE 互換）

```json
{"bios": "SYNTHETIC", "case": "365-arbitrary-pc-reverify-04922be2", "core_revision": "b49ae82a012eade566d72e9d61c9d95a5caa4da3", "execution_mode": "debug-single-step", "issue": 365, "observation_kind": "REAL_CORE_EXECUTION", "result": {"armed_refused_or_fail_closed": 17, "host_record_tests_ok": 141, "host_full_tests_ok": 5613, "mutations_killed": 22, "pos_write_landed": true, "probe_exit": 0, "resume_refused_3_of_3": 17}, "verdict": "PASS_IN_VERIFIABLE_SCOPE_ONLY__NOT_A_GENERAL_INTERCEPTOR"}
```

`OBSERVED`：上記の全測定値・終了コード・行数。`INFERRED`：分類器の verdict 割当（composite 条件による判定）。`UNKNOWN`：free-run ゲスト一般化、release ビルド挙動、libretro parity、純正 JSR 経路（#173/#366/#367 の対象。未実測のため UNKNOWN のまま）。

## 未解決の安全条件（BLOCKED 維持）

- 本 single-step synthetic monitor は自由実行ゲストに対する一般 VDC interceptor ではない（設計上の対象外。未実測）。
- IRQ handler entry（#366）、block transfer／device-originated write（#367／#459 の TransferStart push 経路を含む）は対象外。
- 任意の reference BIOS JSR は実行していない（0/81）。`spec/reference_jsr_gate.json` の slot $01 は `BLOCKED` のまま。
- `release_eligible=false`、genuine ABI VERIFIED 0/81、#173 OPEN／BLOCKED を変更しない。本記録の `PASS` は「検証可能な合成 single-step 範囲だけ」の意味であり、互換性・一般安全性の VERIFIED ではない。
- 検証用プローブ（`tests/integration/geargrafx_st_boundary.*`、pinned debugger 専用）は本番安全機構ではない。出荷 ROM・ゲート判定に組み込まない。
- 本 Draft PR は独立レビュー・最終HEAD CI・Owner 承認を待つ。承認までマージしない。
