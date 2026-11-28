# G5 P0 表示次機能選定 (Agent C, #802): $25/$24/$36/$35 再調査 — 全件BLOCKED (survey only)

Status: **調査文書 (survey only)。機能実装なし。B1 置換なし。
NOT a compatible System Card BIOS. NOT genuine ABI. NOT DEV_CALLABLE.
`release_eligible=false`. G5_OBSERVED=false.**
Reference JSR 0/81, genuine ABI VERIFIED 0/81（本書変更なし）。
No System Card image was executed, disassembled, or copied.
No game-disc bytes, titles, paths, hashes, sector contents, audio, or saves appear here.
`./rom/` was not accessed in any form for this document
(no existence check, listing, or read).

Branch basis: `b743a8c2` (origin/main at task start).
Task: #802 親 #635。`$25`(Issue #47)・`$24`(#46)・`$36`(#64)・`$35`(#63) の
4 API を最新 main 上で再検証し、次の一機能実装対象を1件選定する。
先行選定文書 docs/g5-display-functional-selection.md（`$2C` D2-機能 SELECTED）は
変更しない（consistent 維持）。本書は台帳・ソース・ゲート・ROM バイトを変更しない。

## 1. 結論（先に）

- **判定: 全件 BLOCKED。次機能実装対象の選定なし。**
  `$25`・`$24`・`$36`・`$35` のいずれも D1（真正機能契約の実装）および
  D2-機能（限定機能 experimental）の受入条件を満たさない。
  既存 B1 preservation scaffold 4 件（PR #828/#829/#830/#834）を維持し、
  本タスクで置換・再定義しない。
- `$2C` D2-機能（PR #840 MERGED）および `$2B` D2-機能（PR #841 MERGED）は
  本書の選定対象外（選定範囲は表示 4 API のみ）。両者の実装済み状態は
  §2 で再確認した既定事実として扱う。
- 子 Issue は作らない。既存 Issue #47/#46/#64/#63 が各 API の完了条件を
  保持しており、本書は各 API の unblock 条件と受入手順スケッチを
  既存 Issue に紐付けて記録する（§6）。

## 2. 最新 main 上の再検証（既定事実の確認）

HEAD `b743a8c2` 時点の確認結果（code grep + gh + spec 読取り）：

| ID | 再確認事項 | 結果 |
|----|-----------|------|
| R1 | PR #839 (`$2D` pair contract + VDC snapshot) | MERGED（base main、2026-11-27） |
| R2 | PR #840 (`$2C` D2-機能) | MERGED（`f8acbfce`）。`src/ex_cr_family.s` NEED_CR2C head（29-byte）＋共有 tail/reject、`tools/build_experimental_bios_boot.py` で `cd_2c="experimental"` opt-in 配線。`tests/integration/test_g5_irqon_2c.py`＋`tests/host/test_build_g5_2c_host.py` が合成 GREEN |
| R3 | B1 scaffolds #828/#829/#830/#834 | 全 MERGED。`src/ex_scrmod.s`・`ex_dotmod.s`・`ex_satclr.s`・`ex_sprdma.s` はいずれも `STA` 0 件＝MMIO/RAM 書込みなしの preservation-only（B1 維持を確認） |
| R4 | 先行選定文書 | `docs/g5-display-functional-selection.md` 存在・本書は非改変。`$2C` 選定理由（§3-§4）と 4 API の D1/D2-機能 BLOCKED 判定は最新 main でも有効（§4 で再評価） |
| R5 | Issue #47/#46/#64/#63 | 全 OPEN。spec HYPOTHESIS・実装 NOT_STARTED のまま。新規証拠コメントなし（#46・#64 の comments count 1 は projectCards 自動付与のみ） |
| R6 | `spec/api_slots.json` | slot id 36=`$24` EX_DOTMOD / 37=`$25` EX_SCRMOD / 53=`$35` EX_SPRDMA / 54=`$36` EX_SATCLR（10進 id＝16進 `$24`/`$25`/`$35`/`$36` と一致） |
| R7 | `spec/api_contracts.json` | 6 API（`$24`/`$25`/`$2C`/`$2D`/`$35`/`$36`）の mapping_status は全 HYPOTHESIS（真正 ABI 未確定のまま） |
| R8 | `spec/dev_implementations.json` | 対象 6 slot 全 NOT_READY（experimental D2 は本台帳の状態語彙外。PR #840 も本台帳不変。VERIFIED 不変） |
| R9 | `spec/abi1_reference_review.json` + `reference_jsr_gate` | 全 slot で reference JSR 実行不可（gate が全拒否）。真正挙動の実測比較は不可のまま |
| R10 | 契約文書 47/46/64/63 | いずれも「genuine UNKNOWN・B1/SIMULATED のみ可」の verdict を維持。新規公開機能記述の追加なし（§3） |
| R11 | BOOT 予算・cross-API 配線 | `unified_api_windows_4a_25_24_36_35_2c_2b` まで full ExpG 配線が存在。本書は配線追加なし（survey のため予算消費 0） |

