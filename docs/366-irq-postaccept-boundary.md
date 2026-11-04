# #366 IRQ 受理後の handler 境界（合成・debugger 限定）

`tests/integration/geargrafx_irq_postaccept_boundary.py` は pinned Geargrafx
`b49ae82a012eade566d72e9d61c9d95a5caa4da3` の debugger build だけを対象にする。
純正 BIOS JSR、CD_READ/CD_BASE 実装、ABI 変更は行わない。`BLOCKED_ABI` と
`release_eligible=false` は維持する。

## 実コアで確認した順序

上流 `src/huc6280_inline.h` の `RunInstruction()` は opcode fetch の後に
`CheckIRQs()`、現在命令の実行、`HandleIRQ()` の順で呼ぶ。`HandleIRQ()` は
IRQ 種別を選び、`StackPush16` / `StackPush8`、I flag 設定、ベクタ read、PC 更新の
順で行う。この順序は推測ではなく、上記 source と合成実行の両方で確認する。

probe は `CLI; BRA *` を debugger single-step する。IRQ2 は `AssertIRQ2(true)`、
IRQ1 は `AssertIRQ1(true)`、TIQ は public timer register API と `ClockTimer()` で
実コアへ要求する。2 回目の step（BRA）終了時に種別ごとの PC (`$3F80/$3FA0/$3FC0`)、SP=`$FC` となるので、
IRQ 受理・ベクタ解決・stack push・handler PC 更新を host の PC 書換えなしで観測する。
3 回目は入らず、CPU fetch mapping（page 1/MPR1=`$F8`）で読んだ handler 先頭 ST0
(`$03`) を拒否する。

## 合格条件と副作用の区別

各 IRQ2/IRQ1/TIQ で、handler 命令数は 0、VDC AR/R と禁止 marker は不変、再開 3 回は
全て拒否する。受付自体の許可された stack frame は CPU `$21FF/$21FE/$21FD`、MPR1=`$F8`
による物理 WRAM `$01FF/$01FE/$01FD` として、CPU mapping と WRAM の両方を照合する。
差分もこの 3 byte 以外に無いことを確認する。SP は `$FF -> $FC`。
測定された stacked return は `$3E01` であり、実コアの BRA 実装による観測値として記録し、
ハードウェア一般の主張にはしない。

RED 実行は入口検査無効、opcode 検査省略、IRQ 種別誤分類、handler PC 後の再検査省略、
resume 許可、stack 検査無効の 6 種である。前 4/resume は実コアで handler ST0 実行または
分類不一致となる。stack 検査無効は、必須の alias/許可範囲証拠を削った classifier mutation
として拒否する（stack mapping を危険な値にして実行する試験ではない）。

## 構成の境界

これは PR #610 の debugger instruction-step / CPU-fetch opcode boundary の構造を再利用する、
合成 ROM/合成 CD の実コア診断である。
libretro shared-core にはこの probe を駆動する単一命令停止 API がないため **BLOCKED**。
従って libretro、実機、純正 System Card、商用ディスク、CD device IRQ 経路の安全性を
PASS としない。#367 の block/device-originated write も対象外である。
