# Issue #498: 方式 A（MPR1 事前検査）の TAM + IRQ1 実コア測定

## 位置づけ

- #473 S2。#497（契約、main 統合済み）の判定値を基準に、方式 A（命令境界の MPR1 検査）の有効範囲と取りこぼしを、pinned Geargrafx b49ae82 の**無改造コア**で測定した。
- 対象は合成 HuCARD（`tests/fixtures/mpr1_tam_irq.s`）だけ。純正 BIOS、商用データ、`rom/` 配下の ROM は使わない。
- 本書は**本番の安全機構ではない**。#459 / #367 は BLOCKED のまま。`release_eligible=false`、参照 ABI VERIFIED 0/81、参照 JSR 許可 0/81 は変えない。
- 結論の一行: **方式 A だけでは「TAM の MPR1 変更と IRQ1 の push が同一 RunInstruction に入る」場合を防げない。** 実コアで副作用の発生を確認した（UNSAFE）。方式 A を単独で合格にしない。

## 方法

- 探針 `tests/integration/geargrafx_mpr1_tam_irq_probe.cpp` は、`HuC6280::RunInstruction` を 1 命令ずつ呼ぶ。`tam_site` に到達した命令の前後で、PC/A/X/Y/P/S/MPR0-7/IRR/IDR を記録する。
- IRQ1 は、`tam_site` に到達した直後（TAM の命令を実行する前）に host が assert する。コアは命令の**先頭**で `CheckIRQs()` により保留を確定し（`huc6280_inline.h`）、命令の**末尾**で `HandleIRQ()` を呼ぶ。つまり保留は TAM の命令内で受理される。
- WRAM の変化（WRW）と VDC レジスタの変化（VDW）を命令ごとに記録する。VDC の読出しは観測しない。値が変わらない書込みは見えない。
- 判定は `tests/integration/test_mpr1_tam_irq.py`（unittest、`make integration-test` で自動検出）。方式 A の境界判定と #497 の IRQ 窓判定は `tools/mpr1_stack_contract.py` を再利用する。
- 合成 fixture は 4 ケース。ケースごとに別ビルドする。

| ケース | fixture | 条件 | 目的 |
|---|---|---|---|
| `n_irq_tam` | CASE_TAM_IRQ | I=0、A=$FF、TAM #$02、保留 IRQ1 | N-IRQ-TAM（穴の実測） |
| `ff_no_irq` | CASE_TAM_NOIRQ | I=0、A=$FF、IRQ なし | $FF 拒否（対照） |
| `ff_irq_masked` | CASE_TAM_MASKED | I=1、A=$FF、IRQ1 assert | I=1 では受理されないことの対照 |
| `f8_irq` | CASE_TAM_F8 | I=0、A=$F8、IRQ1 assert | 正常系（MPR1 は WRAM のまま） |

## 測定ログ（pinned b49ae82、無改造コア、STEPS=40）

`python3 -m unittest test_mpr1_tam_irq -v` の出力（`MEASURE` 行）から転記した。

| ケース | MPR1 前→後 | PC 前→後 | S 前→後 | P 前→後 | 命令内の副作用 | IRQ 受理 | 状態差分 |
|---|---|---|---|---|---|---|---|
| n_irq_tam | $F8→$FF | $E00E→$E012 | $FF→$FC | $81→$85 | VDC R0 の変化 1 件（push 3 バイト分の VDC 書込み） | あり（IRQ1 の入口） | pc, p, s, mpr |
| ff_no_irq | $F8→$FF | $E00E→$E010 | $FF→$FF | $81→$81 | なし | なし | pc, mpr |
| ff_irq_masked | $F8→$FF | $E00D→$E00F | $FF→$FF | $85→$85 | なし | なし（I=1） | pc, mpr |
| f8_irq | $F8→$F8 | $E00E→$E012 | $FF→$FC | $81→$85 | WRAM $01FD〜$01FF の 3 件（push） | あり | pc, p, s |

- `n_irq_tam` の IRR は TAM 前に $02（IRQ1 保留）、IDR は $00。TAM の命令の末尾で IRQ1 の入口（ベクタ分岐）に入った。
- `f8_irq` の push は WRAM（スタック $01FD〜$01FF）に入った。MPR1 は変わらないため、命令境界の判定と整合する。
- 差分はすべて報告した。`n_irq_tam` で想定外の差分（A、X、Y、WRAM の別箇所）は無い。MPR0-7 は MPR1 だけが変わった。

## 判定表（方式 A と #497 契約）

