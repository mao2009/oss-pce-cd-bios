# Issue #473: TransferStart/TransferEnd 本番安全機構の方式選定 — 補足（PR #701 後 S7/S8 統合）

Status: **design only**. 本番安全機構は実装しない。本番ガードは有効化しない。#173 / #367 / #459 は BLOCKED のまま。
`release_eligible=false`、ABI VERIFIED 0/81、DEV_CALLABLE 0、参照 JSR 許可 0/81 は変更しない。
本プロジェクトは System Card BIOS と互換ではない。

## 1. 目的・重複回避

本書は PR #701（`docs/473-production-safety-mechanism-design.md`）の再実装ではない。PR #701 は
A/B/C/D/E/T の表、push/pop 制御フロー、per-method 停止後期待、no-resume 則を統合済みであり、本書では
繰り返さない。

本書の新規部分は次だけである。

- PR #701 が未参照の #653（S7）・#654（S8）の統合。
- PR #701 以後の HEAD 差分（#712 #365 entry-PC、#709 API-entry observer）の「先例」としての位置付け。
  いずれも TransferStart/End の新たな実測ではなく、選定結論は変えない。
- 本 Issue（#473 選定要求）の A1–A8 / B1–B8 / C1–C8 への個別回答と、要求された比較表・推奨・最小実装計画。

対象 HEAD: `0990b776`（PR #712 統合後）。Pinned Geargrafx: `b49ae82a012eade566d72e9d61c9d95a5caa4da3`。
証拠は3区分に分ける：#497 host-contract（実機コアなし）、#498 無改造コア、#499/#645 以降は verification-only patch 付き synthetic real-core。
#647/#648/#653/#654 の結果は当該文書が「CI `verify` 通過までは expectations」と記す範囲の記録であり、本書は新規実行による確定ではない。
Geargrafx 観測は実機の証明ではない（実機 #655 UNKNOWN）。

## 2. 制御フローの再確認（HEAD に対し整合）

`docs/473-production-safety-mechanism-design.md` §2 と差分は無い。要点のみ：

- `OPCodes_TransferStart`: `m_transfer_state = 2` を設定してから `StackPush8` × 3（Y/A/X）。
  したがって push 前拒否でも state は既に 2 である（#499 `tstart` BLOCKED）。
- `OPCodes_TransferEnd`: `StackPop8` × 3（X/A/Y）→ PC+6。state リセットの位置は**Pinned ソースとの照合が未完の既知の相違**として残す：
  PR #701 §2 の記録（pop 後に reset）と #499 driver のコメント（pop 前に state 0、`tend` は refusal 時に state 0）は食い違う。
  本書では順序を断定せず XML としては「state 遷移を含む TransferEnd 命令は本体書込み済み（#499 `tend` BLOCKED、#470 O4: 宛先 `$5A` 着地）」とだけ使う。順序の解決は本番設計の前提条件（§7）である。
- 4 関数 `StackPush8/16` / `StackPop8/16` は `STACK_ADDR 0x2100 | S` を MPR1 で解決する。
  順序は push: 書込み → S--、pop: **S++ → 読出し**（読出し前に S が動く）。MPR1=`$FF` のとき push は書込み、pop は読出しの副作用を生む。
- `RunInstruction` は fetch → `CheckIRQs()` → opcode → 状態 0 のとき `HandleIRQ()` の順。
  TAM で MPR1 を `$FF` にして同一命令で IRQ を受理すると、入口 push が `$21xx` に到達する
  （#498 `n_irq_tam` UNSAFE）。これが方式 A 単独の穴である。

`src/` に本番ガードは存在しない（`TransferStart/TransferEnd/StackPush/StackPop/gg_verify` は 0 件）。
フックはすべて `tests/integration/` の検証専用パッチ・probe にある。GPL 境界は
`docs/173-safety-monitor-limits.md` の方針（GPL ソースを MIT リポジトリへ持ち込まない）どおりである。

## 3. 方式 A — MPR1 への危険バンク割当を禁止

許可表は #497：MPR1=`$F8`（WRAM）のみ PASS。その他は FAIL（fail-closed）。リセット直後の MPR1 は
UNDETERMINED（信用しない。命令 0 の前に `DIRECT_SETMPR $F8` を記録しなければ BLOCKED）。

