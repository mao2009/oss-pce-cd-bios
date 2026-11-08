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
15 armed ファミリ（entry / unknown-PC / データ依存分岐 / JSR-RTS 復帰 / page・bank 境界 /
ROM page 0 / MPR 再マップ / 動的 hardware-page / 未使用 bank / IRQ survey）と 8 変異種・17 run を
Python ドライバ `tests/integration/geargrafx_st_boundary.py` が実行・分類する。`make
test-real-core`（required CI）から `integration` 経由で実行される。

### BBR3 動的 MMIO 進入と固定 PC 判定を採用しない理由

BBR3 に代表されるデータ依存分岐は、分岐の成立が VDC read buffer 値に依存するため、実際に
着地する PC は実行のたびに（プレセットによって）変わりうる。分類器が「branch target の
完全一致」や「停止 PC `$4002` の完全一致」を成功条件とすると、正常な fail-closed 停止を
見逃し、偶然停止しただけのケースを PASS 誤判定しうる。さらに停止 PC の数値だけから安全性を
推定することは禁止する（hardware page 内で止まっただけでは PASS にしない）。

### 実測値（事実）

pinned Geargrafx `b49ae82a012eade566d72e9d61c9d95a5caa4da3`、合成 ROM、MPR0=$00。Linux
x86_64 上の debug single-step 実行で以下を確認（fixture
`tests/fixtures/365-st-boundary-v2-evidence.json`、32 行 = armed 15 + mutation 17）:

- 安全 run（st0-hwpage-fetch-blocked）: 停止 PC `$4000`、実行 3 命令（LDA+TAM+JMP）、
  `fetch_via=peek`・`fetch_failed=true`・`fetch_blocked=true`、SR `0x3F→0x3F`（sentinel プリセット
  に対する不変）。
- TryPeek→Memory::Read 変異（unsafe-read）: 停止 PC `$4000`、実行 3、SR `0x3F→0x00`。
  参照 PC での CPU Read 自体が SR（status clear）の副作用を先に着地させる。
- peek-fail-step 変異（fail-open）: 停止 PC `$4002`、実行 4（unsafe PC で 1 step 発行済み）、
  SR `0x3F→0x00`。

推論との分離: `$4000`（安全 run）と `$4002`（step 発行済み mutation の hazard run）の実測値を
もとに、分類器は「hardware page メンバーシップ + MPR マッピング整合性」のみを停止 PC 条件とし、
いずれの固定値も要求しない。実測値と期待値は fixture に分離記録され、host テストは独立した
不変条件（page 外停止の拒否、step 発行済みの拒否、SR hazard の拒否、SR 不変だけでの偽 PASS の
防止、MPR 不一致の拒否）で正負例を検証する。

### 分類規則

1. 実行命令数: ST 本体は実行されず（armed の期待値と一致）、hwpage では unsafe PC での
   step が発行されていない（実行 3 = prologue のみ）。予算超過・タイムアウト・プロセス異常・
   probe 出力不足は FAIL/BLOCKED であり PASS にしない。
2. VDC SR: 安全 run では `Memory::Read` 由来の SR 副作用が発生しない。SR 観測は probe の
   state 構造体への host 側読取（CPU fetch ではない）であり、観測自体は副作用を起こさない。
   AR/R/IRQ 状態も併せて比較する。
3. 停止 PC: hwpage 進入では hardware page（CPU page 2 / bank `$FF`）内であること + MPR2 整合性
   + TryPeek 失敗による停止 + Step 発行前停止。単に page 内で止まっただけでは FAIL。
4. 測定 PC: `$4000` / `$4002` は実測証拠として記録し、固定期待として扱わない。

TryPeek の差: 境界判定はコア内 disassembler も使う `Memory::TryPeek`（副作用なし）で行い、
hardware page / peek 不可能な bank では false（デバイスアクセスなし）を返す。`Memory::Read`
による fetch は hardware page PC で SR clear を先に着地させるため、peek 設計は load-bearing
である（変異 2 件で証明）。

### mutation 結果

8 変異種・17 run: 全件で禁止効果が観測可能となり、armed 分類器が全 run を拒否（killed）。
- `-unguarded`（監視 off）: ST が実行され VDC へ着地。
- `-no-entry-check` / `-no-recheck`（MPR2 再検査のみ）/ `-wrong-opcode`（ST2 欠落のみの
  単一 opcode リスト実証）: armed のまま ST / ST2 が実行・着地。
- `-knownpc-only`: 既知 PC 限定では未登録 PC（`$3E20` / `$3E03` / `$1FFF` / `$0000` /
  `$6010` / `$3F80`）で ST が実行・着地。
- `-resume-allowed`: latch が保持されず ST が step・着地（executed 4、PC `$3E12`）。
- `-unsafe-read` / `-peek-fail-step`: SR `0x3F→0x00` の hazard を観測。安全分類器は両方を
  FAIL として拒否。

### IRQ survey の stack 副作用

IRQ2 が命令境界で受け入れられると、handler 先頭（`$3F80`）の ST0 が拒否される。観測した
副作用: SP `$FF→$FC`（3 バイトの architectural push）、WRAM 差分は stack 書込みのみ
（`only_stack_writes=true`）、IRQ2 vector `$FFF6`。`irq2-core-stop-survey` のみ core debugger の
`stop_on_irq` も発火する。

### 実証範囲

debug single-step 実行のみ実測。release ビルドの挙動と free-run（自由実行）ゲストに対する
一般 VDC interceptor は本記録の範囲外（未実測）である。

### #173 に残る未保護経路

本 single-step synthetic monitor は自由実行の全命令列に対する VDC interceptor ではない。
IRQ handler entry（#366）、block transfer / device-originated write（#367）、任意の reference
BIOS JSR は対象外。`tools/reference_jsr_gate.py` と `BLOCKED_ABI` / `release_eligible=false` は
変更していない。#186/#187 の純正観測、#473/#727 の本番安全機構は未承認のままである。