分類語彙（本書共通。先行文書 §4 および契約文書 §3 と同一定義）:
`SOURCE_CONFIRMED`＝pin 済み独立資料が明示＋矛盾なし、または pinned harness の project-own 実測 /
`REFERENCE_ONLY`＝外部資料の事実引用（実装根拠にしない） /
`HYPOTHESIS`＝単一資料の主張・名称一致のみ（研究指針、実装根拠にしない） /
`UNGROUNDED`＝根拠なし（禁止） /
`PROJECT_SPEC_ONLY`＝DEV 層の約定（真正契約の主張ではない）。
一致（agreement-alone）は真正証明にならない。

## 3. 評価軸別の再評価（4 API 共通事項と差分）

評価軸（タスク指定）：implemented vs missing side-effects / VDC・VRAM・SATB・mode 影響 /
title-blocking / public docs / independent-impl contracts / provenance review /
undetermined ABI / BOOT size / cross-API 影響 / feasibility / verifiability /
test gaps / deps。

### 3.1 共通事項（4 API 全体に適用）

- **Implemented（B1 で実装済み）**: entry-state preservation（A/X/Y/P latch-exact、
  T=1 域外、MPR 不変、SP balanced）、VDC・VRAM・RAM 無書込み、MPR1=`$F8` 事前検証
  （harness 側、body 内 gate なし）。合成 real-core GREEN（各 B1 test）。
  分類: 実装の存在は SOURCE_CONFIRMED（in-tree 実測）。ただし B1 は SIMULATED
  であり真正能力の証拠ではない（各 B1 文書で明示）。
- **Missing（全 API で未実装）**: 真正の入力値域・出力値・flags・対象 HW 操作・
  shadow/mirror 契約・timing・error。`api_contracts.json` mapping HYPOTHESIS、
  reference JSR 全拒否のため真正比較 oracle は存在しない。
- **Provenance**: Zeograd/Stum/HuC/Hugo は全て REFERENCE_ONLY/PENDING。
  ライセンス混在資料（旧 HuC 等）は実装根拠にしない。Pinned Geargrafx
  `b49ae82a`・cc65 `71746c8`（`tools/dependencies.json`）のみ pin 済み。
  HW 対応付け（pinned-core の HW 事実）は BIOS 呼出契約の証拠にならない（混同禁止）。
- **BOOT size**: 現状 full ExpG（`4a_25_24_36_35_2c_2b`）が BOOT 予算内に収まることは
  in-tree build で検証済み。本書は追加ゼロ。将来の機能 body は 29-byte head 規律
  （CR family 実績）または B1 規模（PHP/PHA/PHX/PHY…RTS）を目安に予算審査が必要。
- **Cross-API 影響**: 4 API は商用 CUE[0] 順序
  `$02→$33→$4A→$23→$25→$24→$36→$35→$2C→$2B` の上流に位置する。
  いずれかの機能実装が誤動作すれば下流観測（`$2C`/`$2B` ExpF/ExpG 比較）が汚染される。
  機能化は単独 API ごとに独立検証（RAM-diff・VDC-diff・chain cold/warm）してから
 統合する順序が必須。
- **Title-blocking**: 分類分離（cause determination なし）。
  SOURCE_CONFIRMED: B1 は entry-state preservation のみで表示初期化を
  何も実装しない（STA 0、MMIO/RAM 無書込みの in-tree 実測）。4 API の
  真正契約は未確定のまま（`api_contracts.json` 全 HYPOTHESIS、reference JSR
  全拒否）。表示 debt 4 件は残存する。UNKNOWN: 実際の title blocker
  （どの API が必須か、商用表示失敗の原因）は未確定のまま
  （G5_OBSERVED=false）。本書は原因特定・per-API necessity の確定・
  ABI 昇格を行わない。HYPOTHESIS（blocking 可能性。実装根拠にしない）:
  4 API の真正 debt および下流 API 群の UNKNOWN が title 未到達に
  関与し得る。機能実装の誤りは表示破壊（中リスク）〜VRAM/DMA 破壊
  （高リスク）の回帰を招き得るため、grounds なき実装は行わない
  （title に近づけない）。