| 項目 | 回答 | 根拠 |
|---|---|---|
| A1 TransferStart 前に MPR1 実値を検査できるか | PARTIAL。命令実行前フック（#645 `gg_verify_pre_instruction`）で JSR/PHA/5 転送 SAFE_IN_SCOPE として実測。ただし検証専用パッチであり、本番配線は無い | docs/498 追記 |
| A2 安全な RAM の許可条件 | `$F8` のみ。`$F7` は NOT_ASSERTED の fail-closed。CD-RAM（`$80`）/ BRAM（`$F7`）は #645 で `cdram_bytes_changed` / `bram_bytes_changed` として測定済み。ROM バンクの stack 用途は #497 で FAIL（許可表に無い） | docs/497 §1 / #645 |
| A3 動的 TAM | 不十分。TAM と同一 `RunInstruction` 内の pending IRQ push は境界検査より先に起きる。A 単独 UNSAFE | #498 / docs/647 |
| A4 IRQ 処理 MPR1 書換 | ハンドラ内の TAM は次命令境界で検出可能だが、同命令 IRQ の穴は残る。転送中の IRQ 受理は起きない（#653） | #653 |
| A5 MPR alias | 不可。MPR1 以外（例 MPR0→I/O ページのコード fetch ホール）が A の範囲外。buffer は #654 で D のみ保持が実測 | #647 TMA negative / #654 |
| A6 MPR1 以外の I/O 経路 | 不可。ST0/ST1/ST2 の `WriteFromCPU` 迂回は #173 BLOCKED。デバイス DMA は #367 W17 未実測 | docs/173 / docs/367 |
| A7 誤拒否・許可漏れ | 過剰拒否は推定（`gg_verify_pre_instruction` の配置上、非 stack 命令を実測したものではない: NOT_RUN)。許可漏れあり：MPR1=`$F8` でも src/dst が I/O の転送は A では防げない（T の管轄） | #645 / #648 |
| A8 強制可能な検査位置 | probe 側の命令前フックのみ。停止機構を持たない free-run での成立は未測定（§7） | #653 前提 |

結論：**A 単独は UNSAFE**（#498）。減らせるものは stack 書込みだけであり、pop 読出し副作用を防げない。

## 4. 方式 B — push/pop 全体を保護

S0 の結論どおり B は単独候補から外す（pop 抑止は命令意味を変更する）。

| 項目 | 回答 | 根拠 |
|---|---|---|
| B1 TransferStart push 前停止 | 書込み停止は可能（#459: AR/R[7] 不変）。ただし state は既に 2。 | docs/367 §#459 |
| B2 TransferEnd pop 前停止 | 部分的。pop フックは C の部分集合。#499 では pop の副作用停止は確認されたが状態は BLOCKED。 | #499 |
| B3 push 拒否後の pop 継続防止 | **不可**。未書込みスロットから `$2102/$2103/$2104` を読む（A/X/Y `$12/$34/$07`→`$BE/$EF/$05`、VDC status `$05`→`$00`）。これが本 Issue の核心問題。 | #470 O2/O3 |
| B4 ロールバック | 不可。部分書込みは巻き戻されない。 | #648 |
| B5 SP/レジスタ保存復元 | 不可。S は `$04`→`$04` で均衡する偶然のみ。A/X/Y は破壊される。 | #470 |
| B6 デバイス読取り副作用防止 | 不可。status 読出しでクリアされる。 | #470 |
| B7 IRQ との関係 | 転送中（state≠0）は受理されない。TransferEnd 後に入る。 | #653 |
| B8 Geargrafx 改修範囲 | B 単独ではフックのみで足りるが不十分。D（decode）+ T（pre-start）の追加配線は本書（開発側提案）の合成であり、S0 の承認内容ではない。 | #647 / #648 |

結論：**B は単独不成立**。push の書込みを拒否できても、pop/read の副作用が残る。

## 5. 方式 C — 命令全体を副作用前に中止

