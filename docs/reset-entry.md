# Issue #143: 最小の実BIOS resetエントリ（開発用イメージ）

`src/reset_entry.s` は独自実装の最小resetエントリ。完成BIOS、API、CD起動、割り込みABIではない。
既定のDEV ROMは変更せず、opt-inで `build/<mode>/reset-entry/reset-entry-not-bios.pce` を生成する。

```sh
make dev-reset-entry
python3 -m unittest tests.host.test_reset_entry -v
make dev-reset-entry-integration   # 固定版Geargrafx、cold/warm
```

## 採用した初期化と根拠 (`spec/rom-contract.json`)

| 内容 | 値 | 根拠claim |
| --- | --- | --- |
| reset時 MPR7 / vector | MPR7=0, `$FFF6..$FFFF` | `reset_mapping`, `vectors` (SOURCE_FACT) |
| 割り込み/10進フラグ | `SEI`, `CLD` | `cpu_reset_flags` (SOURCE_FACT) |
| MPR0 | `$FF` (IOバンク; `$1800`系アクセスに必要) | `cd_io` (SOURCE_FACT) |
| MPR1 | `$F8` (work RAM; スタックページ `$2100..$21FF`) | `work_ram`, `cpu_stack` (SOURCE_FACT) |
| SP | `$FF` (スタックページ内の選択。ABIではない) | プロジェクト方針 |

到達点 `reset_ready` は `BRA` 自己ループで、成功を報告しない。IRQ2/IRQ1/Timer/NMIは
`reset_unexpected_interrupt` (X=`$EE`) に向ける。

## UNKNOWN / BLOCKED（推測しない）

- MPR2..MPR6の割当、CSH(速度)方針、SP値のABI、割り込みABI、最終System Cardレイアウト (`historical_system_card_*`, `final_system_card_layout`, `boot_and_interrupt_abi` はUNKNOWNのまま)。
- 上記が確定するまで本番BIOS用の追加初期化は実装しない。ABIはVERIFIEDにしない。

## 検証

- ホスト: 全バイト反転が拒否され、MPR0/MPR1/TXS/vector/API表/未使用バンクの破損が負例として拒否される。
- 実コア: 固定版Geargrafxで cold/warm の PC/SP/MPR0,1,7/フラグを観測し、MPR0/MPR1/SP変異ROMが同じ検査で失敗する。
