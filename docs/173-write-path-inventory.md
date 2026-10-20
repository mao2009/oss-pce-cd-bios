# Issue #173: slot-1e 実行中の未監視書込み経路インベントリと副作用前遮断の検証計画

## 目的と状態

slot `$1E`（EX_GETVER 仮説）の参照JSR実行で、pinned Geargrafx（`b49ae82a012eade566d72e9d61c9d95a5caa4da3`）上の
CPU/デバイス書込み経路のうち、**監視されていないもの・副作用の後にしか検出されないもの**を機械可読な表にまとめた。
この文書はゲートを開けない。`slot $01` と全スロットは引き続き **BLOCKED / release_eligible=false**。

- 機械可読インベントリ: `spec/write_path_inventory.json`（検証: `tools/write_path_inventory.py --check`）
- 観測データのスキーマ: `tools/jsr_observation.py`（A/X/Y/P/SP/PC、MPR[0..7]、RAM差分、IRQ、戻りPC、来歴）
- ホスト側テスト: `tests/host/test_write_path_inventory.py`（合成入力のみ）

## 方法と限界

- pinned コア本体のソースは、この作業ツリーには存在せず、クローンもコピーもしていない。各行の根拠は既存のプローブ
  （`tests/integration/geargrafx_reference_jsr.cpp` とその Python ハーネス）と既存文書（`docs/173-safety-monitor-limits.md`,
  `docs/cd-reset-contract.md`）である。コア内の行番号は既存文書に記載されたもののみを引用し、再監査はしていない。
- 「実測」列は合成ROM・合成BIOS・合成CDでの実コア実行の結果のみを指す。`NOT_MEASURED` は計画であり、結果ではない。
- コア本体へのフック追加は行っていない。既存の patch 機構がなく、外部エミュレータコードをリポジトリに取り込まない方針のため。
  したがって、**既存の実コアプローブのC++は変更していない**。

## 未監視書込み経路の表

