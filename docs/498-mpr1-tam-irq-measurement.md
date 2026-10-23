# Issue #498: 方式 A（MPR1 事前検査）の TAM + IRQ1 実コア測定

## 位置づけ

- #473 S2。#497（契約、main 統合済み）の判定値を基準に、方式 A（命令境界の MPR1 検査）の有効範囲と取りこぼしを、pinned Geargrafx b49ae82 の**無改造コア**で測定した。
- 対象は合成 HuCARD（`tests/fixtures/mpr1_tam_irq.s`）だけ。純正 BIOS、商用データ、`rom/` 配下の ROM は使わない。ROM バイト列と逆アセンブルは含めない。
- 本書は**本番の安全機構ではない**。#459 / #367 は BLOCKED のまま。`release_eligible=false`、参照 ABI VERIFIED 0/81、参照 JSR 許可 0/81 は変えない。
- **#498 は本 PR でクローズしない**（`Refs #498`）。#499（S3、方式 C）の開始条件は満たしていない。本 PR は S3 の着手を意味しない。
- 結論の一行: **方式 A だけでは「保留 IRQ1 の下で TAM が MPR1 を $FF にする」場合の副作用を防げない。** 測定したコアでは、同じ命令内で副作用（VDC R0 の値変化）と IRQ1 の受理が起きた（UNSAFE）。境界で止めても、その副作用は既に起きている。方式 A を単独で合格にしない。
- 緑（テスト成功）は「穴が再現した」の意味であり、安全の証明ではない。既知の穴は UNSAFE として固定している。

## 方法

探針 `tests/integration/geargrafx_mpr1_tam_irq_probe.cpp` は `HuC6280::RunInstruction` を 1 命令ずつ呼ぶ。2 つのモードを持つ（同じバイナリ、引数 `mode`）。

- **measure（測定のみ）**: TAM の後も STEPS の予算いっぱいまで実行する（ゲストは `after_tam` で周回する）。停止しない。状態の変化をすべて記録する。
- **safety（判定モード）**: `tam_site` に到達した後、命令境界で MPR1 が許可値（$F8、#497 の許可表）と異なった時点で**停止**する。停止後は `RunInstruction` を呼ばない。`END` は停止時の状態を繰り返すので、停止を検査できる。停止は**ホスト側（探針）が emulated guest の実行を止めること**であり、ゲスト側の仕組みではない。実機のゲストを止める機構ではない。

記録の種類:

- `STEP before/after`: `tam_site` に到達した命令の前後の PC/A/X/Y/P/S/MPR0-7/IRR/IDR
- `RET`: 最初に `after_tam` に到達した時点の状態（TAM 直後、または IRQ1 の RTI の後）。measure モードのみ
- `WRW`: 命令ごとに値が変わった WRAM バイト（オフセット、旧値、新値）
- `VDW`: 命令ごとに値が変わった VDC レジスタ（AR、SR、R0-R31）
- `HALT` / `HALTMEM`: safety モードで停止した時点の状態（PC/A/X/Y/P/S/MPR/IRR/IDR、WRAM スタック $1FD-$1FF、VDC R0）
- `END`: 予算、実行数（停止前の `RunInstruction` 数）、停止したか、最終 PC と MPR0-7

IRQ1 は、`tam_site` に到達した直後（TAM の命令を実行する前）に host が assert する。`STEP before` の時点で IRR=$02 を確認する。コアは命令の**先頭**で `CheckIRQs()` により保留を確定し（`huc6280_inline.h`）、命令の**末尾**で `HandleIRQ()` を呼ぶ。つまり保留は TAM の命令内で受理される。

safety モードの停止判定は、ゲストが `tam_site` に到達した後だけ有効にする（fixture 自身の初期化、`lda #$F8` / `tam #$02` は判定しない）。

判定は `tests/integration/test_mpr1_tam_irq.py`（unittest、`make integration-test` で自動検出）。両モードを各ケースで実行する。方式 A の境界判定と #497 の IRQ 窓判定は `tools/mpr1_stack_contract.py` を再利用する。

合成 fixture は 4 ケース。ケースごとに別ビルドする。

