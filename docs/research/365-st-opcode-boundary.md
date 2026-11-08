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

---

## v2 (arbitrary-PC) 拡張と BBR3 分類器

作業ブランチ `mao2009/365-st-opcode-arbitrary-pc` における v2 スコープ。C++ プローブ v2 の
17 armed ファミリ（entry / unknown-PC / データ依存分岐 / JSR-RTS 復帰 / page・bank 境界 /
ROM page 0 / MPR 再マップ / 固定 JMP による動的 hardware-page / BBR3 データ依存分岐 /
未使用 bank / IRQ survey）と 9 変異種・22 run を
Python ドライバ `tests/integration/geargrafx_st_boundary.py` が実行・分類する。`make
test-real-core`（required CI）から `integration` 経由で実行される。

### BBR3 実測（HuC6280 BBR3、2 条件）

BBR3（opcode `$3F`）は bit がリセット（clear）時に分岐するデータ依存分岐である。PCE 構成の
CPU zero page 基点は `$2000`（WRAM page 1）なので、zp テストバイトは WRAM の CPU `$2010`。
ゲストは `LDA #$FF; TAM #$04; JMP $3F80; BBR3 $10,+$7F`（+ ST0 / BRA）で、BBR3 と ST0 の
ゲストバイトは 2 条件で同一。ホストが WRAM の `$2010` だけを変える:

- **taken**（bit clear、`$2010=0x00`）: BBR3 が `$4002`（hardware page = VDC data port）へ
  着地し、TryPeek が失敗して fail-closed する。停止 PC の実測は `$4002`。
- **skip**（bit set、`$2010=0x08`）: BBR3 は fall through し `$3F83` の ST0 が
  instruction boundary で拒否される。

分類器は停止 PC の固定値完全一致を成功条件にしない（hardware page メンバーシップ +
MPR マッピング整合性のみ）。BBR3 の taken 変異（unsafe-read / peek-fail-step）は `$4002`
での unsafe fetch として hazard 判定される。`$4002/SR` の設備実測差（事実）: `$4000` の
CPU Read は SR を clear するが（`0x3F→0x00`）、`$4002`（data port）の Read は SR を
触らない（`0x3F→0x3F`、read buffer も不変）。このため SR sentinel は `$4000` 変異の実測
署名としてのみ要求され、`$4002` 変異は probe 判定 hazard + 実行数 + page 内停止で判定する
（理由は実測として記録し、SR 不変による偽 PASS はしない）。

### 測定 PC と検出 PC

`detected_pc` は拒否判定が発生した PC の**実測値**（ST 拒否 → `refused_pc`、fail-closed /
hazard → 停止点）。`expected_st_pc` は独立した期待値として別フィールドにある。「停止 PC で
止まっただけ」は fail-closed の PASS にならない（TryPeek 失敗 + step 発行前 + VDC 不変 +
MPR 整合の中でしか PASS しない）。`$4000` / `$4002` は実測証拠として記録され、固定期待値
として扱われない。

### no-resume の実証

初回実行と再開要求は同一の guarded execution entry（probe の `guarded_execute`）を通る。3 回の
再開試行はコア実行処理を呼ばず、実行命令数・PC・VDC AR/R/SR・RAM 状態が全て不変である
ことを分類器が検証する（`ram_unchanged_during_resume`）。`-resume-allowed` 変異は latch が
保持されず ST が step・着地すること（resume_refused=0、executed 増加、PC 前進）を実証した
もので、この変異で no-resume guard が load-bearing である。

### IRQ survey の stack 副作用

IRQ2 が命令境界で受け入れられると、handler 先頭（`$3F80`）の ST0 が拒否される。SP の
`$FF→$FC`（3 バイトの architectural push）に加え、IRQ vector `$FFF6` の内容（handler
`$3F80`）を TryPeek で実際に読取検証し、stack frame の復帰 PC `$3E01`（`[0x3E, 0x01]`）も
検証する。`irq2-core-stop-survey` のみ core debugger の `stop_on_irq` も発火する。