| 項目 | 回答 | 根拠 |
|---|---|---|
| C1 decode 後・最初の副作用前の中止 | 全命令ではない。TransferStart は state が先行し、TransferEnd は本体書込み済み。PHA/PHP 系のみ状態保持。 | #499 `tstart`/`tend` |
| C2 途中命令の巻戻し | 不可。 | #648 / #499 |
| C3 PC/S/P/A/X/Y/MPR 保証 | なし。停止状態は BLOCKED（#499 の内状態 axes / #500 の八基準比較 `geargrafx_stack_methods_compare.py`）。SAFE 成功への昇格はしない。 | #499 / #500 |
| C4 VDC/IRQ/MMIO 保証 | バイト副作用は 19 経路で停止確認。#499 は全 I/O ページ接続を毎回記録し、#645 は值変化に限らず I/O ページ接続を記録する。残る穴は非 CPU 起点（DMA・ST）であり、C4 自体は CPU 経路に限定する。 | #499 / #645 |
| C5 書込みの原子性 | なし。5 命令は 1 命令内複数書込み。 | docs/367 |
| C6 step / free-run 両立 | debugger ハーネスのみ実測（#499 は RunToVBlank・no stepping・post-instruction stop、#712 は step 計数）。命令境界の停止機構を持たない真の free-run と libretro（単一命令停止 API なし）への適用は UNKNOWN。 | docs/367 / docs/653 / docs/366 |
| C7 デバイス起点書込み | 不可（CPU フックのみ）。 | #367 W17 |
| C8 本番組込み範囲・保守 | 4 関数のフック + state 処理 + kill 意味論。検証パッチは配布禁止。本番配線は未決定。 | docs/173 |

**#712（ST entry-PC）の先例**：ST0/ST1/ST2 を guest entry PC で 0 実行拒否し、mutation
（no-entry-check）で RED を確認した「命令境界で 0 実行拒否」のパターンは、D/T pre-start の設計先例として
強い。ただし ST 経路自体は #173 で阻止不能（BLOCKED）であり、transfer 安全の証拠ではない。

結論：C はバイト副作用停止としては SUPPORTED だが、停止後状態は BLOCKED。再開禁止の kill 限定でしか
使えない。

## 6. 安全性比較表

SUPPORTED = 副作用/状態の両方で実測。PARTIAL = 一部経路のみ。UNKNOWN = 未測定。
BLOCKED = 安全停止を保証できない実測/設計上の否定。

| 評価項目 | A | B | C |
|---|---|---|---|
| push 前拒否 | PARTIAL（pre-instruction 実測。IRQ-in-TAM 穴あり） | PARTIAL（書込み停止可。state 先行） | SUPPORTED（19 経路で書込み停止） |
| pop 前拒否 | BLOCKED（O3 読出し） | BLOCKED（pop 継続が穴） | SUPPORTED（読出し停止。状態は BLOCKED） |
| CPU 状態維持 | PARTIAL（`ff_no_irq` は不変。TAM 内は破壊） | BLOCKED | BLOCKED（PC/S/P/MPR 等が差異） |
| VDC 状態維持 | PARTIAL（観測範囲内のみ） | BLOCKED（status クリア） | PARTIAL（CPU 経路のみ。DMA・ST は BLOCKED 行） |
| MPR alias 対応 | BLOCKED（MPR0 ホール・buffer） | UNKNOWN | UNKNOWN（C は MPR1 stack 経路のみ。MPR0 fetch ホールには触れない） |
| 動的 MPR 対応 | BLOCKED（IRQ-in-TAM UNSAFE） | BLOCKED | PARTIAL（push は止まる。state dirty） |
| IRQ 対応 | BLOCKED（UNSAFE） | PARTIAL（転送中は受理せず） | PARTIAL（ベクタ分岐前の停止を #366 で確認） |
| デバイス副作用防止 | BLOCKED（DMA・ST は範囲外） | BLOCKED | BLOCKED（CPU フックのみ） |
| free-run での適用 | UNKNOWN | UNKNOWN | UNKNOWN |
| 実装規模 | 小（probe のみ。本番配線は未知） | 小（だが無効） | 中–大（4 関数＋制御＋保守） |
| 実コア証拠 | 単独 UNSAFE が固定 | 不十分が実測 | 副作用停止は実測。状態は BLOCKED |
| 残存リスク | TAM/IRQ 競合、非 MPR1 経路、DMA | pop/read 副作用の全経路 | 停止後状態の安全再開不可 |