| ケース | fixture | 条件 | 目的 |
|---|---|---|---|
| `n_irq_tam` | CASE_TAM_IRQ | I=0、A=$FF、TAM #$02、保留 IRQ1 | N-IRQ-TAM（穴の実測） |
| `ff_no_irq` | CASE_TAM_NOIRQ | I=0、A=$FF、IRQ なし | $FF 拒否（対照） |
| `ff_irq_masked` | CASE_TAM_MASKED | I=1、A=$FF、IRQ1 assert | I=1 では受理されないことの対照 |
| `f8_irq` | CASE_TAM_F8 | I=0、A=$F8、IRQ1 assert | 正常系（MPR1 は WRAM のまま） |

fixture の既知値（テストの期待値の出どころ）: MPR0=$F8（`lda #$F8` / `tam #$01`）、X=$FF、Y=$00、S=$FF（`ldx`/`ldy`/`txs`）、N フラグは直前の `lda`、I は `cli`（I=0）か `sei` 維持（I=1）。Y は以前は未設定で、同一ケースの実行間で値が変わったため、`ldy #$00` を追加した。

## 命令前の条件（テストで assert）

| ケース | IRR（前） | IDR（前） | I（前） | 保留（判定） |
|---|---|---|---|---|
| n_irq_tam | $02 | $00 | 0 | あり |
| ff_no_irq | $00 | $00 | 0 | なし |
| ff_irq_masked | $02 | $00 | 1 | なし（I=1） |
| f8_irq | $02 | $00 | 0 | あり |

全ケースで MPR1 は TAM の前に $F8（WRAM）、A/X/Y/S は fixture の値。`test_tam_start_pending_state_is_asserted` が上表と fixture 値を assert する。

## 測定ログ（pinned b49ae82、無改造コア、STEPS=40、measure モード）

`python3 tests/integration/test_mpr1_tam_irq.py -v` の `MEASURE` 行から転記した。

| ケース | MPR1 前→後 | PC 前→後 | S 前→後 | P 前→後 | 命令内の副作用 | IRQ 受理 | 状態差分（STEP） | 戻り（RET） |
|---|---|---|---|---|---|---|---|---|
| n_irq_tam | $F8→$FF | $E012→$E016 | $FF→$FC | $80→$84 | VDC R0 の値変化 1 件（新値 $E014）。WRAM の変化なし | あり（IRQ1 の入口 $E016） | pc, p, s, mpr | **NOT MEASURED**（`after_tam` に戻らず） |
| ff_no_irq | $F8→$FF | $E012→$E014 | $FF→$FF | $80→$80 | なし | なし | pc, mpr | pc=$E014、P=$80、S=$FF（TAM 直後） |
| ff_irq_masked | $F8→$FF | $E011→$E013 | $FF→$FF | $84→$84 | なし | なし（I=1） | pc, mpr | pc=$E013、P=$84、S=$FF（TAM 直後） |
| f8_irq | $F8→$F8 | $E012→$E016 | $FF→$FC | $80→$84 | WRAM $01FD=$80、$01FE=$14、$01FF=$E0 の 3 件（push） | あり | pc, p, s | pc=$E014、P=$80、S=$FF（RTI 後） |

- `n_irq_tam` の IRR は TAM 前に $02（IRQ1 保留）、IDR は $00。TAM の命令の末尾で IRQ1 の入口（ベクタ分岐）に入った。
- `n_irq_tam` の push 先は**推定**である。WRAM ($01FD-$01FF) は変化しなかった（実測）。観測できたのは VDC R0 の値変化 1 件（新値 $E014）だけで、新値は戻り先 `after_tam` と一致する。3 バイトの push が VDC の書込みとして現れたことは推定であり、書込み先アドレス、書込み順は未測定（bank $FF の I/O 書込みは探針が記録しない）。
- 測定の区別: `f8_irq` の push 先（WRAM $01FD-$01FF）は**実測**。`n_irq_tam` の push 先は**推定**。WRAM の HALTMEM（$01FD-$01FF の 0）は `n_irq_tam` の実際の push 先ではない（push は WRAM に入っていない）。
- `n_irq_tam` は RTI が `after_tam` に戻らなかった（RET なし）。戻りの読出しは観測していない。VDC 読出しは観測しないため、読まれた値も記録されない。
- `f8_irq` の push は WRAM（スタック $01FD〜$01FF）に入った。押された値は、戻り先 `after_tam`（$E014）と TAM 前の P（$80）に一致する。RTI 後は PC、A、X、Y、P、S、MPR0-7 が TAM 前と一致した（MPR1 は変化なし）。
- `ff_no_irq`、`ff_irq_masked` は TAM 直後の状態が TAM 前と一致した（MPR1 の変化だけを除く）。
- 測定前の P は `clc`/`clv` で $80 に固定した（固定前は同じケースで $81 と $C0 が観測され、再現性が無かった）。P の値は判定に使っていない（I フラグのみ判定に使う）。