WRAM 差分は**最終状態との net diff**（書込みイベントの監視ではない）。書戻した一時書込みや
同値書込みは detect されず、列挙は最大 256 件で打ち切られる（257 件目以上の差分がある場合
のみ `ram_diff_truncated=true` で明示。255/256 件ちょうどは false）。`only_stack_net_diffs` は
stack セル `$21FD-$21FF` 内の net diff のみを意味し、IRQ verdict は truncated を拒否する。
#173/#366 の generic RAM 安全性を確定する主張ではない。

### JSR 観測への hazard 除外

`tools/jsr_observation.py` v1 には hazard の outcome がないため、hazard 行は JSR view から
除外する（`JSR_OBSERVATION_EXCLUDED`）。`ok`（成功）や `rejected`（事前遮断成功）への
付け替えは hazard を安全な成功と誤認されるため行わない。hazard の正本は EVIDENCE である。

### hazard ラッチ（H3 強化）

unsafe Read による hazard が着地した時点で停止は latch され、resume 試行はデバイスに
触れずに拒否される（追加の `Memory::Read(PC)` は発生しない）。実測では unsafe-read 行の
`mmio_read_attempts`（unsafe bank への Read 判定回数）は正確に 1、latch 除去変異
（`-no-hazard-latch`、unsafe fetch 経路 + ラッチなし）では 4（初回 + resume 3 回の
反復）。armed 行は全て 0。SR 不変だけで hazard 反復の停止を推定していない。

VDC read buffer も armed 実行で verified する（リセット値 `0xFFFF` の landmark。変化 /
改ざんされた before/after / 欠落 / 型不一致は全て拒否）。

### mutation 結果

9 変異種・22 run: 全件で禁止効果が観測可能となり、armed 分類器が全 run を拒否（killed）。
- `-unsafe-read` / `-peek-fail-step` / `-no-hazard-latch`: unsafe fetch の危険を
  hazard latch の有無で区別する。
- `-unguarded`（監視 off）: ST が実行され VDC へ着地。
- `-no-entry-check` / `-no-recheck`（MPR2 再検査のみ）/ `-wrong-opcode`（ST2 欠落のみの
  単一 opcode リスト実証）: armed のまま ST / ST2 が実行・着地。
- `-knownpc-only`: 既知 PC 限定では未登録 PC（`$3E20` / `$3E03` / `$1FFF` / `$0000` /
  `$6010` / `$3F83` / `$3F80`）で ST が実行・着地。
- `-resume-allowed`: latch が保持されず ST が step・着地（resume_refused=0）。
- `-unsafe-read` / `-peek-fail-step` / `-no-hazard-latch`: `$4000` では SR `0x3F→0x00` の
  device hazard が、`$4002` では unsafe fetch 自体（hazard 判定 + 実行数 + page 内停止）が
  hazard として観測される。latch がない場合は resume 3 回の反復 Read（`mmio_read_attempts`=4）を
  機械的に検出する。安全分類器は全変異を FAIL として拒否。

### 実行結果（事実）

Linux x86_64 上の debug single-step で 17 armed 全 PASS・22 mutation 全 killed・pos-write PASS。
fixture `tests/fixtures/365-st-boundary-v2-evidence.json`（39 行 = armed 17 + mutation 22）。

### 実証範囲

debug single-step 実行のみ実測。release ビルドの挙動と free-run（自由実行）ゲストに対する
一般 VDC interceptor は本記録の範囲外（未実測）である。

### #173 に残る未保護経路

本 single-step synthetic monitor は自由実行の全命令列に対する VDC interceptor ではない。
IRQ handler entry（#366）、block transfer / device-originated write（#367）、任意の reference
BIOS JSR は対象外。`tools/reference_jsr_gate.py` と `BLOCKED_ABI` / `release_eligible=false` は
変更していない。#186/#187 の純正観測、#473/#727 の本番安全機構は未承認のままである。