ハイブリッド参照（PR #701 継承）: A+D（decode-time TAM 拒否）は検証範囲内で SAFE_IN_SCOPE
（停止は TAM 前で PC/MPR/buffer 不変、#647/#654）。A+E（IRQ 遅延）も SAFE_IN_SCOPE だが停止は TAM 後で
MPR1=`$FF` が可視、+1 命令遅延、非 peekable の starvation 未測定。T（pre-start）は TRANSFER 開始前拒否が
SAFE_IN_SCOPEになり得るが転送途中は BLOCKED（#648）。

**#653 の新規統合（PR #701 への差分）**：新規 assert IRQ の UNKNOWN は狭められた。TAM 内に clock point が
無いため「TAM 内 assert」は NOT_MEASURED のまま。一方、転送中の新規 assert は TransferEnd まで保留され、
割込 entry は起きない。よって T の「IRQ が転送中 MPR を変更する」経路は pinned core 上は
**発生しない**（mutant 9 で強制した時のみ T が BLOCKED として拒否）。PR #701 の
「IRQ は UNKNOWN」という記述は、本書により「pinned core 上は D/T の追加の穴は認められない（#653 を実証済み文書として受け取る範囲）」へ絞る。ただし #653 の「TAM 内 assert は NOT_MEASURED」の限定は残し、実機・デバイス IRQ 波形の結論は出さない。
ただし実機・デバイス IRQ 波形・TAM 内 assert は引き続き UNKNOWN。

**#654 の新規統合**：`TMA #$00` で読める `m_mpr_buffer` が追加の基準である。拒否された TAM で
buffer が開始値と等しいこと。A+D は `$02`→`$02`（TAM 前拒否）で保持。A / A+E は `$02`→`$FF` で
「安全だが禁止値が可視」。TAM #$00 本体は buffer 不変。multi-bit TMA は OR 値を保持する。
PR #701 の per-method 表には無かった基準であり、D の推奨優位（E に対する純度）を強める。

## 7. 推奨

**本番方式は選定保留。** Owner 承認なしに本番採用・ゲート解除をしない。

検証計画上の優先候補（製品推奨ではない）は **A+D+T ハイブリッド** である。

- A（MPR1=`$F8` 許可表 #497）を基礎とし、IRQ-in-TAM を D（decode-time TAM 拒否）で塞ぐ。
  D（E より推奨）理由: (1) 停止が TAM 前であり PC/MPR/buffer が clean（#654）である。
  (2) E は TAM 後の不可視化と +1 遅延と starvation 未測定を伴う。E は代替保持する。
- T（pre-start 転送拒否）で src/dst の全バイト範囲（TDD 減算、TIN 固定 dst、TIA/TAI 交互、len0=65536）と
  自 opcode 上書きを fetch 前に塞ぐ。すなわち TransferStart 前の拒否であり、転送途中拒否は採用しない。
- C は最終背止としてのみ用いる：フック停止後のゲスト再開を禁止する kill 意味論。
  状態復元・pop 抑止・巻戻しは採用しない（B′ 案は不採用）。
- A 単独・B 単独・C 単独を本番安全機構としない（各々 §3/§4/§5 の否定）。

保留理由：

- D/T の SAFE_IN_SCOPE は pinned 合成コア・debugger ハーネス・検証専用パッチの範囲に限る。
  peek 可否はコア固有であり、unpeekable は fail-closed（過剰拒否）である。
- libretro ビルド（#103）、実機（#655）、デバイス DMA（#367 W17）、ST 迂回（#173）は UNKNOWN/BLOCKED。
- 停止機構を持たない free-run への適用は未測定（#712 の entry-boundary 0 実行パターンは先例であり、transfer の証拠ではない）。
- 本番配線位置・保守・運用（S0 item 4 の C 停止点定義含む）が未決定。

## 8. 最小実装計画（将来・Owner 承認後の分割）