| ケース | 方式 A（次の境界で MPR1 を判定） | #497 IRQ 窓（`judge_irq_window`） | 実測の分類 | 期待 | 結果 |
|---|---|---|---|---|---|
| n_irq_tam | FAIL（$FF） | UNMEASURED | **UNSAFE_AFTER_SIDE_EFFECT**（副作用が TAM の命令内で発生し、境界判定より前） | UNSAFE | 一致（想定された穴の再現） |
| ff_no_irq | FAIL（$FF） | PASS（IRQ なし） | **REJECTED_BEFORE_SIDE_EFFECT**（副作用なし。次の境界で停止） | 拒否 | 一致 |
| ff_irq_masked | FAIL（$FF） | UNMEASURED（保守的。実際は受理なし） | **REJECTED_BEFORE_SIDE_EFFECT**（I=1 で受理なし） | 拒否 | 一致 |
| f8_irq | PASS（$F8） | PASS（MPR1 変化なし） | **PASS**（push は WRAM） | PASS | 一致 |

分類の定義:

- **UNSAFE_AFTER_SIDE_EFFECT**: MPR1 が拒否バンクに変わり、同じ命令内で副作用（WRAM/VDC への書込み）と IRQ 受理が起きた。方式 A の境界判定は、この副作用より後にしか効かない。
- **REJECTED_BEFORE_SIDE_EFFECT**: MPR1 が拒否バンクに変わったが、命令内に副作用と IRQ 受理が無い。境界判定で次の命令を実行する前に止まる。
- 「rejected before side effect」は、境界判定が次の命令より前に効くことを**この測定の範囲で**示すだけである。TAM 以外の経路（直接 SetMpr、リセット直後の値）は本測定の対象外。

## 結果の解釈

- 方式 A は「TAM が $FF にした後の命令境界」では確実に検出した（`ff_no_irq`、`ff_irq_masked`）。
- IRQ が TAM の命令の**前**から保留されていた場合（`n_irq_tam`）、TAM 自身の命令末尾で push が起きる。方式 A の境界判定はそれより後なので、副作用を防げない。これは #497 の「同一 RunInstruction 内の穴」（保留の場合）が**実際に起きる**ことの実測である。
- #497 の判定は `n_irq_tam` を UNMEASURED のまま保持する。本測定により、この穴は **UNSAFE（副作用あり）** と確定した。UNMEASURED を PASS に変えることはしない。
- 保留でない（I=1）場合は受理されない（`ff_irq_masked`）。ただし、これは観測上の対照であり、保留 IRQ の受理条件を一般化したものではない。

## 限界と未実施（NOT_RUN）

| 項目 | 状態 | 理由 |
|---|---|---|
| TII/TIN/TIA/TDD/TAI 各命令での $FF 書込み/読出し | NOT_RUN | 本 PR は TAM + IRQ1 の窓に絞った（PR 規模）。ブロック転送の 1 例は #473（`docs/473-caller-stack-experiment.md` 結果 3）で観測済み。他の転送命令は未測定 |
| JSR/PHA の $FF 書込み | NOT_RUN | #473 の結果 2（CASE_IO）で観測済み。本 PR では再測定しない |
| ハーネス直接 SetMpr（$FF、中間の命令番号） | NOT_RUN | #497 の契約（DIRECT_SETMPR）の経路。本 PR の fixture は TAM のみ |
| IRQ の push 順序（S=$FF から $21FF/$21FE/$21FD の順） | UNMEASURED | VDW は**値の変化**だけを記録する。R0 の 1 件しか見えず、3 回の push の個別の順序と書込み番地は未確定 |
| VDC 読出し、ステータス読出しの副作用 | UNMEASURED | 探針は VDC の読出しを観測しない |
| NMI | NOT_APPLICABLE | pinned コアに受理経路が無い（#497 §4.1） |
| 実機の TAM/IRQ タイミング | UNKNOWN | 合成 HuCARD、合成コアのみ。実機は未確認 |
| IRQ 非保留（I=0、IRQ なし）で TAM 中に assert が入る場合 | NOT_RUN | 本 fixture は TAM の直前に assert する。TAM の命令中に新規 assert する経路は、探針から命令内に介入できないため測定不能（#497 の「新規 assert」） |

## 次の段階への入力

- **S3 で扱う**: 同一命令内の IRQ 受理を防ぐ仕組み（例: TAM の MPR1 変更後にだけ IRQ を遅らせる、または TAM 対象バンクの書込みを命令前に拒否する）。方式 A 単独では不足。
- 本書の結論は PR の Draft 段階で、オーナーの確認を経ていない。#459 / #367 の解除、#324 の承認は行わない。

## 再現

```
TOOLS_DIR=<tools> OSS_PCE_REQUIRE_CC65=1 python3 tests/integration/test_mpr1_tam_irq.py -v
```

（`make integration-test` でも実行される。コアかツールが無い場合は FAIL で、スキップしない。）
