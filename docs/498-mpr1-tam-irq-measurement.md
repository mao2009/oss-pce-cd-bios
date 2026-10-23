# Issue #498: 方式 A（MPR1 事前検査）の TAM + IRQ1 実コア測定

## 位置づけ

- #473 S2。#497（契約、main 統合済み）の判定値を基準に、方式 A（命令境界の MPR1 検査）の有効範囲と取りこぼしを、pinned Geargrafx b49ae82 の**無改造コア**で測定した。
- 対象は合成 HuCARD（`tests/fixtures/mpr1_tam_irq.s`）だけ。純正 BIOS、商用データ、`rom/` 配下の ROM は使わない。ROM バイト列と逆アセンブルは含めない。
- 本書は**本番の安全機構ではない**。#459 / #367 は BLOCKED のまま。`release_eligible=false`、参照 ABI VERIFIED 0/81、参照 JSR 許可 0/81 は変えない。
- **#498 は本 PR でクローズしない**（`Refs #498`）。#499（S3、方式 C）の開始条件は満たしていない。本 PR は S3 の着手を意味しない。
- 結論の一行: **方式 A だけでは「保留 IRQ1 の下で TAM が MPR1 を $FF にする」場合を防げない。** 測定したコアでは、同じ命令内で副作用（VDC R0 の値変化）と IRQ1 の受理が起きた（UNSAFE）。方式 A を単独で合格にしない。
- 探針は**停止せず続行する**。TAM の後も STEPS の上限まで実行し（ゲストは `after_tam` で周回する）、拒否判定は記録された状態から導く。ゲストを拒否バンクで止めることは測定していない。

## 方法

- 探針 `tests/integration/geargrafx_mpr1_tam_irq_probe.cpp` は、`HuC6280::RunInstruction` を 1 命令ずつ呼ぶ。記録は次の 4 種。
  - `STEP before/after`: `tam_site` に到達した命令の前後の PC/A/X/Y/P/S/MPR0-7/IRR/IDR
  - `RET`: 最初に `after_tam` に到達した時点の状態（TAM 直後、または IRQ1 の RTI の後）
  - `WRW`: 命令ごとに値が変わった WRAM バイト（オフセット、旧値、新値）
  - `VDW`: 命令ごとに値が変わった VDC レジスタ（AR、SR、R0-R31）
- IRQ1 は、`tam_site` に到達した直後（TAM の命令を実行する前）に host が assert する。`STEP before` の時点で IRR=$02 を確認する。コアは命令の**先頭**で `CheckIRQs()` により保留を確定し（`huc6280_inline.h`）、命令の**末尾**で `HandleIRQ()` を呼ぶ。つまり保留は TAM の命令内で受理される。
- 判定は `tests/integration/test_mpr1_tam_irq.py`（unittest、`make integration-test` で自動検出）。方式 A の境界判定と #497 の IRQ 窓判定は `tools/mpr1_stack_contract.py` を再利用する。
- 合成 fixture は 4 ケース。ケースごとに別ビルドする。

| ケース | fixture | 条件 | 目的 |
|---|---|---|---|
| `n_irq_tam` | CASE_TAM_IRQ | I=0、A=$FF、TAM #$02、保留 IRQ1 | N-IRQ-TAM（穴の実測） |
| `ff_no_irq` | CASE_TAM_NOIRQ | I=0、A=$FF、IRQ なし | $FF 拒否（対照） |
| `ff_irq_masked` | CASE_TAM_MASKED | I=1、A=$FF、IRQ1 assert | I=1 では受理されないことの対照 |
| `f8_irq` | CASE_TAM_F8 | I=0、A=$F8、IRQ1 assert | 正常系（MPR1 は WRAM のまま） |

## 命令前の条件（テストで assert）

| ケース | IRR（前） | IDR（前） | I（前） | 保留（判定） |
|---|---|---|---|---|
| n_irq_tam | $02 | $00 | 0 | あり |
| ff_no_irq | $00 | $00 | 0 | なし |
| ff_irq_masked | $02 | $00 | 1 | なし（I=1） |
| f8_irq | $02 | $00 | 0 | あり |