| ID | 経路 | 分類 | 監視状態 | 副作用前の遮断 | 検出点 | 実測 | 根拠 |
|----|------|------|----------|----------------|--------|------|------|
| W01 | ページ0を許可ページと同一物理バンクにエイリアス（MPR0==MPR1） | MPR | UNKNOWN | UNKNOWN（主張撤回: 検証可能な実行ID未記録） | 実行前 | 実コア合成（exit 8、実行IDなし） | probe, reference_guard.py |
| W02 | 許可外CPUアドレスへの書込み（$0000・$1FFF・$4000 の3点のみ） | RAM | UNKNOWN | UNKNOWN（主張撤回: 検証可能な実行ID未記録） | ストア前 | 実コア合成（3点のみ、実行IDなし） | probe, reference_guard.py |
| W03 | 実行中のMPR変更（TAM 等） | MPR | DETECTED_AT_INSTRUCTION_BOUNDARY | UNKNOWN（主張撤回: 検証可能な実行ID未記録。次命令を実行しない主張はしない） | 命令境界 | 実コア合成（TAMのみ） | probe, reference_guard.py |
| W04 | いずれかのMPRを$FFにしてハードウェアページを到達 | MMIO | UNKNOWN | UNKNOWN（主張撤回） | 命令境界 | 静的（到達性検査、ハードウェアページ分岐は未到達） | probe |
| W05 | **ST0 ($03)**: VDC アドレスレジスタ書込み（WriteFromCPU 迂回） | ST | **UNMONITORED** | **NO** | なし | 実コア合成（検出サイトなし） | geargrafx_st_bypass.py |
| W06 | **ST1 ($13)**: VDC データ下位書込み | ST | DETECTED_AFTER_SIDE_EFFECT | NO | 副作用後 | 実コア合成 | geargrafx_st_bypass.py |
| W07 | **ST2 ($23)**: VDC データ上位書込み | ST | DETECTED_AFTER_SIDE_EFFECT | NO | 副作用後 | 実コア合成 | geargrafx_st_bypass.py |
| W08 | $1802 IRQ 許可書込み（$FF到達時のみ） | IRQ | DETECTED_AFTER_SIDE_EFFECT | NO | 副作用後 | 実コア合成（irqreg） | geargrafx_irq_register.py |
| W09 | $1402 IRQ マスク書込み | IRQ | UNKNOWN | NO（書込みの着地を観測） | UNKNOWN（未記録） | 実コア合成（マスクが変化） | geargrafx_irq_register.py |
| W10 | $1403 TIQ 確認書込み | IRQ | UNKNOWN | NO（書込みの着地を観測） | UNKNOWN（未記録） | 実コア合成（bit2のみ変化） | geargrafx_irq_register.py |
| W11 | $1400/$1401 無効な割込レジスタ書込み | IRQ | UNKNOWN | UNKNOWN | UNKNOWN | 副作用なしを $1402/$1403 でのみ実測 | probe |
| W12 | $1804 CD リセット（SCSI RST）書込み | MMIO | DETECTED_AFTER_SIDE_EFFECT | NO | 副作用後（既知PCのみ実行ブレークで事前遮断。部分的な事前遮断は主張しない） | 実コア合成（既知コードのみ） | geargrafx_mmio1804_trap.py, _preblock.py |
| W13 | その他の I/O（VDC/VCE/PSG/タイマ/ジョイパッド/CD レジスタ） | MMIO | UNKNOWN | UNKNOWN（主張撤回。MPR経由の到達性のみ、実行IDなし） | 命令境界 | 静的（個別未実測） | probe。VDC は ST 経由で別途到達（W05〜W07） |
| W14 | 被呼出し側が CLI を実行し、IRQ/TIQ/タイマの割込みに入る | IRQ | **UNMONITORED** | NO | なし | 未測定 | probe（P=$04、stop_on_irq=false） |
| W15 | ブロック転送命令（TII/TIN/TDD/TIA/TAI）の1命令内の複数書込み | CPU | UNKNOWN | UNKNOWN（命令途中停止は未実測） | UNKNOWN | 未測定 | probe。命令途中で停止するかは不明 |
| W16 | 許可域 [$2000,$4000) 内（ZP・スタック・スタブ・被呼出し側データ） | RAM | PERMITTED_UNISOLATED | NO | なし | 実コア合成（事後の ram_diff と stub_ok のみ） | probe |
| W17 | デバイス起点の RAM 書込み: CD/ADPCM 転送（未監査） | DEVICE | UNKNOWN | UNKNOWN | UNKNOWN | 未監査・未測定 | docs/cd-reset-contract.md |
| W18 | 許可外CPUアドレスのうち W02 以外（ページ3〜7、ページ2の残り） | RAM | UNKNOWN | UNKNOWN | UNKNOWN | 未測定（ブレーク範囲は設定されるが、ストアは未観測） | probe |

集計（`--check` の出力）: 18行、未解決（監視状態が未解決）17行、副作用前遮断が未証明 18行（`prevention_unproven`。監視状態が検出済みでも遮断が UNKNOWN の行、例: W03、は未証明に数える。未証明が 0 になるのは全行が証明された場合のみ）。
副作用前遮断を主張する行は 0 件。以前の実測・到達性の主張（W01〜W04・W13）は撤回した。合成BIOSの実測（REAL_CORE_SYNTHETIC）は副作用前遮断の根拠にならず、静的な到達性の主張も実行IDを含む証拠ファイルで検証されていないため。W12 の既知PCのみの実行ブレークも主張しない（副作用後検出）。
ST0/1/2（W05〜W07）と IRQ 書込み（W08〜W10）には汎用の副作用前遮断が無く、割込みエントリ（W14）は未監視。W09・W10 は検出点が未記録（UNKNOWN）。

## 副作用前遮断の検証計画

すべて **NOT_RUN**。実コア実行は親（コーディネーター）が WSL 環境で別途行う。この worktree では `wsl.exe` が拒否されるため、ここでは行わない。