本 Issue では本番実装はしない。以下は独立 Issue への分割案である。各 Issue = 1 Draft PR。

1. 本番ガードの最小変更：A+D+T の配線位置（probe ではなく将来の本番コア/ハーネス側）。GPL 境界
   （docs/173）・検証パッチの配布禁止が前提。C は kill 限定。
2. Geargrafx 変更要否：D の peek（side-effect-free opcode/operand 読取り）と T の 6 operand 計算は
   コア固有。変更が要る場合は read-only accessor の方針（#648 の counters accessor）を踏襲する。
3. 合成 HuC6280 fixture：MPR1=`$FF` / src-dst crossing / self-overwrite / IRQ-in-TAM / buffer /
   assert-inside（clock point なしは NOT_MEASURED と明示）の positive/negative。
4. 正常系・異常系：MPR1=`$F8` WRAM 正例（#481 CASE_RAM / #470 stackwram）の回帰。deny/allow の両方向。
5. Mutation tests：D-off / wrong-bit / E-off（#647）、mut-tresume / mut-tnomid / mut-tfirst /
   mut-tseq / mut-tnoself（#648）、ST entry の no-entry-check（#712）の要領で RED を必須とする。
6. 状態検証：PC/S/P/A/X/Y/MPR0-7/VDC/WRAM/stack 画像 + buffer（#654）+ transfer counters。停止後再開禁止。
7. 5 命令回帰：TII/TIN/TDD/TIA/TAI × 長さ（1 / 0=65536 / crossing）× バンク。
8. cold/warm：#709 の observer（MPR1-gated `stack_check`、IRQ/JSR 区別）の要領で記録。raw stack bytes は出さない。
9. debug/release 差異：debug ビルドでのみ走る検証コードを release に持ち込まない。`release_eligible` 等を変更しない。
10. release gate 維持：#173/#367/#459 BLOCKED 維持。`make release` は BLOCKED のまま。
11. #367 から受け取る証拠：#367 multi-byte / other-bank / TIA-TAI order / device DMA 判定が揃うまで
    transfer の安全主張は検証範囲内止まりである。
12. #173 への統合条件：ST 迂回の完全遮断と generic 副作用前フックが揃うまで #173 は解除しない。

## 9. 検証と独立レビュー

- 本書の各主張は HEAD の `docs/473-*` / `docs/647-*` / `docs/648-*` / `docs/653-*` / `docs/654-*` /
  `docs/367-block-transfer-audit.md` / `docs/497-mpr1-stack-contract.md` / `docs/498-mpr1-tam-irq-measurement.md` /
  `docs/366-irq-postaccept-boundary.md` および integration driver（#499 `geargrafx_stack_access_guard.py`、
  #645 `geargrafx_stack_methods.py` / `geargrafx_stack_methods_compare.py`）と突合済み。`docs/499-*` / `docs/500-*` は存在しない。
  存在しない文書・撤去済み機構・古い結果は根拠にしていない。
- #612/#613 の 30/40 ケースは #367 の時点記録であり、現行件数ではない（merge 本書の誤読防止）。
- 独立レビューは本 PR で別エージェントに依頼し、重点（§8 重点項目）を確認のうえ CRITICAL/MAJOR/MEDIUM が
  0 になるまで修正する想定であり、レビュー結果は PR に記録する。
- 公開境界：私的 `./rom/` は使わない。商用 ROM/BIOS バイトは含まない。実メディア未検証は BLOCKED/SKIP と記す。

## 10. Owner Decision

選択肢：

1. **提案（推奨）**：A+D+T を検証計画上の候補として承認し、§8 の子 Issue 分割のみを承認する。
   本番実装は別途承認を要する。ゲートは BLOCKED のまま。
2. #473 を design-only で継続し（#701＋本書で終結）、#367 の進展を待つ。
3. 選定を保留（本書の状態のまま）。E 側の検討（starvation 測定）を先行させる。

MERGE_RECOMMENDED: **yes**（本文書 + 整合テストのみ。doc-only）
CLOSE_RECOMMENDED: **no**（案内のみ。#473 は開いたまま。ゲート解除・close は Owner 判断待ち）
