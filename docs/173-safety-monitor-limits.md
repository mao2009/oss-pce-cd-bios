# Issue #173: 監視限界・GPL非混入・BLOCKEDゲート維持の記録

## 目的
純正 System Card 3.0 の `slot-1e` / `EX_GETVER` API JSR を将来解禁するための in-core 実行強制ゲートについて、pinned Geargrafx 実コアでの監視限界を整理し、GPLコードを MIT リポジトリに混入させない検証方式を明示する。

本記録は Issue #173 の上位トラッカーとしての監視限界裁定であり、**実行強制ゲートを有効化するものではない**。pinned コアに必要なフックがなければ実行を有効化せず BLOCKED を維持する。

## 対象と前提
- HuC6280 実行中の RAM 書込み / memory-mapped I/O アクセスを「発生時点で観測・拒否」できるか
- 最小対象: `slot-1e` / `EX_GETVER` 仮説（合成 ROM のみ、私的 System Card 3.0 BIOS は読み込み・実行しない）
- 負例: 一時書込み→復元、許可域外 RAM、書込可 MMIO、IRQ、命令予算超過、タイムアウト、プロセス異常、未知ABI、ROM hash不一致

## 手法（GPL非混入の検証方式）
pinned Geargrafx（GPL-3.0, revision `b49ae82a012eade566d72e9d61c9d95a5caa4da3`）のプリビルドオブジェクトファイルを `tests/integration/geargrafx_api.py::compile_probe` でリンクし、**コアソースコードを MIT リポジトリに持ち込まない**。リンク実行物はローカルのみで、CIアーティファクト・gitに含めない。

検討した代替案:
- 独立した隔離プロセス/スタブ方式: コアの挙動を再現するスタブを作ればGPL非混入は達成できるが、実コア実測の真正性が失われ、「実コアで観測・拒否できるか」の受入条件を満たさない。採用しない。
- コアソースの改修+リンク: GPLコードをMITリポジトリに持ち込むため禁止。

採用: 既存のオブジェクトファイルリンクフロー（`compile_probe`）を踏襲し、プローブ実行物は `tests/integration/` のローカル一時ディレクトリ内でのみビルド・実行する。

## 監視限界の整理
各対象について (a) 監視フックの有無、(b) 検出 vs 副作用前阻止、(c) 実現可能性を裁定する。

| 対象 | (a) 監視フックの有無 | (b) 検出 vs 副作用前阻止 | (c) 実現可能性 |
|------|----------------------|--------------------------|----------------|
| ST0 (`$03`) | `m_huc6202->WriteFromCPU()` が `Memory::Write`/`map_write`/CPUアドレスブレークポイントを迂回。アドレスレジスタ書込みに `CheckMemoryBreakpoints` 呼び出しサイトなし | 検出不可、VDC副作用は確実に着地 | **阻止不能 (BLOCKED)** |
| ST1 (`$13`) | `HUC6270_REGISTER` 書込みブレークポイント（フラグは書込み完了後に立ち上がる） | 検出は事後、書込み完遂 | **阻止不能 (BLOCKED)** |
| ST2 (`$23`) | 同上 | 同上 | **阻止不能 (BLOCKED)** |
| `$1802` | CPUアドレス書込みブレークポイント | 検出あり、デバイス書込み（IRQ enable）は完遂 | **事前阻止不能 (BLOCKED)** |
| `$1804` | CPUアドレス書込みブレークポイント | 検出あり、デバイス書込み（SCSI RST）は完遂 | **事前阻止不能 (BLOCKED)** |
| `$1804`（既知PC） | CPUアドレス実行ブレークポイント | フラグは命令フェッチ前に立ち上がり、モニタが再突入を拒否 | **既知 guest PC のみ可能**（一般的なMMIO書込みインターセプタではない） |
| `$1402` | CPUアドレス書込みブレークポイント | 検出あり、IRQマスク書込みは完遂 | **事前阻止不能 (BLOCKED)** |
| `$1403` | CPUアドレス書込みブレークポイント | 検出あり、TIQ Ack（bit2）は完遂、IRQ2 bit0 不変 | **事前阻止不能 (BLOCKED)** |

MPR変更（`TAM` による動的マッピング変更）と物理バンク別名は、実行前の静的バンクページ整合チェックと実行後の動的MPR比較で事前阻止・拒否可能（`tests/integration/geargrafx_reference_guard.py`）。

## 結論
ST0/ST1/ST2 は ST0 の `CheckMemoryBreakpoints` 欠如により完全に阻止不能。`$1802`/`$1402`/`$1403` は検出のみで書込みが完遂し、事前阻止不能。`$1804` は実行ブレークポイントで既知 guest PC に限り事前遮断可能だが、一般的なMMIO書込みインターセプタではない。

よって pinned Geargrafx 実コアに汎用の「副作用前拒否」ゲートを実装するフックはなく、**`slot-1e` / `EX_GETVER` の JSR 実行強制ゲートは BLOCKED を維持**し、実行を有効化しない。

## 証拠（実コア実測、合成 ROM のみ）
すべて合成 BIOS / 合成 CD のみ。私的 BIOS は読み込み・実行しない。`make integration` において全PASS。

- `tests/integration/geargrafx_reference_guard.py`: 許可域外RAM書込みの防止+検出、許可域内RAM書込みの着地、動的MPR変更の検出、物理バンク別名の拒否 — PASS
- `tests/integration/geargrafx_mmio1804_trap.py`: `$1804` 書込みは検出するがデバイス副作用が着地し事前阻止不能 — `BLOCKED`
- `tests/integration/geargrafx_mmio1804_preblock.py`: `$1804` 既知PCへの実行ブレークポイントによる事前遮断（`GREEN`、既知コードのみ） — PASS
- `tests/integration/geargrafx_st_bypass.py`: ST0/ST1/ST2 `WriteFromCPU` 迂回、ST0は検出サイトなし — `BLOCKED` / 実装不可
- `tests/integration/geargrafx_irq_register.py`: `$1802`/`$1804`/`$1402`/`$1403` 検出あり書込み完遂、IRQ2非着地（`m_active_irqs==0`） — `BLOCKED`

## ゲートと不変条件
- slot `$01` は BLOCKED 維持、`MODEL_ELIGIBLE` は実行許可ではない
- `spec/api_contracts.json` の変更なし
- 純正 BIOS JSR 未実行
- GPLコードの MIT リポジトリへのコピーなし
- 公開CIでは私的純正BIOS不在をPASS扱いしない

## 実測したこと / 未実測のこと
実測済み: 上記「証拠」の5統合テスト（ST/1802/1804/402/403、動的MPR、物理バンク別名、実行ブレークポイント事前遮断）。

未実測（今後の独立Issue/PRで個別に判定）: 命令予算超過・タイムアウト・プロセス異常・未知ABI・ROM hash不一致の各負例を、本ゲート下でどう扱うか。現時点では「阻止対象が防げないなら BLOCKED/UNKNOWN」として監視できない操作を隠さない。

## 残余リスクと次の実装可能範囲
- ST0/ST1/ST2 のVDC書込み迂回は pinned コアでは阻止不能。任意の guest コード実行を許可する前に、この経路の完全遮断（コア側フック追加または実行前静的解析）が必要。
- `$1804` 事前遮断は既知PCに限定され、汎用MMIOインターセプタではない。
- 次の実装可能範囲: 単一MMIO書込み（`$1804`）の既知PC事前遮断を子Issueとして独立実証し、ST迂回経路の完全遮断は別Issueとして未解決のまま残す。