| 計画ID | 対象 | 合格条件 | 不合格・BLOCKED条件 |
|--------|------|----------|----------------------|
| V01 | W05 ST0 | ST0 の VDC 副作用が発生前に停止し、AR が変化しない | 副作用が着地する（現状の予想）。その場合 BLOCKED を維持 |
| V02 | W06/W07 ST1/ST2 | 書込み前に停止（ストア PC の実行ブレーク） | 既知PC以外では遮断できない。汎用は BLOCKED |
| V03 | W14 割込みエントリ | 割込みハンドラの最初の命令前に停止（解決済みベクタ先で実行ブレーク） | ベクタ先が実行時に解決できない、または割込みが届く前に停止しない場合 BLOCKED |
| V04 | W15 ブロック転送 | 1命令内の許可外書込みが各々ドロップされ、命令途中の停止有無が判明 | 命令途中で停止しない・ハードウェアページへの書込みが通る場合 BLOCKED |
| V05 | W02 の残り | 許可外の全バンク・全アドレス帯（境界アドレス含む）でドロップと検出 | 1アドレスでも副作用が着地した場合 FAIL |
| V06 | W03 の残り | MPR 変更（TAM 以外の全 MPR 書込み経路）が次命令前に検出される | 検出されない経路があれば FAIL |
| V07 | W17 デバイス起点書込み | コアソースの監査で RAM へ直接書く経路を列挙する（監査、実行は不要） | 経路が見つかり遮断できない場合 BLOCKED |
| V08 | 実行ごとの観測（IRQ 項目に限る） | `tools/jsr_observation.py` で IRQ が OBSERVED（mask・pending の値あり）か UNKNOWN（値なし）として明示される。UNKNOWN は PASS の根拠にしない | UNKNOWN を観測済みとして集計・判定に含める場合 FAIL。他の項目（レジスタ・MPR・RAM 差分）は UNKNOWN を許さない |

ゲート解放の条件: W05〜W11 と W14 が**副作用前に遮断される**と実コアで示されるまで、slot $1E も BLOCKED のまま。
W05 の遮断手段はコア側フックが無いと成立しない可能性が高い。その場合は slot $1E の実行経路を BLOCKED として閉じる。

## ホスト側テストの結果（この worktree）

- `tests/host/test_write_path_inventory.py`: 91 件（インベントリの不変条件・改変テスト、観測スキーマの正負例、ドキュメント整合）
- `tests/host/test_reference_jsr_gate.py`: 既存のゲートテストを維持（BLOCKED）
- 全体の `python -m unittest discover -s tests/host`（UTF-8 モード）: 1225 件中、残りの失敗は Windows 固有（`os.getuid`、`fcntl`、非 Win32 バイナリ、cc65 不在）による既存のもので、この PR の変更ファイルとは無関係。`make test` は NOT_RUN（Windows では Makefile と cc65 が動かないため）。

## 範囲と限界

- 含むもの: 未監視経路の機械可読表、その検証とスキーマのホスト側テスト、本文書。
- 含まないもの: コア本体へのフック、C++ プローブの変更、実コア実行、純正BIOSの扱い（一切読み込まない）、ゲートの解放。
- 既存の実コアプローブの出力には IRQ 状態が無い。したがってスキーマは IRQ を UNKNOWN と記録する。プローブ側の観測拡張は別 PR。
- 純正 BIOS 由来の情報は含まない。`rom/` 配下は読んでいない。GPL の対象コードは複製していない。

## 残作業（親が実行すること）

1. WSL で V01〜V08 の実コア検証を行い、結果を `tools/jsr_observation.py` のスキーマで記録する。
2. W14（割込み）と W15（ブロック転送）の実コアプローブを追加する（別 PR）。
3. W17 のコア監査（ライセンス承認を経た範囲のみ）。

BLOCKED 理由: 副作用前遮断の汎用フックがコアに無い（W05〜W11）。割込み・ブロック転送・デバイス書込みは未監査・未測定。