### 3.2 API 別差分（missing 側の具体的内容）

| API | 公開機能記述（missing の核心） | 独立 HW 根拠の到達点 | deps / test gaps |
|-----|-------------------------------|---------------------|------------------|
| `$25` EX_SCRMOD (#47) | Zeograd bare heading（IN/OUT なし）。HuC `vce_cr`/`vdc_hdw`/`vdc_vdw`（`$2244-2246`）＋旧 HuC 同帯域は caller-side 割付コメントに留まり、値 encoding・VDC/VCE 対応・flags・timing を定義しない。コメントアウト呼出は無効コード | HW 位置は pinned だが `$25`→HW 対応付けなし（HYPOTHESIS 止まり） | B1 test GREEN（preservation＋VDC 不変）。機能 oracle なし。`$23` との関係は契約文書で分析済みだが値契約なし |
| `$24` EX_DOTMOD (#46) | Zeograd bare heading。VCE `$0400` clock 位置は pinned だが `$24`→VCE 対応付けは HYPOTHESIS | 同上 | B1 test GREEN。機能 oracle なし |
| `$36` EX_SATCLR (#64) | Zeograd bare heading（`$35` one-liner は `$36` 証拠にならない）。名称「SATCLR」を zero-fill と読むことは禁止。対象（WRAM SAT / VRAM SAT / SATB）の特定なし | SATB DMA 位置は pinned だが対象不明。zero-fill 推測は禁止 | B1 test GREEN。DMA 完了 oracle なし |
| `$35` EX_SPRDMA (#63) | Zeograd one-liner「`$2214/5` word → VDC #19」。方向・所有・値域・timing・完了条件・flags・shadow なし。`$34` one-liner（VDC #15＋`$2243`）は `$35` 証拠にならない | R13 DVSSR＝VRAM–SATB source の対応付けは可（REFERENCE_ONLY HW 事実）。書込み値・handshake 不明 | B1 test GREEN。DMA oracle なし。in-tree に VRAM SAT・SATB DMA・VCE sprite の DEV 先例なし（S5b） |

## 4. 4-API 比較表（implemented vs missing の分離）

凡例: ◎=機能実装を ground できる水準 / ○=部分的（hint のみ） / △=HW 位置等の事実のみ（BIOS 契約の根拠にならない） / ×=なし。
D1=真正機能契約の実装 / D2-機能=限定機能 experimental / D2-B1=B1 型 scaffold（機能なし、維持）。

| API | B1 実装 (implemented) | 公開記述の具体性 | 独立根拠 | 入力確度 | HW 操作確度 | shadow 確度 | 検証可能性 | 規模 | 回帰リスク | G5 診断価値 | 判定 |
|-----|----------------------|-----------------|---------|---------|------------|------------|-----------|-----|-----------|------------|------|
| `$25` (#47) | ✅ GREEN 維持（`ex_scrmod.s`、STA 0） | ×（bare。`$2244-2246` hint は値なし） | △（HW 位置のみ） | × | ×（VCE?/VDC? 不明） | × | ○（B1 のみ。機能 oracle なし） | 小（対象不明で不可） | 中（VDC 誤設定は表示破壊） | ○（B1 済み。debt 残存） | **D1 BLOCKED・D2-機能 BLOCKED**（B1 維持） |
| `$24` (#46) | ✅ GREEN 維持（`ex_dotmod.s`、STA 0） | ×（bare） | △（VCE 位置のみ） | × | × | × | ○（B1 のみ） | 小（対象不明で不可） | 中 | ○（B1 済み） | **D1 BLOCKED・D2-機能 BLOCKED**（B1 維持） |
| `$36` (#64) | ✅ GREEN 維持（`ex_satclr.s`、STA 0） | ×（bare。「zero-fill」推測禁止） | △（SATB 位置のみ） | × | ×（対象不明） | × | ○（B1 のみ） | 対象不明で不可 | 大（VRAM 破壊リスク） | ○（B1 済み） | **D1 BLOCKED・D2-機能 BLOCKED**（B1 維持） |
| `$35` (#63) | ✅ GREEN 維持（`ex_sprdma.s`、STA 0） | △（one-liner のみ。値域・timing なし） | △（R13 対応付けのみ） | ×（`$2214/5` 方向・所有不明） | △（#19 対応付け可。値・handshake 不明） | × | ○（B1 のみ。DMA oracle なし） | 中 | 大（DMA 誤動作リスク） | ○（B1 済み） | **D1 BLOCKED・D2-機能 BLOCKED**（B1 維持） |

### 選定なしの理由（`$2C` との対比）

`$2C` が D2-機能 SELECTED となった 5 点（IN N/A の公開記述＋単一 bit 操作＋
対称 pair＋no-setup 呼出慣行＋pinned bit 割付）はいずれの表示 4 API にも揃わない。
特に `$35` の one-liner は `$2C` の bit 記述と異なり、値・方向・完了のいずれも
定義しないため、DVSSR 書込みの正当化に足りない。単一 hint からの推測実装は
forbids（推測追加の禁止）に抵触するため行わない。

## 5. 判定の grounds 分類（API 別・行別）

| API | 判定 | 主 grounds | 分類 |
|-----|------|-----------|------|
| `$25` | D1 BLOCKED | 真正入力・VDC/VCE 対象・shadow・flags・timing の全欠落（契約文書 §3 全 UNKNOWN） | UNKNOWN（genuine） |
| `$25` | D2-機能 BLOCKED | HuC `$2244-2246` は caller-side 観測に留まり値契約なし。機能範囲を限定できない | REFERENCE_ONLY（caller 事実）＋ HYPOTHESIS（`$25`→HW 対応付け） |
| `$24` | D1 BLOCKED | 同上（契約文書 §3 全 UNKNOWN） | UNKNOWN（genuine） |
| `$24` | D2-機能 BLOCKED | VCE `$0400` 位置は HW 事実だが `$24` との対応付けは単一推測 | HYPOTHESIS |
| `$36` | D1 BLOCKED | 対象・値・timing 全 UNKNOWN。名称からの推測は禁止（契約文書 verdict） | UNKNOWN（genuine）＋ UNGROUNDED（名称推測は拒否） |
| `$36` | D2-機能 BLOCKED | 限定範囲（例：特定領域の zero-fill）を定義する公開根拠なし | UNKNOWN |
| `$35` | D1 BLOCKED | 方向・値域・flags・shadow・timing 全 UNKNOWN（契約文書 §3 verdict） | UNKNOWN（genuine） |
| `$35` | D2-機能 BLOCKED | one-liner は target hint のみ。R13 対応付けは HW 事実だが BIOS 契約ではない | REFERENCE_ONLY（HW＋one-liner） |
| 全 API | B1 維持 | B1 実装・test GREEN は in-tree 実測 | SOURCE_CONFIRMED（DEV 層の事実。真正能力の証拠ではない） |
| 全 API | mapping | slot/entry 対応は HuC 等の一致に留まる | HYPOTHESIS（agreement-alone、真正証明にならない） |

## 6. Unblock 条件と受入スケッチ（既存 Issue 紐付け・子 Issue 不要）

いずれも「真正契約の主張」ではなく「将来の実装 PR が満たすべき入口条件＋
受入テスト形状」の要求水準である。各 API の完了条件は既存 Issue 本文が保持する
ため子 Issue は作らない。

### `$25`（→ Issue #47）Unblock 条件

- 公開資料（Zeograd/Stum と独立承認資料の 2 系統以上）で入力レジスタ・値域・
  対象レジスタ（VCE/VDC）・出力・flags が一致して定義されること。一致のみでは
  不足（agreement-alone ≠ proof）のため、pinned-core HW 事実との対応付けも必要。

受入スケッチ（unblock 後の実装 PR 用。normal/abnormal/boundary/mutation）：

- normal: 合成 seed（値域内）→ 対象レジスタが期待値に変化、他 bit/VDC 保持、
  C=0（約定は PROJECT_SPEC_ONLY）。
- abnormal: 未初期化 shadow／域外 MPR1・SP → fail-closed（無書込み・C=1 または
  harness 拒否）。商用 WRAM は観測しない。
- boundary: 値域端（最小/最大/端+1 拒否）、T=1 域外、AR 残置規律。
- mutation: bit 反転・書込み先すり替え wrong-target（別レジスタ書込み mutant は RED）、
  成功値固定 mutant は RED（oracle は seed 由来）。

### `$24`（→ Issue #46）Unblock 条件

- `$24`→VCE/VDC 操作の公開 BIOS 契約リンク（値・対象・timing）が `$25` と
  同水準で揃うこと。HW 位置の存在は unblock にならない。

受入スケッチ: `$25` と同型（対象が VCE の場合は VCE-diff oracle を追加し、
VRAM/SATB/VCE palette の不変を assert）。

### `$36`（→ Issue #64）Unblock 条件

- 操作対象（WRAM SAT / VRAM SAT / SATB）の特定＋範囲・値（zero-fill か否か）・
  完了条件（sync/async、wait）の公開根拠。名称からの推測は unblock にならない。

受入スケッチ: `$25` と同型＋対象メモリ diff oracle（VRAM 触れる場合は
VRAM-diff・canary・cold/warm 一致）。DMA 完了 oracle が定義できない間は
D2-機能も BLOCKED のまま。

### `$35`（→ Issue #63）Unblock 条件

- `$2214/5` の方向・所有・値域＋VDC #19 書込み値・handshake・完了・flags・
  shadow の公開根拠。one-liner の再確認だけでは unblock にならない
  （既に S1 として記録済みのため）。

受入スケッチ: `$25` と同型＋DVSSR/VDC-diff oracle＋DMA 完了条件の assert。
in-tree に SAT/DMA 先例がないため、window 規律（MPR0=`$FF`＋SEI＋restore）の
静的監査も受入に含める。

## 7. 残存リスクと FOLLOW_UP

- 表示 debt 4 件は残存する（B1 のまま）。G5 タイトル到達には後続の真正実装が
  要り得る（HYPOTHESIS。実際の blocker・per-API necessity は UNKNOWN のため
  原因確定しない）が、本書時点では grounds がないため着手しない。
- `$2C`/`$2B` CR-family gate の解釈規律（条件付き期待。商用断定なし）。
  SOURCE_CONFIRMED（`src/ex_cr_family.s`）: gate は `$2503==$A5` のみを見て
  分岐する（`lda $2503 / cmp #$A5 / bne cr_reject`）。loader BUF
  `$2200–$29FF` は staging 転送で `$2502`/`$2503` を覆い、sector バイトが
  そのまま載る。よって (a) marker`!=$A5` → reject（条件付き期待） /
  (b) marker`==$A5` → ok-path に進み得る（game の seed なしでも sector 残留
  `$A5` で ok-path 可） / (c) (b)＋shadow desync → false-ok リスク
  （stale image publish）。(b)(c) の synthetic 再現は未 merge の SHA-pinned
  synthetic-only 証拠の引用のみ: #876 L4（branch
  `agent/861-crshadow-desync-20261128` evidence `2e55117e`: desync shadow＋
  `$A5` で wrong-image publish RED）＋ #878 L5（branch
  `agent/862-loader-ownership-20261128` evidence `cc7c6ebd`: last-sector-wins
  で IPLJMP 時に最終 sector バイトが `$2502`/`$2503` に残る）。いずれも pinned
  Geargrafx `b49ae82a`・cc65 `71746c8`（`tools/dependencies.json` 既 pin、
  新規 dep なし）上の synthetic media＋synthetic core のみ。商用の marker 値・
  sync 状態・実際分岐は UNKNOWN/UNOBSERVED（商用観測なし、`./rom/` 非接触を
  維持）。false-ok の商用発生は主張しない。ExpG 比較の解釈は構造観測に留め、
  NEXT 到達のみで機能成功としない（先行文書 §5.4 の規律を継承）。
- FOLLOW_UP: 各 API の unblock 監視は既存 Issue #47/#46/#64/#63 で継続する。
  新規公開資料の出現時は当該 Issue の契約文書（docs/47・46・64・63）に
  S-record を追加し、本書 §4 表の再評価を行う。本書自体の更新は不要
  （survey 時点の verdict として凍結）。

---

*Provenance: original research + original code (MIT). No proprietary firmware/game/reference content.
Pinned: Geargrafx `b49ae82a`, cc65 `71746c8` (`tools/dependencies.json`).
Zeograd/Stum/HuC/Hugo は REFERENCE_ONLY（本文非転載、fact citation のみ）。*
