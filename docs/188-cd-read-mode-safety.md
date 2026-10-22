# Issue #188: CD_READ 転送先モード 2 の安全性評価と非実行フィクスチャ

親: #11 / #12。前提: #389（`docs/cd-read-mode-contract.md`、`tools/cd_read_contract.py`）。

## 判定

**BLOCKED。** 純正 BIOS の `$E009` (slot `$03`) は実行していない。安全条件（JSR gate、#173 の副作用前遮断、callee ABI の裏付け）は現時点で成立しないため、実観測は行わない。本 PR は非実行フィクスチャと判定テストのみを追加する。

## 1. 実コール前に成立していなければならない条件

| ID | 条件 | 現状 | 根拠 |
| --- | --- | --- | --- |
| G1 | JSR gate: slot 3 の reviewed 記録と独立承認があり、execution が `ALLOWED` | **不成立**。slot 3 の記録が存在せず `NO_REVIEWED_GATE_RECORD` と `NO_INDEPENDENT_APPROVAL` で BLOCKED。runner は `NONE`、`jsr_count` は 0 | `spec/reference_jsr_gate.json` は slot 1 のみ。`tools/reference_jsr_gate.py` の `resolve()` |
| G2 | #173: 書込み・MMIO 副作用を発生前に遮断できる | **不成立**。#173、#365（命令境界）、#366（IRQ 入口）、#367（ブロック転送・デバイス起点書込み）は全て OPEN | `docs/173-safety-monitor-limits.md`: ST0/ST1/ST2 (`$03`/`$13`/`$23`) は阻止不能、`$1802`/`$1804`/`$1402`/`$1403` は事前阻止不能 |
| G3 | callee ABI のうち実行に必要な項目（R1-R3 の入口とバンク、R4 宛先、R5 基底、R9 MPR、R10 スタック、R12 IRQ 前提） | **未確定**。R4、R5、R7、R9、R10、R11、R12 は UNKNOWN | `tools/cd_read_contract.py` `RAM_MODE_ITEMS`。`spec/api_contracts.json` slot 3 は `verification.executed_callee: false`、`NOT_RUN` |
| G4 | 媒体状態: 無媒体・範囲外・0 count の戻り | **未確定**。T7（無媒体）、T8（count 0、4 以上）は UNKNOWN | `tools/cd_read_contract.py` `TRANSPORT_CLAIMS` |
| G5 | 転送先バンク `_bl` の MPR 変化を戻せる | **未確定**。呼出側が防御するのは MPR3/MPR4 のみ。R9 は UNKNOWN | `CALLER_LEDGER` C19-C27（呼出側の防御であり callee の証拠ではない） |
| G6 | 純正 BIOS の入力 | 本作業では読まず、実行しない。自己所有・非配布・git 非収録 | Issue #188 の制約 |

判定: G1、G2 が成立しないため、G3 から G6 を検討する前に実観測は禁止される。

## 2. 非実行フィクスチャ

`tests/fixtures/cd-read-mode2-single-sector.json`。1 セクター、`_dh = 2`、count 1 を中心とする。
注記（2026-10-22 オーナー判断）: 優先観測候補は `_dh = 3`（[188-cd-read-transfer-mode-contract.md](188-cd-read-transfer-mode-contract.md) の D1）。本節の `_dh = 2` フィクスチャは既存の記録として残す。

- 期待値テンプレート（`expected_state_template`）: A、N/Z/C/T、MPR、宛先アドレス、宛先バイト、スタック、X/Y/`$20F8-$20FF`、CLI 前提、媒体状態。全て `UNKNOWN`。
- ケース表は `RAM_MODE_EXAMPLES` の ID（P1-P3、N1-N4）と一致する。転送層の期待値（送達セクター数、早期 STATUS）は READ(6) の実コア計測（T1-T4）に由来し、callee の値ではない。callee 期待値は全て `UNKNOWN`。
- count 境界: 0、2、3、4、256 は UNKNOWN。count 1 のみ IN_SCOPE。

| ID | 極性 | LBA | count | 転送層の期待値 | callee の期待値 |
| --- | --- | --- | --- | --- | --- |
| P1 | positive | 5 | 1 | 計測済み (1, False) | UNKNOWN |
| P2 | positive | 0 | 1 | 計測済み (1, False) | UNKNOWN |
| P3 | positive | 15 | 1 | 計測済み (1, False) | UNKNOWN |
| N1 | negative | 16 | 1 | 計測済み (0, True) | UNKNOWN |
| N2 | negative | 2097151 | 1 | 計測済み (0, True) | UNKNOWN |
| N3 | negative | 5 | 0 | UNKNOWN | UNKNOWN |
| N4 | negative | 5 | 4 | UNKNOWN | UNKNOWN |

## 3. 実施していないこと

- 純正 BIOS の実行: 0 件（`CALLEE_REAL_CORE_RUNS = 0`）。
- 新規の Geargrafx 実コア実行: 0 件。転送層の値は #389 までの既存計測を参照するのみ。
- 実機、第二エミュレータ、媒体なし・範囲外の観測: なし。

## 4. #188 と #189 の阻害要因

- #188 の実観測を阻む要因: G1（slot 3 の gate 記録と承認が無い）、G2（#173 子 #365-#367 が OPEN）、G3-G5 の callee 項目が UNKNOWN。
- #189 は #188 の完了と安全な参照実行境界に依存する。無媒体・範囲外の戻りは T7/T8 と callee 項目が未確定のため、現時点で観測できない。

解除条件: #365、#366、#367 の独立レビュー付きクローズ。slot 3 の reviewed gate 記録と独立承認。R4、R5、R9 の出典付き確定。