## 停止時の記録（safety モード、pinned b49ae82）

「拒否の後で止まったか」の実測。停止後に実行された命令は 0、停止時の END の状態は HALT と一致（`demonstrated=True`）。

| ケース | 停止 step | 停止時 PC | S | P | MPR1 | 停止時 WRAM $01FD-$01FF | 停止時 VDC R0 | 停止後の WRAM/VDC 変化 | 停止後の実行命令 |
|---|---|---|---|---|---|---|---|---|---|
| n_irq_tam | 12 | $E016（IRQ1 入口、RTI の前） | $FC | $84 | $FF | 0, 0, 0 | $E014（TAM 中に変化済み） | 0 | 0 |
| ff_no_irq | 12 | $E014（`after_tam`） | $FF | $80 | $FF | 0, 0, 0 | 0（変化なし） | 0 | 0 |
| ff_irq_masked | 11 | $E013（`after_tam`） | $FF | $84 | $FF | 0, 0, 0 | 0（変化なし） | 0 | 0 |
| f8_irq | なし（停止しない） | - | - | - | $F8 | - | - | - | STEPS 全件 |

- 停止は「次の命令を実行しない」ことで行う。`bra after_tam` や `rti` は実行されない。`END` の PC/MPR は HALT と一致した。
- ゲストの停止は host の探針が止めるものであり、ゲスト側（実機）で拒否を強制する機構ではない。方式 A の「次の境界で止める」を探針で再現したにすぎない。

## 判定表（方式 A と #497 契約）

| ケース | 方式 A（次の境界で MPR1 を判定） | #497 IRQ 窓（`judge_irq_window`） | 実測の分類 | 停止（safety） | 期待 | 結果 |
|---|---|---|---|---|---|---|
| n_irq_tam | FAIL（$FF） | UNMEASURED | **UNSAFE_AFTER_SIDE_EFFECT**（副作用が TAM の命令内で観測され、境界判定より前） | 停止を確認（IRQ 入口の前） | UNSAFE | 一致（既知の穴の再現。合格ではない） |
| ff_no_irq | FAIL（$FF） | PASS（IRQ なし） | **REJECTED_NO_WRAM_VDC_CHANGE_HALTED**（TAM の命令内で観測された副作用なし、停止を確認） | 停止を確認 | 拒否 | 一致 |
| ff_irq_masked | FAIL（$FF） | UNMEASURED（保守的。実際は受理なし） | **REJECTED_NO_WRAM_VDC_CHANGE_HALTED**（I=1 で受理なし、副作用なし、停止を確認） | 停止を確認 | 拒否 | 一致 |
| f8_irq | PASS（$F8） | PASS（MPR1 変化なし） | **PASS**（push は WRAM） | 停止なし | PASS | 一致 |

分類の定義:

- **UNSAFE_AFTER_SIDE_EFFECT**: MPR1 が拒否バンクに変わり、同じ命令内で副作用（WRAM/VDC の値変化）と IRQ 受理が観測された。方式 A の境界判定と停止は、この副作用より後にしか効かない。**方式 A 単独では、この副作用を防げない。**
- **REJECTED_NO_WRAM_VDC_CHANGE_HALTED**: MPR1 が拒否バンクに変わり、TAM の命令内に観測された副作用（WRAM/VDC の値変化）と IRQ 受理が無く、safety モードで停止した（停止後の実行命令 0）。名称が示すのは「探針が観測する範囲（WRAM の値変化と VDC レジスタの値変化）で変化が無い」「停止を測定した」の 2 点だけである。同値の書込み、VDC 読出し、bank $FF のその他の I/O（PSG、タイマ、IRQ 制御）は観測できないため、副作用が無いことの証明ではない。
- **REJECTED_NO_WRAM_VDC_CHANGE_HALT_NOT_MEASURED**: 上と同じ観測だが、停止が測定されなかった場合の名称。合格ではない。本測定では使われない（判定コードには残す）。
- **PASS**: MPR1 が変わらず、境界判定が PASS。

## 限界と未実施（NOT_RUN / UNMEASURED / NOT_APPLICABLE）

| 項目 | 状態 | 理由・既存 Issue |
|---|---|---|
| `n_irq_tam` の戻り PC、A/X/Y/P/S/MPR | NOT_RUN | RTI が `after_tam` に戻らなかった（measure モード）。停止により戻りは測定しない |
| 値が変わらない書込み（同値の VDC 書込み等） | NOT_RUN | 探針は値の変化だけを記録する |
| bank $FF の VDC 以外の I/O（PSG、タイマ、IRQ 制御、I/O ポート） | NOT_RUN | 探針は VDC レジスタだけを観測する |
| VDC 読出し、ステータス読出しの副作用 | NOT_RUN | 探針は VDC の読出しを観測しない |
| IRQ の push 順序（S=$FF から $21FF/$21FE/$21FD の順） | UNMEASURED | 1 命令内の書込み順は記録しない。`f8_irq` で 3 バイトの値と番地は確認したが、順序は未確定 |
| TII/TIN/TIA/TDD/TAI 各命令での $FF 書込み/読出し | NOT_RUN | 本 PR は TAM + IRQ1 の窓に絞った。既存 Issue: #367（OPEN、BLOCKED） |
| JSR/PHA の $FF 書込み | NOT_RUN | #473 の結果 2（CASE_IO）で観測済み。本 PR では再測定しない。既存 Issue: #173（API JSR 用の強制監視） |
| ハーネス直接 SetMpr（$FF、中間の命令番号） | NOT_RUN | #497 の契約（DIRECT_SETMPR）の経路。本 PR の fixture は TAM のみ。既存 Issue: #497（契約）、#173 |
| リセット直後の MPR1 の初期値 | NOT_RUN | fixture は reset 後に MPR1 を TAM で設定する。リセット直後の値は測定していない。#497 は初期値を契約の対象外（UNDETERMINED）とする |
| IRQ 非保留（I=0、IRQ なし）で TAM 中に assert が入る場合 | NOT_RUN | 本 fixture は TAM の直前に assert する。TAM の命令中に新規 assert する経路は、探針から命令内に介入できないため測定不能（#497 の「新規 assert」） |
| NMI | NOT_APPLICABLE | pinned コアに受理経路が無い（#497 §4.1） |
| 実機の TAM/IRQ タイミング | UNKNOWN | 合成 HuCARD、合成コアのみ。実機は未確認 |

## #498 の未完了条件（項目別）