全ケースで MPR1 は TAM の前に $F8（WRAM）。`test_tam_start_pending_state_is_asserted` が上表を assert する。

## 測定ログ（pinned b49ae82、無改造コア、STEPS=40）

`python3 tests/integration/test_mpr1_tam_irq.py -v` の `MEASURE` 行から転記した。

| ケース | MPR1 前→後 | PC 前→後 | S 前→後 | P 前→後 | 命令内の副作用 | IRQ 受理 | 状態差分（STEP） | 戻り（RET） |
|---|---|---|---|---|---|---|---|---|
| n_irq_tam | $F8→$FF | $E010→$E014 | $FF→$FC | $80→$84 | VDC R0 の値変化 1 件。WRAM の変化なし | あり（IRQ1 の入口） | pc, p, s, mpr | **NOT MEASURED**（`after_tam` に 40 ステップ以内に戻らず） |
| ff_no_irq | $F8→$FF | $E010→$E012 | $FF→$FF | $80→$80 | なし | なし | pc, mpr | pc=$E012、P=$80、S=$FF（TAM 直後） |
| ff_irq_masked | $F8→$FF | $E00F→$E011 | $FF→$FF | $84→$84 | なし | なし（I=1） | pc, mpr | pc=$E011、P=$84、S=$FF（TAM 直後） |
| f8_irq | $F8→$F8 | $E010→$E014 | $FF→$FC | $80→$84 | WRAM $01FD=$80、$01FE=$12、$01FF=$E0 の 3 件（push） | あり | pc, p, s | pc=$E012、P=$80、S=$FF（RTI 後） |

- `n_irq_tam` の IRR は TAM 前に $02（IRQ1 保留）、IDR は $00。TAM の命令の末尾で IRQ1 の入口（ベクタ分岐）に入った。
- `n_irq_tam` の push は bank $FF 側に入り、観測上は VDC R0 の値変化 1 件だけが見えた（3 バイト分の push が VDC のどの書込みに当たるかは未確定）。
- `n_irq_tam` は RTI が `after_tam` に戻らなかった（RET なし）。戻り値は**測定していない**。戻りの読出しが bank $FF 側に当たったことは推定であり未確認。探針は VDC 読出しを観測しないため、読まれた値も記録されない。
- `f8_irq` の push は WRAM（スタック $01FD〜$01FF）に入った。押された値は、戻り先 $E012（`after_tam`）と TAM 前の P（$80）に一致する。RTI 後は PC、A、X、Y、P、S、MPR0-7 が TAM 前と一致した（MPR1 は変化なし）。
- `ff_no_irq`、`ff_irq_masked` は TAM 直後の状態が TAM 前と一致した（MPR1 の変化だけを除く）。
- `n_irq_tam` で想定外の差分（A、X、Y、WRAM の別箇所）は観測されていない。MPR0-7 は MPR1 だけが変わった。
- 測定前の P は `clc`/`clv` で $80 に固定した（固定前は同じケースで $81 と $C0 が観測され、再現性が無かった）。P の値は判定に使っていない（I フラグのみ判定に使う）。

## 判定表（方式 A と #497 契約）

| ケース | 方式 A（次の境界で MPR1 を判定） | #497 IRQ 窓（`judge_irq_window`） | 実測の分類 | 期待 | 結果 |
|---|---|---|---|---|---|
| n_irq_tam | FAIL（$FF） | UNMEASURED | **UNSAFE_AFTER_SIDE_EFFECT**（副作用が TAM の命令内で観測され、境界判定より前） | UNSAFE | 一致（既知の穴の再現。合格ではない） |
| ff_no_irq | FAIL（$FF） | PASS（IRQ なし） | **REJECTED_BEFORE_SIDE_EFFECT**（観測された副作用なし） | 拒否 | 一致 |
| ff_irq_masked | FAIL（$FF） | UNMEASURED（保守的。実際は受理なし） | **REJECTED_BEFORE_SIDE_EFFECT**（I=1 で観測された受理なし、副作用なし） | 拒否 | 一致 |
| f8_irq | PASS（$F8） | PASS（MPR1 変化なし） | **PASS**（push は WRAM） | PASS | 一致 |

分類の定義:

- **UNSAFE_AFTER_SIDE_EFFECT**: MPR1 が拒否バンクに変わり、同じ命令内で副作用（WRAM/VDC の値変化）と IRQ 受理が観測された。方式 A の境界判定は、この副作用より後にしか効かない。
- **REJECTED_BEFORE_SIDE_EFFECT**: MPR1 が拒否バンクに変わったが、命令内に観測された副作用と IRQ 受理が無い。境界判定は次の命令より前に FAIL を出す。ただし探針は止めない。止めることは本測定の対象外。
- 「REJECTED_BEFORE_SIDE_EFFECT」は、この測定の範囲（TAM、合成 HuCARD、無改造コア、観測された書込みだけ）で副作用が無かったことを示すだけである。同値の書込み、VDC 読出し、bank $FF のその他の I/O は観測できないため、「副作用が無い」ことの証明ではない。

## 限界と未実施（NOT MEASURED / NOT_RUN / UNMEASURED）

| 項目 | 状態 | 理由・既存 Issue |
|---|---|---|
| `n_irq_tam` の戻り PC、A/X/Y/P/S/MPR | NOT MEASURED | RTI が `after_tam` に戻らなかった（原因は未確定。TAM 後の MPR1=$FF による推定）。戻りの読出しは観測していない |
| 値が変わらない書込み（同値の VDC 書込み等） | NOT MEASURED | 探針は値の変化だけを記録する |
| bank $FF の VDC 以外の I/O（PSG、タイマ、IRQ 制御、I/O ポート） | NOT MEASURED | 探針は VDC レジスタだけを観測する |
| VDC 読出し、ステータス読出しの副作用 | NOT MEASURED | 探針は VDC の読出しを観測しない |
| IRQ の push 順序（S=$FF から $21FF/$21FE/$21FD の順） | UNMEASURED | 1 命令内の書込み順は記録しない。`f8_irq` で 3 バイトの値と番地は確認したが、順序は未確定 |
| TII/TIN/TIA/TDD/TAI 各命令での $FF 書込み/読出し | NOT_RUN | 本 PR は TAM + IRQ1 の窓に絞った。ブロック転送の 1 例は #473（`docs/473-caller-stack-experiment.md` 結果 3）で観測済み。他の転送命令は未測定。既存 Issue: #367（OPEN、BLOCKED） |
| JSR/PHA の $FF 書込み | NOT_RUN | #473 の結果 2（CASE_IO）で観測済み。本 PR では再測定しない。既存 Issue: #173（API JSR 用の強制監視） |
| ハーネス直接 SetMpr（$FF、中間の命令番号） | NOT_RUN | #497 の契約（DIRECT_SETMPR）の経路。本 PR の fixture は TAM のみ |
| リセット直後の MPR1 の初期値 | NOT_RUN | 本 fixture は reset 後に MPR1 を TAM で設定する。リセット直後の値は測定していない |
| IRQ 非保留（I=0、IRQ なし）で TAM 中に assert が入る場合 | NOT_RUN | 本 fixture は TAM の直前に assert する。TAM の命令中に新規 assert する経路は、探針から命令内に介入できないため測定不能（#497 の「新規 assert」） |
| NMI | NOT_APPLICABLE | pinned コアに受理経路が無い（#497 §4.1） |
| 実機の TAM/IRQ タイミング | UNKNOWN | 合成 HuCARD、合成コアのみ。実機は未確認 |

## 次の段階への入力

- **S3 で扱う**: 同一命令内の IRQ 受理を防ぐ仕組み（例: TAM の MPR1 変更後にだけ IRQ を遅らせる、または TAM 対象バンクの書込みを命令前に拒否する）。方式 A 単独では不足。S3 の開始条件（#499）は本 PR では満たしていない。
- 本書の結論は PR の Draft 段階で、オーナーの確認を経ていない。#459 / #367 の解除、#324 の承認は行わない。

## 再現

```
TOOLS_DIR=<tools> OSS_PCE_REQUIRE_CC65=1 python3 tests/integration/test_mpr1_tam_irq.py -v
```

（`make integration-test` でも実行される。コアかツールが無い場合は FAIL で、スキップしない。）
