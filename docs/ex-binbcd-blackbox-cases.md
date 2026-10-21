# EX_BINBCD ブラックボックスケースの分類表（Issue #70）

親 #70。子 #310（入力被覆計画・非実行フィクスチャ、PR #346 でマージ済み）の入力と観測項目を再利用し、
各ケースを検証クラスに分類する。本書は**分類と未確定項目の一覧**であり、新しい実行や新しい期待値を追加しない。

- 純正 System Card の `$3C` / `$E0B4` の ABI は未確定（`abi_status: BLOCKED_ABI`、`release_eligible: false`）。
- 純正 `$E0B4` への JSR は一度も実行しない（`reference_jsr: NOT_CALLED`）。
- 自作ルーチン `src/ex_binbcd.s` の結果は BIOS ABI の証拠ではない（`docs/ex-binbcd-internal.md`）。
- OSS 出力を純正の期待値として使わない。

## 分類の定義

| クラス | 意味 |
|---|---|
| HOST_FIXTURE | 合成入力と判定をホストで行う。ROM は実行しない |
| REAL_CORE_OSS | 自作ルーチンを実コア（pinned Geargrafx）で実行する。純正 ABI の証拠ではない |
| REFERENCE_LOCAL | 承認済みの純正 BIOS 観測のみ。現時点で承認は無く、件数は 0 |
| BLOCKED | 安全性または ABI の前提（#173 / #228 の承認）が未充足 |

## 期待値の出典

- `SPEC_OWN`: 自作契約 `docs/ex-binbcd-internal.md`（自作ルーチンのみに適用）
- `UNKNOWN`: 出典なし。純正側はすべてこれ

## ケース表（自作ルーチン）

初期状態はいずれも「A = 入力、X = `$A5`、D = 0」（プローブの設定）。SP と MPR は記録対象だが、
自作ルーチンの前提として SP 差分 3 バイト以内（JSR 2 + PHX 1）のみ規定する。

| ID | 区分 | 入力 | 期待 A / C（期待値の出典） | 期待 X / 戻り | RAM 差分 / 副作用 | 分類 | 実行結果 | ハッシュ |
|---|---|---|---|---|---|---|---|---|
| N-00 | 通常・境界下限 | 0 | `$00` / C=0（SPEC_OWN） | X=`$A5` / RTS | なし | HOST_FIXTURE, REAL_CORE_OSS | PASS（ホスト 11 件、実コア 0..255 走査） | ROM `f705379c…c9c5` |
| N-09 | 境界（一桁上限） | 9 | `$09` / C=0（SPEC_OWN） | 同上 | なし | HOST_FIXTURE, REAL_CORE_OSS | PASS | 同上 |
| N-10 | 境界（桁上がり） | 10 | `$10` / C=0（SPEC_OWN） | 同上 | なし | HOST_FIXTURE, REAL_CORE_OSS | PASS | 同上 |
| N-99 | 境界（二桁上限） | 99 | `$99` / C=0（SPEC_OWN） | 同上 | なし | HOST_FIXTURE, REAL_CORE_OSS | PASS | 同上 |
| N-all | 通常（0..99 全値） | 0..99 | packed 値（SPEC_OWN） | 同上 | なし | HOST_FIXTURE, REAL_CORE_OSS | PASS（0..255 全走査） | 同上 |
| E-100 | 範囲外（先頭） | 100 | A=入力 `$64` / C=1（SPEC_OWN） | 同上 | なし | HOST_FIXTURE, REAL_CORE_OSS | PASS | 同上 |
| E-255 | 範囲外（最大） | 255 | A=入力 `$FF` / C=1（SPEC_OWN） | 同上 | なし | HOST_FIXTURE, REAL_CORE_OSS | PASS | 同上 |
| E-host | 不正入力（バイト範囲外） | -1, 256 | ValueError（SPEC_OWN、ホストモデルのみ） | 該当なし | なし | HOST_FIXTURE | PASS | 該当なし |
| M-tens | 変異負例（`ADC #$10` → `#$20`） | 0..255 | 不一致を検出すること（SPEC_OWN） | 同上 | なし | REAL_CORE_OSS | 期待どおり失敗（PASS_EXPECTED_FAILURE） | 変異 ROM は build 内のみ |

注: 「`ADC #$10` 変異」は実コアの検出力を確かめるための負例であり、純正の振る舞いではない。

## ケース表（純正 `$3C` / `$E0B4`）

すべて `BLOCKED`。期待値はすべて `UNKNOWN`。入力は #310 の 9 点をそのまま使う。

| ID | 区分 | 入力 | 期待 A / P / 戻り PC | 初期状態 | RAM 差分 / 副作用 | 分類 | 実行結果 | ハッシュ |
|---|---|---|---|---|---|---|---|---|
| R-00 | 通常・下限 | 0 | UNKNOWN | UNKNOWN（入力レジスタ不明） | UNKNOWN | BLOCKED | NOT_RUN | 該当なし |
| R-01 | 通常 | 1 | UNKNOWN | UNKNOWN | UNKNOWN | BLOCKED | NOT_RUN | 該当なし |
| R-09 | 境界 | 9 | UNKNOWN | UNKNOWN | UNKNOWN | BLOCKED | NOT_RUN | 該当なし |
| R-10 | 境界 | 10 | UNKNOWN | UNKNOWN | UNKNOWN | BLOCKED | NOT_RUN | 該当なし |
| R-99 | 境界 | 99 | UNKNOWN | UNKNOWN | UNKNOWN | BLOCKED | NOT_RUN | 該当なし |
| R-100 | 範囲外（先頭） | 100 | UNKNOWN | UNKNOWN | UNKNOWN | BLOCKED | NOT_RUN | 該当なし |
| R-127 | 符号境界 | 127 | UNKNOWN | UNKNOWN | UNKNOWN | BLOCKED | NOT_RUN | 該当なし |
| R-128 | 符号境界 | 128 | UNKNOWN | UNKNOWN | UNKNOWN | BLOCKED | NOT_RUN | 該当なし |
| R-255 | 範囲外（最大） | 255 | UNKNOWN | UNKNOWN | UNKNOWN | BLOCKED | NOT_RUN | 該当なし |

純正側のエラー表現、範囲外挙動、戻り PC、スタック、MPR 設定はいずれも UNKNOWN（`spec/api_contracts.json` slot 60 も全項目 UNKNOWN）。

## REFERENCE_LOCAL

承認済みの純正観測は 0 件。本書で純正 BIOS の値は一つも確定していない。

## 未確定項目（UNKNOWN の一覧）

1. 純正の入力レジスタ（A か作業 RAM か）。#150 の `cd_playmsf` 証拠は CALLER 側の作業 RAM のみで、EX_BINBCD の入力を示さない。
2. 戻り値のレジスタ、保存レジスタ、破壊レジスタ、戻りフラグ（N/Z/V を含む）。
3. 範囲外（> 99）の挙動、エラー表現。
4. スタック使用量と戻り PC、MPR 設定、使用 RAM。
5. エントリの存在: `$E0B4` は `huc:include/hucc/pcengine.inc` からの HYPOTHESIS のみ。`hugo:bios.c` は選択子定義であり実行エントリの証拠ではない。
6. 純正 `$E0B4` の観測を許可する承認（#173 / #228）。

## 次の作業

- 純正側の観測は #173 / #228 の承認後に限る。承認前は本書の表を埋めない。
- 自作ルーチンの実コア検証は既に 0..255 で通っており、追加の実行は不要。