| # | #498 の条件 | 状態 | 根拠・残り |
|---|---|---|---|
| 1 | 正常系 MPR1=$F8（#481 CASE_RAM / #470 stackwram の再利用、正常復帰・レジスタ保存） | PARTIAL | `f8_irq` で RTI 後の PC/A/X/P/S/MPR と push 値を fixture 定数で assert。#481 / #470 の fixture は再利用していない |
| 2 | 拒否（TAM / ハーネス直接 / #481 CASE_IO / JSR・PHA / TII 系） | PARTIAL | TAM 経路のみ DONE（`ff_no_irq`、`ff_irq_masked`、`n_irq_tam`）。直接 SetMpr、CASE_IO、JSR/PHA、TII 系は NOT_RUN |
| 3 | TAM と同一命令内の IRQ push | PARTIAL | 発生と受理は DONE（IRQ 入口到達、同ステップ内の副作用）。push の順序と番地は UNMEASURED |
| 4 | IRQ-in-TAM 負例 | DONE | `n_irq_tam` を UNSAFE に固定、対照は `ff_no_irq` |
| 5 | PC/SP/P/MPR/VDC/WRAM の比較と差分報告 | PARTIAL | PC/A/X/Y/P/S/MPR/IRR/IDR は前後比較、WRAM は毎ステップ比較、VDC は値変化のみ。VDC 読出し値（status、buffer、R7）は比較なし |
| 6 | 副作用が拒否の前か後かの区別 | PARTIAL | TAM 経路で DONE（UNSAFE と REJECTED の分類）。他の経路は NOT_RUN |
| 7 | 拒否後にゲストを止める（停止の実測） | DONE（探針の停止、TAM 経路）／オーナー判断待ち | safety モードで停止し、停止後の実行 0 と END の一致を assert（`test_safety_mode_halts_at_first_denied_boundary`、`test_halt_state_is_fixture_derived`）。ただし停止は探針（ホスト側）が行うもので、実 BIOS・本番経路の停止機構ではない。「ホスト側停止で #498 の拒否後停止条件を満たすか」はオーナー未決定。ゲスト側の強制機構は本 PR の対象外（#499 / S3 の議論） |
| 8 | 退行テスト | PARTIAL | 本 PR の変更ごとに新規テスト（下の RED/GREEN 節）。既存の #470 / #481 の退行は既存スイートで間接的に通る |
| 9 | 期待値の独立性（probe の自己比較ではなく fixture 定数） | PARTIAL | A/X/Y/S/P/MPR0 と停止時の S は fixture 定数で assert。MPR2-7 のみ pre-TAM 記録と比較（fixture が設定しない） |
| 10 | 停止時の PC/SP/メモリ/副作用の記録 | DONE（TAM 経路） | `HALT`、`HALTMEM`、`END` を記録し、表（停止時の記録節）に転記 |
| 11 | 独立レビュー | NOT_RUN | 本 PR の再レビューはオーナーまたは別レビュアーが行う |

## 既存 Issue との照合（子 Issue の要否）

| 項目 | 既存 Issue | 判定 |
|---|---|---|
| 直接 SetMpr（中間の命令番号で $FF） | #497（契約、DIRECT_SETMPR）、#173（API JSR 用の強制監視） | 契約と監視は既存。実コア測定は既存 Issue の本文に明記なし → 子 Issue 候補 |
| リセット直後の MPR1 初期値 | #497（初期状態の契約、RESET_MPR1 = UNDETERMINED） | 契約は既存。実コアでの初期値の測定は既存 Issue に明記なし → 子 Issue 候補 |
| TII / TIN / TIA / TDD / TAI の $FF 書込み | #367（OPEN、BLOCKED）、#459（TransferStart の push、BLOCKED） | 既存（BLOCKED） |
| JSR / PHA の $FF 書込み | #173（JSR 用の強制監視）、#361（子、副作用前遮断プローブ） | JSR は既存。PHA は明示されていない → 子 Issue 候補（PHA を含むか、#173 の範囲で確認が必要） |
| IRQ-in-TAM の再検証 | #500（S4、方式 A+C 併用の IRQ-in-TAM 回帰検証）、#499（S3、方式 C） | 既存 |

## 次の段階への入力

- **S3 で扱う**: 同一命令内の IRQ 受理を防ぐ仕組み（例: TAM の MPR1 変更後にだけ IRQ を遅らせる、または TAM 対象バンクの書込みを命令前に拒否する）。方式 A 単独では不足（停止しても TAM 内の副作用は残る）。S3 の開始条件（#499）は本 PR では満たしていない。
- 本書の結論は PR の Draft 段階で、オーナーの確認を経ていない。#459 / #367 の解除、#324 の承認は行わない。

## 再現

```
TOOLS_DIR=<tools> OSS_PCE_REQUIRE_CC65=1 python3 tests/integration/test_mpr1_tam_irq.py -v
```

（`make integration-test` でも実行される。コアかツールが無い場合は FAIL で、スキップしない。）
